"""Text-fidelity comparison: what went missing, and what was added."""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}
# Soft hyphen, zero-width space/joiners, word joiner, BOM.
INVISIBLE = "\u00ad\u200b\u200c\u200d\u2060\ufeff"
_TRANSLATE = str.maketrans({**LIGATURES, **dict.fromkeys(INVISIBLE, "")})
_WS = re.compile(r"\s+")


def normalise(text: str) -> str:
    """NFC, ligatures expanded, invisibles dropped, whitespace collapsed.

    Typographic characters (curly quotes, dashes, ellipses) are deliberately
    kept: changing them is a fidelity failure, not noise.
    """
    text = unicodedata.normalize("NFC", text).translate(_TRANSLATE)
    # Removing invisibles can expose a new composition, so normalise again.
    text = unicodedata.normalize("NFC", text)
    return _WS.sub(" ", text).strip()


_QUOTE_FOLD = str.maketrans(
    {"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2018": "'",
     "\u2019": "'", "\u201a": "'", "\u201b": "'"}
)  # fmt: skip


_INNER_HYPHEN = re.compile(r"(?<=\w)[-\u2010] ?(?=\w)")
_DASH = re.compile(r" ?([\u2014\u2013]) ?")
_QUOTE_SPACE_OPEN = re.compile(r"([\u201c\u2018]) ")
_QUOTE_SPACE_CLOSE = re.compile(r" ([\u201d\u2019])")


def tokens(text: str, fold_quotes: bool = False, pdf_mode: bool = False) -> list[str]:
    """Word tokens. pdf_mode makes hyphenation and line-end dash joins neutral:
    "mi-litia", "mi- litia", and "militia" compare equal, as do "word\u2014word"
    and "word\u2014 word". Dehyphenation choices are reported as decisions instead."""
    text = normalise(text)
    if fold_quotes:
        text = text.translate(_QUOTE_FOLD)
    if pdf_mode:
        text = _DASH.sub(r" \1 ", _INNER_HYPHEN.sub("", text)).strip()
        # PDF text extraction sometimes detaches a quotation mark from its word.
        text = _QUOTE_SPACE_OPEN.sub(r"\1", _QUOTE_SPACE_CLOSE.sub(r"\1", text))
    return text.split() if text else []


Removal = tuple[str, str] | tuple[str, str, str]  # (text, context[, replacement])


def subtract_removals(text: str, removals: Sequence[Removal]) -> tuple[str, list[str]]:
    """Cut logged removals out of the reference, in document order.

    Each removal is (text, context) or (text, context, replacement); a
    replacement covers deliberate rewrites such as joining a drop cap to its
    word. Each is searched for from the position of the previous one, preferring
    an occurrence whose preceding text matches the recorded context, then a
    whole-token occurrence, so that a removed page number "4" is not cut from
    "1840". Returns the remaining text and the removals that could not be found.
    """
    text = normalise(text)
    cursor = 0
    unmatched: list[str] = []
    for item in removals:
        raw, context = item[0], item[1]
        replacement = normalise(item[2]) if len(item) > 2 else ""  # type: ignore[misc]
        target = normalise(raw)
        if not target:
            continue
        idx = _locate(text, target, normalise(context), cursor)
        if idx < 0:
            idx = _locate(text, target, normalise(context), 0)
        if idx < 0:
            unmatched.append(raw)
            continue
        text = text[:idx] + replacement + text[idx + len(target) :]
        cursor = idx
    return normalise(text), unmatched


def _occurrences(text: str, target: str, start: int) -> list[int]:
    found = []
    idx = text.find(target, start)
    while idx >= 0:
        found.append(idx)
        idx = text.find(target, idx + 1)
    return found


def _locate(text: str, target: str, context: str, start: int) -> int:
    found = _occurrences(text, target, start)
    if not found:
        return -1
    if context:
        tail = context[-12:].rstrip()
        for idx in found:
            if text[:idx].rstrip().endswith(tail):
                return idx
    for idx in found:
        end = idx + len(target)
        if (idx == 0 or text[idx - 1] == " ") and (end == len(text) or text[end] == " "):
            return idx
    return found[0]


@dataclass(slots=True)
class Hunk:
    text: str
    position: int  # token index in the sequence the text belongs to
    before: str = ""  # a little context from the reference
    after: str = ""


