"""Images from outside a Word document: checked, and converted to an EPUB core type."""

from __future__ import annotations

import io

from PIL import Image as PILImage

from novel2epub.diagnostics import ConversionError

CORE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}

# Apple Books' limit for images inside a book (its asset guide, 2023 onwards).
APPLE_MAX_PIXELS = 5_600_000
# The widely reported Kindle limit per image; Amazon also charges delivery by size.
MAX_FILE_BYTES = 5 * 1024 * 1024
# A rule of thumb, not a store rule: pixels for a picture stretched across a
# whole tablet's text column to look sharp. Without a chosen width a picture is
# shown at its own size (never stretched), so this applies only to a width.
FULL_WIDTH_PIXELS = 600


def size_findings(
    width: int, height: int, nbytes: int, display_width: float | None
) -> list[tuple[str, str]]:
    """(code, problem) for an image of this size, shown at `display_width` per cent."""
    found = []
    if width * height > APPLE_MAX_PIXELS:
        found.append(("image-pixels", "more than 5.6 million pixels (Apple's limit)"))
    if nbytes > MAX_FILE_BYTES:
        found.append(
            ("image-file-size", "larger than 5 MB (the reported Kindle limit; large files also "
             "raise Kindle's delivery charge)")
        )  # fmt: skip
    if display_width is not None and width < FULL_WIDTH_PIXELS * display_width / 100:
        found.append(("image-few-pixels", f"only {width} pixels wide: may look soft at this width"))
    return found


def size_problems(width: int, height: int, nbytes: int, display_width: float | None) -> list[str]:
    return [text for _, text in size_findings(width, height, nbytes, display_width)]


# Short labels for the window's Size column; the full text is in size_findings.
SHORT = {
    "image-pixels": "too large for Apple",
    "image-file-size": "over 5 MB",
    "image-few-pixels": "may look soft",
}


def describe_size(width: int, height: int, nbytes: int) -> str:
    if nbytes >= 1_000_000:
        size = f"{nbytes / 1_000_000:.1f} MB"
    elif nbytes >= 1000:
        size = f"{round(nbytes / 1000)} KB"
    else:
        size = "under 1 KB"
    return f"{width} x {height}, {size}"


def measure(data: bytes) -> tuple[int, int] | None:
    """Pixel size of image data, or None if it cannot be read."""
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            return im.size
    except (OSError, ValueError):
        return None


def to_core_image(data: bytes, src: str, where: str) -> tuple[bytes, str, tuple[int, int]]:
    """The image as an EPUB core type (converting to PNG if needed), and its size."""
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            size = im.size
            fmt = im.format or ""
            if fmt in CORE_FORMATS:
                return data, CORE_FORMATS[fmt], size
            im.load()
            out = io.BytesIO()
            converted = im.convert("RGBA") if im.mode not in ("RGB", "RGBA", "L", "LA") else im
            converted.save(out, format="PNG", optimize=False)
            return out.getvalue(), "image/png", size
    except (OSError, ValueError) as exc:
        if data.lstrip().startswith(b"<"):
            raise ConversionError(
                "image-unsupported",
                f"The image {src} at {where} is SVG, which is not supported yet. "
                "Replace it with a PNG or JPEG.",
            ) from exc
        raise ConversionError(
            "image-unsupported", f"The image {src} at {where} cannot be read: {exc}"
        ) from exc
