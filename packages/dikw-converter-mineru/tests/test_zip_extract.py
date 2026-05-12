"""Tests 20-24 from the plan: result-ZIP unpacking + md image rewriting."""

from __future__ import annotations

import pytest
from conftest import BuildResultZipFn
from dikw_converter_mineru._errors import MineruApiError
from dikw_converter_mineru._zip_extract import extract_result_zip

_IMG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_zip_extract_renames_full_md(build_result_zip: BuildResultZipFn) -> None:
    """full.md inside the ZIP becomes <stem>.md content; assets stay empty
    when no images were included.
    """
    zip_bytes = build_result_zip(markdown="# Title\n\nhello\n")
    md, assets = extract_result_zip(zip_bytes)
    assert "# Title" in md
    assert "hello" in md
    assert assets == {}


def test_zip_extract_preserves_relpath(build_result_zip: BuildResultZipFn) -> None:
    """Images keep their ZIP relpath under ``assets/`` so two files
    with the same basename in different ZIP directories don't collapse.
    """
    zip_bytes = build_result_zip(
        markdown="# Doc\n\n![Fig](images/fig1.png)\n",
        images={"images/fig1.png": _IMG_BYTES},
    )
    md, assets = extract_result_zip(zip_bytes)
    assert "assets/images/fig1.png" in assets
    assert assets["assets/images/fig1.png"] == _IMG_BYTES
    # md ref rewritten to the wikilink form, pointing at the relpath.
    assert "![[assets/images/fig1.png|Fig]]" in md
    assert "(images/fig1.png)" not in md


def test_zip_extract_drops_json_files(build_result_zip: BuildResultZipFn) -> None:
    """MinerU's layout.json / *_content_list.json are byproducts, not assets."""
    zip_bytes = build_result_zip(
        markdown="# Doc\n",
        extra_files={
            "layout.json": b'{"_": []}',
            "doc_content_list.json": b"[]",
            "doc_model.json": b"{}",
        },
    )
    _md, assets = extract_result_zip(zip_bytes)
    # No JSON entries — and no naked file paths at root either.
    for key in assets:
        assert not key.endswith(".json")
        assert key.startswith("assets/")


def test_zip_extract_sanitizes_breaking_filenames(
    build_result_zip: BuildResultZipFn,
) -> None:
    """``]`` and ``|`` in an image filename break wikilink syntax; the
    extractor scrubs them to ``_`` and updates the md ref to match.
    """
    zip_bytes = build_result_zip(
        markdown="![cap](weird]name|x.png)\n",
        images={"weird]name|x.png": _IMG_BYTES},
    )
    md, assets = extract_result_zip(zip_bytes)
    assert "assets/weird_name_x.png" in assets
    assert "![[assets/weird_name_x.png|cap]]" in md
    assert "]name|" not in md


def test_zip_extract_rejects_zip_slip(build_result_zip: BuildResultZipFn) -> None:
    """``../escape.png`` and absolute paths are silently dropped."""
    zip_bytes = build_result_zip(
        markdown="# Doc\n",
        unsafe_entries=[
            ("../escape.png", _IMG_BYTES),
            ("/abs/path/evil.png", _IMG_BYTES),
            ("C:/win/abs.png", _IMG_BYTES),
        ],
    )
    _md, assets = extract_result_zip(zip_bytes)
    for key in assets:
        assert ".." not in key
        assert not key.startswith("/")
        assert "C:" not in key


def test_zip_extract_raises_when_full_md_missing(
    build_result_zip: BuildResultZipFn,
) -> None:
    """Missing full.md is a contract violation — fail clear, not silent."""
    import zipfile
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("images/x.png", _IMG_BYTES)
    with pytest.raises(MineruApiError, match=r"full\.md"):
        extract_result_zip(buf.getvalue())


