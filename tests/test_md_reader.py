"""The Markdown front end, and its independent reference text."""

from __future__ import annotations

from pathlib import Path

import pytest
from books import png

from novel2epub.diagnostics import Diagnostics
from novel2epub.ir import BlockQuote, Image, Italic, LineBreak, Paragraph, SceneBreak
from novel2epub.markdown.reader import read_markdown
from novel2epub.normalise.passes import normalise_book
from novel2epub.pipeline import ConvertOptions, ExitCode, convert
from novel2epub.verify.epubcheck import find_epubcheck
from novel2epub.verify.reference_md import markdown_text

BOOK = """\
---
title: The Harbour
author: Ann Author
language: en-GB
---

# The Harbour

## Chapter One

The harbour was old, and the *Mary Rose* was older.[^1]
Nobody remembered who had built it, not even the snake_case clerk.

[^1]: Rebuilt in 1850, after the storm.

***

Morning came **quickly**.\\
The gulls were already up.

## Chapter Two

> Keep the light burning.

She did. It cost her 5 \\* 3 nights of sleep, and a [lantern](https://example.org/lamp).

![A map of the harbour](map.png)
"""


def read(tmp_path: Path, text: str = BOOK):  # type: ignore[no-untyped-def]
    (tmp_path / "map.png").write_bytes(png(400, 300))
    path = tmp_path / "book.md"
    path.write_text(text, encoding="utf-8")
    diag = Diagnostics()
    result = read_markdown(path, diag)
    normalise_book(result.book, diag)
    return result.book, diag


def plain(nodes) -> str:  # type: ignore[no-untyped-def]
    out = []
    for n in nodes or []:
        if hasattr(n, "text"):
            out.append(n.text)
        elif hasattr(n, "children"):
            out.append(plain(n.children))
    return "".join(out)


def test_front_matter_gives_metadata(tmp_path: Path) -> None:
    book, _ = read(tmp_path)
    assert book.metadata.title == "The Harbour"
    assert book.metadata.authors == ["Ann Author"]
    assert book.metadata.language == "en-GB"


def test_chapters_and_blocks(tmp_path: Path) -> None:
    book, _ = read(tmp_path)
    assert [plain(s.title) for s in book.body] == ["Chapter One", "Chapter Two"]
    one = book.body[0].blocks
    assert any(isinstance(b, SceneBreak) for b in one)
    first = one[0]
    assert isinstance(first, Paragraph)
    assert plain(first.content).startswith("The harbour was old, and the Mary Rose was older.")
    assert "snake_case" in plain(first.content)
    assert any(isinstance(n, Italic) for n in first.content)
    last = [b for b in one if isinstance(b, Paragraph)][-1]
    assert any(isinstance(n, LineBreak) for n in last.content)
    two = book.body[1].blocks
    assert isinstance(two[0], BlockQuote)
    assert "5 * 3 nights" in plain(two[1].content)  # type: ignore[union-attr]
    assert any(isinstance(b, Image) and b.alt == "A map of the harbour" for b in two)


def test_footnotes_wherever_they_are_defined(tmp_path: Path) -> None:
    book, _ = read(tmp_path)
    assert len(book.notes) == 1
    note = next(iter(book.notes.values()))
    assert plain(note.blocks[0].content).strip() == "Rebuilt in 1850, after the storm."  # type: ignore[union-attr]


def test_sources_are_markdown_lines(tmp_path: Path) -> None:
    book, _ = read(tmp_path)
    first = book.body[0].blocks[0]
    assert str(first.source) == "book.md line 11"  # type: ignore[union-attr]


def test_without_front_matter_a_single_top_heading_is_the_title(tmp_path: Path) -> None:
    book, _ = read(tmp_path, "# Salt\n\n## One\n\nA.\n\n## Two\n\nB.\n")
    assert book.metadata.title == "Salt"
    assert [plain(s.title) for s in book.body] == ["One", "Two"]


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("# Title\n\nSome *em* and **strong** text.", "Title Some em and strong text."),
        ("A [link](http://x.org) and ![pic](a.png) here.", "A link and here."),
        ("snake_case and _emphasis_ and __bold__.", "snake_case and emphasis and bold."),
        ("Line one  \nline two\\\nline three", "Line one line two line three"),
        ("> quoted\n> more", "quoted more"),
        ("***\n\nafter\n\n* * *", "after"),
        ("1. first\n2. second\n- third", "first second third"),
        ("Escaped \\* star and `code` span.", "Escaped * star and code span."),
        ("Text[^n] here.\n\n[^n]: The note.\n\nMore.", "Text here. More. The note."),
        ("---\ntitle: X\n---\nBody.", "Body."),
        ("Setext\n======\n\ntext", "Setext text"),
        ("Tom &amp; Jerry <br> again", "Tom & Jerry again"),
    ],
)
def test_reference_text(markdown: str, expected: str) -> None:
    assert markdown_text(markdown) == expected


TOOL = find_epubcheck()


def test_pipeline_fidelity(tmp_path: Path) -> None:
    (tmp_path / "map.png").write_bytes(png(400, 300))
    src = tmp_path / "book.md"
    src.write_text(BOOK, encoding="utf-8")
    outcome = convert(
        ConvertOptions(input=src, output=tmp_path / "book.epub", epubcheck=TOOL is not None)
    )
    assert outcome.exit_code in (ExitCode.OK, ExitCode.WARNINGS), outcome.summary()
    fid = outcome.fidelity_main
    assert fid is not None and fid.ratio == 1.0 and not fid.added, outcome.summary()
    assert not fid.unmatched_removals, outcome.summary()
    if TOOL is not None:
        assert outcome.epubcheck is not None and not outcome.epubcheck.messages
