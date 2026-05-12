"""MinerU online-API converter plugin for dikw-core.

Exposes :class:`MineruConverter`, which implements
:class:`dikw_core.client.converters.Converter`. Heavier submodules
(`_client`, `_convert`) are imported lazily inside :meth:`convert` so
dikw-core's plugin discovery pass — which instantiates every registered
converter at startup — pays nothing for users who don't import a
MinerU-claimed format.
"""

from __future__ import annotations

from pathlib import Path

from ._errors import (
    MineruApiError,
    MineruAuthError,
    MineruInputError,
    MineruQuotaError,
    MineruTimeoutError,
)


class MineruConverter:
    """Convert a document via the MinerU online API into markdown + assets.

    Output shape under ``output_dir``::

        <stem>.md            # MinerU's full.md, renamed; image refs rewritten
        assets/
            <stem>.<ext>     # original input, verbatim provenance
            …                # images extracted by MinerU

    API key resolution order (first non-empty wins):

    1. ``api_key`` constructor argument
    2. ``MinerUAPIKey`` env var (matches the MinerU dashboard label)
    3. ``DIKW_MINERU_API_KEY`` env var (dikw-prefixed fallback)
    """

    name: str = "mineru"
    # PDF + Office formats. Image inputs and HTML are deferred to v0.2 —
    # see README "Supported formats" for the rationale.
    extensions: tuple[str, ...] = (
        ".pdf",
        ".docx", ".doc",
        ".pptx", ".ppt",
        ".xlsx", ".xls",
    )

    def __init__(self, api_key: str | None = None) -> None:
        # Resolution is deferred to convert() so a missing key doesn't
        # raise during dikw-core's plugin discovery pass — discovery
        # instantiates every Converter, even ones the user never invokes.
        self._api_key = api_key

    def convert(self, input_path: Path, output_dir: Path) -> None:
        from ._convert import run_convert

        run_convert(input_path, output_dir, explicit_api_key=self._api_key)


__all__ = [
    "MineruApiError",
    "MineruAuthError",
    "MineruConverter",
    "MineruInputError",
    "MineruQuotaError",
    "MineruTimeoutError",
]
