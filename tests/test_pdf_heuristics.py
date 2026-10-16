"""Unit tests for the milestone 4 PDF heuristics on synthetic pages."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from novel2epub.diagnostics import Diagnostics
from novel2epub.pdf.checks import check_layout
from novel2epub.pdf.extract import Line, Page, Span
from novel2epub.pdf.footnotes import extract_footnotes
from novel2epub.pdf.furniture import remove_furniture
from novel2epub.pdf.hyphenation import Dehyphenator
from novel2epub.pdf.layout import Layout, Metrics, build_blocks, measure
from novel2epub.pdf.structure import TOC_ENTRY

H, W, GAP = 792.0, 612.0, 15.0


def ln(page: int, y: float, text: str, x0: float = 72, x1: float = 540, size: float = 12.0,
       spans: list[Span] | None = None) -> Line:  # fmt: skip
    return Line(page, x0, y - size, x1, y, spans or [Span(text, "Serif", size, False, False)])


def body_lines(page: int, n: int = 30, top: float = 700) -> list[Line]:
    return [
        ln(page, top - k * GAP, f"body text line {k} on page {page} with words") for k in range(n)
    ]


def book_pages(
    count: int,
    header: Callable[[int], str] | None = None,
    footer: Callable[[int], str] | None = None,
) -> list[Page]:
    pages = []
    for n in range(1, count + 1):
        lines = body_lines(n)
        if header:
            lines.insert(0, ln(n, 750, header(n), 250, 360, 10))
        if footer:
            lines.append(ln(n, 40, footer(n), 300, 312, 10))
        pages.append(Page(n, W, H, lines, []))
    return pages


# --- running headers, footers, page numbers ------------------------------


def test_page_numbers_with_offset_are_removed() -> None:
    pages = book_pages(8, footer=lambda n: str(n + 4))  # printed numbering starts at 5
    diag = Diagnostics()
    remove_furniture(pages, GAP, diag)
    assert [r.kind for r in diag.removals] == ["page-number"] * 8
    assert all(len(p.lines) == 30 for p in pages)


def test_alternating_running_heads_are_removed() -> None:
    pages = book_pages(8, header=lambda n: "The Book" if n % 2 == 0 else "Chapter One")
    diag = Diagnostics()
    remove_furniture(pages, GAP, diag)
    assert sorted({r.text for r in diag.removals}) == ["Chapter One", "The Book"]
    assert sum(r.kind == "running-header" for r in diag.removals) == 8


def test_one_off_header_at_established_height_is_removed() -> None:
    titles = ["Long Chapter"] * 6 + ["Short Chapter"]
    pages = book_pages(7, header=lambda n: titles[n - 1])
    diag = Diagnostics()
    remove_furniture(pages, GAP, diag)
    assert "Short Chapter" in {r.text for r in diag.removals}


def test_scene_break_at_page_foot_is_not_furniture() -> None:
    pages = book_pages(6)
    for pg in pages:
        pg.lines.append(ln(pg.number, 80, "* * *", 290, 322))
    diag = Diagnostics()
    remove_furniture(pages, GAP, diag)
    assert not diag.removals


def test_repeated_body_text_is_not_furniture() -> None:
    pages = book_pages(6)
    for pg in pages:  # identical first lines, but not separated from the body
        pg.lines[0] = ln(pg.number, 700, "The same opening words on every page")
    diag = Diagnostics()
    remove_furniture(pages, GAP, diag)
    assert not diag.removals


# --- footnotes -----------------------------------------------------------


def _marker_line(page: int, y: float, before: str, marker: str, after: str = "") -> Line:
    spans = [Span(before, "Serif", 12, False, False), Span(marker, "Serif", 7, False, False, True)]
    if after:
        spans.append(Span(after, "Serif", 12, False, False))
    return Line(page, 72, y - 12, 540, y, spans)


def test_footnote_linked_to_marker_and_removed_from_page() -> None:
    lines = body_lines(1, 20)
    lines[5] = _marker_line(1, lines[5].y1, "a sentence with a note.", "1", " And more.")
    note = Line(
        1,
        72,
        88,
        540,
        100,
        [
            Span("1", "Serif", 7, False, False, True),
            Span(" The note text.", "Serif", 10, False, False),
        ],
    )
    page = Page(1, W, H, [*lines, note], [], rules=[(72, 105, 189)])
    diag = Diagnostics()
    markers, notes = extract_footnotes([page], 12, GAP, diag)
    assert markers == {(1, "1"): notes[0].id}
    assert notes[0].lines[0].text == "The note text."
    assert note not in page.lines
    assert [r.kind for r in diag.removals] == ["note-mark", "footnote"]


def test_unmatched_footnote_like_text_stays_in_the_text() -> None:
    lines = body_lines(1, 20)
    stray = Line(
        1,
        72,
        88,
        540,
        100,
        [Span("2", "Serif", 7, False, False, True), Span(" Orphan.", "Serif", 10, False, False)],
    )
    page = Page(1, W, H, [*lines, stray], [], rules=[(72, 105, 189)])
    diag = Diagnostics()
    _, notes = extract_footnotes([page], 12, GAP, diag)
    assert not notes and stray in page.lines
    assert any(i.code == "footnote-unmatched" for i in diag.warnings)


def test_note_reference_replaces_the_marker_in_body_text() -> None:
    page = Page(1, W, H, [_marker_line(1, 700, "Text.", "1", " More text that runs on.")], [])
    metrics = Metrics(12, 72, 540, GAP, 18, True)
    diag = Diagnostics()
    blocks = build_blocks([page], metrics, Dehyphenator(Counter()), diag, {(1, "1"): "n1"})
    kinds = [s.kind for s in blocks[0].segments]
    assert "noteref" in kinds
    assert blocks[0].text == "Text. More text that runs on."


# --- drop caps -----------------------------------------------------------


def _drop_cap_page(cap: str, first: str) -> Page:
    cap_line = Line(1, 72, 643, 107, 706, [Span(cap, "Serif", 63, False, False)])
    beside = [
        ln(1, 698, first, 107),
        ln(1, 682, "continues beside the cap", 107),
        ln(1, 666, "and once more", 107),
    ]
    rest = [ln(1, 650 - k * GAP, "the paragraph carries on at the full measure") for k in range(10)]
    return Page(1, W, H, [cap_line, *beside, *rest], [])


def test_drop_cap_joins_its_word() -> None:
    page = _drop_cap_page("S", "ir Walter Elliot")
    diag = Diagnostics()
    layout = Layout(
        Metrics(12, 72, 540, GAP, 18, True), Dehyphenator(Counter(), frozenset({"sir"})), diag
    )
    blocks = layout.page_blocks(page)
    assert blocks[0].text.startswith(
        "Sir Walter Elliot continues beside the cap and once more the paragraph"
    )
    rewrites = [r for r in diag.removals if r.replacement]
    assert rewrites and rewrites[0].text == "ir" and rewrites[0].replacement == "Sir"


def test_quotation_mark_drop_cap() -> None:
    page = _drop_cap_page("\u201c", "I must take leave")
    diag = Diagnostics()
    layout = Layout(Metrics(12, 72, 540, GAP, 18, True), Dehyphenator(Counter()), diag)
    assert layout.page_blocks(page)[0].text.startswith("\u201cI must take leave")


def test_single_letter_word_drop_cap_keeps_its_space() -> None:
    page = _drop_cap_page("I", "was born in a village")
    diag = Diagnostics()
    layout = Layout(
        Metrics(12, 72, 540, GAP, 18, True), Dehyphenator(Counter(), frozenset({"was"})), diag
    )
    assert layout.page_blocks(page)[0].text.startswith("I was born in a village")


# --- layouts not converted -----------------------------------------------


def test_two_column_page_is_reported() -> None:
    left = [ln(1, 700 - k * GAP, "left column text", 72, 290) for k in range(20)]
    right = [ln(1, 700 - k * GAP, "right column text", 322, 540) for k in range(20)]
    pages = [Page(1, W, H, [*left, *right], [])]
    diag = Diagnostics()
    check_layout(pages, measure(book_pages(3)), diag)
    assert any(i.code == "multi-column" for i in diag.warnings)


def test_single_column_page_is_not_reported() -> None:
    pages = book_pages(3)
    diag = Diagnostics()
    check_layout(pages, measure(pages), diag)
    assert not diag.warnings


# --- printed contents ----------------------------------------------------


def test_contents_entries() -> None:
    for text in (
        "Sir Walter ........ 3",
        "The Lawyer\u2026\u2026 12",
        "Chapter One   7",
        "Preface  xi",
    ):
        assert TOC_ENTRY.match(text), text
    for text in ("He was 3", "It was 1810", "A sentence that ends with a year 1810."):
        assert not TOC_ENTRY.match(text), text


# --- centred text --------------------------------------------------------


def test_wide_centred_title_lines_form_one_block() -> None:
    m = Metrics(10.5, 54, 378, 15.2, 15, True)
    lines = [
        ln(1, 413, "I Will Not Be Buying", 71, 361, size=32),
        ln(1, 375, "the Catgirl", 140, 292, size=32),
        ln(1, 326, "A novel in ten parts, with a prologue and an epilogue.", 82, 350, size=12.5),
    ]
    blocks = Layout(m, Dehyphenator(Counter()), Diagnostics()).page_blocks(
        Page(1, 432, 648, lines, [])
    )
    assert [b.centred for b in blocks] == [True, True]
    assert [s.kind for s in blocks[0].segments].count("break") == 1


def test_short_paragraph_at_the_indent_is_not_centred() -> None:
    m = Metrics(10.5, 54, 378, 15.2, 15, True)
    layout = Layout(m, Dehyphenator(Counter()), Diagnostics())
    # Starts exactly at the first-line indent and happens to end near the middle.
    assert not layout.centred(ln(1, 500, "\u201cWell,\u201d said James Henry.", 69, 363, size=10.5))
