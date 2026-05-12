"""Internal data carriers — parsed OPF metadata and HTML-extracted blocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BlockType = Literal["heading", "text", "image"]
ListType = Literal["ul", "ol"]


@dataclass(frozen=True)
class ManifestItem:
    """One ``<item>`` from the OPF manifest."""

    id: str
    href: str  # OPF-relative; resolve against ``OpfPackage.opf_dir`` for zip path
    media_type: str | None


@dataclass(frozen=True)
class OpfPackage:
    """Parsed OPF: metadata + manifest + spine + the OPF's own zip dir."""

    title: str | None
    creator: str | None
    manifest: dict[str, ManifestItem]
    spine: list[str]  # ordered idrefs into ``manifest``
    opf_dir: str  # posix dirname of the OPF; "" when OPF is at zip root


@dataclass
class Block:
    """A single rendered unit produced by the XHTML walker.

    Mutable on purpose: the orchestrator rewrites ``image`` from a raw
    zip path into the final ``assets/<opf-relative>`` form after the
    walker returns, so the renderer never sees the intermediate state.
    """

    type: BlockType
    text: str | None = None
    image: str | None = None
    alt: str | None = None
    level: int | None = None
    tag: str | None = None
    list_type: ListType | None = None
    list_index: int | None = None


@dataclass(frozen=True)
class ImageRef:
    """An image referenced from XHTML; key for dedup before extraction."""

    zip_path: str  # absolute path inside the EPUB zip
    alt: str | None
