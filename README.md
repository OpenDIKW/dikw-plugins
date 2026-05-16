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
| `dikw-converter-epub`       | `epub`      | `.epub`      | 0.1.0 — pure-Python EPUB → markdown |
| `dikw-converter-mineru`     | `mineru`    | `.pdf`, `.docx`, `.doc`, `.pptx`, `.ppt`, `.xlsx`, `.xls` | 0.1.0 — MinerU online API (PDF + Office); needs `MinerUAPIKey` env |

`dikw-converter-mineru` covers multiple input formats because they all
share one upstream tool. CLAUDE.md's "one format per package" guideline
calls this out as the explicit exception when formats share an engine.

## Quick install

```bash
# Install dikw-core first (the host).
pip install dikw-core

# Then install any plugin you want.
pip install dikw-converter-mineru    # PDF + DOCX + PPTX + XLSX via MinerU online
pip install dikw-converter-epub      # pure-Python EPUB

# MinerU is hosted — export your API token first.
# See packages/dikw-converter-mineru/README.md § Auth for the 3-tier
# resolution order, token rotation, and how to load .env per shell.
$env:MinerUAPIKey = "eyJ..."         # PowerShell
# export MinerUAPIKey="eyJ..."       # bash / zsh

# Use it.
dikw client import paper.pdf
dikw client import book.epub
```

`uv pip install` works identically; substitute `uv pip` for `pip` if
you manage envs with `uv`.

## Manage installed plugins

Each `dikw-converter-*` is a normal PyPI package — upgrading, pinning,
and uninstalling follow the standard `pip` / `uv` semantics with no
special handling on dikw-core's side.

```bash
# Upgrade to the latest published version.
pip install --upgrade dikw-converter-epub

# Pin to a specific version (recommended for production environments —
# converter output is deterministic per pinned plugin version).
pip install 'dikw-converter-mineru==0.1.0'

# Inspect what's installed and which entry-points it registers.
pip show dikw-converter-epub
python -c "from importlib.metadata import entry_points; print([(e.name, e.value) for e in entry_points(group='dikw.client.converters')])"

# Uninstall — the entry-point disappears the next time dikw client
# does converter discovery, so there's nothing else to clean up
# inside the host. Files already imported into <base>/sources/ stay
# put (they're not the plugin's data).
pip uninstall dikw-converter-epub
```

### Offline / restricted networks

CI builds attach every release's wheel and sdist to its GitHub Release.
For air-gapped environments, download the wheel from the release page
and install from the local file:

```bash
# From https://github.com/opendikw/dikw-plugins/releases
pip install ./dikw_converter_epub-0.1.0-py3-none-any.whl
```

The wheel still declares its `dikw-core` dependency; if PyPI isn't
reachable, install `dikw-core` from its own GitHub Release first.

### Where releases live

- **PyPI**: `https://pypi.org/project/dikw-converter-<format>/`
  (e.g. [dikw-converter-epub][pypi-epub]).
- **GitHub Releases**: `https://github.com/opendikw/dikw-plugins/releases`
  — tagged `dikw-converter-<format>-vX.Y.Z`, with release notes lifted
  verbatim from that package's `CHANGELOG.md`.
- **Per-package changelog**: each `packages/dikw-converter-<format>/CHANGELOG.md`
  is the authoritative history for that plugin.

[pypi-epub]: https://pypi.org/project/dikw-converter-epub/

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
│   ├── release-process.md    tag → CI → PyPI + GitHub Release pipeline
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
