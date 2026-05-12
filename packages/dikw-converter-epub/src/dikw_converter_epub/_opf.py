"""EPUB container / OPF parsing.

XML is parsed with stdlib ``ElementTree`` plus a hand-rolled
``<!DOCTYPE`` guard so we don't carry a ``defusedxml`` dependency.
EPUB container.xml and OPF files are not supposed to have DTDs;
rejecting any DTD declaration shuts down billion-laughs-style entity
expansion attacks at the parser boundary.
"""

from __future__ import annotations

import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ET

from ._models import ManifestItem, OpfPackage

_DOCTYPE_RE = re.compile(rb"<!\s*DOCTYPE", re.IGNORECASE)
# EPUB spec requires container.xml / OPF to be UTF-8. A UTF-16 or
# UTF-32 BOM means our ASCII-byte ``<!DOCTYPE`` regex would scan past
# the actual declaration bytes (each ASCII letter is interleaved with
# NULs). Reject these encodings outright at the parser entry point.
_NON_UTF8_BOMS = (
    b"\xff\xfe\x00\x00",  # UTF-32 LE
    b"\x00\x00\xfe\xff",  # UTF-32 BE
    b"\xff\xfe",  # UTF-16 LE
    b"\xfe\xff",  # UTF-16 BE
)


class EpubParseError(RuntimeError):
    """Raised when an EPUB is missing structural pieces we rely on."""


def _first_text(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    text = node.text or ""
    text = " ".join(text.split())
    return text or None


def _parse_xml(blob: bytes, *, source: str) -> ET.Element:
    for bom in _NON_UTF8_BOMS:
        if blob.startswith(bom):
            raise EpubParseError(
                f"{source}: refused non-UTF-8 XML encoding (BOM {bom!r}); "
                "EPUB spec requires UTF-8"
            )
    # BOM-less UTF-16/32 XML interleaves NUL bytes between ASCII chars,
    # which would slip past the BOM check above AND the ASCII-byte
    # DOCTYPE regex below. Legitimate UTF-8 EPUB XML never contains NUL
    # bytes in its prolog/declaration; reject anything that does.
    if b"\x00" in blob[:32]:
        raise EpubParseError(
            f"{source}: refused XML containing NUL bytes in its leading bytes "
            "(BOMless UTF-16/32 or other non-UTF-8 encoding)"
        )
    if _DOCTYPE_RE.search(blob):
        raise EpubParseError(f"{source}: refused XML containing <!DOCTYPE>")
    try:
        return ET.fromstring(blob)
    except ET.ParseError as e:
        raise EpubParseError(f"{source}: not valid XML: {e}") from e


def read_container(container_xml: bytes) -> str:
    """Return the OPF full-path from a ``META-INF/container.xml`` document."""
    root = _parse_xml(container_xml, source="container.xml")
    rootfile = root.find(".//{*}rootfile")
    if rootfile is None:
        raise EpubParseError("container.xml missing <rootfile>")
    full_path = rootfile.get("full-path")
    if not full_path:
        raise EpubParseError("container.xml <rootfile> missing full-path attribute")
    # full-path is also a URL per spec; decode percent-escapes so we
    # match the zip entry name on disk.
    return _decode_zip_path(full_path)


def parse_opf(opf_xml: bytes, opf_path: str) -> OpfPackage:
    """Parse the OPF rootfile into an :class:`OpfPackage`."""
    root = _parse_xml(opf_xml, source=opf_path)

    metadata = root.find(".//{*}metadata")
    title = _first_text(metadata.find(".//{*}title") if metadata is not None else None)
    creator = _first_text(metadata.find(".//{*}creator") if metadata is not None else None)

    manifest: dict[str, ManifestItem] = {}
    manifest_root = root.find(".//{*}manifest")
    if manifest_root is not None:
        for item in manifest_root.findall(".//{*}item"):
            item_id = item.get("id")
            href = item.get("href")
            if not item_id or not href:
                continue
            manifest[item_id] = ManifestItem(
                id=item_id,
                href=href,
                media_type=item.get("media-type"),
            )

    spine: list[str] = []
    spine_root = root.find(".//{*}spine")
    if spine_root is not None:
        for itemref in spine_root.findall(".//{*}itemref"):
            idref = itemref.get("idref")
            if idref:
                spine.append(idref)

    return OpfPackage(
        title=title,
        creator=creator,
        manifest=manifest,
        spine=spine,
        opf_dir=posixpath.dirname(opf_path),
    )


def resolve_href(base_dir: str, href: str) -> str | None:
    """Resolve an EPUB href to a normalized posix zip path.

    EPUB manifest hrefs are URL-encoded per spec, so ``chapter%201.xhtml``
    maps to the zip entry ``chapter 1.xhtml``. Fragments and queries are
    stripped. Anything with a non-empty scheme or netloc (``http://...``,
    ``//cdn/...``) is rejected as external — we can only resolve local
    in-zip paths.
    """
    parsed = urllib.parse.urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None
    decoded = urllib.parse.unquote(parsed.path)
    if not decoded:
        return None
    # Reject backslashes after percent-decode: they're path separators on
    # Windows but ``posixpath.normpath`` treats them as literal characters,
    # so an ``..%5C..%5Coutside.png`` would slip past the POSIX-only
    # traversal check and end up referenced from the markdown. We don't
    # try to be smart about it — backslashes never appear in legitimate
    # zip entry names, so refuse.
    if "\\" in decoded:
        return None
    joined = posixpath.join(base_dir, decoded) if base_dir else decoded
    return posixpath.normpath(joined)


def _decode_zip_path(href: str) -> str:
    """Like :func:`resolve_href` but for paths that are already absolute
    (no base_dir needed). Strip fragments/queries, percent-decode.
    """
    parsed = urllib.parse.urlsplit(href)
    return posixpath.normpath(urllib.parse.unquote(parsed.path))


def to_opf_relative(zip_path: str, opf_dir: str) -> str | None:
    """Express ``zip_path`` as a path under ``opf_dir``, or ``None`` if
    it escapes the OPF root.

    Returns ``None`` for anything that resolves outside ``opf_dir`` —
    including ``..`` traversal when the OPF lives at zip root. Single
    source of truth used both when rewriting markdown image refs and
    when picking which zip entries to extract.
    """
    if opf_dir:
        prefix = opf_dir + "/"
        if not zip_path.startswith(prefix):
            return None
        rel = zip_path[len(prefix) :]
    else:
        rel = zip_path
    rel = posixpath.normpath(rel)
    if rel == ".." or rel.startswith("../") or posixpath.isabs(rel):
        return None
    return rel
