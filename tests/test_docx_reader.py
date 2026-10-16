from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import docx_fixtures as fx
import pytest
from docxbuild import Docx, p, r, styles_xml

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.docx.reader import DocxOptions, read_docx
from novel2epub.ir import (
    Align,
    AllCaps,
    BlockQuote,
    Bold,
    Book,
    Heading,
    Image,
    Inline,
    Italic,
    LineBreak,
    Link,
    NoteKind,
    NoteRef,
    Paragraph,
    SceneBreak,
    Section,
    SectionKind,
    SmallCaps,
    Span,
    Subscript,
    Superscript,
    Text,
)
from novel2epub.normalise.passes import normalise_book


def read(doc: Docx, tmp_path: Path, **opts: object) -> tuple[Book, Diagnostics]:
    path = doc.save(tmp_path / "in.docx")
    diag = Diagnostics()
    result = read_docx(path, DocxOptions(**opts), diag)  # type: ignore[arg-type]
    normalise_book(result.book, diag)  # the pipeline always runs this after a front end
    return result.book, diag


def text(nodes: list[Inline] | None) -> str:
    out = []
    for n in nodes or []:
        if isinstance(n, Text):
            out.append(n.text)
        elif isinstance(n, LineBreak):
            out.append("\n")
        elif isinstance(n, Span):
            out.append(text(n.children))
    return "".join(out)


def paras(section: Section) -> list[Paragraph]:
    return [b for b in section.blocks if isinstance(b, Paragraph)]


def find(nodes: list[Inline], kind: type) -> list[Span]:
    found: list[Span] = []
    for n in nodes:
        if isinstance(n, kind):
            found.append(n)  # type: ignore[arg-type]
        if isinstance(n, Span):
            found.extend(find(n.children, kind))
    return found


# --- styled manuscript ---------------------------------------------------


def test_styled_metadata(tmp_path: Path) -> None:
    path = fx.styled().save(tmp_path / "s.docx")
    result = read_docx(path, DocxOptions(), Diagnostics())
    assert result.book.metadata.title == "The Styled Manuscript"
    assert result.book.metadata.authors == ["Ann Author"]
    assert result.book.metadata.language == "en-GB"
    assert result.source_date == datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)


def test_styled_structure(tmp_path: Path) -> None:
    book, diag = read(fx.styled(), tmp_path)
    assert [s.kind for s in book.front] == [SectionKind.TITLEPAGE]
    assert [text(s.title) for s in book.body] == ["Chapter One", "Chapter Two"]
    assert all(s.kind is SectionKind.CHAPTER for s in book.body)
    methods = {d.method for d in diag.decisions if d.kind == "chapter-detection"}
    assert methods == {"style"}


def test_styled_inline_formatting(tmp_path: Path) -> None:
    book, _ = read(fx.styled(), tmp_path)
    ch1 = book.body[0]
    ps = paras(ch1)
    assert text(ps[0].content) == "It was, she thought, entirely the wrong boat."
    assert [text(s.children) for s in find(ps[0].content, Italic)] == ["entirely"]
    assert [text(s.children) for s in find(ps[1].content, SmallCaps)] == ["Harbourmaster"]
    assert [text(s.children) for s in find(ps[1].content, Superscript)] == ["rd"]
    assert [text(s.children) for s in find(ps[1].content, Subscript)] == ["2"]
    # Character style "Emphasis" resolves to italic; typography is untouched.
    assert text(ps[2].content) == "\u201cNo,\u201d he said\u2014quietly\u2026 never."
    assert [text(s.children) for s in find(ps[2].content, Italic)] == ["never"]
    assert text(ps[3].content) == "A line\nand the next line, with a non\u00a0breaking space."
    links = find(ps[4].content, Link)
    assert [(text(lk.children), lk.href) for lk in links] == [  # type: ignore[attr-defined]
        ("the notice", "https://example.org/harbour")
    ]


