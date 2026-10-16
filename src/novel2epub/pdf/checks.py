"""Layouts v1 does not convert: detected and reported, never silently mangled."""

from __future__ import annotations

from novel2epub.diagnostics import Diagnostics
from novel2epub.pdf.extract import Page
from novel2epub.pdf.layout import Metrics


def _ranges(pages: list[int]) -> str:
    out: list[str] = []
    for n in pages:
        if out and out[-1].split("-")[-1] == str(n - 1):
            out[-1] = f"{out[-1].split('-')[0]}-{n}"
        else:
            out.append(str(n))
    return ", ".join(out)


def check_layout(pages: list[Page], m: Metrics, diag: Diagnostics) -> None:
    columns: list[int] = []
    tables: list[int] = []
    width = m.right - m.left
    for pg in pages:
        body = [ln for ln in pg.lines if abs(ln.size - m.body_size) < 0.5]
        narrow = [ln for ln in body if ln.x1 - ln.x0 < 0.55 * width]
        left = [ln for ln in narrow if ln.x1 < m.centre]
        right = [ln for ln in narrow if ln.x0 > m.centre]
        if len(left) >= 5 and len(right) >= 5:
            overlap = min(max(ln.y1 for ln in left), max(ln.y1 for ln in right)) - max(
                min(ln.y0 for ln in left), min(ln.y0 for ln in right)
            )
            if overlap > 5 * m.line_gap:
                columns.append(pg.number)
        if sum(1 for ln in pg.lines if ln.wide_gaps >= 1) >= 3:
            tables.append(pg.number)
    if columns:
        diag.warn(
            "multi-column",
            f"Page(s) {_ranges(columns)} appear to be set in columns, which this version does not "
            "convert; their text is kept, but the reading order may be wrong. Check them.",
        )
    if tables:
        diag.warn(
            "table",
            f"Page(s) {_ranges(tables)} appear to contain a table, which this version does not "
            "convert; the cells are kept as text, row by row. Check them.",
        )
