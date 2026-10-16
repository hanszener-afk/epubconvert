"""Turn PDF blocks into front matter, parts, chapters, and back matter.

Chapter headings, in order of trust:
  1. the PDF outline (bookmarks), matched to the printed heading;
  2. --chapter-regex, if given;
  3. "Chapter N" / "Prologue" patterns at the top of a page;
  4. large type at the top of a page.
Each heading is logged as a decision with its method and confidence.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from novel2epub.diagnostics import Diagnostics
from novel2epub.ir import (
    Align,
    Block,
    BlockQuote,
    Heading,
    Image,
    Inline,
    LineBreak,
    Paragraph,
    PdfRef,
    SceneBreak,
    Section,
    SectionKind,
)
from novel2epub.normalise.headings import (
    BACK,
    BARE_NUMBER,
    BARE_WORD_NUMBER,
    CHAPTER_PATTERN,
    COPYRIGHT_RE,
    STORY,
    named_kind,
    norm_name,
)
from novel2epub.normalise.inline import build_inlines
from novel2epub.normalise.scenebreaks import scene_break_confidence
from novel2epub.pdf.inspect import OutlineEntry
from novel2epub.pdf.layout import Metrics, PdfBlock

# A contents entry: a title, a run of leader dots or wide space, and a page number.
TOC_ENTRY = re.compile(
    r"^(?P<title>.*?\w.*?)\s*(?:[.\u00b7\u2026_]{2,}|\s{2,}|\t)\s*(?P<page>\d{1,4}|[ivxlcdm]{1,7})$",
    re.IGNORECASE,
)
PART_PATTERN = re.compile(r"^(part|book)\s+\S+", re.IGNORECASE)
GAP_SCENE_BREAK = 2.2  # an extra gap of this many line spaces mid-page
ORNAMENT_MAX_W = 180.0
ORNAMENT_MAX_H = 72.0


@dataclass
class PlacedImage:
    """An image positioned among the blocks: it goes before block `before`."""

    resource_id: str
    page: int
    bbox: tuple[float, float, float, float]
    before: int


@dataclass
class _Heading:
    index: int
    kind: SectionKind
    depth: int  # 0, or 1 for a chapter inside a part; -1 for a sub-heading
    method: str
    confidence: float
    title_indices: list[int] = field(default_factory=list)


def _key(text: str) -> str:
    t = norm_name(text).replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"[^\w\s']", "", t).strip()


class PdfStructurer:
    def __init__(
        self,
        blocks: list[PdfBlock],
        metrics: Metrics,
        outline: list[OutlineEntry],
        images: list[PlacedImage],
        diag: Diagnostics,
        chapter_regex: str | None,
        title: str,
    ) -> None:
        self.blocks = blocks
        self.m = metrics
        self.outline = outline
        self.images = images
        self.diag = diag
        self.regex = re.compile(chapter_regex) if chapter_regex else None
        self.title = title
        self.first_on_page = {
            i for i, b in enumerate(blocks) if i == 0 or blocks[i - 1].page != b.page
        }
        self.toc_blocks: set[int] = set()
        self.toc_titles: list[str] = []
        self._find_printed_toc()

    # ------------------------------------------------------- printed contents

    def _find_printed_toc(self) -> None:
        """A "Contents" heading followed only by entry lines ("Title ..... 12").

        Only blocks made entirely of entry-like lines are taken, so prose can
        never be removed as a contents list."""
        for i, b in enumerate(self.blocks):
            named, kind = named_kind(b.text)
            if not (named and kind is None and self._is_heading_like(i)):
                continue
            titles: list[str] = []
            j = i + 1
            while j < len(self.blocks):
                entries = [TOC_ENTRY.match(ln.text.strip()) for ln in self.blocks[j].lines]
                if not entries or not all(entries):
                    break
                titles.extend(m.group("title").strip() for m in entries if m)
                j += 1
            if len(titles) >= 2:
                self.toc_blocks = set(range(i, j))
                self.toc_titles = titles
                return

    def _printed_toc_headings(self) -> list[_Heading]:
        if not self.toc_titles:
            return []
        found: list[_Heading] = []
        pointer = max(self.toc_blocks) + 1
        for title in self.toc_titles:
            target = _key(title)
            match = next(
                (j for j in range(pointer, len(self.blocks))
                 if self._is_heading_like(j) and _key(self.blocks[j].text) == target),
                None,
            )  # fmt: skip
            if match is None:
                self.diag.decided(
                    "chapter-detection",
                    f"Contents entry {title!r} matches no heading in the text.",
                    0.3,
                    "printed-toc",
                )
                continue
            named, kind = named_kind(title)
            kind = (kind or SectionKind.CHAPTER) if named else SectionKind.CHAPTER
            found.append(_Heading(match, kind, 0, "printed-toc", 0.85))
            pointer = match + 1
        return found if len(found) >= max(2, len(self.toc_titles) // 2) else []

    # ------------------------------------------------------------ headings

    def _match_outline(self) -> list[_Heading]:
        if not self.outline:
            return []
        has_children = {
            i for i, e in enumerate(self.outline)
            if i + 1 < len(self.outline) and self.outline[i + 1].level > e.level
        }  # fmt: skip
        top = min(e.level for e in self.outline)
        found: list[_Heading] = []
        used: set[int] = set()
        for n, entry in enumerate(self.outline):
            match = self._find_title(entry, used)
            if match is None:
                self.diag.decided(
                    "chapter-detection",
                    f"Outline entry {entry.title!r} (page {entry.page}) matches no printed "
                    "heading; it was not used.",
                    0.3,
                    "outline",
                )
                continue
            indices, confidence = match
            used.update(indices)
            level = entry.level - top
            named, kind_ = named_kind(entry.title)
            if level == 0:
                # A named section (an epilogue with sub-sections) keeps its kind;
                # any other top-level entry with children is a part.
                if named:
                    kind = kind_ or SectionKind.OTHER
                elif n in has_children:
                    kind = SectionKind.PART
                else:
                    kind = SectionKind.CHAPTER
                depth = 0
            elif level == 1:
                kind = (kind_ or SectionKind.CHAPTER) if named else SectionKind.CHAPTER
                depth = 1  # a level-1 entry always has a level-0 parent
            else:
                kind, depth = SectionKind.CHAPTER, -1  # a heading inside a chapter
            found.append(_Heading(indices[0], kind, depth, "outline", confidence, indices))
        found.sort(key=lambda h: h.index)
        return found

    def _find_title(self, entry: OutlineEntry, used: set[int]) -> tuple[list[int], float] | None:
        target = _key(entry.title)
        candidates = [
            i for i, b in enumerate(self.blocks)
            if entry.page <= b.page <= entry.page + 1 and i not in used
        ]  # fmt: skip
        for i in candidates:
            if _key(self.blocks[i].text) == target:
                return [i], 0.95
        # A title split over two blocks (for example "Chapter One" / "The Arrival").
        for i in candidates:
            j = i + 1
            if (
                j < len(self.blocks)
                and _key(self.blocks[i].text + " " + self.blocks[j].text) == target
            ):
                return [i, j], 0.9
        best, score = None, 0.0
        for i in candidates:
            text = _key(self.blocks[i].text)
            if not text or len(text) > 2 * len(target) + 10:
                continue
            ratio = difflib.SequenceMatcher(None, text, target).ratio()
            if ratio > score:
                best, score = i, ratio
        if best is not None and score >= 0.8:
            return [best], 0.75
        return None

    def _is_heading_like(self, i: int) -> bool:
        b = self.blocks[i]
        text = b.text.strip()
        return 0 < len(text) <= 100 and not b.quote and len(b.lines) <= 3

    def _pattern_headings(self) -> list[_Heading]:
        out = []
        for i, b in enumerate(self.blocks):
            text = b.text.strip()
            if not self._is_heading_like(i) or not (i in self.first_on_page or b.centred):
                continue
            named, kind = named_kind(text)
            if named and kind is None:
                continue
            if CHAPTER_PATTERN.match(text) or named or PART_PATTERN.match(text):
                conf = 0.85
            elif (BARE_NUMBER.match(text) or BARE_WORD_NUMBER.match(text)) and b.centred:
                conf = 0.7
            else:
                continue
            k = kind if named and kind else SectionKind.CHAPTER
            out.append(_Heading(i, k, 0, "pattern", conf))
        return out if len(out) >= 2 else []

    def _size_headings(self) -> list[_Heading]:
        out = []
        for i, b in enumerate(self.blocks):
            if not self._is_heading_like(i) or b.size < 1.3 * self.m.body_size:
                continue
            if i not in self.first_on_page and not b.centred:
                continue
            named, kind = named_kind(b.text)
            out.append(
                _Heading(
                    i,
                    (kind or SectionKind.CHAPTER) if named else SectionKind.CHAPTER,
                    0,
                    "font-size",
                    0.65,
                )
            )
        return out if len(out) >= 2 else []

    def _regex_headings(self) -> list[_Heading]:
        assert self.regex is not None
        return [
            _Heading(i, SectionKind.CHAPTER, 0, "regex", 0.9)
            for i, b in enumerate(self.blocks)
            if self._is_heading_like(i) and self.regex.search(b.text.strip())
        ]

    def _assign_parts(self, found: list[_Heading]) -> None:
        """Without an outline, "Part One" headings become parts over the chapters after them."""
        if not any(PART_PATTERN.match(self.blocks[h.index].text.strip()) for h in found):
            return
        in_part = False
        for h in found:
            if PART_PATTERN.match(self.blocks[h.index].text.strip()):
                h.kind, h.depth, in_part = SectionKind.PART, 0, True
            elif h.kind is SectionKind.CHAPTER and in_part:
                h.depth = 1
            else:
                in_part = False

    def _merge_subtitles(self, found: list[_Heading]) -> None:
        taken = {i for h in found for i in (h.title_indices or [h.index])}
        for h in found:
            if h.title_indices:
                continue
            h.title_indices = [h.index]
            j = h.index + 1
            if j >= len(self.blocks) or j in taken:
                continue
            b, nxt = self.blocks[h.index], self.blocks[j]
            if (
                nxt.page == b.page
                and self._is_heading_like(j)
                and nxt.size > self.m.body_size * 1.1
            ):
                h.title_indices.append(j)
                taken.add(j)

    def headings(self) -> list[_Heading]:
        found = self._match_outline()
        if found:
            matched = sum(1 for h in found if h.depth >= 0)
            if matched < 0.5 * sum(
                1 for e in self.outline if e.level <= min(x.level for x in self.outline) + 1
            ):
                self.diag.warn("outline-poor", "Most outline entries match no printed heading.")
        if not found:
            found = self._printed_toc_headings()
            if found:
                self._assign_parts(found)
        if not found and self.regex is not None:
            found = self._regex_headings()
        if not found:
            found = self._pattern_headings()
            if found:
                self._assign_parts(found)
        if not found:
            found = self._size_headings()
            if found:
                self._assign_parts(found)
        self._merge_subtitles(found)
        return found

    # ------------------------------------------------------------- assembly

    def build(self) -> tuple[list[Section], list[Section], list[Section]]:
        found = self.headings()
        for h in found:
            label = " / ".join(self.blocks[i].text.strip()[:60] for i in h.title_indices)
            self.diag.decided(
                "chapter-detection" if h.depth >= 0 else "heading-detection",
                f"Heading: {label!r}",
                h.confidence,
                h.method,
                self.blocks[h.index].source,
            )
        tops = [h for h in found if h.depth >= 0]
        subs = {h.index: h for h in found if h.depth < 0}
        # Only section titles are lifted out of the text; sub-headings stay in place.
        title_parts = {i for h in found if h.depth >= 0 for i in h.title_indices}
        if not tops:
            self.diag.warn(
                "no-chapters",
                "No chapter headings were found; the whole text is one chapter. "
                "Use --chapter-regex if the manuscript has headings this missed.",
            )
            return (
                [],
                [
                    Section(
                        SectionKind.CHAPTER,
                        None,
                        self._blocks(range(len(self.blocks)), subs, title_parts),
                    )
                ],
                [],
            )

        starts = self._section_starts(tops)
        front = self._preamble(range(starts[0]), subs, title_parts)
        sections: list[tuple[Section, bool]] = []
        for n, h in enumerate(tops):
            start = starts[n]
            end = starts[n + 1] if n + 1 < len(tops) else len(self.blocks)
            title = self._title(h.title_indices)
            opener = self._blocks(range(start, h.index), subs, title_parts)
            blocks = self._blocks(range(h.index, end), subs, title_parts)
            has_children = n + 1 < len(tops) and tops[n + 1].depth > h.depth
            if not blocks and h.kind is not SectionKind.PART and not has_children:
                self.diag.warn(
                    "empty-chapter",
                    f"The section {self.blocks[h.index].text.strip()!r} has no text.",
                    self.blocks[h.index].source,
                )
            named, _ = named_kind(self.blocks[h.index].text)
            sections.append(
                (
                    Section(
                        h.kind, title, blocks, self.blocks[h.index].source, max(h.depth, 0), opener
                    ),
                    named,
                )
            )

        story = [
            i for i, (s, _) in enumerate(sections) if s.kind in STORY or s.kind is SectionKind.PART
        ]
        first_story, last_story = (story[0], story[-1]) if story else (0, len(sections) - 1)
        body: list[Section] = []
        back: list[Section] = []
        for i, (section, named) in enumerate(sections):
            if i < first_story and named:
                front.append(section)
            elif i > last_story and section.kind in BACK:
                back.append(section)
            else:
                body.append(section)
        return front, body, back

    def _section_starts(self, tops: list[_Heading]) -> list[int]:
        """Where each section starts. A quotation printed above a heading on the same
        page (a document or epigraph set over a chapter title) opens that section and
        is rendered before the title, so the author's order is kept exactly."""
        starts = []
        for n, h in enumerate(tops):
            floor = tops[n - 1].index + 1 if n else 0
            start = h.index
            page = self.blocks[h.index].page
            while (
                start - 1 >= floor
                and self.blocks[start - 1].quote
                and self.blocks[start - 1].page == page
            ):
                start -= 1
            starts.append(start)
        return starts

    def _title(self, indices: list[int]) -> list[Inline]:
        out: list[Inline] = []
        for n, i in enumerate(indices):
            if n:
                out.append(LineBreak())
            out.extend(build_inlines([s for s in self.blocks[i].segments]))
        return out

    def _preamble(self, indices: range, subs: dict[int, _Heading], skip: set[int]) -> list[Section]:
        pages: dict[int, list[int]] = {}
        for i in indices:
            pages.setdefault(self.blocks[i].page, []).append(i)
        out = []
        for page_indices in pages.values():
            kind, confidence = self._classify_front(page_indices)
            self.diag.decided(
                "front-matter",
                f"Front-matter page classified as {kind.value}",
                confidence,
                "content",
                self.blocks[page_indices[0]].source,
            )
            span = range(page_indices[0], page_indices[-1] + 1)
            titles: set[int] = set()
            if kind is SectionKind.TITLEPAGE:
                # The largest type on a title page is the book's title.
                biggest = max(page_indices, key=lambda i: self.blocks[i].size)
                if self.blocks[biggest].size >= 1.4 * self.m.body_size:
                    titles.add(biggest)
            out.append(
                Section(
                    kind,
                    None,
                    self._blocks(span, subs, skip, titles),
                    self.blocks[page_indices[0]].source,
                )
            )
        return out

    def _classify_front(self, indices: list[int]) -> tuple[SectionKind, float]:
        texts = [self.blocks[i].text.strip() for i in indices]
        joined = " ".join(texts)
        if self.title and _key(self.title) in _key(joined):
            return SectionKind.TITLEPAGE, 0.85
        first_page = self.blocks[indices[0]].page == self.blocks[0].page
        if (
            first_page
            and len(joined) <= 300
            and all(
                self.blocks[i].centred or self.blocks[i].size > self.m.body_size * 1.2
                for i in indices
            )
        ):
            return SectionKind.TITLEPAGE, 0.7
        if COPYRIGHT_RE.search(joined):
            return SectionKind.COPYRIGHT, 0.85
        if (
            len(texts) <= 3
            and len(joined) <= 200
            and re.match(r"^(for|to|in memory)\b", joined, re.IGNORECASE)
        ):
            return SectionKind.DEDICATION, 0.75
        return SectionKind.OTHER, 0.4

    # --------------------------------------------------------------- blocks

    def _blocks(
        self,
        indices: range,
        subs: dict[int, _Heading],
        skip: set[int],
        titles: frozenset[int] | set[int] = frozenset(),
    ) -> list[Block]:
        out: list[Block] = []
        quote: list[Block] | None = None
        for i in indices:
            for img in self.images:
                if img.before == i:
                    out.append(self._image(img))
            if i in skip:
                continue
            b = self.blocks[i]
            if i in self.toc_blocks:
                for ln in b.lines:
                    ref = PdfRef(
                        ln.page,
                        (round(ln.x0, 1), round(ln.y0, 1), round(ln.x1, 1), round(ln.y1, 1)),
                    )
                    self.diag.removed("manual-toc", ln.text, ref)
                continue
            text = b.text.strip()
            if not b.quote:
                quote = None
            marker = scene_break_confidence(text, b.centred) if len(b.lines) == 1 else 0.0
            if marker:
                self.diag.removed("scene-break-marker", text, b.source)
                self.diag.decided(
                    "scene-break", "Scene break from a typed marker.", marker, "marker", b.source
                )
                out.append(SceneBreak(b.source))
                continue
            if (
                b.gap_before is not None
                and b.gap_before > GAP_SCENE_BREAK
                and not b.quote
                and not b.centred
                and abs(b.size - self.m.body_size) < 0.4
                and out
                and isinstance(out[-1], Paragraph)
                and out[-1].align is Align.START
                and i not in subs
            ):
                self.diag.decided(
                    "scene-break",
                    f"Scene break from a gap of {b.gap_before:.1f} lines.",
                    0.7,
                    "gap",
                    b.source,
                )
                out.append(SceneBreak(b.source))
            content = build_inlines(b.segments)
            if i in titles:
                out.append(Heading(content, 1, b.source))
                continue
            if i in subs:
                out.append(Heading(content, 2, b.source))
                continue
            align = Align.CENTRE if b.centred else Align.START
            para = Paragraph(content, align, b.source)
            if b.quote:
                if quote is None:
                    quote = []
                    out.append(BlockQuote(quote, b.source))
                quote.append(para)
            else:
                out.append(para)
        tail = indices.stop
        for img in self.images:
            if img.before == tail and tail == len(self.blocks):
                out.append(self._image(img))
        return out

    def _image(self, img: PlacedImage) -> Block:
        x0, y0, x1, y1 = img.bbox
        source = PdfRef(img.page, (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)))
        if x1 - x0 <= ORNAMENT_MAX_W and y1 - y0 <= ORNAMENT_MAX_H:
            self.diag.removed("ornament-image", "", source)
            self.diag.decided(
                "scene-break", "Scene break from a small ornament image.", 0.75, "ornament", source
            )
            return SceneBreak(source)
        return Image(img.resource_id, None, source=source)
