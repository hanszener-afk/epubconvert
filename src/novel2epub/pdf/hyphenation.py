"""Decide whether a hyphen at the end of a line is a line break or part of a word.

Evidence, in order of trust:
  1. a soft hyphen is always a line break;
  2. a capital letter or digit after the break means a real compound ("anti-American");
  3. the document itself: which form ("militia" or "mi-litia") occurs elsewhere;
  4. a bundled English word list, when present;
  5. prefix rules: "self-", "well-", and a doubled vowel after "re-", "co-", "pre-";
  6. otherwise join, with low confidence so it is reported for review.
"""

from __future__ import annotations

import gzip
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from importlib import resources

HYPHENS = "-\u2010\u00ad"
KEEP_PREFIXES = {"self", "well", "ill", "half", "ex", "quasi", "pseudo", "non", "all"}
VOWEL_PREFIXES = {"re", "co", "pre", "de", "anti", "semi", "multi"}
_WORD = re.compile(r"^[\W_]*(.*?)[\W_]*$", re.UNICODE)


@dataclass(frozen=True, slots=True)
class HyphenDecision:
    join: bool
    confidence: float
    method: str
    text: str


def _word(token: str) -> str:
    m = _WORD.match(token)
    return (m.group(1) if m else token).lower()


def word_counts(lines: Iterable[str]) -> Counter[str]:
    """Words in the document, excluding fragments broken at a line end."""
    counts: Counter[str] = Counter()
    for text in lines:
        tokens = text.split()
        if tokens and tokens[-1][-1:] in HYPHENS:
            tokens = tokens[:-1]
        for tok in tokens:
            w = _word(tok)
            if w:
                counts[w] += 1
    return counts


class Dehyphenator:
    def __init__(self, counts: Counter[str], wordlist: frozenset[str] = frozenset()) -> None:
        self.counts = counts
        self.wordlist = wordlist

    def decide(self, left: str, right: str, hyphen: str) -> HyphenDecision:
        joined, kept = left + right, left + "-" + right

        def result(join: bool, confidence: float, method: str) -> HyphenDecision:
            return HyphenDecision(join, confidence, method, joined if join else kept)

        if hyphen == "\u00ad":
            return result(True, 1.0, "soft-hyphen")
        if right[:1].isupper() or any(c.isdigit() for c in left[-1:] + right[:1]):
            return result(False, 0.85, "capital-or-digit")
        lj, lk = joined.lower(), kept.lower()
        cj, ck = self.counts.get(lj, 0), self.counts.get(lk, 0)
        if cj != ck:
            return result(cj > ck, 0.95, "document")
        if self.wordlist:
            words = self.wordlist
            if lj in words:
                return result(True, 0.9, "wordlist")
            if lk in words or (
                len(left) > 1
                and len(right) > 1
                and left.lower() in words
                and right.lower() in words
            ):
                return result(False, 0.85, "wordlist")  # "drawing-room": two words, not one
        prefix = left.lower()
        if prefix in KEEP_PREFIXES:
            return result(False, 0.7, "prefix")
        if prefix in VOWEL_PREFIXES and right[:1].lower() == prefix[-1:]:
            return result(False, 0.7, "prefix")
        return result(True, 0.65 if not self.wordlist else 0.6, "default")


@cache
def english_words() -> frozenset[str]:
    """The bundled SCOWL-derived English word list (see data/SCOWL-COPYRIGHT.txt)."""
    data = (resources.files("novel2epub") / "data" / "words-en.txt.gz").read_bytes()
    return frozenset(gzip.decompress(data).decode("utf-8").split("\n"))
