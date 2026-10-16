"""Interpret literal HTML formatting tags typed into manuscript text.

Writers who post to AO3 and similar sites often type <i>...</i> directly into
their documents. Balanced <i>, <em>, <b>, and <strong> become formatting and
each tag is logged as a removal. Anything else stays as literal text:
unbalanced tags and other HTML tag names produce a warning; non-HTML angle
brackets (such as "<grin>" or "a < b") are left alone silently.
"""

from __future__ import annotations

import re

from novel2epub.diagnostics import Diagnostics
from novel2epub.ir import SourceRef
from novel2epub.normalise.inline import Segment

INTERPRETED = {"i": "italic", "em": "italic", "b": "bold", "strong": "bold"}
OTHER_HTML = {
    "u", "s", "strike", "del", "ins", "span", "p", "br", "sup", "sub", "small", "big",
    "a", "font", "center", "blockquote", "hr", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "code", "pre", "tt", "abbr", "cite", "q", "mark",
}  # fmt: skip
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)\s*/?>")


def interpret_tags(
    segments: list[Segment], diag: Diagnostics, source: SourceRef | None
) -> list[Segment]:
    texts = [(i, s) for i, s in enumerate(segments) if s.kind == "text"]
    joined = "".join(s.text for _, s in texts)
    if "<" not in joined:
        return segments

    # Match opening and closing tags with a stack per tag kind.
    tags = list(_TAG.finditer(joined))
    pairs: list[tuple[int, int, int, int, str]] = []  # open start/end, close start/end, fmt
    open_stack: list[re.Match[str]] = []
    unbalanced: list[str] = []
    unknown: set[str] = set()
    for m in tags:
        name = m.group(2).lower()
        if name not in INTERPRETED:
            if name in OTHER_HTML:
                unknown.add(m.group(0))
            continue
        fmt = INTERPRETED[name]
        if m.group(1) == "":
            open_stack.append(m)
            continue
        match = next(
            (o for o in reversed(open_stack) if INTERPRETED[o.group(2).lower()] == fmt), None
        )
        if match is None:
            unbalanced.append(m.group(0))
            continue
        open_stack.remove(match)
        pairs.append((match.start(), match.end(), m.start(), m.end(), fmt))
    unbalanced.extend(o.group(0) for o in open_stack)
    if unbalanced:
        diag.warn(
            "html-tag-unbalanced",
            f"Unbalanced literal tag(s) {', '.join(sorted(set(unbalanced)))} left as text.",
            source,
        )
    if unknown:
        diag.warn(
            "html-tag-unknown",
            f"Literal HTML tag(s) {', '.join(sorted(unknown))} are not interpreted; left as text.",
            source,
        )
    if not pairs:
        return segments

    hidden: set[int] = set()  # character positions of interpreted tags
    removed: list[tuple[int, str]] = []
    for os_, oe, cs, ce, _ in pairs:
        hidden.update(range(os_, oe))
        hidden.update(range(cs, ce))
        removed.append((os_, joined[os_:oe]))
        removed.append((cs, joined[cs:ce]))
    for start, tag in sorted(removed):
        context = "".join(c for k, c in enumerate(joined[:start]) if k not in hidden)
        diag.removed("html-tag", tag, source, context)

    def fmt_at(pos: int) -> frozenset[str]:
        return frozenset(f for os_, oe, cs, _, f in pairs if oe <= pos < cs)

    out: list[Segment] = []
    pos = 0
    for seg in segments:
        if seg.kind != "text":
            out.append(Segment(seg.text, seg.fmt | fmt_at(pos), seg.link, seg.kind, seg.note_id))
            continue
        run_chars: list[str] = []
        run_fmt: frozenset[str] | None = None
        for ch in seg.text:
            if pos in hidden:
                pos += 1
                continue
            f = fmt_at(pos)
            if run_fmt is not None and f != run_fmt:
                out.append(Segment("".join(run_chars), seg.fmt | run_fmt, seg.link))
                run_chars = []
            run_fmt = f
            run_chars.append(ch)
            pos += 1
        if run_chars:
            out.append(Segment("".join(run_chars), seg.fmt | (run_fmt or frozenset()), seg.link))
    return out
