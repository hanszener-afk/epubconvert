"""The six synthetic DOCX fixtures of the golden corpus.

Each builder returns a Docx; tests save it into a temporary directory.
`scripts/make_fixtures.py` writes them to tests/fixtures/docx for manual
inspection in Word, LibreOffice, or Sigil.
"""

from __future__ import annotations

from books import png
from docxbuild import (
    HR,
    PAGE_BREAK,
    SOFT_BREAK,
    TAB,
    Docx,
    dele,
    endnote_ref,
    fld,
    footnote_ref,
    image_run,
    ins,
    p,
    r,
    text_p,
)

LOREM = (
    "The rain had not stopped for three days, and the harbour lay grey and "
    "restless under a low sky."
)


def styled() -> Docx:
    """Clean manuscript using named styles throughout."""
    d = Docx(title="The Styled Manuscript", creator="Ann Author")
    d.media["rIdImg1"] = ("map.png", png(300, 200))
    d.links["rIdLink1"] = "https://example.org/harbour"
    d.add(
        text_p("The Styled Manuscript", style="Title"),
        p(r("Chapter One"), style="Heading1"),
        p(r("It was, she thought, "), r("entirely", i=True), r(" the wrong boat.")),
        p(
            r("The "),
            r("Harbourmaster", sc=True),
            r(" wrote on the 3"),
            r("rd", sup=True),
            r(": \u201cH"),
            r("2", sub=True),
            r("O everywhere.\u201d"),
        ),
        p(r("\u201cNo,\u201d he said\u2014quietly\u2026 "), r("never", style="Emphasis"), r(".")),
        p(r("A line"), SOFT_BREAK, r("and the next line, with a non\u00a0breaking space.")),
        p("", ""),  # empty spacing paragraph
        p(r("Read "), f'<w:hyperlink r:id="rIdLink1">{r("the notice")}</w:hyperlink>', r(".")),
        p(r("* * *"), jc="center"),
        p(r(LOREM)),
        p(r("A Letter"), style="Heading2"),
        p(r("Dear Sir, I write in haste."), style="Quote"),
        p(r("Yours, &c."), style="BlockText"),
        p(image_run("rIdImg1", 1905000, 1270000, "A map of the harbour.")),
        p(r(LOREM)),
        p(PAGE_BREAK),
        p(r("Chapter Two"), style="ChapterTitle"),
        p(TAB, r("Indented with a tab, as some authors do.")),
        p(r("Bold "), r("words", b=True), r(" and "), r("shouting", caps=True), r(".")),
    )
    return d


def direct() -> Docx:
    """No styles: chapters marked only by direct formatting and page breaks."""
    d = Docx(title="", creator="Bea Writer")
    heading = {"jc": "center"}
    d.add(
        p(r("CHAPTER ONE", b=True, sz=32), **heading),
        p(r("The Arrival", i=True, sz=28), **heading),
        p(r(LOREM)),
        p(r("She did not look back. "), r("Not once.", i=True)),
        p(r("End of the first chapter.")),
        p(PAGE_BREAK),
        p(r("CHAPTER TWO", b=True, sz=32), **heading),
        p(r(LOREM)),
        p(r("A short line.")),
        p(PAGE_BREAK),
        p(r("CHAPTER THREE", b=True, sz=32), **heading),
        p(r(LOREM)),
    )
    return d


def footnotes() -> Docx:
    d = Docx(title="Notes and Queries", creator="Cid Scholar")
    d.footnotes[1] = p(
        '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>',
        r(" The harbour was rebuilt in 1850."),
    )
    d.footnotes[2] = p(
        '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>',
        r(" A second note, with "),
        r("italics", i=True),
        r("."),
    ) + p(r("And a second paragraph."))
    d.footnotes[3] = p(r("* A custom-marked note."))
    d.endnotes[1] = p(
        '<w:r><w:rPr><w:rStyle w:val="EndnoteReference"/></w:rPr><w:endnoteRef/></w:r>',
        r(" An endnote about sources."),
    )
    d.add(
        p(r("Chapter One"), style="Heading1"),
        p(r("The harbour"), footnote_ref(1), r(" was old.")),
        p(r("Very old indeed."), footnote_ref(2)),
        p(
            r("A custom mark"),
            '<w:r><w:footnoteReference w:customMarkFollows="1" w:id="3"/><w:t>*</w:t></w:r>',
            r(" here."),
        ),
        p(r("The source is cited."), endnote_ref(1)),
    )
    return d


