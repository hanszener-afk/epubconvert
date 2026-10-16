"""Find footnotes at the foot of PDF pages and link them to their markers.

A footnote area starts at a line in the lower half of the page that begins
with a note marker (a raised or smaller digit or symbol), below a short
separator rule or a gap. A note is only taken out of the text when the same
marker appears raised in the body text above it on that page; otherwise the
lines stay where they are, in the text, with a warning. Lines of a note that
continues from the previous page (below a rule, no marker) join that note.

Every line taken out is logged as a removal, so the main-text fidelity check
accounts for it, and the same logged lines form the reference for the notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from novel2epub.diagnostics import Diagnostics
from novel2epub.ir import PdfRef
from novel2epub.pdf.extract import Line, Page, Span

_MARKER = re.compile(r"^(\d{1,3}|[*\u2020\u2021\u00a7\u00b6]{1,3}|[a-z])$")


@dataclass
class PdfNote:
    id: str
    page: int
    marker: str
    lines: list[Line] = field(default_factory=list)


def _ref(ln: Line) -> PdfRef:
    return PdfRef(ln.page, (round(ln.x0, 1), round(ln.y0, 1), round(ln.x1, 1), round(ln.y1, 1)))


def _leading_marker(ln: Line, body_size: float) -> str | None:
    spans = [s for s in ln.spans if s.text.strip()]
    if not spans:
        return None
    first = spans[0]
    text = first.text.strip()
    small = first.superscript or first.size < 0.8 * max(ln.size, body_size)
    if small and _MARKER.match(text) and len(spans) > 1:
        return text
    return None


def _body_markers(lines: list[Line]) -> set[str]:
    return {
        s.text.strip()
        for ln in lines
        for s in ln.spans
        if s.superscript and _MARKER.match(s.text.strip())
    }


def _area_start(pg: Page, body_size: float, line_gap: float) -> tuple[int | None, bool]:
    """Index of the first footnote line and whether a separator rule marks the area."""
    lines = pg.lines
    rule_y = max(
        (y for x0, y, x1 in pg.rules if x1 - x0 < 0.6 * pg.width and y < pg.height / 2),
        default=None,
    )
    for i, ln in enumerate(lines):
        if ln.y1 > pg.height / 2 or i == 0:
            continue
        above = lines[i - 1]
        ruled = rule_y is not None and ln.y1 < rule_y < above.y0
        if rule_y is not None and ruled:
            return i, True
        if _leading_marker(ln, body_size) and (above.y1 - ln.y1) > 1.3 * line_gap:
            return i, False
    return None, False


def extract_footnotes(
    pages: list[Page], body_size: float, line_gap: float, diag: Diagnostics
) -> tuple[dict[tuple[int, str], str], list[PdfNote]]:
    """Remove footnote lines from `pages`; return marker -> note id, and the notes."""
    markers: dict[tuple[int, str], str] = {}
    notes: list[PdfNote] = []
    for pg in pages:
        start, ruled = _area_start(pg, body_size, line_gap)
        if start is None:
            continue
        body, area = pg.lines[:start], pg.lines[start:]
        wanted = _body_markers(body)
        taken: list[Line] = []
        current: PdfNote | None = None
        for k, ln in enumerate(area):
            marker = _leading_marker(ln, body_size)
            if marker is not None and marker in wanted and (pg.number, marker) not in markers:
                current = PdfNote(f"p{pg.number}n{len(notes) + 1}", pg.number, marker)
                notes.append(current)
                markers[(pg.number, marker)] = current.id
                stripped = _without_marker(ln)
                diag.removed("note-mark", marker, _ref(ln))
                diag.removed("footnote", stripped.text, _ref(ln))
                current.lines.append(stripped)
                taken.append(ln)
            elif marker is None and current is not None:
                diag.removed("footnote", ln.text, _ref(ln))
                current.lines.append(ln)
                taken.append(ln)
            elif marker is None and k == 0 and ruled and notes and notes[-1].page == pg.number - 1:
                current = notes[-1]  # a note continued from the previous page
                diag.removed("footnote", ln.text, _ref(ln))
                current.lines.append(ln)
                taken.append(ln)
                diag.decided(
                    "footnote",
                    "A footnote continued from the previous page.",
                    0.8,
                    "continuation",
                    _ref(ln),
                )
            else:
                current = None
                diag.warn(
                    "footnote-unmatched",
                    f"Text at the foot of page {pg.number} looks like a footnote but no matching "
                    "marker was found; it was kept in the text where it stands.",
                    _ref(ln),
                )
        pg.lines = body + [ln for ln in area if ln not in taken]
    for note in notes:
        diag.decided(
            "footnote",
            f"Footnote {note.marker} on page {note.page}.",
            0.85 if note.lines else 0.5,
            "marker",
            _ref(note.lines[0]) if note.lines else None,
        )
    return markers, notes


def _without_marker(ln: Line) -> Line:
    spans = list(ln.spans)
    for i, s in enumerate(spans):
        if s.text.strip():
            spans = spans[:i] + spans[i + 1 :]
            break
    if spans:
        spans[0] = Span(
            spans[0].text.lstrip(), spans[0].font, spans[0].size, spans[0].bold, spans[0].italic
        )
    return Line(ln.page, ln.x0, ln.y0, ln.x1, ln.y1, spans)