def test_styled_blocks(tmp_path: Path) -> None:
    book, diag = read(fx.styled(), tmp_path)
    kinds = [type(b).__name__ for b in book.body[0].blocks]
    assert kinds == [
        "Paragraph", "Paragraph", "Paragraph", "Paragraph", "Paragraph",
        "SceneBreak", "Paragraph", "Heading", "BlockQuote", "Image", "Paragraph",
    ]  # fmt: skip
    quote = next(b for b in book.body[0].blocks if isinstance(b, BlockQuote))
    assert [text(q.content) for q in quote.blocks] == ["Dear Sir, I write in haste.", "Yours, &c."]  # type: ignore[union-attr]
    image = next(b for b in book.body[0].blocks if isinstance(b, Image))
    assert image.alt == "A map of the harbour."
    assert book.resources[image.resource_id].media_type == "image/png"
    heading = next(b for b in book.body[0].blocks if isinstance(b, Heading))
    assert text(heading.content) == "A Letter" and heading.level == 2
    assert any(rm.kind == "empty-paragraph" for rm in diag.removals)
    assert any(rm.kind == "scene-break-marker" and rm.text == "* * *" for rm in diag.removals)


def test_styled_chapter_two(tmp_path: Path) -> None:
    book, _ = read(fx.styled(), tmp_path)
    ps = paras(book.body[1])
    assert text(ps[0].content) == "Indented with a tab, as some authors do."
    assert [text(s.children) for s in find(ps[1].content, Bold)] == ["words"]
    assert [text(s.children) for s in find(ps[1].content, AllCaps)] == ["shouting"]


# --- direct formatting only ----------------------------------------------


def test_direct_formatting_chapters(tmp_path: Path) -> None:
    book, diag = read(fx.direct(), tmp_path)
    assert [text(s.title) for s in book.body] == [
        "CHAPTER ONE\nThe Arrival",
        "CHAPTER TWO",
        "CHAPTER THREE",
    ]
    decisions = [d for d in diag.decisions if d.kind == "chapter-detection"]
    assert decisions and all(d.method in ("pattern", "formatting") for d in decisions)
    assert [text(s.children) for s in find(paras(book.body[0])[1].content, Italic)] == ["Not once."]


def test_missing_title_falls_back_to_file_name(tmp_path: Path) -> None:
    book, diag = read(fx.direct(), tmp_path)
    assert book.metadata.title == "in"
    assert any(i.code == "title-from-filename" for i in diag.warnings)


# --- notes ---------------------------------------------------------------


def test_footnotes_and_endnotes(tmp_path: Path) -> None:
    book, diag = read(fx.footnotes(), tmp_path)
    ps = paras(book.body[0])
    refs = [n.note_id for pr in ps for n in pr.content if isinstance(n, NoteRef)]
    assert len(refs) == 4
    notes = [book.notes[nid] for nid in refs]
    assert [n.kind for n in notes] == [NoteKind.FOOTNOTE] * 3 + [NoteKind.ENDNOTE]
    assert text(notes[0].blocks[0].content) == "The harbour was rebuilt in 1850."  # type: ignore[union-attr]
    assert len(notes[1].blocks) == 2
    assert find(notes[1].blocks[0].content, Italic)  # type: ignore[union-attr]
    assert notes[2].label == "*"
    assert text(notes[2].blocks[0].content) == "A custom-marked note."  # type: ignore[union-attr]
    assert text(ps[2].content) == "A custom mark here."
    assert text(notes[3].blocks[0].content) == "An endnote about sources."  # type: ignore[union-attr]
    assert sum(1 for rm in diag.removals if rm.kind == "note-mark") == 2


# --- tracked changes -----------------------------------------------------


def test_tracked_changes_fail_by_default(tmp_path: Path) -> None:
    with pytest.raises(ConversionError) as exc:
        read(fx.tracked(), tmp_path)
    assert exc.value.code == "tracked-changes"
    assert "--accept-changes" in str(exc.value)


def test_accept_changes(tmp_path: Path) -> None:
    book, diag = read(fx.tracked(), tmp_path, revisions="accept")
    t = [text(pr.content) for pr in paras(book.body[0])]
    assert t[0] == "The cat sat noisily on the mat."
    assert t[1] == "Then the end moved words."
    assert t[2] == "This is now italic."
    assert find(paras(book.body[0])[2].content, Italic)
    assert t[3] == "Commented text stays."
    assert t[5] == "First half and second half."
    assert len(t) == 6
    assert any(i.code == "comments-removed" and "2" in i.message for i in diag.warnings)


def test_reject_changes(tmp_path: Path) -> None:
    book, _ = read(fx.tracked(), tmp_path, revisions="reject")
    ps = paras(book.body[0])
    t = [text(pr.content) for pr in ps]
    assert t[0] == "The cat sat quietly on the mat."
    assert t[1] == "Then moved words the end."
    assert t[2] == "This is now italic."
    assert not find(ps[2].content, Italic)
    assert t[5:] == ["First half", "and second half."]