def tracked() -> Docx:
    """Insertions, deletions, a move, a formatting change, and comments."""
    d = Docx(title="Under Revision", creator="Dee Drafter")
    d.comments[0] = "Is this right?"
    d.comments[1] = "Cut this?"
    move_from = (
        '<w:moveFrom w:id="5" w:author="Ed" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:t xml:space="preserve">moved words </w:t></w:r></w:moveFrom>'
    )
    move_to = (
        '<w:moveTo w:id="6" w:author="Ed" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:t xml:space="preserve"> moved words</w:t></w:r></w:moveTo>'
    )
    fmt_change = (
        "<w:r><w:rPr><w:i/>"
        '<w:rPrChange w:id="7" w:author="Ed" w:date="2026-01-01T00:00:00Z"><w:rPr/></w:rPrChange>'
        "</w:rPr><w:t>now italic</w:t></w:r>"
    )
    d.add(
        p(r("Chapter One"), style="Heading1"),
        p(r("The cat sat "), dele("quietly "), ins(r("noisily ")), r("on the mat.")),
        p(r("Then "), move_from, r("the end"), move_to, r(".")),
        p(r("This is "), fmt_change, r(".")),
        p(
            '<w:commentRangeStart w:id="0"/>',
            r("Commented text"),
            '<w:commentRangeEnd w:id="0"/>',
            '<w:r><w:commentReference w:id="0"/></w:r>',
            r(" stays."),
        ),
        p(r("Another"), '<w:r><w:commentReference w:id="1"/></w:r>', r(" comment.")),
        # A paragraph whose mark was deleted: accepting merges it with the next.
        p(
            r("First half"),
            extra_ppr='<w:rPr><w:del w:id="8" w:author="Ed" w:date="2026-01-01T00:00:00Z"/></w:rPr>',
        ),
        p(r(" and second half.")),
    )
    return d


def scene_breaks() -> Docx:
    """Every scene-break form, plus AO3-style literal HTML tags."""
    d = Docx(title="", creator="Eve Fan")
    d.media["rIdOrn"] = ("ornament.png", png(60, 20, (0, 0, 0)))
    para = lambda n: p(r(f"Scene {n} text. {LOREM}"))  # noqa: E731
    d.add(
        para(1),
        p(r("***"), jc="center"),
        para(2),
        p(r("* * *")),
        para(3),
        p(r("#"), jc="center"),
        para(4),
        p(image_run("rIdOrn", 571500, 190500, None), jc="center"),
        para(5),
        p(HR),
        para(6),
        p(r(f"Scene 7 ends with a border. {LOREM}"), border="bottom"),
        para(8),
        p(r("________________________________________")),
        para(9),
        p(),
        p(),
        para(10),
        p(r("He meant it. <i>Really</i> meant it, <b>all</b> of it.")),
        p(r("Split <i>across "), r("two runs</i> works.")),
        p(r("An unbalanced <i>tag stays literal.")),
        p(r("Unknown <u>tags</u> stay too.")),
        p(r("\u201c\u2026\u201d")),
    )
    return d


def front_back() -> Docx:
    """Heavy front and back matter, a manual contents list, and a TOC field."""
    d = Docx(title="The Long Book", creator="Fay Novelist")
    toc_field = (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-1" \\h </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
    )
    d.add(
        text_p("The Long Book", style="Title", jc="center"),
        p(r("Fay Novelist"), jc="center"),
        p(PAGE_BREAK),
        p(r("Copyright \u00a9 2026 Fay Novelist. All rights reserved.")),
        p(r("ISBN 978-0-00-000000-2")),
        p(PAGE_BREAK),
        p(r("For my mother."), jc="center"),
        p(PAGE_BREAK),
        p(r("Contents"), style="Heading1"),
        p(toc_field, r("Prologue\t1")),
        p(r("Chapter One\t3")),
        p(r("Epilogue\t9"), '<w:r><w:fldChar w:fldCharType="end"/></w:r>'),
        p(r("Prologue"), style="Heading1"),
        p(r("Before it all began. "), fld("PAGE", "4"), r("")),
        p(r("Chapter One"), style="Heading1"),
        p(r(LOREM)),
        p(r("Hidden note to self.", vanish=True), r("Visible text.")),
        p(r("Epilogue"), style="Heading1"),
        p(r("After it all ended.")),
        p(r("Acknowledgements"), style="Heading1"),
        p(r("Thanks to everyone.")),
        p(r("About the Author"), style="Heading1"),
        p(r("Fay lives by the sea.")),
    )
    return d


ALL = {
    "styled": styled,
    "direct": direct,
    "footnotes": footnotes,
    "tracked": tracked,
    "scene_breaks": scene_breaks,
    "front_back": front_back,
}
