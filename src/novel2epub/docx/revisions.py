"""Detect tracked changes, and accept or reject them in place."""

from __future__ import annotations

from collections import Counter

__all__ = ["Counter"]

from lxml import etree

from novel2epub.docx.package import W

RUN_REVISIONS = ("ins", "del", "moveFrom", "moveTo")
PROPERTY_REVISIONS = (
    "rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange", "tcPrChange",
    "tblGridChange", "numberingChange", "cellIns", "cellDel", "cellMerge",
)  # fmt: skip
RANGE_MARKERS = ("moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd")
LABELS = {
    "ins": "insertion",
    "del": "deletion",
    "moveFrom": "move",
    "moveTo": "move",
}


def count(root: etree._Element) -> Counter[str]:
    found: Counter[str] = Counter()
    for name in RUN_REVISIONS:
        for _el in root.iter(f"{W}{name}"):
            found[LABELS[name]] += 1
    for name in PROPERTY_REVISIONS:
        found["formatting change"] += sum(1 for _ in root.iter(f"{W}{name}"))
    # A move is recorded twice (from and to); report it once.
    if found["move"]:
        found["move"] = (found["move"] + 1) // 2
    return +found


def _unwrap(el: etree._Element) -> None:
    parent = el.getparent()
    if parent is None:
        return
    index = parent.index(el)
    for child in list(el):
        parent.insert(index, child)
        index += 1
    parent.remove(el)


def _drop(el: etree._Element) -> None:
    parent = el.getparent()
    if parent is not None:
        parent.remove(el)


def _mark_revision(p: etree._Element, name: str) -> etree._Element | None:
    """The ins/del marker on a paragraph mark, if any."""
    return p.find(f"{W}pPr/{W}rPr/{W}{name}")


def _merge_with_next(p: etree._Element) -> None:
    """Join p with the following paragraph (its mark no longer exists)."""
    nxt = p.getnext()
    while nxt is not None and nxt.tag != f"{W}p":
        nxt = nxt.getnext()
    if nxt is None:
        return
    content = [c for c in p if c.tag != f"{W}pPr"]
    ppr = nxt.find(f"{W}pPr")
    index = 0 if ppr is None else nxt.index(ppr) + 1
    for child in content:
        nxt.insert(index, child)
        index += 1
    _drop(p)


def apply(root: etree._Element, mode: str) -> None:
    """mode is 'accept' or 'reject'. Mutates root."""
    keep, discard = (
        (("ins", "moveTo"), ("del", "moveFrom"))
        if mode == "accept"
        else (
            ("del", "moveFrom"),
            ("ins", "moveTo"),
        )
    )
    # Paragraph marks first, while the structure is intact.
    for p in list(root.iter(f"{W}p")):
        for name in discard:
            if _mark_revision(p, name) is not None:
                _merge_with_next(p)
                break
        else:
            for name in keep:
                marker = _mark_revision(p, name)
                if marker is not None:
                    _drop(marker)
    for name in discard:
        for el in list(root.iter(f"{W}{name}")):
            if el.getparent() is not None and el.getparent().tag == f"{W}rPr":  # type: ignore[union-attr]
                _drop(el)
                continue
            if el.getparent() is not None and el.getparent().tag == f"{W}trPr":  # type: ignore[union-attr]
                row = el.getparent().getparent()  # type: ignore[union-attr]
                if row is not None:
                    _drop(row)
                continue
            _drop(el)
    for name in keep:
        for el in list(root.iter(f"{W}{name}")):
            parent = el.getparent()
            if parent is not None and parent.tag in (f"{W}rPr", f"{W}trPr"):
                _drop(el)
            else:
                _unwrap(el)
    if mode == "reject":
        for el in list(root.iter(f"{W}delText")):
            el.tag = f"{W}t"
        for el in list(root.iter(f"{W}delInstrText")):
            el.tag = f"{W}instrText"
        for change in list(root.iter(f"{W}rPrChange")):
            rpr = change.getparent()
            old = change.find(f"{W}rPr")
            if rpr is None:
                continue
            for child in list(rpr):
                rpr.remove(child)
            if old is not None:
                for child in list(old):
                    rpr.append(child)
        for change in list(root.iter(f"{W}pPrChange")):
            ppr = change.getparent()
            old = change.find(f"{W}pPr")
            if ppr is None:
                continue
            for child in list(ppr):
                if child.tag not in (f"{W}rPr", f"{W}sectPr"):
                    ppr.remove(child)
            if old is not None:
                for i, child in enumerate(list(old)):
                    ppr.insert(i, child)
    for name in (*PROPERTY_REVISIONS, *RANGE_MARKERS):
        for el in list(root.iter(f"{W}{name}")):
            _drop(el)
