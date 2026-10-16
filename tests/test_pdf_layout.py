"""Unit tests for PDF layout heuristics on synthetic lines."""

from __future__ import annotations

from collections import Counter

from hypothesis import given
from hypothesis import strategies as st

from novel2epub.diagnostics import Diagnostics
from novel2epub.pdf.extract import Line, Page, Span
from novel2epub.pdf.hyphenation import Dehyphenator, word_counts
from novel2epub.pdf.layout import build_blocks, measure

LEFT, RIGHT, SIZE, GAP = 72.0, 540.0, 12.0, 15.0


def line(page: int, y: float, text: str, x0: float = LEFT, x1: float | None = None,
         size: float = SIZE, italic: bool = False, font: str = "Times-Roman") -> Line:  # fmt: skip
    if x1 is None:
        x1 = RIGHT
    return Line(page, x0, y - size, x1, y, [Span(text, font, size, False, italic)])


def page(n: int, *lines: Line) -> Page:
    return Page(n, 612, 792, list(lines), [])


def body(page_no: int, texts: list[tuple[str, str]], top: float = 700) -> list[Line]:
    """texts: (kind, text) where kind is 'full', 'indent', or 'short'."""
    out = []
    y = top
    for kind, text in texts:
        x0 = LEFT + 18 if kind == "indent" else LEFT
        x1 = 300 if kind == "short" else RIGHT
        out.append(line(page_no, y, text, x0, x1))
        y -= GAP
    return out


FILLER = [("full", "filler words to establish the body metrics of the page")] * 12


def blocks_of(*pages: Page, counts: Counter[str] | None = None):  # type: ignore[no-untyped-def]
    diag = Diagnostics()
    metrics = measure(list(pages))
    dehyph = Dehyphenator(counts or Counter())
    return build_blocks(list(pages), metrics, dehyph, diag), diag


# --- metrics -------------------------------------------------------------


def test_measure_finds_body_metrics() -> None:
    pg = page(1, *body(1, [("indent", "a"), *FILLER, ("short", "end.")]))
    m = measure([pg])
    assert m.body_size == SIZE and m.left == LEFT and m.right == RIGHT
    assert abs(m.line_gap - GAP) < 0.5
    assert m.justified


# --- paragraphs ----------------------------------------------------------


def test_indent_and_short_line_split_paragraphs() -> None:
    lines = body(1, [
        ("indent", "First paragraph begins here and runs"),
        ("full", "on to the right margin before it"),
        ("short", "ends."),
        ("indent", "Second paragraph."),
        *FILLER,
    ])  # fmt: skip
    blocks, _ = blocks_of(page(1, *lines))
    assert (
        blocks[0].text
        == "First paragraph begins here and runs on to the right margin before it ends."
    )
    assert blocks[1].text.startswith("Second paragraph.")


def test_vertical_gap_splits_and_is_recorded() -> None:
    lines = [
        *body(1, FILLER[:3]),
        *body(1, [("full", "after a gap")], top=700 - 3 * GAP - 2.5 * GAP),
    ]
    blocks, _ = blocks_of(page(1, *lines))
    assert len(blocks) == 2
    assert blocks[1].gap_before is not None and blocks[1].gap_before > 2


def test_paragraph_continues_across_pages() -> None:
    p1 = page(
        1,
        *body(
            1,
            [
                *FILLER,
                ("indent", "This sentence runs to the end of the"),
                ("full", "page and does not"),
            ],
        ),
    )
    p2 = page(
        2,
        *body(
            2,
            [("full", "stop there."), ("short", "Really."), ("indent", "New paragraph."), *FILLER],
        ),
    )
    blocks, _ = blocks_of(p1, p2)
    texts = [b.text for b in blocks]
    assert any(t.endswith("page and does not stop there. Really.") for t in texts)


def test_indented_first_line_on_next_page_is_a_new_paragraph() -> None:
    p1 = page(1, *body(1, [*FILLER, ("full", "a line that fills the measure exactly")]))
    more = [("indent", "Another paragraph."), *FILLER[:2], ("indent", "And another."), *FILLER[:2]]
    p2 = page(2, *body(2, [("indent", "A new paragraph."), *FILLER[:2], *more]))
    blocks, _ = blocks_of(p1, p2)
    assert any(b.text.startswith("A new paragraph.") for b in blocks)


