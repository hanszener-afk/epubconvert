"""Book styling: page margins, paragraph spacing, line spacing, and embedded fonts."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from books import minimal_book
from fontbuild import make_font
from lxml import etree

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.epub.writer import build_epub, style_css
from novel2epub.fonts import FONT_SLOTS, load_fonts
from novel2epub.ir import EmbeddedFont, Style
from novel2epub.verify.epubcheck import find_epubcheck, run_epubcheck

OPF = {"opf": "http://www.idpf.org/2007/opf"}


def _zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


# --- style to CSS ---------------------------------------------------------


def test_default_style_adds_nothing() -> None:
    assert style_css(Style(), []) == ""


def test_margins_and_spacing_become_css() -> None:
    css = style_css(
        Style(side_margin=3, top_margin=2, bottom_margin=1, indent=2, paragraph_gap=0.5,
              line_spacing=1.4),
        [],
    )  # fmt: skip
    assert "div.page { margin: 2em 3em 1em 3em; }" in css
    assert "p { text-indent: 2em; margin-bottom: 0.5em; }" in css
    assert "body { line-height: 1.4; }" in css
    # The @supports rule in the base stylesheet must not override a chosen side margin.
    assert "@supports (margin: 1%) { div.page { margin: 2em 3em 1em 3em; } }" in css


def test_block_paragraphs_have_no_indent_and_a_gap() -> None:
    css = style_css(Style(block_paragraphs=True), [])
    assert "p { text-indent: 0; margin-bottom: 0.8em; }" in css


def test_style_values_are_checked() -> None:
    for bad in (Style(side_margin=-1), Style(line_spacing=9), Style(indent=50)):
        with pytest.raises(ValueError):
            style_css(bad, [])


# --- fonts ---------------------------------------------------------------


def _font_files(tmp_path: Path, fs_type: int = 0) -> dict[str, Path]:
    files = {}
    for slot, style in (("body", "Regular"), ("body_italic", "Italic"), ("headings", "Bold")):
        path = tmp_path / f"{slot}.ttf"
        path.write_bytes(make_font("Test Serif" if slot != "headings" else "Test Sans", style,
                                   fs_type))  # fmt: skip
        files[slot] = path
    return files


def test_load_fonts_reads_family_names(tmp_path: Path) -> None:
    diag = Diagnostics()
    fonts = load_fonts(_font_files(tmp_path), diag)
    by_slot = {f.slot: f for f in fonts}
    assert by_slot["body"].family == "Test Serif"
    assert by_slot["body_italic"].italic and not by_slot["body_italic"].bold
    assert by_slot["headings"].family == "Test Sans"
    assert by_slot["body"].media_type == "font/ttf"
    assert not diag.warnings


def test_font_that_forbids_embedding_is_embedded_with_a_warning(tmp_path: Path) -> None:
    diag = Diagnostics()
    fonts = load_fonts(_font_files(tmp_path, fs_type=0x0002), diag)
    assert len(fonts) == 3
    assert any(i.code == "font-licence" for i in diag.warnings)


def test_font_slots_are_fixed(tmp_path: Path) -> None:
    assert set(FONT_SLOTS) == {"body", "body_italic", "body_bold", "body_bold_italic", "headings"}
    with pytest.raises(ConversionError):
        load_fonts({"title": tmp_path / "x.ttf"}, Diagnostics())


def test_not_a_font_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    with pytest.raises(ConversionError):
        load_fonts({"body": bad}, Diagnostics())


def test_italic_without_regular_is_refused(tmp_path: Path) -> None:
    files = _font_files(tmp_path)
    with pytest.raises(ConversionError):
        load_fonts({"body_italic": files["body_italic"]}, Diagnostics())


def test_fonts_are_embedded_and_declared(tmp_path: Path) -> None:
    book = minimal_book()
    book.fonts = load_fonts(_font_files(tmp_path), Diagnostics())
    z = _zip(build_epub(book, Diagnostics()))
    names = z.namelist()
    assert "OEBPS/Fonts/body.ttf" in names and "OEBPS/Fonts/headings.ttf" in names
    opf = etree.fromstring(z.read("OEBPS/content.opf"))
    types = opf.xpath("//opf:manifest/opf:item[starts-with(@href, 'Fonts/')]/@media-type",
                      namespaces=OPF)  # fmt: skip
    assert set(types) == {"font/ttf"}
    css = z.read("OEBPS/Styles/style.css").decode()
    assert (
        '@font-face { font-family: "Test Serif"; font-style: italic; font-weight: normal; src: url("../Fonts/body_italic.ttf"); }'
        in css
    )
    assert 'body { font-family: "Test Serif", serif; }' in css
    assert 'h1, h2, h3, h4, h5, h6 { font-family: "Test Sans", serif; }' in css
    # Apple Books ignores embedded fonts unless the book says to use them.
    assert "META-INF/com.apple.ibooks.display-options.xml" in names


def test_no_fonts_no_apple_options() -> None:
    names = _zip(build_epub(minimal_book(), Diagnostics())).namelist()
    assert not any("Fonts/" in n for n in names)
    assert "META-INF/com.apple.ibooks.display-options.xml" not in names


TOOL = find_epubcheck()


@pytest.mark.epubcheck
@pytest.mark.skipif(TOOL is None, reason="EPUBCheck or Java not found")
def test_styled_book_with_fonts_passes_epubcheck(tmp_path: Path) -> None:
    book = minimal_book()
    book.style = Style(side_margin=2, top_margin=3, indent=1, paragraph_gap=0.3, line_spacing=1.3)
    book.fonts = load_fonts(_font_files(tmp_path), Diagnostics())
    out = tmp_path / "book.epub"
    out.write_bytes(build_epub(book, Diagnostics()))
    assert TOOL is not None
    result = run_epubcheck(TOOL, out)
    assert result.ran and result.messages == [], [str(m) for m in result.messages]


def test_embedded_font_type_is_data() -> None:
    # The writer takes font data, not paths: it never touches the disk.
    font = EmbeddedFont("body", "Test", False, False, "font/ttf", b"x")
    assert font.extension == "ttf"
