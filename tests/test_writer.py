from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest
from books import full_book, minimal_book
from lxml import etree

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.epub.writer import build_epub, write_epub
from novel2epub.ir import Image, Resource, SectionKind

NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "x": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
}
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"


def build(book=None, diag=None) -> zipfile.ZipFile:
    import io

    data = build_epub(book or full_book(), diag or Diagnostics())
    return zipfile.ZipFile(io.BytesIO(data))


def opf_of(z: zipfile.ZipFile) -> tuple[str, etree._Element]:
    container = etree.fromstring(z.read("META-INF/container.xml"))
    path = container.xpath("//c:rootfile/@full-path", namespaces=NS)[0]
    return path, etree.fromstring(z.read(path))


def spine_docs(z: zipfile.ZipFile) -> list[tuple[str, etree._Element]]:
    path, opf = opf_of(z)
    base = path.rsplit("/", 1)[0] + "/"
    items = {
        i.get("id"): i.get("href") for i in opf.xpath("//opf:manifest/opf:item", namespaces=NS)
    }
    refs = opf.xpath("//opf:spine/opf:itemref/@idref", namespaces=NS)
    # The nav document is tested separately; leaving it out keeps indices stable.
    return [
        (items[r], etree.fromstring(z.read(base + items[r]))) for r in refs if r != "x-contents"
    ]


# --- container and packaging ---------------------------------------------


def test_mimetype_is_first_and_stored() -> None:
    z = build()
    first = z.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED
    assert z.read("mimetype") == b"application/epub+zip"
    assert not first.extra


def test_zip_entries_sorted_after_mimetype() -> None:
    names = [i.filename for i in build().infolist()]
    assert names[1:] == sorted(names[1:])


def test_output_is_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a.epub", tmp_path / "sub" / "b.epub"
    b.parent.mkdir()
    write_epub(full_book(), a, Diagnostics())
    write_epub(full_book(), b, Diagnostics())
    assert a.read_bytes() == b.read_bytes()


def test_zip_timestamps_fixed_from_modified_date() -> None:
    for info in build().infolist():
        assert info.date_time == (2026, 3, 14, 9, 26, 52)  # zip has 2-second resolution


# --- structure -----------------------------------------------------------


def test_one_file_per_section_plus_cover_and_endnotes() -> None:
    docs = [href for href, _ in spine_docs(build())]
    book = full_book()
    expected = 1 + len(book.sections()) + 1  # cover page, sections, endnotes
    assert len(docs) == expected
    assert len(set(docs)) == expected
    assert docs[0].endswith("cover.xhtml")
    assert docs[-1].endswith("endnotes.xhtml")


def test_minimal_book_has_no_cover_or_endnotes_file() -> None:
    names = build(minimal_book()).namelist()
    assert not any("cover" in n for n in names)
    assert not any("endnotes" in n for n in names)


def test_body_matter_types() -> None:
    types = [doc.find("x:body", NS).get(EPUB_TYPE) for _, doc in spine_docs(build())]
    # cover, 4 front, 2 chapters, 2 back, endnotes
    assert types == ["cover"] + ["frontmatter"] * 4 + ["bodymatter"] * 2 + ["backmatter"] * 3


@pytest.mark.parametrize(
    ("index", "epub_type", "role"),
    [
        (1, "titlepage", None),
        (2, "copyright-page", None),
        (3, "dedication", "doc-dedication"),
        (4, "epigraph", None),
        (5, "chapter", "doc-chapter"),
        (7, "acknowledgments", "doc-acknowledgments"),
    ],
)
def test_section_semantics(index: int, epub_type: str, role: str | None) -> None:
    _, doc = spine_docs(build())[index]
    section = doc.find("x:body/x:div/x:section", NS)
    assert section.get(EPUB_TYPE) == epub_type
    assert section.get("role") == role


def test_every_document_has_lang_and_title() -> None:
    for _, doc in spine_docs(build()):
        assert doc.get("{http://www.w3.org/XML/1998/namespace}lang") == "en-GB"
        assert doc.get("lang") == "en-GB"
        assert doc.findtext("x:head/x:title", namespaces=NS).strip()


