"""Copy the original input into ``assets/`` and image-ref it from the
rendered markdown so dikw-core's md_inspect picks it up.

dikw-core's ``_DEFAULT_ASSET_EXTENSIONS`` already includes ``.pdf``, so
PDF provenance survives md_inspect even without a ref. For Office
formats (``.docx`` etc.) the extension isn't in the default set —
the image-ref is what keeps the file. We image-ref uniformly so the
two paths don't diverge.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ._zip_extract import sanitize_asset_name, wikilink


def write_provenance(input_path: Path, output_dir: Path) -> str:
    """Stream-copy the input into ``output_dir/assets/<safe_name>`` and
    return the wikilink to append to the markdown.

    Streaming (``shutil.copyfile``) keeps RAM flat for 200 MB inputs.
    The sanitized name is computed once and used both for the on-disk
    filename and the markdown ref so md_inspect's asset graph stays
    consistent.
    """
    safe_name = sanitize_asset_name(input_path.name)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_path, assets_dir / safe_name)
    return wikilink(f"assets/{safe_name}", "original")
