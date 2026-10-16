"""Write an IR Book as an EPUB 3 file. Consumes the IR only."""

from __future__ import annotations

import io
import re
import zipfile
from importlib import resources as importlib_resources
from pathlib import Path

from PIL import Image as PILImage

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.epub import package, xhtml
from novel2epub.ir import (
    Block,
    BlockQuote,
    Book,
    Epigraph,
    Heading,
    Image,
    Inline,
    NoteKind,
    NoteRef,
    Paragraph,
    Section,
    SectionKind,
    Span,
    Verse,
)

ROOT = "OEBPS"  # Sigil's conventional layout: OEBPS/Text, OEBPS/Styles, OEBPS/Images
TEXT_DIR = "Text"
CSS_HREF_FROM_TEXT = "../Styles/style.css"

CORE_IMAGE_TYPES = {
    "image/jpeg": ("jpg", "JPEG"),
    "image/png": ("png", "PNG"),
    "image/gif": ("gif", "GIF"),
    "image/webp": ("webp", "WEBP"),
    "image/svg+xml": ("svg", None),
}

# Kindle's recommended cover is 1600 x 2560 (ratio 1.6).
COVER_MIN_LONG_SIDE = 1600
COVER_RATIO_RANGE = (1.3, 1.8)


def write_epub(book: Book, path: Path, diag: Diagnostics) -> None:
    data = build_epub(book, diag)
    path.write_bytes(data)


