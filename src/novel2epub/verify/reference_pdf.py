"""Reference text from a PDF via PDFium, independent of the pdfminer front end.

Returns one string per page so that removals (running headers, page
numbers) can be cut from the page they came from.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium


def pdf_reference_pages(path: Path) -> list[str]:
    doc = pdfium.PdfDocument(str(path))
    try:
        pages = []
        for n in range(len(doc)):
            text = doc[n].get_textpage().get_text_range()
            # PDFium reports a hyphen it believes is a line-break hyphen as U+0002
            # or U+FFFE, depending on the version.
            text = text.replace("\x02", "-").replace("\ufffe", "-")
            pages.append(text.replace("\r\n", "\n").replace("\r", "\n"))
        return pages
    finally:
        doc.close()
