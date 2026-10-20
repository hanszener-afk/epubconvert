"""Image placeholders: a paragraph "[image: file]" or "[image: file | description]"."""

from __future__ import annotations

from pathlib import Path

import pytest
from books import jpeg, minimal_book, png
from docxbuild import Docx, p, r

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.ir import Image, ManualRef, Paragraph, Text
from novel2epub.metadata import MetaFile, load_meta_file, save_meta_file
from novel2epub.normalise.placeholders import insert_images
from novel2epub.pipeline import ConvertOptions, ExitCode, convert
from novel2epub.verify.epubcheck import find_epubcheck


def _book(*texts: str):  # type: ignore[no-untyped-def]
    book = minimal_book()
    book.body[0].blocks = [Paragraph([Text(t)], source=ManualRef(f"para {n}"))
                           for n, t in enumerate(texts)]  # fmt: skip
    return book


def test_placeholder_paragraph_becomes_an_image(tmp_path: Path) -> None:
    (tmp_path / "map.png").write_bytes(png(300, 200))
    book = _book("Before.", "[image: map.png | A map of the harbour]", "After.")
    diag = Diagnostics()
    insert_images(book, [], [tmp_path], diag)
    blocks = book.body[0].blocks
    assert isinstance(blocks[1], Image)
    assert blocks[1].alt == "A map of the harbour"
    assert book.resources[blocks[1].resource_id].media_type == "image/png"
    removal = next(rm for rm in diag.removals if rm.kind == "image-placeholder")
    assert removal.text == "[image: map.png | A map of the harbour]"


@pytest.mark.parametrize(
    "text", ["[image: map.png]", "  [Image:map.png]  ", "[IMAGE :  map.png ]", "[image: MAP.PNG]"]
)
def test_placeholder_spelling_is_forgiving(tmp_path: Path, text: str) -> None:
    (tmp_path / "map.png").write_bytes(png(300, 200))
    book = _book(text)
    insert_images(book, [], [tmp_path], Diagnostics())
    assert isinstance(book.body[0].blocks[0], Image)


def test_added_images_are_found_by_file_name_and_give_a_description(tmp_path: Path) -> None:
    elsewhere = tmp_path / "pictures"
    elsewhere.mkdir()
    (elsewhere / "ship.jpg").write_bytes(jpeg(400, 300))
    book = _book("[image: ship.jpg]")
    diag = Diagnostics()
    insert_images(book, [(elsewhere / "ship.jpg", "The Mary Rose")], [tmp_path], diag)
    image = book.body[0].blocks[0]
    assert isinstance(image, Image) and image.alt == "The Mary Rose"
    assert book.resources[image.resource_id].media_type == "image/jpeg"


def test_placeholder_description_wins_over_the_added_one(tmp_path: Path) -> None:
    (tmp_path / "ship.jpg").write_bytes(jpeg(400, 300))
    book = _book("[image: ship.jpg | Her sails at dawn]")
    insert_images(book, [(tmp_path / "ship.jpg", "The Mary Rose")], [], Diagnostics())
    assert book.body[0].blocks[0].alt == "Her sails at dawn"  # type: ignore[union-attr]


def test_same_image_twice_is_stored_once(tmp_path: Path) -> None:
    (tmp_path / "rule.png").write_bytes(png(200, 40))
    book = _book("[image: rule.png]", "Text.", "[image: rule.png]")
    insert_images(book, [], [tmp_path], Diagnostics())
    assert len(book.resources) == 1


def test_missing_description_is_warned(tmp_path: Path) -> None:
    (tmp_path / "map.png").write_bytes(png(300, 200))
    diag = Diagnostics()
    insert_images(_book("[image: map.png]"), [], [tmp_path], diag)
    assert any(i.code == "image-description-missing" for i in diag.warnings)


def test_missing_image_stops_with_its_location(tmp_path: Path) -> None:
    with pytest.raises(ConversionError) as exc:
        insert_images(_book("Text.", "[image: nowhere.png]"), [], [tmp_path], Diagnostics())
    assert "nowhere.png" in str(exc.value) and "para 1" in str(exc.value)