def build_epub(book: Book, diag: Diagnostics) -> bytes:
    meta = book.metadata
    if meta.identifier is None or meta.modified is None:
        raise ConversionError(
            "metadata-unresolved", "Metadata must be resolved (identifier and modified date)."
        )
    if not book.body:
        raise ConversionError("no-content", "The book has no body sections.")

    files: dict[str, bytes] = {}
    manifest: list[package.ManifestItem] = []
    spine: list[str] = []

    image_href, image_items = _images(book, diag)
    manifest.extend(image_items)

    sections = [
        *(("frontmatter", s, f"front-{i:02d}.xhtml") for i, s in enumerate(book.front, 1)),
        *(("bodymatter", s, f"chapter-{i:03d}.xhtml") for i, s in enumerate(book.body, 1)),
        *(("backmatter", s, f"back-{i:02d}.xhtml") for i, s in enumerate(book.back, 1)),
    ]
    plan, endnote_ids = _plan_notes(book, [(s, name) for _, s, name in sections], diag)
    note_blocks = {nid: n.blocks for nid, n in book.notes.items()}

    toc: list[package.TocEntry] = []
    landmarks: list[tuple[str, str, str]] = []

    if book.cover is not None:
        if book.cover.resource_id not in image_href:
            raise ConversionError("cover-missing", "Cover refers to an unknown resource.")
        _check_cover(book, diag)
        cover = xhtml.render_cover(
            image_href[book.cover.resource_id], book.cover.alt, meta.language, CSS_HREF_FROM_TEXT
        )
        _add(files, manifest, spine, "cover.xhtml", cover)
        landmarks.append(("cover", "Cover", f"{TEXT_DIR}/cover.xhtml"))

    chapter_no = 0
    part_no = 0
    other_no = 0
    nav_spine_index: int | None = None
    for matter, section, name in sections:
        if matter != "frontmatter" and nav_spine_index is None:
            nav_spine_index = len(spine)
        if section.kind is SectionKind.CHAPTER:
            chapter_no += 1
            number = chapter_no
        elif section.kind is SectionKind.PART:
            part_no += 1
            number = part_no
        else:
            other_no += 1
            number = other_no
        ctx = xhtml.RenderContext(name, meta.language, plan, image_href, diag)
        data = xhtml.render_section(section, number, matter, ctx, CSS_HREF_FROM_TEXT, note_blocks)
        _add(files, manifest, spine, name, data)
        label = xhtml.section_label(section, number)
        toc.append(package.TocEntry(label, f"{TEXT_DIR}/{name}", section.depth))
        if matter == "bodymatter" and not any(t == "bodymatter" for t, _, _ in landmarks):
            landmarks.append(("bodymatter", "Start of Content", f"{TEXT_DIR}/{name}"))
        if section.kind is SectionKind.TITLEPAGE:
            landmarks.append(("titlepage", "Title Page", f"{TEXT_DIR}/{name}"))

    if endnote_ids:
        ctx = xhtml.RenderContext("endnotes.xhtml", meta.language, plan, image_href, diag)
        data = xhtml.render_endnotes(endnote_ids, ctx, CSS_HREF_FROM_TEXT, note_blocks)
        _add(files, manifest, spine, "endnotes.xhtml", data)
        toc.append(package.TocEntry("Notes", f"{TEXT_DIR}/endnotes.xhtml"))
        landmarks.append(("endnotes", "Notes", f"{TEXT_DIR}/endnotes.xhtml"))

    landmarks.append(("toc", "Contents", f"{TEXT_DIR}/contents.xhtml"))
    nav_toc = [package.TocEntry(t.label, t.href.removeprefix(f"{TEXT_DIR}/"), t.depth) for t in toc]
    nav_landmarks = [(t, lbl, h.removeprefix(f"{TEXT_DIR}/")) for t, lbl, h in landmarks]
    menu_toc = [*nav_toc]
    menu_toc.insert(len(book.front), package.TocEntry("Contents", "contents.xhtml"))
    files[f"{TEXT_DIR}/nav.xhtml"] = package.nav_xhtml(
        meta.title, meta.language, CSS_HREF_FROM_TEXT, menu_toc, nav_landmarks
    )
    manifest.append(
        package.ManifestItem("nav", f"{TEXT_DIR}/nav.xhtml", "application/xhtml+xml", "nav")
    )
    # A visible contents page in the reading order, after the front matter, as
    # Kindle recommends. The navigation document itself stays out of the spine,
    # so readers that render every spine item (Okular) never show its landmarks.
    assert nav_spine_index is not None
    contents_href = f"{TEXT_DIR}/contents.xhtml"
    files[contents_href] = package.contents_xhtml(meta.language, CSS_HREF_FROM_TEXT, nav_toc)
    manifest.append(package.ManifestItem("x-contents", contents_href, "application/xhtml+xml"))
    spine.insert(nav_spine_index, "x-contents")
    # Readers' own contents menus list the contents page too, in reading
    # order; otherwise some (Foliate) name it after the page before it.
    toc.insert(len(book.front), package.TocEntry("Contents", contents_href))
    files["toc.ncx"] = package.toc_ncx(meta, toc)
    manifest.append(package.ManifestItem("ncx", "toc.ncx", "application/x-dtbncx+xml"))
    files["Styles/style.css"] = _stylesheet()
    manifest.append(package.ManifestItem("css", "Styles/style.css", "text/css"))

    images_in_text = [b for s in book.sections() for b in _walk_blocks(s.opener + s.blocks)]
    images_in_text += [b for n in book.notes.values() for b in _walk_blocks(n.blocks)]
    has_images = bool(images_in_text) or book.cover is not None
    all_alt = all(img.alt for img in images_in_text)
    cover_id = _item_id_for_resource(book.cover.resource_id) if book.cover else None
    files["content.opf"] = package.content_opf(
        meta,
        sorted(manifest, key=lambda m: m.href),
        spine,
        package.Accessibility(has_images, all_alt),
        cover_id,
    )

    for href, data in _image_files(book, image_href).items():
        files[href] = data
    return _zip(files, meta.modified.timetuple()[:6])


# --------------------------------------------------------------------------
# helpers


def _add(
    files: dict[str, bytes],
    manifest: list[package.ManifestItem],
    spine: list[str],
    name: str,
    data: bytes,
) -> None:
    href = f"{TEXT_DIR}/{name}"
    files[href] = data
    item_id = "x-" + name.removesuffix(".xhtml")
    manifest.append(package.ManifestItem(item_id, href, "application/xhtml+xml"))
    spine.append(item_id)


