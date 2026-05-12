"""Result-ZIP unpacking + markdown image-ref rewriting.

MinerU returns a ZIP containing roughly::

    full.md
    images/<hash>.png            (or sometimes top-level .png/.jpg)
    layout.json
    *_content_list.json
    *_model.json
    *_origin.pdf                 (sometimes present, ignored)

We:

1. Rename ``full.md`` to ``<stem>.md``.
2. Copy every image (extension in :data:`_IMAGE_EXTS`) to ``<output>/assets/``
   under a safe filename (no ``]`` or ``|`` — see EPUB plugin rationale).
3. Drop every other byproduct (``.json``, intermediate ``.pdf``, ``.html``).
4. Rewrite markdown image refs to wikilink form ``![[assets/<safe>|alt]]``,
   matching the EPUB plugin's contract so dikw-core's md_inspect picks
   them up uniformly.
5. Refuse zip-slip entries (``..`` in path, absolute paths).
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from io import BytesIO

from ._errors import MineruApiError

_FULL_MD = "full.md"
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp"})

# Match BOTH md image syntaxes:
#   ![alt](path)
#   ![[path|alt]]   (rare in MinerU output but defensive)
# Capture groups: 1=alt for standard form (may be empty), 2=path for
# standard form; 3=path for wikilink form, 4=alt for wikilink (may be
# absent). The | in alternation is literal because we escape it in the
# regex string.
_MD_IMAGE_RE = re.compile(
    r"!\[(?P<alt_std>[^\]]*?)\]\((?P<path_std>[^)]+?)\)"
    r"|!\[\[(?P<path_wiki>[^|\]]+?)(?:\|(?P<alt_wiki>[^\]]*?))?\]\]"
)


def sanitize_asset_name(name: str) -> str:
    """Replace characters that would break md_inspect's wikilink regex.

    ``]`` and ``|`` are the breakers; ``\\`` collapses to ``/`` so
    Windows-style separators (which MinerU shouldn't emit, but be
    defensive) don't leak into the asset path. Forward-slash is
    preserved as a directory separator within ``assets/``.
    """
    safe = name.replace("\\", "/")
    safe = safe.replace("]", "_").replace("|", "_")
    return safe.lstrip("/")


def _is_safe_zip_path(name: str) -> bool:
    """Reject paths that would escape the destination after extraction."""
    if not name or name.endswith("/"):
        return False
    normalized = posixpath.normpath(name.replace("\\", "/"))
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        return False
    # Reject absolute Windows paths too (e.g. ``C:/...``).
    return not (len(normalized) >= 2 and normalized[1] == ":")


def _basename_image(zip_name: str) -> str | None:
    """Return a sanitized asset basename for an image entry, or None
    if the entry isn't an image we want to keep.
    """
    base = posixpath.basename(zip_name)
    if not base:
        return None
    suffix = posixpath.splitext(base)[1].lower()
    if suffix not in _IMAGE_EXTS:
        return None
    return sanitize_asset_name(base)


def wikilink(rel_path: str, alt: str | None = None) -> str:
    """Render ``![[rel_path|alt]]``, with breaking chars in alt scrubbed."""
    safe_alt = (alt or "").replace("]", " ").replace("|", " ").strip()
    return f"![[{rel_path}|{safe_alt}]]" if safe_alt else f"![[{rel_path}]]"


def _rewrite_md_image_refs(md_text: str, asset_map: dict[str, str]) -> str:
    """Rewrite every image ref in ``md_text`` to point at ``assets/<safe>``.

    ``asset_map`` maps the **sanitized basename** of an in-zip image to
    its final asset path under ``assets/``. Refs that point at files
    not in the map (e.g. external URLs, JSON files) are left untouched —
    md_inspect will surface external URLs as warnings and ignore missing
    ones with our wikilink rewriting.
    """

    def _sub(m: re.Match[str]) -> str:
        if m.group("path_std") is not None:
            raw_path = m.group("path_std").strip()
            alt = m.group("alt_std")
        else:
            raw_path = (m.group("path_wiki") or "").strip()
            alt = m.group("alt_wiki")
        # MinerU often emits ``images/<hash>.png`` or just ``<hash>.png``.
        # Both shapes map by basename — we don't try to preserve
        # MinerU's intermediate directory layout.
        base = posixpath.basename(raw_path.replace("\\", "/"))
        safe_base = sanitize_asset_name(base)
        if safe_base in asset_map:
            return wikilink(asset_map[safe_base], alt)
        return m.group(0)

    return _MD_IMAGE_RE.sub(_sub, md_text)


def extract_result_zip(zip_bytes: bytes) -> tuple[str, dict[str, bytes]]:
    """Decode MinerU's result ZIP into ``(markdown_text, asset_files)``.

    ``asset_files`` keys are paths relative to ``output_dir`` (all start
    with ``assets/``) so the orchestrator writes them out without
    further path math. Image refs in the returned markdown are already
    rewritten to point at those keys.

    Raises :class:`MineruApiError` if the ZIP doesn't contain
    ``full.md`` — the one MinerU output guarantee we depend on.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise MineruApiError(f"MinerU result is not a valid ZIP: {exc}") from exc

    md_text: str | None = None
    assets: dict[str, bytes] = {}
    asset_map: dict[str, str] = {}

    with zf:
        for info in zf.infolist():
            name = info.filename
            if not _is_safe_zip_path(name):
                continue
            base = posixpath.basename(name)
            if base == _FULL_MD:
                md_text = zf.read(info).decode("utf-8", errors="replace")
                continue
            safe = _basename_image(name)
            if safe is None or safe in asset_map:
                # First-win on basename collisions keeps output deterministic.
                continue
            assets[f"assets/{safe}"] = zf.read(info)
            asset_map[safe] = f"assets/{safe}"

    if md_text is None:
        raise MineruApiError(
            "MinerU result ZIP did not contain full.md (got: "
            f"{sorted(name for name in zf.namelist())[:10]}…)"
        )

    # Normalize line endings before regex so the rewriter sees clean lines.
    md_text = md_text.replace("\r\n", "\n").replace("\r", "\n")
    md_text = _rewrite_md_image_refs(md_text, asset_map)
    if not md_text.endswith("\n"):
        md_text += "\n"
    return md_text, assets