def test_placeholder_inside_a_sentence_stays_text_with_a_warning(tmp_path: Path) -> None:
    (tmp_path / "map.png").write_bytes(png(300, 200))
    book = _book("See [image: map.png] here.")
    diag = Diagnostics()
    insert_images(book, [], [tmp_path], diag)
    assert isinstance(book.body[0].blocks[0], Paragraph)
    assert any(i.code == "image-placeholder-inline" for i in diag.warnings)


def test_not_an_image_is_refused(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not a picture", encoding="utf-8")
    with pytest.raises(ConversionError):
        insert_images(_book("[image: notes.txt]"), [], [tmp_path], Diagnostics())


# --- the book file -----------------------------------------------------------


def test_book_file_keeps_added_images(tmp_path: Path) -> None:
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "map.png").write_bytes(png(10, 10))
    (tmp_path / "art" / "ship.jpg").write_bytes(jpeg(10, 10))
    meta = MetaFile(
        {"title": "T"},
        images=[((tmp_path / "art" / "map.png").resolve(), "A map"),
                ((tmp_path / "art" / "ship.jpg").resolve(), None)],
    )  # fmt: skip
    path = tmp_path / "book.toml"
    save_meta_file(meta, path)
    text = path.read_text(encoding="utf-8")
    assert '[[images]]\nfile = "art/map.png"\ndescription = "A map"' in text
    assert load_meta_file(path) == meta


# --- whole conversions, with the fidelity check -------------------------------


TOOL = find_epubcheck()


def _check(outcome) -> None:  # type: ignore[no-untyped-def]
    assert outcome.exit_code in (ExitCode.OK, ExitCode.WARNINGS), outcome.summary()
    fid = outcome.fidelity_main
    assert fid is not None and fid.ratio == 1.0 and not fid.added, outcome.summary()
    assert not fid.unmatched_removals, outcome.summary()
    assert outcome.book is not None
    images = [b for s in outcome.book.sections() for b in s.blocks if isinstance(b, Image)]
    assert len(images) == 1
    if TOOL is not None:
        assert outcome.epubcheck is not None and not outcome.epubcheck.messages


def test_docx_with_a_placeholder(tmp_path: Path) -> None:
    (tmp_path / "map.png").write_bytes(png(300, 200))
    d = Docx(title="Maps", creator="Ann Author")
    for text in ("Chapter One", "It was a long way.", "[image: map.png | The route]", "Then home."):
        d.add(p(r(text), style="Heading1" if text.startswith("Chapter") else None))
    src = d.save(tmp_path / "maps.docx")
    _check(convert(ConvertOptions(input=src, output=tmp_path / "maps.epub",
                                  epubcheck=TOOL is not None)))  # fmt: skip


def test_markdown_with_a_placeholder(tmp_path: Path) -> None:
    (tmp_path / "map.png").write_bytes(png(300, 200))
    src = tmp_path / "maps.md"
    src.write_text(
        "---\ntitle: Maps\nauthor: Ann Author\n---\n\n# One\n\nA long way.\n\n"
        "[image: map.png | The route]\n\nThen home.\n\n# Two\n\nThe end.\n",
        encoding="utf-8",
    )
    _check(convert(ConvertOptions(input=src, output=tmp_path / "maps.epub",
                                  epubcheck=TOOL is not None)))  # fmt: skip


def test_html_with_a_placeholder_and_an_added_image(tmp_path: Path) -> None:
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    (pictures / "map.png").write_bytes(png(300, 200))
    src = tmp_path / "maps.html"
    src.write_text(
        '<html lang="en"><head><title>Maps</title><meta name="author" content="Ann">'
        "</head><body><h1>One</h1><p>A long way.</p><p>[image: map.png]</p><p>Home.</p>"
        "<h1>Two</h1><p>The end.</p></body></html>",
        encoding="utf-8",
    )
    meta = MetaFile({}, images=[(pictures / "map.png", "The route")])
    _check(convert(ConvertOptions(input=src, output=tmp_path / "maps.epub", meta_data=meta,
                                  epubcheck=TOOL is not None)))  # fmt: skip
