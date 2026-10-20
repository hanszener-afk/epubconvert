"""Read a Markdown manuscript: CommonMark with footnotes, rendered and read as HTML.

markdown-it renders the text (raw HTML inside it is allowed, as Claude
sometimes writes <br>), each block carrying its Markdown line number so that
warnings point at the right line. The HTML front end then reads it, so
chapters, scene breaks (*** lines), and footnotes ([^1]) behave as for HTML.

A front matter block at the top (between --- lines) can set the title,
authors, and language:

    ---
    title: The Harbour
    author: Ann Author
    language: en-GB
    ---
"""

from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.html.reader import HtmlResult, read_html_text

_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def renderer() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": True}).use(footnote_plugin).use(front_matter_plugin)


def read_markdown(path: Path, diag: Diagnostics, chapter_regex: str | None = None) -> HtmlResult:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except OSError as exc:
        raise ConversionError("input-unreadable", f"Cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConversionError("input-encoding", f"{path.name} is not UTF-8 text.") from exc
    md = renderer()
    env: dict[str, object] = {}
    tokens = md.parse(text, env)
    meta: dict[str, object] = {}
    for token in tokens:
        if token.type == "front_matter":
            meta = front_matter(token.content)
        elif token.map and token.nesting >= 0 and token.type != "inline":
            token.attrSet("data-line", str(token.map[0] + 1))
    html = md.renderer.render(tokens, md.options, env)
    return read_html_text(html, path, diag, chapter_regex, meta)


def front_matter(text: str) -> dict[str, object]:
    """title, authors, and language from a simple front matter block."""
    meta: dict[str, object] = {}
    key = None
    for line in text.splitlines():
        item = re.match(r"^\s*-\s+(.*)$", line)
        if item and key in ("author", "authors"):
            authors = meta.setdefault("authors", [])
            assert isinstance(authors, list)
            authors.append(_unquote(item.group(1)))
            continue
        match = _KEY.match(line)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key == "title" and value:
            meta["title"] = _unquote(value)
        elif key in ("author", "authors") and value:
            if value.startswith("[") and value.endswith("]"):
                meta["authors"] = [_unquote(v) for v in value[1:-1].split(",") if v.strip()]
            else:
                meta["authors"] = [_unquote(value)]
        elif key in ("language", "lang") and value:
            meta["language"] = _unquote(value)
        elif key in ("description", "summary") and value:
            meta["description"] = _unquote(value)
    return meta


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value