def test_short_last_line_does_not_continue_onto_next_page() -> None:
    p1 = page(1, *body(1, [*FILLER, ("short", "The end.")]))
    p2 = page(2, *body(2, [("full", "Unindented start."), *FILLER]))
    blocks, _ = blocks_of(p1, p2)
    assert any(b.text.startswith("Unindented start.") for b in blocks)


def test_italic_spans_survive_line_joins() -> None:
    lines = [
        line(1, 700, "plain then", LEFT + 18),
        Line(
            1,
            LEFT,
            700 - GAP - SIZE,
            300,
            700 - GAP,
            [
                Span("slanted", "Times-Italic", SIZE, False, True),
                Span(" end.", "Times-Roman", SIZE, False, False),
            ],
        ),
        *body(1, FILLER, top=700 - 2 * GAP),
    ]
    blocks, _ = blocks_of(page(1, *lines))
    segs = blocks[0].segments
    assert [s.text for s in segs if "italic" in s.fmt] == ["slanted"]
    assert blocks[0].text == "plain then slanted end."


def test_line_end_hyphen_is_joined_and_logged() -> None:
    lines = body(1, [("indent", "the local mi-"), ("short", "litia came."), *FILLER])
    blocks, diag = blocks_of(page(1, *lines))
    assert blocks[0].text == "the local militia came."
    assert any(d.kind == "dehyphenation" for d in diag.decisions)


def test_document_evidence_keeps_compound_hyphen() -> None:
    lines = body(1, [("indent", "a well-"), ("short", "known face."), *FILLER])
    blocks, _ = blocks_of(page(1, *lines), counts=Counter({"well-known": 3}))
    assert blocks[0].text == "a well-known face."


def test_em_dash_at_line_end_joins_without_space() -> None:
    lines = body(1, [("indent", "she said\u2014"), ("short", "and stopped."), *FILLER])
    blocks, _ = blocks_of(page(1, *lines))
    assert blocks[0].text == "she said\u2014and stopped."


def test_centred_marker_is_its_own_block() -> None:
    lines = [
        *body(1, FILLER[:4]),
        line(1, 700 - 4 * GAP, "* * *", 290, 322),
        *body(1, FILLER[:4], top=700 - 5 * GAP),
    ]
    blocks, _ = blocks_of(page(1, *lines))
    marker = [b for b in blocks if b.text == "* * *"]
    assert len(marker) == 1 and marker[0].centred


def test_indented_smaller_text_is_a_quote() -> None:
    quote = [
        line(1, 700 - k * 12, f"quoted line {k}", LEFT + 22, RIGHT - 22, size=9.5) for k in range(3)
    ]
    lines = [*quote, *body(1, FILLER, top=700 - 3 * 12 - GAP)]
    blocks, _ = blocks_of(page(1, *lines))
    assert blocks[0].quote and blocks[0].text.startswith("quoted line 0 quoted line 1")
    assert not blocks[-1].quote


# --- dehyphenation -------------------------------------------------------


def test_dehyphenation_rules() -> None:
    d = Dehyphenator(Counter({"militia": 2, "co-operate": 1, "nonsense": 1}))
    assert d.decide("mi", "litia", "-").join
    assert not d.decide("co", "operate", "-").join  # document evidence
    assert not d.decide("re", "enter", "-").join  # doubled vowel after a prefix
    assert not d.decide("self", "evident", "-").join  # compound prefix
    assert not d.decide("anti", "American", "-").join  # capital after the break
    assert d.decide("non", "sense", "-").join  # evidence beats the prefix rule
    assert d.decide("hyphen", "ation", "\u00ad").join  # soft hyphen is always a break
    assert d.decide("hyphen", "ation", "\u00ad").confidence == 1.0


def test_word_counts_ignore_line_end_fragments() -> None:
    counts = word_counts(["The well-known man said, \u201cMilitia!\u201d and mi-", "litia came."])
    assert counts["well-known"] == 1 and counts["militia"] == 1
    assert counts["mi-"] == 0


@given(
    st.text(alphabet="abcdefghij", min_size=1, max_size=8),
    st.text(alphabet="abcdefghijABC", min_size=1, max_size=8),
    st.sampled_from(["-", "\u2010", "\u00ad"]),
)
def test_dehyphenation_never_loses_or_invents_characters(
    left: str, right: str, hyphen: str
) -> None:
    result = Dehyphenator(Counter()).decide(left, right, hyphen)
    assert result.text in (left + right, left + "-" + right)
    assert 0 < result.confidence <= 1
