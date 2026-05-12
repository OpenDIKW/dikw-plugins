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
2. Copy every local asset candidate (extension in :data:`_ASSET_EXTS`) to
   ``<output>/assets/`` under a **normalized relative path** so that two
   images named ``fig.png`` in different ZIP directories don't collapse
   to one asset.
3. Drop every unreferenced byproduct (``.json``, intermediate ``.pdf``,
   ``.html``).
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
_ASSET_EXTS = _IMAGE_EXTS | frozenset({".pdf"})

# Per-entry and total uncompressed size caps. Real MinerU result ZIPs
# tend to be < 50 MB; these caps are generous but not unbounded so a
# hostile / corrupt payload can't OOM the import process.
_MAX_ENTRY_UNCOMPRESSED = 64 * 1024 * 1024  # 64 MB per asset
_MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024  # 512 MB across all assets

# Match BOTH md image syntaxes:
#   ![alt](path "title") — standard, with optional title
#   ![[path|alt]]        — wikilink (rare in MinerU output but defensive)
_MD_IMAGE_RE = re.compile(
    r"!\[(?P<alt_std>[^\]]*?)\]\(\s*(?P<path_std>[^)\n]+?)"
    r"(?=\s+\"[^\"\n]*\"\s*\)|\s*\))"
    r"(?:\s+\"[^\"\n]*\")?\s*\)"
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
    # Reject ``:`` anywhere in any component — covers Windows absolute
    # paths (``C:/...``), Windows alternate-data-stream syntax
    # (``fig.png:stream``), and the colon-prefixed weird names ZIPs
    # produced by some converters.
    if any(":" in part for part in normalized.split("/")):
        return None
    return sanitize_asset_name(normalized)


def _asset_extension_ok(relpath: str) -> bool:
    return posixpath.splitext(relpath)[1].lower() in _ASSET_EXTS


def _normalize_md_asset_ref(raw_path: str) -> str:
    """Normalize a local markdown asset ref into the ZIP relpath form."""
    decoded = urllib.parse.unquote(raw_path)
    cleaned = sanitize_asset_name(decoded)
    normalized = posixpath.normpath(cleaned)
    if normalized in ("", "."):
        return cleaned
    return normalized.lstrip("/")


def _rewrite_md_image_refs(
    md_text: str, asset_map: dict[str, str]
) -> tuple[str, set[str]]:
    """Rewrite md image refs to wikilink form when they resolve to an
    extracted asset. Returns the rewritten text plus the set of asset
    paths (``"assets/<relpath>"``) actually referenced — callers use
    this to drop orphan assets so dikw-core's md_inspect doesn't reject
    the import.

    Match priority:

    1. Exact normalized relpath (``images/fig.png`` → ``assets/images/fig.png``).
    2. Case-insensitive relpath fallback, but only when unique.
    3. Basename-only fallback, but ONLY when that basename is unique
       across the asset set (so ``page1/fig.png`` and ``page2/fig.png``
       living side-by-side don't both rewrite to whichever was last).

    External URLs (anything with a scheme like ``http://``) are left
    untouched — they aren't ours and a basename collision must not
    swap in a local asset.
    """
    by_relpath: dict[str, str] = {}
    by_relpath_folded: dict[str, list[str]] = {}
    by_basename: dict[str, list[str]] = {}
    by_basename_folded: dict[str, list[str]] = {}
    for relpath, asset_path in asset_map.items():
        by_relpath[relpath] = asset_path
        by_relpath_folded.setdefault(relpath.casefold(), []).append(asset_path)
        by_basename.setdefault(posixpath.basename(relpath), []).append(asset_path)
        by_basename_folded.setdefault(
            posixpath.basename(relpath).casefold(), []
        ).append(asset_path)
    referenced: set[str] = set()

    def _wikilink_if_unique(candidates: list[str], alt: str | None) -> str | None:
        if len(candidates) != 1:
            return None
        referenced.add(candidates[0])
        return wikilink(candidates[0], alt)

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
        decoded = urllib.parse.unquote(raw_path)
        decoded_parsed = urllib.parse.urlparse(decoded)
        if (
            parsed.scheme
            or parsed.netloc
            or decoded_parsed.scheme
            or decoded_parsed.netloc
        ):
            return m.group(0)

        cleaned = _normalize_md_asset_ref(raw_path)
        if cleaned in by_relpath:
            asset_path = by_relpath[cleaned]
            referenced.add(asset_path)
            return wikilink(asset_path, alt)
        rewritten = _wikilink_if_unique(
            by_relpath_folded.get(cleaned.casefold(), []), alt
        )
        if rewritten is not None:
            return rewritten
        base = posixpath.basename(cleaned)
        rewritten = _wikilink_if_unique(by_basename.get(base, []), alt)
        if rewritten is not None:
            return rewritten
        rewritten = _wikilink_if_unique(
            by_basename_folded.get(base.casefold(), []), alt
        )
        if rewritten is not None:
            return rewritten
        return m.group(0)

    return _MD_IMAGE_RE.sub(_sub, md_text), referenced


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
        try:
            infos = zf.infolist()
            names = zf.namelist()
            for info in infos:
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

                # ``full.md`` must live at the ZIP root. A ``nested/full.md``
                # smuggled in by a malicious or buggy upstream would otherwise
                # override the real body.
                if relpath == _FULL_MD:
                    md_text = zf.read(info).decode("utf-8", errors="replace")
                    cumulative_bytes += info.file_size
                    continue
                if not _asset_extension_ok(relpath):
                    # JSON, Office byproducts, HTML, anything not referenced
                    # by dikw-core as a local asset candidate: drop.
                    continue
                if relpath in asset_map:
                    continue
                assets[f"assets/{relpath}"] = zf.read(info)
                asset_map[relpath] = f"assets/{relpath}"
                cumulative_bytes += info.file_size
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
            raise MineruApiError(f"MinerU result ZIP could not be read: {exc}") from exc

    if md_text is None:
        raise MineruApiError(
            "MinerU result ZIP did not contain full.md (got: "
            f"{sorted(names)[:10]}…)"
        )

    md_text = md_text.replace("\r\n", "\n").replace("\r", "\n")
    md_text, referenced = _rewrite_md_image_refs(md_text, asset_map)
    # Drop orphan assets — dikw-core's md_inspect rejects any asset
    # whose extension is in the default set (png/jpg/etc.) but isn't
    # image-ref'd from the markdown. MinerU sometimes ships extra
    # thumbnails or layout-only crops the body never mentions.
    assets = {k: v for k, v in assets.items() if k in referenced}
    if not md_text.endswith("\n"):
        md_text += "\n"
    return md_text, assets