def _stylesheet() -> bytes:
    return (importlib_resources.files("novel2epub.epub") / "style.css").read_bytes()


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return cleaned if cleaned[:1].isalpha() else f"r{cleaned}"


def _item_id_for_resource(resource_id: str) -> str:
    return "img-" + _safe(resource_id)


def _images(book: Book, diag: Diagnostics) -> tuple[dict[str, str], list[package.ManifestItem]]:
    """Validate referenced image resources. Returns hrefs (from Text/) and manifest items."""
    used = {b.resource_id for s in book.sections() for b in _walk_blocks(s.opener + s.blocks)}
    used |= {b.resource_id for n in book.notes.values() for b in _walk_blocks(n.blocks)}
    if book.cover:
        used.add(book.cover.resource_id)
    missing = sorted(used - set(book.resources))
    if missing:
        raise ConversionError("image-missing", f"Images refer to unknown resources: {missing}.")
    hrefs: dict[str, str] = {}
    items: list[package.ManifestItem] = []
    taken: set[str] = set()
    for rid in sorted(book.resources):
        res = book.resources[rid]
        if rid not in used:
            diag.warn("resource-unused", f"Resource {rid!r} is not referenced; not included.")
            continue
        if res.media_type not in CORE_IMAGE_TYPES:
            raise ConversionError(
                "image-unsupported",
                f"Resource {rid!r} has media type {res.media_type}, which EPUB does not support. "
                "It should have been converted before writing.",
            )
        ext, pil_format = CORE_IMAGE_TYPES[res.media_type]
        if pil_format is not None:
            _check_raster(rid, res.data, pil_format)
        stem = _safe(rid)
        while f"{stem}.{ext}" in taken:
            stem += "_"
        taken.add(f"{stem}.{ext}")
        href = f"Images/{stem}.{ext}"
        hrefs[rid] = f"../{href}"
        props = "cover-image" if book.cover and book.cover.resource_id == rid else None
        items.append(package.ManifestItem(_item_id_for_resource(rid), href, res.media_type, props))
    return hrefs, items


def _image_files(book: Book, hrefs: dict[str, str]) -> dict[str, bytes]:
    return {href.removeprefix("../"): book.resources[rid].data for rid, href in hrefs.items()}


def _check_raster(rid: str, data: bytes, expected: str) -> None:
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            actual = im.format
            im.verify()
    except Exception as exc:
        raise ConversionError("image-corrupt", f"Image {rid!r} cannot be read: {exc}") from exc
    if actual != expected:
        raise ConversionError(
            "image-type-mismatch", f"Image {rid!r} is declared {expected} but is {actual}."
        )


def _check_cover(book: Book, diag: Diagnostics) -> None:
    assert book.cover is not None
    res = book.resources[book.cover.resource_id]
    if res.media_type not in ("image/jpeg", "image/png"):
        raise ConversionError("cover-format", "The cover must be a JPEG or PNG image.")
    with PILImage.open(io.BytesIO(res.data)) as im:
        width, height = im.size
        mode = im.mode
    if max(width, height) < COVER_MIN_LONG_SIDE:
        diag.warn(
            "cover-small",
            f"Cover is {width} x {height}; retailers recommend at least 1600 x 2560.",
        )
    ratio = height / width if width else 0
    lo, hi = COVER_RATIO_RANGE
    if not lo <= ratio <= hi:
        diag.warn("cover-ratio", f"Cover aspect ratio {ratio:.2f} is unusual (1.6 is typical).")
    if mode == "CMYK":
        diag.warn("cover-cmyk", "Cover is CMYK; some reading systems show odd colours. Use RGB.")
    if not book.cover.alt.strip():
        diag.warn("cover-no-alt", "The cover has no alt text.")


def _walk_blocks(blocks: list[Block]) -> list[Image]:
    found: list[Image] = []
    for b in blocks:
        if isinstance(b, Image):
            found.append(b)
        elif isinstance(b, BlockQuote | Epigraph):
            found.extend(_walk_blocks(b.blocks))
    return found


