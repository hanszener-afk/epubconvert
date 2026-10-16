from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.ir import Metadata
from novel2epub.metadata import resolve


def meta(**kw: object) -> Metadata:
    base: dict[str, object] = {"title": "T", "authors": ["A"], "language": "en-GB"}
    base.update(kw)
    return Metadata(**base)  # type: ignore[arg-type]


def test_derived_identifier_is_stable_and_warned() -> None:
    d1, d2 = Diagnostics(), Diagnostics()
    a = resolve(meta(), datetime(2026, 1, 1, tzinfo=UTC), d1)
    b = resolve(meta(), datetime(2027, 1, 1, tzinfo=UTC), d2)
    assert a.identifier == b.identifier
    assert a.identifier is not None and a.identifier.startswith("urn:uuid:")
    assert any(i.code == "identifier-derived" for i in d1.warnings)


def test_identifier_changes_with_title_or_author() -> None:
    d = Diagnostics()
    when = datetime(2026, 1, 1, tzinfo=UTC)
    ids = {
        resolve(meta(), when, d).identifier,
        resolve(meta(title="U"), when, d).identifier,
        resolve(meta(authors=["B"]), when, d).identifier,
    }
    assert len(ids) == 3


def test_supplied_identifier_is_kept() -> None:
    m = resolve(
        meta(identifier="urn:uuid:12345678-1234-5678-1234-567812345678"), None, Diagnostics()
    )
    assert m.identifier == "urn:uuid:12345678-1234-5678-1234-567812345678"


def test_modified_comes_from_source_date_in_utc_without_microseconds() -> None:
    src = datetime(2026, 5, 1, 12, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    m = resolve(meta(), src, Diagnostics())
    assert m.modified == datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)


def test_missing_source_date_falls_back_with_warning() -> None:
    d = Diagnostics()
    m = resolve(meta(), None, d)
    assert m.modified == datetime(2000, 1, 1, tzinfo=UTC)
    assert any(i.code == "modified-fallback" for i in d.warnings)


def test_naive_source_date_is_treated_as_utc() -> None:
    m = resolve(meta(), datetime(2026, 5, 1, 12, 0), Diagnostics())
    assert m.modified == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("bad", ["", "english", "en_GB", "e"])
def test_bad_language_rejected(bad: str) -> None:
    with pytest.raises(ConversionError):
        resolve(meta(language=bad), None, Diagnostics())


def test_non_english_language_warns() -> None:
    d = Diagnostics()
    resolve(meta(language="fr"), None, d)
    assert any(i.code == "language-unsupported" for i in d.warnings)


@pytest.mark.parametrize("field", ["title", "authors"])
def test_required_fields(field: str) -> None:
    with pytest.raises(ConversionError):
        resolve(meta(**{field: "" if field == "title" else []}), None, Diagnostics())


def test_isbn_validated() -> None:
    d = Diagnostics()
    assert resolve(meta(isbn="978-0-00-000000-2"), None, d).isbn == "9780000000002"
    with pytest.raises(ConversionError):
        resolve(meta(isbn="9780000000003"), None, Diagnostics())
