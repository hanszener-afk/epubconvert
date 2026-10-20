# Stylesheet decisions

The stylesheet (`src/novel2epub/epub/style.css`) is deliberately small. Each choice below is either a rule it contains or a rule it avoids, with the reason.

None of this has been verified on physical devices yet. The reasons come from the reading systems' published guidelines and from widely reported behaviour. `docs/reader-checklist.md` is the manual check to run on real readers.

## What it does

| Rule | Why |
|---|---|
| `div.page { margin: 1.5em 2.5em }`, and `1.5em 6%` inside `@supports` | Page margins. Readers that add little or no margin of their own (Sigil's preview, Okular, some Kobo modes, browser-based previews) otherwise run text to the edge of the screen. They sit on a wrapper `div`, not on `body`, because Okular ignores `body` margins. The plain rule uses ems because Okular converts ems to pixels but misreads percentages (6% becomes about 6 pixels). Engines that understand `@supports` get 6% at the sides instead, which scales with the screen; Okular skips the `@supports` block. In Kindle and Apple Books this adds to their own margins, so it is kept moderate. |
| `hr.chapter-start` with no border, height, or margin | An empty rule opening each file. Okular joins the first block of each file onto the last block of the previous one; without a sacrificial first block, every chapter heading loses its centring and size. Elsewhere it is invisible. In Okular it shows as a thin line between chapters, which reads as a chapter divider. |
| `p { margin: 0; text-indent: 1.5em }` | Fiction convention: indented paragraphs, no gaps between them. |
| `p.first { text-indent: 0 }` | First paragraph after a section title, sub-heading, scene break, epigraph, or image is not indented. Written as an explicit class rather than `h1 + p` so that it behaves the same everywhere, including converters that flatten structure. |
| Scene break as `<p class="scene-break" role="separator">* * *</p>` | The asterisks are real text, so the break survives a reading system that ignores or strips CSS, and a screen reader announces a separator. An `<hr/>` would vanish visually on some readers once CSS is stripped and is restyled unpredictably by others. |
| Headings centred, margins in `em` | Relative to the reader's chosen size. No heading font sizes are set; reading-system defaults scale with the reader's settings. |
| `page-break-after: avoid` on headings | Stops a heading being stranded at the bottom of a page. Ignored harmlessly where unsupported. |
| `.sc { font-variant: small-caps }` | Small caps from the manuscript. If unsupported, the text shows in normal case, which loses nothing. |
| `.caps { text-transform: uppercase }` | Word's "All caps" formatting. The text keeps its typed case; only the display changes, as in Word. |
| `blockquote p { text-indent: 0; margin-bottom: 0.5em }` | Quoted letters, notices, and inventories read as blocks, as they are usually printed. |
| Display pages (`section.titlepage`, `.part`, `.dedication`, `.epigraph`, `.halftitle`, `.copyright`) | Centred, unindented, with `padding-top: 3em`. Without this, a title page is a column of left-aligned bold paragraphs. |
| `ol.toc { list-style-type: none }` | Chapter titles often carry their own numbers ("1. The Ditch"); list numbering would print "1. 1. The Ditch". Selected by the list's own class: Okular rewrites `<section>` as `<p>`, which breaks any selector that goes through a section. |
| `img { max-width: 100% }` | Images never overflow the screen. Percentages, not pixels. |
| `ol.notes { list-style-type: none }` | Endnotes carry their own backlinked marker; this avoids a second number. If CSS is stripped, the number appears twice, which is ugly but loses nothing. |

## Settings chosen for a book

The author's Appearance settings (or the book file's `[style]` and `[fonts]`) are written as a short block after the base stylesheet, under a comment saying so, so a book with no settings has exactly the stylesheet above. Margins replace both the plain `div.page` rule and its `@supports` percentage version. Lengths are in em only.

## What it avoids, and why

| Avoided | Reason |
|---|---|
| `font-family`, `@font-face`, embedded fonts | Not by default: the reader's font choice should win, and embedded fonts add licensing questions and file size. Added only when the author chooses fonts (Appearance tab or `[fonts]`), after the base stylesheet; readers can still override them, and Kindle uses them only under "Publisher font". |
| `font-size` and `line-height` on text | Not by default: these override reader settings, and Kindle in particular reacts badly to fixed line heights. `line-height` is added only when the author sets a line spacing. |
| `text-align` on `body` or on body paragraphs | Justification is a reader setting on Kindle, Kobo, and Apple Books. Forcing it removes that choice. |
| Absolute units (`px`, `pt`, `cm`) and `rem`, `vw`, `vh` | Absolute units ignore reader scaling. `rem` and viewport units are inconsistently supported in older Kindle and Kobo renderers. |
| `position`, `float` | Unreliable or ignored on Kindle and older Kobo firmware. Drop caps built from floats are a common source of broken layouts, so drop caps are not styled. |
| `display: none` | Some Kindle conversions and Kobo rendering paths treat hidden content inconsistently, and Okular ignores it (and the `hidden` attribute) entirely. Nothing that is content should be hidden. This is also why the navigation document is kept out of the spine: its landmarks list would otherwise be shown as a page. |
| `::before` / `::after` generated content | Kindle ignores it. It would also put visible text outside the document, where the fidelity check cannot see it. |
| Flexbox, grid, CSS custom properties (`var()`) | Not supported by Kindle's KF8/KFX conversion or older Kobo firmware. |
| Negative `text-indent` (hanging indents) | Kindle clips negative indents in some layouts. Verse uses a left margin instead. |
| Media queries | Not needed for a reflowable novel. Kindle's support differs between conversion paths. (The single `@supports` block is a feature test, not a media query; an engine that does not understand it uses the em margins.) |

## Kindle-specific notes

- Send to Kindle converts EPUB to KFX on Amazon's servers. EPUBCheck errors cause rejection, so zero errors is a hard requirement.
- Footnotes use `<a epub:type="noteref">` and `<aside epub:type="footnote">`. Kindle and Apple Books show these as pop-ups. Readers without pop-up support show the aside at the end of the chapter.
- The legacy `<meta name="cover">` element is included alongside the EPUB 3 `cover-image` property because some Kindle conversion paths still look for it.
- A separate contents page (`contents.xhtml`) is in the spine, so the book has a visible Contents page, which Amazon's guidelines ask for. The navigation document itself is not in the spine.

## Okular-specific notes

Okular renders EPUB through Qt's rich-text engine, which supports a small subset of HTML and CSS and has some quirks. These were found by reading Okular's EPUB backend and reproducing it with Qt, then confirmed in Okular 23.08 itself (see `docs/reader-results.md`):

- `<section>` is rewritten as `<p>`, so selectors through sections fail.
- `body` margins, `line-height`, `display: none`, and `hidden` are ignored; ems are converted to pixels at the font's point size; percentages are misread.
- Files after the first are inserted at the end of the document, merging each file's first block into the previous file's last one (hence `hr.chapter-start`).
- Each file still starts on a new page, so chapters do. Display pages (title page, dedication) lose their space above, because it was set on the section.