def test_section_title_is_h1_and_labels_section() -> None:
    _, doc = spine_docs(build())[5]
    section = doc.find("x:body/x:div/x:section", NS)
    h1 = section.find("x:h1", NS)
    assert "".join(h1.itertext()) == "Chapter One: Arrival"
    assert section.get("aria-labelledby") == h1.get("id")


def test_untitled_section_gets_aria_label_not_visible_heading() -> None:
    _, doc = spine_docs(build())[3]  # dedication, no title in source
    section = doc.find("x:body/x:div/x:section", NS)
    assert section.find("x:h1", NS) is None
    assert section.get("aria-label") == "Dedication"


# --- paragraphs and scene breaks -----------------------------------------


def paragraphs(doc: etree._Element) -> list[etree._Element]:
    return doc.findall("x:body/x:div/x:section/x:p", NS)


def test_first_paragraph_after_heading_and_scene_break_has_no_indent_class() -> None:
    _, doc = spine_docs(build())[5]
    ps = paragraphs(doc)
    texts = ["".join(x.itertext()) for x in ps]
    first_flags = {
        t[:12]: "first" in (x.get("class") or "").split() for t, x in zip(texts, ps, strict=True)
    }
    assert first_flags["\u201cYou\u2019re late"] is True  # after chapter heading
    assert first_flags["The Admiralt"] is False
    assert first_flags["Later, she r"] is True  # after scene break
    assert first_flags["Dear Sir,i"] is True  # after sub-heading
    assert first_flags["She folded i"] is False


def test_scene_break_is_visible_without_css() -> None:
    _, doc = spine_docs(build())[5]
    breaks = doc.xpath("//x:*[contains(@class, 'scene-break')]", namespaces=NS)
    assert len(breaks) == 1
    sb = breaks[0]
    assert sb.tag == "{http://www.w3.org/1999/xhtml}p"
    assert sb.get("role") == "separator"
    assert "".join(sb.itertext()).strip() == "* * *"


def test_inline_formatting() -> None:
    _, doc = spine_docs(build())[5]
    html = etree.tostring(doc, encoding="unicode")
    assert "<i>very</i>" in html
    assert '<span class="sc">Admiralty</span>' in html
    assert "<sup>rd</sup>" in html
    assert "<sub>2</sub>" in html
    assert '<a href="https://example.org/">the notice</a>' in html
    assert "soft<br/>line" in html
    assert "Non\u00a0breaking" in html
    assert "\u201cYou\u2019re late,\u201d she said\u2014not unkindly\u2026 " in html


def test_alignment_classes() -> None:
    _, doc = spine_docs(build())[1]
    assert all("centre" in x.get("class").split() for x in paragraphs(doc))


def test_verse_lines_are_blocks() -> None:
    _, doc = spine_docs(build())[6]
    stanzas = doc.xpath("//x:div[@class='verse']/x:div[@class='stanza']", namespaces=NS)
    assert [len(s.findall("x:p", NS)) for s in stanzas] == [2, 1]


def test_epigraph_and_blockquote() -> None:
    _, doc = spine_docs(build())[4]
    epi = doc.xpath("//x:blockquote[@class='epigraph']", namespaces=NS)
    assert len(epi) == 1
    assert epi[0].xpath("x:p[@class='attribution']", namespaces=NS)
    _, ch1 = spine_docs(build())[5]
    assert len(ch1.xpath("//x:blockquote[not(@class)]/x:p", namespaces=NS)) == 2


# --- notes ---------------------------------------------------------------


def test_footnotes_are_asides_in_the_referencing_chapter_with_backlinks() -> None:
    _, doc = spine_docs(build())[5]
    refs = doc.xpath("//x:a[@epub:type='noteref']", namespaces=NS)
    asides = doc.xpath("//x:aside[@epub:type='footnote']", namespaces=NS)
    assert [a.get("id") for a in asides] == ["note-fn1"]
    fn_ref = next(r for r in refs if r.get("href") == "#note-fn1")
    assert fn_ref.get("role") == "doc-noteref"
    back = asides[0].xpath(".//x:a[@role='doc-backlink']", namespaces=NS)
    assert back[0].get("href") == "#" + fn_ref.get("id")


