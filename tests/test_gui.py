"""The GUI: form checking without a display, and real conversions with one."""

from __future__ import annotations

import importlib.util
import os
import zipfile
from pathlib import Path

import docx_fixtures as fx
import pytest
from books import jpeg, png
from fontbuild import make_font

from novel2epub.gui import BookForm, Form, from_metafile, to_metafile, to_options
from novel2epub.ir import Style
from novel2epub.metadata import load_meta_file, save_meta_file

# --- conversion form (no display needed) ----------------------------------


def test_minimal_form_defaults_output_next_to_manuscript(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    opts = to_options(Form(input=str(src)))
    assert not isinstance(opts, str)
    assert opts.output == tmp_path / "book.epub"
    assert opts.revisions == "fail"
    assert opts.epubcheck
    assert opts.meta_data is None


def test_form_options_map_to_conversion_options(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    form = Form(
        input=str(src),
        output=str(tmp_path / "out" / "novel.epub"),
        changes="accept",
        smarten=True,
        literal_tags=True,
        chapter_regex=r"^Book \d+$",
        strict=True,
    )
    opts = to_options(form, book=BookForm(title="  A Title  ", authors="Ann; Bea"))
    assert not isinstance(opts, str)
    assert opts.output == tmp_path / "out" / "novel.epub"
    assert opts.revisions == "accept"
    assert opts.smarten and not opts.html_tags and opts.strict
    assert opts.chapter_regex == r"^Book \d+$"
    assert opts.meta_data is not None
    assert opts.meta_data.fields["title"] == "A Title"
    assert opts.meta_data.fields["authors"] == ["Ann", "Bea"]


def test_output_folder_gets_the_manuscript_name(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    out = tmp_path / "out"
    out.mkdir()
    opts = to_options(Form(input=str(src), output=str(out)))
    assert not isinstance(opts, str)
    assert opts.output == out / "book.epub"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"input": ""}, "Choose a manuscript"),
        ({"input": "missing.docx"}, "cannot be found"),
        ({"input": "notes.txt"}, ".docx, .pdf, .md, or .html"),
        ({"chapter_regex": "("}, "chapter pattern"),
        ({"output": "SAME"}, "overwrite the manuscript"),
    ],
)
def test_form_errors_are_plain_messages(tmp_path: Path, change: dict, message: str) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    fields = {"input": str(src)}
    for key, value in change.items():
        if value == "SAME":
            value = str(src)
        elif key == "input" and value:
            value = str(tmp_path / value)
        fields[key] = value
    result = to_options(Form(**fields))
    assert isinstance(result, str)
    assert message in result


# --- book details and appearance (no display needed) ------------------------


def _book_form(tmp_path: Path) -> BookForm:
    font = tmp_path / "Body.ttf"
    font.write_bytes(make_font())
    (tmp_path / "map.png").write_bytes(png(40, 30))
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(jpeg(600, 900))
    return BookForm(
        title="The Long Book", subtitle="A Novel", authors="Ann Author; Bea Writer",
        language="en-GB", identifier="urn:uuid:0e8a1a3c-55a4-4c7e-9a8d-6b4b52c3f0a2",
        publisher="Small Press", date="2026-10", series="Tales", series_index="2",
        description="Two lines.\nOf description.", rights="All rights reserved.",
        isbn="978-0-00-000000-2", cover=str(cover), cover_alt="A lighthouse",
        a11y_summary="Plain text.", conforms_to="", certified_by="",
        side_margin="3", top_margin="", bottom_margin="", indent="1.2",
        paragraph_gap="", block_paragraphs=False, line_spacing="1.4",
        font_body=str(font),
        images=((str(cover), "The cover again"), (str(font.with_name("map.png")), "")),
    )  # fmt: skip


def test_book_form_becomes_a_book_file_and_back(tmp_path: Path) -> None:
    form = _book_form(tmp_path)
    meta = to_metafile(form)
    assert not isinstance(meta, str)
    assert meta.fields["authors"] == ["Ann Author", "Bea Writer"]
    assert meta.fields["description"] == "Two lines.\nOf description."
    assert meta.style == Style(side_margin=3, indent=1.2, line_spacing=1.4)
    assert meta.fonts["body"] == Path(form.font_body)
    assert meta.images == [(Path(form.images[0][0]), "The cover again"),
                           (Path(form.images[1][0]), None)]  # fmt: skip
    path = tmp_path / "book.toml"
    save_meta_file(meta, path)
    assert from_metafile(load_meta_file(path)) == form


def test_blank_book_form_means_nothing_chosen() -> None:
    meta = to_metafile(BookForm())
    assert not isinstance(meta, str)
    assert meta.fields == {} and meta.style == Style() and meta.fonts == {}
    assert meta.cover is None


