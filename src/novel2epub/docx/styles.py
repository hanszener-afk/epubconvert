"""Resolve effective paragraph and run properties from styles.xml.

Order of application (ECMA-376 17.7.2):
  document defaults -> paragraph style chain -> character style chain -> direct.
Toggle properties (bold, italic, caps, ...) XOR between the paragraph-style
and character-style levels rather than overriding (17.7.3); within one
basedOn chain a derived style overrides its parent. Direct formatting is
absolute.
"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from novel2epub.docx.package import W

TOGGLES = ("b", "i", "caps", "smallCaps", "vanish", "webHidden")
_FALSE = {"0", "false", "off", "none"}


def on_off(el: etree._Element | None) -> bool | None:
    if el is None:
        return None
    return el.get(f"{W}val", "true").lower() not in _FALSE


@dataclass(frozen=True, slots=True)
class RunProps:
    bold: bool = False
    italic: bool = False
    caps: bool = False
    small_caps: bool = False
    hidden: bool = False
    vert: str | None = None  # "superscript" | "subscript"
    size: int | None = None  # half-points


@dataclass(frozen=True, slots=True)
class ParaProps:
    style_id: str | None
    style_names: tuple[str, ...]  # lower-case names along the basedOn chain, own first
    outline_level: int | None
    jc: str | None
    page_break_before: bool
    border_top: bool
    border_bottom: bool


def _run_values(rpr: etree._Element | None) -> dict[str, object]:
    out: dict[str, object] = {}
    if rpr is None:
        return out
    for name in TOGGLES:
        v = on_off(rpr.find(f"{W}{name}"))
        if v is not None:
            out[name] = v
    va = rpr.find(f"{W}vertAlign")
    if va is not None:
        val = va.get(f"{W}val", "baseline")
        out["vert"] = val if val in ("superscript", "subscript") else None
    sz = rpr.find(f"{W}sz")
    if sz is not None and (sz.get(f"{W}val") or "").isdigit():
        out["size"] = int(sz.get(f"{W}val", "0"))
    return out


class Styles:
    def __init__(self, root: etree._Element | None) -> None:
        self.styles: dict[str, etree._Element] = {}
        self.default_para: str | None = None
        self.defaults: dict[str, object] = {}
        self.language: str | None = None
        self._run_cache: dict[str | None, dict[str, object]] = {}
        self._para_cache: dict[str | None, tuple[dict[str, object], tuple[str, ...]]] = {}
        if root is None:
            return
        for st in root.iter(f"{W}style"):
            sid = st.get(f"{W}styleId")
            if not sid:
                continue
            self.styles[sid] = st
            is_default = st.get(f"{W}default") in ("1", "true", "on")
            if st.get(f"{W}type") == "paragraph" and is_default:
                self.default_para = sid
        rpr = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
        self.defaults = _run_values(rpr)
        if rpr is not None:
            lang = rpr.find(f"{W}lang")
            if lang is not None and lang.get(f"{W}val"):
                self.language = lang.get(f"{W}val")

    def _chain(self, sid: str | None) -> list[etree._Element]:
        chain: list[etree._Element] = []
        seen: set[str] = set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            st = self.styles[sid]
            chain.append(st)
            based = st.find(f"{W}basedOn")
            sid = based.get(f"{W}val") if based is not None else None
        return chain  # own style first

    def _style_run_values(self, sid: str | None) -> dict[str, object]:
        if sid in self._run_cache:
            return self._run_cache[sid]
        values: dict[str, object] = {}
        for st in reversed(self._chain(sid)):  # ancestors first, derived override
            values.update(_run_values(st.find(f"{W}rPr")))
        self._run_cache[sid] = values
        return values

    def para_style_id(self, ppr: etree._Element | None) -> str | None:
        if ppr is not None:
            ps = ppr.find(f"{W}pStyle")
            val = ps.get(f"{W}val") if ps is not None else None
            if isinstance(val, str) and val in self.styles:
                return val
        return self.default_para

    def run_props(self, para_style: str | None, rpr: etree._Element | None) -> RunProps:
        pvals = self._style_run_values(para_style)
        rstyle = None
        if rpr is not None:
            rs = rpr.find(f"{W}rStyle")
            if rs is not None:
                rstyle = rs.get(f"{W}val")
        rvals = self._style_run_values(rstyle) if rstyle else {}
        values = dict(self.defaults)
        for key in set(pvals) | set(rvals):
            if key in TOGGLES and key in pvals and key in rvals:
                values[key] = bool(pvals[key]) ^ bool(rvals[key])
            elif key in rvals:
                values[key] = rvals[key]
            else:
                values[key] = pvals[key]
        values.update(_run_values(rpr))
        return RunProps(
            bold=bool(values.get("b", False)),
            italic=bool(values.get("i", False)),
            caps=bool(values.get("caps", False)),
            small_caps=bool(values.get("smallCaps", False)),
            hidden=bool(values.get("vanish", False)) or bool(values.get("webHidden", False)),
            vert=values.get("vert"),  # type: ignore[arg-type]
            size=values.get("size"),  # type: ignore[arg-type]
        )

    def _style_para_values(self, sid: str | None) -> tuple[dict[str, object], tuple[str, ...]]:
        if sid in self._para_cache:
            return self._para_cache[sid]
        chain = self._chain(sid)
        values: dict[str, object] = {}
        for st in reversed(chain):
            values.update(_para_values(st.find(f"{W}pPr")))
        names = tuple(
            (st.find(f"{W}name").get(f"{W}val") or "").lower()  # type: ignore[union-attr]
            for st in chain
            if st.find(f"{W}name") is not None
        )
        self._para_cache[sid] = (values, names)
        return values, names

    def para_props(self, ppr: etree._Element | None) -> ParaProps:
        sid = self.para_style_id(ppr)
        style_values, names = self._style_para_values(sid)
        values = dict(style_values)
        values.update(_para_values(ppr))
        level = values.get("outline")
        if level is None:
            for name in names:
                if name.startswith("heading ") and name[8:].isdigit():
                    level = int(name[8:]) - 1
                    break
        return ParaProps(
            style_id=sid,
            style_names=names,
            outline_level=level if isinstance(level, int) and level < 9 else None,
            jc=values.get("jc"),  # type: ignore[arg-type]
            page_break_before=bool(values.get("pbb", False)),
            border_top=bool(values.get("btop", False)),
            border_bottom=bool(values.get("bbottom", False)),
        )


def _para_values(ppr: etree._Element | None) -> dict[str, object]:
    out: dict[str, object] = {}
    if ppr is None:
        return out
    ol = ppr.find(f"{W}outlineLvl")
    if ol is not None and (ol.get(f"{W}val") or "").isdigit():
        out["outline"] = int(ol.get(f"{W}val", "9"))
    jc = ppr.find(f"{W}jc")
    if jc is not None:
        out["jc"] = jc.get(f"{W}val")
    pbb = on_off(ppr.find(f"{W}pageBreakBefore"))
    if pbb is not None:
        out["pbb"] = pbb
    bdr = ppr.find(f"{W}pBdr")
    if bdr is not None:
        for side, key in (("top", "btop"), ("bottom", "bbottom")):
            el = bdr.find(f"{W}{side}")
            if el is not None:
                out[key] = el.get(f"{W}val", "none") not in ("none", "nil")
    return out
