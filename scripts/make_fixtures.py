"""Regenerate the committed DOCX fixtures in tests/fixtures/docx.

    python scripts/make_fixtures.py

Writes the six synthetic fixtures, and builds a realistic manuscript from
Jane Austen's Persuasion (public domain; Project Gutenberg text with the
Gutenberg header and footer removed). The Persuasion DOCX is round-tripped
through LibreOffice when it is installed, so the committed file has
LibreOffice-authored XML rather than ours.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import docx_fixtures as fx  # noqa: E402
from docxbuild import PAGE_BREAK, Docx, p, r  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "docx"
PDF_OUT = ROOT / "tests" / "fixtures" / "pdf"
WORDS = ["One", "Two", "Three", "Four", "Five", "Six"]
PERSUASION = ROOT / "tests" / "fixtures" / "gutenberg" / "persuasion.txt"


def _runs(text: str) -> str:
    """Gutenberg marks italics as _word_; turn them into italic runs."""
    parts = re.split(r"_([^_]+)_", text)
    return "".join(r(part, i=bool(n % 2)) for n, part in enumerate(parts) if part)


def persuasion_paragraphs() -> list[str]:
    lines = PERSUASION.read_text(encoding="utf-8").splitlines()
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in [*lines, ""]:
        if line.strip():
            buf.append(line.strip())
        elif buf:
            paragraphs.append(" ".join(buf))
            buf = []
    return paragraphs


def persuasion_chapters(count: int) -> list[list[str]]:
    """The first `count` chapters of Persuasion, as lists of paragraphs."""
    chapters: list[list[str]] = []
    for para in persuasion_paragraphs():
        if re.fullmatch(r"CHAPTER [IVXL]+\.", para):
            if chapters and len(chapters) == count:
                break
            chapters.append([])
        elif chapters:
            chapters[-1].append(para)
    return chapters


BODY = '<w:ind w:firstLine="360"/><w:spacing w:after="0" w:line="276" w:lineRule="auto"/>'


def clean_book() -> Docx:
    """A cleanly typeset novel: justified, indented, styled chapter headings."""
    d = Docx(title="Persuasion (opening chapters)", creator="Jane Austen",
             modified="2026-01-15T10:00:00Z")  # fmt: skip
    d.add(p(r("Persuasion"), style="Title", jc="center"), p(r("by Jane Austen"), jc="center"))
    for n, chapter in enumerate(persuasion_chapters(3)):
        d.add(p(r(f"Chapter {WORDS[n]}"), style="Heading1", jc="center", page_break_before=True))
        for k, para in enumerate(chapter):
            if n == 1 and k == len(chapter) // 2:
                d.add(p(r("* * *"), jc="center"))
            d.add(p(_runs(para), jc="both", extra_ppr=BODY))
    return d


def to_pdf(docx: Docx, name: str, soffice: str, bookmarks: bool = True) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"{name}.docx"
        docx.save(src)
        value = str(bookmarks).lower()
        opts = '{"ExportBookmarks":{"type":"boolean","value":"' + value + '"}}'
        subprocess.run(
            [soffice, "--headless", "--convert-to", f"pdf:writer_pdf_Export:{opts}",
             "--outdir", tmp, str(src)],
            check=True, capture_output=True, timeout=300,
        )  # fmt: skip
        PDF_OUT.mkdir(parents=True, exist_ok=True)
        target = PDF_OUT / f"{name}.pdf"
        shutil.copy(Path(tmp) / f"{name}.pdf", target)
    print("wrote", target)
    return target


def scanned(source: Path, pages: int) -> None:
    """Rasterise pages of a PDF into an image-only PDF, as a scanner would."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(source))
    images = [doc[i].render(scale=100 / 72).to_pil().convert("L") for i in range(pages)]
    target = PDF_OUT / "scanned.pdf"
    images[0].save(target, save_all=True, append_images=images[1:], resolution=100)
    print("wrote", target)


def persuasion() -> Docx:
    paragraphs = persuasion_paragraphs()
    d = Docx(title="Persuasion", creator="Jane Austen", modified="2026-01-15T10:00:00Z")
    first_chapter = True
    in_contents = False
    for para in paragraphs:
        if para == "Persuasion":
            d.add(p(r("Persuasion", b=True, sz=48), jc="center"))
        elif para.startswith("by Jane Austen") or para == "(1818)":
            d.add(p(r(para), jc="center"))
        elif para == "Contents":
            in_contents = True
            d.add(p(PAGE_BREAK), p(r("Contents", b=True), jc="center"))
        elif re.fullmatch(r"(CHAPTER [IVXL]+\. ?)+", para):
            if in_contents:  # the typed contents list, one entry per line
                for entry in re.findall(r"CHAPTER [IVXL]+\.", para):
                    d.add(p(r(entry)))
                in_contents = False
                continue
            if not first_chapter:
                d.add(p(PAGE_BREAK))
            first_chapter = False
            d.add(p(r(para, b=True, sz=32), jc="center"))
        else:
            d.add(p(_runs(para)))
    return d


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in fx.ALL.items():
        build().save(OUT / f"{name}.docx")
        print("wrote", OUT / f"{name}.docx")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        print("LibreOffice not found; persuasion-lo.docx not regenerated")
        return
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "persuasion-lo.docx"
        persuasion().save(src)
        out_dir = Path(tmp) / "lo"
        subprocess.run(
            [soffice, "--headless", "--convert-to", "docx:MS Word 2007 XML",
             "--outdir", str(out_dir), str(src)],
            check=True, capture_output=True, timeout=300,
        )  # fmt: skip
        shutil.copy(out_dir / "persuasion-lo.docx", OUT / "persuasion-lo.docx")
    print("wrote", OUT / "persuasion-lo.docx")
    clean = to_pdf(clean_book(), "clean", soffice)
    to_pdf(clean_book(), "clean-nobookmarks", soffice, bookmarks=False)
    scanned(clean, 2)


if __name__ == "__main__":
    main()
