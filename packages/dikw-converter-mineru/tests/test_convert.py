"""Tests 25-36 from the plan: end-to-end orchestrator + provenance.

All HTTP is mocked via pytest-httpx, so this exercises the full pipeline
(submit → upload → poll → download → unzip → publish) without touching
the network. The "smoke against real API" test is in scratch/, not here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from conftest import BATCH_URL as _BATCH_URL
from conftest import PRESIGNED_URL as _PRESIGNED
from conftest import TEST_TOKEN as _TOKEN
from conftest import BuildResultZipFn, stage_happy_path
from dikw_converter_mineru import (
    MineruApiError,
    MineruConverter,
    MineruInputError,
)
from pytest_httpx import HTTPXMock

_IMG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def _stage_happy_path(
    httpx_mock: HTTPXMock, *, zip_bytes: bytes, batch_id: str = "B999"
) -> None:
    stage_happy_path(httpx_mock, zip_bytes=zip_bytes, batch_id=batch_id)


def _write_pdf(path: Path, body: bytes = b"%PDF-1.4 test body\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_convert_pdf_full_flow(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full happy path: input PDF → md + assets, including provenance."""
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    input_pdf = _write_pdf(tmp_path / "src" / "paper.pdf")
    out = tmp_path / "out"

    zip_bytes = build_result_zip(
        markdown="# Paper\n\n![Fig](images/fig1.png)\n\nhello\n",
        images={"images/fig1.png": _IMG_BYTES},
    )
    _stage_happy_path(httpx_mock, zip_bytes=zip_bytes)

    MineruConverter().convert(input_pdf, out)

    md = (out / "paper.md").read_text(encoding="utf-8")
    assert "# Paper" in md
    # ZIP relpath is preserved under assets/ (no more basename collapse).
    assert "![[assets/images/fig1.png|Fig]]" in md
    assert "![[assets/paper.pdf|original]]" in md
    assert (out / "assets" / "images" / "fig1.png").read_bytes() == _IMG_BYTES
    assert (out / "assets" / "paper.pdf").read_bytes() == input_pdf.read_bytes()


