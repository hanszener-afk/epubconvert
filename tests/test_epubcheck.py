from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from books import full_book, minimal_book, parts_book

from novel2epub.diagnostics import Diagnostics
from novel2epub.epub.writer import write_epub
from novel2epub.verify.epubcheck import find_epubcheck, run_epubcheck

TOOL = find_epubcheck()
needs_epubcheck = pytest.mark.skipif(TOOL is None, reason="EPUBCheck or Java not found")
pytestmark = pytest.mark.epubcheck


@needs_epubcheck
@pytest.mark.parametrize(
    "make", [minimal_book, full_book, parts_book], ids=["minimal", "full", "parts"]
)
def test_hand_built_books_pass_epubcheck_cleanly(make, tmp_path: Path) -> None:
    out = tmp_path / "book.epub"
    write_epub(make(), out, Diagnostics())
    assert TOOL is not None
    result = run_epubcheck(TOOL, out)
    assert result.ran
    assert result.messages == [], "\n".join(str(m) for m in result.messages)
    assert result.version.startswith("5.")


@needs_epubcheck
def test_broken_epub_is_reported(tmp_path: Path) -> None:
    good = tmp_path / "good.epub"
    write_epub(minimal_book(), good, Diagnostics())
    bad = tmp_path / "bad.epub"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.endswith(".opf"):
                data = data.replace(b"<dc:language>en-GB</dc:language>", b"")
            dst.writestr(info, data)
    assert TOOL is not None
    result = run_epubcheck(TOOL, bad)
    assert result.ran
    assert result.errors


def test_missing_tool_is_reported_not_crashed(tmp_path: Path) -> None:
    assert find_epubcheck(explicit=tmp_path / "nonexistent.jar") is None


def test_frozen_app_prefers_its_bundled_runtime_and_jar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from novel2epub.verify import epubcheck

    bundle = tmp_path / "_internal"
    java = bundle / "runtime" / "bin" / ("java.exe" if sys.platform == "win32" else "java")
    jar = bundle / "epubcheck" / "epubcheck.jar"
    for f in (java, jar):
        f.parent.mkdir(parents=True)
        f.write_bytes(b"")
    other = tmp_path / "jdk"
    (other / "bin").mkdir(parents=True)
    (other / "bin" / java.name).write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("JAVA_HOME", str(other))
    monkeypatch.delenv(epubcheck.ENV_VAR, raising=False)
    tool = epubcheck.find_epubcheck()
    assert tool is not None
    assert tool.java == java and tool.jar == jar
