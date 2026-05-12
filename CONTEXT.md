# dikw-plugins

Local glossary. Most terms are inherited from
[`dikw-core/CONTEXT.md`](https://github.com/opendikw/dikw-core/blob/main/CONTEXT.md);
this file only defines things specific to this repo.

## Inherited (don't redefine here)

- **base** — the user's dikw instance root (owned by the server).
- **source** — a `.md` file under `<base>/sources/`. The output of a
  converter, after dikw-core imports it.
- **import** — verb: take files outside the base and commit them into
  `<base>/sources/`. Now accepts non-md inputs via converter plugins.
- **converter plugin** / **converter engine name** — defined in
  dikw-core's `## Plugin contract` section. Recap: a pypi package that
  turns one non-md file into md+assets; the engine name (`marker`,
  `mineru`) disambiguates when multiple plugins claim the same extension.

## Local terms

**workspace**:
The uv workspace rooted at this repo's top-level `pyproject.toml`.
Members are the per-plugin packages under `packages/*`. Workspaces let
all plugins share dev tooling (ruff, mypy, pytest) and run
`uv sync` once, while each plugin still publishes to pypi as its own
package with its own version.
_Avoid_: monorepo (too generic), namespace package (a different
Python concept).

**package**:
One pypi-publishable plugin — `packages/dikw-converter-<format>/`
with its own `pyproject.toml`, `src/`, and `tests/`. Each package
ships exactly one Converter implementation (or a tightly-related set;
e.g. a `pandoc`-based package that handles `.docx` + `.rtf` is fine).
_Avoid_: module (ambiguous — Python modules live inside packages),
plugin (we use plugin for the user-facing concept; package is its
on-disk shape).

**output_dir**:
The temp directory `dikw client` passes to `Converter.convert()`.
Plugin writes `<stem>.md` + `assets/*` into it. Cleaned up after the
import bundle is built — plugin code must not assume it persists.
_Avoid_: target, dest (too generic).

**stub package**:
The `dikw-converter-example` package — a minimal working Converter
that registers `.example` extension. Exists as a copy-template for new
plugin authors, not as a tool to actually use. CI keeps it green so
the template never goes stale.
_Avoid_: skeleton (could mean "the whole repo skeleton"), example
(too generic when written without `package`).

## Relationships

- A **package** ships one **converter plugin**. The package name and
  the engine name are usually different (`dikw-converter-pdf` ships
  the `marker` engine, hypothetically).
- A **package** depends on dikw-core (it imports the Converter
  Protocol); dikw-core never depends on any package.
- The **workspace** is a dev-time convenience, not a runtime concept —
  end users `pip install` individual packages.
