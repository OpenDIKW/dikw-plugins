"""Unit tests for ``scripts/extract_changelog.py``.

Lives next to the artifact-level packaging tests because both gate the
release pipeline. Pure string-in / string-out, no fixtures needed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from conftest import WORKSPACE_ROOT


def _load_module() -> ModuleType:
    path = WORKSPACE_ROOT / "scripts" / "extract_changelog.py"
    spec = importlib.util.spec_from_file_location("extract_changelog", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("extract_changelog", module)
    spec.loader.exec_module(module)
    return module


CHANGELOG_SAMPLE = """# Changelog

## [Unreleased]

## [0.2.0] - 2026-06-01

### Added

- Foo.
- Bar.

### Fixed

- Quux.

## [0.1.0] - 2026-05-13

### Added

- Initial release.

[Unreleased]: https://...
[0.2.0]: https://...
[0.1.0]: https://...
"""


def test_extract_returns_body_between_headings() -> None:
    module = _load_module()
    body = module.extract(CHANGELOG_SAMPLE, "0.2.0")
    assert "### Added" in body
    assert "- Foo." in body
    assert "- Quux." in body
    assert "## [0.1.0]" not in body
    assert "## [0.2.0]" not in body


def test_extract_reaches_end_of_file_for_latest_version() -> None:
    module = _load_module()
    # 0.1.0 is the last `## [` block before the link references; the
    # extractor should stop at those reference-style links (they are
    # not `## [` headings) so the link footer ends up included.
    body = module.extract(CHANGELOG_SAMPLE, "0.1.0")
    assert "Initial release." in body


def test_extract_missing_version_raises() -> None:
    module = _load_module()
    with pytest.raises(ValueError, match=r"0\.99\.0"):
        module.extract(CHANGELOG_SAMPLE, "0.99.0")


def test_main_writes_body_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    file = tmp_path / "CHANGELOG.md"
    file.write_text(CHANGELOG_SAMPLE, encoding="utf-8")
    rc = module.main(["extract_changelog", str(file), "0.2.0"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "- Foo." in captured.out


def test_main_missing_version_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    file = tmp_path / "CHANGELOG.md"
    file.write_text(CHANGELOG_SAMPLE, encoding="utf-8")
    rc = module.main(["extract_changelog", str(file), "0.99.0"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "0.99.0" in captured.err


def test_main_argv_misuse() -> None:
    module = _load_module()
    rc = module.main(["extract_changelog"])
    assert rc == 2


def test_extract_does_not_partial_match_version_prefix() -> None:
    """`0.1.0` must not be satisfied by ``## [0.1.0-rc1]``."""
    module = _load_module()
    sample = (
        "# Changelog\n\n"
        "## [0.1.0-rc1] - 2026-05-01\n\n"
        "- Pre-release.\n"
    )
    with pytest.raises(ValueError, match=r"0\.1\.0"):
        module.extract(sample, "0.1.0")


def test_extract_skips_fenced_code_block_headings() -> None:
    """A `## [` heading inside a fenced code block must not start or stop a section.

    The fenced content stays part of whichever section it appears in (here, 0.2.0),
    but the parser must keep walking past the fenced `## [9.9.9]` to find the real
    next heading — otherwise the section would be truncated at the doc-example.
    """
    module = _load_module()
    sample = (
        "# Changelog\n\n"
        "## [0.2.0] - 2026-06-01\n\n"
        "Example heading inside a doc snippet:\n\n"
        "```\n"
        "## [9.9.9]\n"
        "(should be ignored as a section delimiter)\n"
        "```\n\n"
        "- Real body line after the fence.\n\n"
        "## [0.1.0] - 2026-05-13\n\n"
        "- Earlier.\n"
    )
    body = module.extract(sample, "0.2.0")
    # The body keeps walking past the fenced heading and includes the
    # line that comes after the fence — proves the fenced `## [9.9.9]`
    # didn't terminate the section.
    assert "Real body line after the fence." in body
    assert "## [0.1.0]" not in body  # real next heading is the terminator
    # And the fenced heading didn't register as its own section either.
    with pytest.raises(ValueError):
        module.extract(sample, "9.9.9")


def test_extract_empty_section_raises() -> None:
    """A heading immediately followed by another heading has no body — fail."""
    module = _load_module()
    sample = (
        "# Changelog\n\n"
        "## [0.3.0] - 2026-07-01\n\n"
        "## [0.2.0] - 2026-06-01\n\n"
        "- Earlier release.\n"
    )
    with pytest.raises(ValueError, match=r"empty"):
        module.extract(sample, "0.3.0")
