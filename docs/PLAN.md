# novel2epub: Step 0 plan (awaiting approval)

Status: approved 2026-10-16. Milestones 1 to 4 implemented.

## 1. Language and runtime

Python 3.11+ (developed and tested on 3.11 and 3.13). Reasons:

- The two hardest parts of the job, OOXML parsing and PDF glyph-level extraction, have mature, permissively licensed Python libraries (lxml, python-docx, pdfminer.six, pypdfium2).
- `tomllib`, `zipfile`, `difflib`, `unicodedata`, `argparse`, and `dataclasses` in the standard library cover metadata, packaging, diffing, normalisation, and the CLI without extra dependencies.
- EPUBCheck is Java whatever language we pick, so a JVM-free tool is not on offer in any language.

Nothing here justifies Rust or Go: throughput is not a concern for 100k-word novels, and the ecosystem for both input formats is weaker.

## 2. Architecture

```
input -> front end -> IR -> normalise -> EPUB writer -> EPUBCheck -> fidelity check -> report
                 \________ Diagnostics collector (warnings, removals, decisions) ________/
```

Package layout (one package, no plugin system):

```
src/novel2epub/
  ir.py              typed IR (dataclasses with slots), SourceRef
  diagnostics.py     Warning, Removal, Decision(confidence, SourceRef); one collector per run
  metadata.py        merge: CLI flags > meta file > document properties; validation
  docx/              reader.py, styles.py (inheritance + toggle resolution), revisions.py, notes.py, images.py
  pdf/               classify.py, extract.py (spans), lines.py, paragraphs.py, furniture.py
                     (headers/footers/page numbers), hyphenation.py, chapters.py, notes.py, layout_checks.py
  normalise/         scenebreaks.py, whitespace.py, smarten.py, structure.py (front/body/back matter)
  epub/              writer.py, xhtml.py, opf.py, nav.py, ncx.py, ids.py, zipper.py, style.css
  verify/            epubcheck.py (subprocess), fidelity.py, reference_text.py
  report/            model.py, json_out.py, md_out.py
  cli.py             argparse, exit codes
```

Boundary rules, enforced by an import-linter style test (a plain pytest that walks imports, no extra dependency): `docx/` and `pdf/` may import only `ir`, `diagnostics`, and shared utilities; `epub/` may import only `ir` and `diagnostics`.

### IR sketch

```
Book(metadata, front: list[Section], body: list[Part | Chapter], back: list[Section], notes, images)
Section(kind: titlepage|copyright|dedication|epigraph|foreword|acknowledgements|about-author|other, title?, blocks)
Chapter(title: Inline[] | None, label?, blocks, source)
Block = Paragraph | SceneBreak | Heading | BlockQuote | Epigraph | Verse(lines) | Image | NoteBlock
Inline = Text | Italic | Bold | SmallCaps | Sup | Sub | Link | NoteRef | LineBreak
```

Every node carries `SourceRef`: `DocxRef(part, paragraph_index, run_index?)` or `PdfRef(page, bbox)`.

### Fidelity check (the central requirement) done so it can actually catch loss

A fidelity check is worthless if the "source text" comes from the same code path that produced the IR: a front-end bug that drops a paragraph drops it from both sides. So the reference text is extracted independently:

- DOCX: a separate, deliberately dumb walker over every `w:t`, `w:tab`, `w:br`, and `w:sym` in `document.xml`, `footnotes.xml`, and `endnotes.xml`, sharing no code with the front end.
- PDF: pypdfium2's text extraction (a different engine, PDFium) while the front end uses pdfminer.six. Disagreement between engines is itself a useful signal.

Expected differences are reconciled from the Diagnostics log, not hidden: every logged removal (header, page number, comment, TOC field, deleted revision) is subtracted from the reference. Anything else missing is loss. Comparison runs on normalised word tokens (NFC, whitespace collapsed, ligatures expanded, soft hyphens and zero-width characters dropped, line-end hyphen joins matched either way). The EPUB side is extracted from spine XHTML only, excluding generated text (nav, backlinks, the scene-break glyph) which the writer marks with a known class.

## 3. Dependencies and licences

### Runtime

