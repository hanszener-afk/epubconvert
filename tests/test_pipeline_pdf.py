"""Whole pipeline on the PDF corpus: convert, EPUBCheck, fidelity."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from novel2epub.epub.xhtml import plain_text
from novel2epub.ir import BlockQuote, Heading, Italic, Paragraph, SceneBreak, SectionKind, Span
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
    chapters = [s for s in book.body if s.kind is SectionKind.CHAPTER]
    assert len(chapters) >= 60  # every numbered chapter in every part, plus epilogue sections
    assert all(s.depth == 1 for s in chapters)
    assert plain_text(chapters[0].title or []) == "1. The Ditch"
    # The quest board printed above the first chapter's title opens that chapter.
    assert isinstance(chapters[0].opener[0], BlockQuote)
    part_one = next(s for s in book.body if s.kind is SectionKind.PART)
    assert part_one.blocks == []
    front_title = book.front[0].blocks[0]
    assert isinstance(front_title, Heading) and "Catgirl" in plain_text(front_title.content)
    assert kinds[-1] is SectionKind.CHAPTER and book.body[-1].depth == 1  # inside the epilogue


def _all_text(outcome) -> str:  # type: ignore[no-untyped-def]
    from novel2epub.verify.text import epub_text

    return epub_text(outcome.written).main


@pytest.mark.parametrize("name", ["headers", "hyphenation", "dropcaps", "footnotes", "printed-toc"])
def test_milestone_4_fixtures_convert_exactly(name: str, tmp_path: Path) -> None:
    outcome = run(PDFS / f"{name}.pdf", tmp_path)
    assert outcome.exit_code in (ExitCode.OK, ExitCode.WARNINGS), outcome.summary()
    assert outcome.fidelity_main.missing == [] and outcome.fidelity_main.added == [], (
        outcome.summary()
    )
    assert outcome.fidelity_notes.missing == [] and outcome.fidelity_notes.added == []
    assert outcome.fidelity_main.unmatched_removals == []
    if HAS_EPUBCHECK:
        assert outcome.epubcheck.messages == [], outcome.summary()


def test_running_heads_and_page_numbers_are_gone(tmp_path: Path) -> None:
    outcome = run(PDFS / "headers.pdf", tmp_path, epubcheck=False)
    kinds = Counter(r.kind for r in outcome.diagnostics.removals)
    assert kinds["page-number"] >= 15 and kinds["running-header"] >= 15
    heads = {r.text for r in outcome.diagnostics.removals if r.kind == "running-header"}
    assert "Persuasion" in heads and "Chapter One" in heads
    text = _all_text(outcome)
    assert "Persuasion Chapter" not in text  # a header glued into the text
    assert len(outcome.book.body) == 4


def test_hyphenation_is_resolved(tmp_path: Path) -> None:
    outcome = run(PDFS / "hyphenation.pdf", tmp_path, epubcheck=False)
    decisions = [d for d in outcome.diagnostics.decisions if d.kind == "dehyphenation"]
    assert len(decisions) >= 30
    text = _all_text(outcome)
    assert "him- self" not in text and "himself" in text
    review = [d for d in decisions if d.confidence < 0.75]
    assert len(review) <= 0.1 * len(decisions)


def test_drop_caps_rejoin_their_words(tmp_path: Path) -> None:
    outcome = run(PDFS / "dropcaps.pdf", tmp_path, epubcheck=False)
    starts = []
    for section in outcome.book.body:
        first = next(b for b in section.blocks if isinstance(b, Paragraph))
        starts.append(plain_text(first.content)[:12])
    assert starts == ["Sir Walter E", "Mr Shepherd,", "\u201cI must take"]


def test_footnotes_become_linked_notes(tmp_path: Path) -> None:
    outcome = run(PDFS / "footnotes.pdf", tmp_path, epubcheck=False)
    notes = outcome.book.notes
    assert len(notes) == 4
    texts = [plain_text(n.blocks[0].content) for n in notes.values()]  # type: ignore[union-attr]
    assert texts[0] == "Kellynch Hall is imaginary, though Somerset has many such houses."
    assert not any(i.code == "note-unreferenced" for i in outcome.diagnostics.warnings)
    assert "Kellynch Hall is imaginary" not in _all_text(outcome)


def test_printed_contents_drives_chapters(tmp_path: Path) -> None:
    outcome = run(PDFS / "printed-toc.pdf", tmp_path, epubcheck=False)
    titles = [plain_text(s.title or []) for s in outcome.book.body]
    assert titles == [
        "Sir Walter and the Baronetage",
        "The Lawyer from Crewkerne",
        "An Admiral for a Tenant",
    ]
    methods = {d.method for d in outcome.diagnostics.decisions if d.kind == "chapter-detection"}
    assert methods == {"printed-toc"}
    assert sum(r.kind == "manual-toc" for r in outcome.diagnostics.removals) == 4


@pytest.mark.skipif(not (LOCAL / "catgirl.pdf").exists(), reason="local PDF not present")
def test_real_pdf_converts_exactly(tmp_path: Path) -> None:
    outcome = run(LOCAL / "catgirl.pdf", tmp_path, authors=["Unknown"])
    assert outcome.exit_code in (ExitCode.OK, ExitCode.WARNINGS), outcome.summary()
    assert outcome.fidelity_main.missing == [] and outcome.fidelity_main.added == []
    text = _all_text(outcome)
    assert "Buying the Catgirl \u00b7" not in text
    breaks = sum(isinstance(b, SceneBreak) for s in outcome.book.body for b in s.blocks)
    assert breaks == 246
    assert len(outcome.diagnostics.needs_review()) <= 5
