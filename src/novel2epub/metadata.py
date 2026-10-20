"""Validate metadata and fill in deterministic defaults."""

from __future__ import annotations

import dataclasses
import os
import re
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.fonts import FONT_SLOTS
from novel2epub.ir import STYLE_LIMITS, Metadata, Style

# Fixed namespace so that derived identifiers are stable across machines and versions.
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/hanszener-afk/epubconvert")
_BCP47 = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")


def is_language_tag(text: str) -> bool:
    """True for something shaped like a BCP 47 language tag (en, en-GB)."""
    return bool(_BCP47.match(text))


FALLBACK_MODIFIED = datetime(2000, 1, 1, tzinfo=UTC)


def derive_identifier(title: str, authors: list[str]) -> str:
    key = title.strip() + "\x1f" + "\x1e".join(a.strip() for a in authors)
    return f"urn:uuid:{uuid.uuid5(_NAMESPACE, key)}"


def _utc(when: datetime) -> datetime:
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(UTC).replace(microsecond=0)


def normalise_isbn(raw: str) -> str:
    digits = re.sub(r"[\s-]", "", raw).upper()
    if re.fullmatch(r"\d{13}", digits):
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
        if (10 - total % 10) % 10 == int(digits[12]):
            return digits
    elif re.fullmatch(r"\d{9}[\dX]", digits):
        total = sum((10 - i) * int(d) for i, d in enumerate(digits[:9]))
        check = (11 - total % 11) % 11
        if digits[9] == ("X" if check == 10 else str(check)):
            return digits
    raise ConversionError("isbn-invalid", f"ISBN {raw!r} is not a valid ISBN-10 or ISBN-13.")


def resolve(meta: Metadata, source_date: datetime | None, diag: Diagnostics) -> Metadata:
    """Return a copy of meta with identifier and modified set, after validation.

    Precedence for dcterms:modified: an explicit value in meta, then the source
    document's own modification date, then a fixed fallback (with a warning).
    """
    if not meta.title.strip():
        raise ConversionError("metadata-missing", "A title is required.")
    authors = [a.strip() for a in meta.authors if a.strip()]
    if not authors:
        raise ConversionError("metadata-missing", "At least one author is required.")
    if not _BCP47.match(meta.language):
        raise ConversionError(
            "language-invalid",
            f"Language {meta.language!r} is not a BCP 47 tag (for example en-GB).",
        )
    if meta.language.split("-")[0].lower() != "en":
        diag.warn(
            "language-unsupported",
            f"Language {meta.language!r}: only English is supported; heuristics may misbehave.",
        )

    identifier = meta.identifier
    if not identifier:
        identifier = derive_identifier(meta.title, authors)
        diag.warn(
            "identifier-derived",
            f"No identifier supplied; derived {identifier} from title and authors. "
            "Pin it in the metadata file so it survives a change of title.",
        )

    if meta.modified is not None:
        modified = _utc(meta.modified)
    elif source_date is not None:
        modified = _utc(source_date)
    else:
        modified = FALLBACK_MODIFIED
        diag.warn(
            "modified-fallback",
            "The source has no modification date; using 2000-01-01T00:00:00Z.",
        )

    isbn = normalise_isbn(meta.isbn) if meta.isbn else None
    return dataclasses.replace(
        meta, authors=authors, identifier=identifier, modified=modified, isbn=isbn
    )


# --------------------------------------------------------------------------
# The book file (TOML): metadata, cover, style, and fonts for one book.
# Paths in it are relative to the file, so a book's folder can be moved.

_SIMPLE_FIELDS = (
    "title", "subtitle", "language", "identifier", "publisher", "date", "series",
    "series_index", "description", "rights", "isbn",
)  # fmt: skip
_A11Y_FIELDS = {"summary": "accessibility_summary", "conforms_to": "conforms_to",
                "certified_by": "certified_by"}  # fmt: skip
FONT_KEYS = tuple(FONT_SLOTS)


@dataclasses.dataclass
class MetaFile:
    fields: dict[str, object]
    cover: Path | None = None
    cover_alt: str | None = None
    style: Style = dataclasses.field(default_factory=Style)
    fonts: dict[str, Path] = dataclasses.field(default_factory=dict)
    # Images for [image: file] placeholders: (file, description or None).
    images: list[tuple[Path, str | None]] = dataclasses.field(default_factory=list)


