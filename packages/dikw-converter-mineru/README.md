# dikw-converter-mineru

[MinerU online-API](https://mineru.net/apiManage/docs) converter plugin
for [`dikw-core`][core]'s `dikw client import`. Once installed alongside
dikw-core, running

```bash
dikw client import paper.pdf
```

uploads the PDF to MinerU, waits for it to finish parsing, downloads the
result ZIP, and commits the converted markdown + assets into
`<base>/sources/paper/`.

[core]: https://github.com/opendikw/dikw-core

## Supported formats

The MinerU API claims many formats; the plugin's `extensions` tuple is
deliberately the **subset that fits dikw cleanly**:

- `.pdf` — primary use case
- `.docx`, `.doc`
- `.pptx`, `.ppt`
- `.xlsx`, `.xls`

Not enabled in v0.1: image inputs (`.png` / `.jpg` etc. — would collide
with dikw-core's asset semantics) and `.html` (overkill for HTML; use a
lighter local converter). Both may land in v0.2 behind an env flag.

## Install

```bash
# Once published:
pip install dikw-converter-mineru

# Upgrade later:
pip install --upgrade dikw-converter-mineru

# Pin a specific version:
pip install 'dikw-converter-mineru==0.1.0'

# Uninstall — the entry-point disappears on next discovery.
pip uninstall dikw-converter-mineru

# For local development from this monorepo:
pip install -e packages/dikw-converter-mineru
```

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md) for the per-release history. Each
GitHub Release also carries the same notes; published wheels and
sdists are attached there for offline / air-gapped installs.

## Auth — `MinerUAPIKey` env var

The plugin reads the MinerU API token from the process environment:

1. **Explicit constructor param** wins: `MineruConverter(api_key="…")`.
   Useful for programmatic use, smoke tests, or scripts where you don't
   want to rely on shell-level env.
2. Otherwise `MinerUAPIKey` — matches the literal key name on MinerU's
   user dashboard, so users can paste-and-go.
3. Otherwise `DIKW_MINERU_API_KEY` — dikw-convention fallback for
   environments that want all plugin secrets to share a single prefix.

The plugin **does not auto-load `.env`** — that would force a
`python-dotenv` dep and surprise users about which file gets loaded
when. Load `.env` into your shell yourself, e.g.

```bash
# uv (cross-platform)
uv run --env-file .env dikw client import paper.pdf

# PowerShell
$env:MinerUAPIKey = ((Get-Content .env | Select-String "^MinerUAPIKey=") -split "=", 2)[1]
dikw client import paper.pdf

# direnv / shell rc are also fine.
```

Get a token at [mineru.net](https://mineru.net/) → user menu → API
manage. Tokens are JWTs and last roughly 90 days; rotate at expiry.

## What it produces

For `paper.pdf`:

```
<output_dir>/
├── paper.md                    # MinerU's full.md, renamed, with image refs rewritten
└── assets/
    ├── paper.pdf               # original input, kept as provenance
    └── …                       # images extracted by MinerU (png/jpg/…)
```

Image references in the markdown use the wikilink form
(`![[assets/figure-1.png|caption]]`) for the same reason as
[dikw-converter-epub](../dikw-converter-epub/README.md): it survives
filenames containing `(` or `)`, and alt text containing `]`.

MinerU's internal byproducts (`layout.json`, `*_content_list.json`,
`*_model.json`) are dropped — they're useful to MinerU developers, not
to a dikw user. If you want them in v0.2, file an issue.

## Privacy

The MinerU API is **hosted**. Your file is uploaded to OpenXLab's CDN
and processed in the cloud. Don't import documents that aren't allowed
to leave your machine. If you need local processing, install one of the
local-engine plugins instead (currently in the planning stage:
`dikw-converter-pymupdf`, `dikw-converter-docling`).

## Quota & limits

- Each MinerU account gets **~1000 pages/day at high priority**; beyond
  that you're downgraded (slower, not failed).
- Hard caps: **200 MB per file, 200 pages per file**. The plugin
  pre-checks the file size and fails with a clear error before any
  upload if you exceed.
- HTTP 5xx is retried with exponential backoff (up to 3 retries).
- Auth errors (`A0202`, `A0211`) and quota exhaustion (`-60018`) fast-fail
  with an actionable message.

## Determinism

VLM-based document parsing is **not byte-deterministic on the server
side**. The plugin compensates by setting `cache_tolerance` to the
maximum allowed value when submitting; the same input within the cache
window returns the same cached result, so back-to-back imports
of the same file produce identical bytes.

After the cache window lapses, the same file may produce subtly
different markdown on a re-run. dikw-core's content-hash skip would then
treat it as a new revision and re-chunk + re-embed. This is a documented
trade-off of the hosted route; the local-engine plugins (when they
land) will be fully deterministic.

## Known limitations (v0.1)

- Cannot run offline.
- No `--language` override yet (uses MinerU's `"ch"` default —
  Chinese+English bilingual). v0.2 will add an env-var knob.
- No `is_ocr` / `enable_table` / `enable_formula` overrides yet.
- No structured-output (`extra_formats`) support.

## Tests

```bash
uv run pytest packages/dikw-converter-mineru
```

All tests are unit-level; they mock HTTP via `pytest-httpx`. No tests
call the real MinerU API (would burn your quota + leak your token into
CI artifacts).

For a real-API smoke test, place a small PDF in the workspace's
`scratch/` directory (gitignored) and run a one-off conversion
yourself; see the plugin's [plan note][plan] if one exists, or just
construct `MineruConverter()` directly with your token.

[plan]: ../../.claude/plans/converter-epub-cheeky-wall.md