def test_second_reference_to_same_footnote_links_across_files() -> None:
    href1, _ = spine_docs(build())[5]
    _, ch2 = spine_docs(build())[6]
    refs = [r.get("href") for r in ch2.xpath("//x:a[@epub:type='noteref']", namespaces=NS)]
    assert href1.rsplit("/", 1)[-1] + "#note-fn1" in refs
    assert "#note-fn2" in refs


def test_endnotes_in_separate_file() -> None:
    _, doc = spine_docs(build())[-1]
    section = doc.find("x:body/x:div/x:section", NS)
    assert section.get(EPUB_TYPE) == "endnotes"
    assert section.get("role") == "doc-endnotes"
    ids = [li.get("id") for li in section.findall("x:ol/x:li", NS)]
    assert ids == ["note-en1", "note-orphan"]


def test_unreferenced_note_is_kept_and_warned() -> None:
    diag = Diagnostics()
    build(full_book(), diag)
    assert any(i.code == "note-unreferenced" for i in diag.warnings)


# --- images and cover ----------------------------------------------------


def test_images_have_alt_and_missing_alt_is_warned() -> None:
    diag = Diagnostics()
    z = build(full_book(), diag)
    _, ch2 = spine_docs(z)[6]
    imgs = ch2.xpath("//x:img", namespaces=NS)
    assert [i.get("alt") for i in imgs] == ["A map of the headland and the lighthouse.", ""]
    assert any(i.code == "image-no-alt" for i in diag.warnings)
    assert ch2.xpath("//x:figure/x:figcaption", namespaces=NS)


def test_cover_image_marked() -> None:
    _, opf = opf_of(build())
    items = opf.xpath("//opf:item[@properties='cover-image']", namespaces=NS)
    assert len(items) == 1
    assert items[0].get("media-type") == "image/jpeg"


def test_unsupported_media_type_is_rejected() -> None:
    book = minimal_book()
    book.resources["x"] = Resource("x", "image/x-emf", b"\x01\x00")
    book.body[0].blocks.append(Image("x", alt="x"))
    with pytest.raises(ConversionError):
        build(book)


def test_missing_resource_is_rejected() -> None:
    book = minimal_book()
    book.body[0].blocks.append(Image("nope", alt="x"))
    with pytest.raises(ConversionError):
        build(book)


def test_unresolved_metadata_is_rejected() -> None:
    book = minimal_book()
    book.metadata.identifier = None
    with pytest.raises(ConversionError):
        build(book)


# --- OPF -----------------------------------------------------------------


def test_opf_required_metadata() -> None:
    _, opf = opf_of(build())
    assert opf.get("version") == "3.0"
    uid = opf.get("unique-identifier")
    ident = opf.xpath(f"//dc:identifier[@id='{uid}']", namespaces=NS)[0]
    assert ident.text == "urn:uuid:0e8a1a3c-55a4-4c7e-9a8d-6b4b52c3f0a2"
    assert opf.xpath("//dc:language/text()", namespaces=NS) == ["en-GB"]
    assert opf.xpath("//dc:creator/text()", namespaces=NS) == ["Ann Author", "Bea Writer"]
    modified = opf.xpath("//opf:meta[@property='dcterms:modified']/text()", namespaces=NS)
    assert modified == ["2026-03-14T09:26:53Z"]
    assert "urn:isbn:9780000000002" in opf.xpath("//dc:identifier/text()", namespaces=NS)
    assert opf.xpath("//opf:meta[@property='belongs-to-collection']/text()", namespaces=NS) == [
        "Coastal Tales"
    ]


def test_accessibility_metadata_present_but_no_conformance_claim_by_default() -> None:
    _, opf = opf_of(build())
    props = opf.xpath("//opf:meta/@property", namespaces=NS)
    for required in (
        "schema:accessMode",
        "schema:accessModeSufficient",
        "schema:accessibilityFeature",
        "schema:accessibilityHazard",
        "schema:accessibilitySummary",
    ):
        assert required in props
    assert "dcterms:conformsTo" not in props
    features = opf.xpath(
        "//opf:meta[@property='schema:accessibilityFeature']/text()", namespaces=NS
    )
    assert "alternativeText" not in features  # one image lacks alt text