def _refs_in_inlines(inlines: list[Inline]) -> list[str]:
    out: list[str] = []
    for node in inlines:
        if isinstance(node, NoteRef):
            out.append(node.note_id)
        elif isinstance(node, Span):
            out.extend(_refs_in_inlines(node.children))
    return out


def note_refs_in_blocks(blocks: list[Block]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        if isinstance(b, Paragraph | Heading):
            out.extend(_refs_in_inlines(b.content))
        elif isinstance(b, BlockQuote):
            out.extend(note_refs_in_blocks(b.blocks))
        elif isinstance(b, Epigraph):
            out.extend(note_refs_in_blocks(b.blocks))
            out.extend(_refs_in_inlines(b.attribution or []))
        elif isinstance(b, Verse):
            for stanza in b.stanzas:
                for line in stanza:
                    out.extend(_refs_in_inlines(line))
        elif isinstance(b, Image):
            out.extend(_refs_in_inlines(b.caption or []))
    return out


def _roman(n: int) -> str:
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]  # fmt: skip
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def _plan_notes(
    book: Book, sections: list[tuple[Section, str]], diag: Diagnostics
) -> tuple[xhtml.NotePlan, list[str]]:
    plan = xhtml.NotePlan()
    first_file: dict[str, str] = {}
    order: list[str] = []
    for section, name in sections:
        refs = (
            note_refs_in_blocks(section.opener)
            + _refs_in_inlines(section.title or [])
            + note_refs_in_blocks(section.blocks)
        )
        for nid in refs:
            if nid not in book.notes:
                raise ConversionError("note-missing", f"Reference to unknown note {nid!r}.")
            if nid not in first_file:
                first_file[nid] = name
                order.append(nid)
    used_ids: set[str] = set()
    for nid in book.notes:
        hid = "note-" + re.sub(r"[^A-Za-z0-9_-]", "_", nid)
        while hid in used_ids:
            hid += "_"
        used_ids.add(hid)
        plan.html_id[nid] = hid

    footnote_no = endnote_no = 0
    endnotes: list[str] = []
    for nid in order:
        note = book.notes[nid]
        if note.kind is NoteKind.FOOTNOTE:
            footnote_no += 1
            plan.label[nid] = note.label or str(footnote_no)
            plan.home[nid] = first_file[nid]
        else:
            endnote_no += 1
            plan.label[nid] = note.label or _roman(endnote_no)
            plan.home[nid] = "endnotes.xhtml"
            endnotes.append(nid)
    for nid, note in book.notes.items():
        if nid in first_file:
            continue
        diag.warn(
            "note-unreferenced",
            f"Note {nid!r} is never referenced in the text; placed in the endnotes.",
            note.source,
        )
        endnote_no += 1
        plan.label[nid] = note.label or _roman(endnote_no)
        plan.home[nid] = "endnotes.xhtml"
        endnotes.append(nid)
    return plan, endnotes


def _zip(files: dict[str, bytes], date_time: tuple[int, ...]) -> bytes:
    """Deterministic zip: mimetype first and stored, then sorted entries."""
    stamp = tuple(date_time)
    if stamp[0] < 1980:
        stamp = (1980, 1, 1, 0, 0, 0)
    buf = io.BytesIO()
    entries = {"mimetype": b"application/epub+zip", "META-INF/container.xml": b""}
    entries["META-INF/container.xml"] = package.container_xml(f"{ROOT}/content.opf")
    entries.update({f"{ROOT}/{name}": data for name, data in files.items()})
    with zipfile.ZipFile(buf, "w") as z:
        for name in ["mimetype", *sorted(n for n in entries if n != "mimetype")]:
            info = zipfile.ZipInfo(name, date_time=stamp)  # type: ignore[arg-type]
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            if name == "mimetype":
                info.compress_type = zipfile.ZIP_STORED
                z.writestr(info, entries[name])
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, entries[name], compresslevel=9)
    return buf.getvalue()
