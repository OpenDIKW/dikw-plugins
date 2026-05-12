# dikw-converter-example

Reference stub for the `dikw-plugins` repo. Registers a `Converter`
for the `.example` extension that takes the input file's text and
wraps it as a single-block markdown source, copying the original as a
provenance asset.

**This package is not useful in production** — it exists so plugin
authors have a working minimum to copy. See
[`docs/plugin-author-guide.md`][guide] in the repo root for the
"copy this, then rename + replace" walkthrough.

[guide]: ../../docs/plugin-author-guide.md

## What it does

Given `notes.example`:

```
Hello
world
```

Running `dikw client import notes.example` (with this plugin
installed) produces:

```
<base>/sources/notes/
├── notes.md          # "# notes\n\n```\nHello\nworld\n```\n\n![original](assets/notes.example)\n"
└── assets/
    └── notes.example  # bytes copied verbatim from input
```

That's the entire contract on disk — md plus image-referenced assets.

## Install (editable, for development)

```bash
pip install -e ../../packages/dikw-converter-example
```

Or from the workspace root with `uv sync`.

## Run the tests

```bash
uv run pytest packages/dikw-converter-example/tests
```