def test_publisher_supplied_conformance_claim_is_passed_through() -> None:
    book = minimal_book()
    book.metadata.conforms_to = "EPUB Accessibility 1.1 - WCAG 2.1 Level AA"
    book.metadata.certified_by = "Small Press"
    _, opf = opf_of(build(book))
    claims = opf.xpath("//opf:meta[@property='dcterms:conformsTo']/text()", namespaces=NS)
    assert claims == ["EPUB Accessibility 1.1 - WCAG 2.1 Level AA"]


def test_nav_has_toc_and_landmarks() -> None:
    z = build()
    path, opf = opf_of(z)
    nav_href = opf.xpath("//opf:item[@properties='nav']/@href", namespaces=NS)[0]
    nav = etree.fromstring(z.read(path.rsplit("/", 1)[0] + "/" + nav_href))
    toc = nav.xpath("//x:nav[@epub:type='toc']//x:a/text()", namespaces=NS)
    assert "Chapter One: Arrival" in toc
    assert "Dedication" in toc
    landmarks = nav.xpath("//x:nav[@epub:type='landmarks']//x:a/@epub:type", namespaces=NS)
    assert {"cover", "bodymatter", "toc"} <= set(landmarks)
    spine = opf.xpath("//opf:spine/opf:itemref/@idref", namespaces=NS)
    assert "nav" not in spine  # its landmarks must never be shown as a page
    assert spine.index("x-contents") == 1 + len(full_book().front)  # after cover and front matter


def test_contents_lists_are_classed_for_styling() -> None:
    z = build()
    contents = etree.fromstring(z.read("OEBPS/Text/contents.xhtml"))
    lists = contents.xpath("//x:ol", namespaces=NS)
    assert lists and all(
        ol.get("class") == "toc" for ol in lists if ol.getparent().tag.endswith("section")
    )
    page = contents.find("x:body/x:div", NS)
    assert page.get("class") == "page" and page[0].get("class") == "chapter-start"


def test_ncx_present_and_matches_identifier() -> None:
    z = build()
    path, opf = opf_of(z)
    ncx_href = opf.xpath("//opf:item[@media-type='application/x-dtbncx+xml']/@href", namespaces=NS)
    ncx = etree.fromstring(z.read(path.rsplit("/", 1)[0] + "/" + ncx_href[0]))
    uid = ncx.xpath("//ncx:meta[@name='dtb:uid']/@content", namespaces=NS)
    assert uid == ["urn:uuid:0e8a1a3c-55a4-4c7e-9a8d-6b4b52c3f0a2"]
    # Every section, plus the contents page and the endnotes.
    assert len(ncx.xpath("//ncx:navPoint", namespaces=NS)) == len(full_book().sections()) + 2


# --- CSS and source readability ------------------------------------------


def test_stylesheet_respects_reader_settings() -> None:
    z = build()
    css = next(z.read(n).decode() for n in z.namelist() if n.endswith(".css"))
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for banned in (
        "font-family",
        "font-size",
        "line-height",
        "@font-face",
        "position",
        "float",
        "display: none",
        "::before",
        "::after",
        "var(",
        "flex",
        "grid",
    ):
        assert banned not in body, banned
    assert not re.search(r"\d(px|pt|rem|vw|vh|cm|mm|in)\b", body)
    assert re.search(r"(^|\})\s*p\s*\{[^}]*text-indent", body)


def test_block_elements_start_on_their_own_line() -> None:
    z = build()
    name = next(n for n in z.namelist() if "chapter" in n)
    text = z.read(name).decode()
    for line in text.splitlines():
        assert line.count("<p") <= 1, line


def test_section_kinds_cover_every_enum_value() -> None:
    from novel2epub.epub.xhtml import SECTION_SEMANTICS

    assert set(SECTION_SEMANTICS) == set(SectionKind)


