"""Copy the original input into ``assets/`` and image-ref it from the
rendered markdown so dikw-core's md_inspect picks it up.

dikw-core's ``_DEFAULT_ASSET_EXTENSIONS`` already includes ``.pdf``, so
PDF provenance survives md_inspect even without a ref. For Office
formats (``.docx`` etc.) the extension isn't in the default set —
the image-ref is what keeps the file. We image-ref uniformly so the
two paths don't diverge.
"""

from __future__ import annotations

from pathlib import Path

from ._zip_extract import sanitize_asset_name, wikilink


def write_provenance(
    file_bytes: bytes, input_name: str, output_dir: Path
) -> str:
    """Write ``file_bytes`` to ``output_dir/assets/<safe_name>`` and
    return the wikilink line to append to the markdown.

    Takes bytes (not a Path) because the orchestrator already has the
    input in memory for hashing + uploading — re-reading 200 MB from
    disk here would be wasteful.
    """
    safe_name = sanitize_asset_name(input_name)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / safe_name).write_bytes(file_bytes)
    return wikilink(f"assets/{safe_name}", "original")
