"""Open a DOCX package safely and locate its parts through relationships."""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lxml import etree

from novel2epub.diagnostics import ConversionError

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
CORE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
STRICT_W_NS = "http://purl.oclc.org/ooxml/wordprocessingml/main"

MAX_PART_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True)


@dataclass(frozen=True, slots=True)
class Rel:
    id: str
    type: str  # last path segment of the relationship type, e.g. "image"
    target: str  # absolute part name for internal targets, URL for external ones
    external: bool


def parse_xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, _PARSER)


class DocxPackage:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.zip = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ConversionError(
                "input-invalid", f"{path.name} is not a DOCX file: {exc}"
            ) from exc
        total = 0
        for info in self.zip.infolist():
            if info.file_size > MAX_PART_BYTES:
                raise ConversionError(
                    "input-invalid", f"Part {info.filename} is implausibly large."
                )
            total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise ConversionError("input-invalid", "The DOCX is implausibly large when unpacked.")
        if "encryption" in {n.lower() for n in self.zip.namelist()}:
            raise ConversionError("input-encrypted", "The document is password protected.")
        package_rels = self.rels("")
        main = next((r for r in package_rels.values() if r.type == "officeDocument"), None)
        if main is None or not self.has(main.target):
            raise ConversionError("input-invalid", f"{path.name} has no main document part.")
        self.main = main.target
        self.main_rels = self.rels(self.main)
        self.core_part = next(
            (r.target for r in package_rels.values() if r.type == "core-properties"), None
        )
        data = self.read(self.main)
        if STRICT_W_NS.encode() in data[:2000]:
            raise ConversionError(
                "input-unsupported",
                "This DOCX uses the 'Strict Open XML' format. Save it from Word as a standard "
                "'Word Document (.docx)' and try again.",
            )

    def has(self, part: str) -> bool:
        return part in self.zip.namelist()

    def read(self, part: str) -> bytes:
        try:
            return self.zip.read(part)
        except KeyError as exc:
            raise ConversionError("input-invalid", f"Missing part {part}.") from exc

    def xml(self, part: str) -> etree._Element:
        try:
            return parse_xml(self.read(part))
        except etree.XMLSyntaxError as exc:
            raise ConversionError("input-invalid", f"{part} is not well-formed XML: {exc}") from exc

    def rels(self, part: str) -> dict[str, Rel]:
        base, name = posixpath.split(part)
        rels_part = posixpath.join(base, "_rels", f"{name}.rels")
        if not self.has(rels_part):
            return {}
        root = parse_xml(self.read(rels_part))
        out: dict[str, Rel] = {}
        for el in root.iter(f"{{{REL_NS}}}Relationship"):
            external = el.get("TargetMode") == "External"
            target = el.get("Target", "")
            if not external:
                target = posixpath.normpath(posixpath.join(base, target)).lstrip("/")
                if target.startswith("/"):
                    target = target[1:]
            out[el.get("Id", "")] = Rel(
                el.get("Id", ""), el.get("Type", "").rsplit("/", 1)[-1], target, external
            )
        return out

    def related_part(self, rel_type: str) -> str | None:
        rel = next((r for r in self.main_rels.values() if r.type == rel_type), None)
        return rel.target if rel and not rel.external and self.has(rel.target) else None

    def core(self) -> tuple[str, str, datetime | None]:
        """(title, creator, modified) from docProps/core.xml."""
        if not self.core_part or not self.has(self.core_part):
            return "", "", None
        root = parse_xml(self.read(self.core_part))
        dc = "{http://purl.org/dc/elements/1.1/}"
        dcterms = "{http://purl.org/dc/terms/}"
        title = (root.findtext(f"{dc}title") or "").strip()
        creator = (root.findtext(f"{dc}creator") or "").strip()
        raw = (root.findtext(f"{dcterms}modified") or "").strip()
        modified = None
        if raw:
            try:
                modified = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                modified = None
        return title, creator, modified
