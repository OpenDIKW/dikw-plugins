"""Exception hierarchy for the MinerU plugin.

These are surfaced to dikw-core's importer (which wraps any
``Converter.convert`` exception in a ``SourceImportError``) and to
programmatic callers who want fine-grained handling.

The base ``MineruApiError`` exists so callers can ``except`` a single
type when they don't care about the specific failure category.
"""

from __future__ import annotations


class MineruApiError(RuntimeError):
    """Base class for any failure during a MinerU API interaction."""


class MineruAuthError(MineruApiError):
    """Raised when the API token is missing, invalid, or expired.

    The plugin guarantees the token itself never appears in this
    exception's ``str()`` — only a short suffix for user identification
    (see :mod:`._config`). Callers can safely log or re-raise.
    """


class MineruInputError(MineruApiError):
    """Raised when the input fails an API-level pre-check.

    Covers oversized files (>200MB), too many pages (>200), unsupported
    file format the API rejected, and similar caller-fixable problems.
    """


class MineruQuotaError(MineruApiError):
    """Raised when the account's daily task quota has been exhausted.

    MinerU returns ``-60018``; the plugin propagates this distinctly so
    callers can retry the next UTC day, queue, or fall back to a local
    engine if one is installed.
    """


class MineruTimeoutError(MineruApiError):
    """Raised when polling for task completion exceeded the configured
    deadline. Distinct from network errors — the API stayed reachable
    but never reported ``state: done``.
    """
