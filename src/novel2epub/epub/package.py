"""Package documents: content.opf, nav.xhtml, toc.ncx, container.xml."""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from novel2epub.epub.xhtml import EPUB_TYPE, new_document, page, serialise, sub
from novel2epub.ir import Metadata

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


@dataclass(frozen=True, slots=True)
class ManifestItem:
    id: str
    href: str  # relative to the OPF
    media_type: str
    properties: str | None = None


@dataclass(frozen=True, slots=True)
class TocEntry:
    label: str
    href: str  # relative to the OPF
    depth: int = 0


def _nest(entries: list[TocEntry]) -> list[tuple[TocEntry, list[TocEntry]]]:
    """Group depth-1 entries under the depth-0 entry before them."""
    out: list[tuple[TocEntry, list[TocEntry]]] = []
    for e in entries:
        if e.depth > 0 and out:
            out[-1][1].append(e)
        else:
            out.append((e, []))
    return out


@dataclass(frozen=True, slots=True)
class Accessibility:
    has_images: bool
    all_images_have_alt: bool


def container_xml(opf_path: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "<rootfiles>\n"
        f'<rootfile full-path="{opf_path}" media-type="application/oebps-package+xml"/>\n'
        "</rootfiles>\n"
        "</container>\n"
    ).encode()


def _opf(tag: str) -> str:
    return f"{{{OPF_NS}}}{tag}"


def _dc(parent: etree._Element, tag: str, text: str, id_: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{DC_NS}}}{tag}")
    el.text = text
    if id_:
        el.set("id", id_)
    return el


def _meta(
    parent: etree._Element, prop: str, text: str, refines: str | None = None
) -> etree._Element:
    el = etree.SubElement(parent, _opf("meta"))
    if refines:
        el.set("refines", refines)
    el.set("property", prop)
    el.text = text
    return el


