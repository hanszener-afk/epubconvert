"""The HTML front end: Claude's HTML, Google Docs exports, AO3 downloads, and the generic rules."""

from __future__ import annotations

import base64
from pathlib import Path

import html_fixtures as hx
import pytest
from books import png

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.html.reader import read_html
from novel2epub.ir import (
    BlockQuote,
    Bold,
    Image,
    Italic,
    Link,
    NoteRef,
    Paragraph,
    SceneBreak,
    Section,
    SectionKind,
)
from novel2epub.normalise.passes import normalise_book
from novel2epub.pipeline import ConvertOptions, ExitCode, convert
from novel2epub.verify.epubcheck import find_epubcheck


def read(tmp_path: Path, html: str, name: str = "book.html"):  # type: ignore[no-untyped-def]
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    diag = Diagnostics()
    result = read_html(path, diag)
    normalise_book(result.book, diag)
    return result.book, diag


def text(nodes) -> str:  # type: ignore[no-untyped-def]
    out = []
    for n in nodes or []:
        if hasattr(n, "text"):
            out.append(n.text)
        elif hasattr(n, "children"):
            out.append(text(n.children))
    return "".join(out)


def titles(sections: list[Section]) -> list[str]:
    return [text(s.title) for s in sections]


def find(nodes, kind):  # type: ignore[no-untyped-def]
    out = []
    for n in nodes or []:
        if isinstance(n, kind):
            out.append(n)
        if hasattr(n, "children"):
            out += find(n.children, kind)
    return out


# --- Claude ------------------------------------------------------------------


def test_claude_page(tmp_path: Path) -> None:
    book, diag = read(tmp_path, hx.CLAUDE)
    assert book.metadata.title == "The Harbour"
    assert book.metadata.authors == ["Ann Author"]
    assert book.metadata.language == "en-GB"
    assert titles(book.body) == ["Chapter One", "Chapter Two"]
    one = book.body[0].blocks
    assert any(isinstance(b, SceneBreak) for b in one)
    first = next(b for b in one if isinstance(b, Paragraph))
    assert text(find(first.content, Italic)[0].children) == "Mary Rose"
    second = [b for b in one if isinstance(b, Paragraph)][1]
    assert text(second.content) == "Nobody remembered who had built it."
    assert any(isinstance(b, BlockQuote) for b in book.body[1].blocks)
    # The navigation list duplicates the chapters: removed, and logged.
    assert any(r.kind == "contents-list" for r in diag.removals)
    assert book.front and book.front[0].kind is SectionKind.TITLEPAGE


# --- Google Docs ---------------------------------------------------------------


def test_google_docs_export(tmp_path: Path) -> None:
    book, diag = read(tmp_path, hx.GOOGLE_DOCS)
    assert book.metadata.title == "The Long Book"
    assert titles(book.body) == ["Chapter One", "Chapter Two"]
    para = next(b for b in book.body[0].blocks if isinstance(b, Paragraph))
    assert text(find(para.content, Italic)[0].children) == "whisper"
    assert text(find(para.content, Bold)[0].children) == "shout"
    # The footnote is linked, and its marker is not left in the text.
    refs = find(para.content, NoteRef)
    assert len(refs) == 1 and "[1]" not in text(para.content)
    assert text(book.notes[refs[0].note_id].blocks[0].content).strip() == "A note about whispers."
    # Google's redirect is removed from links.
    links = [ln for b in book.body[0].blocks if isinstance(b, Paragraph)
             for ln in find(b.content, Link)]  # fmt: skip
    assert links[0].href == "https://example.org/map"
    # Comments are removed and counted, as for Word.
    assert any(r.kind == "comment" for r in diag.removals)
    assert any(i.code == "comments-removed" for i in diag.warnings)
    # The hidden page-break rule is not a scene break; the typed marker is.
    assert not any(isinstance(b, SceneBreak) for b in book.body[0].blocks)
    assert any(isinstance(b, SceneBreak) for b in book.body[1].blocks)


# --- AO3 -----------------------------------------------------------------------


