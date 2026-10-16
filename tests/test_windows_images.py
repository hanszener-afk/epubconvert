"""EMF images: converted to PNG on Windows, a clear refusal elsewhere.

Pillow renders Windows metafiles only on Windows (through GDI), so this test
checks a different outcome on each platform. The Windows build workflow runs
it on Windows.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest
from docxbuild import Docx, image_run, p, r

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.docx.reader import DocxOptions, read_docx
from novel2epub.ir import Image


def emf() -> bytes:
    """A minimal Enhanced Metafile: a 100x50 frame holding a filled rectangle."""

    def record(kind: int, payload: bytes) -> bytes:
        return struct.pack("<II", kind, 8 + len(payload)) + payload

    brush = record(39, struct.pack("<IIII", 1, 0, 0x00335599, 0))  # CREATEBRUSHINDIRECT
    select = record(37, struct.pack("<I", 1))  # SELECTOBJECT
    rect = record(43, struct.pack("<iiii", 10, 10, 90, 40))  # RECTANGLE
    eof = record(14, struct.pack("<III", 0, 16, 20))  # EOF
    body = brush + select + rect + eof
    header_size = 88
    total = header_size + len(body)
    header = struct.pack(
        "<II4i4iIIIIHHIIIiiii",
        1, header_size,
        0, 0, 99, 49,  # bounds, device units
        0, 0, 2645, 1322,  # frame, 0.01 mm
        0x464D4520, 0x10000, total, 5,  # " EMF", version, bytes, records
        2, 0, 0, 0, 0,  # handles, reserved, description, palette
        1920, 1080, 508, 286,  # reference device, pixels and millimetres
    )  # fmt: skip
    assert len(header) == header_size
    return header + body


def _docx(tmp_path: Path) -> Path:
    d = Docx()
    d.media["rIdEmf"] = ("chart.emf", emf())
    d.add(p(r("Before the chart.")))
    # 3 by 2 inches: larger than a scene-break ornament, so it stays an image.
    d.add(p(image_run("rIdEmf", 2743200, 1828800, "A small chart.")))
    d.add(p(r("After the chart.")))
    return d.save(tmp_path / "emf.docx")


@pytest.mark.skipif(sys.platform != "win32", reason="Pillow renders EMF only on Windows")
def test_emf_is_converted_to_png_on_windows(tmp_path: Path) -> None:
    diag = Diagnostics()
    book = read_docx(_docx(tmp_path), DocxOptions(), diag).book
    images = [b for s in book.body for b in s.blocks if isinstance(b, Image)]
    assert len(images) == 1
    resource = book.resources[images[0].resource_id]
    assert resource.media_type == "image/png"
    assert resource.data.startswith(b"\x89PNG")
    assert any(i.code == "image-converted" for i in diag.issues)


@pytest.mark.skipif(sys.platform == "win32", reason="EMF converts on Windows")
def test_emf_is_refused_clearly_elsewhere(tmp_path: Path) -> None:
    with pytest.raises(ConversionError) as exc:
        read_docx(_docx(tmp_path), DocxOptions(), Diagnostics())
    assert "can only be converted on Windows" in str(exc.value)
