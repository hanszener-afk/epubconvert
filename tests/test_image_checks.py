"""Image size checks and display widths, for every image in the book."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest
from books import minimal_book, png
from lxml import etree
from PIL import Image as PILImage

from novel2epub.diagnostics import Diagnostics
from novel2epub.epub.writer import build_epub
from novel2epub.images import describe_size, size_problems
from novel2epub.ir import Image, Resource
from novel2epub.verify.epubcheck import find_epubcheck, run_epubcheck

X = "{http://www.w3.org/1999/xhtml}"


def noisy_png(width: int, height: int) -> bytes:
    """A PNG that does not compress, to make a large file."""
    im = PILImage.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _book_with(data: bytes, width: float | None = None):  # type: ignore[no-untyped-def]
    book = minimal_book()
    book.resources["pic"] = Resource("pic", "image/png", data)
    book.body[0].blocks.append(Image("pic", "A picture", width=width))
    return book


def _codes(book) -> set[str]:  # type: ignore[no-untyped-def]
    diag = Diagnostics()
    build_epub(book, diag)
    return {i.code for i in diag.warnings}


def test_a_well_sized_image_raises_nothing() -> None:
    assert not {"image-pixels", "image-file-size", "image-few-pixels"} & _codes(
        _book_with(png(1400, 1000))
    )


def test_more_than_apples_pixel_limit_is_warned() -> None:
    assert "image-pixels" in _codes(_book_with(png(2500, 2400)))  # 6 million pixels


def test_a_large_file_is_warned() -> None:
    assert "image-file-size" in _codes(_book_with(noisy_png(1350, 1350)))  # about 5.5 MB


def test_too_few_pixels_for_its_width_is_warned() -> None:
    assert "image-few-pixels" in _codes(_book_with(png(300, 200), width=100))
    assert "image-few-pixels" not in _codes(_book_with(png(300, 200), width=25))
    # Without a width, a picture is shown at its own size and never stretched.
    assert "image-few-pixels" not in _codes(_book_with(png(300, 200)))


def test_size_problems_and_description_for_the_window() -> None:
    assert size_problems(2500, 2400, 1000, None) == ["more than 5.6 million pixels (Apple's limit)"]
    assert size_problems(300, 200, 1000, 100) == [
        "only 300 pixels wide: may look soft at this width"
    ]
    assert size_problems(300, 200, 1000, None) == []
    assert size_problems(300, 200, 1000, 25) == []
    assert describe_size(1200, 800, 345_000) == "1200 x 800, 345 KB"
    assert describe_size(20, 20, 300) == "20 x 20, under 1 KB"


def test_width_is_written_on_the_image() -> None:
    book = _book_with(png(1400, 1000), width=60)
    z = zipfile.ZipFile(io.BytesIO(build_epub(book, Diagnostics())))
    page = next(n for n in z.namelist() if n.endswith("chapter-001.xhtml"))
    img = etree.fromstring(z.read(page)).find(f".//{X}img")
    assert img is not None and img.get("style") == "width: 60%"


TOOL = find_epubcheck()


@pytest.mark.epubcheck
@pytest.mark.skipif(TOOL is None, reason="EPUBCheck or Java not found")
def test_width_passes_epubcheck(tmp_path: Path) -> None:
    out = tmp_path / "book.epub"
    out.write_bytes(build_epub(_book_with(png(1400, 1000), width=45), Diagnostics()))
    assert TOOL is not None
    result = run_epubcheck(TOOL, out)
    assert result.ran and not result.messages, [str(m) for m in result.messages]
