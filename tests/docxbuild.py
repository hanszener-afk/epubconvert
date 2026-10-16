"""Build small DOCX packages from raw WordprocessingML for tests.

Raw XML gives exact control over the constructs the reader must handle
(revisions, fields, VML rules, borders, notes), which python-docx cannot
produce. Output is deterministic.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
    'mc:Ignorable="w14"'
)

REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
REL_LINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"


# --------------------------------------------------------------------------
# Run and paragraph helpers


def r(
    text: str = "",
    *,
    i: bool | None = None,
    b: bool | None = None,
    sc: bool = False,
    caps: bool = False,
    sup: bool = False,
    sub: bool = False,
    vanish: bool = False,
    sz: int | None = None,
    style: str | None = None,
    extra: str = "",
) -> str:
    props = ""
    if style:
        props += f'<w:rStyle w:val="{style}"/>'
    if b is not None:
        props += "<w:b/>" if b else '<w:b w:val="0"/>'
    if i is not None:
        props += "<w:i/>" if i else '<w:i w:val="false"/>'
    if caps:
        props += "<w:caps/>"
    if sc:
        props += "<w:smallCaps/>"
    if vanish:
        props += "<w:vanish/>"
    if sz:
        props += f'<w:sz w:val="{sz}"/>'
    if sup:
        props += '<w:vertAlign w:val="superscript"/>'
    if sub:
        props += '<w:vertAlign w:val="subscript"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    t = f'<w:t xml:space="preserve">{escape(text)}</w:t>' if text else ""
    return f"<w:r>{rpr}{extra}{t}</w:r>"


def p(
    *content: str,
    style: str | None = None,
    jc: str | None = None,
    border: str | None = None,
    page_break_before: bool = False,
    extra_ppr: str = "",
) -> str:
    props = ""
    if style:
        props += f'<w:pStyle w:val="{style}"/>'
    if page_break_before:
        props += "<w:pageBreakBefore/>"
    if border:
        props += (
            f'<w:pBdr><w:{border} w:val="single" w:sz="4" w:space="1" w:color="auto"/></w:pBdr>'
        )
    if jc:
        props += f'<w:jc w:val="{jc}"/>'
    props += extra_ppr
    ppr = f"<w:pPr>{props}</w:pPr>" if props else ""
    body = "".join(c if c.startswith("<") else r(c) for c in content)
    return f"<w:p>{ppr}{body}</w:p>"


def text_p(text: str, **kw: object) -> str:
    return p(r(text), **kw)  # type: ignore[arg-type]


PAGE_BREAK = '<w:r><w:br w:type="page"/></w:r>'
SOFT_BREAK = "<w:r><w:br/></w:r>"
TAB = "<w:r><w:tab/></w:r>"
HR = (
    '<w:r><w:pict><v:rect style="width:468pt;height:1pt" o:hralign="center" '
    'o:hrstd="t" o:hr="t" fillcolor="black" stroked="f"/></w:pict></w:r>'
)


def footnote_ref(n: int) -> str:
    return f'<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="{n}"/></w:r>'


def endnote_ref(n: int) -> str:
    return f'<w:r><w:rPr><w:rStyle w:val="EndnoteReference"/></w:rPr><w:endnoteReference w:id="{n}"/></w:r>'


def fld(instr: str, result: str) -> str:
    return (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve"> {escape(instr)} </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        f"{r(result)}"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )


def ins(content: str, n: int = 1) -> str:
    return f'<w:ins w:id="{n}" w:author="Ed" w:date="2026-01-01T00:00:00Z">{content}</w:ins>'


def dele(text: str, n: int = 2) -> str:
    return (
        f'<w:del w:id="{n}" w:author="Ed" w:date="2026-01-01T00:00:00Z">'
        f'<w:r><w:delText xml:space="preserve">{escape(text)}</w:delText></w:r></w:del>'
    )


def image_run(rid: str, cx_emu: int, cy_emu: int, descr: str | None, n: int = 1) -> str:
    d = f' descr="{escape(descr)}"' if descr is not None else ""
    return (
        "<w:r><w:drawing><wp:inline>"
        f'<wp:extent cx="{cx_emu}" cy="{cy_emu}"/>'
        f'<wp:docPr id="{n}" name="Picture {n}"{d}/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{n}" name="img{n}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx_emu}" cy="{cy_emu}"/></a:xfrm></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


# --------------------------------------------------------------------------
# Styles

BASE_STYLES = """
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="56"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:rPr><w:i/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="BlockText"><w:name w:val="Block Text"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="ChapterTitle"><w:name w:val="Chapter Title"/><w:basedOn w:val="Heading1"/><w:pPr><w:jc w:val="center"/></w:pPr></w:style>
<w:style w:type="character" w:styleId="Emphasis"><w:name w:val="Emphasis"/><w:rPr><w:i/></w:rPr></w:style>
<w:style w:type="character" w:styleId="FootnoteReference"><w:name w:val="footnote reference"/><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>
<w:style w:type="character" w:styleId="EndnoteReference"><w:name w:val="endnote reference"/><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>
"""


def styles_xml(extra: str = "", lang: str = "en-GB", default_rpr: str = "") -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles {NS}>'
        f'<w:docDefaults><w:rPrDefault><w:rPr><w:lang w:val="{lang}"/><w:sz w:val="24"/>'
        f"{default_rpr}</w:rPr></w:rPrDefault><w:pPrDefault/></w:docDefaults>"
        f"{BASE_STYLES}{extra}</w:styles>"
    )


# --------------------------------------------------------------------------
# Package


@dataclass
class Docx:
    body: list[str] = field(default_factory=list)
    styles: str = field(default_factory=styles_xml)
    footnotes: dict[int, str] = field(default_factory=dict)  # id -> paragraphs xml
    endnotes: dict[int, str] = field(default_factory=dict)
    comments: dict[int, str] = field(default_factory=dict)  # id -> comment text
    media: dict[str, tuple[str, bytes]] = field(default_factory=dict)  # rId -> (name, data)
    links: dict[str, str] = field(default_factory=dict)  # rId -> url
    title: str = ""
    creator: str = "Ann Author"
    modified: str = "2026-02-03T04:05:06Z"
    sect_pr: str = '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
    # Header and footer content by type ("default", "even", "first"): paragraph XML.
    headers: dict[str, str] = field(default_factory=dict)
    footers: dict[str, str] = field(default_factory=dict)
    settings: str = ""  # inner XML of w:settings, e.g. "<w:evenAndOddHeaders/>"

    def add(self, *paragraphs: str) -> Docx:
        self.body.extend(paragraphs)
        return self

    def build(self) -> bytes:
        files: dict[str, str | bytes] = {}
        overrides = [
            ("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
            ("/word/styles.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"),
            ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
        ]  # fmt: skip
        rels = [
            ("rIdStyles", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles", "styles.xml", False),
        ]  # fmt: skip
        refs = ""
        for kind, parts in (("header", self.headers), ("footer", self.footers)):
            tag = "hdr" if kind == "header" else "ftr"
            for n, (typ, xml) in enumerate(sorted(parts.items()), start=1):
                name = f"{kind}{n}.xml"
                files[f"word/{name}"] = (
                    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f"<w:{tag} {NS}>{xml}</w:{tag}>"
                )
                rid = f"rId{kind}{n}"
                rels.append(
                    (
                        rid,
                        f"http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}",
                        name,
                        False,
                    )
                )
                overrides.append(
                    (
                        f"/word/{name}",
                        f"application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml",
                    )
                )
                refs += f'<w:{kind}Reference w:type="{typ}" r:id="{rid}"/>'
        if self.settings:
            files["word/settings.xml"] = (
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:settings {NS}>'
                f"{self.settings}</w:settings>"
            )
            rels.append(
                (
                    "rIdSettings",
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings",
                    "settings.xml",
                    False,
                )
            )
            overrides.append(
                (
                    "/word/settings.xml",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
                )
            )
        sect_pr = self.sect_pr.replace("<w:sectPr>", "<w:sectPr>" + refs, 1)
        files["word/document.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document {NS}><w:body>'
            + "".join(self.body)
            + sect_pr
            + "</w:body></w:document>"
        )
        files["word/styles.xml"] = self.styles
        for kind, notes in (("footnote", self.footnotes), ("endnote", self.endnotes)):
            if not notes:
                continue
            parts = [
                f'<w:{kind} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{kind}>',
                f'<w:{kind} w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:{kind}>',
            ]
            for nid, xml in notes.items():
                parts.append(f'<w:{kind} w:id="{nid}">{xml}</w:{kind}>')
            files[f"word/{kind}s.xml"] = (
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:{kind}s {NS}>'
                + "".join(parts)
                + f"</w:{kind}s>"
            )
            overrides.append(
                (f"/word/{kind}s.xml", f"application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}s+xml")
            )  # fmt: skip
            rels.append(
                (f"rId{kind}", f"http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}s", f"{kind}s.xml", False)
            )  # fmt: skip
        if self.comments:
            body = "".join(
                f'<w:comment w:id="{cid}" w:author="Ed"><w:p>{r(text)}</w:p></w:comment>'
                for cid, text in self.comments.items()
            )
            files["word/comments.xml"] = (
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:comments {NS}>{body}</w:comments>'
            )
            overrides.append(
                ("/word/comments.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml")
            )  # fmt: skip
            rels.append(
                ("rIdComments", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments", "comments.xml", False)
            )  # fmt: skip
        for rid, (name, data) in self.media.items():
            files[f"word/media/{name}"] = data
            rels.append((rid, REL_IMAGE, f"media/{name}", False))
        for rid, url in self.links.items():
            rels.append((rid, REL_LINK, url, True))
        files["word/_rels/document.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="{rid}" Type="{typ}" Target="{escape(target)}"'
                + (' TargetMode="External"' if external else "")
                + "/>"
                for rid, typ, target, external in rels
            )
            + "</Relationships>"
        )
        files["docProps/core.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape(self.title)}</dc:title><dc:creator>{escape(self.creator)}</dc:creator>"
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{self.modified}</dcterms:modified>'
            "</cp:coreProperties>"
        )
        files["_rels/.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            "</Relationships>"
        )
        defaults = (
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Default Extension="jpeg" ContentType="image/jpeg"/>'
            '<Default Extension="bmp" ContentType="image/bmp"/>'
            '<Default Extension="tiff" ContentType="image/tiff"/>'
        )
        files["[Content_Types].xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            + defaults
            + "".join(f'<Override PartName="{n}" ContentType="{t}"/>' for n, t in overrides)
            + "</Types>"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                data = files[name]
                z.writestr(info, data.encode() if isinstance(data, str) else data)
        return buf.getvalue()

    def save(self, path: Path) -> Path:
        path.write_bytes(self.build())
        return path
