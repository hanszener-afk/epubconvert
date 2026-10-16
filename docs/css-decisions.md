# Stylesheet decisions

The stylesheet (`src/novel2epub/epub/style.css`) is deliberately small. Each choice below is either a rule it contains or a rule it avoids, with the reason.

None of this has been verified on physical devices yet. The reasons come from the reading systems' published guidelines and from widely reported behaviour. A manual device checklist will follow in milestone 5.

## What it does

| Rule | Why |
|---|---|
| `body { margin: 0 5% }` | Side margins. Kindle and Apple Books add their own page margins, but Sigil's preview, some Kobo modes, and several desktop readers add little or none, so text ran to the screen edge (reported on the first real manuscript). The cost is slightly wider margins where both apply. Percentages, so the margin scales with the screen. One rule, so a template can change it. |
| `p { margin: 0; text-indent: 1.5em }` | Fiction convention: indented paragraphs, no gaps between them. |
| `p.first { text-indent: 0 }` | First paragraph after a section title, sub-heading, scene break, epigraph, or image is not indented. Written as an explicit class rather than `h1 + p` so that it behaves the same everywhere, including converters that flatten structure. |
| Scene break as `<p class="scene-break" role="separator">* * *</p>` | The asterisks are real text, so the break survives a reading system that ignores or strips CSS, and a screen reader announces a separator. An `<hr/>` would vanish visually on some readers once CSS is stripped and is restyled unpredictably by others. |
| Headings centred, margins in `em` | Relative to the reader's chosen size. No heading font sizes are set; reading-system defaults scale with the reader's settings. |
| `page-break-after: avoid` on headings | Stops a heading being stranded at the bottom of a page. Ignored harmlessly where unsupported. |
| `.sc { font-variant: small-caps }` | Small caps from the manuscript. If unsupported, the text shows in normal case, which loses nothing. |
| `.caps { text-transform: uppercase }` | Word's "All caps" formatting. The text keeps its typed case; only the display changes, as in Word. |
| `img { max-width: 100% }` | Images never overflow the screen. Percentages, not pixels. |
| `ol.notes { list-style-type: none }` | Endnotes carry their own backlinked marker; this avoids a second number. If CSS is stripped, the number appears twice, which is ugly but loses nothing. |

## What it avoids, and why

| Avoided | Reason |
|---|---|
| `font-family`, `@font-face`, embedded fonts | The reader's font choice must win. Embedded fonts also add licensing questions and file size. |
| `font-size` and `line-height` on text | The same: these override reader settings, and Kindle in particular reacts badly to fixed line heights. |
| `text-align` on `body` or on body paragraphs | Justification is a reader setting on Kindle, Kobo, and Apple Books. Forcing it removes that choice. |
| Absolute units (`px`, `pt`, `cm`) and `rem`, `vw`, `vh` | Absolute units ignore reader scaling. `rem` and viewport units are inconsistently supported in older Kindle and Kobo renderers. |
| `position`, `float` | Unreliable or ignored on Kindle and older Kobo firmware. Drop caps built from floats are a common source of broken layouts, so drop caps are not styled. |
| `display: none` | Some Kindle conversions and Kobo rendering paths treat hidden content inconsistently. Nothing that is content should be hidden. (The landmarks list in the nav uses the HTML `hidden` attribute, which reading systems handle themselves.) |
| `::before` / `::after` generated content | Kindle ignores it. It would also put visible text outside the document, where the fidelity check cannot see it. |
| Flexbox, grid, CSS custom properties (`var()`) | Not supported by Kindle's KF8/KFX conversion or older Kobo firmware. |
| Negative `text-indent` (hanging indents) | Kindle clips negative indents in some layouts. Verse uses a left margin instead. |
| Media queries | Not needed for a reflowable novel. Kindle's support differs between conversion paths. |

## Kindle-specific notes

- Send to Kindle converts EPUB to KFX on Amazon's servers. EPUBCheck errors cause rejection, so zero errors is a hard requirement.
- Footnotes use `<a epub:type="noteref">` and `<aside epub:type="footnote">`. Kindle and Apple Books show these as pop-ups. Readers without pop-up support show the aside at the end of the chapter.
- The legacy `<meta name="cover">` element is included alongside the EPUB 3 `cover-image` property because some Kindle conversion paths still look for it.
- The navigation document is in the spine, so the book has a visible Contents page, which Amazon's guidelines ask for.
