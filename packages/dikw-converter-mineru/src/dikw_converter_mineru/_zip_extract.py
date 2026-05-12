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
2. Copy every image (extension in :data:`_IMAGE_EXTS`) to
   ``<output>/assets/`` under a **normalized relative path** so that two
   images named ``fig.png`` in different ZIP directories don't collapse
   to one asset.
3. Drop every other byproduct (``.json``, intermediate ``.pdf``, ``.html``).
4. Rewrite markdown image refs to wikilink form
   ``![[assets/<path>|alt]]``. External URL refs
   (``https://...``) are left untouched — they aren't ours to rewrite,
   and rewriting them based on basename collision would silently swap
   in unrelated local assets.
5. Refuse zip-slip entries (``..`` in any normalized path component,
   absolute paths, Windows drive prefixes, backslash-encoded escapes).
6. Enforce per-entry + cumulative uncompressed size caps — defends
   against decompression bombs from the upstream CDN response.
"""

from __future__ import annotations

import posixpath
import re
import urllib.parse
import zipfile
from io import BytesIO

from ._errors import MineruApiError

_FULL_MD = "full.md"
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp"})

# Per-entry and total uncompressed size caps. Real MinerU result ZIPs
# tend to be < 50 MB; these caps are generous but not unbounded so a
# hostile / corrupt payload can't OOM the import process.
_MAX_ENTRY_UNCOMPRESSED = 64 * 1024 * 1024  # 64 MB per asset
_MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024  # 512 MB across all assets

# Match BOTH md image syntaxes:
#   ![alt](path)        — standard
#   ![[path|alt]]       — wikilink (rare in MinerU output but defensive)
_MD_IMAGE_RE = re.compile(
    r"!\[(?P<alt_std>[^\]]*?)\]\((?P<path_std>[^)]+?)\)"
    r"|!\[\[(?P<path_wiki>[^|\]]+?)(?:\|(?P<alt_wiki>[^\]]*?))?\]\]"
)


def sanitize_asset_name(name: str) -> str:
    """Replace characters that would break md_inspect's wikilink regex.

    ``]`` and ``|`` are the breakers. ``\\`` collapses to ``/`` so any
    backslash-encoded path separator can never sneak past
    :func:`_safe_relpath`. Forward-slashes are preserved as directory
    separators within ``assets/``.
    """
    safe = name.replace("\\", "/")
    safe = safe.replace("]", "_").replace("|", "_")
    return safe.lstrip("/")


def wikilink(rel_path: str, alt: str | None = None) -> str:
    """Render ``![[rel_path|alt]]``, scrubbing breaking chars in alt."""
    safe_alt = (alt or "").replace("]", " ").replace("|", " ").strip()
    return f"![[{rel_path}|{safe_alt}]]" if safe_alt else f"![[{rel_path}]]"


def _safe_relpath(raw_name: str) -> str | None:
    """Return a normalized POSIX relpath safe for extraction, or None.

    Critical: **normalize once, validate once** — every downstream
    decision (image filtering, asset keying, md rewriting) uses the
    result, so the validation order can't be reasoned around by a
    mixed-separator entry like ``a/b/..\\..\\escape.png``.
    """
    if not raw_name or raw_name.endswith("/"):
        return None
    # Backslash → forward-slash FIRST so windows-style separators in
    # raw zip entries can't slip past posixpath.normpath (which treats
    # ``\`` as part of a filename, not a separator).
    converted = raw_name.replace("\\", "/")
    normalized = posixpath.normpath(converted)
    if (
        normalized in ("", ".")
        or normalized == ".."
        or normalized.startswith("../")
        or normalized.startswith("/")
    ):
        return None
    # Reject any remaining ``..`` component anywhere in the path; normpath
    # collapses ``a/../b`` to ``b`` but leaves ``..\foo`` (after our
    # backslash conversion: ``../foo``) caught above. Belt-and-braces
    # against esoteric encodings.
    if any(part == ".." for part in normalized.split("/")):
        return None
    # Reject Windows absolute paths (``C:/...`` after conversion).
    if len(normalized) >= 2 and normalized[1] == ":":
        return None
    return sanitize_asset_name(normalized)


def _image_extension_ok(relpath: str) -> bool:
    return posixpath.splitext(relpath)[1].lower() in _IMAGE_EXTS


def _rewrite_md_image_refs(md_text: str, asset_map: dict[str, str]) -> str:
    """Rewrite md image refs to wikilink form when they resolve to an
    extracted asset. Match priority:

    1. Exact normalized relpath (``images/fig.png`` → ``assets/images/fig.png``).
    2. Basename-only fallback, but ONLY when that basename is unique
       across the asset set (so ``page1/fig.png`` and ``page2/fig.png``
       living side-by-side don't both rewrite to whichever was last).

    External URLs (anything with a scheme like ``http://``) are left
    untouched — they aren't ours and a basename collision must not
    swap in a local asset.
    """
    by_relpath: dict[str, str] = {}
    by_basename: dict[str, list[str]] = {}
    for relpath, asset_path in asset_map.items():
        by_relpath[relpath] = asset_path
        by_basename.setdefault(posixpath.basename(relpath), []).append(asset_path)

    def _sub(m: re.Match[str]) -> str:
        if m.group("path_std") is not None:
            raw_path = m.group("path_std").strip()
            alt = m.group("alt_std")
        else:
            raw_path = (m.group("path_wiki") or "").strip()
            alt = m.group("alt_wiki")

        # External URL: leave untouched. ``urlparse`` exposes a scheme
        # for ``http://``, ``https://``, ``data:``, ``//example.com``
        # (netloc), etc.
        parsed = urllib.parse.urlparse(raw_path)
        if parsed.scheme or parsed.netloc:
            return m.group(0)

        cleaned = sanitize_asset_name(raw_path)
        if cleaned in by_relpath:
            return wikilink(by_relpath[cleaned], alt)
        base = posixpath.basename(cleaned)
        candidates = by_basename.get(base, [])
        if len(candidates) == 1:
            return wikilink(candidates[0], alt)
        return m.group(0)

    return _MD_IMAGE_RE.sub(_sub, md_text)


def extract_result_zip(zip_bytes: bytes) -> tuple[str, dict[str, bytes]]:
    """Decode MinerU's result ZIP into ``(markdown_text, asset_files)``.

    ``asset_files`` keys are paths relative to ``output_dir`` (all
    starting with ``assets/``) so the orchestrator can write them out
    without further path math. Image refs in the returned markdown are
    already rewritten to point at those keys.

    Raises :class:`MineruApiError` if:

    - the ZIP is malformed
    - it doesn't contain ``full.md``
    - any entry's uncompressed size exceeds :data:`_MAX_ENTRY_UNCOMPRESSED`
    - the cumulative uncompressed size exceeds :data:`_MAX_TOTAL_UNCOMPRESSED`
    """
    try:
        zf = zipfile.ZipFile(BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise MineruApiError(f"MinerU result is not a valid ZIP: {exc}") from exc

    md_text: str | None = None
    assets: dict[str, bytes] = {}
    asset_map: dict[str, str] = {}  # normalized relpath → "assets/<relpath>"
    cumulative_bytes = 0

    with zf:
        for info in zf.infolist():
            # Decompression-bomb guard: refuse oversized entries before
            # we read them.
            if info.file_size > _MAX_ENTRY_UNCOMPRESSED:
                raise MineruApiError(
                    f"MinerU ZIP entry {info.filename!r} declares "
                    f"{info.file_size} bytes uncompressed, exceeds per-entry "
                    f"cap {_MAX_ENTRY_UNCOMPRESSED}"
                )
            if cumulative_bytes + info.file_size > _MAX_TOTAL_UNCOMPRESSED:
                raise MineruApiError(
                    f"MinerU ZIP cumulative uncompressed size would exceed "
                    f"{_MAX_TOTAL_UNCOMPRESSED} bytes; refusing to extract"
                )

            relpath = _safe_relpath(info.filename)
            if relpath is None:
                continue

            base = posixpath.basename(relpath)
            if base == _FULL_MD:
                md_text = zf.read(info).decode("utf-8", errors="replace")
                cumulative_bytes += info.file_size
                continue
            if not _image_extension_ok(relpath):
                # JSON, intermediate PDFs, anything not an image: drop.
                continue
            if relpath in asset_map:
                continue
            assets[f"assets/{relpath}"] = zf.read(info)
            asset_map[relpath] = f"assets/{relpath}"
            cumulative_bytes += info.file_size

    if md_text is None:
        raise MineruApiError(
            "MinerU result ZIP did not contain full.md (got: "
            f"{sorted(name for name in zf.namelist())[:10]}…)"
        )

    md_text = md_text.replace("\r\n", "\n").replace("\r", "\n")
    md_text = _rewrite_md_image_refs(md_text, asset_map)
    if not md_text.endswith("\n"):
        md_text += "\n"
    return md_text, assets
