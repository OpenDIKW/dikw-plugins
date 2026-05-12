# Plugin author guide

Tutorial for writing a new converter plugin from scratch. Prereqs:
read [`architecture.md`](architecture.md) first so the contract and
output layout are in your head.

## 0. Pick a target

Decide what file extension(s) you're handling and which upstream tool
does the heavy lifting. For PDFs in 2026-05 the realistic options are
documented in [`tool-survey.md`](tool-survey.md). Settle on ONE
upstream tool per plugin — wrapping multiple converters behind a
single plugin makes versioning and dep-conflict debugging painful.

Naming: package = `dikw-converter-<format>`, module =
`dikw_converter_<format>`, engine name = the upstream tool's name
(e.g. `marker`).

## 1. Copy the example stub

```bash
cp -r packages/dikw-converter-example packages/dikw-converter-pdf
```

Then rename everything `example` → `pdf` (and `Example` → `Pdf`):

- `packages/dikw-converter-pdf/pyproject.toml` — `name`,
  `entry-points` table, the engine name on the right side.
- `packages/dikw-converter-pdf/src/dikw_converter_example/` →
  `src/dikw_converter_pdf/`.
- `__init__.py` — class name `ExampleConverter` → `MarkerConverter`,
  `name = "example"` → `name = "marker"`, `extensions = (".example",)`
  → `extensions = (".pdf",)`.

## 2. Implement `convert()`

The Protocol is:

```python
from pathlib import Path

class MarkerConverter:
    name = "marker"
    extensions = (".pdf",)

    def convert(self, input_path: Path, output_dir: Path) -> None:
        ...
```

`convert()` must:

1. `output_dir.mkdir(parents=True, exist_ok=True)`.
2. Write `<input_path.stem>.md` into `output_dir` — the converted prose,
   with image-style asset references to anything else you write.
3. Create `output_dir / "assets"` and write extracted images, the
   original input file (provenance), etc.
4. Ensure every asset is image-referenced from the md
   (`![alt](assets/foo.png)` works for any path, even non-images).

Sketch using `marker-pdf` as the upstream:

```python
from pathlib import Path

class MarkerConverter:
    name = "marker"
    extensions = (".pdf",)

    def convert(self, input_path: Path, output_dir: Path) -> None:
        from marker.converters.pdf import PdfConverter  # lazy import
        from marker.models import create_model_dict

        output_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(exist_ok=True)

        # Run marker.
        models = create_model_dict()
        converter = PdfConverter(artifact_dict=models)
        rendered = converter(str(input_path))
        markdown_text, _, images = rendered.markdown, rendered.metadata, rendered.images

        # Write extracted images to assets/ and rewrite refs.
        for img_name, img_pil in images.items():
            img_pil.save(assets_dir / img_name)
        rewritten = _rewrite_image_paths(markdown_text, prefix="assets/")

        # Copy original PDF as provenance.
        original_dest = assets_dir / input_path.name
        original_dest.write_bytes(input_path.read_bytes())

        # Write the md with the original ref appended.
        body = rewritten + f"\n\n![original](assets/{input_path.name})\n"
        (output_dir / f"{input_path.stem}.md").write_text(body, encoding="utf-8")
```

Lazy-import upstream tools inside `convert()` so a `dikw client status`
or a markdown-only `dikw client import` never triggers PyTorch /
model-weight loading. dikw-core's discovery instantiates your class
once, but `convert()` is what does the heavy work.

## 3. Tests

Use the stub's test layout as a template. The minimum:

```python
from pathlib import Path
from dikw_converter_pdf import MarkerConverter

def test_protocol_attributes() -> None:
    c = MarkerConverter()
    assert c.name == "marker"
    assert c.extensions == (".pdf",)

def test_convert_produces_md_and_keeps_original(tmp_path: Path) -> None:
    input_pdf = Path(__file__).parent / "fixtures" / "tiny.pdf"
    out = tmp_path / "tiny"
    MarkerConverter().convert(input_pdf, out)

    assert (out / "tiny.md").exists()
    assert (out / "assets" / "tiny.pdf").exists()
    md = (out / "tiny.md").read_text(encoding="utf-8")
    assert "![original](assets/tiny.pdf)" in md
```

Add a tiny fixture PDF to `tests/fixtures/`. CI runs `uv run pytest`;
keep fixtures small enough to live in git (< 100 KB ideally).

## 4. Test against a live dikw-core

```bash
# Install dikw-core (editable from a sibling checkout, or from pypi):
pip install -e ../dikw-core
# Install your plugin in editable mode:
pip install -e packages/dikw-converter-pdf
# Start a server in one terminal:
dikw serve
# In another:
dikw client import paper.pdf
```

Verify the import lands under `<base>/sources/paper/`:

```
<base>/sources/paper/
├── paper.md
└── assets/
    ├── paper.pdf      # the original
    ├── figure-1.png
    └── …
```

If md_inspect rejects with `asset_missing` or `orphan asset`, check
that the md references every file you wrote (image syntax, not
regular markdown link).

## 5. Determinism check

Run conversion twice on the same input:

```bash
mkdir /tmp/a /tmp/b
python -c "from dikw_converter_pdf import MarkerConverter; from pathlib import Path; MarkerConverter().convert(Path('paper.pdf'), Path('/tmp/a'))"
python -c "from dikw_converter_pdf import MarkerConverter; from pathlib import Path; MarkerConverter().convert(Path('paper.pdf'), Path('/tmp/b'))"
diff -r /tmp/a /tmp/b
```

Empty diff is ideal. If diffs show up only in image binaries (PIL
metadata timestamps, say), normalise the save call. If they show up
in the markdown body, the upstream tool has non-determinism — pin its
seed or document the cost.

## 6. Publish

Each package is independently versioned. Bump `version` in the
package's `pyproject.toml`, tag `dikw-converter-pdf-vX.Y.Z`, and push.
CI publishes to pypi (TBD: trusted-publishing config; mirror what
dikw-core does in `.github/workflows/release.yml`).

## Anti-patterns

- **Lots of inline ML imports at module top.** Pushes the dep cost to
  `dikw client` startup. Keep them inside `convert()`.
- **Hardcoded model paths.** Use the upstream tool's `cache_dir`
  conventions or environment variables; users will hit you with bug
  reports otherwise.
- **Mutating `input_path`.** Don't write back into the user's input
  tree. Read-only.
- **Symlinks / hard links into the user's tree.** dikw-core's
  importer rejects symlinks at pre-flight; symlinks under assets/
  would silently break.
- **Multiple Converter classes in one entry-point.** One entry-point =
  one Converter. Ship multiple entry-points if you have multiple
  engines in the same package.
