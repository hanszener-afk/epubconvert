"""Tiny generated fonts for tests: no font files are committed."""

from __future__ import annotations

import io

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


def make_font(family: str = "Test Serif", style: str = "Regular", fs_type: int = 0) -> bytes:
    """A valid TrueType font with three glyphs, named `family` `style`."""
    fb = FontBuilder(1000, isTTF=True)
    glyphs = [".notdef", "space", "A"]
    fb.setupGlyphOrder(glyphs)
    fb.setupCharacterMap({32: "space", 65: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((500, 700))
    pen.lineTo((900, 0))
    pen.closePath()
    empty = TTGlyphPen(None).glyph()
    fb.setupGlyf({".notdef": empty, "space": empty, "A": pen.glyph()})
    fb.setupHorizontalMetrics({g: (600, 0) for g in glyphs})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": style})
    fb.setupOS2(fsType=fs_type, sTypoAscender=800, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    fb.updateHead(created=0, modified=0)
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()
