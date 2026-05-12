# PDF/EPUB tool ecosystem — 2026-05 snapshot

Quick orientation for picking which upstream tool to wrap. **Not a
recommendation** — none of these is universally best. Use this to
narrow down before doing your own evaluation on your actual document set.

## PDF → Markdown

There are two deployment shapes — **hosted API** (network in, network
out; no GPU on your machine) vs **local engine** (multi-GB models, GPU
preferred). The right axis to pick first.

### Hosted

| Tool          | Strength                       | Weakness                          | Cost / quota | License (of the SDK call) |
| ------------- | ------------------------------ | --------------------------------- | ------------ | -------- |
| **MinerU online** | Chinese academic, tables, formulas; PDF + Office formats | Network dep; privacy (file uploaded to OpenXLab CDN) | 1000 pages/day high-priority, then deprioritized | Apache-derived (token = JWT, ~90d) |
| LlamaParse / LlamaCloud | Very high quality on tables; LLM-assisted | Hosted, per-page billing | Pay per page | Commercial |
| Mathpix MD    | Math / formulas best-in-class  | Commercial, per-call billing      | Pay per call | Commercial |
| AWS Textract / Azure DI / Google DocAI | Enterprise stability | Cloud lock-in; pricing | Pay-as-you-go | Commercial |

### Local engines

| Tool        | Strength                       | Weakness                       | Deps weight | License |
| ----------- | ------------------------------ | ------------------------------ | ----------- | ------- |
| marker      | English papers, fast, GPU-aware | Chinese / non-Latin so-so; GPU expected | PyTorch + Surya OCR (~5GB models) | **GPL-3.0** (was previously reported here as Apache; verify if it changes back) |
| MinerU local | Chinese academic, layout      | Heavier setup, slower than hosted | PyTorch + custom VLM (~10GB) | **Apache-derived** ("MinerU Open Source License", relaxed from AGPL in 2025/26) |
| docling     | General, strong tables, IBM-backed | Slower than marker on English | PyTorch + Granite-Docling-258M (~3GB) | MIT |
| pymupdf4llm | Light, no ML, deterministic    | Falls over on complex layout   | PyMuPDF + bundled layout (~50MB) | AGPL or commercial |
| pdfplumber  | Table-aware, deterministic     | No layout understanding; weak on narrative text | Pure Python (~50MB) | MIT |
| pdfminer.six | CJK support, very low-level   | No layout, no markdown emission; needs scaffolding | Pure Python (~20MB) | MIT/AGPL mix |

The right choice depends on:

- **Online OK?** Hosted MinerU is the fastest way to get production
  quality without provisioning a GPU; the trade is sending files to
  the cloud + a per-account quota. If your documents must stay local,
  pick a local engine.
- **Language mix**: marker for mostly English, MinerU (hosted or local)
  for Chinese-heavy corpora.
- **Tables**: docling shines locally; MinerU + LlamaParse shine hosted.
- **No GPU available**: pymupdf4llm + pdfminer.six are the realistic
  local options; hosted MinerU sidesteps the question entirely.
- **License**: marker is **GPL-3.0** (not Apache as previously stated
  here — verify on each upgrade), so downstream users of your plugin
  inherit GPL. pymupdf4llm is **AGPL or commercial**. MinerU's recent
  license change to its custom "Open Source License" is Apache-derived
  and removes the AGPL ripple it used to carry.

All ecosystems are **moving fast**. Major rewrites and breaking API
changes through 2025 / early 2026; expect to follow upstream closely.
This is one reason we made dikw-core invariant to plugin churn — your
plugin can pin a known-good upstream version without forcing every
dikw-core user to follow.

**Already shipped:** [`dikw-converter-mineru`](../packages/dikw-converter-mineru/)
v0.1, **MinerU online API** route — covers PDF + DOCX + PPTX + XLSX in
one plugin (legitimate "multi-format via shared upstream" exception to
the one-format-per-package norm). Requires `MinerUAPIKey` env var.
Known limitations: hosted-only (no offline mode), VLM output is non-
deterministic outside MinerU's server-side cache window.

## EPUB → Markdown

EPUB is much lighter than PDF. Common approaches:

| Tool         | Notes                                                  |
| ------------ | ------------------------------------------------------ |
| ebooklib     | Python EPUB parser. Combine with html2markdown for the body. |
| pandoc       | Battle-tested, but a subprocess dep — wrap as a Python plugin if you like the conversion quality. |
| Calibre's CLI | `ebook-convert` is high-quality but Calibre is large.  |

Pure-Python via ebooklib + a html → md converter (markdownify,
html2text) gives you a sub-100MB-dep plugin that handles 95% of EPUBs
well.

**Already shipped:** [`dikw-converter-epub`](../packages/dikw-converter-epub/)
v0.1, **stdlib-only** route (no ebooklib / markdownify / lxml deps).
Architecture and parser logic ported from the author's
`holo-epub-reader` project. Known limitation: ~5% of edge-case EPUBs
(non-standard OPF layouts, exotic inline XHTML, books that put body
content inside `<aside>`) may need a future second engine to handle
cleanly.

## Other formats worth considering (not yet covered)

- `.docx` — `python-docx` + custom md emission, or pandoc subprocess.
- `.pptx` — slides have non-linear flow; rarely worth a converter, but
  if you do, treat each slide as a section.
- `.html` / web archives — `readability-lxml` + html2text for "main
  content" extraction.
- `.txt` — trivial, but a plugin can do encoding detection + line-
  ending normalization upfront.

## How to evaluate

For a serious plugin, set up a fixed-doc benchmark:

1. Pick 20-50 representative documents from your domain.
2. Run conversion with each candidate tool.
3. Score on: text fidelity (does prose survive?), structure
   (headings preserved?), tables (rows aligned?), images
   (extracted with reasonable filenames?), determinism (run twice,
   diff).
4. Time on a reference machine.
5. Pick one. Document the choice in the plugin's README so users
   know what trade-offs they're inheriting.

The dikw-plugins repo would happily host the benchmark fixtures if
they're shareable — file an issue.

---

This document is a snapshot; tool capabilities shift quickly. Last
updated 2026-05-12. If you've recently evaluated alternatives, PRs to
update this file are welcome.
