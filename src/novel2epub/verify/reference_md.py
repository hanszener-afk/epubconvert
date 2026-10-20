"""Reference text for a Markdown manuscript, read without a Markdown parser.

The raw text with Markdown's markup stripped by simple line and pattern
rules, independently of markdown-it: heading hashes, quote and list
markers, emphasis, link and image syntax, code ticks, escapes, and front
matter. Footnote definitions are moved to the end, where the EPUB's notes
are compared. Rules are deliberately plain; a construct they miss shows as a
difference in the report rather than passing unnoticed.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_THEMATIC = re.compile(r"^\s{0,3}([*_-])(\s*\1){2,}\s*$")
_SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_ATX = re.compile(r"^\s{0,3}#{1,6}(?=\s|$)")
_ATX_CLOSE = re.compile(r"\s+#+\s*$")
_QUOTE = re.compile(r"^\s{0,3}(>\s?)+")
_LIST = re.compile(r"^\s{0,3}([*+-]|\d{1,9}[.)])\s+")
_FOOTNOTE_DEF = re.compile(r"^\s{0,3}\[\^[^\]]+\]:\s?(.*)$")
_LINK_DEF = re.compile(r"^\s{0,3}\[[^\]^][^\]]*\]:\s*\S+")
_ESCAPABLE = r"""!"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~"""


def markdown_reference_text(path: Path) -> str:
    return markdown_text(path.read_bytes().decode("utf-8-sig"))


def markdown_text(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")), None)
        if end is not None:
            lines = lines[end + 1 :]
    main: list[str] = []
    notes: list[str] = []
    fence: str | None = None
    in_note = False
    previous = ""
    for line in lines:
        if fence is not None:
            if line.strip().startswith(fence):
                fence = None
            else:
                main.append(line)
            continue
        opening = _FENCE.match(line)
        if opening:
            fence = opening.group(1)[0] * 3
            continue
        definition = _FOOTNOTE_DEF.match(line)
        if definition:
            in_note = True
            notes.append(_hard_break(definition.group(1)))
            continue
        if in_note:
            if line.startswith(("    ", "\t")) or not line.strip():
                notes.append(_hard_break(line.strip()))
                continue
            in_note = False
        if _THEMATIC.match(line) or (_SETEXT.match(line) and previous.strip()):
            main.append("")
            previous = ""
            continue
        if _LINK_DEF.match(line):
            continue
        heading = _ATX.match(line)
        line = _ATX.sub("", line)
        if heading:
            line = _ATX_CLOSE.sub("", line)
        line = _LIST.sub("", _QUOTE.sub("", line))
        main.append(_hard_break(line))
        previous = line
    return _inline(" ".join(main) + " " + " ".join(notes))


def _hard_break(line: str) -> str:
    stripped = line.rstrip()
    if stripped.endswith("\\") and not stripped.endswith("\\\\"):
        return stripped[:-1]
    return line


def _inline(text: str) -> str:
    # Escaped characters are literal: protect them from the markup rules below.
    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        protected.append(match.group(1))
        return f"\x00{len(protected) - 1}\x00"

    text = re.sub(rf"\\([{_ESCAPABLE}])", protect, text)
    text = re.sub(r"`+([^`]*?)`+", lambda m: m.group(1).replace("*", "\x01"), text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"!\[[^\]]*\]\[[^\]]*\]", "", text)
    text = re.sub(r"\[\^[^\]]+\]", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", text)
    text = re.sub(r"<((?:https?|mailto):[^>\s]+)>", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("*", "")
    text = re.sub(r"(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])", "", text)
    text = text.replace("\x01", "*")
    text = re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], text)
    text = html.unescape(text)
    return " ".join(text.split())
