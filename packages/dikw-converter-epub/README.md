# dikw-converter-epub

Pure-Python EPUB → markdown converter plugin for [`dikw-core`][core]'s
`dikw client import`. Once installed alongside dikw-core, running

```bash
dikw client import book.epub
```

parses the EPUB locally and commits the converted markdown + assets into
`<base>/sources/book/`.

[core]: https://github.com/opendikw/dikw-core

## What it produces

Given `book.epub`, the plugin writes:

```
<base>/sources/book/
├── book.md                   # H1 book title (if any), italic author, chapter H2s
└── assets/
    ├── book.epub             # original, kept as provenance
    └── <opf-relative-path>/  # extracted images, named by their OPF manifest href
        ├── images/cover.jpg
        └── images/figure-1.png
```

Asset paths inside `assets/` match each image's `href` in the EPUB's OPF
manifest — i.e. the path **relative to the OPF file's directory**. So a
Calibre-produced EPUB whose cover lives at zip path `OEBPS/images/cover.jpg`
lands at `assets/images/cover.jpg` (the `OEBPS/` publication-root prefix
is stripped automatically by the EPUB href-resolution model). A Pandoc
EPUB whose images live under `EPUB/media/` produces `assets/media/...`.

## Design choices (v0.1)

- **No third-party dependencies.** Uses only `zipfile`, `xml.etree.ElementTree`,
  and `html.parser` from the Python stdlib. No ebooklib, no markdownify.
  Trade-off: ~5% of edge-case EPUBs (non-standard OPF layouts, exotic
  inline XHTML) may need follow-up patches.
- **Asset references use wikilink syntax** (`![[path|alt]]`). dikw-core's
  md_inspect accepts both `![alt](path)` and the wikilink form; the
  wikilink form is the only one that handles asset paths containing
  `(` or `)` — common in user-named EPUB files (`book(1).epub`) — and
  alt text containing `]`.
- **Fresh `output_dir` assumed.** dikw-core's importer creates a fresh
  temp directory and hands it to `convert()`. If you're calling this
  plugin directly, pass an empty path you control — reusing a dirty
  directory will leave stale assets from a previous run.
- **One markdown file per EPUB.** Chapters become H2 sections in a single
  `<stem>.md`. Per-chapter splitting is deferred to a future minor
  version.
- **Deterministic output.** The same EPUB bytes produce byte-identical
  markdown + assets on every run (no timestamps, no random IDs).
- **`<nav>` / `<header>` / `<footer>` / `<aside>` / `<script>` / `<style>`
  are stripped** during XHTML walk. Repeats-on-every-chapter nav blocks
  don't survive into the markdown.
- **Heading levels are shifted** so that the book title is the only H1,
  chapter titles are H2, and a chapter's internal XHTML headings sit
  under that. If the EPUB has no `<dc:title>` metadata, the H1 line is
  skipped and chapters become the top level.
- **Non-UTF-8 XHTML is decoded with `errors="replace"`.** XHTML in the
  wild lies about its encoding often enough that strict decoding causes
  more pain than the occasional `�` replacement character in
  output.

## Install

```bash
# In a real dikw client environment:
pip install dikw-converter-epub

# For local development from this monorepo:
pip install -e packages/dikw-converter-epub
```

## Run the tests

```bash
uv run pytest packages/dikw-converter-epub
```