def test_parts_nest_chapters_in_nav_and_ncx() -> None:
    from books import parts_book

    z = build(parts_book())
    path, opf = opf_of(z)
    base = path.rsplit("/", 1)[0] + "/"
    nav_href = opf.xpath("//opf:item[@properties='nav']/@href", namespaces=NS)[0]
    nav = etree.fromstring(z.read(base + nav_href))
    top = nav.xpath("//x:nav[@epub:type='toc']/x:ol/x:li/x:a/text()", namespaces=NS)
    assert top == ["Contents", "Prologue", "Part One", "Part Two", "Epilogue"]
    nested = nav.xpath(
        "//x:nav[@epub:type='toc']/x:ol/x:li[x:a='Part One']/x:ol/x:li/x:a/text()", namespaces=NS
    )
    assert nested == ["1. The Ditch", "2. Registration"]
    ncx_href = opf.xpath("//opf:item[@media-type='application/x-dtbncx+xml']/@href", namespaces=NS)
    ncx = etree.fromstring(z.read(base + ncx_href[0]))
    assert ncx.xpath("//ncx:meta[@name='dtb:depth']/@content", namespaces=NS) == ["2"]
    assert len(ncx.xpath("//ncx:navMap/ncx:navPoint", namespaces=NS)) == 5
    orders = [int(o) for o in ncx.xpath("//ncx:navPoint/@playOrder", namespaces=NS)]
    assert sorted(orders) == list(range(1, 9))
    part = [
        doc
        for _, doc in spine_docs(z)
        if doc.find("x:body/x:div/x:section", NS).get(EPUB_TYPE) == "part"
    ]
    assert len(part) == 2 and part[0].find("x:body/x:div/x:section", NS).get("role") == "doc-part"


def test_opener_is_rendered_before_the_title_and_counted_in_order() -> None:
    import io

    from books import minimal_book, p

    from novel2epub.ir import BlockQuote
    from novel2epub.verify.text import epub_text, ir_text

    book = minimal_book()
    book.body[0].opener = [BlockQuote([p("A notice pinned above the chapter.")])]
    data = build_epub(book, Diagnostics())
    z = zipfile.ZipFile(io.BytesIO(data))
    _, doc = spine_docs(z)[0]
    section = doc.find("x:body/x:div/x:section", NS)
    assert [etree.QName(c).localname for c in section][:2] == ["blockquote", "h1"]
    assert epub_text(data).main == ir_text(book).main
    assert epub_text(data).main.startswith("A notice pinned above the chapter. Chapter One")


def test_title_page_has_a_real_title_and_a_class() -> None:
    from books import minimal_book, p

    from novel2epub.ir import Heading, Section, Text

    book = minimal_book()
    book.front = [
        Section(SectionKind.TITLEPAGE, None, [Heading([Text("A Small Book")], 1), p("Ann Author")])
    ]
    z = build(book)
    _, doc = spine_docs(z)[0]
    section = doc.find("x:body/x:div/x:section", NS)
    assert section.get("class") == "titlepage"
    assert section.find("x:h1", NS).text == "A Small Book"


def test_reader_contents_lists_the_contents_page_in_reading_order() -> None:
    z = build()
    path, opf = opf_of(z)
    base = path.rsplit("/", 1)[0] + "/"
    nav_href = opf.xpath("//opf:item[@properties='nav']/@href", namespaces=NS)[0]
    nav = etree.fromstring(z.read(base + nav_href))
    entries = nav.xpath("//x:nav[@epub:type='toc']/x:ol/x:li/x:a", namespaces=NS)
    front = len(full_book().front)
    assert entries[front].text == "Contents"
    assert entries[front].get("href") == "contents.xhtml"
    ncx_href = opf.xpath("//opf:item[@media-type='application/x-dtbncx+xml']/@href", namespaces=NS)
    ncx = etree.fromstring(z.read(base + ncx_href[0]))
    labels = ncx.xpath("//ncx:navMap/ncx:navPoint/ncx:navLabel/ncx:text/text()", namespaces=NS)
    assert labels[front] == "Contents"
    page = etree.fromstring(z.read(base + "Text/contents.xhtml"))
    assert "Contents" not in page.xpath("//x:a/text()", namespaces=NS)  # not listed on itself
