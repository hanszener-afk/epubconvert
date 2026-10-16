# Reader test results

Results of `docs/reader-checklist.md` on the readers that run on Linux, tested on 16 October 2026 with novel2epub 0.5.0. Books tested: the fixtures `front_back`, `footnotes`, `scene_breaks`, and `styled` (each converted with a cover), and a private 222,000-word novel converted from PDF (title page, prologue with a quoted document above its heading, ten parts, 72 chapters, 246 scene breaks, epilogue).

Screenshots were taken on a virtual display; nothing here was checked on a phone, tablet, or e-ink device.

## Summary

| Reader | Engine | Result |
|---|---|---|
| Okular 23.08.5 | Qt rich text | Passes, with the limits below |
| calibre viewer 7.6.0 | Chromium (QtWebEngine) | Passes |
| Foliate 3.1.1 | WebKit (as Apple Books) | Passes |
| Ace by DAISY 1.3.3 | Accessibility checker | Passes on all five books, no violations |
| calibre conversion to AZW3 (Kindle format) | calibre | Converts with no errors or warnings; all 222,080 words present afterwards |
| Thorium | Readium | Not tested: the download was blocked by this environment's network policy |
| Kindle, Apple Books, Kobo | | Not tested: no Linux versions. Still to be checked by hand |

## Details

**Okular.** Cover, title page, contents page, and chapters each start on a new page. Side margins present. Chapter headings centred, each under a thin rule (the `hr.chapter-start` work-around, visible only here). Contents sidebar nests chapters under parts. Scene breaks centred. Footnote markers are raised links; there are no pop-ups, so notes appear as a linked list at the end of each chapter, as intended for readers without pop-up support. Display pages (dedication, copyright) lose their space above.

**calibre viewer.** Paged, with margins. Title page and contents page correct, with parts nested and no doubled numbers. Paragraph indents, unindented first paragraphs, and centred scene breaks correct. Clicking a footnote marker opens a pop-up with the note. calibre also shows the notes at the end of the chapter, as it does for any book.

**Foliate.** Paged and justified, with margins. Sidebar contents nested under parts. The opener above the prologue's heading is kept in place, set as a block quotation. Footnotes are hidden in the text and open in a pop-up when the marker is clicked, as in Apple Books. Fixed during testing: the running header on the contents page read "Title Page", because the reader's contents menu did not list the contents page; it now does, in both the navigation document and the NCX.

**Ace.** The DAISY accessibility checker found no violations in any of the five books. That is a necessary condition for an accessibility claim, not a sufficient one: a person still has to check reading order, alt text quality, and screen-reader behaviour before claiming conformance.

**AZW3.** calibre's conversion is not Amazon's (Send to Kindle converts on Amazon's servers), so this shows only that the book survives a second converter. The text extracted from the AZW3 matched the EPUB word for word.
