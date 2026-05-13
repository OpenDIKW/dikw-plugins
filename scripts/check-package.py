"""Local pre-release check for a single workspace plugin package.

Orchestrates the same gates ``release.yml`` runs in CI:

1. Print the CHANGELOG entry for the version in ``pyproject.toml`` —
   the same block that will become the GitHub Release body. Fails if
   the entry is missing.
2. Run the artifact-level packaging tests scoped to this package.
   The pytest fixtures themselves drive ``uv build`` and ``twine
   check --strict``, so green here means the wheel/sdist would clear
   CI's release gate.

Usage::

    uv run python scripts/check-package.py dikw-converter-epub

For a hands-on look at the artifacts (filenames, sizes, etc.), the
underlying command is ``uv build --package <name>``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPTS_DIR.parent
PACKAGES_DIR = WORKSPACE_ROOT / "packages"
PACKAGE_NAME_RE = re.compile(r"^dikw-converter-[a-z0-9-]+$")


def _read_version(pyproject: Path) -> str:
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return str(data["project"]["version"])


def _run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=WORKSPACE_ROOT, check=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "package",
        help="package directory name under packages/, e.g. dikw-converter-epub",
    )
    args = parser.parse_args(argv[1:])

    if not PACKAGE_NAME_RE.match(args.package):
        print(
            f"error: invalid package name {args.package!r} "
            f"(must match {PACKAGE_NAME_RE.pattern})",
            file=sys.stderr,
        )
        return 1
    pkg_dir = (PACKAGES_DIR / args.package).resolve()
    if pkg_dir.parent != PACKAGES_DIR.resolve():
        print(
            f"error: {args.package!r} resolves outside packages/ — refusing",
            file=sys.stderr,
        )
        return 1
    pyproject = pkg_dir / "pyproject.toml"
    if not pyproject.is_file():
        print(f"error: {pyproject} not found", file=sys.stderr)
        return 1

    version = _read_version(pyproject)
    print(f"\n=== {args.package} v{version} ===\n")

    changelog = pkg_dir / "CHANGELOG.md"
    extract_script = SCRIPTS_DIR / "extract_changelog.py"
    print(f"--- CHANGELOG entry for {version} ---")
    _run([sys.executable, str(extract_script), str(changelog), version])

    print(f"\n--- artifact tests for {args.package} ---")
    # Pytest ``-k`` evaluates its arg as a Python expression, so the
    # hyphenated dist name parses as ``a - b - c``. Use the underscore
    # form to match the test ids set in tests/packaging/conftest.py.
    _run(
        [
            "uv",
            "run",
            "pytest",
            "tests/packaging",
            "-k",
            args.package.replace("-", "_"),
            "-v",
        ]
    )

    print(f"\n[ok] {args.package} v{version} ready to tag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
