"""Read an HTML manuscript into the IR.

The page is walked into the same flat paragraph records the DOCX front end
makes, so chapter detection, front and back matter, scene breaks, and typed
contents lists are decided by the same code (docx.structure.Structurer):

- headings h1 to h6 are outline levels; a single h1 above h2 chapters is the
  book's title, set on a title page;
- italic, bold, small caps, and raised text come from tags (<em>, <b>, ...),
  class rules in the page's <style> (Google Docs), and style attributes;
- <hr> is a scene break, unless styled as a page break (Google Docs);
- a link to "#x" whose target links back is a footnote, wherever the notes
  are (Google Docs, markdown-it, Pandoc, hand-written); Google Docs comments
  use the same pattern and are removed and counted, as for Word;
- AO3 downloads: the work's title, author, and summary become metadata, and
  the Archive's own text (tags, "Posted originally on...", "Chapter Text")
  is removed. The author's notes are kept.

Everything taken out of the text is logged as a removal, so the fidelity
check (which reads the page independently) accounts for it.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import lxml.html
from lxml import etree

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.docx.structure import Structurer
from novel2epub.docx.styles import ParaProps
from novel2epub.docx.walker import RawImage, RawPara
from novel2epub.html.css import ClassStyles, alignment, apply_flags, hidden, page_break_before
from novel2epub.images import to_core_image
from novel2epub.ir import Book, HtmlRef, Metadata, Note, NoteKind, Paragraph, Resource
from novel2epub.normalise.inline import Segment, build_inlines

INLINE_FORMAT = {
    "b": "bold", "strong": "bold", "i": "italic", "em": "italic", "cite": "italic",
    "dfn": "italic", "var": "italic", "sup": "superscript", "sub": "subscript",
}  # fmt: skip
BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "pre", "figcaption",
          "caption", "address", "td", "th", "summary", "legend"}  # fmt: skip
CONTAINERS = {
    "html", "body", "div", "section", "article", "main", "header", "footer", "aside",
    "blockquote", "ul", "ol", "dl", "figure", "table", "thead", "tbody", "tfoot", "tr",
    "center", "details", "form", "fieldset", "hgroup", "search", "menu",
}  # fmt: skip
SECTIONING = {"header", "section", "article", "main", "aside", "footer"}
# Not text at all: the independent reference ignores these too.
NOT_TEXT = {"script", "style", "noscript", "template", "head", "title", "meta", "link", "base"}
# Text that is not part of a book (form controls, drawings): removed, and logged.
NOT_BOOK = {"svg", "math", "button", "input", "select", "textarea", "iframe", "object",
            "canvas", "video", "audio", "map", "option"}  # fmt: skip
EMU_PER_PX = 9525
_SPACE = re.compile(r"[ \t\n\r\f]+")
_AO3_SUFFIX = re.compile(r"\s*\[Archive of Our Own\]\s*$")


@dataclass
class HtmlResult:
    book: Book
    source_date: datetime | None


@dataclass
class _Context:
    style_names: tuple[str, ...] = ()
    outline_level: int | None = None
    align: str | None = None
    fmt: frozenset[str] = frozenset()
    pre: bool = False


@dataclass
class _Footnotes:
    ref_note: dict[etree._Element, str] = field(default_factory=dict)  # ref anchor -> note id
    ref_comment: set[etree._Element] = field(default_factory=set)
    notes: dict[str, etree._Element] = field(default_factory=dict)  # note id -> element
    comments: list[etree._Element] = field(default_factory=list)
    backlinks: set[etree._Element] = field(default_factory=set)
    labels: dict[str, str | None] = field(default_factory=dict)

    def skipped(self) -> set[etree._Element]:
        return set(self.notes.values()) | set(self.comments)


def _tag(el: etree._Element) -> str:
    return el.tag.lower() if isinstance(el.tag, str) else ""


_INLINE = {
    "a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data", "del", "dfn", "em", "font",
    "i", "ins", "kbd", "label", "mark", "q", "s", "samp", "small", "span", "strike", "strong",
    "sub", "sup", "time", "tt", "u", "var", "wbr",
}  # fmt: skip


def _text(el: etree._Element) -> str:
    """The text of `el` as shown: block elements and line breaks separate words."""
    out: list[str] = []

    def walk(node: etree._Element) -> None:
        tag = _tag(node)
        if not tag or tag in NOT_TEXT:
            return
        block = tag not in _INLINE
        if block:
            out.append(" ")
        out.append(node.text or "")
        for child in node:
            walk(child)
            out.append(child.tail or "")
        if block:
            out.append(" ")

    walk(el)
    return _SPACE.sub(" ", "".join(out)).strip()


def read_html(path: Path, diag: Diagnostics, chapter_regex: str | None = None) -> HtmlResult:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConversionError("input-unreadable", f"Cannot read {path}: {exc}") from exc
    return read_html_text(_decode(raw, path), path, diag, chapter_regex)


def _decode(raw: bytes, path: Path) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ConversionError("input-encoding", f"{path.name} is not UTF-8 or Windows-1252 text.")


def read_html_text(
    html: str,
    path: Path,
    diag: Diagnostics,
    chapter_regex: str | None = None,
    meta: dict[str, object] | None = None,
) -> HtmlResult:
    """Read HTML given as text; `path` names it and locates its images.

    `meta` (title, authors, language, description) comes from outside the
    page, such as a Markdown front matter block.
    """
    if not html.strip():
        raise ConversionError("input-empty", f"{path.name} is empty.")
    doc = lxml.html.document_fromstring(html)
    reader = _Reader(doc, path, diag)
    reader.meta.update(meta or {})
    return reader.read(chapter_regex)


class _Reader:
    def __init__(self, doc: etree._Element, path: Path, diag: Diagnostics) -> None:
        self.doc = doc
        self.path = path
        self.diag = diag
        self.css = ClassStyles([s.text or "" for s in doc.iter("style")])
        self.paras: list[RawPara] = []
        self.resources: dict[str, Resource] = {}
        self.images_by_src: dict[str, tuple[str, int, int]] = {}
        self.pending_break = False
        self.warned: set[str] = set()
        self.meta: dict[str, object] = {}
        self._open: RawPara | None = None
        self._open_ctx: _Context | None = None

    # ------------------------------------------------------------ helpers

    def ref(self, el: etree._Element) -> HtmlRef:
        """Where `el` came from: its Markdown line if it was rendered, else its HTML line."""
        node: etree._Element | None = el
        while node is not None:
            line = node.get("data-line") if isinstance(node.tag, str) else None
            if line and line.isdigit():
                return HtmlRef(self.path.name, int(line))
            node = node.getparent()
        return HtmlRef(self.path.name, getattr(el, "sourceline", None) or 0)

    def once(self, code: str, message: str) -> None:
        if code not in self.warned:
            self.warned.add(code)
            self.diag.warn(code, message)

    def remove(self, el: etree._Element, kind: str) -> None:
        """Take `el` out of the page, logging its text as removed."""
        text = _text(el)
        if text:
            self.diag.removed(kind, text, self.ref(el))
        el.drop_tree()

    # ------------------------------------------------------------ the read

    def read(self, chapter_regex: str | None) -> HtmlResult:
        body = self.doc.find("body")
        if body is None:
            body = self.doc
        self._ao3(body)
        self._unwrap_redirects()
        notes = self._find_notes()
        title = self._title(body)
        self._mark_title_heading(body)
        self.notes_info = notes
        self.skip = notes.skipped()
        self._container(body, _Context())
        self._flush()

        front, chapters, back = Structurer(self.paras, self.diag, chapter_regex, title).build()
        book_notes = self._convert_notes(notes)
        if notes.comments:
            self.diag.warn("comments-removed", f"Removed {len(notes.comments)} comment(s).")

        authors = self.meta.get("authors") or self._authors()
        language = str(self.meta.get("language") or self.doc.get("lang") or "").strip()
        if not language:
            language = "en"
            self.diag.warn("language-default", "The page declares no language; using 'en'.")
        meta = Metadata(title=title, authors=list(authors), language=language)  # type: ignore[call-overload]
        if self.meta.get("description"):
            meta.description = str(self.meta["description"])
        book = Book(meta, chapters, front, back, book_notes, self.resources)
        modified = datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC).replace(microsecond=0)
        return HtmlResult(book, modified)

    # ------------------------------------------------------------ metadata

    def _title(self, body: etree._Element) -> str:
        if self.meta.get("title"):
            return str(self.meta["title"])
        head_title = self.doc.find(".//title")
        if head_title is not None and _text(head_title):
            return _AO3_SUFFIX.sub("", _text(head_title))
        h1s = list(body.iter("h1"))
        if len(h1s) == 1 and _text(h1s[0]):
            return _text(h1s[0])
        styled = [p for p in body.iter("p") if "title" in (p.get("class") or "").split()]
        if styled and _text(styled[0]):
            return _text(styled[0])
        self.diag.warn(
            "title-from-filename",
            f"The page has no title; using the file name {self.path.stem!r}. "
            "Set the title with --title or in the metadata file.",
        )
        return self.path.stem

    def _authors(self) -> list[str]:
        for m in self.doc.iter("meta"):
            if (m.get("name") or "").lower() in ("author", "dc.creator", "dcterms.creator"):
                content = m.get("content") or ""
                return [a.strip() for a in content.split(";") if a.strip()]
        return []

    def _mark_title_heading(self, body: etree._Element) -> None:
        """A single h1 above h2 chapters is the book's title, not a chapter."""
        h1s = list(body.iter("h1"))
        if len(h1s) == 1 and any(True for _ in body.iter("h2")):
            h1s[0].set("data-n2e-title", "1")

    # ------------------------------------------------------------ AO3

    def _ao3(self, body: etree._Element) -> None:
        preface = body.find(".//div[@id='preface']")
        if preface is None or not any(
            "archiveofourown" in (a.get("href") or "") for a in preface.iter("a")
        ):
            return
        h1 = preface.find(".//h1")
        if h1 is not None:
            self.meta["title"] = _text(h1)
        authors = [_text(a) for a in preface.iter("a") if a.get("rel") == "author"]
        if authors:
            self.meta["authors"] = authors
        for p in list(preface.iter("p")):
            if _text(p) == "Summary":
                quote = p.getnext()
                if quote is not None and _tag(quote) == "blockquote":
                    paras = [_text(q) for q in quote.iter("p")] or [_text(quote)]
                    self.meta["description"] = "\n".join(t for t in paras if t)
                    self.remove(p, "ao3-summary")
                    self.remove(quote, "ao3-summary")
        targets: list[tuple[etree._Element, str]] = []
        for el in body.iter():
            tag, cls = _tag(el), (el.get("class") or "").split()
            if tag == "p" and "message" in cls:
                targets.append((el, "ao3-boilerplate"))
            elif tag == "dl" and "tags" in cls:
                targets.append((el, "ao3-tags"))
            elif el is h1 or (tag == "div" and "byline" in cls):
                targets.append((el, "ao3-preface"))
            elif tag == "h3" and "landmark" in cls:
                targets.append((el, "ao3-landmark"))
        for el, kind in targets:
            self.remove(el, kind)
        self.diag.decided(
            "source", "Recognised an Archive of Our Own download.", 0.95, "ao3", self.ref(body)
        )

    def _unwrap_redirects(self) -> None:
        """Google Docs wraps every link in google.com/url?q=...; keep the real address."""
        for a in self.doc.iter("a"):
            href = a.get("href") or ""
            parsed = urlparse(href)
            if parsed.netloc.endswith("google.com") and parsed.path == "/url":
                real = parse_qs(parsed.query).get("q")
                if real:
                    a.set("href", real[0])

    # ------------------------------------------------------------ footnotes

    def _find_notes(self) -> _Footnotes:
        found = _Footnotes()
        ids = {el.get("id"): el for el in self.doc.iter() if el.get("id")}
        order = {el: n for n, el in enumerate(self.doc.iter())}
        for a in list(self.doc.iter("a")):
            href = a.get("href") or ""
            if not href.startswith("#") or len(href) < 2:
                continue
            target = ids.get(href[1:])
            if target is None:
                continue
            note = _note_element(target)
            # A note follows its reference; this also stops the note's own backlink
            # from making the paragraph it points back to look like a note.
            if note is None or _inside(a, note) or order[note] < order[a]:
                continue
            ref_ids = {a.get("id")}
            parent = a.getparent()
            if parent is not None and _tag(parent) == "sup":
                ref_ids.add(parent.get("id"))
            ref_ids.discard(None)
            backs = [b for b in note.iter("a") if (b.get("href") or "")[1:] in ref_ids]
            if not backs:
                continue
            found.backlinks.update(backs)
            if href[1:].startswith("cmnt"):
                found.ref_comment.add(a)
                if note not in found.comments:
                    found.comments.append(note)
                continue
            nid = f"hn{len(found.notes) + 1}"
            found.notes[nid] = note
            found.ref_note[a] = nid
            label = _text(a).strip("[]() ")
            found.labels[nid] = None if label.isdigit() else (label or None)
        return found

    def _convert_notes(self, found: _Footnotes) -> dict[str, Note]:
        notes: dict[str, Note] = {}
        for nid, el in found.notes.items():
            self.paras = []
            self._open = None
            self.skip = set()
            self._container(el, _Context()) if _tag(el) in CONTAINERS | {"li", "dd"} else (
                self._block(el, _Context())
            )
            self._flush()
            blocks = [
                Paragraph(build_inlines(p.segments), source=p.source)
                for p in self.paras
                if p.text.strip()
            ]
            notes[nid] = Note(nid, NoteKind.FOOTNOTE, blocks, self.ref(el), found.labels[nid])  # type: ignore[arg-type]
        for el in found.comments:
            self.diag.removed("comment", _text(el), self.ref(el))
        return notes

    # ------------------------------------------------------------ blocks

    def _start(self, ctx: _Context, el: etree._Element) -> RawPara:
        if self._open is None:
            props = ParaProps(
                None, ctx.style_names, ctx.outline_level, ctx.align, self.pending_break,
                False, False,
            )  # fmt: skip
            self._open = RawPara([], props, self.ref(el), break_before=self.pending_break)
            self._open_ctx = ctx
            self.pending_break = False
        return self._open

    def _flush(self) -> None:
        para = self._open
        self._open = None
        if para is None:
            return
        _tidy_spaces(para.segments)
        if para.text.strip() or para.images or para.has_noteref or para.hr:
            self.paras.append(para)
        elif para.segments or para.props.style_names:
            self.paras.append(para)  # an empty paragraph: spacing, decided by the Structurer

    def _container(self, el: etree._Element, ctx: _Context) -> None:
        if el.text:
            self._inline_text(el.text, ctx, el)
        for child in el:
            self._child(child, ctx)
            if child.tail:
                self._inline_text(child.tail, ctx, el)
        self._flush()

    def _child(self, child: etree._Element, ctx: _Context) -> None:
        tag = _tag(child)
        if not tag or child in self.skip:
            return
        decls = self.css.for_element(child.get("class"), child.get("style"))
        if tag in NOT_TEXT:
            return
        if tag == "hr":
            self._flush()
            if page_break_before(decls) or hidden(decls):
                self.pending_break = True
            elif not _before_notes(child, self.skip) and "footnotes-sep" not in (
                child.get("class") or ""
            ):
                props = ParaProps(None, (), None, "center", False, False, False)
                self.paras.append(RawPara([], props, self.ref(child), hr=True))
            return
        if hidden(decls):
            self._flush()
            self.remove_text(child, "hidden-text")
            return
        if tag in NOT_BOOK:
            self._flush()
            self.remove_text(child, "non-text-element")
            self.once("non-text-element", "Removed drawings or form controls; see the report.")
            return
        if tag == "nav":
            self._flush()
            self.remove_text(child, "contents-list")
            return
        if tag in BLOCKS:
            self._flush()
            self._block(child, ctx, decls)
            return
        if tag in CONTAINERS:
            self._flush()
            if tag == "table":
                self.once("table-flattened", "A table was flattened into paragraphs.")
            if page_break_before(decls) or (tag in SECTIONING and not ctx.style_names):
                self.pending_break = True
            inner = _Context(
                ("quote",) if tag == "blockquote" else ctx.style_names,
                None,
                alignment(decls) or ("center" if tag == "center" else ctx.align),
                apply_flags(ctx.fmt, decls),
                ctx.pre,
            )
            self._container(child, inner)
            return
        self._inline(child, ctx, ctx.fmt, None, decls)

    def remove_text(self, el: etree._Element, kind: str) -> None:
        text = _text(el)
        if text:
            self.diag.removed(kind, text, self.ref(el))

    def _block(
        self, el: etree._Element, ctx: _Context, decls: dict[str, str] | None = None
    ) -> None:
        if decls is None:
            decls = self.css.for_element(el.get("class"), el.get("style"))
        tag = _tag(el)
        classes = (el.get("class") or "").split()
        names = ctx.style_names
        level = None
        if re.fullmatch(r"h[1-6]", tag):
            if el.get("data-n2e-title"):
                names = ("title",)
            else:
                level = int(tag[1]) - 1
                names = (f"heading {tag[1]}",)
        elif "title" in classes:
            names = ("title",)
        elif "subtitle" in classes:
            names = ("subtitle",)
        if page_break_before(decls):
            self.pending_break = True
        inner = _Context(
            names, level, alignment(decls) or ctx.align, apply_flags(ctx.fmt, decls),
            ctx.pre or tag == "pre",
        )  # fmt: skip
        self._container(el, inner)

    # ------------------------------------------------------------ inline

    def _inline_text(self, text: str, ctx: _Context, el: etree._Element) -> None:
        if not ctx.pre:
            text = _SPACE.sub(" ", text)
            if not text.strip() and self._open is None:
                return
        para = self._start(ctx, el)
        if ctx.pre and "\n" in text:
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if i:
                    para.segments.append(Segment("", ctx.fmt, None, "break"))
                if line:
                    para.segments.append(Segment(line, ctx.fmt, self._link))
            return
        para.segments.append(Segment(text, ctx.fmt, self._link))

    _link: str | None = None

    def _inline(
        self,
        el: etree._Element,
        ctx: _Context,
        fmt: frozenset[str],
        link: str | None,
        decls: dict[str, str] | None = None,
    ) -> None:
        tag = _tag(el)
        if not tag or el in self.skip or tag in NOT_TEXT:
            return
        if decls is None:
            decls = self.css.for_element(el.get("class"), el.get("style"))
        if tag in BLOCKS or tag in CONTAINERS or tag in ("hr", "nav") or tag in NOT_BOOK:
            self._child(el, ctx)  # a block inside a paragraph (lenient HTML)
            return
        if hidden(decls):
            self.remove_text(el, "hidden-text")
            return
        para = self._start(ctx, el)
        if tag == "br":
            para.segments.append(Segment("", fmt, link, "break"))
            return
        if tag == "img":
            self._image(el, para)
            return
        if tag == "a":
            if el in self.notes_info.ref_note:
                context = para.text[-40:]
                if _text(el):
                    self.diag.removed("note-mark", _text(el), self.ref(el), context)
                nid = self.notes_info.ref_note[el]
                para.segments.append(Segment("", fmt, None, "noteref", nid))
                return
            if el in self.notes_info.ref_comment:
                if _text(el):
                    self.diag.removed("comment", _text(el), self.ref(el), para.text[-40:])
                return
            if el in self.notes_info.backlinks:
                if _text(el):
                    self.diag.removed("note-backlink", _text(el), self.ref(el), para.text[-40:])
                return
            href = el.get("href") or ""
            if href.startswith(("http://", "https://", "mailto:")):
                link = href
        if tag in INLINE_FORMAT:
            fmt = fmt | {INLINE_FORMAT[tag]}
        fmt = apply_flags(fmt, decls)
        saved = self._link
        self._link = link
        sub = _Context(ctx.style_names, ctx.outline_level, ctx.align, fmt, ctx.pre)
        if el.text:
            self._inline_text(el.text, sub, el)
        for child in el:
            self._inline(child, sub, fmt, link)
            if child.tail:
                self._inline_text(child.tail, sub, el)
        self._link = saved

    # ------------------------------------------------------------ images

    def _image(self, el: etree._Element, para: RawPara) -> None:
        src = (el.get("src") or "").strip()
        alt = el.get("alt")
        if not src:
            return
        if src not in self.images_by_src:
            data = self._image_data(src, el)
            rid = f"img{len(self.images_by_src) + 1}"
            data, media_type, (w, h) = to_core_image(data, src, str(self.ref(el)))
            self.resources[rid] = Resource(rid, media_type, data)
            self.images_by_src[src] = (rid, w, h)
        rid, w, h = self.images_by_src[src]
        width = _px(el.get("width")) or w
        height = _px(el.get("height")) or h
        para.images.append(RawImage(rid, alt, width * EMU_PER_PX, height * EMU_PER_PX))

    def _image_data(self, src: str, el: etree._Element) -> bytes:
        if src.startswith("data:"):
            header, _, payload = src.partition(",")
            try:
                if header.endswith(";base64"):
                    return base64.b64decode(payload)
                return unquote(payload).encode("latin-1")
            except ValueError as exc:
                raise ConversionError(
                    "image-invalid", f"The image at {self.ref(el)} is not valid data: {exc}"
                ) from exc
        if src.startswith(("http://", "https://", "//")):
            raise ConversionError(
                "image-remote",
                f"The image at {self.ref(el)} is on the web ({src}). novel2epub makes no network "
                "connections: download it into the manuscript's folder and point the page at it.",
            )
        path = self.path.parent / unquote(urlparse(src).path)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ConversionError(
                "image-missing", f"The image {src} at {self.ref(el)} cannot be read: {exc}"
            ) from exc