| Package | Licence | Why |
|---|---|---|
| lxml | BSD-3-Clause (bundles libxml2/libxslt, MIT) | OOXML parsing where python-docx falls short; XHTML/OPF generation |
| ~~python-docx~~ | MIT | Dropped in milestone 2: footnotes, endnotes, tracked changes, fields, VML, and style resolution all needed raw lxml, which left python-docx doing nothing useful |
| pdfminer.six | MIT | Glyph-level extraction: character, font name, size, bbox |
| pypdfium2 | Apache-2.0 or BSD-3-Clause (PDFium: BSD-3/Apache-2.0 with permissive third-party components) | Independent reference text for the fidelity check; PDF classification (text vs scanned); rendering for OCR if ever added |
| SCOWL word list (data, bundled) | Permissive (SCOWL and its public-domain and notice-only sources) | Dehyphenation evidence. Notices shipped in `src/novel2epub/data/SCOWL-COPYRIGHT.txt` |
| Pillow | MIT-CMU (HPND) | Cover and image validation (format, dimensions, colour mode) |
| PyYAML | MIT | Only if you want YAML metadata (see question 3). TOML needs nothing (`tomllib`) |

### Development and test only (not distributed)

| Package / tool | Licence | Why |
|---|---|---|
| pytest | MIT | Tests |
| hypothesis | MPL-2.0 | Property-based tests. MPL is file-level copyleft; dev-only, no impact on distribution |
| ruff | MIT | Lint and format |
| mypy | MIT | Type checking |
| EPUBCheck 5.x (Java) | BSD-3-Clause (bundles Saxon-HE, MPL-2.0; Jing, BSD-3) | Validation. Needs a JRE 11+. Not a Python dependency |
| LibreOffice (headless) | MPL-2.0 | Generating PDF fixtures from DOCX fixtures (real hyphenation, alternating headers, footnotes). Already present in this environment. Generated PDFs are committed, so tests do not need it |

### Rejected, with reasons

| Package | Licence | Problem |
|---|---|---|
| PyMuPDF / fitz | AGPL-3.0 or commercial | AGPL would force source release of anything distributed or served with it |
| EbookLib | AGPL-3.0 | Same. We also need byte-level control of the zip for determinism, so a hand-written writer is better anyway |
| img2pdf | LGPL-3.0 | Not needed: Pillow can write image-only PDFs for the scanned fixture |
| wordfreq | Apache-2.0 code, CC BY-SA 4.0 data, project frozen | Share-alike data in a distributed tool. See dehyphenation below |
| pandoc | GPL-2.0+ | Tempting for DOCX reading, but GPL and an external binary |
| pytesseract + Tesseract | Apache-2.0 | Permissive and fine as an optional `[ocr]` extra, but I recommend deferring OCR (question 13) |

