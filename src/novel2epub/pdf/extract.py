"""Text extraction with positions and fonts (pdfminer.six).

Produces, per page, the visual lines in top-to-bottom order. Each line keeps
styled spans (font, size, bold, italic) and its bounding box, which is all
the layout heuristics need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTAnno, LTChar, LTFigure, LTImage, LTTextContainer, LTTextLine
from pdfminer.pdfparser import PDFSyntaxError

from novel2epub.diagnostics import ConversionError

_ITALIC = re.compile(r"italic|oblique|[-,]it$|[-,]ital|slanted", re.IGNORECASE)
_BOLD = re.compile(r"bold|black|heavy|semibold|demibold|[-,]bd$", re.IGNORECASE)
_LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\ufb05": "st", "\ufb06": "st",
}  # fmt: skip
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def font_flags(fontname: str) -> tuple[bool, bool]:
    """(bold, italic) from a font name such as 'AAAAAA+Caladea-BoldItalic'."""
    name = fontname.split("+", 1)[-1]
    return bool(_BOLD.search(name)), bool(_ITALIC.search(name))


@dataclass
class Span:
    text: str
    font: str
    size: float
    bold: bool
    italic: bool


@dataclass
class Line:
    page: int  # one-based
    x0: float
    y0: float
    x1: float
    y1: float
    spans: list[Span] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def size(self) -> float:
        """Dominant font size, weighted by characters."""
        weights: dict[float, int] = {}
        for s in self.spans:
            if s.text.strip():
                weights[s.size] = weights.get(s.size, 0) + len(s.text.strip())
        return max(weights, key=weights.__getitem__) if weights else 0.0

    def all(self, flag: str) -> bool:
        spans = [s for s in self.spans if s.text.strip()]
        return bool(spans) and all(getattr(s, flag) for s in spans)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass
class PageImage:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Page:
    number: int
    width: float
    height: float
    lines: list[Line]
    images: list[PageImage]
    unmapped: int = 0  # glyphs with no Unicode mapping ("(cid:N)")


def _clean(text: str) -> str:
    for lig, plain in _LIGATURES.items():
        text = text.replace(lig, plain)
    return text.translate(_INVISIBLE)


def _line(obj: LTTextLine, page: int) -> tuple[Line, int]:
    line = Line(page, obj.x0, obj.y0, obj.x1, obj.y1)
    unmapped = 0
    current: Span | None = None
    for item in obj:
        if isinstance(item, LTChar):
            text = item.get_text()
            if text.startswith("(cid:"):
                unmapped += 1
            text = _clean(text)
            size = round(item.size, 1)
            key = (item.fontname, size)
            if current is None or (current.font, current.size) != key:
                bold, italic = font_flags(item.fontname)
                current = Span("", item.fontname, size, bold, italic)
                line.spans.append(current)
            current.text += text
        elif isinstance(item, LTAnno):
            text = item.get_text().replace("\n", "")
            if text and current is not None:
                current.text += text
    if line.spans:
        line.spans[-1].text = line.spans[-1].text.rstrip()
    return line, unmapped


def _walk(container: object, page: int, lines: list[Line], images: list[PageImage]) -> int:
    unmapped = 0
    for obj in container:  # type: ignore[attr-defined]
        if isinstance(obj, LTTextLine):
            line, n = _line(obj, page)
            unmapped += n
            if line.text.strip():
                lines.append(line)
        elif isinstance(obj, LTTextContainer):
            unmapped += _walk(obj, page, lines, images)
        elif isinstance(obj, LTImage):
            images.append(PageImage(obj.x0, obj.y0, obj.x1, obj.y1))
        elif isinstance(obj, LTFigure):
            unmapped += _walk(obj, page, lines, images)
    return unmapped


def _merge_same_baseline(lines: list[Line]) -> list[Line]:
    """Rejoin pieces of one visual line that the layout analysis split apart."""
    lines.sort(key=lambda ln: (-round(ln.y0, 0), ln.x0))
    merged: list[Line] = []
    for ln in lines:
        prev = merged[-1] if merged else None
        if prev is not None:
            overlap = min(prev.y1, ln.y1) - max(prev.y0, ln.y0)
            height = min(prev.y1 - prev.y0, ln.y1 - ln.y0)
            if height > 0 and overlap > 0.6 * height and ln.x0 >= prev.x1 - 1:
                gap = ln.x0 - prev.x1
                if gap > 0.15 * max(prev.size, ln.size) and not prev.text.endswith(" "):
                    prev.spans[-1].text += " "
                prev.spans.extend(ln.spans)
                prev.x1 = ln.x1
                prev.y0 = min(prev.y0, ln.y0)
                prev.y1 = max(prev.y1, ln.y1)
                continue
        merged.append(ln)
    merged.sort(key=lambda ln: (-ln.y1, ln.x0))
    return merged


def extract(path: Path) -> list[Page]:
    params = LAParams(line_overlap=0.5, char_margin=3.0, line_margin=0.3, word_margin=0.1,
                      boxes_flow=None, all_texts=True)  # fmt: skip
    pages: list[Page] = []
    try:
        for n, layout in enumerate(extract_pages(str(path), laparams=params), start=1):
            lines: list[Line] = []
            images: list[PageImage] = []
            unmapped = _walk(layout, n, lines, images)
            pages.append(
                Page(n, layout.width, layout.height, _merge_same_baseline(lines), images, unmapped)
            )
    except (PDFSyntaxError, ValueError, KeyError, TypeError) as exc:
        raise ConversionError(
            "input-invalid", f"{path.name} could not be read as a PDF: {exc}"
        ) from exc
    return pages
