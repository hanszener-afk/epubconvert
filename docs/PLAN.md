# novel2epub: Step 0 plan (awaiting approval)

Status: proposal only. No code has been written.

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
| python-docx | MIT | Package/rels/style access, core properties. It does not support footnotes, endnotes, or tracked changes, so those are lxml |
| pdfminer.six | MIT | Glyph-level extraction: character, font name, size, bbox; outline/bookmarks |
| pypdfium2 | Apache-2.0 or BSD-3-Clause (PDFium: BSD-3/Apache-2.0 with permissive third-party components) | Independent reference text for the fidelity check; PDF classification (text vs scanned); rendering for OCR if ever added |
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

## 7. Open questions

See the Step 0 reply in the session; answers will be recorded here once given.
