"""Orchestrator for one EPUB → md + assets conversion.

Kept separate from ``__init__.py`` so the public surface module pays no
import cost during dikw-core's plugin discovery pass.

Output contract: the caller (dikw-core's importer) gives us a fresh,
empty ``output_dir`` (a freshly-mkdtemp'd staging path). We assume the
directory is empty and not a symlink. If you're calling
:class:`EpubConverter` outside the dikw client flow, ensure
``output_dir`` is a fresh path you control — re-using a dirty path will
leave stale assets behind and writing into a symlinked dest will follow
the link.

Flow is two-pass on purpose:

1. Walk every spine chapter, collect raw blocks and image refs.
2. Compute which referenced images are actually extractable (in zip,
   not encrypted, inside the OPF directory).
3. Build a collision-free safe-name map covering every extractable
   image AND the provenance ``.epub`` copy. The same names are reused
   when writing files and when rewriting markdown image refs.
4. Filter each chapter's image blocks to drop unextractable ones, so
   the rendered markdown never references an asset we won't write.
5. Render markdown, write files.

Splitting (1)/(2)/(3)/(4) avoids the failure mode where an encrypted or
missing image leaves a dangling ``![[...]]`` in the md, which dikw-core
would surface as ``asset_missing`` during preflight.
"""

from __future__ import annotations

import shutil
import urllib.parse
import zipfile
from pathlib import Path

from ._html_blocks import extract_blocks
from ._models import Block, ImageRef, OpfPackage
from ._opf import (
    EpubParseError,
    _parse_xml,
    parse_opf,
    read_container,
    resolve_href,
    to_opf_relative,
)
from ._render import render_markdown

_CONTAINER_PATH = "META-INF/container.xml"
_ENCRYPTION_PATH = "META-INF/encryption.xml"
_ASSETS_SUBDIR = "assets"
_CHAPTER_FALLBACK_FORMAT = "chapter-{:03d}"

# Decompression caps. EPUBs are typically a few MB; legitimate art /
# comic books climb into the low hundreds. Anything past these limits
# is either malformed or a decompression bomb — abort rather than
# spend disk on it. Values are deliberately generous so real corpora
# stay below them and constants live here for easy tuning.
_MAX_OPF_SIZE = 4 * 1024 * 1024  # 4 MiB — OPF + container.xml are tiny
_MAX_CHAPTER_SIZE = 16 * 1024 * 1024  # 16 MiB — a huge single XHTML
_MAX_ASSET_SIZE = 64 * 1024 * 1024  # 64 MiB — high-res cover, oversized photo
_MAX_TOTAL_DECOMPRESSED = 512 * 1024 * 1024  # 512 MiB — whole-book ceiling


