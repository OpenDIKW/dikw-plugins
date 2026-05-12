"""Synthetic EPUB factory for the dikw-converter-epub test suite.

Every test builds the EPUB it needs in-memory via ``zipfile`` rather
than checking large binary fixtures into git. ``build_epub`` mirrors
the real EPUB on-disk layout but keeps the inputs declarative so each
test reads as a recipe.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pytest

BuildEpubFn = Callable[..., Path]

_CONTAINER_XML = """\
<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="{opf_path}" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def xhtml(body: bytes) -> bytes:
    """Wrap ``body`` (raw inner XHTML) in a minimal XHTML document."""
    return (
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<html xmlns='http://www.w3.org/1999/xhtml'>"
        b"<body>" + body + b"</body></html>"
    )


def _default_opf(
    *,
    manifest_items: Iterable[tuple[str, str, str]],
    spine_idrefs: Iterable[str],
    title: str | None,
    creator: str | None,
) -> str:
    metadata_lines: list[str] = []
    if title is not None:
        metadata_lines.append(f"    <dc:title>{title}</dc:title>")
    if creator is not None:
        metadata_lines.append(f"    <dc:creator>{creator}</dc:creator>")
    metadata_block = "\n".join(metadata_lines)

    manifest_lines = "\n".join(
        f'    <item id="{item_id}" href="{href}" media-type="{media_type}"/>'
        for item_id, href, media_type in manifest_items
    )
    spine_lines = "\n".join(f'    <itemref idref="{idref}"/>' for idref in spine_idrefs)

    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
{metadata_block}
  </metadata>
  <manifest>
{manifest_lines}
  </manifest>
  <spine>
{spine_lines}
  </spine>
</package>
"""


def _build_epub(
    target: Path,
    *,
    opf_path: str = "OEBPS/content.opf",
    title: str | None = "Sample",
    creator: str | None = "Sample Author",
    manifest_items: Iterable[tuple[str, str, str]] = (),
    spine_idrefs: Iterable[str] = (),
    files: Mapping[str, bytes] | None = None,
) -> Path:
    """Build a minimal-but-valid EPUB at ``target``.

    Tests that need malformed input (missing container.xml, missing OPF)
    construct the zip directly with ``zipfile`` rather than going
    through here — pushing override hooks into this factory would muddy
    it without simplifying any caller.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    container = _CONTAINER_XML.format(opf_path=opf_path)
    opf = _default_opf(
        manifest_items=list(manifest_items),
        spine_idrefs=list(spine_idrefs),
        title=title,
        creator=creator,
    )

    with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Per EPUB spec, mimetype must be the first entry and uncompressed.
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr(opf_path, opf)
        for zip_path, data in (files or {}).items():
            zf.writestr(zip_path, data)

    return target


@pytest.fixture
def build_epub() -> BuildEpubFn:
    return _build_epub
