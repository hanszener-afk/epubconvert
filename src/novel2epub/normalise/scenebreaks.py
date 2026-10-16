"""Recognise typed scene-break markers such as "***", "* * *", and "#"."""

from __future__ import annotations

ORNAMENTS = set(
    "*#~\u2022\u00b7\u2219\u25e6\u25cb\u25cf\u25c6\u25c7\u2756\u2726\u2727\u2605\u2606\u2042\u2767\u2619\u273b\u273c\u2766\u00a7\u2020\u2021\u25aa\u25ab\u2058\u2059"
)
LINES = set("-_=\u2013\u2014\u2015\u2500")
ELLIPSIS = set(".\u2026")


def scene_break_confidence(text: str, centred: bool) -> float:
    """Confidence that a paragraph consisting of `text` is a scene-break marker.

    Ellipses alone are not breaks: a bare "..." is usually a silent beat.
    Line characters need at least three to count, so a lone dash is text.
    """
    core = "".join(text.split())
    if not core or len(core) > 60:
        return 0.0
    chars = set(core)
    if not chars <= ORNAMENTS | LINES | ELLIPSIS:
        return 0.0
    if chars <= ELLIPSIS:
        return 0.0
    if chars <= LINES | ELLIPSIS and len(core) < 3:
        return 0.0
    confidence = 0.9 if chars & {"*", "#"} else 0.85
    if centred:
        confidence += 0.05
    return min(confidence, 0.97)
