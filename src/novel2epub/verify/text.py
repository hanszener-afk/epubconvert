"""Plain-text extraction from the IR and from a written EPUB.

Both return the main text and the notes text separately, normalised, so that
the order of notes (at chapter end, or in an endnotes file) does not register
as a difference in the main text.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from novel2epub.ir import (
    Block,
    BlockQuote,
    Book,
    Epigraph,
    Heading,
    Image,
    Inline,
    LineBreak,
    NoteKind,
    NoteRef,
    Paragraph,
    SceneBreak,
    Span,
    Text,
    Verse,
)
from novel2epub.verify.fidelity import normalise


@dataclass(frozen=True, slots=True)
class ExtractedText:
    main: str
    notes: str


# --------------------------------------------------------------------------
# IR


def _inline(nodes: list[Inline], out: list[str], refs: list[str]) -> None:
    for node in nodes:
        if isinstance(node, Text):
            out.append(node.text)
        elif isinstance(node, LineBreak):
            out.append(" ")
        elif isinstance(node, NoteRef):
            refs.append(node.note_id)
        elif isinstance(node, Span):
            _inline(node.children, out, refs)


def _blocks(blocks: list[Block], out: list[str], refs: list[str]) -> None:
    for b in blocks:
        if isinstance(b, Paragraph | Heading):
            _inline(b.content, out, refs)
        elif isinstance(b, SceneBreak):
            pass
        elif isinstance(b, BlockQuote):
            _blocks(b.blocks, out, refs)
        elif isinstance(b, Epigraph):
            _blocks(b.blocks, out, refs)
            out.append(" ")
            _inline(b.attribution or [], out, refs)
        elif isinstance(b, Verse):
            for stanza in b.stanzas:
                for line in stanza:
                    _inline(line, out, refs)
                    out.append(" ")
        elif isinstance(b, Image):
            _inline(b.caption or [], out, refs)
        out.append(" ")


def ir_text(book: Book) -> ExtractedText:
    main: list[str] = []
    refs: list[str] = []
    for section in book.sections():
        _blocks(section.opener, main, refs)
        _inline(section.title or [], main, refs)
        main.append(" ")
        _blocks(section.blocks, main, refs)
    seen: dict[str, None] = {}
    for nid in refs:
        seen.setdefault(nid, None)
    referenced = list(seen)
    ordered = [n for n in referenced if book.notes[n].kind is NoteKind.FOOTNOTE]
    ordered += [n for n in referenced if book.notes[n].kind is NoteKind.ENDNOTE]
    ordered += [n for n in book.notes if n not in seen]
    notes: list[str] = []
    for nid in ordered:
        _blocks(book.notes[nid].blocks, notes, [])
    return ExtractedText(normalise("".join(main)), normalise("".join(notes)))


# --------------------------------------------------------------------------
# EPUB

_X = "{http://www.w3.org/1999/xhtml}"
_EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
_BLOCKS = {
    "p", "div", "section", "aside", "blockquote", "figure", "figcaption", "li", "ol",
    "ul", "h1", "h2", "h3", "h4", "h5", "h6", "body", "nav", "header", "footer",
}  # fmt: skip


def _generated(el: etree._Element) -> bool:
    classes = (el.get("class") or "").split()
    return (
        el.get(_EPUB_TYPE) == "noteref"
        or el.get("role") == "doc-backlink"
        or "scene-break" in classes
        or "generated" in classes
    )


def _is_note_container(el: etree._Element) -> bool:
    return el.get(_EPUB_TYPE) in ("footnote", "endnotes")


def _walk(el: etree._Element, main: list[str], notes: list[str], in_notes: bool) -> None:
    if not isinstance(el.tag, str):
        return
    in_notes = in_notes or _is_note_container(el)
    out = notes if in_notes else main
    tag = el.tag.removeprefix(_X)
    if not _generated(el):
        if tag == "br":
            out.append(" ")
        if el.text:
            out.append(el.text)
        for child in el:
            _walk(child, main, notes, in_notes)
            if child.tail:
                out.append(child.tail)
    if tag in _BLOCKS:
        out.append(" ")


def epub_text(path: Path | bytes) -> ExtractedText:
    main: list[str] = []
    notes: list[str] = []
    source = io.BytesIO(path) if isinstance(path, bytes) else path
    with zipfile.ZipFile(source) as z:
        container = etree.fromstring(z.read("META-INF/container.xml"))
        opf_path = container.find(".//{*}rootfile").get("full-path")
        assert opf_path
        opf = etree.fromstring(z.read(opf_path))
        base = posixpath.dirname(opf_path)
        items = {i.get("id"): i for i in opf.iterfind(".//{*}manifest/{*}item")}
        for ref in opf.iterfind(".//{*}spine/{*}itemref"):
            item = items[ref.get("idref")]
            if "nav" in (item.get("properties") or "").split():
                continue
            doc = etree.fromstring(z.read(posixpath.join(base, item.get("href") or "")))
            body = doc.find(f"{_X}body")
            if body is None or body.get(_EPUB_TYPE) == "cover":
                continue
            _walk(body, main, notes, False)
    return ExtractedText(normalise("".join(main)), normalise("".join(notes)))