def test_ao3_download(tmp_path: Path) -> None:
    book, diag = read(tmp_path, hx.AO3)
    meta = book.metadata
    assert meta.title == "Salt and Iron"
    assert meta.authors == ["Bea Writer"]
    assert meta.description == "A smith, a sea, a bargain."
    assert titles(book.body) == ["Chapter 1: The Forge", "Chapter 2: The Sea"]
    body_text = " ".join(text(b.content) for s in book.body for b in s.blocks
                         if isinstance(b, Paragraph))  # fmt: skip
    assert "Chapter Text" not in body_text
    assert "Archive of Our Own" not in body_text
    kinds = {r.kind for r in diag.removals}
    assert {"ao3-boilerplate", "ao3-tags", "ao3-landmark"} <= kinds
    # The author's own notes are kept.
    everything = " ".join(text(b.content) for s in book.sections() for b in s.blocks
                          if isinstance(b, Paragraph))  # fmt: skip
    everything += " ".join(text(p.content) for s in book.sections() for b in s.blocks
                           if isinstance(b, BlockQuote) for p in b.blocks)  # fmt: skip
    assert "Written for a friend." in everything
    assert "Thank you for reading." in everything


# --- generic rules ---------------------------------------------------------------


def test_linked_footnotes_with_backlinks(tmp_path: Path) -> None:
    book, diag = read(tmp_path, hx.FOOTNOTES)
    notes = list(book.notes.values())
    assert [text(n.blocks[0].content).strip() for n in notes] == [
        "Rebuilt in 1850.",
        "A custom mark.",
    ]
    assert notes[1].label == "*"
    assert not any(isinstance(b, SceneBreak) for s in book.body for b in s.blocks)
    assert {"note-mark", "note-backlink"} <= {r.kind for r in diag.removals}


def test_inline_style_can_turn_formatting_off(tmp_path: Path) -> None:
    html = ('<html><body><h1>One</h1><p><b style="font-weight:normal">plain</b> and '
            '<span style="font-style: italic">slanted</span></p><h1>Two</h1><p>x</p></body></html>')  # fmt: skip
    book, _ = read(tmp_path, html)
    para = book.body[0].blocks[0]
    assert isinstance(para, Paragraph)
    assert not find(para.content, Bold)
    assert text(find(para.content, Italic)[0].children) == "slanted"


def test_images_from_files_and_data_uris(tmp_path: Path) -> None:
    (tmp_path / "map.png").write_bytes(png(300, 200))
    data = base64.b64encode(png(320, 240)).decode()
    html = (f'<html><body><h1>One</h1><p>Text.</p><p><img src="map.png" alt="A map"></p>'
            f'<h1>Two</h1><p><img src="data:image/png;base64,{data}" alt="Inline"></p>'
            '</body></html>')  # fmt: skip
    book, _ = read(tmp_path, html)
    images = [b for s in book.body for b in s.blocks if isinstance(b, Image)]
    assert [i.alt for i in images] == ["A map", "Inline"]
    assert all(book.resources[i.resource_id].media_type == "image/png" for i in images)


def test_images_on_the_web_are_refused(tmp_path: Path) -> None:
    html = '<html><body><p><img src="https://example.org/a.png" alt="x"></p></body></html>'
    with pytest.raises(ConversionError) as exc:
        read(tmp_path, html)
    assert "download" in str(exc.value)


def test_fragment_without_html_or_body(tmp_path: Path) -> None:
    book, _ = read(tmp_path, "<h1>One</h1><p>A.</p><h1>Two</h1><p>B.</p>")
    assert titles(book.body) == ["One", "Two"]
    assert book.metadata.title == "book"  # from the file name, with a warning


# --- the whole pipeline ------------------------------------------------------


TOOL = find_epubcheck()


@pytest.mark.parametrize("name", ["CLAUDE", "GOOGLE_DOCS", "AO3", "FOOTNOTES"])
def test_pipeline_fidelity(tmp_path: Path, name: str) -> None:
    src = tmp_path / f"{name.lower()}.html"
    src.write_text(getattr(hx, name), encoding="utf-8")
    outcome = convert(
        ConvertOptions(
            input=src,
            output=tmp_path / "out.epub",
            authors=["Someone"] if name == "GOOGLE_DOCS" else None,
            epubcheck=TOOL is not None,
        )
    )
    assert outcome.exit_code in (ExitCode.OK, ExitCode.WARNINGS), outcome.summary()
    assert outcome.fidelity_main is not None and outcome.fidelity_main.ratio == 1.0, (
        outcome.summary()
    )
    assert not outcome.fidelity_main.added, outcome.summary()
    if TOOL is not None:
        assert outcome.epubcheck is not None and not outcome.epubcheck.messages