All proposed runtime dependencies are permissive. You can distribute the tool, including commercially, provided you ship the licence notices (PDFium's third-party notices in particular if you ever ship a frozen binary).

### Dehyphenation word list

Recommendation: decide each line-end hyphen in this order: (1) in-document evidence (a novel of 80k+ words usually contains the joined or hyphenated form elsewhere); (2) a bundled word list derived from SCOWL (permissive, MIT-like licence), en-GB and en-US; (3) prefix rules (`re-`, `co-`, `pre-`, `non-`) with a lowered confidence; (4) if still undecided, keep the hyphen and record a low-confidence decision. Adds roughly 1 MB of data, no dependency.

## 4. Design decisions I intend to take unless you object

- DOCX run properties are resolved properly: docDefaults, paragraph style chain (`basedOn`), character style chain, then direct formatting, with the OOXML toggle-property rules for bold/italic/caps. python-docx does not do this; getting it wrong silently loses or inverts italics, which matters for fiction.
- `w:caps` (display all caps) text is output uppercased, with a logged decision; `w:smallCaps` becomes a `span` with `font-variant: small-caps`.
- Hidden text (`w:vanish`), generated TOC fields, and field-code instructions are removed and counted. Field results (what Word displays) are kept.
- Tracked changes: any `w:ins`, `w:del`, `w:moveFrom`, `w:moveTo`, or property-change revision causes a hard failure listing counts and first locations, unless `--accept-changes` or `--reject-changes` is given.
- First paragraph after heading or scene break: an explicit `class="first"` written by the generator, not `h2 + p` selectors. Explicit classes behave the same everywhere.
- Scene break: `<hr class="scene-break"/>` preceded by nothing, or `<p class="scene-break" role="separator">* * *</p>`; see question 10.
- No generated content via CSS (`::before`/`::after`): Kindle ignores it and it would hide text from the fidelity check.
- No embedded fonts, no `font-family`, no `font-size` on body text, no `line-height`, no `text-align` on body, no `position`, `float`, flexbox, grid, CSS variables, `vw`/`vh`/`rem`, or `display: none`. Headings may use relative sizes in `em` only. A `docs/css-decisions.md` will record each rule and the reader behaviour behind it.
- Zip: `mimetype` first and stored; other entries sorted, fixed timestamps, fixed permission bits.
- Accessibility metadata: accessMode, accessModeSufficient, accessibilityFeature (structuralNavigation, tableOfContents, readingOrder, alternativeText only if every image has alt text), accessibilityHazard, accessibilitySummary. The tool will not emit `dcterms:conformsTo` claiming WCAG/EPUB Accessibility conformance automatically: under the European Accessibility Act (in force since June 2025) that is a claim a publisher makes and must be able to stand behind.

## 5. Concerns with the brief

1. Byte-identical output across machines is not achievable with deflate. Python uses the system zlib, and some distributions (Fedora 40+, for example) ship zlib-ng, which produces different compressed bytes for the same input. Options: (a) guarantee determinism within one environment and test that; (b) store XHTML uncompressed (a novel is 1 to 2 MB of XHTML, so files grow but remain small), which gives true cross-platform identity. See question 6.
2. Reader compatibility (Send to Kindle, Apple Books, Kobo, Thorium) cannot be tested automatically or offline. I can avoid known-bad constructs and produce a manual test checklist; actual verification needs you or devices.
3. OCR makes the fidelity check circular: the reference text would be the OCR output itself, so recognition errors are invisible. An OCR path cannot honestly meet the "never lose content" standard. Hence the recommendation to defer it.
4. EPUBCheck's "zero warnings" depends on its version. I will pin one version in the docs and tests.
5. The repository licence is CC0 1.0. CC0 explicitly withholds any patent licence and is not OSI-approved; for software, MIT or Apache-2.0 is the usual choice. Your call, but it affects how others can use the tool (question 1).
6. Project Gutenberg texts: the texts are public domain in the US, but copyright terms differ elsewhere (life plus 70 in the UK and EU). I will only use authors who died before 1950, and strip the Gutenberg licence header and footer, as their trademark terms require for redistributed derivatives.
7. Front and back matter in DOCX manuscripts is rarely styled distinctly. Classification will be heuristic (heading text such as "Dedication", "Acknowledgements", position before the first chapter) with a meta-file override, and every classification is reported with its confidence.

## 6. Milestones

As in the brief. Milestone 1 delivers: IR, Diagnostics, EPUB writer, CSS, zip determinism, EPUBCheck runner, EPUB text extraction, and tests against hand-built IR books (including footnotes, scene breaks, front and back matter, images, and an all-features book), with a determinism test that builds twice and compares bytes.

## 7. Decisions (answers to the Step 0 questions)

1. Licence: MIT (replaces CC0). Audience: one author, so no PyPI release is planned.
2. English only (en-GB and en-US). Other languages warn.
3. Metadata file: TOML only (standard-library `tomllib`, no PyYAML).
4. Identifier: if none is supplied, a UUID v5 derived from title and authors, with a warning to pin it.
5. `dcterms:modified`: the source document's modification date. Falls back to 2000-01-01T00:00:00Z, with a warning.
6. Determinism: same-environment byte identity (deflate kept). The final product is a portable Windows .exe (see note below).
7. EPUBCheck: not bundled at runtime. Found via an explicit path, `NOVEL2EPUB_EPUBCHECK`, or the `epubcheck` package. Missing means a warning, or an error under `--strict`.
8. A fidelity failure writes `*.failed.epub` in the report directory, never at the requested output path.
9. Exit codes: 0 clean, 1 success with warnings, 2 usage error, 3 input rejected, 4 fidelity or EPUBCheck failure.
10. Scene breaks: `<p class="scene-break" role="separator">* * *</p>`, with one fixed glyph (see docs/css-decisions.md).
11. Footnotes at chapter end as asides; endnotes in a separate back-matter file.
12. Title pages only from the source; never generated.
13. OCR deferred beyond v1. Scanned PDFs fail with a clear error.
14. Unsupported images (EMF, WMF, TIFF, BMP): convert where possible. Pillow reads WMF/EMF only on Windows, which suits the .exe target; elsewhere they fail loudly.
15. No parts: a single level of chapters.
16. No automatic conformance claim. The output is built so that a publisher can reasonably make one: language set, navigation, headings, reading order, alt text (warned where missing), and pass-through `conforms_to` / `certified_by` metadata.

17. Windows .exe: bundle a trimmed Java runtime (made with `jlink`) so that EPUBCheck runs without an installed Java.
18. Literal HTML tags typed into manuscript text (common in AO3-style writing): balanced `<i>`, `<em>`, `<b>`, `<strong>` are interpreted as formatting by default and counted in the report. Unbalanced or unknown tags stay as literal text with a warning. A flag turns interpretation off.
19. No detectable chapters: the whole body becomes one untitled chapter, with a warning. A short first line stays as an ordinary paragraph (never dropped) and is flagged for review. The title comes from the metadata.

### Findings from the first real manuscript (kept in `local/`, never committed)

- No named styles, no Word italics or bold, no alignment: everything is direct formatting or plain text.
- Italics are literal `<i>...</i>` text (25 pairs).
- Scene breaks in three forms: VML horizontal rules (`v:rect o:hr="t"`), a bottom border on the last paragraph of a scene (`w:pBdr/w:bottom`), and a line of underscores.
- No headings; a working file title as the first line.
- Mixed straight and curly quotes (42 straight against 651 curly double quotes): preserved, flagged for review.
- `w:lastRenderedPageBreak` (layout cache) appears 33 times; it is not a real break and must be ignored.

### Milestone 2 decisions

- The DOCX front end reads OOXML directly with lxml (python-docx dropped; see the dependency table).
- Logged removals are cut out of the reference text positionally, in document order, before the word diff. Each removal records the text just before it, so that, for example, a removed page number "4" is cut from the right place and not from "1840". Matching removals against the diff afterwards proved fragile, because the diff can align repeated words (a "Prologue" in a contents list with the real heading) arbitrarily.
- Lesson recorded: a removal heuristic that misfires can hide real loss from the fidelity check, because its removal is logged and therefore "explained". During testing, a typed-contents detector swallowed a whole chapter while fidelity still read 100%. Every heuristic that removes text must therefore be bounded on its own terms (the contents remover refuses to remove anything that looks like prose), and the report shows removal counts by kind so that an unusual count stands out.
- A single empty paragraph is not a scene break (reported for review); two or more are, when the manuscript normally has none between paragraphs.
- Word's "All caps" keeps the typed characters and displays them in capitals through CSS, so the text survives exactly.
- Note references never carry the formatting of Word's footnote-reference style (it double-superscripted them).

### Milestone 3 decisions

- Parts are supported (asked and agreed): `SectionKind.PART` plus `Section.depth`, giving a two-level contents and NCX. Books without parts are unchanged.
- Side margins added (`body { margin: 0 5% }`) after the first real EPUB ran to the screen edge in a reader that adds none.
- Text printed above a chapter heading (a quotation or "document" set before the title) stays where it was printed. Moving it below the heading as an epigraph would reorder the author's text, which the fidelity check rightly flagged.
- The fidelity comparison for PDF is neutral to hyphenation ("mi-litia" = "militia") and to dash spacing at line ends; dehyphenation choices are logged decisions instead. Removals are cut from the PDFium reference page by page.
- PDFium reports line-break hyphens as U+0002 or U+FFFE depending on version; both are mapped back to hyphens in the reference.
- An outline entry with children is a part, unless it is a named section (an Epilogue with sub-sections keeps its kind and nests its children).

### Milestone 4 decisions

- Page furniture is identified by repetition and position, never by position alone: a body's first line also sits at the same height on every page. Removal is limited to short lines in the outer 15% of the page, two per edge, and never a scene-break marker (a `* * *` that fell at the foot of several pages was removed as a "footer" until this rule; the fidelity check could not see it because the removal was logged).
- Second instance of the milestone 2 lesson: every removal heuristic must be bounded on its own terms, because a logged removal is invisible to the fidelity check.
- Removals can carry a replacement, for deliberate rewrites such as joining a drop cap ("ir" becomes "Sir"); locating them prefers the recorded context, then a whole-token match.
- PDF footnotes: the lines removed from the main text are the reference for the notes, so note text is checked too. Notes are numbered afresh in the EPUB; symbol markers (*, dagger) are kept.
- Chapter detection order for PDF: outline, printed contents, `--chapter-regex`, "Chapter N" patterns, large type. The brief put large type before patterns; patterns are more specific, so they are tried first, and each heading still records its method.
- Text printed above a chapter heading stays where it was printed (milestone 3), and a gap before a centred line ("THE END") is not a scene break.

### v2 direction (not in v1)

The author wants template-driven output, like InDesign templates, as an alternative to cleaning up in Sigil after conversion. v1 prepares for this without building it: a small, stable, documented class vocabulary (docs/markup.md), all styling in one replaceable stylesheet, and XHTML laid out one block per line for hand editing.

### Windows .exe note

PyInstaller (GPL-2.0 with a bootloader exception that allows distributing bundled applications under any licence) is the likely packager. EPUBCheck needs a Java runtime. Decision: bundle a trimmed runtime made with `jlink` (OpenJDK is GPL-2.0 with the Classpath Exception, so it is redistributable). Expect roughly 40 to 50 MB added to the .exe.
