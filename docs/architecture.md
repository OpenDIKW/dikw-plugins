# dikw-plugins — architecture

**This is the file to read first if you're new to the repo.** It tells
the whole story of why this repo exists, what the plugin contract is,
and how `dikw client import paper.pdf` actually works end-to-end. The
content is self-contained — no prior context required, no other docs
need to be open.

For the formal contract spec (Protocol shape, entry-points, error
modes), see [`dikw-core/docs/converters.md`][spec]. For the decision
record (what alternatives we rejected and why), see
[`dikw-core/docs/adr/0001-client-side-converter-plugins.md`][adr].

[spec]: https://github.com/opendikw/dikw-core/blob/main/docs/converters.md
[adr]: https://github.com/opendikw/dikw-core/blob/main/docs/adr/0001-client-side-converter-plugins.md

---

## 1. The problem

[`dikw-core`][core] is a knowledge engine that ingests markdown + assets.
Its `dikw client import` command commits `.md` files into a base's
`sources/` tree; ingest then chunks + embeds them. Two long-standing
constraints:

[core]: https://github.com/opendikw/dikw-core

- **Input contract is `md + assets/`.** PDF, EPUB, audio, video — any
  preprocessing into that shape belongs upstream of dikw-core. The
  engine never grew a PDF parser, MinerU integration, or marker
  bindings, and that was deliberate.
- **Client / server split.** `dikw client` is a thin process (stdlib +
  httpx + typer + rich) talking HTTP to a separate `dikw serve`. The
  server has its own dependency whitelist (`api/schemas/storage/providers`
  only) — no ML libraries, no PDF parsers.

So users hit a real friction point: every PDF/EPUB has to be converted
externally first, then imported. The natural ask is "let me just
`dikw client import paper.pdf` and have it work".

## 2. The architectural problem

There are four places a PDF-conversion step *could* live, and three of
them break dikw-core's existing invariants:

| Location                          | Why it doesn't work                                                       |
| --------------------------------- | ------------------------------------------------------------------------- |
| Engine-side `SourceBackend`       | `parse_any()` is called inside `dikw serve`. Server would need PyTorch.   |
| Server-side plugin                | Same dep problem + the user has to upload raw bytes to the server first.  |
| `pip install dikw-core[pdf]` extra | Conflates dikw-core's release cycle with marker / MinerU's churn.        |
| **Client-side plugin**            | ✅ Fits — conversion runs in the thin client, server stays oblivious.    |

Why we landed here: every PDF-to-md tool ships heavy dependencies
(PyTorch, OCR models, GB of weights). Bundling those into dikw-core
would make every install heavy and pin the tool ecosystem to
dikw-core's release cadence. Conversely, **client-side plugins** let
dikw-core stay light, let plugin authors pick their dep stack freely,
and let the conversion run on whatever machine has the user's GPU.

(The pure-upstream alternative — separate CLI like `dikw-loaders pdf
paper.pdf -o /tmp/paper/` then `dikw client import /tmp/paper/` — is a
valid escape hatch and was actually the prior convention. We chose the
plugin path purely for one-step UX. See ADR 0001 for the full
trade-off discussion.)

## 3. How it works end-to-end

```
User                  dikw client                  Plugin                 Server
 │                     │                            │                      │
 │  $ dikw client      │                            │                      │
 │    import paper.pdf │                            │                      │
 │ ───────────────────>│                            │                      │
 │                     │ resolve(client.toml + env) │                      │
 │                     │ discover(entry_points)     │                      │
 │                     │ pick(".pdf") → marker      │                      │
 │                     │ mkdtemp(staging/)          │                      │
 │                     │ ───────────────────────────>│                      │
 │                     │                            │  parse PDF (GPU)     │
 │                     │                            │  write paper.md +    │
 │                     │                            │    assets/*.png +    │
 │                     │                            │    assets/paper.pdf  │
 │                     │ <───────────────────────────│                      │
 │                     │ build_import(staging)      │                      │
 │                     │ ────────────────────────────────────────────────> │
 │                     │                            │                      │ commit to <base>/sources/paper/
 │                     │ <──────────────────────────────────────────────── │
 │                     │ shutil.rmtree(staging)     │                      │
 │ <───────────────────│                            │                      │
```

Key points:

- **Plugin discovery** is via `importlib.metadata.entry_points(group="dikw.client.converters")`,
  lazy — only fires when a non-md file triggers dispatch. Common
  commands (`status`, markdown-only `import`, `query`) never pay
  plugin import cost.
- **The plugin writes to a temp staging directory** that dikw-core
  creates and owns. dikw-core packages the staging dir like any other
  user-authored md tree, then cleans it up.
- **The server sees a normal md+assets import.** It has no idea a PDF
  was involved.

## 4. The contract

Plugins implement this Protocol from `dikw_core.client.converters`:

```python
@runtime_checkable
class Converter(Protocol):
    name: str                    # engine label, e.g. "marker"
    extensions: tuple[str, ...]  # claimed file suffixes, e.g. (".pdf",)
    def convert(self, input_path: Path, output_dir: Path) -> None: ...
```

Plugins register themselves via entry-points in their `pyproject.toml`:

```toml
[project.entry-points."dikw.client.converters"]
marker = "dikw_converter_pdf:MarkerConverter"
```

`Converter.convert(input_path, output_dir)`:
- Writes one or more `*.md` files into `output_dir`, each with its
  assets in a sibling `assets/` directory.
