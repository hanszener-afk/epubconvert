"""Turn raw DOCX paragraphs into front matter, chapters, and back matter.

Chapter headings, in order of trust:
  1. paragraph styles or outline levels (Heading 1, styles based on it);
  2. --chapter-regex, if given;
  3. "Chapter N" / "Prologue" style patterns;
  4. direct formatting (short bold, large, or capitalised lines after a page break).
Each heading is logged as a decision with its method and confidence.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from novel2epub.diagnostics import Diagnostics
from novel2epub.docx.walker import RawPara
from novel2epub.ir import (
    Align,
    Block,
    BlockQuote,
    Heading,
    Image,
    Inline,
    LineBreak,
    Paragraph,
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
from novel2epub.normalise.inline import Segment, build_inlines
from novel2epub.normalise.scenebreaks import scene_break_confidence

QUOTE_STYLES = ("quote", "block text", "intense quote", "block quote", "blockquote")
ORNAMENT_MAX_CX = int(2.5 * 914400)
ORNAMENT_MAX_CY = 914400


@dataclass
class _Heading:
    index: int
    level: int  # 0 = chapter level
    method: str
    confidence: float
    title_indices: list[int] = field(default_factory=list)


def _short(text: str) -> bool:
    t = text.strip()
    return 0 < len(t) <= 80


def _centred(p: RawPara) -> bool:
    return p.props.jc == "center"


class Structurer:
    def __init__(
        self, paras: list[RawPara], diag: Diagnostics, chapter_regex: str | None, title: str
    ) -> None:
        self.paras = paras
        self.diag = diag
        self.regex = re.compile(chapter_regex) if chapter_regex else None
        self.title = title
        self.kind = [self._classify(p) for p in paras]
        self.boundary = self._boundaries()
        sizes = Counter(s for p in paras for s in p.sizes)
        self.body_size = sizes.most_common(1)[0][0] if sizes else 24

    # ----------------------------------------------------------- classify

    def _classify(self, p: RawPara) -> str:
        text = p.text.strip()
        if p.in_toc:
            return "toc"
        if not text and not p.images and not p.hr and not p.has_noteref:
            return "suppressed" if p.suppressed else "blank"
        if not text and p.hr and not p.images:
            return "hr"
        if not text and len(p.images) == 1 and not p.has_noteref:
            img = p.images[0]
            if 0 < img.cx <= ORNAMENT_MAX_CX and 0 < img.cy <= ORNAMENT_MAX_CY:
                return "ornament"
        if text and not p.images and scene_break_confidence(text, _centred(p)) > 0:
            return "marker"
        return "text"

    def _boundaries(self) -> list[bool]:
        """boundary[i]: a page break separates paragraph i from the previous content."""
        out = []
        pending = True  # document start counts as a boundary
        for p, k in zip(self.paras, self.kind, strict=True):
            if p.break_before:
                pending = True
            out.append(pending)
            if k not in ("blank", "suppressed"):
                pending = False
            if p.break_after:
                pending = True
        return out

    # ------------------------------------------------------------ headings

    def _style_headings(self) -> list[_Heading]:
        found = [
            (i, p.props.outline_level)
            for i, p in enumerate(self.paras)
            if self.kind[i] == "text" and p.props.outline_level is not None
        ]
        if not found:
            titled = [i for i, p in enumerate(self.paras)
                      if self.kind[i] == "text" and "title" in p.props.style_names]  # fmt: skip
            if len(titled) >= 2:
                return [_Heading(i, 0, "style", 0.85) for i in titled]
            return []
        top = min(level for _, level in found if level is not None)
        return [_Heading(i, (level or 0) - top, "style", 0.95) for i, level in found]

    def _regex_headings(self) -> list[_Heading]:
        assert self.regex is not None
        return [
            _Heading(i, 0, "regex", 0.9)
            for i, p in enumerate(self.paras)
            if self.kind[i] == "text" and self.regex.search(p.text.strip())
        ]

    def _pattern_headings(self) -> list[_Heading]:
        out = []
        for i, p in enumerate(self.paras):
            text = p.text.strip()
            if self.kind[i] != "text" or not _short(text):
                continue
            named, _ = named_kind(text)
            if CHAPTER_PATTERN.match(text) or named:
                conf = 0.85
            elif (BARE_NUMBER.match(text) or BARE_WORD_NUMBER.match(text)) and (
                self.boundary[i] or _centred(p)
            ):
                conf = 0.7
            else:
                continue
            if self.boundary[i] or _centred(p) or p.all_have("bold"):
                conf += 0.05
            out.append(_Heading(i, 0, "pattern", conf))
        return out

    def _looks_like_heading(self, i: int) -> bool:
        p = self.paras[i]
        text = p.text.strip()
        if self.kind[i] != "text" or not _short(text) or p.has_noteref:
            return False
        if text[-1] in ".,;:" and not text.endswith("..."):
            return False
        big = bool(p.sizes) and min(p.sizes) >= self.body_size * 1.25
        letters = [c for c in text if c.isalpha()]
        shouty = len(letters) >= 3 and all(c.isupper() for c in letters)
        return (p.all_have("bold") or big or shouty) and (self.boundary[i] or _centred(p))

    def _format_headings(self) -> list[_Heading]:
        out = [
            _Heading(i, 0, "formatting", 0.6)
            for i in range(len(self.paras))
            if self._looks_like_heading(i)
        ]
        return out if len(out) >= 2 else []

    def headings(self) -> list[_Heading]:
        found = self._without_contents_lists(self._style_headings())
        if not found and self.regex is not None:
            found = self._without_contents_lists(self._regex_headings())
        if not found:
            found = self._without_contents_lists(self._pattern_headings())
            if len(found) < 2 and not any(self.boundary[h.index] for h in found):
                found = []
        if not found:
            found = self._without_contents_lists(self._format_headings())
            if len(found) < 2:
                found = []
        self._merge_subtitles(found)
        return found

    def _without_contents_lists(self, found: list[_Heading]) -> list[_Heading]:
        """Drop runs of three or more headings with no text between: a typed contents list.

        If the run starts with a "Contents" heading, that heading is kept so the
        whole list is removed with it; otherwise the lines stay as ordinary text
        and the decision is flagged for review.
        """
        chapters = sorted((h for h in found if h.level == 0), key=lambda h: h.index)
        runs: list[list[_Heading]] = []
        for h in chapters:
            # Contents entries sit on one page; a page break starts a new run.
            if (
                runs
                and not self.boundary[h.index]
                and all(
                    self.kind[k] in ("blank", "suppressed")
                    for k in range(runs[-1][-1].index + 1, h.index)
                )
            ):
                runs[-1].append(h)
            else:
                runs.append([h])
        drop: set[int] = set()
        candidates = {h.index for h in chapters}
        for run in runs:
            if len(run) < 3:
                continue
            # The last line of a run is a real heading when prose follows it.
            nxt = next(
                (k for k in range(run[-1].index + 1, len(self.paras))
                 if self.kind[k] not in ("blank", "suppressed")),
                None,
            )  # fmt: skip
            if nxt is not None and self.kind[nxt] == "text" and nxt not in candidates:
                run = run[:-1]
            named, kind = named_kind(self.paras[run[0].index].text)
            if named and kind is None:
                drop.update(h.index for h in run[1:])
            else:
                drop.update(h.index for h in run)
                self.diag.decided(
                    "contents-list",
                    f"{len(run)} consecutive heading-like lines were read as a typed contents "
                    "list and kept as text.",
                    0.5,
                    run[0].method,
                    self.paras[run[0].index].source,
                )
        return [h for h in found if h.index not in drop]

    def _merge_subtitles(self, headings: list[_Heading]) -> None:
        indices = {h.index for h in headings}
        for h in headings:
            h.title_indices = [h.index]
            if h.level != 0:
                continue
            j = h.index + 1
            if j >= len(self.paras) or j in indices or self.kind[j] != "text":
                continue
            nxt = self.paras[j]
            text = nxt.text.strip()
            if not _short(text) or text[-1] in ".,;:" or nxt.has_noteref:
                continue
            if h.method == "style":
                if not any("subtitle" in n for n in nxt.props.style_names):
                    continue
            elif not (_centred(nxt) or nxt.all_have("italic") or nxt.all_have("bold")):
                continue
            h.title_indices.append(j)

    # ------------------------------------------------------------- assembly

    def build(self) -> tuple[list[Section], list[Section], list[Section]]:
        headings = self.headings()
        breaks = self._blank_breaks()
        chapter_heads = [h for h in headings if h.level == 0]
        for h in headings:
            self.diag.decided(
                "chapter-detection" if h.level == 0 else "heading-detection",
                f"Heading: {self.paras[h.index].text.strip()[:80]!r}",
                h.confidence,
                h.method,
                self.paras[h.index].source,
            )
        sub_heads = {h.index: h for h in headings if h.level > 0}
        if not chapter_heads:
            self.diag.warn(
                "no-chapters",
                "No chapter headings were found; the whole text is one chapter. "
                "Use --chapter-regex if the manuscript has headings this missed.",
            )
            first = next((i for i, k in enumerate(self.kind) if k == "text"), None)
            if (
                first is not None
                and _short(self.paras[first].text)
                and self.paras[first].text.strip()[-1] not in ".!?\"\u201d'\u2019"
            ):
                self.diag.decided(
                    "title-line",
                    f"The first line {self.paras[first].text.strip()!r} may be a title; it was "
                    "kept as an ordinary paragraph.",
                    0.5,
                    "first-line",
                    self.paras[first].source,
                )
            blocks = self._blocks(range(len(self.paras)), breaks, sub_heads, set())
            return (
                [],
                [
                    Section(
                        SectionKind.CHAPTER,
                        None,
                        blocks,
                        self.paras[0].source if self.paras else None,
                    )
                ],
                [],
            )

        title_parts = {i for h in chapter_heads for i in h.title_indices}
        front: list[Section] = self._preamble(range(chapter_heads[0].index), breaks, sub_heads)
        sections: list[tuple[Section, bool]] = []  # (section, has a known name)
        for n, h in enumerate(chapter_heads):
            end = chapter_heads[n + 1].index if n + 1 < len(chapter_heads) else len(self.paras)
            named, kind = named_kind(self.paras[h.index].text)
            if named and kind is None:
                if self._is_contents_list(range(h.index + 1, end)):
                    self._remove_range(range(h.index, end), "manual-toc")
                    continue
                self.diag.decided(
                    "contents-list",
                    "A 'Contents' section contains ordinary text, so it was kept, not removed.",
                    0.3,
                    h.method,
                    self.paras[h.index].source,
                )
                kind = SectionKind.OTHER
            title = self._title(h.title_indices)
            span = range(max(h.title_indices) + 1, end)
            blocks = self._blocks(span, breaks, sub_heads, title_parts)
            if not blocks:
                self.diag.warn(
                    "empty-chapter",
                    f"The section {self.paras[h.index].text.strip()!r} has no text.",
                    self.paras[h.index].source,
                )
            sections.append(
                (
                    Section(kind or SectionKind.CHAPTER, title, blocks, self.paras[h.index].source),
                    named,
                )
            )

        story = [i for i, (s, _) in enumerate(sections) if s.kind in STORY]
        first_story = story[0] if story else 0
        last_story = story[-1] if story else len(sections) - 1
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

    def _title(self, indices: list[int]) -> list[Inline]:
        out: list[Inline] = []
        for n, i in enumerate(indices):
            if n:
                out.append(LineBreak())
            out.extend(build_inlines(self.paras[i].visible_segments()))
        return out

    def _is_contents_list(self, indices: range) -> bool:
        """Only short entry-like lines may be removed as a typed contents list."""
        texts = [self.paras[i].text.strip() for i in indices if self.kind[i] == "text"]

        def prose(t: str) -> bool:
            return len(t) > 80 or (len(t.split()) >= 8 and t.rstrip('"\u201d\u2019')[-1:] in ".!?")

        return not any(prose(t) for t in texts) and len(texts) <= 200

    def _remove_range(self, indices: range, kind: str) -> None:
        for i in indices:
            p = self.paras[i]
            if p.text.strip():
                self.diag.removed(kind, p.text, p.source)

    def _preamble(
        self, indices: range, breaks: set[int], sub_heads: dict[int, _Heading]
    ) -> list[Section]:
        chunks: list[list[int]] = []
        for i in indices:
            if (
                self.boundary[i]
                and chunks
                and chunks[-1]
                and self.kind[i] not in ("blank", "suppressed")
            ):
                chunks.append([])
            if not chunks:
                chunks.append([])
            chunks[-1].append(i)
        out = []
        for chunk in chunks:
            content = [i for i in chunk if self.kind[i] not in ("blank", "suppressed")]
            if not content:
                self._blocks(range(chunk[0], chunk[-1] + 1), breaks, sub_heads, set())
                continue
            kind, confidence = self._classify_front(content)
            self.diag.decided(
                "front-matter",
                f"Front-matter page classified as {kind.value}",
                confidence,
                "content",
                self.paras[content[0]].source,
            )
            titles: set[int] = set()
            if kind is SectionKind.TITLEPAGE:
                names = {i: self.paras[i].props.style_names for i in content}
                titles = {
                    i
                    for i, ns in names.items()
                    if any(n == "title" or n.endswith(" title") for n in ns)
                }
            blocks = self._blocks(range(chunk[0], chunk[-1] + 1), breaks, sub_heads, set(), titles)
            out.append(Section(kind, None, blocks, self.paras[content[0]].source))
        return out

    def _classify_front(self, content: list[int]) -> tuple[SectionKind, float]:
        texts = [self.paras[i].text.strip() for i in content]
        joined = " ".join(texts)
        if any("title" in self.paras[i].props.style_names for i in content):
            return SectionKind.TITLEPAGE, 0.9
        if self.title and any(norm_name(t) == norm_name(self.title) for t in texts):
            return SectionKind.TITLEPAGE, 0.8
        if COPYRIGHT_RE.search(joined):
            return SectionKind.COPYRIGHT, 0.85
        if (
            len(texts) <= 3
            and len(joined) <= 200
            and re.match(r"^(for|to|in memory)\b", joined, re.IGNORECASE)
        ):
            return SectionKind.DEDICATION, 0.75
        if len(texts) <= 6 and texts and re.match(r"^[\u2014\u2013-]", texts[-1]):
            return SectionKind.EPIGRAPH, 0.6
        return SectionKind.OTHER, 0.4

    # --------------------------------------------------------- scene breaks

    def _blank_breaks(self) -> set[int]:
        """Indices of blank runs (first blank) that count as scene breaks."""
        runs: list[tuple[int, int]] = []  # (start, length) between two text paragraphs
        gaps: list[int] = []
        i = 0
        n = len(self.paras)
        while i < n:
            if self.kind[i] != "text":
                i += 1
                continue
            j = i + 1
            blanks = 0
            while j < n and self.kind[j] in ("blank", "suppressed"):
                blanks += self.kind[j] == "blank"
                j += 1
            if j < n and self.kind[j] == "text" and not self.boundary[j]:
                gaps.append(blanks)
                if blanks:
                    runs.append((i + 1, blanks))
            i = j
        if not gaps:
            return set()
        mode = Counter(gaps).most_common(1)[0][0]
        out: set[int] = set()
        for start, length in runs:
            if length <= mode:
                continue
            if mode == 0 and length == 1:
                self.diag.decided(
                    "scene-break",
                    "A single empty paragraph was not treated as a scene break.",
                    0.5,
                    "blank-lines",
                    self.paras[start].source,
                )
                continue
            confidence = 0.8 if mode == 0 else 0.65
            self.diag.decided(
                "scene-break", f"{length} empty paragraphs read as a scene break.", confidence,
                "blank-lines", self.paras[start].source,
            )  # fmt: skip
            out.add(start)
        return out

    # --------------------------------------------------------------- blocks

    def _blocks(
        self,
        indices: range,
        breaks: set[int],
        sub_heads: dict[int, _Heading],
        skip: set[int],
        titles: set[int] | None = None,
    ) -> list[Block]:
        out: list[Block] = []
        quote: list[Block] | None = None
        idx = list(indices)
        for pos, i in enumerate(idx):
            if i in skip:
                continue
            p = self.paras[i]
            kind = self.kind[i]
            is_quote = kind == "text" and any(
                any(q in name for q in QUOTE_STYLES) for name in p.props.style_names
            )
            if not is_quote and quote is not None:
                quote = None
            if i in breaks:
                out.append(SceneBreak(p.source))
            if kind == "blank":
                self.diag.removed("empty-paragraph", "", p.source)
                continue
            if kind == "suppressed":
                continue
            if kind == "toc":
                if p.text.strip():
                    self.diag.removed("manual-toc", p.text, p.source)
                continue
            if kind in ("hr", "marker", "ornament"):
                if kind == "marker":
                    self.diag.removed("scene-break-marker", p.text.strip(), p.source)
                    conf = scene_break_confidence(p.text, _centred(p))
                elif kind == "ornament":
                    self.diag.removed("ornament-image", "", p.source)
                    conf = 0.9 if _centred(p) else 0.8
                else:
                    conf = 0.9
                self.diag.decided("scene-break", f"Scene break from {kind}.", conf, kind, p.source)
                out.append(SceneBreak(p.source))
                continue
            nxt_bordered = pos + 1 < len(idx) and self.paras[idx[pos + 1]].props.border_bottom
            if (
                p.props.border_top
                and i not in sub_heads
                and not (pos and self.paras[idx[pos - 1]].props.border_top)
            ):
                self.diag.decided(
                    "scene-break", "Scene break from a top border.", 0.7, "border", p.source
                )
                out.append(SceneBreak(p.source))
            block = self._text_block(p, sub_heads.get(i), bool(titles) and i in (titles or set()))
            if block is not None:
                if is_quote:
                    if quote is None:
                        quote = []
                        out.append(BlockQuote(quote, p.source))
                    quote.append(block)
                else:
                    out.append(block)
            for img in p.images:
                if p.text.strip():
                    self.diag.info(
                        "image-moved", "An image inside a paragraph was placed after it.", p.source
                    )
                out.append(Image(img.resource_id, img.alt, source=p.source))
            if p.props.border_bottom and not nxt_bordered and i not in sub_heads:
                self.diag.decided(
                    "scene-break", "Scene break from a bottom border.", 0.75, "border", p.source
                )
                out.append(SceneBreak(p.source))
        return out

    def _text_block(
        self, p: RawPara, heading: _Heading | None, title: bool = False
    ) -> Block | None:
        segs: list[Segment] = p.segments
        if not p.text.strip() and not p.has_noteref:
            return None
        content = build_inlines(segs)
        if title:
            return Heading(content, level=1, source=p.source)
        if heading is not None:
            return Heading(content, level=min(heading.level + 1, 6), source=p.source)
        align = {"center": Align.CENTRE, "right": Align.END, "end": Align.END}.get(
            p.props.jc or "", Align.START
        )
        return Paragraph(content, align, p.source)
