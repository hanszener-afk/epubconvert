# Reading-system checklist

EPUBCheck proves that a book is valid EPUB. It cannot prove that the book looks right in a given reader, and reading systems cannot be tested automatically or offline. This checklist is for a person with the readers to hand. Run it once per release of novel2epub, and on any book whose layout is unusual.

Use one book that exercises everything: a converted book with a title page, a dedication, parts, footnotes, scene breaks, an epigraph or quoted document, italics, and a cover. Record the reader, its version, the device, and the date for each run.

## Every reader

| Check | What good looks like |
|---|---|
| Opens | No error, no blank first page |
| Cover | Shown in the library and as the first page, not stretched |
| Contents | The reader's own contents menu lists every chapter, nested under parts; each entry jumps to the right place |
| Contents page | The book's own contents page has no list numbers ("1. 1. The Ditch" is wrong) |
| Margins | Text does not touch the screen edges |
| Paragraphs | First-line indents, no gaps between paragraphs; no indent after a heading or scene break |
| Headings | Chapter titles centred, larger than body text, each chapter starting on a new screen |
| Title page and dedication | Centred, with space above |
| Scene breaks | `* * *` centred, with space around it |
| Italics and bold | Present where the manuscript has them |
| Footnotes | Tapping a marker shows the note (pop-up, or a jump with a working way back) |
| Reader settings | Changing font, size, and line spacing in the reader changes the book: the book never fights the reader |
| Chosen fonts (if any) | The embedded fonts are shown, italic and bold included; Kindle only under "Publisher font" |
| Chosen layout (if any) | Margins, indents, and paragraph gaps match the Appearance settings |
| Accessibility | A screen reader (VoiceOver, TalkBack, or NVDA with Thorium) reads in order, announces headings, and announces scene breaks as separators |

## Reader-specific

| Reader | Also check |
|---|---|
| Kindle (Send to Kindle) | The upload is accepted (Amazon rejects EPUBs with EPUBCheck errors). Footnotes pop up. Indents are kept (Kindle sometimes overrides them; that is the reader's choice) |
| Apple Books | Pop-up footnotes. Both scrolling and paged views |
| Kobo | Both the standard and the "kepub" rendering, if the shop converts to kepub |
| Thorium | Accessibility with a screen reader |
| Okular | Side margins present; chapter headings centred, each preceded by a thin rule. No pop-up footnotes: notes are listed, linked, at the end of each chapter |
| Sigil | Opens without "repair" prompts; the files are easy to read and edit (one paragraph per line) |

## Known differences that are not faults

- Okular ignores `body` margins, line height, and hidden content, and joins files together; the stylesheet works around this (see `css-decisions.md`).
- Kindle and Apple Books add their own margins on top of the book's.
- Justification is the reader's setting, not the book's.
