# novel2epub

Converts DOCX and PDF novel manuscripts into EPUB 3, and checks that no text was lost on the way.

**Status: v1 (all five milestones).** DOCX and PDF conversion work end to end, with a text-fidelity check and EPUBCheck validation on every run. A portable Windows program needs no Python or Java. It has been tested on generated fixtures, a public-domain novel, and two real manuscripts. It has not yet been checked on physical e-readers (see `docs/reader-checklist.md`).

## Windows

Download `novel2epub-VERSION-windows.zip` and unzip it anywhere. There is nothing to install, and Java is included. Then either:

- drag a `.docx` or `.pdf` onto `novel2epub.exe`. A window shows a summary and waits for Enter, and the EPUB and reports appear next to the manuscript; or
- run `novel2epub.exe` from Command Prompt or PowerShell with the options below.

The zip is built by GitHub Actions on every push (the "Windows build" workflow, under "Artifacts"), or locally with `python scripts/build_exe.py` on Windows.

## What it does

```
novel2epub manuscript.docx -o book.epub --meta book.toml --cover cover.jpg
```

For every conversion it:

1. reads the manuscript into a format-neutral model of the book;
2. writes an EPUB 3 file (one file per chapter, contents page, navigation, accessibility metadata);
3. extracts the text from the EPUB and compares it, word for word, with text read independently from the manuscript. Anything deliberately removed (such as comments, a typed contents list, or page-number fields) is logged, and only unlogged differences count as loss;
4. validates the EPUB with EPUBCheck;
5. writes `NAME.report.md` and `NAME.report.json` (NAME being the EPUB's name) describing the structure it found, everything it removed, every heuristic decision with its confidence, and what needs a human to check.

If more than 0.1% of the text is missing, or EPUBCheck reports an error, the EPUB is written as `NAME.failed.epub` in the report directory, never at the requested output path.

### Reading the report

`NAME.report.md` is written for a person, in this order:

1. **Result, book, and a one-line summary:** chapters, words, items removed, warnings.
2. **Needs human review:** every warning, then every decision the converter made with confidence below 0.75 (an uncertain hyphen, a chapter heading found only by its size), each with its location: a paragraph number for DOCX, a page and position for PDF. Read this before publishing.
3. **Text fidelity:** how much of the source text was found in the EPUB, with any missing or added words quoted in context.
4. **Structure:** every section, how it was found (Word style, outline, pattern, size, ...) and with what confidence.
5. **Decisions:** how many of each kind, how many were uncertain, and by which method.
6. **Removed:** what was taken out and why, with the text itself (page numbers are summarised as a range). An unusual count is worth a look: a novel with no running headers removed, or hundreds of "comments".
7. **EPUBCheck:** the validator's verdict and any messages.

`NAME.report.json` holds the same information in full, with no lists cut short, for scripts. Both are named after the EPUB, so several books can share a folder.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Success with warnings (see the report) |
| 2 | Usage error |
| 3 | Input rejected (for example tracked changes, or an unreadable file) |
| 4 | Verification failed (text loss above the threshold, EPUBCheck errors, or warnings under `--strict`) |
| 5 | Internal error: a bug in novel2epub. Nothing is written; please report it with the message shown |

### Options

| Option | Effect |
|---|---|
| `-o PATH` | The EPUB to write, or a folder to write it in. Default: next to the manuscript |
| `--meta FILE` | Metadata in TOML (see below) |
| `--cover FILE` | Cover image, JPEG or PNG. Never generated |
| `--accept-changes`, `--reject-changes` | Resolve tracked changes. Without one of these, a document with tracked changes is rejected |
| `--smarten` | Convert straight quotes to curly quotes. Without it, quotation marks stay exactly as typed |
| `--chapter-regex PATTERN` | Regular expression for chapter headings, when automatic detection misses them |
| `--literal-tags` | Keep typed tags such as `<i>` as text rather than turning them into formatting |
| `--strict` | Treat warnings as failures |
| `--report-dir DIR` | Where to write the reports (default: next to the EPUB) |
| `--title`, `--author`, `--language` | Override metadata |
| `--threshold PERCENT` | Maximum text loss before failing (default 0.1) |
| `--epubcheck JAR`, `--no-epubcheck` | Use a specific EPUBCheck, or skip validation |

There is no network access and no telemetry.

### Metadata file

```toml
title = "The Lighthouse Keeper's Daughter"
authors = ["Ann Author"]
language = "en-GB"
identifier = "urn:uuid:0e8a1a3c-55a4-4c7e-9a8d-6b4b52c3f0a2"  # pin this; see below
publisher = "Small Press"
date = "2026"
series = "Coastal Tales"
series_index = "2"
description = "A story about a lighthouse and the sea."
rights = "Copyright 2026 Ann Author. All rights reserved."
isbn = "978-0-00-000000-2"
cover = "cover.jpg"
cover_alt = "A lighthouse at dusk"

[accessibility]
summary = "..."
conforms_to = "EPUB Accessibility 1.1 - WCAG 2.1 Level AA"  # only if you have checked
certified_by = "Small Press"
```

Command-line flags override the file, which overrides the document's own properties. Unknown keys are errors, to catch typos. If no identifier is given, one is derived from the title and authors, so it changes if either changes: pin it once the book has one.

## How DOCX manuscripts are read

- **Formatting:** italics, bold, small caps, all caps, superscript and subscript come from Word formatting, resolved through styles the way Word does. Balanced `<i>`, `<em>`, `<b>`, and `<strong>` tags typed into the text (common when writing for AO3) are also turned into formatting, unless `--literal-tags` is given.
- **Chapters**, in order of trust: heading styles or outline levels; `--chapter-regex`; lines such as "Chapter 12", "CHAPTER XII.", "Prologue"; and short bold, large, or capitalised lines after a page break. A typed contents list is recognised and replaced by the generated one, but only when it contains nothing that looks like prose. A manuscript with no detectable headings becomes a single chapter, with a warning.
- **Scene breaks:** typed markers (`***`, `* * *`, `#`, a line of underscores, ornament characters), Word horizontal rules, small ornament images on a line of their own, a bottom border on the last paragraph of a scene, and two or more empty paragraphs. All become one consistent `* * *` break. A single empty paragraph is not treated as a break, but is reported for review.
- **Front and back matter:** title page, copyright page, dedication, and epigraph before the first chapter; acknowledgements, afterword, and "About the Author" after the last. Classification is heuristic and reported with its confidence.
- **Footnotes and endnotes** become pop-up notes. Custom note marks (such as `*`) are kept.
- **Removed, and listed in the report:** comments, hidden text, Word's generated contents and page-number fields, empty paragraphs, and typed scene-break markers (replaced by the standard break).
- **Text is never "fixed".** Curly or straight quotes, dashes, ellipses, and spelling stay exactly as typed. Runs of spaces collapse to one, as they would on any web page.

## How PDFs are read

A PDF records where each character is drawn, not which characters form a paragraph, a heading, or a footnote. Everything below is reconstruction, and is reported as such.

- **Text** comes from pdfminer.six, with font names and sizes. Italic and bold are inferred from font names (for example `Caladea-Italic`), so a font with an unhelpful name loses them. Ligatures (fi, fl, ffi) become plain letters.
- **Paragraphs** are rebuilt from first-line indents, short last lines in justified text, and vertical gaps, measured against the body text's own metrics. A paragraph that runs over a page break is joined back together.
- **End-of-line hyphens** are resolved from the document's own usage first, then a bundled English word list (SCOWL), then prefix rules. Every choice is logged, and uncertain ones are flagged for review.
- **Running headers, footers, and page numbers** are found by what repeats at the page edges: page numbers that run in step with the pages, and lines that repeat (including alternating left and right page headers). Only short lines at the very top or bottom of a page can be removed, a scene-break marker never is, and every removal is listed in the report.
- **Footnotes** at the foot of a page become linked pop-up notes when their marker is found raised in the text above. Footnote-like text with no matching marker stays where it is, with a warning.
- **Drop caps** (including an opening quotation mark set as a drop cap) are rejoined to their word.
- **Chapters and parts** come from the PDF outline (bookmarks) when it has one; then from a printed contents page; then from "Chapter N" style lines; then from large type at the top of a page. The report says which method was used.
- **Scene breaks:** typed markers such as `* * *`, small ornament images, and extra vertical gaps in mid-page. A gap at the foot of a page is never treated as a break.
- **Scanned PDFs** (images of pages with no text) are rejected. OCR is not available.
- **The fidelity check** compares against text extracted by a second, independent engine (PDFium). Hyphenation and dash spacing are compared neutrally, because those are layout decisions rather than lost text.

## Limitations

These are known and deliberate for v1:

- Tables are flattened into paragraphs, with a warning.
- Text boxes are kept, but placed after the paragraph they are anchored to.
- Charts and drawing shapes without text are skipped, with a warning.
- EMF and WMF images can only be converted to PNG on Windows. Elsewhere they stop the conversion with a clear message.
- PDF: multi-column pages and tables are detected and reported, not converted: their text is kept, but the reading order on those pages may be wrong.
- PDF: italic and bold are inferred from font names. A typeface whose names do not say "Italic" or "Bold" loses them, and the fidelity check cannot notice.
- PDF: the fidelity check cannot see furniture that was *not* removed (a header left in the text is present in the source too). The report's removal counts are the check: a long book with no running headers removed is worth a look.
- PDF: PDFs rarely record their language; set `--language` if the book is not in English.
- Only English is supported. Other languages convert, but chapter and front-matter detection expect English words.
- No conversion is perfect. The report lists every decision the converter was unsure of; read the "Needs human review" section before publishing.

## Development

Python 3.11 or later, and Java 11 or later for EPUBCheck.

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip
.venv/bin/pytest
.venv/bin/ruff check src tests scripts && .venv/bin/mypy
python scripts/make_fixtures.py         # regenerate tests/fixtures (needs LibreOffice)
python scripts/preview.py book.epub previews --pages 1,2,3   # render pages to PNG (needs playwright)
python scripts/build_exe.py --jdk PATH  # portable program (needs packaging/requirements-build.txt and a JDK 21)
```

The build trims a Java runtime to the modules EPUBCheck needs (`jlink`), bundles it with EPUBCheck and the Python libraries (PyInstaller), writes the licence notices, and then converts a DOCX and a PDF with the built program while no other Java is reachable, failing unless EPUBCheck ran clean on both. PyInstaller does not cross-compile: build on Windows for Windows.

The `dev` extra installs the `epubcheck` package (BSD-3-Clause), which bundles EPUBCheck 5.3.0. To use a different jar, set `NOVEL2EPUB_EPUBCHECK` to its path. EPUBCheck tests are skipped if Java or the jar cannot be found.

Manuscripts used for local testing go in `local/`, which git ignores. Tests that use them are skipped when they are absent.

## Documents

- `docs/PLAN.md`: architecture, dependencies and licences, decisions
- `docs/markup.md`: the XHTML and class vocabulary the writer produces
- `docs/css-decisions.md`: what the stylesheet does and avoids, and why
- `docs/reader-checklist.md`: the manual check on real e-readers
- `tests/fixtures/README.md`: where the test corpus comes from

## Licence

MIT. See `LICENSE`.
