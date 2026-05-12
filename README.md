# dikw-plugins

Sibling-repo home for converter plugins that extend [`dikw-core`][core]'s
`dikw client import` command to non-markdown formats — PDF, EPUB, and
whatever comes next.

dikw-core ingests `.md` + assets only. Plugins in this repo turn other
formats (`paper.pdf`, `book.epub`, …) into that md+assets shape so they
can flow through the standard import pipeline. Conversion happens in the
client process; the server never loads converter dependencies.

[core]: https://github.com/opendikw/dikw-core

## Installed plugins

This repo is structured as a **uv workspace** with one package per
plugin under `packages/`. Each is independently published to pypi and
versioned.

| Package                     | Engine name | Extensions   | Status        |
| --------------------------- | ----------- | ------------ | ------------- |
| `dikw-converter-example`    | `example`   | `.example`   | reference stub — copy as template |
| _(future)_ `dikw-converter-pdf` | TBD     | `.pdf`       | not yet built |
| _(future)_ `dikw-converter-epub` | TBD    | `.epub`      | not yet built |

## Quick install

```bash
# Install dikw-core first (the host).
pip install dikw-core

# Then install any plugin you want.
pip install dikw-converter-pdf       # whenever it ships

# Use it.
dikw client import paper.pdf
```

## I want to add a new plugin

Start with [`docs/plugin-author-guide.md`](docs/plugin-author-guide.md)
— end-to-end walkthrough from `cargo-cult` the `dikw-converter-example`
package to publishing on pypi.

The formal contract spec lives in
[dikw-core's `docs/converters.md`][spec]; the rationale for why this
all lives in a sibling repo (rather than inside dikw-core) is in
[dikw-core's ADR 0001][adr].

[spec]: https://github.com/opendikw/dikw-core/blob/main/docs/converters.md
[adr]: https://github.com/opendikw/dikw-core/blob/main/docs/adr/0001-client-side-converter-plugins.md

## Repository layout

```
dikw-plugins/
├── README.md                 (you are here)
├── CLAUDE.md                 agent guidance — start with docs/architecture.md
├── CONTEXT.md                local terms (defers to dikw-core's glossary)
├── docs/
│   ├── architecture.md       why client-side plugin, what the contract is
│   ├── plugin-author-guide.md  tutorial: write a new converter
│   └── tool-survey.md        PDF/EPUB tool ecosystem snapshot (2026-05)
├── pyproject.toml            uv workspace root
└── packages/
    └── dikw-converter-example/    reference stub
        ├── pyproject.toml
        ├── README.md
        ├── src/dikw_converter_example/__init__.py
        └── tests/test_example.py
```

## Dev workflow

```bash
# From repo root:
uv sync                                # install workspace deps
uv run pytest                          # run all packages' tests
uv run ruff check .
uv run mypy packages/*/src

# Install a plugin into your dikw client env:
pip install -e packages/dikw-converter-example
```

## Status

Pre-alpha along with dikw-core. The plugin contract (Protocol shape,
entry-points group name, selection order) is stable enough to build on
but may evolve through the deprecation cycle described in
[`dikw-core/docs/converters.md`](https://github.com/opendikw/dikw-core/blob/main/docs/converters.md#versioning).