# --- scene breaks and literal tags ---------------------------------------


def test_scene_break_variants(tmp_path: Path) -> None:
    book, diag = read(fx.scene_breaks(), tmp_path)
    assert len(book.body) == 1
    assert any(i.code == "no-chapters" for i in diag.warnings)
    blocks = book.body[0].blocks
    breaks = [i for i, b in enumerate(blocks) if isinstance(b, SceneBreak)]
    assert len(breaks) == 8
    # Every break sits between two scenes.
    for i in breaks:
        assert isinstance(blocks[i - 1], Paragraph) and isinstance(blocks[i + 1], Paragraph)
    assert not any(isinstance(b, Image) for b in blocks)  # ornament became a break
    ornament = [d for d in diag.removals if d.kind == "ornament-image"]
    assert len(ornament) == 1


def test_literal_html_tags(tmp_path: Path) -> None:
    book, diag = read(fx.scene_breaks(), tmp_path)
    ps = paras(book.body[0])
    tagged = ps[10]
    assert text(tagged.content) == "He meant it. Really meant it, all of it."
    assert [text(s.children) for s in find(tagged.content, Italic)] == ["Really"]
    assert [text(s.children) for s in find(tagged.content, Bold)] == ["all"]
    assert [text(s.children) for s in find(ps[11].content, Italic)] == ["across two runs"]
    assert text(ps[12].content) == "An unbalanced <i>tag stays literal."
    assert text(ps[13].content) == "Unknown <u>tags</u> stay too."
    assert text(ps[14].content) == "\u201c\u2026\u201d"
    codes = [i.code for i in diag.warnings]
    assert "html-tag-unbalanced" in codes and "html-tag-unknown" in codes
    assert sum(1 for rm in diag.removals if rm.kind == "html-tag") == 6


def test_literal_tags_can_be_disabled(tmp_path: Path) -> None:
    book, _ = read(fx.scene_breaks(), tmp_path, html_tags=False)
    assert text(paras(book.body[0])[10].content).startswith("He meant it. <i>Really</i>")


# --- front and back matter -----------------------------------------------


def test_front_and_back_matter(tmp_path: Path) -> None:
    book, diag = read(fx.front_back(), tmp_path)
    assert [s.kind for s in book.front] == [
        SectionKind.TITLEPAGE,
        SectionKind.COPYRIGHT,
        SectionKind.DEDICATION,
    ]
    assert [s.kind for s in book.body] == [
        SectionKind.PROLOGUE,
        SectionKind.CHAPTER,
        SectionKind.EPILOGUE,
    ]
    assert [s.kind for s in book.back] == [SectionKind.ACKNOWLEDGEMENTS, SectionKind.ABOUT_AUTHOR]
    kinds = {rm.kind for rm in diag.removals}
    assert {"manual-toc", "hidden-text", "field-result"} <= kinds
    prologue = paras(book.body[0])
    assert text(prologue[0].content) == "Before it all began."
    ch1 = paras(book.body[1])
    assert text(ch1[1].content) == "Visible text."
    assert book.front[2].blocks[0].align is Align.CENTRE  # type: ignore[union-attr]


def test_chapter_regex_override(tmp_path: Path) -> None:
    d = Docx(title="Regex", creator="A")
    d.add(
        p(r("~ 1 ~")), p(r("One.")), p(r("~ 2 ~")), p(r("Two.")),
    )  # fmt: skip
    book, diag = read(d, tmp_path, chapter_regex=r"^~ \d+ ~$")
    assert [text(s.title) for s in book.body] == ["~ 1 ~", "~ 2 ~"]
    assert {d.method for d in diag.decisions if d.kind == "chapter-detection"} == {"regex"}


# --- robustness ----------------------------------------------------------


def test_toggle_properties_xor_between_styles(tmp_path: Path) -> None:
    # Paragraph style italic + character style italic = not italic (toggle);
    # direct formatting is absolute.
    d = Docx(title="T", creator="A")
    d.styles = styles_xml()
    d.add(
        p(r("Chapter"), style="Heading1"),
        p(r("plain"), r("upright", style="Emphasis"), r("forced", i=True), style="Quote"),
    )
    book, _ = read(d, tmp_path)
    para = book.body[0].blocks[0]
    assert isinstance(para, BlockQuote)
    inner = para.blocks[0]
    assert isinstance(inner, Paragraph)
    italic = [text(s.children) for s in find(inner.content, Italic)]
    assert italic == ["plain", "forced"]


