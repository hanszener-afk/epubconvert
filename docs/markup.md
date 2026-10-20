# Output markup

This is the complete markup vocabulary the EPUB writer produces. It is kept small and stable deliberately:

- Anyone tidying the book by hand (for example in Sigil) can learn it in a few minutes.
- A future template layer can restyle a book by replacing `Styles/style.css` and targeting these classes, without changes to the converter.

Changing a class name is a breaking change and should be treated as one.

## Package layout

Sigil's conventional layout:

```
mimetype
META-INF/container.xml
OEBPS/content.opf
OEBPS/toc.ncx
OEBPS/Styles/style.css
OEBPS/Images/<resource>.<ext>
OEBPS/Fonts/<slot>.ttf|otf      only with chosen fonts: body, body_italic, body_bold, body_bold_italic, headings
META-INF/com.apple.ibooks.display-options.xml   only with chosen fonts: tells Apple Books to use them
OEBPS/Text/cover.xhtml          (only with a cover)
OEBPS/Text/front-NN.xhtml       front matter, in order
OEBPS/Text/nav.xhtml            navigation document (not in the spine; for reading systems' menus)
OEBPS/Text/contents.xhtml       visible contents page (in the spine, after the front matter)
OEBPS/Text/chapter-NNN.xhtml    body sections, in order
OEBPS/Text/back-NN.xhtml        back matter, in order
OEBPS/Text/endnotes.xhtml       only if the book has endnotes or unreferenced notes
```

Each content file's body is one `<div class="page">`, opening with an empty `<hr class="chapter-start" aria-hidden="true"/>` and then one `<section>`. Both exist for Okular (see `css-decisions.md`): it ignores margins on `body`, and it merges the first block of each file into the end of the previous file, which would strip a chapter heading of its formatting. The rule is invisible elsewhere. The cover file has neither.

Each `<section>` with `epub:type` and an ARIA `role` where one exists. Titled sections start with `<h1 id="title">`. Untitled ones carry `aria-label`.

- Display pages carry a class naming their kind: `titlepage`, `halftitle`, `part`, `dedication`, `epigraph`, `copyright`. Other untitled sections carry `untitled`.
- The contents page is a `<section class="contents generated">` holding `<ol class="toc">` lists (nested for parts). `generated` marks text the converter wrote, which the fidelity check skips.
- A title page's title is an `<h1>` inside the (untitled) section.
- Text printed above a chapter's title (a quotation or document set over the heading) comes first in the section, before the `<h1>`, as in print.

## Blocks

| Markup | Meaning |
|---|---|
| `<p>` | Ordinary paragraph (indented) |
| `<p class="first">` | First paragraph after a title, heading, scene break, epigraph, or image (not indented) |
| `<p class="centre">`, `<p class="right">` | Paragraph aligned in the manuscript. May combine with `first` |
| `<p class="scene-break" role="separator">* * *</p>` | Scene break |
| `<h2>` to `<h6>` | Headings inside a section |
| `<blockquote>` | Block quotation (letters, quoted documents) |
| `<blockquote class="epigraph">` + `<p class="attribution">` | Epigraph and its source |
| `<div class="verse">` > `<div class="stanza">` > `<p>` per line | Poetry or song |
| `<div class="image"><img/></div>` or `<figure><img/><figcaption/></figure>` | Image, with or without a caption. A width chosen for it is written on the image as `style="width: 60%"`, a share of the text column |

## Inline

| Markup | Meaning |
|---|---|
| `<i>` | Italic. Deliberately `<i>`, not `<em>`: in fiction, italics mark titles, foreign words, and thoughts as often as emphasis |
| `<b>` | Bold |
| `<span class="sc">` | Small caps |
| `<span class="caps">` | Displayed in capitals (Word's "All caps"). The characters keep the case the author typed, so the text survives exactly; a reading system that ignores CSS shows them as typed |
| `<sup>`, `<sub>` | Superscript, subscript |
| `<a href>` | Hyperlink |
| `<br/>` | Line break inside a paragraph |

## Notes

- Reference: `<sup><a epub:type="noteref" role="doc-noteref" id="ref-X-N" href="...#note-X">1</a></sup>`
- Footnote, at the end of the chapter that first references it: `<aside epub:type="footnote" role="doc-footnote" id="note-X">`
- Endnote, in `endnotes.xhtml`: `<li id="note-X">` inside `<section epub:type="endnotes" role="doc-endnotes">`
- Each note starts with a backlink carrying its marker: `<a role="doc-backlink">1.</a>`

Footnotes are numbered 1, 2, 3. Endnotes are numbered i, ii, iii (Word's default), unless the manuscript gave a marker of its own.

## Generated text

The writer adds a little text that is not in the manuscript. The fidelity check ignores exactly these elements, so the test for "text the author did not write" is mechanical:

- `epub:type="noteref"` links (note markers)
- `role="doc-backlink"` links (markers at the start of each note)
- `class="scene-break"` (the asterisks)
- `class="generated"` (the "Notes" heading, markers of unreferenced notes)
- the navigation document and the cover page