def test_convert_docx_omits_model_version(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A .docx submission must NOT carry model_version=vlm —
    MinerU's VLM is PDF-only.
    """
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    input_docx = _write_pdf(tmp_path / "deck.docx", body=b"PK\x03\x04 fake docx")
    out = tmp_path / "out"

    _stage_happy_path(httpx_mock, zip_bytes=build_result_zip(markdown="# Deck\n"))

    MineruConverter().convert(input_docx, out)

    submit_req = httpx_mock.get_requests()[0]
    payload = json.loads(submit_req.read())
    assert "model_version" not in payload
    assert "model_version" not in payload["files"][0]


def test_convert_writes_provenance(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Original input lands at assets/<stem>.<ext>, byte-identical."""
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    pdf_body = b"%PDF-1.4 original bytes\n" + b"x" * 200
    input_pdf = _write_pdf(tmp_path / "doc.pdf", body=pdf_body)
    _stage_happy_path(httpx_mock, zip_bytes=build_result_zip(markdown="# D\n"))

    out = tmp_path / "out"
    MineruConverter().convert(input_pdf, out)

    assert (out / "assets" / "doc.pdf").read_bytes() == pdf_body


def test_convert_md_ends_with_provenance_wikilink(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    input_pdf = _write_pdf(tmp_path / "doc.pdf")
    _stage_happy_path(httpx_mock, zip_bytes=build_result_zip(markdown="# Doc\n\nbody\n"))

    out = tmp_path / "out"
    MineruConverter().convert(input_pdf, out)

    md = (out / "doc.md").read_text(encoding="utf-8")
    # Provenance link is the trailing image-ref so md_inspect picks it up.
    last_lines = md.rstrip().splitlines()[-1]
    assert last_lines == "![[assets/doc.pdf|original]]"


def test_convert_clean_output_dir_on_failure(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mid-run HTTP failure must NOT leave half-written outputs around."""
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    input_pdf = _write_pdf(tmp_path / "doc.pdf")
    out = tmp_path / "out"

    # Submit succeeds, upload fails — output_dir should end up empty.
    httpx_mock.add_response(
        url=_BATCH_URL,
        method="POST",
        json={"code": 0, "data": {"batch_id": "B1", "file_urls": [_PRESIGNED]}},
    )
    httpx_mock.add_response(url=_PRESIGNED, method="PUT", status_code=500)

    with pytest.raises(MineruApiError):
        MineruConverter().convert(input_pdf, out)

    assert out.exists()  # we created it
    assert list(out.iterdir()) == []


def test_convert_pre_check_200mb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized inputs are rejected before any network call."""
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    # Create a sparse 201 MB file via seek; no real allocation needed.
    big = tmp_path / "big.pdf"
    with open(big, "wb") as f:
        f.seek(201 * 1024 * 1024)
        f.write(b"\0")

    with pytest.raises(MineruInputError, match="200"):
        MineruConverter().convert(big, tmp_path / "out")


def test_convert_payload_includes_cache_tolerance_and_data_id(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """data_id = sha256(input) prefix and cache_tolerance = 1 year — these
    are what make repeated imports of the same file deterministic.
    """
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    body = b"reproducible PDF body bytes" * 30
    input_pdf = _write_pdf(tmp_path / "doc.pdf", body=body)
    _stage_happy_path(httpx_mock, zip_bytes=build_result_zip(markdown="# D\n"))

    MineruConverter().convert(input_pdf, tmp_path / "out")
    payload = json.loads(httpx_mock.get_requests()[0].read())
    assert payload["cache_tolerance"] == 31_536_000
    expected = hashlib.sha256(body).hexdigest()[:32]
    assert payload["files"][0]["data_id"] == expected


def test_convert_deterministic_within_cache(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two converts with the same mocked response → byte-equal outputs."""
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    input_pdf = _write_pdf(tmp_path / "src" / "doc.pdf")
    zip_bytes = build_result_zip(
        markdown="# D\n\n![cap](images/i.png)\n",
        images={"images/i.png": _IMG_BYTES},
    )

    # Run 1 and run 2 each get the same mocked exchanges.
    _stage_happy_path(httpx_mock, zip_bytes=zip_bytes, batch_id="B-1")
    _stage_happy_path(httpx_mock, zip_bytes=zip_bytes, batch_id="B-2")

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    MineruConverter().convert(input_pdf, out_a)
    MineruConverter().convert(input_pdf, out_b)

    for name in ("doc.md", "assets/images/i.png", "assets/doc.pdf"):
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name


def test_convert_md_is_utf8(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    input_pdf = _write_pdf(tmp_path / "d.pdf")
    _stage_happy_path(
        httpx_mock,
        zip_bytes=build_result_zip(markdown="# 标题\n\n中文段落\n"),
    )
    out = tmp_path / "out"
    MineruConverter().convert(input_pdf, out)
    raw = (out / "d.md").read_bytes()
    # Round-trip through UTF-8 strict (no errors=replace) — fails fast
    # if any byte was written as surrogate-escape or in another encoding.
    assert "中文段落" in raw.decode("utf-8")


def test_convert_with_chinese_filename(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chinese filenames + spaces propagate cleanly into output filenames."""
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    input_pdf = _write_pdf(tmp_path / "文档 标题.pdf")
    _stage_happy_path(httpx_mock, zip_bytes=build_result_zip(markdown="# 文档\n"))

    out = tmp_path / "out"
    MineruConverter().convert(input_pdf, out)

    assert (out / "文档 标题.md").is_file()
    assert (out / "assets" / "文档 标题.pdf").is_file()
    md = (out / "文档 标题.md").read_text(encoding="utf-8")
    assert "![[assets/文档 标题.pdf|original]]" in md


def test_convert_pdf_uses_vlm_model_version(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF inputs carry model_version=vlm — VLM is MinerU's recommended
    PDF pipeline.
    """
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    input_pdf = _write_pdf(tmp_path / "paper.pdf")
    _stage_happy_path(httpx_mock, zip_bytes=build_result_zip(markdown="# P\n"))

    MineruConverter().convert(input_pdf, tmp_path / "out")
    payload = json.loads(httpx_mock.get_requests()[0].read())
    assert payload.get("model_version") == "vlm"


def test_convert_explicit_api_key_constructor_path(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    build_result_zip: BuildResultZipFn,
) -> None:
    """No env var; api_key passed at construct time — convert still
    succeeds and the right Authorization header is sent."""
    input_pdf = _write_pdf(tmp_path / "p.pdf")
    _stage_happy_path(httpx_mock, zip_bytes=build_result_zip(markdown="# P\n"))

    MineruConverter(api_key=_TOKEN).convert(input_pdf, tmp_path / "out")

    submit_req = httpx_mock.get_requests()[0]
    assert submit_req.headers["Authorization"] == f"Bearer {_TOKEN}"


def test_convert_missing_input_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-existent file fails clean before any network call."""
    monkeypatch.setenv("MinerUAPIKey", _TOKEN)
    with pytest.raises(MineruInputError):
        MineruConverter().convert(tmp_path / "missing.pdf", tmp_path / "out")