def load_meta_file(path: Path) -> MetaFile:
    """Read a book file. Unknown keys and out-of-range values are errors, to catch typos."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConversionError("meta-invalid", f"Cannot read metadata file {path}: {exc}") from exc
    meta = MetaFile({})
    for key, value in data.items():
        if key in _SIMPLE_FIELDS:
            meta.fields[key] = str(value)
        elif key in ("author", "authors"):
            meta.fields["authors"] = [value] if isinstance(value, str) else [str(v) for v in value]
        elif key == "cover":
            meta.cover = (path.parent / str(value)).resolve()
        elif key == "cover_alt":
            meta.cover_alt = str(value)
        elif key == "accessibility" and isinstance(value, dict):
            for k, v in value.items():
                if k not in _A11Y_FIELDS:
                    raise ConversionError(
                        "meta-invalid", f"Unknown key accessibility.{k} in {path}."
                    )
                meta.fields[_A11Y_FIELDS[k]] = str(v)
        elif key == "style" and isinstance(value, dict):
            meta.style = _load_style(value, path)
        elif key == "fonts" and isinstance(value, dict):
            for k, v in value.items():
                if k not in FONT_KEYS:
                    raise ConversionError(
                        "meta-invalid",
                        f"Unknown key fonts.{k} in {path}; use {', '.join(FONT_KEYS)}.",
                    )
                meta.fonts[k] = (path.parent / str(v)).resolve()
        elif key == "images" and isinstance(value, list):
            for entry in value:
                if not isinstance(entry, dict) or "file" not in entry:
                    raise ConversionError(
                        "meta-invalid", f"Each [[images]] entry in {path} needs a file."
                    )
                unknown = set(entry) - {"file", "description"}
                if unknown:
                    raise ConversionError(
                        "meta-invalid", f"Unknown key images.{sorted(unknown)[0]} in {path}."
                    )
                description = str(entry["description"]) if entry.get("description") else None
                meta.images.append(((path.parent / str(entry["file"])).resolve(), description))
        else:
            raise ConversionError("meta-invalid", f"Unknown key {key!r} in {path}.")
    return meta


def _load_style(table: dict[str, object], path: Path) -> Style:
    style = Style()
    for key, value in table.items():
        if key == "block_paragraphs":
            if not isinstance(value, bool):
                raise ConversionError(
                    "meta-invalid", f"style.block_paragraphs in {path} must be true or false."
                )
            style.block_paragraphs = value
            continue
        if key not in STYLE_LIMITS:
            raise ConversionError("meta-invalid", f"Unknown key style.{key} in {path}.")
        low, high = STYLE_LIMITS[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConversionError("meta-invalid", f"style.{key} in {path} must be a number.")
        if not low <= value <= high:
            raise ConversionError(
                "meta-invalid", f"style.{key} in {path} must be between {low:g} and {high:g}."
            )
        setattr(style, key, float(value))
    return style


def _toml_string(text: str) -> str:
    out = []
    for ch in text:
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return f"{value:g}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_string(str(v)) for v in value) + "]"
    return _toml_string(str(value))


def _relative(target: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(target, base)).as_posix()
    except ValueError:  # another drive on Windows
        return str(target)


def save_meta_file(meta: MetaFile, path: Path) -> None:
    """Write `meta` as a book file that load_meta_file reads back unchanged."""
    base = path.resolve().parent
    lines = ["# novel2epub book file: metadata, cover, style, and fonts for one book.", ""]

    def put(key: str, value: object) -> None:
        if value not in (None, "", []):
            lines.append(f"{key} = {_toml_value(value)}")

    for key in ("title", "subtitle"):
        put(key, meta.fields.get(key))
    put("authors", meta.fields.get("authors"))
    for key in _SIMPLE_FIELDS[2:]:
        put(key, meta.fields.get(key))
    if meta.cover is not None:
        put("cover", _relative(meta.cover, base))
    put("cover_alt", meta.cover_alt)

    sections = [
        ("accessibility", [(k, meta.fields.get(f)) for k, f in _A11Y_FIELDS.items()]),
        ("style", [(k, getattr(meta.style, k)) for k in (*STYLE_LIMITS, "block_paragraphs")]),
        ("fonts", [(k, _relative(meta.fonts[k], base)) for k in FONT_KEYS if k in meta.fonts]),
    ]
    for name, entries in sections:
        present = [(k, v) for k, v in entries if v not in (None, "", False)]
        if present:
            lines += ["", f"[{name}]"]
            lines += [f"{k} = {_toml_value(v)}" for k, v in present]
    for image, description in meta.images:
        lines += ["", "[[images]]", f"file = {_toml_string(_relative(image, base))}"]
        if description:
            lines.append(f"description = {_toml_string(description)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_overrides(meta: Metadata, overrides: dict[str, object]) -> Metadata:
    clean = {k: v for k, v in overrides.items() if v not in (None, "", [])}
    return dataclasses.replace(meta, **clean)  # type: ignore[arg-type]
