"""Block list → markdown string renderer.

Two safety contracts the renderer enforces:

1. Asset references use the Obsidian wikilink form ``![[path|alt]]``
   rather than the standard ``![alt](path)``. dikw-core's md_inspect
   accepts both, but the wikilink form copes with filenames containing
   ``(`` or ``)`` — entirely common for user-named EPUB inputs (e.g.
   ``book(1).epub``) — whereas the standard form's regex terminates at
   the first ``)`` in the path and silently drops those references.

2. **EPUB-derived text is escaped** before emission. A book whose
   ``<dc:title>`` or paragraph text contains literal ``![pwn](evil.png)``
   or ``![[assets/evil.png]]`` would otherwise inject image refs into
   the rendered markdown — md_inspect scans the whole body, so the
   injected ref would either trigger asset_missing errors or hijack an
   already-extracted asset name. The escape only neutralizes image-embed
   openers (``!`` immediately before ``[``); other markdown punctuation
   is left alone so prose still reads naturally.
"""

from __future__ import annotations

from ._models import Block

_MAX_MD_HEADING_LEVEL = 6
# When a book title is present, it claims the only H1 line and chapter
# titles drop to H2; chapter-internal headings then shift by +2 so their
# XHTML H1 lands at md H3. With no book title, the chapter title is the
# top level and inner headings shift by +1 instead.
_INNER_SHIFT_WITH_TITLE = 2
_INNER_SHIFT_WITHOUT_TITLE = 1


def render_markdown(
    chapters: list[tuple[str, list[Block]]],
    *,
    title: str | None,
    creator: str | None,
    provenance_path: str,
) -> str:
    """Render one EPUB's chapters into a single markdown string.

    The trailing ``![original](<provenance_path>)`` line is load-bearing:
    md_inspect only follows image-style refs, and ``.epub`` is not in
    dikw-core's default asset extensions — without this image-ref the
    provenance copy is silently dropped from the import bundle.

    ``provenance_path`` is treated as trusted (it's the path the
    orchestrator wrote to disk and wants the md to point at); EPUB-
    derived text fields are escaped to neutralize injected image
    embeds.
    """
    inner_shift = _INNER_SHIFT_WITH_TITLE if title else _INNER_SHIFT_WITHOUT_TITLE
    chapter_level = inner_shift  # same predicate, by construction
    lines: list[str] = []

    if title:
        lines.append(f"# {_escape_md_text(title)}")
        lines.append("")
    if creator:
        lines.append(f"*{_escape_md_text(creator)}*")
        lines.append("")

    for chapter_title, blocks in chapters:
        lines.append(f"{'#' * chapter_level} {_escape_md_text(chapter_title)}")
        lines.append("")
        for block in blocks:
            _emit_block(lines, block, inner_shift=inner_shift, chapter_title=chapter_title)

    lines.append(_wikilink(provenance_path, "original"))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _escape_md_text(text: str) -> str:
    """Neutralize image-embed openers in EPUB-derived text.

    md_inspect's regex matches ``!\\[`` as the opening of a standard
    markdown image and ``!\\[\\[`` for the wikilink form. We need to
    break that match WITHIN the ``!`` ``[`` boundary — escaping the
    leading ``!`` (``![`` → ``\\![``) leaves the regex able to start
    matching at the next character. Inserting a backslash BETWEEN ``!``
    and ``[`` (``![`` → ``!\\[``) is what actually defeats the regex,
    since regex character-class matching is positional and a literal
    ``\\`` is not the same character as ``[``.
    """
    return text.replace("![", "!\\[")


def _wikilink(path: str, alt: str) -> str:
    """Format ``![[path|alt]]`` with breaking characters substituted out.

    The substitution is shared with disk-write call sites (see
    :func:`_convert.safe_asset_relpath`) so the path in the md always
    matches what was written to disk — otherwise the md ref points at
    ``book_1.epub`` while the file is ``book]1.epub`` and md_inspect
    raises ``asset_missing``.
    """
    safe_alt = alt.replace("]", " ").replace("|", " ").strip()
    return f"![[{path}|{safe_alt}]]" if safe_alt else f"![[{path}]]"


def _emit_block(
    out: list[str],
    block: Block,
    *,
    inner_shift: int,
    chapter_title: str,
) -> None:
    if block.type == "heading" and block.text:
        # The first heading inside the chapter often IS the chapter
        # title (since that's how we pick the chapter title). Skip it
        # so it doesn't repeat under its own header line.
        if block.text.strip() == chapter_title.strip():
            return
        level = (block.level or 1) + inner_shift
        level = max(1, min(level, _MAX_MD_HEADING_LEVEL))
        out.append(f"{'#' * level} {_escape_md_text(block.text)}")
        out.append("")
        return

    if block.type == "text" and block.text:
        safe = _escape_md_text(block.text)
        if block.tag == "li":
            if block.list_type == "ol" and block.list_index is not None:
                out.append(f"{block.list_index}. {safe}")
            else:
                out.append(f"- {safe}")
            return
        if block.tag == "blockquote":
            for line in safe.splitlines() or [""]:
                out.append(f"> {line}".rstrip())
            out.append("")
            return
        if block.tag == "pre":
            # md_inspect scans the whole body — it doesn't honor code
            # fences — so ``![evil](x.png)`` inside a ``pre`` block
            # would still be picked up as an asset ref. Escape pre
            # content the same way as inline text. Visible artifact: a
            # literal ``\[`` shows up inside the fence; acceptable
            # trade for safety since pre blocks rarely show prose.
            out.append("```")
            out.extend(_escape_md_text(block.text).splitlines())
            out.append("```")
            out.append("")
            return
        out.append(safe)
        out.append("")
        return

    if block.type == "image" and block.image:
        out.append(_wikilink(block.image, block.alt or ""))
        out.append("")
