"""Detect and remove running headers, running footers, and page numbers.

Only lines in the top or bottom band of a page are candidates, and at most
two per band per page. A candidate is furniture when:
- it is a page number: its value runs in step with the page number (a
  constant offset) on enough pages; or
- its text, with digits masked, repeats on at least three pages in the same
  band (this covers alternating odd and even headers, which are simply two
  repeating texts); or
- it sits at a height where repeating furniture was found, at the same size,
  separated from the body by more than a line space (headers of short
  chapters that repeat only once or twice).
Every removal is logged with its text and position.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from novel2epub.diagnostics import Diagnostics
from novel2epub.ir import PdfRef
from novel2epub.normalise.scenebreaks import scene_break_confidence
from novel2epub.pdf.extract import Line, Page

BAND = 0.15  # top and bottom 15% of the page
MAX_LEN = 100
MAX_PER_BAND = 2
_NUMBER = re.compile(r"^\W*(\d{1,4}|[ivxlcdm]{1,7})\W*$", re.IGNORECASE)
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman(text: str) -> int | None:
    total, prev = 0, 0
    for ch in reversed(text.lower()):
        value = _ROMAN.get(ch)
        if value is None:
            return None
        total += -value if value < prev else value
        prev = max(prev, value)
    return total


def _value(text: str) -> int | None:
    m = _NUMBER.match(text.strip())
    if not m:
        return None
    token = m.group(1)
    return int(token) if token.isdigit() else _roman(token)


def _key(text: str) -> str:
    masked = re.sub(r"\d+", "#", text.lower())
    masked = re.sub(r"\b[ivxlcdm]{1,7}\b", "#", masked)
    return re.sub(r"\s+", " ", masked).strip()


@dataclass
class _Candidate:
    page: Page
    line: Line
    band: str  # "top" or "bottom"


def _candidates(pages: list[Page]) -> list[_Candidate]:
    out = []
    for pg in pages:
        if len(pg.lines) < 2:
            continue
        top = [ln for ln in pg.lines if ln.y1 >= pg.height * (1 - BAND)][:MAX_PER_BAND]
        bottom = [ln for ln in pg.lines if ln.y0 <= pg.height * BAND][-MAX_PER_BAND:]
        for band, lines in (("top", top), ("bottom", bottom)):
            for ln in lines:
                text = ln.text.strip()
                # A scene-break marker at the foot of a page is content, not furniture.
                if len(text) <= MAX_LEN and not scene_break_confidence(text, False):
                    out.append(_Candidate(pg, ln, band))
    return out


def _separated(c: _Candidate, line_gap: float) -> bool:
    """True if the line stands apart from its neighbour in the body."""
    lines = c.page.lines
    i = lines.index(c.line)
    neighbour = lines[i + 1] if c.band == "top" else lines[i - 1] if i else None
    if c.band == "top" and i + 1 >= len(lines):
        return True
    if neighbour is None:
        return True
    gap = (c.line.y1 - neighbour.y1) if c.band == "top" else (neighbour.y1 - c.line.y1)
    return gap > 1.3 * line_gap


def remove_furniture(pages: list[Page], line_gap: float, diag: Diagnostics) -> None:
    """Remove page furniture from `pages` in place, logging every removal."""
    candidates = _candidates(pages)
    furniture: dict[int, str] = {}  # id(line) -> kind

    # Page numbers: a constant offset between printed and physical numbers.
    offsets: dict[str, Counter[int]] = defaultdict(Counter)
    numeric = []
    for c in candidates:
        value = _value(c.line.text)
        if value is not None:
            numeric.append((c, value))
            offsets[c.band][value - c.page.number] += 1
    for c, value in numeric:
        counts = offsets[c.band]
        offset, hits = counts.most_common(1)[0]
        if hits >= 3 and value - c.page.number == offset:
            furniture[id(c.line)] = "page-number"

    # Repeating texts (digits masked) in the same band, at a consistent height.
    found_at: dict[tuple[str, str], list[_Candidate]] = defaultdict(list)
    for c in candidates:
        if id(c.line) not in furniture and _key(c.line.text):
            found_at[(c.band, _key(c.line.text))].append(c)
    texts: dict[tuple[str, str], set[int]] = {}
    for key, group in found_at.items():
        usual = Counter(round(c.line.y1) for c in group).most_common(1)[0][0]
        texts[key] = {c.page.number for c in group if abs(c.line.y1 - usual) <= 3}
    heights: dict[str, Counter[tuple[int, float]]] = defaultdict(Counter)
    for c in candidates:
        if id(c.line) in furniture:
            continue
        pages_with = texts.get((c.band, _key(c.line.text)), set())
        if c.page.number in pages_with and len(pages_with) >= 3 and _separated(c, line_gap):
            kind = "running-header" if c.band == "top" else "running-footer"
            furniture[id(c.line)] = kind
            heights[c.band][(round(c.line.y1), c.line.size)] += 1

    # One-off headers at an established furniture height and size.
    for c in candidates:
        if id(c.line) in furniture:
            continue
        height, size_here = round(c.line.y1), c.line.size
        near = any(
            abs(y - height) <= 2 and abs(size - size_here) < 0.2 and n >= 3
            for (y, size), n in heights[c.band].items()
        )
        if near and _separated(c, line_gap):
            furniture[id(c.line)] = "running-header" if c.band == "top" else "running-footer"

    removed: Counter[str] = Counter()
    for pg in pages:
        kept: list[Line] = []
        for ln in pg.lines:
            found = furniture.get(id(ln))
            if found is None:
                kept.append(ln)
                continue
            removed[found] += 1
            bbox = (round(ln.x0, 1), round(ln.y0, 1), round(ln.x1, 1), round(ln.y1, 1))
            diag.removed(found, ln.text, PdfRef(pg.number, bbox))
        pg.lines = kept
    for kind, n in sorted(removed.items()):
        diag.decided(
            "page-furniture",
            f"Removed {n} {kind.replace('-', ' ')}(s) repeating at the page edge.",
            0.9,
            kind,
        )
