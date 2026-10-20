"""What the window's forms hold, and how they become conversion options.

Kept apart from the window itself so that it can be tested without a display.
Every field is text (or a tick box), exactly as typed; checking turns it into
ConvertOptions or a MetaFile, or into a plain message saying what to fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path

from novel2epub.diagnostics import ConversionError
from novel2epub.ir import STYLE_LIMITS, Style
from novel2epub.metadata import FONT_KEYS, MetaFile, is_language_tag, normalise_isbn
from novel2epub.pipeline import ConvertOptions

INPUT_TYPES = (".docx", ".pdf", ".md", ".markdown", ".html", ".htm")
LANGUAGES = ["", "en-GB", "en-US"]
_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")

# Messages are written for the command line; in the window, name the controls.
PLAIN_WORDS = [
    ("pass --accept-changes or --reject-changes", "choose Accept all or Reject all"),
    ("--accept-changes and --reject-changes apply", "The tracked-changes choice applies"),
    ("--chapter-regex", "the chapter heading pattern"),
    ("--language", "the Language box on the Book details tab"),
    ("--title", "the Title box on the Book details tab"),
    ("--strict", "Strict"),
]


def plain(text: str) -> str:
    """`text` with command-line options replaced by the window's own words."""
    for flag, words in PLAIN_WORDS:
        text = text.replace(flag, words)
    return text


@dataclass
class Form:
    """The Convert tab."""

    input: str = ""
    output: str = ""
    changes: str = "fail"  # fail, accept, or reject
    smarten: bool = False
    literal_tags: bool = False
    chapter_regex: str = ""
    strict: bool = False


@dataclass
class BookForm:
    """The Book details and Appearance tabs: everything a book file holds."""

    title: str = ""
    subtitle: str = ""
    authors: str = ""  # separated by semicolons
    language: str = ""
    identifier: str = ""
    publisher: str = ""
    date: str = ""
    series: str = ""
    series_index: str = ""
    description: str = ""
    rights: str = ""
    isbn: str = ""
    cover: str = ""
    cover_alt: str = ""
    a11y_summary: str = ""
    conforms_to: str = ""
    certified_by: str = ""
    side_margin: str = ""
    top_margin: str = ""
    bottom_margin: str = ""
    indent: str = ""
    paragraph_gap: str = ""
    block_paragraphs: bool = False
    line_spacing: str = ""
    font_body: str = ""
    font_body_italic: str = ""
    font_body_bold: str = ""
    font_body_bold_italic: str = ""
    font_headings: str = ""
    # Images for [image: file] placeholders: (file, description).
    images: tuple[tuple[str, str], ...] = ()

    def is_blank(self) -> bool:
        return self == BookForm()


# Book form field -> metadata field, for the plain text fields.
_TEXT_FIELDS = {
    "title": "title", "subtitle": "subtitle", "language": "language",
    "identifier": "identifier", "publisher": "publisher", "date": "date", "series": "series",
    "series_index": "series_index", "description": "description", "rights": "rights",
    "isbn": "isbn", "a11y_summary": "accessibility_summary", "conforms_to": "conforms_to",
    "certified_by": "certified_by",
}  # fmt: skip
STYLE_LABELS = {
    "side_margin": "Side margin", "top_margin": "Top margin", "bottom_margin": "Bottom margin",
    "indent": "Paragraph indent", "paragraph_gap": "Space between paragraphs",
    "line_spacing": "Line spacing",
}  # fmt: skip
FONT_LABELS = {
    "body": "Body text", "body_italic": "Body italic", "body_bold": "Body bold",
    "body_bold_italic": "Body bold italic", "headings": "Headings",
}  # fmt: skip


