"""TDD test suite for ``dikw-converter-epub``."""

from __future__ import annotations

import filecmp
import zipfile
from pathlib import Path

import pytest
from conftest import BuildEpubFn, xhtml
from dikw_converter_epub import EpubConverter, EpubParseError

_MINIMAL_XHTML = xhtml(b"<head><title>Chapter 1</title></head><p>hello body</p>")
_XHTML_TYPE = "application/xhtml+xml"


def test_protocol_attributes() -> None:
    c = EpubConverter()
    assert c.name == "epub"
    assert c.extensions == (".epub",)


def test_satisfies_dikw_core_protocol() -> None:
    """Structural Protocol conformance against the real dikw-core; skipped
    when dikw-core isn't importable in the test env."""
    converters_mod = pytest.importorskip("dikw_core.client.converters")
    assert isinstance(EpubConverter(), converters_mod.Converter)


def test_convert_writes_md_and_provenance(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Minimal EPUB → md + provenance asset + image-ref to original."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": _MINIMAL_XHTML},
    )
    out = tmp_path / "out"

    EpubConverter().convert(epub_path, out)

    md = (out / "book.md").read_text(encoding="utf-8")
    assert "hello body" in md
    assert "![[assets/book.epub|original]]" in md
    assert (out / "assets" / "book.epub").read_bytes() == epub_path.read_bytes()


