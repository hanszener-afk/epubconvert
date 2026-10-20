"""Images from outside a Word document: checked, and converted to an EPUB core type."""

from __future__ import annotations

import io

from PIL import Image as PILImage

from novel2epub.diagnostics import ConversionError

CORE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}


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
