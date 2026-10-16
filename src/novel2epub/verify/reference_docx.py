"""Reference text from a DOCX, extracted independently of the front end.

Deliberately simple: it reads every text node in the body and notes, in
document order, honouring only the revision mode. It shares no code with
novel2epub.docx, so a front-end bug cannot hide itself by corrupting both
sides of the fidelity comparison.
"""

from __future__ import annotations

import contextlib
import posixpath
import zipfile
from pathlib import Path

from lxml import etree

from novel2epub.verify.fidelity import normalise
from novel2epub.verify.text import ExtractedText

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True)


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    base, name = posixpath.split(part)
    rels_name = posixpath.join(base, "_rels", f"{name}.rels")
    if rels_name not in z.namelist():
        return {}
    root = etree.fromstring(z.read(rels_name), _PARSER)
    out = {}
    for rel in root.iter(_REL):
        if rel.get("TargetMode") == "External":
            continue
        kind = (rel.get("Type") or "").rsplit("/", 1)[-1]
        out[kind] = posixpath.normpath(posixpath.join(base, rel.get("Target") or "")).lstrip("/")
    return out


def _text(root: etree._Element, mode: str) -> str:
    skip = {f"{_W}del", f"{_W}moveFrom"} if mode != "reject" else {f"{_W}ins", f"{_W}moveTo"}
    out: list[str] = []

    def walk(el: etree._Element) -> None:
        for child in el:
            tag = child.tag
            if not isinstance(tag, str) or tag in skip or tag == _MC_FALLBACK:
                continue
            if tag == f"{_W}t" or (mode == "reject" and tag == f"{_W}delText"):
                out.append(child.text or "")
            elif tag in (f"{_W}tab", f"{_W}ptab", f"{_W}br", f"{_W}cr"):
                out.append(" ")
            elif tag == f"{_W}noBreakHyphen":
                out.append("\u2011")
            elif tag == f"{_W}sym":
                with contextlib.suppress(ValueError):
                    out.append(chr(int(child.get(f"{_W}char", ""), 16)))
            else:
                walk(child)
            if tag == f"{_W}p":
                out.append(" ")

    walk(root)
    return "".join(out)


def docx_reference_text(path: Path, revision_mode: str) -> ExtractedText:
    with zipfile.ZipFile(path) as z:
        package = _rels(z, "")
        main = package.get("officeDocument", "word/document.xml")
        rels = _rels(z, main)
        main_text = _text(etree.fromstring(z.read(main), _PARSER), revision_mode)
        notes: list[str] = []
        for kind, tag in (("footnotes", "footnote"), ("endnotes", "endnote")):
            part = rels.get(kind)
            if not part or part not in z.namelist():
                continue
            root = etree.fromstring(z.read(part), _PARSER)
            for note in root.iter(f"{_W}{tag}"):
                if note.get(f"{_W}type") in (None, "normal"):
                    notes.append(_text(note, revision_mode))
    return ExtractedText(normalise(main_text), normalise(" ".join(notes)))
