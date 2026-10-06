# CLAUDE.md

Guidance for Claude Code (and other coding agents) in the `dikw-plugins` repository.

## What this repo is

A **uv-workspace monorepo** of converter plugins that extend [`dikw-core`][core]'s `dikw client import` to non-Markdown formats.

- Each `packages/dikw-converter-<format>/` is its own PyPI package, with its own version, dependencies, and tests.
- The repo shares tooling (ruff, mypy, pytest, CI). Each package has its own release cadence.

[core]: https://github.com/opendikw/dikw-core

## Read before you change things

Read the document that matches your change:

| change | read first |
|---|---|
| anything in the plugin contract, output layout, or idempotency | [`docs/architecture.md`](docs/architecture.md) — the full architecture story; self-contained |
| what every plugin must implement (Protocol shape, entry points, selection order) | [`dikw-core/docs/converters.md`][spec] — the formal contract and source of truth |
| why plugins live client-side in a sibling repo | [`dikw-core/docs/adr/0001-client-side-converter-plugins.md`][adr] |
| a new plugin | [`docs/plugin-author-guide.md`](docs/plugin-author-guide.md) — the tutorial |
| a release | [`docs/release-process.md`](docs/release-process.md) |

[spec]: https://github.com/opendikw/dikw-core/blob/main/docs/converters.md
[adr]: https://github.com/opendikw/dikw-core/blob/main/docs/adr/0001-client-side-converter-plugins.md

## Layering invariants (inherited from dikw-core)

- **No server dependencies.** Plugins run in the `dikw client` process. Never import from `dikw_core.server.*`, `dikw_core.api`, `dikw_core.storage`, or `dikw_core.providers`. The Converter Protocol and `Path` are all you need from dikw-core.
- **No engine imports.** `dikw_core.domains.*` belongs to the engine. Plugins do not touch it. If a plugin imports engine modules, a thin client cannot load it.
- **Plugin dependencies stay in the plugin.** Marker, MinerU, docling, or any other dependency goes only in that package's `pyproject.toml`. Do not add a dependency to a sibling package "for convenience".

## Dev workflow

```bash
uv sync                                # workspace deps
uv run pytest                          # all package tests
uv run pytest packages/dikw-converter-mineru/tests  # one package
uv run ruff check .
uv run mypy packages/*/src

# Test a plugin against a locally checked-out dikw-core:
pip install -e ../dikw-core
pip install -e packages/dikw-converter-mineru
dikw client import sample.pdf
```

## Conventions

- **Package names:** `dikw-converter-<format>`. One format per package is the norm. A multi-format package is allowed only when the formats share an upstream tool (for example a hypothetical `dikw-converter-pandoc`).
- **Module names:** `dikw_converter_<format>` (the Python identifier form).
- **Engine names:** the `Converter.name` attribute. Keep it short and unique across the plugins a user installs (for example `marker`, `mineru`, `docling`).
- **Output layout:** `<output_dir>/<stem>.md` + `<output_dir>/assets/*`. The Markdown must image-reference every asset (see `docs/architecture.md` § "Asset reference rule").
- **Versioning:** each package has its own SemVer. In the same commit, bump the package's `version` field AND add a matching `## [X.Y.Z]` block at the top of that package's `CHANGELOG.md`. The release pipeline rejects a tag whose version is not in the changelog.
- **Releasing:** a tag `dikw-converter-<format>-vX.Y.Z` triggers `.github/workflows/release.yml` (PyPI through OIDC + GitHub Release).
  Before you tag, run `uv run python scripts/check-package.py dikw-converter-<format>`. It runs the same artifact gate (`tests/packaging/`) as CI, so red on your machine means red on the runner.
  Full procedure: `docs/release-process.md`.

## Tooling

- Python 3.12+, the same as dikw-core.
- `uv` for the workspace, the venv, and dependency resolution.
- `ruff` rules and line length match dikw-core's `pyproject.toml`, so both repos share one style.
- `mypy strict = true`. Plugins are fully typed.
- `pytest` with the workspace's shared config.

## Things not to do

- Don't put the original PDF / EPUB into `output_dir/` top-level. Put it under `output_dir/assets/` and image-ref it from the md.
- Don't hard-code paths inside `<base>/sources/` — plugins write to the temp `output_dir` they were given; dikw-core handles staging into the base.
- Don't reach into dikw-core internals to "speed things up" — the Protocol contract is what we promise to keep stable. Internals may move.
- Don't write a single mega-plugin handling many formats unless they legitimately share an upstream tool. One pypi package per logical unit makes user dep-pinning easier.

## Working rules

- State your assumptions. If a decision blocks you, ask one question with the AskUserQuestion tool, and put your recommended answer first.
- Write the minimum code that solves the request. Change only what the request needs, and match the existing style.
- Test first: a bug fix starts with a failing test that reproduces it.

## Autonomy

- When a step does not need my input, continue. Put status notes in the same message as your next action.
- Stop and ask only when you cannot continue without my decision, or before a destructive action: delete data or files you did not create, push a release tag, force-push, or change anything outside this repository.
- Do not end a turn with a summary that announces the next step but does not take it, or with an offer to continue "unless you prefer otherwise".

## Finish line

A change is done when `uv run pytest`, `uv run ruff check .`, and `uv run mypy packages/*/src` are green, the PR is merged, and local `main` is synced.
A release is done when `scripts/check-package.py` passes, the tag is pushed with my approval, and the release workflow is green.

## Report

End every run with three headings: **需要你决定** (decisions you wait for; "无" if none), **改动** (what changed, with PR links), **发现** (what you found; mark each claim you could not confirm, and say where you looked).
