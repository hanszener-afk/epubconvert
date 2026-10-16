"""PDF front end: produces the IR and nothing else."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.ir import Book, Metadata, Resource
from novel2epub.pdf.extract import extract
from novel2epub.pdf.hyphenation import Dehyphenator, english_words, word_counts
from novel2epub.pdf.inspect import inspect
from novel2epub.pdf.layout import build_blocks, measure
from novel2epub.pdf.structure import PdfStructurer, PlacedImage


@dataclass
class PdfOptions:
    chapter_regex: str | None = None


@dataclass
class PdfResult:
    book: Book
    source_date: datetime | None


def read_pdf(path: Path, options: PdfOptions, diag: Diagnostics) -> PdfResult:
    info = inspect(path)
    if info.page_count and len(info.scanned_pages) == info.page_count:
        raise ConversionError(
            "input-scanned",
            "This PDF contains only images of pages (a scan), with no text to extract. "
            "OCR is not available in this version.",
        )
    for page in info.scanned_pages:
        diag.warn(
            "page-image",
            f"Page {page} is an image with no text; it was kept as an image, but any words in it "
            "are not part of the text.",
        )

    pages = extract(path)
    unmapped = sum(p.unmapped for p in pages)
    if unmapped:
        diag.warn(
            "unmapped-glyphs",
            f"{unmapped} character(s) have no Unicode mapping in the PDF's fonts and may be wrong.",
        )
    metrics = measure(pages)
    dehyph = Dehyphenator(word_counts(ln.text for pg in pages for ln in pg.lines), english_words())
    blocks = build_blocks(pages, metrics, dehyph, diag)

    resources: dict[str, Resource] = {}
    placed: list[PlacedImage] = []
    for n, img in enumerate(info.images, start=1):
        rid = f"img{n}"
        resources[rid] = Resource(rid, "image/png", img.png)
        top = img.bbox[3]
        before = next(
            (i for i, b in enumerate(blocks)
             if b.page > img.page or (b.page == img.page and b.lines[0].y1 < top)),
            len(blocks),
        )  # fmt: skip
        placed.append(PlacedImage(rid, img.page, img.bbox, before))

    title = info.title or path.stem
    if not info.title:
        diag.warn(
            "title-from-filename",
            f"The PDF has no title; using the file name {title!r}. Set it with --title.",
        )
    front, body, back = PdfStructurer(
        blocks, metrics, info.outline, placed, diag, options.chapter_regex, title
    ).build()
    used = {
        img.resource_id
        for img in placed
        if img.bbox[2] - img.bbox[0] > 180 or img.bbox[3] - img.bbox[1] > 72
    }
    resources = {k: v for k, v in resources.items() if k in used}
    diag.warn(
        "language-default", "PDFs rarely declare a language; using 'en'. Set it with --language."
    )
    authors = [info.author] if info.author else []
    meta = Metadata(title=title, authors=authors, language="en")
    return PdfResult(Book(meta, body, front, back, {}, resources), info.modified)