def test_output_is_deterministic(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Same input bytes → byte-identical output across runs.

    dikw-core hashes md content to skip unchanged sources at ingest
    time; non-deterministic output forces a re-chunk+re-embed every
    import.
    """
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": _MINIMAL_XHTML},
    )
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"

    EpubConverter().convert(epub_path, out_a)
    EpubConverter().convert(epub_path, out_b)

    _, mismatches, errors = filecmp.cmpfiles(
        out_a, out_b, ["book.md"], shallow=False
    )
    assert mismatches == [] and errors == []
    _, asset_mismatches, asset_errors = filecmp.cmpfiles(
        out_a / "assets", out_b / "assets", ["book.epub"], shallow=False
    )
    assert asset_mismatches == [] and asset_errors == []


def test_single_chapter_extracts_paragraphs_and_headings(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Pins the heading-shift rule: book-title H1 → chapter H2 → inner +2."""
    body = (
        b"<h1>The Beginning</h1>"
        b"<p>First paragraph.</p>"
        b"<h2>Subsection</h2>"
        b"<p>Second paragraph.</p>"
        b"<p>Third paragraph.</p>"
    )
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        title="Bookland",
        creator="A. Author",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": xhtml(body)},
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    md = (out / "book.md").read_text(encoding="utf-8")
    pos_first = md.index("First paragraph.")
    pos_second = md.index("Second paragraph.")
    pos_third = md.index("Third paragraph.")
    assert pos_first < pos_second < pos_third
    assert "## The Beginning" in md
    assert "#### Subsection" in md  # h2 + inner_shift(2) = 4
    assert "### The Beginning" not in md  # chapter title shouldn't repeat


def test_spine_order_preserved(tmp_path: Path, build_epub: BuildEpubFn) -> None:
    """Chapters render in spine order, not filename / manifest order."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[
            ("a", "text/a.xhtml", _XHTML_TYPE),
            ("b", "text/b.xhtml", _XHTML_TYPE),
            ("c", "text/c.xhtml", _XHTML_TYPE),
        ],
        spine_idrefs=["c", "a", "b"],
        files={
            "OEBPS/text/a.xhtml": xhtml(b"<p>alpha-body</p>"),
            "OEBPS/text/b.xhtml": xhtml(b"<p>beta-body</p>"),
            "OEBPS/text/c.xhtml": xhtml(b"<p>gamma-body</p>"),
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")

    assert md.index("gamma-body") < md.index("alpha-body") < md.index("beta-body")


_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfe\xa3y\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_image_extraction_strips_opf_root_oebps(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """OPF at ``OEBPS/content.opf`` → asset path strips the OEBPS prefix."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        opf_path="OEBPS/content.opf",
        manifest_items=[
            ("ch1", "text/ch1.xhtml", _XHTML_TYPE),
            ("fig1", "images/fig1.png", "image/png"),
        ],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(
                b"<p>see figure</p><img src='../images/fig1.png' alt='F1'/>"
            ),
            "OEBPS/images/fig1.png": _PNG_BYTES,
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    assert (out / "assets" / "images" / "fig1.png").read_bytes() == _PNG_BYTES
    md = (out / "book.md").read_text(encoding="utf-8")
    assert "![[assets/images/fig1.png|F1]]" in md
    assert "OEBPS" not in md
    assert not (out / "assets" / "OEBPS").exists()


def test_image_extraction_strips_opf_root_root_level(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """OPF at zip root → asset path == zip path (nothing to strip)."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        opf_path="content.opf",
        manifest_items=[
            ("ch1", "ch1.xhtml", _XHTML_TYPE),
            ("cover", "img/cover.jpg", "image/jpeg"),
        ],
        spine_idrefs=["ch1"],
        files={
            "ch1.xhtml": xhtml(b"<img src='img/cover.jpg' alt='cover'/>"),
            "img/cover.jpg": _PNG_BYTES,
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    assert (out / "assets" / "img" / "cover.jpg").read_bytes() == _PNG_BYTES
    md = (out / "book.md").read_text(encoding="utf-8")
    assert "![[assets/img/cover.jpg|cover]]" in md


def test_metadata_in_header(tmp_path: Path, build_epub: BuildEpubFn) -> None:
    """``dc:title`` + ``dc:creator`` surface as H1 + italic byline."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        title="The Real Title",
        creator="Jane Doe",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": xhtml(b"<p>content</p>")},
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    md = (out / "book.md").read_text(encoding="utf-8")
    assert md.startswith("# The Real Title\n\n*Jane Doe*\n")


def test_missing_title_skips_h1(tmp_path: Path, build_epub: BuildEpubFn) -> None:
    """Without ``dc:title`` the chapter title becomes the only H1."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        title=None,
        creator=None,
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": xhtml(b"<h1>Chapter One</h1><p>content</p>")},
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    md = (out / "book.md").read_text(encoding="utf-8")
    first_nonblank = next(line for line in md.splitlines() if line.strip())
    assert first_nonblank == "# Chapter One"


def test_zip_slip_image_rejected(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """``<img src>`` pointing outside the OPF dir must not extract bytes
    anywhere on the filesystem."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(
                b"<img src='../../etc/passwd.png' alt='pwn'/>"
            ),
            # A real zip entry at the attacker-targeted location; without
            # the OPF-dir filter the extractor would happily copy these bytes.
            "etc/passwd.png": b"pwned",
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    forbidden_locations = [
        tmp_path / "etc" / "passwd.png",
        tmp_path / "passwd.png",
        out / "passwd.png",
        out / "etc" / "passwd.png",
        out / "assets" / "etc" / "passwd.png",
    ]
    for path in forbidden_locations:
        assert not path.exists(), f"zip-slip leaked: {path}"


def _write_raw_zip(target: Path, entries: dict[str, bytes]) -> Path:
    """Build a zip directly, bypassing the EPUB factory.

    Used by error-path tests that need the on-disk structure to be
    deliberately malformed.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, mode="w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, "application/epub+zip")
        for name, data in entries.items():
            zf.writestr(name, data)
    return target


def test_missing_container_raises(tmp_path: Path) -> None:
    """No ``META-INF/container.xml`` → ``EpubParseError``."""
    epub_path = _write_raw_zip(tmp_path / "src" / "broken.epub", entries={})

    with pytest.raises(EpubParseError, match=r"META-INF/container\.xml"):
        EpubConverter().convert(epub_path, tmp_path / "out")


def test_missing_opf_raises(tmp_path: Path) -> None:
    """container.xml references a non-existent OPF path → EpubParseError."""
    container = (
        b"<?xml version='1.0'?>"
        b"<container version='1.0' "
        b"xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
        b"<rootfiles><rootfile full-path='OEBPS/missing.opf' "
        b"media-type='application/oebps-package+xml'/></rootfiles></container>"
    )
    epub_path = _write_raw_zip(
        tmp_path / "src" / "broken.epub",
        entries={"META-INF/container.xml": container},
    )

    with pytest.raises(EpubParseError, match=r"missing\.opf"):
        EpubConverter().convert(epub_path, tmp_path / "out")


def test_empty_spine_raises(tmp_path: Path, build_epub: BuildEpubFn) -> None:
    """Empty spine raises rather than emitting a near-empty markdown."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[],
        spine_idrefs=[],
    )
    with pytest.raises(EpubParseError, match="spine"):
        EpubConverter().convert(epub_path, tmp_path / "out")


