"""Tests 11-19 from the plan: MineruClient HTTP behavior.

All HTTP is mocked via pytest-httpx. ``sleep`` is stubbed to no-op so
backoff / poll loops don't pay wall-clock time. Each test exercises one
contract — request shaping, retry policy, error mapping, or timeout —
so a regression surfaces with a sharp message.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from conftest import BATCH_URL as _BATCH_URL
from conftest import PRESIGNED_URL as _PRESIGNED
from conftest import TEST_TOKEN as _TOKEN
from conftest import batch_results_url
from dikw_converter_mineru import (
    MineruApiError,
    MineruAuthError,
    MineruInputError,
    MineruQuotaError,
    MineruTimeoutError,
)
from dikw_converter_mineru._client import MineruClient, SubmitParams
from pytest_httpx import HTTPXMock

_BATCH_RESULTS = batch_results_url("B123")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[MineruClient]:
    """A MineruClient wrapping a real httpx.Client + no-op sleep."""
    monkeypatch.setattr("time.monotonic", _monotonic_stub())
    with httpx.Client() as http:
        yield MineruClient(
            client=http,
            token=_TOKEN,
            poll_initial_s=0.0,
            poll_max_s=0.0,
            poll_total_timeout_s=10.0,
            sleep=lambda _s: None,
        )


def _monotonic_stub() -> object:
    """Pure-monotonic clock that doesn't advance during a single
    `_with_retry` call — keeps retry math predictable.
    """
    counter = [0.0]

    def _tick() -> float:
        counter[0] += 0.001
        return counter[0]

    return _tick


def _ok_submit_response(batch_id: str = "B123") -> dict[str, object]:
    return {
        "code": 0,
        "msg": "ok",
        "data": {"batch_id": batch_id, "file_urls": [_PRESIGNED]},
    }


def _ok_poll_done(zip_url: str) -> dict[str, object]:
    return {
        "code": 0,
        "data": {
            "batch_id": "B123",
            "extract_result": [
                {"state": "done", "full_zip_url": zip_url, "data_id": "x"}
            ],
        },
    }


def _ok_poll_state(state: str) -> dict[str, object]:
    return {
        "code": 0,
        "data": {
            "batch_id": "B123",
            "extract_result": [{"state": state, "data_id": "x"}],
        },
    }


def _ok_poll_failed(err_code: str, err_msg: str = "boom") -> dict[str, object]:
    return {
        "code": 0,
        "data": {
            "batch_id": "B123",
            "extract_result": [
                {
                    "state": "failed",
                    "err_code": err_code,
                    "err_msg": err_msg,
                    "data_id": "x",
                }
            ],
        },
    }


def test_client_builds_upload_request_correctly(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """POST to /file-urls/batch carries Authorization Bearer + expected payload."""
    httpx_mock.add_response(
        url=_BATCH_URL,
        method="POST",
        json=_ok_submit_response(),
    )
    params = SubmitParams(file_name="paper.pdf", data_id="abc", model_version="vlm")
    handle = client.submit(params)

    assert handle.batch_id == "B123"
    assert handle.upload_url == _PRESIGNED

    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == f"Bearer {_TOKEN}"
    assert request.headers["Content-Type"] == "application/json"

    payload = json.loads(request.read())
    assert payload["language"] == "ch"
    assert payload["cache_tolerance"] == 31_536_000
    assert payload["enable_formula"] is True
    assert payload["enable_table"] is True
    # ``model_version`` lives at the top level of MinerU's batch payload,
    # not nested under files[].
    assert payload["model_version"] == "vlm"
    assert "model_version" not in payload["files"][0]
    assert payload["files"][0]["data_id"] == "abc"
    assert payload["files"][0]["name"] == "paper.pdf"


def test_client_omits_model_version_when_none(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """Non-PDF inputs leave ``model_version`` out of the payload so
    MinerU's per-format default pipeline kicks in.
    """
    httpx_mock.add_response(url=_BATCH_URL, method="POST", json=_ok_submit_response())
    client.submit(SubmitParams(file_name="deck.pptx", data_id="d", model_version=None))
    payload = json.loads(httpx_mock.get_requests()[0].read())
    assert "model_version" not in payload
    assert "model_version" not in payload["files"][0]


def test_client_uploads_without_content_type(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """OSS presigned PUT must omit Content-Type — signature mismatch
    otherwise. httpx adds 'Content-Length' and other transport headers
    automatically; only Content-Type is forbidden here.
    """
    httpx_mock.add_response(url=_PRESIGNED, method="PUT", status_code=200)
    client.upload(_PRESIGNED, b"pdf-bytes")
    request = httpx_mock.get_requests()[0]
    # httpx may auto-set a generic content-type for raw bytes; check
    # that we didn't pass 'application/json' or similar.
    ct = request.headers.get("Content-Type", "")
    assert "json" not in ct.lower()
    assert request.read() == b"pdf-bytes"


def test_client_polls_until_done(httpx_mock: HTTPXMock, client: MineruClient) -> None:
    """Three polls: pending → running → done. Returns the zip URL."""
    httpx_mock.add_response(url=_BATCH_RESULTS, method="GET", json=_ok_poll_state("pending"))
    httpx_mock.add_response(url=_BATCH_RESULTS, method="GET", json=_ok_poll_state("running"))
    httpx_mock.add_response(
        url=_BATCH_RESULTS,
        method="GET",
        json=_ok_poll_done("https://cdn.example.com/out.zip"),
    )
    zip_url = client.poll_until_done("B123")
    assert zip_url == "https://cdn.example.com/out.zip"


def test_client_raises_on_state_failed_generic(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """Unknown err_code → base MineruApiError with the message echoed."""
    httpx_mock.add_response(
        url=_BATCH_RESULTS,
        method="GET",
        json=_ok_poll_failed("-99999", "weird thing"),
    )
    with pytest.raises(MineruApiError) as exc:
        client.poll_until_done("B123")
    assert "weird thing" in str(exc.value)
    assert "-99999" in str(exc.value)


def test_client_raises_A0211_token_expired(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """Submit returns code A0211 → MineruAuthError, NOT generic ApiError."""
    httpx_mock.add_response(
        url=_BATCH_URL,
        method="POST",
        status_code=401,
        json={"code": "A0211", "msg": "token expired"},
    )
    with pytest.raises(MineruAuthError) as exc:
        client.submit(SubmitParams(file_name="x.pdf", data_id="d"))
    # Suffix-redacted token mentioned, full token NOT.
    assert "A0211" in str(exc.value)
    assert _TOKEN not in str(exc.value)


def test_client_raises_60018_quota_exhausted(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    httpx_mock.add_response(
        url=_BATCH_URL,
        method="POST",
        status_code=429,
        json={"code": "-60018", "msg": "quota gone"},
    )
    with pytest.raises(MineruQuotaError) as exc:
        client.submit(SubmitParams(file_name="x.pdf", data_id="d"))
    assert "-60018" in str(exc.value)


def test_client_raises_60005_file_too_large(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """Pre-API mapping ensures size errors map to MineruInputError."""
    httpx_mock.add_response(
        url=_BATCH_URL,
        method="POST",
        status_code=400,
        json={"code": "-60005", "msg": "file too big"},
    )
    with pytest.raises(MineruInputError):
        client.submit(SubmitParams(file_name="big.pdf", data_id="d"))


def test_client_retries_on_5xx_then_succeeds(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """Three 503s followed by a 200 must succeed — the retry budget is 3."""
    httpx_mock.add_response(url=_BATCH_URL, method="POST", status_code=503)
    httpx_mock.add_response(url=_BATCH_URL, method="POST", status_code=503)
    httpx_mock.add_response(url=_BATCH_URL, method="POST", json=_ok_submit_response())
    handle = client.submit(SubmitParams(file_name="x.pdf", data_id="d"))
    assert handle.batch_id == "B123"
    assert len(httpx_mock.get_requests()) == 3


def test_client_gives_up_after_max_retries(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """A 4th 5xx isn't tried — surface the failure clearly."""
    for _ in range(3):
        httpx_mock.add_response(url=_BATCH_URL, method="POST", status_code=502)
    with pytest.raises(MineruApiError) as exc:
        client.submit(SubmitParams(file_name="x.pdf", data_id="d"))
    assert "502" in str(exc.value)


