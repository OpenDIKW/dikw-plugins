# CLAUDE.md

Guidance for Claude Code (and other coding agents) working in the
`dikw-plugins` repo.

## What this repo is

A **uv-workspace monorepo** of converter plugins that extend
[`dikw-core`][core]'s `dikw client import` to non-markdown formats.
Each `packages/dikw-converter-<format>/` is its own pypi package with
its own version + dependencies + tests. The repo as a whole has
shared tooling (ruff, mypy, pytest, CI) but per-package release
cadence.

[core]: https://github.com/opendikw/dikw-core

## Before doing anything: read these in order

1. [`docs/architecture.md`](docs/architecture.md) — **start here**. The
   full architecture story: why this lives in a sibling repo, the
   plugin contract, output layout, idempotency rules. Self-contained;
   no assumed prior context.
2. [`dikw-core/docs/converters.md`][spec] — the formal contract spec
   (Protocol shape, entry-points, selection order). Source of truth
   for what every plugin must implement.
3. [`dikw-core/docs/adr/0001-client-side-converter-plugins.md`][adr] —
   why we picked this design over the alternatives (engine-side
   SourceBackend, server-side plugin, pure upstream tool).
4. [`docs/plugin-author-guide.md`](docs/plugin-author-guide.md) —
   tutorial when you're actually writing a new plugin.

[spec]: https://github.com/opendikw/dikw-core/blob/main/docs/converters.md
[adr]: https://github.com/opendikw/dikw-core/blob/main/docs/adr/0001-client-side-converter-plugins.md

## Layering invariants (inherited from dikw-core)

- **No server dependencies.** Plugins run in the `dikw client` process.
  Never import anything from `dikw_core.server.*`, `dikw_core.api`,
  `dikw_core.storage`, or `dikw_core.providers`. The Converter Protocol
  + Path is all you need from dikw-core.
- **No engine-side imports.** `dikw_core.domains.*` is engine territory
  — plugins don't touch it. The split is structural, not stylistic;
  importing engine modules means your plugin can't be loaded by a
  thin client.
- **Plugin deps are your problem.** Marker, MinerU, docling — whatever
  your plugin pulls in stays scoped to your package's `pyproject.toml`.
  Don't add deps to a sibling package "for convenience".

## Dev workflow

```bash
uv sync                                # workspace deps
uv run pytest                          # all package tests
uv run pytest packages/dikw-converter-pdf/tests  # one package
uv run ruff check .
uv run mypy packages/*/src

# Test a plugin against a locally checked-out dikw-core:
pip install -e ../dikw-core
pip install -e packages/dikw-converter-pdf
dikw client import sample.pdf
```

## Conventions

- **Package names**: `dikw-converter-<format>` (one format per package
  is the norm; multi-format packages allowed when formats share an
  upstream tool, e.g. a hypothetical `dikw-converter-pandoc`).
- **Module names**: `dikw_converter_<format>` (Python identifier form).
- **Engine names**: the `Converter.name` attribute — short, unique
  across plugins the user has installed (e.g. `marker`, `mineru`,
  `docling`).
- **Output layout**: `<output_dir>/<stem>.md` + `<output_dir>/assets/*`,
  with every asset image-referenced from the md (see
  `docs/architecture.md` § "Asset reference rule").
- **Versioning**: each package is independently SemVer'd. Bump the
  package's own `version` field AND add a matching `## [X.Y.Z]` block
  at the top of that package's `CHANGELOG.md` in the same commit —
  the release pipeline rejects a tag whose version is not present in
  the changelog.
- **Releasing**: tagging `dikw-converter-<format>-vX.Y.Z` triggers
  `.github/workflows/release.yml` (PyPI via OIDC + GitHub Release).
  Before tagging, run `uv run python scripts/check-package.py
  dikw-converter-<format>` locally — it executes the same artifact
  gate (`tests/packaging/`) that CI runs, so red on your machine =
  red on the runner. Full procedure in `docs/release-process.md`.

## Tooling

- Python 3.12+, same as dikw-core.
- `uv` for workspace + venv + dep resolution.
- `ruff` rules + line length match dikw-core's `pyproject.toml` so
  there's a single style across both repos.
- `mypy strict = true`. Plugins should be fully typed.
- `pytest` with the workspace's shared config.

## Things not to do

- Don't put the original PDF / EPUB into `output_dir/` top-level.
  Put it under `output_dir/assets/` and image-ref it from the md.
- Don't hard-code paths inside `<base>/sources/` — plugins write to
  the temp `output_dir` they were given; dikw-core handles staging
  into the base.
- Don't reach into dikw-core internals to "speed things up" — the
  Protocol contract is what we promise to keep stable. Internals
  may move.
- Don't write a single mega-plugin handling many formats unless they
  legitimately share an upstream tool. One pypi package per logical
  unit makes user dep-pinning easier.
