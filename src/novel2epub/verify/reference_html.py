"""Reference text for an HTML manuscript, read independently of the HTML front end.

Deliberately simple: every piece of text in the body, in document order, with
a space wherever a non-inline element starts or ends. It does not know about
footnotes, AO3, Google Docs, or CSS, so whatever the front end removes on
purpose must be logged as a removal to be accounted for.
"""

from __future__ import annotations

from pathlib import Path

import lxml.html
from lxml import etree

_IGNORED = {"script", "style", "noscript", "template", "head", "title", "meta", "link", "base"}
_INLINE = {
    "a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data", "del", "dfn", "em", "font",
    "i", "ins", "kbd", "label", "mark", "q", "s", "samp", "small", "span", "strike", "strong",
    "sub", "sup", "time", "tt", "u", "var", "wbr",
}  # fmt: skip


def html_reference_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return html_text(text)


def html_text(html: str) -> str:
    doc = lxml.html.document_fromstring(html)
    body = doc.find("body")
    out: list[str] = []
    _walk(body if body is not None else doc, out)
    return " ".join("".join(out).split())


def _walk(el: etree._Element, out: list[str]) -> None:
    tag = el.tag.lower() if isinstance(el.tag, str) else ""
    if tag in _IGNORED or not tag:
        return
    block = tag not in _INLINE
    if block:
        out.append(" ")
    if el.text:
        out.append(el.text)
    for child in el:
        _walk(child, out)
        if child.tail:
            out.append(child.tail)
    if block:
        out.append(" ")
