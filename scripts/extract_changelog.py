"""Extract a single version's section from a Keep a Changelog file.

Usage::

    python scripts/extract_changelog.py <changelog.md> <version>

Prints the body of the ``## [<version>]`` section (everything until the
next ``## [`` heading or end-of-file) to stdout. Exits non-zero if the
version is missing — used by ``release.yml`` to fail a release that
forgot a changelog entry, and by ``scripts/check-package.py`` for the
same local check.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_HEADING_RE = re.compile(r"^##\s+\[(?P<version>[^\]]+)\]")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def extract(changelog: str, version: str) -> str:
    """Return the body lines for ``## [<version>]`` (no heading, trimmed).

    Matches the exact bracket-enclosed version so ``0.1.0`` does not also
    accept ``## [0.1.0-rc1]``. Fenced code blocks are skipped so a doc-
    example heading inside a code fence does not confuse the parser.

    Raises ``ValueError`` if the section is missing or empty.
    """
    lines = changelog.splitlines()
    in_fence = False
    start: int | None = None
    end: int | None = None
    for i, raw in enumerate(lines):
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(raw)
        if m is None:
            continue
        if start is None:
            if m.group("version") == version:
                start = i + 1
            continue
        end = i
        break
    if start is None:
        raise ValueError(f"changelog has no '## [{version}]' section")
    body = lines[start : end if end is not None else len(lines)]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if not body:
        raise ValueError(f"changelog section '## [{version}]' is empty")
    return "\n".join(body) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            f"usage: {argv[0]} <changelog.md> <version>",
            file=sys.stderr,
        )
        return 2
    changelog_path = Path(argv[1])
    version = argv[2]
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {changelog_path}: {exc}", file=sys.stderr)
        return 1
    try:
        body = extract(text, version)
    except ValueError as exc:
        print(f"{changelog_path}: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