def test_text_box_content_is_kept(tmp_path: Path) -> None:
    textbox = (
        '<w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:anchor>'
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        "<wps:wsp><wps:txbx><w:txbxContent>"
        + p(r("Inside the box."))
        + "</w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic></wp:anchor></w:drawing>"
        "</mc:Choice><mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>"
        + p(r("Inside the box."))
        + "</w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r>"
    )
    d = Docx(title="T", creator="A").add(p(r("Before.")), p(textbox), p(r("After.")))
    book, diag = read(d, tmp_path)
    t = [text(pr.content) for pr in paras(book.body[0])]
    assert t == ["Before.", "Inside the box.", "After."]
    assert any(i.code == "text-box" for i in diag.warnings)


def test_tables_are_flattened_with_warning(tmp_path: Path) -> None:
    table = (
        "<w:tbl><w:tr><w:tc>" + p(r("Cell one")) + "</w:tc><w:tc>" + p(r("Cell two"))
        + "</w:tc></w:tr></w:tbl>"
    )  # fmt: skip
    d = Docx(title="T", creator="A").add(p(r("Before.")), table, p(r("After.")))
    book, diag = read(d, tmp_path)
    assert [text(pr.content) for pr in paras(book.body[0])] == [
        "Before.", "Cell one", "Cell two", "After.",
    ]  # fmt: skip
    assert any(i.code == "table-flattened" for i in diag.warnings)


def test_symbol_and_special_characters(tmp_path: Path) -> None:
    d = Docx(title="T", creator="A").add(
        p(
            r("non"),
            "<w:r><w:noBreakHyphen/></w:r>",
            r("breaking and soft"),
            "<w:r><w:softHyphen/></w:r>",
            r("hyphen"),
        )
    )
    book, _ = read(d, tmp_path)
    assert text(paras(book.body[0])[0].content) == "non\u2011breaking and soft\u00adhyphen"


def test_not_a_docx(tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ConversionError) as exc:
        read_docx(bad, DocxOptions(), Diagnostics())
    assert exc.value.code == "input-invalid"


# --- typed contents lists ------------------------------------------------


def _contents_doc(with_heading: bool, page_break: bool) -> Docx:
    from docxbuild import PAGE_BREAK

    d = Docx(title="T", creator="A")
    if with_heading:
        d.add(p(r("Contents")))
    d.add(*(p(r(f"Chapter {n}")) for n in (1, 2, 3, 4)))
    if page_break:
        d.add(p(PAGE_BREAK))
    for n in (1, 2, 3, 4):
        d.add(p(r(f"Chapter {n}")), p(r(f"The text of chapter {n} is long enough to be prose.")))
    return d


@pytest.mark.parametrize("page_break", [True, False])
def test_typed_contents_list_is_not_chapters(tmp_path: Path, page_break: bool) -> None:
    book, diag = read(_contents_doc(True, page_break), tmp_path)
    assert [text(s.title) for s in book.body] == [f"Chapter {n}" for n in (1, 2, 3, 4)]
    assert all(paras(s) for s in book.body)
    toc = [rm.text for rm in diag.removals if rm.kind == "manual-toc"]
    assert toc == ["Contents", "Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"]


def test_contents_list_without_heading_is_kept_and_flagged(tmp_path: Path) -> None:
    book, diag = read(_contents_doc(False, True), tmp_path)
    assert [text(s.title) for s in book.body] == [f"Chapter {n}" for n in (1, 2, 3, 4)]
    assert not any(rm.kind == "manual-toc" for rm in diag.removals)
    assert any(d.kind == "contents-list" for d in diag.needs_review())


def test_contents_section_with_prose_is_never_removed(tmp_path: Path) -> None:
    d = Docx(title="T", creator="A").add(
        p(r("Contents"), style="Heading1"),
        p(r("This is not a contents list at all but a long paragraph of real prose text.")),
        p(r("Chapter One"), style="Heading1"),
        p(r("Story.")),
    )
    _, diag = read(d, tmp_path)
    assert not any(rm.kind == "manual-toc" for rm in diag.removals)
    assert any(d.kind == "contents-list" for d in diag.needs_review())
