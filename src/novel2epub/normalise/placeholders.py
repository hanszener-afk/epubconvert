"""Image placeholders: a paragraph of its own reading "[image: file]".

    [image: map.png]
    [image: map.png | A map of the harbour at low tide]

The text after "|" is the image's description (its alt text, read aloud by
screen readers). The file is looked for among the images added for the book
(the window's Images tab, or [[images]] in the book file), by file name, and
then in the manuscript's folder. Case does not matter.

A placeholder inside a sentence is left as text, with a warning: images go
between paragraphs. A placeholder whose image cannot be found stops the
conversion, naming the placeholder and where it is, rather than leaving the
book without the picture. Each replaced placeholder is logged as a removal,
so the fidelity check accounts for its text.
"""

from __future__ import annotations

import re
from pathlib import Path

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.images import to_core_image
from novel2epub.ir import Block, Book, Image, Paragraph, Resource, Section
from novel2epub.normalise.inline import plain_text

PLACEHOLDER = re.compile(r"^\s*\[\s*image\s*:\s*([^\]|]+?)\s*(?:\|\s*([^\]]*?)\s*)?\]\s*$", re.I)
_ANYWHERE = re.compile(r"\[\s*image\s*:[^\]]*\]", re.I)


class _Images:
    def __init__(
        self, book: Book, added: list[tuple[Path, str | None]], folders: list[Path]
    ) -> None:
        self.book = book
        self.added = {path.name.lower(): (path, alt) for path, alt in added}
        self.folders = folders
        self.ids: dict[Path, str] = {}

    def find(self, name: str) -> tuple[Path, str | None] | None:
        found = self.added.get(Path(name).name.lower())
        if found:
            return found
        for folder in self.folders:
            candidate = folder / name
            if candidate.is_file():
                return candidate, None
            if folder.is_dir():  # the same name in another case
                for entry in folder.iterdir():
                    if entry.name.lower() == name.lower() and entry.is_file():
                        return entry, None
        return None

    def resource(self, path: Path, where: str) -> str:
        key = path.resolve()
        if key not in self.ids:
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ConversionError(
                    "image-unreadable", f"The image {path} for {where} cannot be read: {exc}"
                ) from exc
            data, media_type, _ = to_core_image(data, path.name, where)
            rid = f"placed{len(self.ids) + 1}"
            self.book.resources[rid] = Resource(rid, media_type, data)
            self.ids[key] = rid
        return self.ids[key]


def insert_images(
    book: Book, added: list[tuple[Path, str | None]], folders: list[Path], diag: Diagnostics
) -> None:
    """Replace placeholder paragraphs in `book` with the images they name."""
    images = _Images(book, added, folders)
    for section in book.sections():
        section.opener = _blocks(section.opener, section, images, diag)
        section.blocks = _blocks(section.blocks, section, images, diag)


def _blocks(
    blocks: list[Block], section: Section, images: _Images, diag: Diagnostics
) -> list[Block]:
    out: list[Block] = []
    for block in blocks:
        if not isinstance(block, Paragraph):
            out.append(block)
            continue
        text = plain_text(block.content)
        match = PLACEHOLDER.match(text)
        if match is None:
            if _ANYWHERE.search(text):
                diag.warn(
                    "image-placeholder-inline",
                    "An [image: ...] placeholder is inside a paragraph, so it was kept as text. "
                    "Put it on a line of its own.",
                    block.source,
                )
            out.append(block)
            continue
        name, description = match.group(1), match.group(2)
        where = f"the placeholder {text.strip()!r}" + (
            f" at {block.source}" if block.source else ""
        )
        found = images.find(name)
        if found is None:
            raise ConversionError(
                "image-placeholder-missing",
                f"No image named {name!r} for {where}. Add it on the Images tab, or put it in "
                "the manuscript's folder.",
            )
        path, added_description = found
        alt = (description or "").strip() or added_description
        if not alt:
            diag.warn(
                "image-description-missing",
                f"The image {name} has no description for readers who cannot see it. Add one "
                "after a | in the placeholder, or on the Images tab.",
                block.source,
            )
        rid = images.resource(path, where)
        diag.removed("image-placeholder", text.strip(), block.source)
        diag.info("image-placed", f"Placed {path.name} for {where}.", block.source)
        out.append(Image(rid, alt or None, source=block.source))
    return out