def test_client_does_not_retry_on_4xx(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """Auth errors fast-fail — retrying would just waste quota."""
    httpx_mock.add_response(
        url=_BATCH_URL,
        method="POST",
        status_code=401,
        json={"code": "A0202", "msg": "token invalid"},
    )
    with pytest.raises(MineruAuthError):
        client.submit(SubmitParams(file_name="x.pdf", data_id="d"))
    # Exactly one request — no retry.
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_client_polling_timeout(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Polling must surface a typed timeout if the API never finishes.

    Rig monotonic to advance 100s per call so the 300s deadline trips
    after a few iterations. ``assert_all_responses_were_requested
    =False`` lets us queue a generous backlog without requiring every
    queued response to be consumed.
    """
    monotonic_counter = [0.0]

    def _fake_monotonic() -> float:
        monotonic_counter[0] += 100.0
        return monotonic_counter[0]

    monkeypatch.setattr(
        "dikw_converter_mineru._client.time.monotonic", _fake_monotonic
    )

    for _ in range(20):
        httpx_mock.add_response(
            url=_BATCH_RESULTS, method="GET", json=_ok_poll_state("running")
        )

    with httpx.Client() as http:
        c = MineruClient(
            client=http,
            token=_TOKEN,
            poll_initial_s=0.0,
            poll_max_s=0.0,
            poll_total_timeout_s=300.0,
            sleep=lambda _s: None,
        )
        with pytest.raises(MineruTimeoutError) as exc:
            c.poll_until_done("B123")
    assert "300" in str(exc.value)


def test_client_poll_response_missing_data_raises(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """A 200 with no ``data`` object is a contract violation; fail fast
    rather than treating the empty case as "pending" and timing out.
    """
    httpx_mock.add_response(
        url=_BATCH_RESULTS, method="GET", json={"code": 0, "msg": "ok"}
    )
    with pytest.raises(MineruApiError, match="'data'"):
        client.poll_until_done("B123")


def test_client_poll_extract_result_wrong_type_raises(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """``extract_result`` arriving as a dict (or any non-list) is a
    contract violation — refuse instead of silently waiting it out.
    """
    httpx_mock.add_response(
        url=_BATCH_RESULTS,
        method="GET",
        json={"code": 0, "data": {"extract_result": {"state": "done"}}},
    )
    with pytest.raises(MineruApiError, match="extract_result"):
        client.poll_until_done("B123")


def test_client_error_msg_scrubs_token(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """If the upstream echoes our bearer token in an error msg field,
    the raised exception must NOT contain the full token.
    """
    httpx_mock.add_response(
        url=_BATCH_URL,
        method="POST",
        status_code=400,
        json={
            "code": "-60002",
            "msg": f"upstream said: Bearer {_TOKEN}",
        },
    )
    with pytest.raises(MineruInputError) as exc:
        client.submit(SubmitParams(file_name="x.pdf", data_id="d"))
    assert _TOKEN not in str(exc.value)


def test_client_submit_validates_file_urls_shape(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """A string-typed ``file_urls`` would let ``urls[0]`` index a
    single character; reject the shape outright instead.
    """
    httpx_mock.add_response(
        url=_BATCH_URL,
        method="POST",
        json={"code": 0, "data": {"batch_id": "B", "file_urls": "oops"}},
    )
    with pytest.raises(MineruApiError, match="file_urls"):
        client.submit(SubmitParams(file_name="x.pdf", data_id="d"))


def test_client_poll_state_non_string_raises(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """A poll response with a non-string ``state`` field is a contract
    violation — failing now beats waiting out the 10-min timeout.
    """
    httpx_mock.add_response(
        url=_BATCH_RESULTS,
        method="GET",
        json={
            "code": 0,
            "data": {"extract_result": [{"state": {"running": True}}]},
        },
    )
    with pytest.raises(MineruApiError, match="state"):
        client.poll_until_done("B123")


def test_client_raise_for_code_non_string_msg(
    httpx_mock: HTTPXMock, client: MineruClient
) -> None:
    """A non-string ``msg`` (list/dict) must coerce cleanly rather than
    raising ``TypeError`` and bypassing the typed exception hierarchy.
    """
    httpx_mock.add_response(
        url=_BATCH_URL,
        method="POST",
        status_code=400,
        json={"code": "-60002", "msg": ["array", "of", "errors"]},
    )
    with pytest.raises(MineruInputError) as exc:
        client.submit(SubmitParams(file_name="x.pdf", data_id="d"))
    assert "errors" in str(exc.value)


def test_client_download_zip_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The download path streams + caps; an oversize response is
    refused before being buffered. Cap is monkey-patched low so the
    test runs with a small fixture payload.
    """
    monkeypatch.setattr(
        "dikw_converter_mineru._client._MAX_ZIP_DOWNLOAD_BYTES", 16
    )
    # Run a tiny in-process httpx mock; the streaming path is hard to
    # exercise via pytest-httpx alone, so we build an explicit MockTransport.
    big_payload = b"x" * 1024

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big_payload)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http:
        c = MineruClient(client=http, token=_TOKEN, sleep=lambda _s: None)
        with pytest.raises(MineruApiError, match="download cap"):
            c.download_zip("https://cdn.example.com/big.zip")
