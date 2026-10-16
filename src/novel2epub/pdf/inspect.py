"""PDF-level facts via PDFium: page classification, outline, metadata, images."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from novel2epub.diagnostics import ConversionError

SCANNED_AREA = 0.5  # an image covering half the page with no text: a scan
_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?")


@dataclass(frozen=True)
class OutlineEntry:
    level: int
    title: str
    page: int  # one-based


@dataclass
class PdfImage:
    page: int
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    png: bytes


@dataclass
class PdfInfo:
    page_count: int
    title: str
    author: str
    modified: datetime | None
    outline: list[OutlineEntry]
    scanned_pages: list[int] = field(default_factory=list)
    images: list[PdfImage] = field(default_factory=list)


def parse_pdf_date(raw: str) -> datetime | None:
    m = _DATE.match(raw.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, sign, oh, om = m.groups()
    try:
        when = datetime(int(y), int(mo or 1), int(d or 1), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None
    if sign in ("+", "-") and oh:
        offset = timedelta(hours=int(oh), minutes=int(om or 0))
        return when.replace(tzinfo=timezone(offset if sign == "+" else -offset))
    return when.replace(tzinfo=UTC)


def inspect(path: Path) -> PdfInfo:
    try:
        doc = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise ConversionError("input-encrypted", "The PDF is password protected.") from exc
        raise ConversionError(
            "input-invalid", f"{path.name} could not be read as a PDF: {exc}"
        ) from exc
    try:
        meta = doc.get_metadata_dict()
        outline = []
        for bm in doc.get_toc(max_depth=4):
            dest = bm.get_dest()
            index = dest.get_index() if dest is not None else None
            title = (bm.get_title() or "").strip()
            if index is not None and title:
                outline.append(OutlineEntry(bm.level, title, index + 1))
        info = PdfInfo(
            page_count=len(doc),
            title=(meta.get("Title") or "").strip(),
            author=(meta.get("Author") or "").strip(),
            modified=parse_pdf_date(meta.get("ModDate") or "")
            or parse_pdf_date(meta.get("CreationDate") or ""),
            outline=outline,
        )
        for n in range(len(doc)):
            page = doc[n]
            width, height = page.get_size()
            chars = page.get_textpage().count_chars()
            area = 0.0
            for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=4):
                x0, y0, x1, y1 = obj.get_bounds()
                area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
                info.images.append(PdfImage(n + 1, (x0, y0, x1, y1), _png(obj)))
            if chars == 0 and area >= SCANNED_AREA * width * height:
                info.scanned_pages.append(n + 1)
        return info
    finally:
        doc.close()


def _png(obj: pdfium.PdfImage) -> bytes:
    try:
        image = obj.get_bitmap(render=False).to_pil()
    except Exception:  # some encodings can only be rendered
        image = obj.get_bitmap(render=True).to_pil()
    if image.mode not in ("RGB", "RGBA", "L", "LA"):
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
