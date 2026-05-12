"""XHTML → list of :class:`Block` walker.

Trimmed compared to a fuller HTML→md walker: we deliberately do not
chunk text (dikw-core handles chunking downstream) and we do not detect
placeholder chapter titles (chapter labels come from the OPF spine, not
the XHTML ``<title>``).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from html.parser import HTMLParser

from ._models import Block, ImageRef, ListType
from ._opf import resolve_href

BLOCK_TEXT_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre"})
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
IGNORED_TAGS = frozenset({"script", "style", "noscript"})
# Nav-style chrome that repeats across chapters; stripped unless the
# caller opts in. EPUB authors often duplicate the table of contents on
# every page, which produces a wall of noise in the merged markdown.
NAV_TAGS = frozenset({"nav", "header", "footer", "aside"})
@dataclass
class _ListState:
    """One frame on the parser's nested-list stack."""

    type: ListType
    index: int  # last-emitted 1-based index for "ol"; unused for "ul"


def _clean_text(text: str) -> str:
    return " ".join(text.split())


class _HTMLBlockParser(HTMLParser):
    def __init__(self, html_path: str, *, strip_nav: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.html_path = html_path
        self._html_dir = posixpath.dirname(html_path)
        self.strip_nav = strip_nav
        self.blocks: list[Block] = []
        self.image_refs: list[ImageRef] = []
        self._current_tag: str | None = None
        self._current_text_parts: list[str] = []
        self._ignore_stack: list[str] = []
        self._list_stack: list[_ListState] = []
        self._current_list_type: ListType | None = None
        self._current_list_index: int | None = None

    def _is_ignored(self) -> bool:
        return bool(self._ignore_stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in IGNORED_TAGS or (self.strip_nav and tag in NAV_TAGS):
            self._ignore_stack.append(tag)
            return

        # <br> in the middle of text would silently glue adjacent words
        # together ("hello<br/>world" → "helloworld") because the parser
        # collapses whitespace between data callbacks. Emit a space so
        # the surrounding tokens stay separate words.
        if tag == "br" and not self._is_ignored() and self._current_tag is not None:
            self._current_text_parts.append(" ")
            return

        attrs_dict = dict(attrs)

        if tag == "ol" and not self._is_ignored():
            try:
                initial_index = int(attrs_dict.get("start") or 1) - 1
            except ValueError:
                initial_index = 0
            self._list_stack.append(_ListState(type="ol", index=initial_index))
            return

        if tag == "ul" and not self._is_ignored():
            self._list_stack.append(_ListState(type="ul", index=0))
            return

        if tag == "img" and not self._is_ignored():
            src = attrs_dict.get("src")
            if src:
                zip_path = resolve_href(self._html_dir, src)
                if zip_path is None:
                    return  # external URL or unparseable href; ignore
                alt_raw = attrs_dict.get("alt") or attrs_dict.get("title")
                alt = _clean_text(alt_raw) if alt_raw else None
                self.blocks.append(
                    Block(type="image", image=zip_path, alt=alt, tag="img")
                )
                self.image_refs.append(ImageRef(zip_path=zip_path, alt=alt))
            return

        if tag in BLOCK_TEXT_TAGS and not self._is_ignored() and self._current_tag is None:
            self._current_tag = tag
            self._current_text_parts = []
            if tag == "li" and self._list_stack:
                current_list = self._list_stack[-1]
                if current_list.type == "ol":
                    current_list.index += 1
                    self._current_list_index = current_list.index
                self._current_list_type = current_list.type

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._current_tag == tag:
            raw_text = "".join(self._current_text_parts)
            text = raw_text.replace("\r\n", "\n").strip("\n") if tag == "pre" else _clean_text(raw_text)
            if text:
                if tag in HEADING_TAGS:
                    self.blocks.append(
                        Block(type="heading", text=text, level=int(tag[1]), tag=tag)
                    )
                else:
                    self.blocks.append(
                        Block(
                            type="text",
                            text=text,
                            tag=tag,
                            list_type=self._current_list_type,
                            list_index=self._current_list_index,
                        )
                    )
            self._current_tag = None
            self._current_text_parts = []
            self._current_list_type = None
            self._current_list_index = None

        if self._ignore_stack and self._ignore_stack[-1] == tag:
            self._ignore_stack.pop()

        if self._list_stack and self._list_stack[-1].type == tag:
            self._list_stack.pop()

    def handle_data(self, data: str) -> None:
        if not self._is_ignored() and self._current_tag is not None:
            self._current_text_parts.append(data)


def extract_blocks(
    html_bytes: bytes,
    html_path: str,
    *,
    strip_nav: bool = True,
) -> tuple[list[Block], list[ImageRef]]:
    """Walk one XHTML chapter and emit blocks + referenced images.

    Always decodes with ``errors="replace"`` so a single mis-declared
    chapter doesn't crash the whole import. Edge characters surface as
    ``U+FFFD`` in the resulting markdown.
    """
    parser = _HTMLBlockParser(html_path, strip_nav=strip_nav)
    parser.feed(html_bytes.decode("utf-8", errors="replace"))
    parser.close()
    return parser.blocks, parser.image_refs
