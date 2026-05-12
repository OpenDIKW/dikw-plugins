"""Pure-Python EPUB → markdown converter plugin.

Exposes :class:`EpubConverter`, which implements
:class:`dikw_core.client.converters.Converter`. Heavier submodules are
imported lazily inside :meth:`EpubConverter.convert` so dikw-core's
plugin discovery pass — which instantiates every registered converter
at startup — pays nothing for users who don't import EPUBs.
"""

from __future__ import annotations

from pathlib import Path

from ._opf import EpubParseError


class EpubConverter:
    """Convert an EPUB file to a single markdown + provenance assets.

    Output shape under ``output_dir``::

        <stem>.md            # rendered book content
        assets/
            <stem>.epub      # original input, verbatim provenance
            <opf-href>/...   # extracted images, keyed by OPF manifest href
    """

    name: str = "epub"
    extensions: tuple[str, ...] = (".epub",)

    def convert(self, input_path: Path, output_dir: Path) -> None:
        from ._convert import run_convert

        run_convert(input_path, output_dir)


__all__ = ["EpubConverter", "EpubParseError"]