def test_non_utf8_xhtml_decoded_with_replace(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Mis-declared encoding falls back to UTF-8 + ``errors='replace'``;
    surrounding ASCII content still survives without crashing."""
    body = b"<p>caf\xe9 \xff weird</p>"
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": xhtml(body)},
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    md = (out / "book.md").read_text(encoding="utf-8")
    assert "weird" in md
    assert "caf" in md


# ---- regression coverage for codex round 1 findings -----------------------


def test_missing_spine_chapter_raises(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Spine idref that's not declared in manifest must raise, not silently
    drop the chapter — otherwise a malformed EPUB succeeds with incomplete
    content and the user has no signal that something was lost."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1", "ghost-chapter"],
        files={"OEBPS/text/ch1.xhtml": xhtml(b"<p>only one</p>")},
    )
    with pytest.raises(EpubParseError, match="ghost-chapter"):
        EpubConverter().convert(epub_path, tmp_path / "out")


def test_missing_chapter_zip_entry_raises(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Manifest item that resolves to a non-existent zip path must raise.

    Mirror of the previous test but at the zip-entry layer rather than
    the manifest layer.
    """
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[
            ("ch1", "text/ch1.xhtml", _XHTML_TYPE),
            ("ch2", "text/missing.xhtml", _XHTML_TYPE),
        ],
        spine_idrefs=["ch1", "ch2"],
        files={"OEBPS/text/ch1.xhtml": xhtml(b"<p>one</p>")},
    )
    with pytest.raises(EpubParseError, match="ch2"):
        EpubConverter().convert(epub_path, tmp_path / "out")


def test_percent_encoded_href_resolves(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """OPF hrefs are URLs per spec; ``chapter%201.xhtml`` matches the zip
    entry ``chapter 1.xhtml``. Without URL-decoding the converter would
    skip the chapter and raise spurious 'unresolved chapter' errors on
    every EPUB with spaces in filenames."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/chapter%201.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/chapter 1.xhtml": xhtml(b"<p>spaces work</p>")},
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    assert "spaces work" in (out / "book.md").read_text(encoding="utf-8")


def test_root_opf_image_traversal_dropped(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Even with OPF at zip root, ``<img src="../outside.png">`` must not
    produce a dangling ``assets/../outside.png`` reference in the md."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        opf_path="content.opf",
        manifest_items=[("ch1", "ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={
            "ch1.xhtml": xhtml(b"<img src='../outside.png' alt='pwn'/>"),
            "outside.png": b"\x89PNGfake",
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    md = (out / "book.md").read_text(encoding="utf-8")
    assert "outside.png" not in md
    assert "../" not in md
    assert not (out / "outside.png").exists()


def test_provenance_filename_with_parens_uses_wikilink(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Filenames like ``book(1).epub`` would break ``![alt](path)`` syntax —
    md_inspect's regex terminates at the first ``)`` in the path. The
    wikilink syntax handles parens, brackets, and most other punctuation
    cleanly."""
    epub_path = build_epub(
        tmp_path / "src" / "book(1).epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": xhtml(b"<p>x</p>")},
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    md = (out / "book(1).md").read_text(encoding="utf-8")
    assert "![[assets/book(1).epub|original]]" in md


def test_alt_text_with_brackets_sanitized(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Alt text containing ``]`` or ``|`` would break both the standard
    and wikilink image regexes. The renderer must scrub them."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[
            ("ch1", "text/ch1.xhtml", _XHTML_TYPE),
            ("fig1", "images/fig1.png", "image/png"),
        ],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(
                b"<img src='../images/fig1.png' alt='foo]bar|baz'/>"
            ),
            "OEBPS/images/fig1.png": _PNG_BYTES,
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")
    # Both `]` and `|` are sanitized to spaces in the alias slot.
    assert "foo bar baz" in md
    assert "foo]bar" not in md


