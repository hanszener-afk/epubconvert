"""Flat formatted segments, and building nested IR inlines from them.

Front ends find it easiest to produce a flat list of text runs, each with a
set of formatting flags (as Word does). This module turns that into the
nested IR spans the writer expects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from novel2epub.ir import (
    AllCaps,
    Bold,
    Inline,
    Italic,
    LineBreak,
    Link,
    NoteRef,
    SmallCaps,
    Span,
    Subscript,
    Superscript,
    Text,
)

FORMATS = ("bold", "italic", "smallcaps", "allcaps", "superscript", "subscript")
_SPAN_FOR: dict[str, type[Span]] = {
    "bold": Bold,
    "italic": Italic,
    "smallcaps": SmallCaps,
    "allcaps": AllCaps,
    "superscript": Superscript,
    "subscript": Subscript,
}


@dataclass(slots=True)
class Segment:
    text: str
    fmt: frozenset[str] = field(default_factory=frozenset)
    link: str | None = None
    kind: str = "text"  # "text", "break", or "noteref"
    note_id: str | None = None


def plain(segments: list[Segment]) -> str:
    return "".join(
        s.text if s.kind == "text" else ("\n" if s.kind == "break" else "") for s in segments
    )


def plain_text(inlines: list[Inline]) -> str:
    """The text of built inlines, without formatting; line breaks become spaces."""
    out: list[str] = []
    for node in inlines:
        if isinstance(node, Text):
            out.append(node.text)
        elif isinstance(node, LineBreak):
            out.append(" ")
        elif isinstance(node, Span | Link):
            out.append(plain_text(node.children))
    return "".join(out)


def build_inlines(segments: list[Segment]) -> list[Inline]:
    return _build(segments, ("link", *FORMATS))


def _leaf(seg: Segment) -> Inline:
    if seg.kind == "break":
        return LineBreak()
    if seg.kind == "noteref":
        assert seg.note_id is not None
        return NoteRef(seg.note_id)
    return Text(seg.text)


def _build(segments: list[Segment], order: tuple[str, ...]) -> list[Inline]:
    if not order:
        out: list[Inline] = []
        for seg in segments:
            leaf = _leaf(seg)
            if isinstance(leaf, Text) and out and isinstance(out[-1], Text):
                out[-1] = Text(out[-1].text + leaf.text)
            elif not (isinstance(leaf, Text) and not leaf.text):
                out.append(leaf)
        return out
    attr, rest = order[0], order[1:]
    result: list[Inline] = []
    group: list[Segment] = []
    key: object = None

    def flush() -> None:
        if not group:
            return
        inner = _build(group, rest)
        if attr == "link" and key:
            result.append(Link(inner, href=str(key)))
        elif attr != "link" and key:
            result.append(_SPAN_FOR[attr](inner))  # type: ignore[arg-type]
        else:
            result.extend(inner)

    for seg in segments:
        k: object = seg.link if attr == "link" else (attr in seg.fmt)
        if group and k != key:
            flush()
            group = []
        key = k
        group.append(seg)
    flush()
    return result
