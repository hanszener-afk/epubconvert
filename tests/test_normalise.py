from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from novel2epub.diagnostics import Diagnostics
from novel2epub.ir import (
    Book,
    Italic,
    LineBreak,
    Metadata,
    Paragraph,
    SceneBreak,
    Section,
    SectionKind,
    Text,
)
from novel2epub.normalise.htmltags import Segment, interpret_tags
from novel2epub.normalise.passes import normalise_book
from novel2epub.normalise.scenebreaks import scene_break_confidence
from novel2epub.normalise.smarten import smarten_text


def book_of(*blocks: object) -> Book:
    return Book(
        Metadata("T", ["A"], "en-GB"),
        body=[Section(SectionKind.CHAPTER, None, list(blocks))],  # type: ignore[arg-type]
    )


def flat(p: object) -> str:
    assert isinstance(p, Paragraph)
    out = []

    def walk(nodes: list) -> None:  # type: ignore[type-arg]
        for n in nodes:
            if isinstance(n, Text):
                out.append(n.text)
            elif isinstance(n, LineBreak):
                out.append("\n")
            elif hasattr(n, "children"):
                walk(n.children)

    walk(p.content)
    return "".join(out)


# --- whitespace ----------------------------------------------------------


def test_whitespace_collapsed_and_trimmed() -> None:
    b = book_of(Paragraph([Text("\t  Hello   world  "), Italic([Text(" there ")]), Text("  ")]))
    diag = Diagnostics()
    normalise_book(b, diag)
    assert flat(b.body[0].blocks[0]) == "Hello world there"


def test_nbsp_is_preserved() -> None:
    b = book_of(Paragraph([Text("Mr.\u00a0Smith")]))
    normalise_book(b, Diagnostics())
    assert flat(b.body[0].blocks[0]) == "Mr.\u00a0Smith"


def test_spaces_around_line_breaks_trimmed() -> None:
    b = book_of(Paragraph([Text("one  "), LineBreak(), Text("  two")]))
    normalise_book(b, Diagnostics())
    assert flat(b.body[0].blocks[0]) == "one\ntwo"


@given(st.lists(st.text(alphabet="ab \t\u00a0", max_size=8), min_size=1, max_size=6))
def test_whitespace_pass_never_changes_visible_text(parts: list[str]) -> None:
    b = book_of(Paragraph([Text(x) for x in parts]))
    normalise_book(b, Diagnostics())
    out = "".join(parts)
    blocks = b.body[0].blocks if b.body else []
    result = flat(blocks[0]) if blocks else ""
    visible = lambda s: [c for c in s if c not in " \t"]  # noqa: E731
    assert visible(result) == visible(out)
    assert "  " not in result and not result.startswith(" ") and not result.endswith(" ")


def test_empty_paragraphs_removed_and_logged() -> None:
    b = book_of(Paragraph([Text("a")]), Paragraph([Text("  ")]), Paragraph([Text("b")]))
    diag = Diagnostics()
    normalise_book(b, diag)
    assert len(b.body[0].blocks) == 2
    assert [rm.kind for rm in diag.removals] == ["empty-paragraph"]


def test_redundant_scene_breaks_removed() -> None:
    b = book_of(
        SceneBreak(),
        Paragraph([Text("a")]),
        SceneBreak(),
        SceneBreak(),
        Paragraph([Text("b")]),
        SceneBreak(),
    )
    diag = Diagnostics()
    normalise_book(b, diag)
    kinds = [type(x).__name__ for x in b.body[0].blocks]
    assert kinds == ["Paragraph", "SceneBreak", "Paragraph"]


def test_adjacent_text_merged() -> None:
    b = book_of(Paragraph([Text("a"), Text("b"), Italic([Text("c")]), Italic([Text("d")])]))
    normalise_book(b, Diagnostics())
    content = b.body[0].blocks[0].content  # type: ignore[union-attr]
    assert len(content) == 2
    assert isinstance(content[1], Italic) and content[1].children[0].text == "cd"  # type: ignore[union-attr]


# --- scene-break text patterns -------------------------------------------


def test_scene_break_patterns() -> None:
    for marker in ("***", "* * *", "#", "~", "* * * * *", "\u2042", "\u2766", "___________", "- - -",
                   "\u2022 \u2022 \u2022", "\u00a7"):  # fmt: skip
        assert scene_break_confidence(marker, centred=False) >= 0.8, marker
    for not_marker in ("\u2026", "...", "\u2014", "\u201c\u2026\u201d", "OK", "I", "1", "-", ""):
        assert scene_break_confidence(not_marker, centred=True) == 0.0, not_marker


def test_centring_raises_confidence() -> None:
    assert scene_break_confidence("***", centred=True) > scene_break_confidence(
        "***", centred=False
    )


# --- literal HTML tags ---------------------------------------------------


def seg(text: str, *fmt: str) -> Segment:
    return Segment(text, frozenset(fmt))


def test_tags_become_formatting() -> None:
    diag = Diagnostics()
    out = interpret_tags([seg("a <i>b</i> <strong>c</strong>")], diag, None)
    assert [(s.text, sorted(s.fmt)) for s in out if s.text] == [
        ("a ", []), ("b", ["italic"]), (" ", []), ("c", ["bold"]),
    ]  # fmt: skip
    assert [rm.text for rm in diag.removals] == ["<i>", "</i>", "<strong>", "</strong>"]


def test_tags_across_segments_and_existing_formatting() -> None:
    out = interpret_tags([seg("x <em>y", "bold"), seg(" z</em>")], Diagnostics(), None)
    assert [(s.text, sorted(s.fmt)) for s in out if s.text] == [
        ("x ", ["bold"]), ("y", ["bold", "italic"]), (" z", ["italic"]),
    ]  # fmt: skip


def test_unbalanced_and_unknown_tags_stay_literal() -> None:
    diag = Diagnostics()
    out = interpret_tags([seg("<i>open and <u>under</u>")], diag, None)
    assert "".join(s.text for s in out) == "<i>open and <u>under</u>"
    assert {i.code for i in diag.warnings} == {"html-tag-unbalanced", "html-tag-unknown"}


def test_comparison_operators_are_not_tags() -> None:
    diag = Diagnostics()
    out = interpret_tags([seg("if a < b and c > d")], diag, None)
    assert "".join(s.text for s in out) == "if a < b and c > d"
    assert not diag.warnings


# --- smartening ----------------------------------------------------------


def test_smarten_quotes() -> None:
    assert smarten_text('"Hello," she said. "It\'s fine."') == (
        "\u201cHello,\u201d she said. \u201cIt\u2019s fine.\u201d"
    )
    assert smarten_text("'Tis the '90s, 'quoted' rock 'n' roll") == (
        "\u2019Tis the \u201990s, \u2018quoted\u2019 rock \u2019n\u2019 roll"
    )
    assert smarten_text("(\"A\") [\u2014'B']") == "(\u201cA\u201d) [\u2014\u2018B\u2019]"


def test_smarten_leaves_curly_quotes_alone() -> None:
    s = "\u201cAlready\u201d \u2018curly\u2019"
    assert smarten_text(s) == s


def test_smarten_uses_previous_text_for_context() -> None:
    assert smarten_text('"', before="word") == "\u201d"
    assert smarten_text('"', before="") == "\u201c"
