"""Tests for the reference stub Converter.

These tests exist primarily so the template stays green over time —
when the Converter Protocol or the output convention shift, this
file fails first and signals the rename-and-copy template needs
updating.
"""

from __future__ import annotations

from pathlib import Path

from dikw_converter_example import ExampleConverter


def test_protocol_attributes() -> None:
    c = ExampleConverter()
    assert c.name == "example"
    assert c.extensions == (".example",)


def test_convert_writes_md_and_provenance_asset(tmp_path: Path) -> None:
    input_path = tmp_path / "notes.example"
    input_path.write_text("Hello\nworld\n", encoding="utf-8")
    out = tmp_path / "out"

    ExampleConverter().convert(input_path, out)

    md = (out / "notes.md").read_text(encoding="utf-8")
    assert "# notes" in md
    assert "Hello\nworld" in md
    # Image-ref to the original ensures md_inspect picks up the asset.
    assert "![original](assets/notes.example)" in md

    assert (out / "assets" / "notes.example").read_text(
        encoding="utf-8"
    ) == "Hello\nworld\n"


def test_binary_input_falls_back_to_placeholder(tmp_path: Path) -> None:
    input_path = tmp_path / "binary.example"
    input_path.write_bytes(b"\xff\xfe\x00binary bytes")
    out = tmp_path / "out"

    ExampleConverter().convert(input_path, out)

    md = (out / "binary.md").read_text(encoding="utf-8")
    assert "binary content" in md
    # Original bytes are preserved.
    assert (out / "assets" / "binary.example").read_bytes() == b"\xff\xfe\x00binary bytes"


def test_satisfies_dikw_core_protocol() -> None:
    """If dikw-core is importable in this environment, structurally
    verify our converter matches its Protocol."""
    pytest_importorskip = __import__("pytest").importorskip
    converters_mod = pytest_importorskip("dikw_core.client.converters")
    assert isinstance(ExampleConverter(), converters_mod.Converter)
