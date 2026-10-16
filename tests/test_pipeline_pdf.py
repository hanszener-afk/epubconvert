"""Whole pipeline on the PDF corpus: convert, EPUBCheck, fidelity."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel2epub.epub.xhtml import plain_text
from novel2epub.ir import Italic, Paragraph, SceneBreak, SectionKind, Span
from novel2epub.pipeline import ConvertOptions, ExitCode, convert
from novel2epub.verify.epubcheck import find_epubcheck

PDFS = Path(__file__).parent / "fixtures" / "pdf"
LOCAL = Path(__file__).parent.parent / "local" / "manuscripts"
HAS_EPUBCHECK = find_epubcheck() is not None


def run(path: Path, tmp_path: Path, **kw: object):  # type: ignore[no-untyped-def]
    return convert(ConvertOptions(input=path, output=tmp_path / "out.epub", **kw))  # type: ignore[arg-type]


def _has_italic(nodes: list) -> bool:  # type: ignore[type-arg]
    return any(
        isinstance(n, Italic) or (isinstance(n, Span) and _has_italic(n.children)) for n in nodes
    )


def test_clean_pdf(tmp_path: Path) -> None:
    outcome = run(PDFS / "clean.pdf", tmp_path)
    assert outcome.exit_code in (ExitCode.OK, ExitCode.WARNINGS), outcome.summary()
    fid = outcome.fidelity_main
    assert fid.missing == [] and fid.added == [], outcome.summary()
    assert fid.reference_tokens > 7000
    book = outcome.book
    assert [s.kind for s in book.front] == [SectionKind.TITLEPAGE]
    assert [plain_text(s.title or []) for s in book.body] == [
        "Chapter One",
        "Chapter Two",
        "Chapter Three",
    ]
    methods = {d.method for d in outcome.diagnostics.decisions if d.kind == "chapter-detection"}
    assert methods == {"outline"}
    assert sum(isinstance(b, SceneBreak) for b in book.body[1].blocks) == 1
    paras = [b for s in book.body for b in s.blocks if isinstance(b, Paragraph)]
    assert any(_has_italic(p.content) for p in paras)
    # Paragraphs that cross a page boundary are joined: no paragraph starts in lower case.
    assert not [plain_text(p.content)[:30] for p in paras if plain_text(p.content)[:1].islower()]
    if HAS_EPUBCHECK:
        assert outcome.epubcheck.messages == []


def test_chapters_without_an_outline(tmp_path: Path) -> None:
    outcome = run(PDFS / "clean-nobookmarks.pdf", tmp_path, epubcheck=False)
    titles = [plain_text(s.title or []) for s in outcome.book.body]
    assert titles == ["Chapter One", "Chapter Two", "Chapter Three"]
    methods = {d.method for d in outcome.diagnostics.decisions if d.kind == "chapter-detection"}
    assert methods == {"pattern"}
    assert outcome.fidelity_main.missing == []


def test_scanned_pdf_is_rejected(tmp_path: Path) -> None:
    outcome = run(PDFS / "scanned.pdf", tmp_path)
    assert outcome.exit_code is ExitCode.INPUT_REJECTED
    assert any(i.code == "input-scanned" for i in outcome.diagnostics.errors)
    assert not (tmp_path / "out.epub").exists()


def test_not_a_pdf(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    assert run(bad, tmp_path).exit_code is ExitCode.INPUT_REJECTED


def test_pdf_output_is_deterministic(tmp_path: Path) -> None:
    a = convert(
        ConvertOptions(input=PDFS / "clean.pdf", output=tmp_path / "a.epub", epubcheck=False)
    )
    b = convert(
        ConvertOptions(input=PDFS / "clean.pdf", output=tmp_path / "b.epub", epubcheck=False)
    )
    assert a.exit_code == b.exit_code
    assert (tmp_path / "a.epub").read_bytes() == (tmp_path / "b.epub").read_bytes()


@pytest.mark.skipif(not (LOCAL / "catgirl.pdf").exists(), reason="local PDF not present")
def test_real_pdf_structure(tmp_path: Path) -> None:
    outcome = run(LOCAL / "catgirl.pdf", tmp_path, authors=["Unknown"], epubcheck=False)
    book = outcome.book
    kinds = [s.kind for s in book.body]
    assert kinds[0] is SectionKind.PROLOGUE
    assert kinds.count(SectionKind.PART) == 10
    assert all(s.depth == 1 for s in book.body if s.kind is SectionKind.CHAPTER)