def _px(value: str | None) -> int | None:
    match = re.fullmatch(r"\s*(\d+)(px)?\s*", value or "")
    return int(match.group(1)) if match else None


def _inside(el: etree._Element, ancestor: etree._Element) -> bool:
    node: etree._Element | None = el
    while node is not None:
        if node is ancestor:
            return True
        node = node.getparent()
    return False


def _note_element(target: etree._Element) -> etree._Element | None:
    """The element holding a note, given the element a reference points to."""
    tag = _tag(target)
    if tag in ("li", "aside", "dd", "div", "p", "section"):
        return target if tag != "section" else None
    node = target.getparent()
    while node is not None and _tag(node) not in ("p", "li", "div", "aside", "dd", "body"):
        node = node.getparent()
    if node is None or _tag(node) == "body":
        return None
    parent = node.getparent()
    if (
        _tag(node) == "p"
        and parent is not None
        and _tag(parent) == "div"
        and all(_tag(c) == "p" for c in parent)
        and parent.getparent() is not None
    ):
        return parent  # Google Docs: one <div> per note, a <p> per paragraph
    return node


def _before_notes(hr: etree._Element, notes: set[etree._Element]) -> bool:
    """An <hr> that only separates the text from the notes is not a scene break."""
    nxt = hr.getnext()
    while nxt is not None and not isinstance(nxt.tag, str):
        nxt = nxt.getnext()
    if nxt is None:
        return False
    return nxt in notes or any(n in notes for n in nxt.iter())


def _tidy_spaces(segments: list[Segment]) -> None:
    """Collapse spaces across segment edges, and trim the paragraph's ends."""
    previous_space = True  # trims the start
    for seg in segments:
        if seg.kind == "break":
            previous_space = True
            continue
        if seg.kind != "text":
            continue
        if previous_space:
            seg.text = seg.text.lstrip(" ")
        if seg.text:
            previous_space = seg.text.endswith(" ")
    for seg in reversed(segments):
        if seg.kind == "text":
            seg.text = seg.text.rstrip(" ")
            if seg.text:
                break
        elif seg.kind == "break":
            continue
        else:
            break
    segments[:] = [s for s in segments if s.kind != "text" or s.text]
