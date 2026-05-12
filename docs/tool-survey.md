# PDF/EPUB tool ecosystem — 2026-05 snapshot

Quick orientation for picking which upstream tool to wrap. **Not a
recommendation** — none of these is universally best. Use this to
narrow down before doing your own evaluation on your actual document set.

## PDF → Markdown

| Tool        | Strength                       | Weakness                       | Deps weight | License |
| ----------- | ------------------------------ | ------------------------------ | ----------- | ------- |
| marker      | English papers, fast, GPU-aware | Chinese / non-Latin so-so     | PyTorch + Surya OCR (~5GB models) | Apache 2.0 |
| MinerU      | Chinese academic, layout       | Heavier setup, slower         | PyTorch + custom models (~10GB) | AGPL (check current) |
| docling     | General, strong tables, IBM-backed | Slower than marker on English | PyTorch (~3GB) | MIT |
| pymupdf4llm | Light, no ML, deterministic    | Falls over on complex layout   | PyMuPDF only (~30MB) | AGPL or commercial |

The right choice depends on:

- **Language mix**: marker for mostly English, MinerU for Chinese-heavy
  corpora.
- **Tables**: docling shines here.
- **No GPU available**: pymupdf4llm is the only realistic option.
- **License**: AGPL (MinerU, PyMuPDF) ripples into your plugin's
  license — check before publishing.

All four ecosystems are **moving fast**. Major rewrites and breaking
API changes through 2025 / early 2026; expect to follow upstream
closely. This is one reason we made dikw-core invariant to plugin
churn — your plugin can pin a known-good upstream version without
forcing every dikw-core user to follow.

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