def run_convert(input_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / _ASSETS_SUBDIR
    assets_dir.mkdir(exist_ok=True)
    stem = input_path.stem

    with zipfile.ZipFile(input_path, "r") as zf:
        zip_names = set(zf.namelist())
        budget = _DecompressionBudget(_MAX_TOTAL_DECOMPRESSED)
        encrypted_targets = _read_encryption_targets(zf, zip_names, budget)

        try:
            container_bytes = _safe_read(zf, _CONTAINER_PATH, _MAX_OPF_SIZE, budget)
        except KeyError as e:
            raise EpubParseError(f"{input_path.name}: missing {_CONTAINER_PATH}") from e

        opf_path = read_container(container_bytes)
        if opf_path in encrypted_targets:
            raise EpubParseError(
                f"{input_path.name}: OPF rootfile {opf_path!r} is encrypted (DRM)"
            )
        try:
            opf_bytes = _safe_read(zf, opf_path, _MAX_OPF_SIZE, budget)
        except KeyError as e:
            raise EpubParseError(
                f"{input_path.name}: container.xml points at {opf_path!r} which is not in the zip"
            ) from e

        package = parse_opf(opf_bytes, opf_path)
        if not package.spine:
            raise EpubParseError(f"{input_path.name}: OPF spine is empty")

        raw_chapters, seen_image_paths = _walk_spine(
            zf,
            zip_names,
            package,
            encrypted_targets,
            budget,
            error_prefix=input_path.name,
        )

        extractable = _select_extractable_images(
            set(seen_image_paths),
            zip_names=zip_names,
            opf_dir=package.opf_dir,
            encrypted_targets=encrypted_targets,
        )

        name_map = _build_safe_name_map(
            extractable_zip_paths=sorted(extractable),
            opf_dir=package.opf_dir,
            provenance_filename=input_path.name,
        )

        chapters = [
            (title, _finalize_image_blocks(blocks, extractable, name_map))
            for title, blocks in raw_chapters
        ]

        _extract_images(zf, extractable, name_map, assets_dir, budget)

    safe_provenance = name_map.provenance
    shutil.copyfile(input_path, assets_dir / safe_provenance)

    md_text = render_markdown(
        chapters,
        title=package.title,
        creator=package.creator,
        provenance_path=f"{_ASSETS_SUBDIR}/{safe_provenance}",
    )
    (output_dir / f"{stem}.md").write_text(md_text, encoding="utf-8")


def _walk_spine(
    zf: zipfile.ZipFile,
    zip_names: set[str],
    package: OpfPackage,
    encrypted_targets: frozenset[str],
    budget: _DecompressionBudget,
    *,
    error_prefix: str,
) -> tuple[list[tuple[str, list[Block]]], dict[str, ImageRef]]:
    """Phase 1: read each spine chapter, return raw blocks + image refs.

    The returned blocks still carry raw zip paths in ``image``; phase 4
    rewrites them once the safe-name map is built.
    """
    raw_chapters: list[tuple[str, list[Block]]] = []
    seen_image_paths: dict[str, ImageRef] = {}
    missing_chapters: list[str] = []

    for index, idref in enumerate(package.spine):
        item = package.manifest.get(idref)
        if item is None:
            missing_chapters.append(f"spine idref {idref!r} not in manifest")
            continue
        html_path = resolve_href(package.opf_dir, item.href)
        if html_path is None or html_path not in zip_names:
            missing_chapters.append(
                f"spine item {idref!r} href {item.href!r} not in zip"
            )
            continue
        if html_path in encrypted_targets:
            missing_chapters.append(
                f"spine item {idref!r} href {item.href!r} is encrypted (DRM)"
            )
            continue
        html_bytes = _safe_read(zf, html_path, _MAX_CHAPTER_SIZE, budget)
        blocks, image_refs = extract_blocks(html_bytes, html_path)
        chapter_title = _chapter_title_for(blocks, index)
        for ref in image_refs:
            seen_image_paths.setdefault(ref.zip_path, ref)
        raw_chapters.append((chapter_title, blocks))

    if missing_chapters:
        joined = "; ".join(missing_chapters)
        raise EpubParseError(
            f"{error_prefix}: spine references unresolved chapters: {joined}"
        )
    if not raw_chapters:
        raise EpubParseError(
            f"{error_prefix}: spine resolved to zero readable chapters"
        )
    return raw_chapters, seen_image_paths


def _select_extractable_images(
    zip_paths: set[str],
    *,
    zip_names: set[str],
    opf_dir: str,
    encrypted_targets: frozenset[str],
) -> frozenset[str]:
    """Phase 2: filter image zip paths to those we can actually extract.

    An image is extractable iff it exists in the zip, lives under the
    OPF directory, and is not encrypted. Anything else is dropped both
    from the markdown (phase 4) and from extraction (phase 5) so the md
    never carries refs we don't follow through on.
    """
    return frozenset(
        zip_path
        for zip_path in zip_paths
        if zip_path in zip_names
        and zip_path not in encrypted_targets
        and to_opf_relative(zip_path, opf_dir) is not None
    )


class _SafeNameMap:
    """Bijective zip-path → safe-relative-path map.

    Built once and consumed by both markdown rewriting and image
    extraction so the md ref and the on-disk path can never disagree.
    Collisions from the per-character sanitization are resolved with a
    counter suffix, in sorted-input order, so output is deterministic.
    """

    def __init__(self, images: dict[str, str], provenance: str) -> None:
        self._images = images
        self.provenance = provenance

    def safe_path_for(self, zip_path: str) -> str | None:
        return self._images.get(zip_path)

    def image_items(self) -> list[tuple[str, str]]:
        return sorted(self._images.items())


def _build_safe_name_map(
    *,
    extractable_zip_paths: list[str],
    opf_dir: str,
    provenance_filename: str,
) -> _SafeNameMap:
    """Phase 3: assign each extractable image (and the provenance .epub)
    a wikilink-safe relative path inside ``assets/`` with no collisions.

    Sanitization of ``]`` / ``|`` / ``\\`` is per-character; two distinct
    inputs can collapse to the same candidate (``a]b.png`` and
    ``a|b.png`` both become ``a_b.png``). Detect collisions and append
    a counter suffix (``a_b.png`` / ``a_b-2.png``). The whole map shares
    one taken-name pool — the provenance filename can collide with an
    EPUB-internal asset, theoretically, and must also be deconflicted.
    """
    taken: set[str] = set()
    images: dict[str, str] = {}

    provenance_safe = _unique_name(_sanitize_path(provenance_filename), taken)
    taken.add(provenance_safe)

    for zip_path in extractable_zip_paths:
        rel = to_opf_relative(zip_path, opf_dir)
        # ``rel`` is non-None for extractable paths by construction.
        assert rel is not None
        candidate = _unique_name(_sanitize_path(rel), taken)
        taken.add(candidate)
        images[zip_path] = candidate

    return _SafeNameMap(images=images, provenance=provenance_safe)


def _sanitize_path(rel: str) -> str:
    """Substitute characters that md_inspect's wikilink regex can't capture."""
    return rel.replace("\\", "/").replace("]", "_").replace("|", "_")


def _unique_name(candidate: str, taken: set[str]) -> str:
    """Return ``candidate``, or ``candidate`` with a ``-N`` suffix before
    the file extension if ``candidate`` is already taken."""
    if candidate not in taken:
        return candidate
    parent, sep, name = candidate.rpartition("/")
    base, dot, ext = name.partition(".")
    n = 2
    while True:
        suffix = f"{base}-{n}.{ext}" if dot else f"{name}-{n}"
        attempt = f"{parent}{sep}{suffix}" if sep else suffix
        if attempt not in taken:
            return attempt
        n += 1


def _finalize_image_blocks(
    blocks: list[Block], extractable: frozenset[str], name_map: _SafeNameMap
) -> list[Block]:
    """Phase 4: rewrite ``image`` to its final ``assets/<safe>`` path
    and drop image blocks whose zip path won't actually be extracted."""
    result: list[Block] = []
    for block in blocks:
        if block.type == "image" and block.image:
            if block.image not in extractable:
                continue
            safe = name_map.safe_path_for(block.image)
            assert safe is not None  # extractable implies presence in the map
            block.image = f"{_ASSETS_SUBDIR}/{safe}"
        result.append(block)
    return result


def _chapter_title_for(blocks: list[Block], spine_index: int) -> str:
    """First heading text in the chapter, else a deterministic fallback."""
    for block in blocks:
        if block.type == "heading" and block.text:
            return block.text
    return _CHAPTER_FALLBACK_FORMAT.format(spine_index + 1)


def _read_encryption_targets(
    zf: zipfile.ZipFile, zip_names: set[str], budget: _DecompressionBudget
) -> frozenset[str]:
    """Return the set of zip paths declared encrypted by encryption.xml.

    A common case is Adobe / Calibre font-obfuscation: ``encryption.xml``
    lists embedded TrueType files, but the spine XHTML stays plaintext.
    We allow those EPUBs to convert (the obfuscated fonts are simply
    not extracted as image assets) and only refuse if the OPF or a
    spine document is in the encrypted set.
    """
    if _ENCRYPTION_PATH not in zip_names:
        return frozenset()
    try:
        blob = _safe_read(zf, _ENCRYPTION_PATH, _MAX_OPF_SIZE, budget)
    except KeyError:
        return frozenset()
    root = _parse_xml(blob, source=_ENCRYPTION_PATH)
    targets: set[str] = set()
    for ref in root.findall(".//{*}CipherReference"):
        uri = ref.get("URI")
        if not uri:
            continue
        parsed = urllib.parse.urlsplit(uri)
        if parsed.scheme or parsed.netloc:
            continue
        decoded = urllib.parse.unquote(parsed.path)
        if not decoded or "\\" in decoded:
            continue
        targets.add(decoded.lstrip("/"))
    return frozenset(targets)


class _DecompressionBudget:
    """Track cumulative decompressed bytes against a total ceiling.

    A decompression-bomb EPUB might have many small entries whose
    individual sizes stay under the per-entry caps but whose sum is
    monstrous. The total budget catches that case.
    """

    def __init__(self, total_cap: int) -> None:
        self._total_cap = total_cap
        self._consumed = 0

    def reserve(self, size: int, *, name: str) -> None:
        if self._consumed + size > self._total_cap:
            raise EpubParseError(
                f"{name}: cumulative decompressed size would exceed "
                f"{self._total_cap} bytes (decompression bomb?)"
            )
        self._consumed += size


def _safe_read(
    zf: zipfile.ZipFile, name: str, per_entry_cap: int, budget: _DecompressionBudget
) -> bytes:
    """Read a single zip entry, refusing oversized or budget-busting entries."""
    info = zf.getinfo(name)
    if info.file_size > per_entry_cap:
        raise EpubParseError(
            f"{name}: declared size {info.file_size} bytes exceeds cap {per_entry_cap}"
        )
    budget.reserve(info.file_size, name=name)
    return zf.read(name)


def _extract_images(
    zf: zipfile.ZipFile,
    extractable: frozenset[str],
    name_map: _SafeNameMap,
    assets_dir: Path,
    budget: _DecompressionBudget,
) -> None:
    """Phase 5: stream every extractable image into ``assets/<safe>``.

    By this point the caller has already filtered out anything outside
    the OPF directory, encrypted, or missing — every entry in
    ``extractable`` is guaranteed to map to a unique safe name in
    ``name_map``. The only failure modes left are oversized assets (raise)
    and zip-slip on the resolved on-disk target (silent skip — the md
    ref was already dropped in phase 4, so nothing dangles).
    """
    safe_root = assets_dir.resolve()
    for zip_path, safe_rel in name_map.image_items():
        if zip_path not in extractable:
            continue
        info = zf.getinfo(zip_path)
        if info.file_size > _MAX_ASSET_SIZE:
            raise EpubParseError(
                f"{zip_path}: image size {info.file_size} bytes exceeds cap {_MAX_ASSET_SIZE}"
            )
        budget.reserve(info.file_size, name=zip_path)
        target = (assets_dir / safe_rel).resolve()
        try:
            target.relative_to(safe_root)
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(zip_path) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)


