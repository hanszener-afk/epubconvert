"""Convert straight quotes to typographic ones (only with --smarten).

Only quotation marks are changed. Dashes, ellipses, and everything else
stay exactly as authored.
"""

from __future__ import annotations

import re

_OPENERS = set("([{<\u2014\u2013-/\u201c\u2018")
# Words that start with an elided letter take an apostrophe, not an opening quote.
_ELISION = re.compile(
    r"(tis|twas|twere|twill|em|til|cause|cos|bout|round|n'|n\b|ere|neath|ow|appen|im|er)\b",
    re.IGNORECASE,
)


def _opening_context(prev: str) -> bool:
    return prev == "" or prev.isspace() or prev in _OPENERS


def smarten_text(text: str, before: str = "") -> str:
    out: list[str] = []
    prev = before[-1:] if before else ""
    for i, ch in enumerate(text):
        if ch == '"':
            ch = "\u201c" if _opening_context(prev) else "\u201d"
        elif ch == "'":
            rest = text[i + 1 :]
            if prev.isalnum():
                ch = "\u2019"
            elif _opening_context(prev):
                ch = "\u2019" if rest[:1].isdigit() or _ELISION.match(rest) else "\u2018"
            else:
                ch = "\u2019"
        out.append(ch)
        prev = ch
    return "".join(out)
