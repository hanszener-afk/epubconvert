"""Rebuild paragraphs from positioned lines.

Signals, all measured against the body text's own metrics:
- a first-line indent, a short last line (in justified text), or an extra
  vertical gap starts a new paragraph;
- centred lines, changes of size or weight, and indented smaller text
  (quotations, letters, epigraphs) form their own blocks;
- a paragraph continues onto the next page when the last line of the page is
  full and the next page does not start with an indent. A gap at a page
  boundary is never a scene break.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from novel2epub.diagnostics import Diagnostics
from novel2epub.ir import PdfRef
from novel2epub.normalise.inline import Segment, plain
from novel2epub.normalise.scenebreaks import scene_break_confidence
from novel2epub.pdf.extract import Line, Page
from novel2epub.pdf.hyphenation import HYPHENS, Dehyphenator

TERMINAL = ".!?\u2026\"'\u201d\u2019)]:"


@dataclass(frozen=True)
class Metrics:
    body_size: float
    left: float
    right: float
    line_gap: float
    indent: float | None
    justified: bool

    @property
    def centre(self) -> float:
        return (self.left + self.right) / 2


@dataclass
class PdfBlock:
    lines: list[Line]
    segments: list[Segment] = field(default_factory=list)
    centred: bool = False
    quote: bool = False
    gap_before: float | None = None  # in line gaps; None at the top of a page
    continued: bool = False  # joined across a page break

    @property
    def text(self) -> str:
        return plain(self.segments)

    @property
    def page(self) -> int:
        return self.lines[0].page

    @property
    def size(self) -> float:
        return self.lines[0].size

    @property
    def source(self) -> PdfRef:
        ln = self.lines[0]
        return PdfRef(ln.page, (round(ln.x0, 1), round(ln.y0, 1), round(ln.x1, 1), round(ln.y1, 1)))

    def all(self, flag: str) -> bool:
        return all(ln.all(flag) for ln in self.lines)


def _mode(values: list[float], default: float) -> float:
    return Counter(values).most_common(1)[0][0] if values else default


def measure(pages: list[Page]) -> Metrics:
    sizes: Counter[float] = Counter()
    for pg in pages:
        for ln in pg.lines:
            sizes[ln.size] += len(ln.text.strip())
    body_size = sizes.most_common(1)[0][0] if sizes else 10.0
    body = [ln for pg in pages for ln in pg.lines if abs(ln.size - body_size) < 0.3]
    left = _mode([round(ln.x0) for ln in body], 72.0)
    right = _mode([round(ln.x1) for ln in body], 540.0)
    justified = bool(body) and sum(abs(ln.x1 - right) < 1.5 for ln in body) / len(body) >= 0.4
    if not justified and body:
        right = sorted(ln.x1 for ln in body)[int(len(body) * 0.95)]
    gaps: list[float] = []
    for pg in pages:
        prev = None
        for ln in pg.lines:
            if abs(ln.size - body_size) >= 0.3:
                prev = None
                continue
            if prev is not None:
                delta = round(prev.y1 - ln.y1, 1)
                if 0 < delta < 3 * body_size:
                    gaps.append(delta)
            prev = ln
    line_gap = _mode(gaps, body_size * 1.2)
    indents = [round(ln.x0 - left) for ln in body if 2 < ln.x0 - left < 6 * body_size]
    indent = None
    if indents and len(indents) >= max(2, 0.03 * len(body)):
        indent = _mode([float(i) for i in indents], 0.0)
    return Metrics(body_size, left, right, line_gap, indent, justified)


class Layout:
    def __init__(
        self,
        metrics: Metrics,
        dehyph: Dehyphenator,
        diag: Diagnostics,
        markers: dict[tuple[int, str], str] | None = None,
    ) -> None:
        self.m = metrics
        self.dehyph = dehyph
        self.diag = diag
        self.markers = dict(markers or {})  # (page, marker) -> note id, consumed once

    # ------------------------------------------------------------ line tests

    def centred(self, ln: Line) -> bool:
        m = self.m
        mid = (ln.x0 + ln.x1) / 2
        width = m.right - m.left
        # Symmetric margins of at least an em. A long symmetric line set smaller
        # than the body is an indented quotation, not centred text.
        # A line that starts exactly at the paragraph indent is a short paragraph
        # that happens to sit near the middle.
        at_indent = m.indent is not None and abs((ln.x0 - m.left) - m.indent) < 1.5
        return (
            not at_indent
            and abs(mid - m.centre) < max(3.0, 0.03 * width)
            and ln.x0 - m.left > m.body_size
            and m.right - ln.x1 > m.body_size
            and (ln.x1 - ln.x0 < 0.8 * width or ln.size >= m.body_size - 0.4)
        )

    def same_centred_block(self, prev: Line, ln: Line) -> bool:
        """Consecutive centred lines in one style form one block (a title over two lines)."""
        return (
            self.centred(prev)
            and self.centred(ln)
            and abs(ln.size - prev.size) <= 0.4
            and ln.all("bold") == prev.all("bold")
            and ln.all("italic") == prev.all("italic")
            and self._gap_ratio(prev, ln) <= 1.6
            and not scene_break_confidence(ln.text.strip(), True)
            and not scene_break_confidence(prev.text.strip(), True)
        )

    def quote(self, ln: Line) -> bool:
        m = self.m
        threshold = m.left + (m.indent or m.body_size) + 2
        return ln.x0 > threshold and ln.size < m.body_size - 0.4 and not self.centred(ln)

    def indented(self, ln: Line) -> bool:
        m = self.m
        if m.indent is None:
            return False
        offset = ln.x0 - m.left
        return 0.5 * m.indent <= offset <= 1.8 * m.indent

    def short(self, ln: Line) -> bool:
        m = self.m
        if m.justified:
            return m.right - ln.x1 > 1.5 * m.body_size
        return ln.text.rstrip()[-1:] in TERMINAL and ln.x1 - m.left < 0.75 * (m.right - m.left)

    def _gap_ratio(self, prev: Line, ln: Line) -> float:
        scale = max(prev.size, ln.size) / self.m.body_size
        return (prev.y1 - ln.y1) / (self.m.line_gap * scale)

    def new_block(self, prev: Line, ln: Line) -> bool:
        if self.same_centred_block(prev, ln):
            return False
        if self.centred(ln) or self.centred(prev):
            return True
        if self.quote(ln) != self.quote(prev) or abs(ln.size - prev.size) > 0.4:
            return True
        if ln.all("bold") != prev.all("bold"):
            return True
        if self._gap_ratio(prev, ln) > 1.4:
            return True
        if not self.quote(ln) and self.indented(ln):
            return True
        return not self.quote(ln) and self.short(prev)

    # ----------------------------------------------------------------- joins

    def _segments(self, ln: Line) -> list[Segment]:
        out: list[Segment] = []
        before = ""
        for span in ln.spans:
            marker = span.text.strip()
            note = self.markers.pop((ln.page, marker), None) if span.superscript else None
            if note is not None:
                self.diag.removed("note-mark", marker, _ref(ln), before)
                out.append(Segment("", frozenset(), None, "noteref", note))
                continue
            flags = (
                ("bold", span.bold),
                ("italic", span.italic),
                ("superscript", span.superscript),
            )
            fmt = frozenset(f for f, on in flags if on)
            out.append(Segment(span.text, fmt))
            before += span.text
        return out

    # -------------------------------------------------------------- drop caps

    def drop_caps(self, lines: list[Line]) -> list[Line]:
        """Join a large initial letter to the word it begins, and square up the
        lines set beside it so they are not read as indented paragraphs."""
        body = self.m.body_size
        caps = [ln for ln in lines if _is_cap(ln.text.strip()) and ln.size >= 2.2 * body]
        for cap in caps:
            beside = sorted(
                (ln for ln in lines
                 if ln is not cap and cap.x1 - 3 <= ln.x0 <= cap.x1 + 2 * body
                 and cap.y0 - 1 <= ln.y1 <= cap.y1 + 3),
                key=lambda ln: -ln.y1,
            )  # fmt: skip
            if not beside:
                continue
            first = beside[0]
            letter = cap.text.strip()
            word = _leading_word(first.text.lstrip())
            joined = (letter + word).lower()
            words = self.dehyph.wordlist
            if not letter[-1].isalpha():
                sep, confidence = "", 0.85  # an opening quotation mark set as the drop cap
            elif joined.lstrip(_OPENING) in self.dehyph.counts or joined.lstrip(_OPENING) in words:
                sep, confidence = "", 0.9
            elif letter.lower() in ("a", "i", "o") and word.lower() in words:
                sep, confidence = " ", 0.8
            else:
                sep, confidence = "", 0.6
            lead = next(s for s in first.spans if s.text.strip())
            lead.text = letter + sep + lead.text.lstrip()
            for ln in beside:
                ln.x0 = self.m.left
            lines = [ln for ln in lines if ln is not cap]
            # The rewrite is logged first: the reference text has the word where the
            # paragraph starts and the separate letter somewhere after it.
            self.diag.removed("drop-cap-join", word, _ref(first), replacement=letter + sep + word)
            self.diag.removed("drop-cap", letter, _ref(cap))
            self.diag.decided(
                "drop-cap", f"Drop cap {letter!r} joined as {letter + sep + word!r}.",
                confidence, "drop-cap", _ref(cap),
            )  # fmt: skip
        return lines

    def append_line(self, block: PdfBlock, ln: Line) -> None:
        if block.centred and block.lines:
            # Centred lines were broken by the author, not by the measure: keep the break.
            block.segments.append(Segment("", frozenset(), None, "break"))
            block.segments.extend(self._segments(ln))
        else:
            self.join_segments(block, self._segments(ln), ln)
        block.lines.append(ln)

    def join_segments(self, block: PdfBlock, new: list[Segment], ln: Line) -> None:
        """Append segments that start a new visual line, joining at the line break."""
        if not block.segments:
            block.segments = new
            return
        last = next((s for s in reversed(block.segments) if s.text), None)
        first = next((s for s in new if s.text.strip()), None)
        if last is not None and first is not None:
            last.text = last.text.rstrip()
            first.text = first.text.lstrip()
            end, start = last.text[-1:], first.text[:1]
            if end in HYPHENS and last.text[-2:-1].isalpha() and start.isalpha():
                left = _trailing_word(last.text[:-1])
                right = _leading_word(first.text)
                decision = self.dehyph.decide(left, right, end)
                if decision.join:
                    last.text = last.text[:-1]
                elif end != "-":
                    last.text = last.text[:-1] + "-"
                self.diag.decided(
                    "dehyphenation",
                    f"{left}{end}|{right} read as {decision.text!r}",
                    decision.confidence,
                    decision.method,
                    PdfRef(
                        ln.page,
                        (round(ln.x0, 1), round(ln.y0, 1), round(ln.x1, 1), round(ln.y1, 1)),
                    ),
                )
            elif end in "\u2014\u2013" and last.text[-2:-1] != " ":
                pass  # an unspaced dash: the words run on
            elif start in "\u2014" and end.isalnum():
                pass
            elif block.segments[-1].kind != "text":
                block.segments.append(Segment(" "))  # after a note reference, not before it
            else:
                last.text += " "
        block.segments.extend(new)

    # ---------------------------------------------------------------- blocks

    def page_blocks(self, pg: Page) -> list[PdfBlock]:
        blocks: list[PdfBlock] = []
        prev: Line | None = None
        for ln in self.drop_caps(pg.lines):
            if prev is None or self.new_block(prev, ln):
                gap = None if prev is None else self._gap_ratio(prev, ln)
                blocks.append(
                    PdfBlock([], centred=self.centred(ln), quote=self.quote(ln), gap_before=gap)
                )
            self.append_line(blocks[-1], ln)
            prev = ln
        return blocks

    def continues(self, prev: PdfBlock, nxt: PdfBlock) -> bool:
        if prev.centred or nxt.centred or prev.quote != nxt.quote:
            return False
        if abs(prev.size - nxt.size) > 0.4 or prev.all("bold") != nxt.all("bold"):
            return False
        last, first = prev.lines[-1], nxt.lines[0]
        if last.text.rstrip()[-1:] in HYPHENS:
            return True
        if self.indented(first):
            return False
        if self.m.justified:
            return not self.short(last)
        return last.text.rstrip()[-1:] not in TERMINAL


_OPENING = "\u201c\u2018\"'"


def _is_cap(text: str) -> bool:
    """A drop cap: a letter, an opening quotation mark, or a quotation mark and a letter."""
    if not 1 <= len(text) <= 2:
        return False
    if text[0] in _OPENING:
        return len(text) == 1 or text[1].isalpha()
    return text.isalpha()


def _trailing_word(text: str) -> str:
    i = len(text)
    while i > 0 and (text[i - 1].isalpha() or text[i - 1] in "'\u2019"):
        i -= 1
    return text[i:]


def _leading_word(text: str) -> str:
    i = 0
    while i < len(text) and text[i].isalpha():
        i += 1
    return text[:i]


def _ref(ln: Line) -> PdfRef:
    return PdfRef(ln.page, (round(ln.x0, 1), round(ln.y0, 1), round(ln.x1, 1), round(ln.y1, 1)))


def build_note_blocks(
    lines_by_note: list[list[Line]], metrics: Metrics, dehyph: Dehyphenator, diag: Diagnostics
) -> list[PdfBlock]:
    """One block per note, with its lines joined (and dehyphenated) like body text."""
    layout = Layout(metrics, dehyph, diag)
    out = []
    for lines in lines_by_note:
        block = PdfBlock([])
        for ln in lines:
            layout.append_line(block, ln)
        out.append(block)
    return out


def build_blocks(
    pages: list[Page],
    metrics: Metrics,
    dehyph: Dehyphenator,
    diag: Diagnostics,
    markers: dict[tuple[int, str], str] | None = None,
) -> list[PdfBlock]:
    layout = Layout(metrics, dehyph, diag, markers)
    out: list[PdfBlock] = []
    for pg in pages:
        blocks = layout.page_blocks(pg)
        if (
            out
            and blocks
            and out[-1].page == pg.number - 1
            and layout.continues(out[-1], blocks[0])
        ):
            head = blocks.pop(0)
            prev = out[-1]
            # Join the already-built segments: rebuilding them from the lines would
            # lose note references, which are consumed when first built.
            layout.join_segments(prev, head.segments, head.lines[0])
            prev.lines.extend(head.lines)
            prev.continued = True
        out.extend(blocks)
    for (page, marker), _ in sorted(layout.markers.items()):
        diag.warn(
            "footnote-marker-missing",
            f"Footnote {marker!r} on page {page} has no marker in the text; it was placed with "
            "the notes but nothing links to it.",
        )
    return out
