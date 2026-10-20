"""The book file (TOML): every metadata field, the style, and fonts; saved and read back."""

from __future__ import annotations

import zipfile
from pathlib import Path

import docx_fixtures as fx
import pytest
from fontbuild import make_font

from novel2epub.diagnostics import ConversionError
from novel2epub.ir import Style
from novel2epub.metadata import MetaFile, load_meta_file, save_meta_file
from novel2epub.pipeline import ConvertOptions, convert

FULL_FIELDS: dict[str, object] = {
    "title": 'The "Lighthouse" Keeper\'s Daughter',
    "subtitle": "A Novel",
    "authors": ["Ann Author", "Bea Writer"],
    "language": "en-GB",
    "identifier": "urn:uuid:0e8a1a3c-55a4-4c7e-9a8d-6b4b52c3f0a2",
    "publisher": "Small Press",
    "date": "2026-10-01",
    "series": "Coastal Tales",
    "series_index": "2",
    "description": "Line one.\nLine two, with a back\\slash and an é.",
    "rights": "Copyright 2026 Ann Author.",
    "isbn": "978-0-00-000000-2",
    "accessibility_summary": "Text with headings.",
    "conforms_to": "EPUB Accessibility 1.1 - WCAG 2.1 Level AA",
    "certified_by": "Small Press",
}


def _full(tmp_path: Path) -> MetaFile:
    (tmp_path / "art").mkdir()
    cover = tmp_path / "art" / "cover.jpg"
    cover.write_bytes(b"jpeg")
    font = tmp_path / "art" / "Body.ttf"
    font.write_bytes(make_font())
    return MetaFile(
        dict(FULL_FIELDS),
        cover=cover.resolve(),
        cover_alt="A lighthouse at dusk",
        style=Style(
            side_margin=3,
            top_margin=2,
            indent=1.5,
            paragraph_gap=0.25,
            block_paragraphs=True,
            line_spacing=1.4,
        ),
        fonts={"body": font.resolve()},
    )


def test_round_trip_keeps_every_field(tmp_path: Path) -> None:
    meta = _full(tmp_path)
    path = tmp_path / "book.toml"
    save_meta_file(meta, path)
    assert load_meta_file(path) == meta


def test_paths_are_saved_relative_to_the_file(tmp_path: Path) -> None:
    path = tmp_path / "book.toml"
    save_meta_file(_full(tmp_path), path)
    text = path.read_text(encoding="utf-8")
    assert 'cover = "art/cover.jpg"' in text
    assert 'body = "art/Body.ttf"' in text
    assert str(tmp_path) not in text


def test_empty_values_are_left_out(tmp_path: Path) -> None:
    path = tmp_path / "book.toml"
    save_meta_file(MetaFile({"title": "Only a title", "subtitle": ""}), path)
    text = path.read_text(encoding="utf-8")
    assert text.strip().splitlines()[-1] == 'title = "Only a title"'
    assert "[style]" not in text and "[fonts]" not in text


def test_saved_file_is_plain_readable_toml(tmp_path: Path) -> None:
    path = tmp_path / "book.toml"
    save_meta_file(_full(tmp_path), path)
    text = path.read_text(encoding="utf-8")
    assert 'authors = ["Ann Author", "Bea Writer"]' in text
    assert "[accessibility]" in text and "[style]" in text and "[fonts]" in text
    assert "side_margin = 3" in text and "block_paragraphs = true" in text


@pytest.mark.parametrize(
    "body",
    [
        "[style]\nside_margin = 99\n",
        "[style]\nline_spacing = 0.5\n",
        '[style]\nindent = "wide"\n',
        "[style]\nmargins = 2\n",
        '[fonts]\ntitle = "x.ttf"\n',
        '[style]\nblock_paragraphs = "yes"\n',
    ],
)
def test_bad_style_or_font_entries_are_rejected(tmp_path: Path, body: str) -> None:
    path = tmp_path / "book.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConversionError):
        load_meta_file(path)


def test_conversion_uses_style_and_fonts_from_the_book_file(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    (tmp_path / "Body.ttf").write_bytes(make_font("Book Face"))
    meta = tmp_path / "book.toml"
    meta.write_text(
        'title = "Styled"\n\n[style]\nside_margin = 4\n\n[fonts]\nbody = "Body.ttf"\n',
        encoding="utf-8",
    )
    outcome = convert(
        ConvertOptions(input=src, output=tmp_path / "out.epub", meta=meta, epubcheck=False)
    )
    assert outcome.written is not None
    with zipfile.ZipFile(outcome.written) as z:
        css = z.read("OEBPS/Styles/style.css").decode()
        assert "OEBPS/Fonts/body.ttf" in z.namelist()
    assert "div.page { margin: 1.5em 4em 1.5em 4em; }" in css
    assert '"Book Face"' in css


def test_missing_font_file_is_a_clear_error(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    meta = tmp_path / "book.toml"
    meta.write_text('[fonts]\nbody = "nowhere.ttf"\n', encoding="utf-8")
    outcome = convert(
        ConvertOptions(input=src, output=tmp_path / "out.epub", meta=meta, epubcheck=False)
    )
    assert outcome.exit_code == 3
    assert any("nowhere.ttf" in f for f in outcome.failures)
