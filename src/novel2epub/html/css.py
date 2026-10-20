"""Just enough CSS to know which text is italic, bold, raised, or centred.

Google Docs exports carry no <i> or <b>: every run is a <span> whose class
names a rule in the page's <style> (.c3{font-style:italic}). Other sources
use inline style attributes. Only the properties that change what the text
means are read; layout properties are ignored.
"""

from __future__ import annotations

import re

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# property -> {value: (flag, on)}; on=False switches an inherited flag off.
_FLAGS: dict[str, dict[str, tuple[str, bool]]] = {
    "font-style": {"italic": ("italic", True), "oblique": ("italic", True),
                   "normal": ("italic", False)},
    "font-weight": {"bold": ("bold", True), "bolder": ("bold", True), "600": ("bold", True),
                    "700": ("bold", True), "800": ("bold", True), "900": ("bold", True),
                    "normal": ("bold", False), "400": ("bold", False), "300": ("bold", False),
                    "lighter": ("bold", False), "500": ("bold", False)},
    "font-variant": {"small-caps": ("smallcaps", True), "normal": ("smallcaps", False)},
    "text-transform": {"uppercase": ("allcaps", True), "none": ("allcaps", False)},
    "vertical-align": {"super": ("superscript", True), "sub": ("subscript", True),
                       "baseline": ("superscript", False)},
}  # fmt: skip


def declarations(text: str) -> dict[str, str]:
    """`prop: value; ...` as a dictionary, lower-cased, without !important."""
    out = {}
    for part in text.split(";"):
        if ":" in part:
            prop, value = part.split(":", 1)
            value = value.replace("!important", "").strip().lower()
            out[prop.strip().lower()] = value
    return out


class ClassStyles:
    """The declarations each class selector sets, from the page's <style> blocks."""

    def __init__(self, sheets: list[str]) -> None:
        self.by_class: dict[str, dict[str, str]] = {}
        for sheet in sheets:
            for selectors, body in _RULE.findall(_COMMENT.sub("", sheet)):
                decls = declarations(body)
                for selector in selectors.split(","):
                    name = _single_class(selector.strip())
                    if name:
                        self.by_class.setdefault(name, {}).update(decls)

    def for_element(self, classes: str | None, style: str | None) -> dict[str, str]:
        """Declarations for an element: its classes in order, then its style attribute."""
        out: dict[str, str] = {}
        for name in (classes or "").split():
            out.update(self.by_class.get(name, {}))
        if style:
            out.update(declarations(style))
        return out


def _single_class(selector: str) -> str | None:
    """`.c3` or `p.c3` -> "c3"; anything more complex is not a simple class rule."""
    match = re.fullmatch(r"[a-z0-9]*\.([A-Za-z_][\w-]*)", selector)
    return match.group(1) if match else None


def apply_flags(fmt: frozenset[str], decls: dict[str, str]) -> frozenset[str]:
    """`fmt` changed by the formatting `decls` set or unset."""
    flags = set(fmt)
    for prop, table in _FLAGS.items():
        value = decls.get(prop)
        if value is None:
            continue
        flag = table.get(value.split()[0])
        if flag is None:
            continue
        name, on = flag
        if on:
            flags.add(name)
        else:
            flags.discard(name)
            if name == "superscript":
                flags.discard("subscript")
    return frozenset(flags)


def alignment(decls: dict[str, str]) -> str | None:
    value = decls.get("text-align")
    return {"center": "center", "right": "right", "end": "right"}.get(value or "")


def page_break_before(decls: dict[str, str]) -> bool:
    return decls.get("page-break-before") == "always" or decls.get("break-before") == "page"


def hidden(decls: dict[str, str]) -> bool:
    return decls.get("display") == "none" or decls.get("visibility") == "hidden"
