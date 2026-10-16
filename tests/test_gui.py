"""The GUI: form validation without a display, and one real conversion with one."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import docx_fixtures as fx
import pytest

from novel2epub.gui import Form, to_options

# --- form to options (no display needed) ----------------------------------


def test_minimal_form_defaults_output_next_to_manuscript(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    opts = to_options(Form(input=str(src)))
    assert not isinstance(opts, str)
    assert opts.output == tmp_path / "book.epub"
    assert opts.revisions == "fail"
    assert opts.epubcheck


def test_form_fields_map_to_options(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    form = Form(
        input=str(src),
        output=str(tmp_path / "out" / "novel.epub"),
        title="  A Title  ",
        authors="Ann Author; Bea Writer",
        language="en-GB",
        changes="accept",
        smarten=True,
        literal_tags=True,
        chapter_regex=r"^Book \d+$",
        strict=True,
    )
    opts = to_options(form)
    assert not isinstance(opts, str)
    assert opts.output == tmp_path / "out" / "novel.epub"
    assert opts.title == "A Title"
    assert opts.authors == ["Ann Author", "Bea Writer"]
    assert opts.language == "en-GB"
    assert opts.revisions == "accept"
    assert opts.smarten and not opts.html_tags and opts.strict
    assert opts.chapter_regex == r"^Book \d+$"


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
        ({"input": "notes.txt"}, ".docx or .pdf"),
        ({"chapter_regex": "("}, "chapter pattern"),
        ({"output": "SAME"}, "overwrite the manuscript"),
        ({"language": "not a tag!"}, "language"),
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
        summary = app.summary_text()
        assert "text fidelity" in summary
        assert "Converted" in app.status_var.get()
        assert app.report_path == tmp_path / "book.report.md"
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


def test_plain_words_replace_command_line_options() -> None:
    from novel2epub.gui import plain

    assert plain("Set it with --title.") == "Set it with the Title box."
    assert "Accept all" in plain(
        "Resolve them in Word, or pass --accept-changes or --reject-changes."
    )
    assert "--" not in plain("Use --chapter-regex if ... Set it with --language.")
