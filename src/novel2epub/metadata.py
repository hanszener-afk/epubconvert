"""Validate metadata and fill in deterministic defaults."""

from __future__ import annotations

import dataclasses
import re
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.ir import Metadata

# Fixed namespace so that derived identifiers are stable across machines and versions.
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/hanszener-afk/epubconvert")
_BCP47 = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")
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
# Metadata file (TOML)

_SIMPLE_FIELDS = (
    "title", "subtitle", "language", "identifier", "publisher", "date", "series",
    "series_index", "description", "rights", "isbn",
)  # fmt: skip
_A11Y_FIELDS = {"summary": "accessibility_summary", "conforms_to": "conforms_to",
                "certified_by": "certified_by"}  # fmt: skip


@dataclasses.dataclass
class MetaFile:
    fields: dict[str, object]
    cover: Path | None = None
    cover_alt: str | None = None


def load_meta_file(path: Path) -> MetaFile:
    """Read a TOML metadata file. Unknown keys are errors, to catch typos."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConversionError("meta-invalid", f"Cannot read metadata file {path}: {exc}") from exc
    fields: dict[str, object] = {}
    cover = None
    cover_alt = None
    for key, value in data.items():
        if key in _SIMPLE_FIELDS:
            fields[key] = str(value)
        elif key in ("author", "authors"):
            fields["authors"] = [value] if isinstance(value, str) else [str(v) for v in value]
        elif key == "cover":
            cover = (path.parent / str(value)).resolve()
        elif key == "cover_alt":
            cover_alt = str(value)
        elif key == "accessibility" and isinstance(value, dict):
            for k, v in value.items():
                if k not in _A11Y_FIELDS:
                    raise ConversionError(
                        "meta-invalid", f"Unknown key accessibility.{k} in {path}."
                    )
                fields[_A11Y_FIELDS[k]] = str(v)
        else:
            raise ConversionError("meta-invalid", f"Unknown key {key!r} in {path}.")
    return MetaFile(fields, cover, cover_alt)


def apply_overrides(meta: Metadata, overrides: dict[str, object]) -> Metadata:
    clean = {k: v for k, v in overrides.items() if v not in (None, "", [])}
    return dataclasses.replace(meta, **clean)  # type: ignore[arg-type]
