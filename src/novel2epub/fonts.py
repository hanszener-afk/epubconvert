"""Load font files chosen for embedding, and check what they are.

Each file fills a slot: the body text in its four styles, and headings. Only
a regular body font is needed for the others to make sense; a reader fakes a
missing italic or bold, which looks worse but loses nothing.

A font's OS/2 table records what its licence allows. When it forbids
embedding, the font is still embedded (the owner may hold a licence that
allows it) but the report warns, because selling a book with an unlicensed
font is a legal risk.
"""

from __future__ import annotations

import io
from pathlib import Path

from fontTools.ttLib import TTFont, TTLibError

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.ir import EmbeddedFont

# slot -> (italic, bold)
FONT_SLOTS = {
    "body": (False, False),
    "body_italic": (True, False),
    "body_bold": (False, True),
    "body_bold_italic": (True, True),
    "headings": (False, False),
}
_RESTRICTED = 0x0002  # OS/2 fsType: restricted licence embedding
_BITMAP_ONLY = 0x0200


def load_fonts(files: dict[str, Path], diag: Diagnostics) -> list[EmbeddedFont]:
    """Read the chosen font files, in slot order."""
    unknown = sorted(set(files) - set(FONT_SLOTS))
    if unknown:
        raise ConversionError("font-slot", f"Unknown font slot(s): {', '.join(unknown)}.")
    if any(s.startswith("body_") for s in files) and "body" not in files:
        raise ConversionError(
            "font-incomplete",
            "An italic or bold body font was chosen without the regular one. Choose the "
            "regular body font as well.",
        )
    fonts = []
    for slot in FONT_SLOTS:
        if slot in files:
            fonts.append(_load(slot, files[slot], diag))
    return fonts


def _load(slot: str, path: Path, diag: Diagnostics) -> EmbeddedFont:
    suffix = path.suffix.lower()
    if suffix not in (".ttf", ".otf"):
        raise ConversionError("font-type", f"{path.name}: fonts must be .ttf or .otf files.")
    try:
        data = path.read_bytes()
        font = TTFont(io.BytesIO(data), lazy=True)
        family = _family(font)
        fs_type = font["OS/2"].fsType if "OS/2" in font else 0
        is_cff = "CFF " in font or "CFF2" in font
    except (OSError, TTLibError, KeyError, AssertionError, ValueError) as exc:
        raise ConversionError("font-invalid", f"{path.name} is not a usable font: {exc}") from exc
    if not family:
        raise ConversionError("font-invalid", f"{path.name} has no family name.")
    if fs_type & _RESTRICTED or fs_type & _BITMAP_ONLY:
        diag.warn(
            "font-licence",
            f"{path.name} ({family}) is marked as not licensed for embedding. It was embedded "
            "as asked; make sure your licence allows it before publishing.",
        )
    italic, bold = FONT_SLOTS[slot]
    media_type = "font/otf" if is_cff or suffix == ".otf" else "font/ttf"
    diag.info("font-embedded", f"Embedded {path.name} ({family}) for {slot.replace('_', ' ')}.")
    return EmbeddedFont(slot, family, italic, bold, media_type, data)


def _family(font: TTFont) -> str:
    names = font["name"]
    for name_id in (16, 1):  # typographic family, then family
        record = names.getDebugName(name_id)
        if record:
            return str(record).replace('"', "").strip()
    return ""