def to_metafile(form: BookForm) -> MetaFile | str:
    """The book file for `form`, or a message saying what to fix."""
    meta = MetaFile({})
    for name, key in _TEXT_FIELDS.items():
        value = getattr(form, name).strip() if name != "description" else form.description
        if value.strip():
            meta.fields[key] = value.strip() if name != "description" else value.rstrip()
    authors = [a.strip() for a in form.authors.split(";") if a.strip()]
    if authors:
        meta.fields["authors"] = authors
    language = form.language.strip()
    if language and not is_language_tag(language):
        return f"{language!r} is not a language tag. Use one such as en-GB or en-US."
    if form.isbn.strip():
        try:
            normalise_isbn(form.isbn.strip())
        except ConversionError as exc:
            return str(exc)
    if form.date.strip() and not _DATE.match(form.date.strip()):
        return "The publication date must look like 2026, 2026-10, or 2026-10-01."

    style = Style(block_paragraphs=form.block_paragraphs)
    for name, label in STYLE_LABELS.items():
        text = getattr(form, name).strip().replace(",", ".")
        if not text:
            continue
        try:
            value = float(text)
        except ValueError:
            return f"{label} must be a number, such as 1.5."
        low, high = STYLE_LIMITS[name]
        if not low <= value <= high:
            return f"{label} must be between {low:g} and {high:g}."
        setattr(style, name, value)
    meta.style = style

    if form.cover.strip():
        cover = Path(form.cover.strip())
        if not cover.is_file():
            return f"The cover image {cover} cannot be found."
        meta.cover = cover
    meta.cover_alt = form.cover_alt.strip() or None
    for key in FONT_KEYS:
        text = getattr(form, f"font_{key}").strip()
        if text:
            path = Path(text)
            if not path.is_file():
                return f"The {FONT_LABELS[key].lower()} font {path} cannot be found."
            meta.fonts[key] = path
    for file, description in form.images:
        path = Path(file)
        if not path.is_file():
            return f"The image {path} cannot be found. Remove it from the Images tab or restore it."
        meta.images.append((path, description.strip() or None))
    return meta


def from_metafile(meta: MetaFile) -> BookForm:
    """The form showing `meta`, for editing."""
    form = BookForm()
    for name, key in _TEXT_FIELDS.items():
        value = meta.fields.get(key)
        if value:
            setattr(form, name, str(value))
    authors = meta.fields.get("authors")
    if isinstance(authors, list):
        form.authors = "; ".join(str(a) for a in authors)
    form.cover = str(meta.cover) if meta.cover else ""
    form.cover_alt = meta.cover_alt or ""
    for name in STYLE_LABELS:
        value = getattr(meta.style, name)
        setattr(form, name, f"{value:g}" if value is not None else "")
    form.block_paragraphs = meta.style.block_paragraphs
    for key, path in meta.fonts.items():
        setattr(form, f"font_{key}", str(path))
    form.images = tuple((str(path), description or "") for path, description in meta.images)
    return form


def to_options(
    form: Form, book: BookForm | None = None, epubcheck: bool = True
) -> ConvertOptions | str:
    """The conversion options for the forms, or a message saying what to fix."""
    if not form.input.strip():
        return "Choose a manuscript to convert."
    src = Path(form.input.strip())
    if not src.is_file():
        return f"The manuscript {src} cannot be found."
    if src.suffix.lower() not in INPUT_TYPES:
        return "The manuscript must be a .docx, .pdf, .md, or .html file."
    output = Path(form.output.strip()) if form.output.strip() else src.with_suffix(".epub")
    if output.is_dir():
        output = output / f"{src.stem}.epub"
    if output.resolve() == src.resolve():
        return "The EPUB would overwrite the manuscript. Choose another name."
    if form.chapter_regex.strip():
        try:
            re.compile(form.chapter_regex.strip())
        except re.error as exc:
            return f"The chapter pattern is not valid: {exc}."
    meta = None
    if book is not None and not book.is_blank():
        checked = to_metafile(book)
        if isinstance(checked, str):
            return checked
        meta = checked
    return ConvertOptions(
        input=src,
        output=output,
        meta_data=meta,
        revisions=form.changes,
        smarten=form.smarten,
        chapter_regex=form.chapter_regex.strip() or None,
        strict=form.strict,
        html_tags=not form.literal_tags,
        epubcheck=epubcheck,
    )


BOOK_FIELDS = [f.name for f in fields(BookForm)]
