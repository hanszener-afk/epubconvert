"""The independent DOCX reference extractor shares no code with the reader."""

from __future__ import annotations

from pathlib import Path

import docx_fixtures as fx
from docxbuild import Docx, p, r

from novel2epub.verify.reference_docx import docx_reference_text


def test_reference_includes_everything_visible(tmp_path: Path) -> None:
    ref = docx_reference_text(fx.styled().save(tmp_path / "s.docx"), "fail")
    for fragment in (
        "The Styled Manuscript",
        "Chapter One",
        "entirely",
        "* * *",
        "Dear Sir, I write in haste.",
        "the notice",
        "shouting",
    ):
        assert fragment in ref.main


def test_reference_notes_are_separate(tmp_path: Path) -> None:
    ref = docx_reference_text(fx.footnotes().save(tmp_path / "f.docx"), "fail")
    assert "The harbour was rebuilt in 1850." in ref.notes
    assert "An endnote about sources." in ref.notes
    assert "rebuilt" not in ref.main


def test_reference_follows_revision_mode(tmp_path: Path) -> None:
    path = fx.tracked().save(tmp_path / "t.docx")
    accepted = docx_reference_text(path, "accept").main
    rejected = docx_reference_text(path, "reject").main
    assert "noisily" in accepted and "quietly" not in accepted
    assert "quietly" in rejected and "noisily" not in rejected
    assert accepted.count("moved words") == 1
    assert rejected.count("moved words") == 1
    assert "Is this right?" not in accepted  # comments are not body text


def test_reference_reads_one_alternate_content_branch(tmp_path: Path) -> None:
    alt = (
        '<w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><w:txbxContent>'
        + p(r("Boxed."))
        + "</w:txbxContent></w:drawing></mc:Choice><mc:Fallback><w:pict><w:txbxContent>"
        + p(r("Boxed."))
        + "</w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent></w:r>"
    )
    path = Docx(title="T").add(p(alt)).save(tmp_path / "a.docx")
    assert docx_reference_text(path, "fail").main.count("Boxed.") == 1


def test_reference_excludes_field_instructions(tmp_path: Path) -> None:
    ref = docx_reference_text(fx.front_back().save(tmp_path / "fb.docx"), "fail")
    assert "TOC" not in ref.main.split()
    assert "Prologue 1" in ref.main  # field results are text
