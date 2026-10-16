"""Render IR sections to XHTML content documents.

Markup conventions (documented in docs/markup.md, relied on by verify/text.py):
- Generated text that is not in the source carries one of: epub:type="noteref",
  role="doc-backlink", class "scene-break", or class "generated".
- Class names are few and stable so that a stylesheet can be swapped later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.ir import (
    Align,
    AllCaps,
    Block,
    BlockQuote,
    Bold,
    Epigraph,
    Heading,
    Image,
    Inline,
    Italic,
    LineBreak,
    Link,
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
    Verse,
)

XHTML_NS = "http://www.w3.org/1999/xhtml"
OPS_NS = "http://www.idpf.org/2007/ops"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NSMAP = {None: XHTML_NS, "epub": OPS_NS}
EPUB_TYPE = f"{{{OPS_NS}}}type"
SCENE_BREAK_TEXT = "* * *"
X = f"{{{XHTML_NS}}}"


def sub(parent: etree._Element, tag: str) -> etree._Element:
    return etree.SubElement(parent, X + tag)


def element(tag: str) -> etree._Element:
    return etree.Element(X + tag)


@dataclass(frozen=True, slots=True)
class Semantics:
    epub_type: str | None
    role: str | None
    label: str  # used when the section has no title; "{n}" is the chapter number


SECTION_SEMANTICS: dict[SectionKind, Semantics] = {
    SectionKind.CHAPTER: Semantics("chapter", "doc-chapter", "Chapter {n}"),
    SectionKind.PART: Semantics("part", "doc-part", "Part {n}"),
    SectionKind.PROLOGUE: Semantics("prologue", "doc-prologue", "Prologue"),
    SectionKind.EPILOGUE: Semantics("epilogue", "doc-epilogue", "Epilogue"),
    SectionKind.TITLEPAGE: Semantics("titlepage", None, "Title Page"),
    SectionKind.HALFTITLE: Semantics("halftitlepage", None, "Half Title"),
    SectionKind.COPYRIGHT: Semantics("copyright-page", None, "Copyright"),
    SectionKind.DEDICATION: Semantics("dedication", "doc-dedication", "Dedication"),
    SectionKind.EPIGRAPH: Semantics("epigraph", None, "Epigraph"),
    SectionKind.FOREWORD: Semantics("foreword", "doc-foreword", "Foreword"),
    SectionKind.PREFACE: Semantics("preface", "doc-preface", "Preface"),
    SectionKind.INTRODUCTION: Semantics("introduction", "doc-introduction", "Introduction"),
    SectionKind.ACKNOWLEDGEMENTS: Semantics(
        "acknowledgments", "doc-acknowledgments", "Acknowledgements"
    ),
    SectionKind.AFTERWORD: Semantics("afterword", "doc-afterword", "Afterword"),
    SectionKind.ABOUT_AUTHOR: Semantics(None, None, "About the Author"),
    SectionKind.ALSO_BY: Semantics(None, None, "Also by the Author"),
    SectionKind.OTHER: Semantics(None, None, "Section {n}"),
}

_CONTAINERS = {
    "html", "head", "body", "section", "blockquote", "div", "aside", "figure",
    "ol", "li", "nav",
}  # fmt: skip


@dataclass
class NotePlan:
    """Where each note lives and how it is labelled. Computed once per book."""

    label: dict[str, str] = field(default_factory=dict)  # note id -> marker text
    home: dict[str, str] = field(default_factory=dict)  # note id -> file name
    html_id: dict[str, str] = field(default_factory=dict)  # note id -> element id
    first_ref: dict[str, str] = field(default_factory=dict)  # note id -> "file#refid"
    ref_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class RenderContext:
    file_name: str
    language: str
    notes: NotePlan
    image_href: dict[str, str]  # resource id -> href relative to the text directory
    diag: Diagnostics
    footnotes_here: list[str] = field(default_factory=list)


def plain_text(inlines: list[Inline]) -> str:
    out: list[str] = []
    for node in inlines:
        if isinstance(node, Text):
            out.append(node.text)
        elif isinstance(node, LineBreak):
            out.append(" ")
        elif isinstance(node, Span):
            out.append(plain_text(node.children))
    return " ".join("".join(out).split())


def section_label(section: Section, number: int) -> str:
    if section.title:
        text = plain_text(section.title)
        if text:
            return text
    return SECTION_SEMANTICS[section.kind].label.format(n=number)


# --------------------------------------------------------------------------
# Document skeleton


def new_document(title: str, language: str, css_href: str) -> tuple[etree._Element, etree._Element]:
    html = etree.Element(X + "html", nsmap=NSMAP)
    html.set(f"{{{XML_NS}}}lang", language)
    html.set("lang", language)
    head = sub(html, "head")
    sub(head, "title").text = title
    link = sub(head, "link")
    link.set("rel", "stylesheet")
    link.set("type", "text/css")
    link.set("href", css_href)
    body = sub(html, "body")
    return html, body


def serialise(html: etree._Element) -> bytes:
    _layout(html)
    data: bytes = etree.tostring(
        html, xml_declaration=True, encoding="utf-8", doctype="<!DOCTYPE html>"
    )
    return data + b"\n"


def _layout(el: etree._Element) -> None:
    """One block element per line, for readable hand editing (e.g. in Sigil)."""
    tag = etree.QName(el).localname
    if tag in _CONTAINERS and len(el):
        if not (el.text or "").strip():
            el.text = "\n"
        for child in el:
            if not (child.tail or "").strip():
                child.tail = "\n"
    for child in el:
        if isinstance(child.tag, str):
            _layout(child)


# --------------------------------------------------------------------------
# Inline content


def _append_text(parent: etree._Element, text: str) -> None:
    if len(parent):
        last = parent[-1]
        last.tail = (last.tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def render_inlines(parent: etree._Element, inlines: list[Inline], ctx: RenderContext) -> None:
    for node in inlines:
        if isinstance(node, Text):
            try:
                _append_text(parent, node.text)
            except ValueError as exc:  # control characters are not allowed in XML
                raise ConversionError(
                    "invalid-character", f"Text contains a character XML cannot hold: {exc}"
                ) from exc
        elif isinstance(node, LineBreak):
            sub(parent, "br")
        elif isinstance(node, NoteRef):
            _render_noteref(parent, node, ctx)
        elif isinstance(node, Span):
            el = _span_element(parent, node)
            render_inlines(el, node.children, ctx)
        else:  # pragma: no cover - exhaustiveness guard
            raise TypeError(f"Unknown inline {node!r}")


def _span_element(parent: etree._Element, node: Span) -> etree._Element:
    if isinstance(node, Italic):
        return sub(parent, "i")
    if isinstance(node, Bold):
        return sub(parent, "b")
    if isinstance(node, SmallCaps):
        el = sub(parent, "span")
        el.set("class", "sc")
        return el
    if isinstance(node, AllCaps):
        el = sub(parent, "span")
        el.set("class", "caps")
        return el
    if isinstance(node, Superscript):
        return sub(parent, "sup")
    if isinstance(node, Subscript):
        return sub(parent, "sub")
    if isinstance(node, Link):
        el = sub(parent, "a")
        el.set("href", node.href)
        return el
    raise TypeError(f"Unknown span {node!r}")  # pragma: no cover


def _render_noteref(parent: etree._Element, node: NoteRef, ctx: RenderContext) -> None:
    plan = ctx.notes
    if node.note_id not in plan.label:
        raise ConversionError("note-missing", f"Reference to unknown note {node.note_id!r}.")
    count = plan.ref_counts.get(node.note_id, 0) + 1
    plan.ref_counts[node.note_id] = count
    ref_id = f"ref-{plan.html_id[node.note_id].removeprefix('note-')}-{count}"
    home = plan.home[node.note_id]
    target = plan.html_id[node.note_id]
    if count == 1:
        plan.first_ref[node.note_id] = f"{ctx.file_name}#{ref_id}"
        if home == ctx.file_name:
            ctx.footnotes_here.append(node.note_id)
    sup = sub(parent, "sup")
    a = sub(sup, "a")
    a.set(EPUB_TYPE, "noteref")
    a.set("role", "doc-noteref")
    a.set("id", ref_id)
    a.set("href", f"#{target}" if home == ctx.file_name else f"{home}#{target}")
    a.text = plan.label[node.note_id]


# --------------------------------------------------------------------------
# Blocks


def render_blocks(parent: etree._Element, blocks: list[Block], ctx: RenderContext) -> None:
    first = True
    for block in blocks:
        if isinstance(block, Paragraph):
            _paragraph(parent, block, ctx, first)
            first = False
        elif isinstance(block, SceneBreak):
            p = sub(parent, "p")
            p.set("class", "scene-break")
            p.set("role", "separator")
            p.text = SCENE_BREAK_TEXT
            first = True
        elif isinstance(block, Heading):
            level = min(max(block.level, 2), 6)
            h = sub(parent, f"h{level}")
            render_inlines(h, block.content, ctx)
            first = True
        elif isinstance(block, BlockQuote):
            q = sub(parent, "blockquote")
            render_blocks(q, block.blocks, ctx)
            first = False
        elif isinstance(block, Epigraph):
            q = sub(parent, "blockquote")
            q.set("class", "epigraph")
            render_blocks(q, block.blocks, ctx)
            if block.attribution:
                a = sub(q, "p")
                a.set("class", "attribution")
                render_inlines(a, block.attribution, ctx)
            first = True
        elif isinstance(block, Verse):
            _verse(parent, block, ctx)
            first = False
        elif isinstance(block, Image):
            _image(parent, block, ctx)
            first = True
        else:  # pragma: no cover
            raise TypeError(f"Unknown block {block!r}")


def _paragraph(parent: etree._Element, block: Paragraph, ctx: RenderContext, first: bool) -> None:
    p = sub(parent, "p")
    classes = []
    if first:
        classes.append("first")
    if block.align is Align.CENTRE:
        classes.append("centre")
    elif block.align is Align.END:
        classes.append("right")
    if classes:
        p.set("class", " ".join(classes))
    render_inlines(p, block.content, ctx)


def _verse(parent: etree._Element, block: Verse, ctx: RenderContext) -> None:
    div = sub(parent, "div")
    div.set("class", "verse")
    for stanza in block.stanzas:
        s = sub(div, "div")
        s.set("class", "stanza")
        for line in stanza:
            render_inlines(sub(s, "p"), line, ctx)


def _image(parent: etree._Element, block: Image, ctx: RenderContext) -> None:
    href = ctx.image_href.get(block.resource_id)
    if href is None:
        raise ConversionError(
            "image-missing", f"Image refers to unknown resource {block.resource_id!r}."
        )
    if block.alt is None:
        ctx.diag.warn(
            "image-no-alt",
            f'Image {block.resource_id!r} has no alt text; written with alt="". '
            "Add a description before claiming accessibility conformance.",
            block.source,
        )
    if block.caption:
        container = sub(parent, "figure")
    else:
        container = sub(parent, "div")
        container.set("class", "image")
    img = sub(container, "img")
    img.set("src", href)
    img.set("alt", block.alt or "")
    if block.caption:
        render_inlines(sub(container, "figcaption"), block.caption, ctx)


# --------------------------------------------------------------------------
# Sections and notes


def render_section(
    section: Section,
    number: int,
    matter: str,
    ctx: RenderContext,
    css_href: str,
    note_blocks: dict[str, list[Block]],
) -> bytes:
    label = section_label(section, number)
    html, body = new_document(label, ctx.language, css_href)
    body.set(EPUB_TYPE, matter)
    sec = sub(body, "section")
    sem = SECTION_SEMANTICS[section.kind]
    if sem.epub_type:
        sec.set(EPUB_TYPE, sem.epub_type)
    if sem.role:
        sec.set("role", sem.role)
    if section.title:
        sec.set("aria-labelledby", "title")
        h1 = sub(sec, "h1")
        h1.set("id", "title")
        render_inlines(h1, section.title, ctx)
    else:
        sec.set("aria-label", label)
    render_blocks(sec, section.blocks, ctx)
    for note_id in ctx.footnotes_here:
        aside = sub(sec, "aside")
        aside.set(EPUB_TYPE, "footnote")
        aside.set("role", "doc-footnote")
        aside.set("id", ctx.notes.html_id[note_id])
        _note_body(aside, note_id, note_blocks[note_id], ctx)
    return serialise(html)


def _note_body(
    parent: etree._Element, note_id: str, blocks: list[Block], ctx: RenderContext
) -> None:
    """Note content, with a backlink carrying the marker at the start."""
    start = len(parent)
    render_blocks(parent, blocks, ctx)
    target = ctx.notes.first_ref.get(note_id)
    first_p = next((el for el in parent[start:] if el.tag == X + "p"), None)
    if first_p is None:
        first_p = element("p")
        parent.insert(start, first_p)
    marker = f"{ctx.notes.label[note_id]}."
    if target is None:
        # Unreferenced note: keep the marker as plain generated text.
        span = element("span")
        span.set("class", "generated")
        span.text = marker
        el = span
    else:
        file_name, ref_id = target.split("#", 1)
        a = element("a")
        a.set("role", "doc-backlink")
        a.set("href", f"#{ref_id}" if file_name == ctx.file_name else target)
        a.text = marker
        el = a
    el.tail = " " + (first_p.text or "")
    first_p.text = None
    first_p.insert(0, el)


def render_endnotes(
    note_ids: list[str],
    ctx: RenderContext,
    css_href: str,
    note_blocks: dict[str, list[Block]],
) -> bytes:
    html, body = new_document("Notes", ctx.language, css_href)
    body.set(EPUB_TYPE, "backmatter")
    sec = sub(body, "section")
    sec.set(EPUB_TYPE, "endnotes")
    sec.set("role", "doc-endnotes")
    sec.set("aria-labelledby", "title")
    h1 = sub(sec, "h1")
    h1.set("id", "title")
    h1.set("class", "generated")
    h1.text = "Notes"
    ol = sub(sec, "ol")
    ol.set("class", "notes")
    for note_id in note_ids:
        li = sub(ol, "li")
        li.set("id", ctx.notes.html_id[note_id])
        _note_body(li, note_id, note_blocks[note_id], ctx)
    return serialise(html)


def render_cover(image_href: str, alt: str, language: str, css_href: str) -> bytes:
    html, body = new_document("Cover", language, css_href)
    body.set(EPUB_TYPE, "cover")
    div = sub(body, "div")
    div.set("class", "cover")
    img = sub(div, "img")
    img.set("src", image_href)
    img.set("alt", alt)
    return serialise(html)