def content_opf(
    meta: Metadata,
    manifest: list[ManifestItem],
    spine: list[str],
    a11y: Accessibility,
    cover_image_id: str | None,
) -> bytes:
    assert meta.identifier is not None and meta.modified is not None
    pkg = etree.Element(_opf("package"), nsmap={None: OPF_NS})
    pkg.set("version", "3.0")
    pkg.set("unique-identifier", "pub-id")
    pkg.set(XML_LANG, meta.language)
    md = etree.SubElement(pkg, _opf("metadata"), nsmap={"dc": DC_NS})

    _dc(md, "identifier", meta.identifier, "pub-id")
    if meta.isbn:
        _dc(md, "identifier", f"urn:isbn:{meta.isbn}", "isbn")
    _dc(md, "title", meta.title, "title")
    _meta(md, "title-type", "main", "#title")
    if meta.subtitle:
        _dc(md, "title", meta.subtitle, "subtitle")
        _meta(md, "title-type", "subtitle", "#subtitle")
    for n, author in enumerate(meta.authors, 1):
        _dc(md, "creator", author, f"author{n}")
        _meta(md, "role", "aut", f"#author{n}").set("scheme", "marc:relators")
    _dc(md, "language", meta.language)
    if meta.publisher:
        _dc(md, "publisher", meta.publisher)
    if meta.date:
        _dc(md, "date", meta.date)
    if meta.description:
        _dc(md, "description", meta.description)
    if meta.rights:
        _dc(md, "rights", meta.rights)
    _meta(md, "dcterms:modified", meta.modified.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if meta.series:
        _meta(md, "belongs-to-collection", meta.series).set("id", "series")
        _meta(md, "collection-type", "series", "#series")
        if meta.series_index:
            _meta(md, "group-position", meta.series_index, "#series")

    _accessibility(md, meta, a11y)
    if cover_image_id:
        # EPUB 2 style hint; still used by some Kindle conversion paths.
        legacy = etree.SubElement(md, _opf("meta"))
        legacy.set("name", "cover")
        legacy.set("content", cover_image_id)

    man = etree.SubElement(pkg, _opf("manifest"))
    for item in manifest:
        el = etree.SubElement(man, _opf("item"))
        el.set("id", item.id)
        el.set("href", item.href)
        el.set("media-type", item.media_type)
        if item.properties:
            el.set("properties", item.properties)

    sp = etree.SubElement(pkg, _opf("spine"))
    sp.set("toc", "ncx")
    for idref in spine:
        etree.SubElement(sp, _opf("itemref")).set("idref", idref)

    _indent(pkg)
    out: bytes = etree.tostring(pkg, xml_declaration=True, encoding="utf-8")
    return out + b"\n"


def _accessibility(md: etree._Element, meta: Metadata, a11y: Accessibility) -> None:
    _meta(md, "schema:accessMode", "textual")
    if a11y.has_images:
        _meta(md, "schema:accessMode", "visual")
    if not a11y.has_images or a11y.all_images_have_alt:
        _meta(md, "schema:accessModeSufficient", "textual")
    if a11y.has_images:
        _meta(md, "schema:accessModeSufficient", "textual,visual")
    features = ["structuralNavigation", "tableOfContents", "readingOrder"]
    features.append("displayTransformability")
    if a11y.has_images and a11y.all_images_have_alt:
        features.append("alternativeText")
    for f in features:
        _meta(md, "schema:accessibilityFeature", f)
    _meta(md, "schema:accessibilityHazard", "none")
    summary = meta.accessibility_summary or (
        "Reflowable text with a navigable table of contents, structured headings, "
        "and a logical reading order. No fonts, sizes, or line spacing are imposed, "
        "so reading-system display settings apply."
        + (
            ""
            if not a11y.has_images or a11y.all_images_have_alt
            else " Some images do not yet have text descriptions."
        )
    )
    _meta(md, "schema:accessibilitySummary", summary)
    if meta.conforms_to:
        _meta(md, "dcterms:conformsTo", meta.conforms_to)
    if meta.certified_by:
        _meta(md, "a11y:certifiedBy", meta.certified_by)


def _indent(el: etree._Element, level: int = 0) -> None:
    pad = "\n" + "  " * (level + 1)
    if len(el):
        el.text = pad
        for child in el:
            _indent(child, level + 1)
            child.tail = pad
        el[-1].tail = "\n" + "  " * level


def _toc_list(parent: etree._Element, toc: list[TocEntry]) -> None:
    ol = sub(parent, "ol")
    ol.set("class", "toc")
    for entry, children in _nest(toc):
        li = sub(ol, "li")
        a = sub(li, "a")
        a.set("href", entry.href)
        a.text = entry.label
        if children:
            inner = sub(li, "ol")
            for child in children:
                ca = sub(sub(inner, "li"), "a")
                ca.set("href", child.href)
                ca.text = child.label


def nav_xhtml(
    title: str,
    language: str,
    css_href: str,
    toc: list[TocEntry],
    landmarks: list[tuple[str, str, str]],  # (epub:type, label, href)
) -> bytes:
    """The EPUB navigation document. Not in the spine: reading systems use it
    for their own contents menus; the visible contents page is separate."""
    html, body = new_document(title, language, css_href)
    nav = sub(body, "nav")
    nav.set(EPUB_TYPE, "toc")
    nav.set("id", "toc")
    nav.set("role", "doc-toc")
    sub(nav, "h1").text = "Contents"
    _toc_list(nav, toc)
    lm = sub(body, "nav")
    lm.set(EPUB_TYPE, "landmarks")
    lm.set("hidden", "hidden")
    sub(lm, "h2").text = "Landmarks"
    ol = sub(lm, "ol")
    for epub_type, label, href in landmarks:
        a = sub(sub(ol, "li"), "a")
        a.set(EPUB_TYPE, epub_type)
        a.set("href", href)
        a.text = label
    return serialise(html)


def contents_xhtml(language: str, css_href: str, toc: list[TocEntry]) -> bytes:
    """The visible contents page in the reading order (Kindle's guidelines ask
    for one). Marked as generated text, which the fidelity check ignores."""
    html, body = new_document("Contents", language, css_href)
    body.set(EPUB_TYPE, "frontmatter")
    sec = sub(page(body), "section")
    sec.set("class", "contents generated")
    sec.set("aria-labelledby", "title")
    h1 = sub(sec, "h1")
    h1.set("id", "title")
    h1.text = "Contents"
    _toc_list(sec, toc)
    return serialise(html)


def toc_ncx(meta: Metadata, toc: list[TocEntry]) -> bytes:
    assert meta.identifier is not None
    ncx = etree.Element(f"{{{NCX_NS}}}ncx", nsmap={None: NCX_NS})
    ncx.set("version", "2005-1")
    ncx.set(XML_LANG, meta.language)
    head = etree.SubElement(ncx, f"{{{NCX_NS}}}head")
    nested = _nest(toc)
    for name, content in (
        ("dtb:uid", meta.identifier),
        ("dtb:depth", "2" if any(children for _, children in nested) else "1"),
        ("dtb:totalPageCount", "0"),
        ("dtb:maxPageNumber", "0"),
    ):
        m = etree.SubElement(head, f"{{{NCX_NS}}}meta")
        m.set("name", name)
        m.set("content", content)
    title = etree.SubElement(ncx, f"{{{NCX_NS}}}docTitle")
    etree.SubElement(title, f"{{{NCX_NS}}}text").text = meta.title
    nav_map = etree.SubElement(ncx, f"{{{NCX_NS}}}navMap")
    order = 0

    def point(parent: etree._Element, entry: TocEntry) -> etree._Element:
        nonlocal order
        order += 1
        el = etree.SubElement(parent, f"{{{NCX_NS}}}navPoint")
        el.set("id", f"navpoint-{order}")
        el.set("playOrder", str(order))
        label = etree.SubElement(el, f"{{{NCX_NS}}}navLabel")
        etree.SubElement(label, f"{{{NCX_NS}}}text").text = entry.label
        etree.SubElement(el, f"{{{NCX_NS}}}content").set("src", entry.href)
        return el

    for entry, children in nested:
        parent = point(nav_map, entry)
        for child in children:
            point(parent, child)
    _indent(ncx)
    out: bytes = etree.tostring(ncx, xml_declaration=True, encoding="utf-8")
    return out + b"\n"
