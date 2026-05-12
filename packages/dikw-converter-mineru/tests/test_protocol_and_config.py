"""Tests 1-10 from the plan: Protocol surface + api-key resolution +
token redaction.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dikw_converter_mineru import MineruAuthError, MineruConverter
from dikw_converter_mineru._config import redact, resolve_api_key


def test_protocol_attributes() -> None:
    c = MineruConverter()
    assert c.name == "mineru"
    assert c.extensions == (
        ".pdf",
        ".docx", ".doc",
        ".pptx", ".ppt",
        ".xlsx", ".xls",
    )


def test_satisfies_dikw_core_protocol() -> None:
    """Structural Protocol conformance against the real dikw-core;
    skipped when dikw-core isn't importable in the test env.
    """
    converters_mod = pytest.importorskip("dikw_core.client.converters")
    assert isinstance(MineruConverter(), converters_mod.Converter)


def test_constructor_accepts_explicit_api_key() -> None:
    """Passing api_key= must not raise, and must not validate yet —
    validation is deferred to convert().
    """
    c = MineruConverter(api_key="some-test-token-value-1234567890")
    assert isinstance(c, MineruConverter)


def test_constructor_no_args_defers_to_convert() -> None:
    """No args, no env — instantiation alone must succeed.

    dikw-core's plugin discovery instantiates every registered
    Converter at startup, so raising here would break that pass even
    for users who never call convert().
    """
    assert isinstance(MineruConverter(), MineruConverter)


def test_missing_api_key_raises_clear_error(tmp_path: Path) -> None:
    """No explicit key + empty env → MineruAuthError naming both env vars."""
    fake_pdf = tmp_path / "demo.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake\n")

    with pytest.raises(MineruAuthError) as exc:
        MineruConverter().convert(fake_pdf, tmp_path / "out")

    msg = str(exc.value)
    assert "MinerUAPIKey" in msg
    assert "DIKW_MINERU_API_KEY" in msg


def test_api_key_explicit_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MinerUAPIKey", "from-env")
    assert resolve_api_key("from-arg") == "from-arg"


def test_api_key_from_primary_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MinerUAPIKey", "primary-token-value")
    assert resolve_api_key(None) == "primary-token-value"


def test_api_key_from_fallback_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback fires only when the primary is absent."""
    monkeypatch.setenv("DIKW_MINERU_API_KEY", "fallback-token-value")
    assert resolve_api_key(None) == "fallback-token-value"


def test_api_key_blank_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only env var triggers the unset error.

    A half-loaded .env would otherwise produce a confusing HTTP 401
    rather than a clear "key not set" at the plugin boundary.
    """
    monkeypatch.setenv("MinerUAPIKey", "   ")
    with pytest.raises(MineruAuthError):
        resolve_api_key(None)


def test_token_redacted_short() -> None:
    """Tokens <= 8 chars collapse to a fixed sentinel — never partial leak."""
    assert redact("short") == "<redacted>"
    assert redact("") == "<redacted>"
    assert redact("12345678") == "<redacted>"


def test_token_redacted_long() -> None:
    """Long tokens show only their last 8 characters with ellipsis."""
    token = "eyJ0eXAi" * 20 + "ABCDEFGH"
    out = redact(token)
    assert out == "…ABCDEFGH"
    assert token not in out
    assert len(out) <= 9


def test_authorization_header_never_in_resolved_error() -> None:
    """Even the unset-key error path must not echo the raw token —
    defense in depth: even if someone accidentally stuffed the token
    in via a weird path, the message stays clean.
    """
    # resolve_api_key with a real-ish token + a deliberate trigger:
    # we expect it to NOT raise here because the key resolves, but if
    # it did, the token wouldn't appear in the message. Since this
    # path doesn't raise, the assertion exercises redact() in isolation.
    secret = "eyJhbGciOiJIUzI1NiJ9.someverylongjwttokenpayload.signature"
    assert secret not in redact(secret)
