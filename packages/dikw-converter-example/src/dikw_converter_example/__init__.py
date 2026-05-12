"""Reference stub Converter — wraps any ``.example`` file's text into a
single-block markdown source.

This package exists to be copied as the template for real plugins
(``dikw-converter-pdf``, ``dikw-converter-epub``, …). The conversion
itself is intentionally trivial; what matters is the **shape**:

1. Class implements the :class:`dikw_core.client.converters.Converter`
   Protocol — exposes ``name``, ``extensions``, and ``convert()``.
2. ``convert()`` writes ``<stem>.md`` plus an ``assets/`` subdirectory.
3. Every asset (including the original-as-provenance) is image-referenced
   from the md so ``md_inspect`` picks it up.
4. The entry-points group ``dikw.client.converters`` is declared in
   ``pyproject.toml``; dikw client discovers this plugin automatically
   once the package is installed.
"""

from __future__ import annotations

from pathlib import Path


class ExampleConverter:
    """Wrap a ``.example`` file's text as a single-block markdown."""

    name: str = "example"
    extensions: tuple[str, ...] = (".example",)

    def convert(self, input_path: Path, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(exist_ok=True)

        # Copy the original verbatim into assets/ as the provenance
        # record. ``.example`` is not in dikw-core's
        # _DEFAULT_ASSET_EXTENSIONS but that doesn't matter — md_inspect
        # follows image refs regardless of extension.
        original_dest = assets_dir / input_path.name
        original_dest.write_bytes(input_path.read_bytes())

        # Convert: wrap text in a single fenced code block. Real plugins
        # would invoke marker / MinerU / docling / etc. here.
        try:
            text = input_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = "(binary content — see linked original)"

        body = (
            f"# {input_path.stem}\n\n"
            "```\n"
            f"{text}\n"
            "```\n\n"
            f"![original](assets/{input_path.name})\n"
        )
        (output_dir / f"{input_path.stem}.md").write_text(body, encoding="utf-8")


__all__ = ["ExampleConverter"]
