"""Shared fixtures for the dikw-converter-mineru test suite.

Tests mock HTTP via pytest-httpx — no real network calls. Synthetic
result ZIPs are built in-memory via stdlib ``zipfile`` so we avoid
checking large binary fixtures into git.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable, Iterable, Mapping
from io import BytesIO

import pytest
from pytest_httpx import HTTPXMock

BuildResultZipFn = Callable[..., bytes]

# URL constants the test suite mocks against — shared between
# test_client.py and test_convert.py so the wire-level shape is asserted
# once.
API_BASE = "https://mineru.net/api/v4"
BATCH_URL = f"{API_BASE}/file-urls/batch"
PRESIGNED_URL = "https://oss.example.com/upload?signature=xyz"
CDN_ZIP_URL = "https://cdn.example.com/result.zip"

# Anything that looks like a JWT — long enough to exercise the
# suffix-redaction path in error messages.
TEST_TOKEN = "eyJhbGc.test-token.signature-padding"


def batch_results_url(batch_id: str) -> str:
    return f"{API_BASE}/extract-results/batch/{batch_id}"


def _build_result_zip(
    *,
    markdown: str = "# Sample\n\nbody\n",
    images: Mapping[str, bytes] | None = None,
    extra_files: Mapping[str, bytes] | None = None,
    unsafe_entries: Iterable[tuple[str, bytes]] = (),
) -> bytes:
    """Synthesize a MinerU-shaped result ZIP.

    The ZIP contains ``full.md`` at the root plus whatever images and
    extra files the test provides. ``unsafe_entries`` is a back-door
    for zip-slip / weird-name tests — those bypass the safe-name helper.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("full.md", markdown)
        for name, data in (images or {}).items():
            zf.writestr(name, data)
        for name, data in (extra_files or {}).items():
            zf.writestr(name, data)
        for name, data in unsafe_entries:
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def build_result_zip() -> BuildResultZipFn:
    return _build_result_zip


def stage_happy_path(
    httpx_mock: HTTPXMock,
    *,
    zip_bytes: bytes,
    batch_id: str = "B999",
) -> None:
    """Queue the 4 HTTP exchanges that make up one successful convert.

    Lives in conftest so test_convert and the smoke harness share one
    definition; otherwise two near-identical helpers drift on every
    payload-shape tweak.
    """
    httpx_mock.add_response(
        url=BATCH_URL,
        method="POST",
        json={
            "code": 0,
            "data": {"batch_id": batch_id, "file_urls": [PRESIGNED_URL]},
        },
    )
    httpx_mock.add_response(url=PRESIGNED_URL, method="PUT", status_code=200)
    httpx_mock.add_response(
        url=batch_results_url(batch_id),
        method="GET",
        json={
            "code": 0,
            "data": {
                "batch_id": batch_id,
                "extract_result": [
                    {"state": "done", "full_zip_url": CDN_ZIP_URL, "data_id": "x"}
                ],
            },
        },
    )
    httpx_mock.add_response(url=CDN_ZIP_URL, method="GET", content=zip_bytes)


@pytest.fixture(autouse=True)
def _clear_mineru_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any real-world MinerU env vars before every test.

    Without this, a developer with ``MinerUAPIKey`` set in their shell
    would have tests behave differently than CI. ``autouse`` makes the
    cleanup unforgettable.
    """
    monkeypatch.delenv("MinerUAPIKey", raising=False)
    monkeypatch.delenv("DIKW_MINERU_API_KEY", raising=False)