- Image-refs every asset from the md it writes (`![alt](assets/foo.png)`
  or `![[assets/foo.pdf]]`). Without that, dikw-core's `md_inspect`
  pass either drops unreferenced assets silently (if their extension
  isn't in `_DEFAULT_ASSET_EXTENSIONS`) or rejects them as orphans
  (if it is).
- Must be **deterministic** for the same input bytes. dikw-core's
  ingest hashes the md and skips unchanged sources; non-deterministic
  output defeats that.

## 5. Output layout convention

For a single-input dispatch (`dikw client import paper.pdf`), the
plugin's `output_dir` becomes:

```
<output_dir>/
├── <stem>.md            # paper.md — the converted markdown
└── assets/
    ├── <stem>.<orig>    # paper.pdf — original, kept as provenance asset
    ├── figure-1.png     # extracted images
    └── …
```

After dikw-core packages this and the server commits, the user's base
holds:

```
<base>/sources/paper/
├── paper.md
└── assets/
    ├── paper.pdf
    ├── figure-1.png
    └── …
```

Multi-md output (e.g. one PDF split into per-chapter markdowns) is
allowed — each `*.md` becomes its own source after import.

## 6. Selection priority (multi-plugin)

When two plugins both claim `.pdf` (e.g. marker + mineru), `dikw client`
picks one in this order:

1. `--converter=<name>` on the command line (one-shot override).
2. `DIKW_CLIENT_CONVERTER_<EXT>` env var, e.g. `DIKW_CLIENT_CONVERTER_PDF=marker`.
3. `client.toml` `[default.converters]` entry:
   ```toml
   [default.converters]
   ".pdf" = "marker"
   ```
4. Exactly one plugin registered → use it (zero config for the common case).
5. Otherwise raise `ConverterError` listing the engines + remediation.

dikw-core never silently picks one of multiple — predictability over
magic.

## 7. What this repo is

A **uv-workspace monorepo** of converter plugin packages. Each
`packages/dikw-converter-<format>/` is its own pypi-publishable
package with its own version and dependencies. They share dev
tooling (one `pyproject.toml` at the root pinning ruff / mypy /
pytest) but release independently.

This shape was picked over alternatives:

- **Multi-repo** (one git repo per plugin) — too much overhead for
  pre-alpha; can split later as the contract stabilises and external
  authors arrive.
- **Inside `dikw-core[pdf]` extra** — defeats the whole point of
  decoupling release cycles.

Why **sibling to dikw-core** rather than inside it: this repo doesn't
need to release in lockstep with dikw-core (or even with itself —
`dikw-converter-pdf` can ship v3.4 while `dikw-converter-epub` is
still at v0.1). Sibling-repo signals "officially maintained but not
core".

## 8. Naming conventions

- **Package name**: `dikw-converter-<format>` (pypi-style). One format
  per package is the norm; multi-format ok when formats share an
  upstream tool.
- **Module name**: `dikw_converter_<format>` (Python identifier form).
- **Engine name** (`Converter.name`): short, unique across plugins
  the user has installed. Typically the underlying tool's name —
  `marker`, `mineru`, `docling`, `ebook2md`, …
- **Entry-point name** (in pyproject.toml): conventionally the engine
  name, but informational only — `Converter.name` is what dispatch
  uses.

## 9. Provenance: keep the original

By design convention, plugins copy the original input file into
`output_dir/assets/<stem>.<ext>` as a provenance asset. dikw-core's
`_DEFAULT_ASSET_EXTENSIONS` already includes `.pdf`; for other
extensions, just image-ref them from the md and dikw-core's
md_inspect picks them up regardless of extension.

This means after import, the user's base has both the converted
markdown AND the original PDF/EPUB — opening `<base>/sources/paper/`
gives them both, useful for verifying quotes back to the source
document.

Plugins that want to skip the original (e.g. when the input is huge
and conversion is "good enough") can omit it; document the choice in
the plugin's README.

## 10. Idempotency

dikw-core's ingest pipeline skips sources whose content hash hasn't
changed. A converter that produces byte-different output for the same
input PDF (because of timestamps, random seeds, run IDs, …) makes
every re-import look like a content change and forces a full re-chunk
+ re-embed.

Plugins should be deterministic. Where the underlying tool isn't
(some OCR pipelines, some LLM-assisted converters), the plugin should
either:

- Pin the upstream tool's seed where exposed, or
- Document the non-determinism so users understand the cost.

## 11. Pointers

- [`dikw-core/src/dikw_core/client/converters.py`](https://github.com/opendikw/dikw-core/blob/main/src/dikw_core/client/converters.py)
  — the actual Protocol + discovery + selection logic. Read this once
  before writing a plugin so the contract is in muscle memory.
- [`dikw-core/docs/converters.md`](https://github.com/opendikw/dikw-core/blob/main/docs/converters.md)
  — formal spec. The "what every plugin must do" document.
- [`dikw-core/docs/adr/0001-client-side-converter-plugins.md`](https://github.com/opendikw/dikw-core/blob/main/docs/adr/0001-client-side-converter-plugins.md)
  — decision record. The "why we picked this over alternatives"
  document.
- [`docs/plugin-author-guide.md`](plugin-author-guide.md) — tutorial.
  Pick up here once you're ready to actually write a plugin.
- [`docs/tool-survey.md`](tool-survey.md) — quick map of the PDF/EPUB
  tool landscape (marker / MinerU / docling / pymupdf4llm) circa
  2026-05. Useful when picking which tool to wrap.
- [`packages/dikw-converter-example/`](../packages/dikw-converter-example/)
  — minimal working Converter you can copy as a template.
