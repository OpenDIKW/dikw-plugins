"""Top-level orchestrator: ``run_convert(input_path, output_dir)``.

Five phases, each isolated so failure in one cleanly aborts the run:

1. **Pre-check** — read API key from env (or constructor), validate file
   size against MinerU's 200 MB limit, stream-hash the input to compute
   a ``data_id`` for server-side cacheability.
2. **Submit & upload** — POST batch URL → PUT presigned URL.
3. **Poll** — wait for ``state: done``, fail clean on any other terminal.
4. **Download & extract** — fetch result ZIP, rename ``full.md`` →
   ``<stem>.md``, copy image assets, rewrite md refs.
5. **Write** — assemble in a staging tempdir under ``output_dir`` then
   move entries up one level, so a mid-run crash leaves ``output_dir``
   empty rather than half-populated.

All phases share a single :class:`httpx.Client` to reuse the TCP
connection. No phase loads the input file or the result ZIP fully into
memory if streaming will do — the plugin must tolerate 200 MB inputs
on resource-constrained client machines.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

import httpx

from ._client import MineruClient, SubmitParams
from ._config import resolve_api_key
from ._errors import MineruApiError, MineruInputError
from ._provenance import write_provenance
from ._zip_extract import extract_result_zip

# MinerU's documented hard cap for v4 batch API.
_MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024  # 200 MB

# MinerU's ``model_version`` applies only to PDFs (its VLM pipeline).
# Office formats use the default pipeline; passing ``vlm`` would either
# be ignored or rejected.
_PDF_EXTENSIONS = frozenset({".pdf"})

# Streaming chunk size for the SHA-256 pre-flight.
_HASH_CHUNK = 1024 * 1024  # 1 MiB


def _model_version_for(input_path: Path) -> str | None:
    return "vlm" if input_path.suffix.lower() in _PDF_EXTENSIONS else None


def _data_id_for(input_path: Path) -> str:
    """SHA-256 of the input streamed in 1 MiB chunks, first 32 hex chars.

    Stable across runs of the same file → MinerU server-side cache hits
    are deterministic. Streaming avoids holding the whole 200 MB file
    in RAM just for one hash.
    """
    h = hashlib.sha256()
    with input_path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()[:32]


def _ensure_within(target: Path, root: Path) -> None:
    """Raise if ``target`` resolves outside ``root``.

    Belt-and-braces against any path-traversal entry that slipped past
    :func:`_zip_extract._safe_relpath`. The cost is one ``resolve()``
    per asset; the benefit is a containment line of defense even if a
    future refactor weakens the upstream check.
    """
    try:
        target.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise MineruApiError(
            f"Refusing to write outside staging root: {target}"
        ) from exc


def run_convert(
    input_path: Path,
    output_dir: Path,
    *,
    explicit_api_key: str | None = None,
) -> None:
    """Implementation of :meth:`MineruConverter.convert`.

    Kept as a module-level function (rather than methods on the
    Converter class) so the discovery-pass instantiation path stays
    free of HTTP imports.
    """
    if not input_path.is_file():
        raise MineruInputError(f"Input path is not a file: {input_path}")

    file_size = input_path.stat().st_size
    if file_size > _MAX_FILE_SIZE_BYTES:
        raise MineruInputError(
            f"Input file is {file_size / 1_048_576:.1f} MB, exceeds "
            f"MinerU's 200 MB limit. Split or use a local engine."
        )

    api_key = resolve_api_key(explicit_api_key)
    params = SubmitParams(
        file_name=input_path.name,
        data_id=_data_id_for(input_path),
        model_version=_model_version_for(input_path),
    )

    # Stage under output_dir so the final move is a same-filesystem
    # rename (effectively atomic per entry).
    output_dir.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix="mineru-stg-", dir=str(output_dir)))
    staging_resolved = staging_root.resolve(strict=False)

    try:
        with httpx.Client(timeout=60.0) as client:
            api = MineruClient(client=client, token=api_key)
            handle = api.submit(params)
            api.upload(handle.upload_url, input_path)
            zip_url = api.poll_until_done(handle.batch_id)
            zip_bytes = api.download_zip(zip_url)

        md_text, asset_files = extract_result_zip(zip_bytes)
        del zip_bytes  # release the result ZIP ASAP; can be 10s of MB

        (staging_root / "assets").mkdir(parents=True, exist_ok=True)
        for rel_path, data in asset_files.items():
            target = staging_root / rel_path
            _ensure_within(target, staging_resolved)
            target.parent.mkdir(parents=True, exist_ok=True)
            _ensure_within(target.parent, staging_resolved)
            target.write_bytes(data)

        # Provenance is copied straight from disk — no need to hold the
        # full input file in RAM. The wikilink ref keeps the markdown's
        # asset graph complete for dikw-core's md_inspect pass.
        provenance_ref = write_provenance(input_path, staging_root)

        md_with_provenance = md_text.rstrip("\n") + "\n\n" + provenance_ref + "\n"
        (staging_root / f"{input_path.stem}.md").write_text(
            md_with_provenance, encoding="utf-8"
        )

        _publish(staging_root, output_dir)
    except BaseException:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise


def _publish(staging: Path, output_dir: Path) -> None:
    """Move every top-level entry from ``staging`` into ``output_dir``,
    then remove the now-empty staging directory.

    ``shutil.move`` handles the same-filesystem rename fast path plus
    the target-already-exists case (overwrites files, refuses for dirs
    — so we pre-clean dirs).
    """
    for entry in staging.iterdir():
        target = output_dir / entry.name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        shutil.move(str(entry), str(target))
    shutil.rmtree(staging, ignore_errors=True)
