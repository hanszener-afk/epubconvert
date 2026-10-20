"""Walk WordprocessingML body content into raw paragraphs.

A RawPara holds a flat list of formatted Segments plus the layout signals
(page breaks, rules, borders, images) that structure detection needs.
Everything not carried into the output is logged as a removal, in
document order, so the fidelity check can account for it.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from lxml import etree
from PIL import Image as PILImage

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.docx.package import R_NS, DocxPackage, Rel, W
from novel2epub.docx.styles import ParaProps, Styles
from novel2epub.ir import DocxRef, Resource, SourceRef
from novel2epub.normalise.htmltags import interpret_tags
from novel2epub.normalise.inline import Segment

IDX_ATTR = "{urn:novel2epub}idx"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
VML_O = "{urn:schemas-microsoft-com:office:office}"
VML = "{urn:schemas-microsoft-com:vml}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
R = f"{{{R_NS}}}"
DECORATIVE = "{http://schemas.microsoft.com/office/drawing/2017/decorative}decorative"

SUPPRESSED_FIELDS = {
    "TOC": "toc-field",
    "PAGE": "field-result",
    "NUMPAGES": "field-result",
    "SECTIONPAGES": "field-result",
    "PAGEREF": "field-result",
    "NOTEREF": "field-result",
    "SECTION": "field-result",
    "INDEX": "field-result",
    "TOA": "field-result",
}
CORE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}
EMU_PER_INCH = 914400


@dataclass
class RawImage:
    resource_id: str
    alt: str | None
    cx: int
    cy: int


@dataclass
class RawPara:
    segments: list[Segment]
    props: ParaProps
    source: SourceRef
    break_before: bool = False
    break_after: bool = False
    hr: bool = False
    images: list[RawImage] = field(default_factory=list)
    suppressed: bool = False  # some content was removed (fields, hidden text)
    in_toc: bool = False
    sizes: list[int] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments if s.kind == "text")

    @property
    def has_noteref(self) -> bool:
        return any(s.kind == "noteref" for s in self.segments)

    def visible_segments(self) -> list[Segment]:
        return [s for s in self.segments if s.kind != "text" or s.text.strip()]

    def all_have(self, flag: str) -> bool:
        segs = [s for s in self.segments if s.kind == "text" and s.text.strip()]
        return bool(segs) and all(flag in s.fmt for s in segs)


@dataclass
class _Field:
    instr: str = ""
    phase: str = "instr"
    removal: str | None = None
    link: str | None = None

    def classify(self) -> None:
        words = self.instr.split()
        name = words[0].upper() if words else ""
        self.removal = SUPPRESSED_FIELDS.get(name)
        if name == "HYPERLINK":
            m = re.search(r'HYPERLINK\s+"([^"]+)"', self.instr)
            if m and "\\l" not in self.instr.split('"')[0]:
                self.link = m.group(1)


class ImageStore:
    """Images referenced from the document, converted to EPUB-core formats."""

    def __init__(self, pkg: DocxPackage, diag: Diagnostics) -> None:
        self.pkg = pkg
        self.diag = diag
        self.by_part: dict[str, str] = {}
        self.resources: dict[str, Resource] = {}

    def get(self, rel: Rel | None, source: SourceRef) -> str:
        if rel is None:
            raise ConversionError("image-missing", f"An image at {source} has no image data.")
        if rel.external:
            raise ConversionError(
                "image-linked",
                f"The image at {source} is linked to {rel.target} rather than embedded. "
                "Embed the image in the document and try again.",
            )
        if rel.target in self.by_part:
            return self.by_part[rel.target]
        data = self.pkg.read(rel.target)
        rid = f"img{len(self.by_part) + 1}"
        if rel.target.lower().endswith(".svg"):
            media_type = "image/svg+xml"
        else:
            data, media_type = self._convert(data, rel.target, source)
        self.by_part[rel.target] = rid
        self.resources[rid] = Resource(rid, media_type, data)
        return rid

    def _convert(self, data: bytes, part: str, source: SourceRef) -> tuple[bytes, str]:
        try:
            with PILImage.open(io.BytesIO(data)) as im:
                fmt = im.format or ""
                if fmt in CORE_FORMATS:
                    return data, CORE_FORMATS[fmt]
                im.load()
                out = io.BytesIO()
                converted = im.convert("RGBA") if im.mode not in ("RGB", "RGBA", "L", "LA") else im
                converted.save(out, format="PNG", optimize=False)
        except (OSError, ValueError) as exc:
            ext = part.rsplit(".", 1)[-1].upper()
            hint = (
                " EMF and WMF images can only be converted on Windows."
                if ext in ("EMF", "WMF")
                else ""
            )
            raise ConversionError(
                "image-unsupported",
                f"The image {part} at {source} ({ext}) cannot be converted to PNG: {exc}.{hint} "
                "Replace it with a PNG or JPEG in the manuscript.",
            ) from exc
        self.diag.info("image-converted", f"Converted {part} ({fmt}) to PNG.", source)
        return out.getvalue(), "image/png"


class Walker:
    def __init__(
        self,
        part: str,
        rels: dict[str, Rel],
        styles: Styles,
        images: ImageStore,
        diag: Diagnostics,
        html_tags: bool,
        note_prefix: dict[str, str],
    ) -> None:
        self.part = part
        self.rels = rels
        self.styles = styles
        self.images = images
        self.diag = diag
        self.html_tags = html_tags
        self.note_prefix = note_prefix  # "footnoteReference" -> "fn", ...
        self.fields: list[_Field] = []
        self.custom_labels: dict[str, str] = {}  # note id -> custom mark text
        self.counts: dict[str, int] = {}

    def _once(self, code: str, message: str, source: SourceRef) -> None:
        self.counts[code] = self.counts.get(code, 0) + 1
        if self.counts[code] == 1:
            self.diag.warn(code, message, source)

    # ----------------------------------------------------------------- blocks

    def blocks(self, container: etree._Element, in_toc: bool = False) -> list[RawPara]:
        out: list[RawPara] = []
        for child in container:
            tag = child.tag
            if tag == f"{W}p":
                out.extend(self.paragraph(child, in_toc))
            elif tag == f"{W}tbl":
                first = child.find(f".//{W}p")
                ref = self._ref(first) if first is not None else DocxRef(self.part, 0)
                self.diag.warn(
                    "table-flattened",
                    "A table was flattened into paragraphs; tables are not supported in v1.",
                    ref,
                )
                for cell in child.iter(f"{W}tc"):
                    out.extend(self.blocks(cell, in_toc))
            elif tag == f"{W}sdt":
                gallery = child.find(f"{W}sdtPr/{W}docPartObj/{W}docPartGallery")
                toc = in_toc or (
                    gallery is not None and "contents" in (gallery.get(f"{W}val") or "").lower()
                )
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    out.extend(self.blocks(content, toc))
            elif tag == f"{W}customXml":
                out.extend(self.blocks(child, in_toc))
        return out

    def _ref(self, p: etree._Element) -> DocxRef:
        return DocxRef(self.part, int(p.get(IDX_ATTR, "0")))

    # -------------------------------------------------------------- paragraph

    def paragraph(self, p: etree._Element, in_toc: bool) -> list[RawPara]:
        ppr = p.find(f"{W}pPr")
        props = self.styles.para_props(ppr)
        ref = self._ref(p)
        para = RawPara([], props, ref, break_before=props.page_break_before, in_toc=in_toc)
        self._para = para
        self._seen_text = False
        self._pending_break = False
        self._extra: list[RawPara] = []
        self._removal_kind: str | None = None
        self._removal_text: list[str] = []
        self._removal_context = ""
        self._consume_label: str | None = None
        for child in p:
            self._inline(child, link=None)
        self._flush_removal()
        if self._pending_break:
            para.break_after = True
        if ppr is not None:
            sect = ppr.find(f"{W}sectPr")
            if sect is not None:
                kind = sect.find(f"{W}type")
                if kind is None or kind.get(f"{W}val") in ("nextPage", "oddPage", "evenPage"):
                    para.break_after = True
        if self.html_tags and any(s.kind == "text" and "<" in s.text for s in para.segments):
            para.segments = interpret_tags(para.segments, self.diag, ref)
        return [para, *self._extra]

    def _flush_removal(self) -> None:
        if self._removal_kind and "".join(self._removal_text):
            self.diag.removed(
                self._removal_kind,
                "".join(self._removal_text),
                self._para.source,
                self._removal_context,
            )
            self._para.suppressed = True
        self._removal_kind = None
        self._removal_text = []

    def _remove(self, kind: str, text: str) -> None:
        if self._removal_kind != kind:
            self._flush_removal()
            self._removal_kind = kind
            self._removal_context = self._para.text
        self._removal_text.append(text)

    def _suppression(self) -> str | None:
        for f in self.fields:
            if f.phase == "result" and f.removal:
                return f.removal
        return None

    def _field_link(self) -> str | None:
        for f in reversed(self.fields):
            if f.phase == "result" and f.link:
                return f.link
        return None

    # ----------------------------------------------------------------- inline

    def _inline(self, el: etree._Element, link: str | None) -> None:
        tag = el.tag
        if not isinstance(tag, str):
            return
        if tag == f"{W}r":
            self._run(el, link)
        elif tag == f"{W}hyperlink":
            rid = el.get(f"{R}id")
            rel = self.rels.get(rid or "")
            href = rel.target if rel is not None and rel.external else None
            for child in el:
                self._inline(child, href or link)
        elif tag == f"{W}fldSimple":
            f = _Field(instr=el.get(f"{W}instr", ""), phase="result")
            f.classify()
            self.fields.append(f)
            for child in el:
                self._inline(child, link)
            self.fields.pop()
        elif tag in (
            f"{W}smartTag",
            f"{W}customXml",
            f"{W}dir",
            f"{W}bdo",
            f"{W}ins",
            f"{W}moveTo",
        ):
            for child in el:
                self._inline(child, link)
        elif tag == f"{W}sdt":
            content = el.find(f"{W}sdtContent")
            if content is not None:
                for child in content:
                    self._inline(child, link)
        elif tag == f"{MC}AlternateContent":
            choice = el.find(f"{MC}Choice")
            if choice is not None:
                for child in choice:
                    self._inline(child, link)

    def _run(self, r: etree._Element, link: str | None) -> None:
        rp = self.styles.run_props(self._para.props.style_id, r.find(f"{W}rPr"))
        fmt = set()
        if rp.italic:
            fmt.add("italic")
        if rp.bold:
            fmt.add("bold")
        if rp.small_caps:
            fmt.add("smallcaps")
        elif rp.caps:
            fmt.add("allcaps")
        if rp.vert == "superscript":
            fmt.add("superscript")
        elif rp.vert == "subscript":
            fmt.add("subscript")
        frozen = frozenset(fmt)
        link = self._field_link() or link
        for child in r:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            name = tag[len(W) :] if tag.startswith(W) else None
            if name == "t":
                self._text(child.text or "", frozen, link, rp.hidden, rp.size)
            elif name in ("tab", "ptab"):
                self._text("\t", frozen, link, rp.hidden, rp.size)
            elif name == "noBreakHyphen":
                self._text("\u2011", frozen, link, rp.hidden, rp.size)
            elif name == "softHyphen":
                self._text("\u00ad", frozen, link, rp.hidden, rp.size)
            elif name == "sym":
                self._symbol(child, frozen, link, rp.hidden, rp.size)
            elif name == "br":
                kind = child.get(f"{W}type")
                if kind == "page":
                    if self._seen_text:
                        self._pending_break = True
                    else:
                        self._para.break_before = True
                elif kind != "column" and not self._suppression() and not rp.hidden:
                    self._para.segments.append(Segment("", frozen, link, "break"))
            elif name == "cr":
                self._para.segments.append(Segment("", frozen, link, "break"))
            elif name in ("footnoteReference", "endnoteReference"):
                nid = f"{self.note_prefix[name]}{child.get(f'{W}id')}"
                self._para.segments.append(Segment("", frozenset(), None, "noteref", nid))
                if child.get(f"{W}customMarkFollows") in ("1", "true", "on"):
                    self._consume_label = nid
            elif name == "fldChar":
                self._field_char(child.get(f"{W}fldCharType", ""))
            elif name == "instrText":
                if self.fields and self.fields[-1].phase == "instr":
                    self.fields[-1].instr += child.text or ""
            elif name == "drawing":
                self._drawing(child)
            elif name == "pict" or name == "object":
                self._vml(child, embedded=name == "object")
            elif name == "ruby":
                base = child.find(f"{W}rubyBase")
                if base is not None:
                    for sub in base:
                        self._inline(sub, link)
            elif tag == f"{MC}AlternateContent":
                choice = child.find(f"{MC}Choice")
                if choice is not None:
                    for sub in choice:
                        if sub.tag == f"{W}drawing":
                            self._drawing(sub)
                        elif sub.tag == f"{W}pict":
                            self._vml(sub, embedded=False)

    def _text(
        self, text: str, fmt: frozenset[str], link: str | None, hidden: bool, size: int | None
    ) -> None:
        if not text:
            return
        if self._consume_label is not None:
            nid = self._consume_label
            self._consume_label = None
            label = text.strip()
            if label:
                self.custom_labels[nid] = label
                self._remove("note-mark", text)
                return
        suppress = self._suppression()
        if suppress:
            self._remove(suppress, text)
            return
        if hidden:
            self._remove("hidden-text", text)
            return
        self._flush_removal()
        if text.strip():
            if self._pending_break:
                self.diag.info(
                    "page-break-mid-paragraph",
                    "A page break inside a paragraph was ignored.",
                    self._para.source,
                )
                self._pending_break = False
            self._seen_text = True
            if size:
                self._para.sizes.append(size)
        self._para.segments.append(Segment(text, fmt, link))

    def _symbol(
        self,
        el: etree._Element,
        fmt: frozenset[str],
        link: str | None,
        hidden: bool,
        size: int | None,
    ) -> None:
        code = el.get(f"{W}char", "")
        font = el.get(f"{W}font", "")
        try:
            value = int(code, 16)
        except ValueError:
            return
        if 0xF000 <= value <= 0xF0FF:
            self._once(
                "symbol-font",
                f"A symbol from the {font or 'symbol'} font may not display correctly; check it.",
                self._para.source,
            )
        self._text(chr(value), fmt, link, hidden, size)

    def _field_char(self, kind: str) -> None:
        if kind == "begin":
            self.fields.append(_Field())
        elif kind == "separate" and self.fields:
            self.fields[-1].phase = "result"
            self.fields[-1].classify()
        elif kind == "end" and self.fields:
            self.fields.pop()

    # ------------------------------------------------------------- drawings

    def _textbox(self, content: etree._Element) -> None:
        self._once(
            "text-box",
            "Text box content was placed after the paragraph that anchors it.",
            self._para.source,
        )
        saved = (self._para, self._seen_text, self._pending_break, self._extra,
                 self._removal_kind, self._removal_text, self._consume_label)  # fmt: skip
        paras = self.blocks(content)
        (self._para, self._seen_text, self._pending_break, self._extra,
         self._removal_kind, self._removal_text, self._consume_label) = saved  # fmt: skip
        self._extra.extend(paras)

    def _drawing(self, el: etree._Element) -> None:
        box = el.find(f".//{W}txbxContent")
        if box is not None:
            self._textbox(box)
            return
        blip = el.find(f".//{A}blip")
        if blip is None:
            self._once(
                "drawing-skipped",
                "A drawing that is not a picture or a text box (a chart or shape) was skipped.",
                self._para.source,
            )
            return
        rid = blip.get(f"{R}embed") or blip.get(f"{R}link")
        res = self.images.get(self.rels.get(rid or ""), self._para.source)
        doc_pr = el.find(f".//{WP}docPr")
        alt: str | None = None
        if doc_pr is not None:
            alt = (doc_pr.get("descr") or doc_pr.get("title") or "").strip() or None
            dec = doc_pr.find(f".//{DECORATIVE}")
            if dec is not None and dec.get("val") in ("1", "true"):
                alt = ""
        extent = el.find(f".//{WP}extent")
        cx = int(extent.get("cx", "0")) if extent is not None else 0
        cy = int(extent.get("cy", "0")) if extent is not None else 0
        self._para.images.append(RawImage(res, alt, cx, cy))

    def _vml(self, el: etree._Element, embedded: bool) -> None:
        for shape in el.iter():
            if isinstance(shape.tag, str) and shape.get(f"{VML_O}hr") in ("t", "true"):
                self._para.hr = True
                return
        box = el.find(f".//{W}txbxContent")
        if box is not None:
            self._textbox(box)
            return
        data = el.find(f".//{VML}imagedata")
        if data is not None:
            rid = data.get(f"{R}id") or data.get(f"{VML_O}relid")
            res = self.images.get(self.rels.get(rid or ""), self._para.source)
            alt = (data.get(f"{VML_O}title") or "").strip() or None
            self._para.images.append(RawImage(res, alt, 0, 0))
            if embedded:
                self._once(
                    "embedded-object",
                    "An embedded object was replaced by its preview image.",
                    self._para.source,
                )
            return
        if embedded:
            self._once(
                "embedded-object",
                "An embedded object without a preview image was skipped.",
                self._para.source,
            )