def test_zip_extract_handles_crlf_in_md(
    build_result_zip: BuildResultZipFn,
) -> None:
    """Windows MinerU sometimes emits \\r\\n; normalize to \\n + trailing \\n."""
    zip_bytes = build_result_zip(markdown="line1\r\nline2\r\n")
    md, _ = extract_result_zip(zip_bytes)
    assert "\r" not in md
    assert md.endswith("\n")


def test_zip_extract_leaves_external_refs_alone(
    build_result_zip: BuildResultZipFn,
) -> None:
    """``![alt](http://...)`` refs to URLs aren't ours to rewrite."""
    zip_bytes = build_result_zip(
        markdown="![x](https://example.com/external.png)\n",
        images={"images/local.png": _IMG_BYTES},
    )
    md, _ = extract_result_zip(zip_bytes)
    assert "https://example.com/external.png" in md


def test_zip_extract_external_url_basename_collision_does_not_rewrite(
    build_result_zip: BuildResultZipFn,
) -> None:
    """An external URL whose basename collides with a local extracted
    asset must still NOT be rewritten — a basename match across origins
    would silently swap document semantics.
    """
    zip_bytes = build_result_zip(
        markdown="![cap](https://cdn.example.com/fig.png)\n",
        images={"fig.png": _IMG_BYTES},
    )
    md, _ = extract_result_zip(zip_bytes)
    assert "https://cdn.example.com/fig.png" in md
    assert "![[assets/fig.png" not in md


def test_zip_extract_duplicate_basenames_in_different_dirs_kept(
    build_result_zip: BuildResultZipFn,
) -> None:
    """Two ZIP images with the same basename in different directories
    must both survive — preserving relpath under ``assets/``.
    """
    a = b"\x89PNG\r\n\x1a\n" + b"A" * 16
    b = b"\x89PNG\r\n\x1a\n" + b"B" * 16
    zip_bytes = build_result_zip(
        markdown="![p1](page1/fig.png)\n\n![p2](page2/fig.png)\n",
        images={"page1/fig.png": a, "page2/fig.png": b},
    )
    md, assets = extract_result_zip(zip_bytes)
    assert assets["assets/page1/fig.png"] == a
    assert assets["assets/page2/fig.png"] == b
    assert "![[assets/page1/fig.png|p1]]" in md
    assert "![[assets/page2/fig.png|p2]]" in md


def test_zip_extract_backslash_traversal_rejected(
    build_result_zip: BuildResultZipFn,
) -> None:
    """Backslash-encoded zip-slip attempts must not slip past the safe
    path filter. ``a/..\\..\\..\\escape.png`` normalizes to
    ``../../escape.png`` after backslash conversion and must be
    rejected — without backslash awareness this would just collapse to
    a basename and silently extract outside the staging tree.
    """
    zip_bytes = build_result_zip(
        markdown="# Doc\n",
        unsafe_entries=[
            ("a/..\\..\\..\\escape.png", _IMG_BYTES),
            ("..\\..\\..\\sneaky.png", _IMG_BYTES),
        ],
    )
    _md, assets = extract_result_zip(zip_bytes)
    for key in assets:
        assert "escape" not in key
        assert "sneaky" not in key
        assert ".." not in key


def test_zip_extract_oversized_entry_rejected(
    build_result_zip: BuildResultZipFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ZIP entry declaring more than the cap uncompressed is refused
    immediately — defends against decompression-bomb payloads from the
    upstream CDN. We monkeypatch the cap low enough that a normal
    fixture trips it; the production cap (64 MiB) is too costly to
    exercise from a unit test directly.
    """
    monkeypatch.setattr(
        "dikw_converter_mineru._zip_extract._MAX_ENTRY_UNCOMPRESSED", 8
    )
    zip_bytes = build_result_zip(
        markdown="# Doc\n",
        images={"big.png": _IMG_BYTES},  # ~24 bytes, exceeds cap of 8
    )
    with pytest.raises(MineruApiError, match="per-entry"):
        extract_result_zip(zip_bytes)
