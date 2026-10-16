"""Format-neutral cleanup of a Book, run after any front end.

- Whitespace: runs of ordinary whitespace collapse to one space; space is
  trimmed at paragraph edges and around line breaks. No-break spaces and
  every other character are kept exactly.
- Empty paragraphs, empty spans, and redundant scene breaks are removed
  (and logged).
- Adjacent text and identical adjacent spans are merged.
- Optionally (--smarten) straight quotes become typographic quotes.
"""

from __future__ import annotations

from dataclasses import dataclass

from novel2epub.diagnostics import Diagnostics
from novel2epub.ir import (
    Block,
    BlockQuote,
    Book,
    Epigraph,
    Heading,
    Image,
    Inline,
    LineBreak,
    Link,
    NoteRef,
    Paragraph,
    SceneBreak,
    Section,
    SourceRef,
    Span,
    Text,
    Verse,
)
from novel2epub.normalise.smarten import smarten_text

_COLLAPSIBLE = " \t\n\r\f\v"


@dataclass
class _Counts:
    smartened: int = 0


def normalise_book(book: Book, diag: Diagnostics, smarten: bool = False) -> None:
    counts = _Counts()
    for section in book.sections():
        _section(section, diag, smarten, counts)
    for note in book.notes.values():
        note.blocks = _blocks(note.blocks, diag, smarten, counts, in_section=False)
    book.front = [s for s in book.front if _keep_section(s, diag)]
    book.body = [s for s in book.body if _keep_section(s, diag)]
    book.back = [s for s in book.back if _keep_section(s, diag)]
    if counts.smartened:
        diag.info("smartened", f"Converted {counts.smartened} straight quote(s) to typographic.")


def _keep_section(section: Section, diag: Diagnostics) -> bool:
    if section.blocks or section.title:
        return True
    diag.removed("empty-section", "", section.source)
    return False


def _section(section: Section, diag: Diagnostics, smarten: bool, counts: _Counts) -> None:
    if section.title:
        section.title = _inlines(section.title, smarten, counts)
    if section.opener:
        section.opener = _blocks(section.opener, diag, smarten, counts, in_section=False)
    section.blocks = _blocks(section.blocks, diag, smarten, counts, in_section=True)


def _blocks(
    blocks: list[Block], diag: Diagnostics, smarten: bool, counts: _Counts, in_section: bool
) -> list[Block]:
    out: list[Block] = []
    for block in blocks:
        if isinstance(block, Paragraph | Heading):
            block.content = _inlines(block.content, smarten, counts)
            if not _has_content(block.content):
                diag.removed("empty-paragraph", "", block.source)
                continue
        elif isinstance(block, BlockQuote):
            block.blocks = _blocks(block.blocks, diag, smarten, counts, in_section=False)
            if not block.blocks:
                continue
        elif isinstance(block, Epigraph):
            block.blocks = _blocks(block.blocks, diag, smarten, counts, in_section=False)
            if block.attribution:
                block.attribution = _inlines(block.attribution, smarten, counts)
            if not block.blocks and not block.attribution:
                continue
        elif isinstance(block, Verse):
            block.stanzas = [
                [_inlines(line, smarten, counts) for line in stanza] for stanza in block.stanzas
            ]
        elif isinstance(block, Image) and block.caption:
            block.caption = _inlines(block.caption, smarten, counts)
        out.append(block)
    return _tidy_scene_breaks(out, diag) if in_section else out


def _tidy_scene_breaks(blocks: list[Block], diag: Diagnostics) -> list[Block]:
    """No scene break at a section's edges, and never two in a row."""
    out: list[Block] = []
    for block in blocks:
        if isinstance(block, SceneBreak) and (not out or isinstance(out[-1], SceneBreak)):
            _log_redundant(diag, block.source)
            continue
        out.append(block)
    while out and isinstance(out[-1], SceneBreak):
        _log_redundant(diag, out.pop().source)  # type: ignore[union-attr]
    return out


def _log_redundant(diag: Diagnostics, source: SourceRef | None) -> None:
    diag.removed("redundant-scene-break", "", source)


# --------------------------------------------------------------------------
# Inline content


def _has_content(nodes: list[Inline]) -> bool:
    for n in nodes:
        if isinstance(n, Text) and n.text.strip(_COLLAPSIBLE):
            return True
        if isinstance(n, NoteRef):
            return True
        if isinstance(n, Span) and _has_content(n.children):
            return True
    return False


def _leaves(nodes: list[Inline]) -> list[Inline]:
    out: list[Inline] = []
    for n in nodes:
        if isinstance(n, Span):
            out.extend(_leaves(n.children))
        else:
            out.append(n)
    return out


def _inlines(nodes: list[Inline], smarten: bool, counts: _Counts) -> list[Inline]:
    leaves = _leaves(nodes)
    # Collapse whitespace runs, with no space at the start or after a break.
    at_space = True
    for leaf in leaves:
        if isinstance(leaf, LineBreak):
            at_space = True
        elif isinstance(leaf, NoteRef):
            at_space = False
        elif isinstance(leaf, Text):
            chars: list[str] = []
            for ch in leaf.text:
                if ch in _COLLAPSIBLE:
                    if not at_space:
                        chars.append(" ")
                    at_space = True
                else:
                    chars.append(ch)
                    at_space = False
            leaf.text = "".join(chars)
    # Trim trailing space at the end and before each line break.
    strip = True
    for leaf in reversed(leaves):
        if isinstance(leaf, LineBreak):
            strip = True
        elif isinstance(leaf, NoteRef):
            strip = False
        elif isinstance(leaf, Text) and strip:
            leaf.text = leaf.text.rstrip(" ")
            if leaf.text:
                strip = False
    if smarten:
        before = ""
        for leaf in leaves:
            if isinstance(leaf, Text):
                new = smarten_text(leaf.text, before)
                counts.smartened += sum(1 for a, b in zip(leaf.text, new, strict=True) if a != b)
                leaf.text = new
                before += new
            elif isinstance(leaf, LineBreak):
                before += " "
    return _tidy(nodes)


def _same_span(a: Inline, b: Inline) -> bool:
    if type(a) is not type(b) or not isinstance(a, Span):
        return False
    return not isinstance(a, Link) or a.href == b.href  # type: ignore[union-attr]


def _tidy(nodes: list[Inline]) -> list[Inline]:
    out: list[Inline] = []
    for n in nodes:
        if isinstance(n, Span):
            n.children = _tidy(n.children)
            if not n.children:
                continue
        if isinstance(n, Text) and not n.text:
            continue
        if out and isinstance(n, Text) and isinstance(out[-1], Text):
            out[-1] = Text(out[-1].text + n.text)
            continue
        if out and _same_span(out[-1], n):
            prev = out[-1]
            assert isinstance(prev, Span) and isinstance(n, Span)
            prev.children = _tidy([*prev.children, *n.children])
            continue
        out.append(n)
    # Leading or trailing line breaks carry no text.
    while out and isinstance(out[0], LineBreak):
        out.pop(0)
    while out and isinstance(out[-1], LineBreak):
        out.pop()
    return out
