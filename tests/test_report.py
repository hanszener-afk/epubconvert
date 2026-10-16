"""Milestone 5: the conversion report (report.json and report.md)."""

from __future__ import annotations

import json
from pathlib import Path

import docx_fixtures as fx

from novel2epub.pipeline import ConvertOptions, convert
from novel2epub.report import build_report, render_markdown, table_cell


def _run(tmp_path: Path, name: str = "styled") -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = getattr(fx, name)().save(tmp_path / f"{name}.docx")
    convert(ConvertOptions(input=src, output=tmp_path / "out" / "book.epub", epubcheck=False))
    return json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))


def test_structure_carries_detection_method_and_confidence(tmp_path: Path) -> None:
    report = _run(tmp_path)
    chapters = [s for s in report["structure"] if s["kind"] == "chapter"]
    assert chapters
    for s in chapters:
        assert s["method"] == "style"
        assert 0 < s["confidence"] <= 1


def test_summary_counts(tmp_path: Path) -> None:
    report = _run(tmp_path)
    summary = report["summary"]
    assert summary["chapters"] == sum(s["kind"] == "chapter" for s in report["structure"])
    assert summary["words"] == report["fidelity"]["main"]["output_words"]
    assert summary["removed"] == sum(report["removed"]["counts"].values())
    assert summary["needs_review"] == len(report["needs_review"])


def test_decisions_are_summarised_by_kind(tmp_path: Path) -> None:
    report = _run(tmp_path)
    by_kind = {d["kind"]: d for d in report["decision_summary"]}
    total = sum(d["kind"] == "chapter-detection" for d in report["decisions"])
    assert by_kind["chapter-detection"]["count"] == total
    assert "style" in by_kind["chapter-detection"]["methods"]


def test_markdown_puts_review_first_and_lists_removed_text(tmp_path: Path) -> None:
    report = _run(tmp_path, "front_back")
    md = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert md.index("## Needs human review") < md.index("## Text fidelity")
    assert md.index("## Text fidelity") < md.index("## Structure")
    removed_texts = {r["text"] for r in report["removed"]["items"]}
    assert removed_texts
    listed = [t for t in removed_texts if f"`{table_cell(t)[:60]}" in md or t[:60] in md]
    assert listed, "removed text should be listed, not only counted"


def test_markdown_states_the_result_in_words(tmp_path: Path) -> None:
    _run(tmp_path)
    md = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "**Result:** Converted, with warnings" in md


def test_table_cells_are_escaped() -> None:
    assert table_cell("A | B\nC") == "A \\| B C"


def test_report_is_deterministic_and_has_no_absolute_paths(tmp_path: Path) -> None:
    first = _run(tmp_path / "a")
    second = _run(tmp_path / "b")
    assert first == second
    assert str(tmp_path) not in json.dumps(first)


def test_long_lists_are_capped_in_markdown_but_complete_in_json(tmp_path: Path) -> None:
    report = _run(tmp_path)
    report["needs_review"] = [
        {"kind": "dehyphenation", "description": f"item {n}", "confidence": 0.5, "source": None}
        for n in range(500)
    ]
    report["summary"]["needs_review"] = 500
    md = render_markdown(report)
    assert "item 0" in md and "item 499" not in md
    assert "report.json" in md


def test_build_report_without_book() -> None:
    from novel2epub.diagnostics import Diagnostics
    from novel2epub.pipeline import ExitCode, Outcome

    outcome = Outcome(
        ExitCode.INPUT_REJECTED,
        Diagnostics(),
        ConvertOptions(input=Path("x.docx"), output=Path("x.epub")),
        failures=["x.docx does not exist."],
    )
    report = build_report(outcome)
    assert report["summary"]["chapters"] == 0
    assert "**Result:** Not converted: the input was rejected" in render_markdown(report)


def test_removed_page_numbers_are_summarised_as_a_range() -> None:
    from novel2epub.report import _removed_md

    report = {
        "removed": {
            "counts": {"page-number": 3},
            "items": [{"kind": "page-number", "text": str(n), "source": None} for n in (7, 8, 9)],
        }
    }
    md = "\n".join(_removed_md(report))
    assert "3 numbers, from 7 to 9." in md
    assert '"8"' not in md