def test_added_image_that_is_missing_is_reported(tmp_path: Path) -> None:
    result = to_metafile(BookForm(images=((str(tmp_path / "gone.png"), ""),)))
    assert isinstance(result, str) and "gone.png" in result


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("language", "not a tag!", "language"),
        ("side_margin", "wide", "Side margin"),
        ("side_margin", "40", "between 0 and 10"),
        ("line_spacing", "0.2", "between 1 and 3"),
        ("isbn", "123", "ISBN"),
        ("date", "next spring", "date"),
        ("cover", "nowhere.jpg", "cannot be found"),
        ("font_body", "nowhere.ttf", "cannot be found"),
    ],
)
def test_book_form_errors_are_plain_messages(field: str, value: str, message: str) -> None:
    result = to_metafile(BookForm(**{field: value}))
    assert isinstance(result, str)
    assert message in result


def test_plain_words_replace_command_line_options() -> None:
    from novel2epub.gui import plain

    assert plain("Set it with --title.") == "Set it with the Title box on the Book details tab."
    assert "Accept all" in plain(
        "Resolve them in Word, or pass --accept-changes or --reject-changes."
    )
    assert "--" not in plain("Use --chapter-regex if ... Set it with --language.")


# --- the window (needs tkinter and a display) ------------------------------

needs_display = pytest.mark.skipif(
    importlib.util.find_spec("tkinter") is None
    or (os.name != "nt" and not os.environ.get("DISPLAY")),
    reason="needs tkinter and a display",
)


@needs_display
def test_window_converts_and_shows_the_summary(tmp_path: Path) -> None:
    from novel2epub.gui import App

    src = fx.styled().save(tmp_path / "book.docx")
    app = App()
    try:
        app.input_var.set(str(src))
        app.epubcheck_enabled = False
        outcome = app.convert_now()
        assert outcome is not None
        assert (tmp_path / "book.epub").is_file()
        assert "text fidelity" in app.summary_text()
        assert "Converted" in app.status_var.get()
        assert app.report_path == tmp_path / "book.report.md"
    finally:
        app.root.destroy()


@needs_display
def test_window_uses_book_details_and_appearance(tmp_path: Path) -> None:
    from novel2epub.gui import App

    src = fx.styled().save(tmp_path / "book.docx")
    app = App()
    try:
        app.input_var.set(str(src))
        app.epubcheck_enabled = False
        app.set_book_form(_book_form(tmp_path))
        outcome = app.convert_now()
        assert outcome is not None and outcome.written is not None
        with zipfile.ZipFile(outcome.written) as z:
            opf = z.read("OEBPS/content.opf").decode()
            css = z.read("OEBPS/Styles/style.css").decode()
            assert "OEBPS/Fonts/body.ttf" in z.namelist()
        assert "The Long Book" in opf and "Bea Writer" in opf
        assert "margin: 1.5em 3em 1.5em 3em" in css
        assert "line-height: 1.4" in css
    finally:
        app.root.destroy()


@needs_display
def test_window_saves_and_reopens_the_book_file(tmp_path: Path) -> None:
    from novel2epub.gui import App

    form = _book_form(tmp_path)
    path = tmp_path / "book.toml"
    app = App()
    try:
        app.set_book_form(form)
        assert app.save_book_file(path)
        app.set_book_form(BookForm())
        assert app.book_form() == BookForm()
        assert app.open_book_file(path)
        assert app.book_form() == form
    finally:
        app.root.destroy()


@needs_display
def test_book_file_next_to_the_manuscript_is_opened(tmp_path: Path) -> None:
    from novel2epub.gui import App

    src = fx.styled().save(tmp_path / "book.docx")
    save_meta_file(to_metafile(BookForm(title="Found Beside It")), tmp_path / "book.toml")  # type: ignore[arg-type]
    app = App()
    try:
        app.input_var.set(str(src))
        app.manuscript_chosen()
        assert app.book_form().title == "Found Beside It"
    finally:
        app.root.destroy()


@needs_display
def test_window_shows_form_errors_without_converting(tmp_path: Path) -> None:
    from novel2epub.gui import App

    app = App()
    try:
        app.input_var.set(str(tmp_path / "nothing.docx"))
        assert app.convert_now() is None
        assert "cannot be found" in app.status_var.get()
    finally:
        app.root.destroy()


@needs_display
def test_images_tab_adds_describes_and_removes(tmp_path: Path) -> None:
    from novel2epub.gui import App

    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(png(20, 20))
    app = App()
    try:
        app.add_images([tmp_path / "a.png", tmp_path / "b.png", tmp_path / "a.png"])
        assert [Path(f).name for f, _ in app.book_form().images] == ["a.png", "b.png"]
        app.describe_image(str(tmp_path / "b.png"), "A bee")
        assert app.book_form().images[1][1] == "A bee"
        app.remove_image(str(tmp_path / "a.png"))
        assert [Path(f).name for f, _ in app.book_form().images] == ["b.png"]
    finally:
        app.root.destroy()
