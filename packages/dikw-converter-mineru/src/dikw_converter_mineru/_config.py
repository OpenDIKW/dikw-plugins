"""API key resolution + token redaction utilities.

The plugin resolves the MinerU API token from three sources in order
of decreasing precedence — see :func:`resolve_api_key`. ``redact`` is
applied wherever the token would otherwise risk appearing in user-
visible output (exception messages, log lines).
"""

from __future__ import annotations

import os

from ._errors import MineruAuthError

# Order: explicit > user-friendly env (matches MinerU's dashboard
# label) > dikw-convention env. Document this in __init__.py and
# README; tests assert each path.
_ENV_PRIMARY = "MinerUAPIKey"
_ENV_FALLBACK = "DIKW_MINERU_API_KEY"


def resolve_api_key(explicit: str | None) -> str:
    """Return the first non-empty API key from the three sources, or
    raise :class:`MineruAuthError` with both env var names mentioned.

    A token equal to ``""`` or only whitespace is treated as missing —
    a half-loaded ``.env`` would otherwise produce a confusing
    HTTP 401 from MinerU rather than a clear "key not set" message
    at the plugin boundary.
    """
    for candidate in (
        explicit,
        os.environ.get(_ENV_PRIMARY),
        os.environ.get(_ENV_FALLBACK),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    raise MineruAuthError(
        f"MinerU API key not set. Pass api_key= to MineruConverter(), "
        f"or set the {_ENV_PRIMARY} environment variable "
        f"(or {_ENV_FALLBACK} as a fallback)."
    )


def redact(token: str) -> str:
    """Render a token as ``…<last-8-chars>`` for user-facing messages.

    Never returns more than 8 characters of token material. ``len(token)
    <= 8`` falls back to a fixed-width sentinel so very short test
    tokens still don't leak in full.
    """
    if not token or len(token) <= 8:
        return "<redacted>"
    return f"…{token[-8:]}"
