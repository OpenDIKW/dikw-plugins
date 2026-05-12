"""MinerU v4 batch API HTTP client (httpx).

Implements the three-step submit / upload / poll flow against
``https://mineru.net/api/v4`` and downloads the result ZIP. Pure HTTP
behaviour — no zip parsing — so this module is what's exercised under
``pytest-httpx``, with the orchestrator staying network-agnostic.

Handles **one file per submission** even though MinerU's batch endpoint
takes up to 50: dikw-core's Converter Protocol is single-file, so wiring
batch concurrency would add complexity for no gain at this layer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ._config import redact
from ._errors import (
    MineruApiError,
    MineruAuthError,
    MineruInputError,
    MineruQuotaError,
    MineruTimeoutError,
)

_API_BASE = "https://mineru.net/api/v4"
_BATCH_UPLOAD_URLS = f"{_API_BASE}/file-urls/batch"
_BATCH_RESULTS_TPL = f"{_API_BASE}/extract-results/batch/{{batch_id}}"

# Polling cadence: most small PDFs finish in < 30s; back off so a
# 10-minute job doesn't hammer the API.
_POLL_INITIAL_S = 2.0
_POLL_MAX_S = 30.0
_POLL_BACKOFF_FACTOR = 1.5
_POLL_TOTAL_TIMEOUT_S = 600.0

# Retry policy for transient 5xx + network errors on idempotent calls
# (submit / poll). PUT to a presigned URL is NOT retried because OSS may
# have partially accepted the body.
_RETRY_ATTEMPTS = 3
_RETRY_INITIAL_BACKOFF_S = 1.0

# Hard cap on the result-ZIP download. Real MinerU result ZIPs are
# < 100 MB; refuse anything larger before allocating the buffer so a
# hostile CDN response can't OOM the client. Independent of the
# uncompressed caps in ``_zip_extract`` (those run AFTER bytes land in
# memory).
_MAX_ZIP_DOWNLOAD_BYTES = 256 * 1024 * 1024  # 256 MiB

# MinerU task lifecycle states. The API ever-only returns these.
_STATE_DONE = "done"
_STATE_FAILED = "failed"

# Map MinerU's textual error codes to the typed exception class we raise.
# Codes not in this map fall through to the base ``MineruApiError`` so
# new server-side codes never silently turn into an opaque RuntimeError.
_AUTH_CODES = frozenset({"A0202", "A0211"})
_INPUT_CODES = frozenset(
    {"-60002", "-60005", "-60006", "-30001", "-30002", "-30003"}
)
_QUOTA_CODES = frozenset({"-60018", "-60019"})


def _classify_code(scode: str) -> type[MineruApiError]:
    """Return the most specific exception class for an API code."""
    if scode in _AUTH_CODES:
        return MineruAuthError
    if scode in _INPUT_CODES:
        return MineruInputError
    if scode in _QUOTA_CODES:
        return MineruQuotaError
    return MineruApiError


@dataclass(frozen=True)
class SubmitParams:
    """Knobs the plugin actually controls — everything else is MinerU
    default. ``model_version`` is ``None`` for non-PDF inputs so MinerU
    picks the right pipeline.
    """

    file_name: str
    data_id: str
    language: str = "ch"
    model_version: str | None = "vlm"
    enable_table: bool = True
    enable_formula: bool = True
    is_ocr: bool = False
    cache_tolerance: int = 31_536_000  # 1 year


@dataclass(frozen=True)
class SubmissionHandle:
    batch_id: str
    upload_url: str


def _scrub(message: str, token: str) -> str:
    """Replace any verbatim copy of ``token`` in a string with its
    suffix-redacted form. httpx tracebacks occasionally echo headers,
    so this runs on every error message that crosses the public API.
    """
    if token and token in message:
        return message.replace(token, redact(token))
    return message


class MineruClient:
    """Stateless wrapper around the three MinerU v4 endpoints we use.

    Construct with an injected ``httpx.Client`` (so tests can pass one
    bound to ``pytest-httpx`` without monkey-patching globals); methods
    raise typed :class:`MineruApiError` subclasses on failure.
    """

    def __init__(
        self,
        client: httpx.Client,
        token: str,
        *,
        poll_initial_s: float = _POLL_INITIAL_S,
        poll_max_s: float = _POLL_MAX_S,
        poll_total_timeout_s: float = _POLL_TOTAL_TIMEOUT_S,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client = client
        self._token = token
        self._poll_initial_s = poll_initial_s
        self._poll_max_s = poll_max_s
        self._poll_total_timeout_s = poll_total_timeout_s
        self._sleep = sleep if sleep is not None else time.sleep

    # ----- public API ----------------------------------------------------

    def submit(self, params: SubmitParams) -> SubmissionHandle:
        # MinerU's v4 batch API takes ``model_version`` at the top level
        # of the request, not nested under ``files[]``. Sending it
        # per-file silently drops back to the default pipeline.
        files_entry: dict[str, Any] = {
            "name": params.file_name,
            "is_ocr": params.is_ocr,
            "data_id": params.data_id,
        }
        payload: dict[str, Any] = {
            "enable_formula": params.enable_formula,
            "enable_table": params.enable_table,
            "language": params.language,
            "cache_tolerance": params.cache_tolerance,
            "files": [files_entry],
        }
        if params.model_version is not None:
            payload["model_version"] = params.model_version
        body = self._request_json_with_retry(
            "POST", _BATCH_UPLOAD_URLS, json_payload=payload
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise MineruApiError(
                _scrub(
                    f"MinerU submit response 'data' is not a dict: {body!r}",
                    self._token,
                )
            )
        batch_id = data.get("batch_id")
        urls = data.get("file_urls")
        # Strict shape: a string ``file_urls`` would silently let
        # ``urls[0]`` index a character; a non-dict ``data`` would
        # likewise produce garbage downstream.
        if not isinstance(batch_id, str) or not batch_id:
            raise MineruApiError(
                _scrub(
                    f"MinerU submit response missing/invalid batch_id: {body!r}",
                    self._token,
                )
            )
        if (
            not isinstance(urls, list)
            or not urls
            or not all(isinstance(url, str) and url for url in urls)
            or not urls[0]
        ):
            raise MineruApiError(
                _scrub(
                    f"MinerU submit response file_urls invalid: {body!r}",
                    self._token,
                )
            )
        return SubmissionHandle(batch_id=batch_id, upload_url=urls[0])

    def upload(self, upload_url: str, source: Path | bytes) -> None:
        """PUT the file to an OSS-presigned URL.

        Must NOT send a ``Content-Type`` header — OSS rejects the
        signature otherwise. Accepts either bytes (small file or test
        fixture) or a ``Path`` whose handle httpx will stream to avoid
        peaking memory at ~2x the file size for 200 MB uploads.
        """
        try:
            if isinstance(source, Path):
                with source.open("rb") as fh:
                    resp = self._client.put(upload_url, content=fh)
            else:
                resp = self._client.put(upload_url, content=source)
        except httpx.HTTPError as exc:
            raise MineruApiError(
                _scrub(f"MinerU upload network error: {exc}", self._token)
            ) from exc
        if resp.status_code >= 400:
            raise MineruApiError(
                f"MinerU upload failed: HTTP {resp.status_code} {resp.reason_phrase}"
            )

    def poll_until_done(self, batch_id: str) -> str:
        """Poll until the task hits ``done`` (returns the result zip URL)
        or ``failed`` (raises). Times out after ``poll_total_timeout_s``.
        """
        url = _BATCH_RESULTS_TPL.format(batch_id=batch_id)
        deadline = time.monotonic() + self._poll_total_timeout_s
        wait = self._poll_initial_s
        last_state = ""
        while True:
            body = self._request_json_with_retry("GET", url)
            data = body.get("data")
            if not isinstance(data, dict):
                raise MineruApiError(
                    _scrub(
                        f"MinerU poll response missing 'data' object: {body!r}",
                        self._token,
                    )
                )
            extract_results = data.get("extract_result")
            # ``extract_result`` may be a list (one entry per task) or
            # absent if the server hasn't enqueued the task yet. Anything
            # else (string, dict, int) means a contract violation — don't
            # wait it out as "pending".
            if extract_results is None:
                first: dict[str, Any] = {}
            elif isinstance(extract_results, list):
                first = extract_results[0] if extract_results else {}
                if not isinstance(first, dict):
                    raise MineruApiError(
                        _scrub(
                            f"MinerU poll extract_result[0] not a dict: {body!r}",
                            self._token,
                        )
                    )
            else:
                raise MineruApiError(
                    _scrub(
                        f"MinerU poll extract_result not a list: {body!r}",
                        self._token,
                    )
                )
            raw_state = first.get("state")
            if raw_state is not None and not isinstance(raw_state, str):
                raise MineruApiError(
                    _scrub(
                        f"MinerU poll state is not a string: {body!r}",
                        self._token,
                    )
                )
            state = raw_state or "pending"
            last_state = state

            if state == _STATE_DONE:
                full_zip_url = first.get("full_zip_url")
                if not isinstance(full_zip_url, str) or not full_zip_url:
                    raise MineruApiError(
                        _scrub(
                            f"MinerU task done but full_zip_url missing: {body!r}",
                            self._token,
                        )
                    )
                return full_zip_url
            if state == _STATE_FAILED:
                self._raise_for_code(
                    first.get("err_code"),
                    first.get("err_msg"),
                    context="task",
                )

            if time.monotonic() >= deadline:
                raise MineruTimeoutError(
                    f"MinerU task did not finish within "
                    f"{self._poll_total_timeout_s:.0f}s (last state={last_state!r})"
                )
            self._sleep(wait)
            wait = min(wait * _POLL_BACKOFF_FACTOR, self._poll_max_s)

    def download_zip(self, zip_url: str) -> bytes:
        """GET the result CDN URL, capped at :data:`_MAX_ZIP_DOWNLOAD_BYTES`.

        Follows CDN redirects, rejects an over-cap ``Content-Length``
        before buffering, and aborts the moment cumulative streamed
        bytes exceed the cap. Public CDN — no auth header.
        """
        chunks: list[bytes] = []
        total = 0
        try:
            with self._client.stream("GET", zip_url, follow_redirects=True) as resp:
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise MineruApiError(
                        f"MinerU result download failed: HTTP {resp.status_code}"
                    )
                content_length = resp.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared = int(content_length)
                    except ValueError:
                        declared = -1
                    if declared > _MAX_ZIP_DOWNLOAD_BYTES:
                        raise MineruApiError(
                            f"MinerU result ZIP Content-Length {declared} exceeds "
                            f"{_MAX_ZIP_DOWNLOAD_BYTES} bytes download cap; "
                            "refusing to buffer"
                        )
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > _MAX_ZIP_DOWNLOAD_BYTES:
                        raise MineruApiError(
                            f"MinerU result ZIP exceeds {_MAX_ZIP_DOWNLOAD_BYTES} "
                            f"bytes download cap; refusing to buffer"
                        )
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise MineruApiError(f"MinerU result download network error: {exc}") from exc
        return b"".join(chunks)

    # ----- internals -----------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _request_json_with_retry(
        self,
        method: str,
        url: str,
        *,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        op = f"{method} {url}"
        backoff = _RETRY_INITIAL_BACKOFF_S
        last_5xx: MineruApiError | None = None

        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                resp = self._client.request(
                    method, url, headers=self._auth_headers(), json=json_payload
                )
            except httpx.HTTPError as exc:
                if attempt < _RETRY_ATTEMPTS:
                    self._sleep(backoff)
                    backoff *= 2
                    continue
                raise MineruApiError(
                    _scrub(
                        f"{op}: network error after {attempt} attempts: {exc}",
                        self._token,
                    )
                ) from exc

            status = resp.status_code
            if 400 <= status < 500:
                body = self._safe_json(resp)
                self._raise_for_code(
                    body.get("code"),
                    body.get("msg") or body.get("message"),
                    context=f"HTTP {status}",
                )
                raise MineruApiError(  # pragma: no cover  # _raise_for_code always raises
                    f"{op}: HTTP {status} {self._safe_text(resp)}"
                )
            if status >= 500:
                last_5xx = MineruApiError(f"{op}: HTTP {status} {resp.reason_phrase}")
                if attempt < _RETRY_ATTEMPTS:
                    self._sleep(backoff)
                    backoff *= 2
                    continue
                raise last_5xx

            body = self._safe_json(resp)
            api_code = body.get("code")
            if api_code not in (0, "0", None):
                self._raise_for_code(
                    api_code,
                    body.get("msg") or body.get("message"),
                    context=f"HTTP {status}",
                )
            return body

        raise MineruApiError(  # pragma: no cover
            f"{op}: exhausted retries without resolution"
        )

    def _safe_json(self, resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
            return data if isinstance(data, dict) else {"_raw": data}
        except Exception:
            return {"_text": self._safe_text(resp)}

    def _safe_text(self, resp: httpx.Response) -> str:
        try:
            return _scrub(resp.text[:500], self._token)
        except Exception:
            return "<unreadable>"

    def _raise_for_code(
        self,
        code: str | int | None,
        msg: Any,
        *,
        context: str,
    ) -> None:
        """Map a MinerU error code to a typed exception and raise it.

        ``msg`` is server-supplied text — a proxy or backend that echoes
        request headers could embed our bearer token in it, so we scrub
        the value before letting it cross the public exception boundary.
        ``context`` is a short caller-controlled label (``"task"`` /
        ``"HTTP 401"``) and is trusted.
        """
        scode = "" if code is None else str(code)
        if not msg:
            text = "(no message)"
        elif isinstance(msg, str):
            text = _scrub(msg, self._token)
        else:
            # API contract violation — msg should always be a string,
            # but a malformed/proxied response can ship a list or dict.
            # Coerce defensively so we never raise TypeError instead of
            # the typed plugin error.
            text = _scrub(repr(msg), self._token)
        exc_cls = _classify_code(scode)
        if exc_cls is MineruAuthError:
            raise exc_cls(
                f"MinerU rejected token in {context} ({scode}: {text}); "
                f"token {redact(self._token)}. "
                "Re-issue at mineru.net → API manage if expired."
            )
        raise exc_cls(f"MinerU {context} error ({scode}: {text})")