def test_doctype_rejected(tmp_path: Path) -> None:
    """OPF / container.xml with a DTD declaration is refused without
    parsing — defense against billion-laughs-style entity expansion."""
    container = (
        b"<?xml version='1.0'?>"
        b"<!DOCTYPE container ["
        b"<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"
        b"<container version='1.0' "
        b"xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
        b"<rootfiles><rootfile full-path='OEBPS/content.opf' "
        b"media-type='application/oebps-package+xml'/></rootfiles></container>"
    )
    epub_path = _write_raw_zip(
        tmp_path / "src" / "broken.epub",
        entries={"META-INF/container.xml": container},
    )
    with pytest.raises(EpubParseError, match=r"DOCTYPE"):
        EpubConverter().convert(epub_path, tmp_path / "out")


def test_br_inserts_space_between_text(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """``<p>hello<br/>world</p>`` must render as ``hello world``, not
    ``helloworld`` (HTMLParser collapses whitespace between data
    callbacks, so adjacent text glues together without an explicit hint).
    """
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": xhtml(b"<p>hello<br/>world</p>")},
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")
    assert "hello world" in md
    assert "helloworld" not in md


def test_oversized_chapter_rejected(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Per-entry size cap protects against zip-bomb-shaped chapters.

    We don't generate an actual gigabyte payload; we just push past the
    16 MiB chapter cap with a deliberately oversized XHTML to verify
    the size check fires before the decompression completes (real zip
    bombs would obviously be much bigger).
    """
    big_body = b"<p>" + b"x" * (17 * 1024 * 1024) + b"</p>"
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": xhtml(big_body)},
    )
    with pytest.raises(EpubParseError, match=r"exceeds cap"):
        EpubConverter().convert(epub_path, tmp_path / "out")


# ---- regression coverage for codex round 2 findings -----------------------


def test_injected_image_ref_in_title_is_escaped(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Book title containing literal ``![evil](ghost.png)`` must not
    become an active image embed in the rendered markdown.

    md_inspect scans the whole body for image refs; an unescaped
    injection in the title would either trigger asset_missing or
    hijack a path the converter itself wrote.
    """
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        title="![evil](ghost.png) Bookland",
        creator="A. Author",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": xhtml(b"<p>x</p>")},
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")
    # After ``![`` → ``!\[`` escaping, the bare ``![evil]`` substring no
    # longer exists in the output (the inserted ``\`` breaks the
    # consecutive ``!`` ``[``). The escaped form is what survives.
    assert "![evil]" not in md
    assert "!\\[evil]" in md


def test_injected_wikilink_in_paragraph_is_escaped(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Paragraph text containing ``![[...]]`` must not become an active
    wikilink embed in the rendered markdown."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(
                b"<p>see ![[assets/ghost.png|hi]] for details</p>"
            )
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")
    # The escape inserts ``\`` between ``!`` and ``[``, so the bare
    # ``![[...`` form is no longer consecutive and md_inspect's wikilink
    # regex (which requires ``!`` then ``[`` then ``[`` consecutively)
    # stops matching.
    assert "![[assets/ghost.png" not in md
    assert "!\\[[assets/ghost.png" in md


def test_provenance_filename_with_bracket_unified(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Provenance filename containing ``]`` (which wikilink can't capture)
    must be renamed on disk AND in the md to the SAME sanitized name —
    otherwise the md ref dangles."""
    epub_path = build_epub(
        tmp_path / "src" / "book]1.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={"OEBPS/text/ch1.xhtml": xhtml(b"<p>x</p>")},
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    # Disk and md must agree on the same path: ``book_1.epub``.
    assert (out / "assets" / "book_1.epub").exists()
    assert not (out / "assets" / "book]1.epub").exists()
    md = (out / "book]1.md").read_text(encoding="utf-8")
    assert "![[assets/book_1.epub|original]]" in md


def test_backslash_url_encoded_traversal_dropped(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """``..%5C..%5Coutside.png`` (URL-encoded backslash traversal) must
    not survive into the markdown — on Windows the backslash would be
    a path separator and md_inspect could follow it outside the bundle."""
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(
                b"<img src='..%5C..%5Coutside.png' alt='pwn'/>"
            ),
            "outside.png": b"\x89PNGfake",
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")
    assert "outside.png" not in md
    assert "\\" not in md


def test_utf16_bom_xml_rejected(tmp_path: Path) -> None:
    """UTF-16 BOMs on container.xml / OPF bypass the byte regex check
    for ``<!DOCTYPE``. EPUB spec mandates UTF-8 — reject other encodings
    at the parser entry point."""
    container = "﻿<container/>".encode("utf-16-le")
    epub_path = _write_raw_zip(
        tmp_path / "src" / "broken.epub",
        entries={"META-INF/container.xml": container},
    )
    with pytest.raises(EpubParseError, match=r"UTF-8"):
        EpubConverter().convert(epub_path, tmp_path / "out")


def test_font_only_encryption_allowed(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Calibre/ADE font-obfuscation EPUBs declare encryption.xml but the
    spine remains plaintext. The converter must allow them — only refuse
    when an OPF or spine target appears in CipherReference."""
    encryption_xml = (
        b"<?xml version='1.0'?>"
        b"<encryption xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
        b"<EncryptedData xmlns='http://www.w3.org/2001/04/xmlenc#'>"
        b"<CipherData><CipherReference URI='OEBPS/fonts/obfuscated.ttf'/>"
        b"</CipherData></EncryptedData></encryption>"
    )
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(b"<p>plaintext</p>"),
            "OEBPS/fonts/obfuscated.ttf": b"<obfuscated bytes>",
            "META-INF/encryption.xml": encryption_xml,
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")
    assert "plaintext" in md


def test_spine_encryption_rejected(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """An encryption.xml that lists a spine document as encrypted must
    cause conversion to refuse — otherwise the converter would
    decompress the ciphertext, get zero blocks, and silently produce
    empty content."""
    encryption_xml = (
        b"<?xml version='1.0'?>"
        b"<encryption xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
        b"<EncryptedData xmlns='http://www.w3.org/2001/04/xmlenc#'>"
        b"<CipherData><CipherReference URI='OEBPS/text/ch1.xhtml'/>"
        b"</CipherData></EncryptedData></encryption>"
    )
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(b"<p>ciphertext, conceptually</p>"),
            "META-INF/encryption.xml": encryption_xml,
        },
    )
    with pytest.raises(EpubParseError, match=r"encrypted"):
        EpubConverter().convert(epub_path, tmp_path / "out")


# ---- regression coverage for codex round 3 findings -----------------------


def test_pre_block_md_injection_escaped(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """A ``<pre>`` block containing ``![evil](ghost.png)`` must not produce
    an active image ref in the rendered markdown.

    Code fences don't shield content from md_inspect's full-body regex
    scan — escape pre content the same as inline text.
    """
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(
                b"<pre>![evil](ghost.png)</pre>"
            )
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")
    assert "![evil](ghost.png)" not in md
    assert "!\\[evil](ghost.png)" in md


def test_md_inspect_does_not_extract_injected_refs(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Direct integration: feed the rendered markdown into dikw-core's
    md_inspect.extract_image_refs and confirm injected ``![evil]`` /
    ``![[ghost]]`` tokens in EPUB text don't surface as asset refs.

    The escape and the actual regex live in different packages; pinning
    this end-to-end stops a future md_inspect regex tweak from silently
    re-enabling injection.
    """
    md_inspect = pytest.importorskip("dikw_core.md_inspect")
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        title="![evil](ghost.png) Title",
        creator="![[secret|hi]] Author",
        manifest_items=[("ch1", "text/ch1.xhtml", _XHTML_TYPE)],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(
                b"<p>see ![[assets/ghost.png]] also ![bad](x.png)</p>"
                b"<pre>![pre-evil](y.png)</pre>"
            )
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")
    refs = md_inspect.extract_image_refs(md)
    # The only legitimate ref is the provenance line; everything else
    # was injected text and must NOT survive the escape.
    paths = [r.original_path for r in refs]
    assert paths == ["assets/book.epub"], (
        f"injection slipped through the escape: {paths!r}"
    )


def test_encrypted_image_dropped_from_md(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """A chapter ``<img>`` whose zip target is listed in encryption.xml
    must be dropped from the markdown — otherwise md_inspect raises
    asset_missing on the dangling ref."""
    encryption_xml = (
        b"<?xml version='1.0'?>"
        b"<encryption xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
        b"<EncryptedData xmlns='http://www.w3.org/2001/04/xmlenc#'>"
        b"<CipherData><CipherReference URI='OEBPS/images/fig.png'/>"
        b"</CipherData></EncryptedData></encryption>"
    )
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[
            ("ch1", "text/ch1.xhtml", _XHTML_TYPE),
            ("fig", "images/fig.png", "image/png"),
        ],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(b"<img src='../images/fig.png' alt='F'/>"),
            "OEBPS/images/fig.png": _PNG_BYTES,
            "META-INF/encryption.xml": encryption_xml,
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)
    md = (out / "book.md").read_text(encoding="utf-8")
    # The image ref must NOT appear — and the assets/ tree must not
    # contain an attempted decrypt either.
    assert "fig.png" not in md
    assert not (out / "assets" / "images" / "fig.png").exists()


def test_sanitization_collision_disambiguated(
    tmp_path: Path, build_epub: BuildEpubFn
) -> None:
    """Two distinct OPF hrefs that sanitize to the same name must end up
    at different on-disk paths AND get different md refs.

    Without disambiguation, ``images/a]b.png`` and ``images/a|b.png``
    both collapse to ``images/a_b.png`` and the second extraction
    overwrites the first.
    """
    epub_path = build_epub(
        tmp_path / "src" / "book.epub",
        manifest_items=[
            ("ch1", "text/ch1.xhtml", _XHTML_TYPE),
            ("a1", "images/a%5Db.png", "image/png"),  # %5D = ]
            ("a2", "images/a%7Cb.png", "image/png"),  # %7C = |
        ],
        spine_idrefs=["ch1"],
        files={
            "OEBPS/text/ch1.xhtml": xhtml(
                b"<img src='../images/a%5Db.png' alt='first'/>"
                b"<img src='../images/a%7Cb.png' alt='second'/>"
            ),
            "OEBPS/images/a]b.png": _PNG_BYTES,
            "OEBPS/images/a|b.png": _PNG_BYTES + b"\x00DIFFERENT",
        },
    )
    out = tmp_path / "out"
    EpubConverter().convert(epub_path, out)

    images = sorted(p.name for p in (out / "assets" / "images").iterdir())
    # Two distinct outputs, not one overwritten path.
    assert len(images) == 2, f"collision overwrote a file; saw: {images!r}"
    md = (out / "book.md").read_text(encoding="utf-8")
    # Both refs appear in the md, each pointing at its own file.
    for name in images:
        assert f"assets/images/{name}" in md


def test_bomless_utf16_xml_rejected(tmp_path: Path) -> None:
    """UTF-16 / UTF-32 XML without a BOM still has NUL bytes interleaved
    between ASCII characters — that bypasses both the BOM check and the
    ASCII ``<!DOCTYPE`` byte regex. Detect by scanning for NUL bytes in
    the leading bytes of the blob."""
    # UTF-16LE without BOM: each ASCII byte becomes ``<byte>\x00``.
    container = "<container/>".encode("utf-16-le")
    assert not container.startswith(b"\xff\xfe"), "fixture must not include a BOM"
    epub_path = _write_raw_zip(
        tmp_path / "src" / "broken.epub",
        entries={"META-INF/container.xml": container},
    )
    with pytest.raises(EpubParseError, match=r"NUL bytes"):
        EpubConverter().convert(epub_path, tmp_path / "out")
