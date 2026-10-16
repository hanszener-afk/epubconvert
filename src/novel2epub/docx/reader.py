"""DOCX front end: produces the IR and nothing else."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from lxml import etree

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.docx import revisions
from novel2epub.docx.package import DocxPackage, W
from novel2epub.docx.structure import Structurer
from novel2epub.docx.styles import Styles
from novel2epub.docx.walker import IDX_ATTR, ImageStore, RawPara, Walker
from novel2epub.ir import Book, Metadata, Note, NoteKind, Paragraph
from novel2epub.normalise.inline import build_inlines

NOTE_PREFIX = {"footnoteReference": "fn", "endnoteReference": "en"}


@dataclass
class DocxOptions:
    revisions: Literal["fail", "accept", "reject"] = "fail"
    chapter_regex: str | None = None
    html_tags: bool = True


@dataclass
class DocxResult:
    book: Book
    source_date: datetime | None


def _index(root: etree._Element) -> None:
    for i, p in enumerate(root.iter(f"{W}p")):
        p.set(IDX_ATTR, str(i))


def _first_location(root: etree._Element, part: str) -> str:
    for name in (*revisions.RUN_REVISIONS, *revisions.PROPERTY_REVISIONS):
        el = root.find(f".//{W}{name}")
        if el is not None:
            p = el if el.tag == f"{W}p" else next(el.iterancestors(f"{W}p"), None)
            if p is not None:
                return f"{part} paragraph {int(p.get(IDX_ATTR, '0')) + 1}"
    return part


def read_docx(path: Path, options: DocxOptions, diag: Diagnostics) -> DocxResult:
    pkg = DocxPackage(path)
    document = pkg.xml(pkg.main)
    parts: dict[str, etree._Element] = {pkg.main: document}
    for rel_type in ("footnotes", "endnotes"):
        part = pkg.related_part(rel_type)
        if part:
            parts[part] = pkg.xml(part)
    for root in parts.values():
        _index(root)

    _handle_revisions(parts, options.revisions, diag)
    _handle_comments(pkg, diag)

    styles_part = pkg.related_part("styles")
    styles = Styles(pkg.xml(styles_part) if styles_part else None)
    images = ImageStore(pkg, diag)

    body = document.find(f"{W}body")
    if body is None:
        raise ConversionError("input-invalid", "The document has no body.")
    walker = Walker(pkg.main, pkg.main_rels, styles, images, diag, options.html_tags, NOTE_PREFIX)
    paras = walker.blocks(body)

    title, creator, modified = pkg.core()
    if not title:
        title = path.stem
        diag.warn(
            "title-from-filename",
            f"The document has no title property; using the file name {title!r}. "
            "Set the title with --title or in the metadata file.",
        )
    front, chapters, back = Structurer(paras, diag, options.chapter_regex, title).build()

    notes = _notes(pkg, parts, styles, images, diag, options, walker.custom_labels)

    language = styles.language
    if not language:
        language = "en"
        diag.warn("language-default", "The document declares no language; using 'en'.")
    authors = [a.strip() for a in creator.replace(";", "\n").splitlines() if a.strip()]
    meta = Metadata(title=title, authors=authors, language=language)
    book = Book(meta, chapters, front, back, notes, images.resources)
    return DocxResult(book, modified)


def _handle_revisions(parts: dict[str, etree._Element], mode: str, diag: Diagnostics) -> None:
    found = {name: revisions.count(root) for name, root in parts.items()}
    total = sum((c for c in found.values()), start=revisions.Counter())
    if not total:
        return
    summary = ", ".join(f"{n} {kind}{'s' if n != 1 else ''}" for kind, n in sorted(total.items()))
    if mode == "fail":
        where = next(_first_location(parts[name], name) for name, c in found.items() if c)
        raise ConversionError(
            "tracked-changes",
            f"The document contains tracked changes ({summary}), the first at {where}. "
            "Resolve them in Word, or pass --accept-changes or --reject-changes.",
        )
    for root in parts.values():
        revisions.apply(root, mode)
    diag.warn("tracked-changes", f"Tracked changes {mode}ed: {summary}.")


def _handle_comments(pkg: DocxPackage, diag: Diagnostics) -> None:
    part = pkg.related_part("comments")
    if not part:
        return
    root = pkg.xml(part)
    comments = list(root.iter(f"{W}comment"))
    for c in comments:
        text = "".join(t.text or "" for t in c.iter(f"{W}t"))
        diag.removed("comment", text)
    if comments:
        diag.warn("comments-removed", f"Removed {len(comments)} comment(s).")


def _notes(
    pkg: DocxPackage,
    parts: dict[str, etree._Element],
    styles: Styles,
    images: ImageStore,
    diag: Diagnostics,
    options: DocxOptions,
    custom_labels: dict[str, str],
) -> dict[str, Note]:
    notes: dict[str, Note] = {}
    for rel_type, tag, prefix, kind in (
        ("footnotes", "footnote", "fn", NoteKind.FOOTNOTE),
        ("endnotes", "endnote", "en", NoteKind.ENDNOTE),
    ):
        part = pkg.related_part(rel_type)
        if not part:
            continue
        walker = Walker(part, pkg.rels(part), styles, images, diag, options.html_tags, NOTE_PREFIX)
        for el in parts[part].iter(f"{W}{tag}"):
            if el.get(f"{W}type") not in (None, "normal"):
                continue
            nid = f"{prefix}{el.get(f'{W}id')}"
            paras = walker.blocks(el)
            label = custom_labels.get(nid)
            if label:
                _strip_label(paras, label, diag)
            blocks = [
                Paragraph(build_inlines(p.segments), source=p.source)
                for p in paras
                if p.text.strip() or p.has_noteref
            ]
            source = paras[0].source if paras else None
            notes[nid] = Note(nid, kind, blocks, source, label)  # type: ignore[arg-type]
    return notes


def _strip_label(paras: list[RawPara], label: str, diag: Diagnostics) -> None:
    """A custom-marked note repeats its mark at the start of its text."""
    for p in paras:
        for seg in p.segments:
            if seg.kind != "text" or not seg.text.strip():
                continue
            stripped = seg.text.lstrip()
            if stripped.startswith(label):
                seg.text = stripped[len(label) :]
                diag.removed("note-mark", label, p.source)
            return