@dataclass(slots=True)
class FidelityResult:
    reference_tokens: int
    output_tokens: int
    matched_tokens: int
    missing: list[Hunk] = field(default_factory=list)
    added: list[Hunk] = field(default_factory=list)

    unmatched_removals: list[str] = field(default_factory=list)

    @property
    def unexplained_missing(self) -> list[Hunk]:
        """Missing text not accounted for by a logged removal."""
        return self.missing

    @property
    def missing_tokens(self) -> int:
        return self.reference_tokens - self.matched_tokens

    @property
    def added_tokens(self) -> int:
        return self.output_tokens - self.matched_tokens

    @property
    def ratio(self) -> float:
        if self.reference_tokens == 0:
            return 1.0 if self.output_tokens == 0 else 0.0
        return self.matched_tokens / self.reference_tokens

    @property
    def loss(self) -> float:
        if not self.reference_tokens:
            return 0.0
        return self.missing_tokens / self.reference_tokens

    def passes(self, threshold: float) -> bool:
        return self.loss <= threshold


CONTEXT = 6


def compare(
    reference: str,
    output: str,
    removals: Sequence[Removal] | None = None,
    fold_quotes: bool = False,
    pdf_mode: bool = False,
) -> FidelityResult:
    """Compare reference and output text, after cutting logged removals from the reference."""
    unmatched: list[str] = []
    if removals:
        reference, unmatched = subtract_removals(reference, removals)
    ref = tokens(reference, fold_quotes, pdf_mode)
    out = tokens(output, fold_quotes, pdf_mode)
    matched = 0
    missing: list[Hunk] = []
    added: list[Hunk] = []
    for op, i1, i2, j1, j2 in _opcodes(ref, out):
        if op == "equal":
            matched += i2 - i1
            continue
        before = " ".join(ref[max(0, i1 - CONTEXT) : i1])
        after = " ".join(ref[i2 : i2 + CONTEXT])
        if i2 > i1:
            missing.append(Hunk(" ".join(ref[i1:i2]), i1, before, after))
        if j2 > j1:
            added.append(Hunk(" ".join(out[j1:j2]), j1, before, after))
    return FidelityResult(len(ref), len(out), matched, missing, added, unmatched)


def _opcodes(ref: list[str], out: list[str]) -> list[tuple[str, int, int, int, int]]:
    """difflib opcodes, anchored on unique tokens for long texts.

    SequenceMatcher without autojunk is slow on whole novels because common
    words occur thousands of times. Tokens that occur exactly once in both
    texts are matched first (as in patience diff), and difflib only runs on
    the short stretches between them.
    """
    # autojunk would treat frequent words as noise and produce misleading diffs.
    if len(ref) + len(out) < 20_000:
        matcher = difflib.SequenceMatcher(None, ref, out, autojunk=False)
        return [(op, i1, i2, j1, j2) for op, i1, i2, j1, j2 in matcher.get_opcodes()]
    # Anchor on tokens that are unique in both sequences (patience-style).
    from collections import Counter

    cr, co = Counter(ref), Counter(out)
    ref_unique = [(t, i) for i, t in enumerate(ref) if cr[t] == 1 and co[t] == 1]
    out_pos = {t: j for j, t in enumerate(out) if co[t] == 1 and cr[t] == 1}
    pairs = [(i, out_pos[t]) for t, i in ref_unique]
    anchors = _longest_increasing(pairs)
    result: list[tuple[str, int, int, int, int]] = []
    pi = pj = 0
    for ai, aj in [*anchors, (len(ref), len(out))]:
        if ai > pi or aj > pj:
            sub = difflib.SequenceMatcher(None, ref[pi:ai], out[pj:aj], autojunk=False)
            for op, i1, i2, j1, j2 in sub.get_opcodes():
                result.append((op, i1 + pi, i2 + pi, j1 + pj, j2 + pj))
        if ai < len(ref):
            result.append(("equal", ai, ai + 1, aj, aj + 1))
        pi, pj = ai + 1, aj + 1
    return _merge(result)


def _longest_increasing(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Longest subsequence of pairs increasing in the second element (pairs sorted by first)."""
    import bisect

    tails: list[int] = []
    tail_idx: list[int] = []
    prev: list[int] = [-1] * len(pairs)
    for k, (_, j) in enumerate(pairs):
        pos = bisect.bisect_left(tails, j)
        if pos == len(tails):
            tails.append(j)
            tail_idx.append(k)
        else:
            tails[pos] = j
            tail_idx[pos] = k
        prev[k] = tail_idx[pos - 1] if pos else -1
    seq: list[tuple[int, int]] = []
    k = tail_idx[-1] if tail_idx else -1
    while k != -1:
        seq.append(pairs[k])
        k = prev[k]
    return seq[::-1]


def _merge(ops: list[tuple[str, int, int, int, int]]) -> list[tuple[str, int, int, int, int]]:
    merged: list[tuple[str, int, int, int, int]] = []
    for op in ops:
        if merged and merged[-1][0] == op[0] == "equal":
            _, i1, _, j1, _ = merged[-1]
            merged[-1] = ("equal", i1, op[2], j1, op[4])
        else:
            merged.append(op)
    return merged
