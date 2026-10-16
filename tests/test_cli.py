from __future__ import annotations

import json
from pathlib import Path

import docx_fixtures as fx
import pytest

from novel2epub.cli import main


def test_converts_with_warnings_exit_code(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    code = main([str(src), "--no-epubcheck"])
    assert code == 1  # warnings: derived identifier, EPUBCheck skipped
    assert (tmp_path / "book.epub").is_file()
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "warnings"
    assert (tmp_path / "report.md").is_file()


def test_clean_run_exits_zero(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    meta = tmp_path / "meta.toml"
    meta.write_text(
        'title = "The Styled Manuscript"\nauthors = ["Ann Author"]\n'
        'identifier = "urn:uuid:5f0e3c1a-8d2b-4f5e-9a7c-1b2d3e4f5a6b"\n',
        encoding="utf-8",
    )
    code = main([str(src), "--meta", str(meta), "-o", str(tmp_path / "out" / "b.epub")])
    if code == 1:
        report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
        codes = {i["code"] for i in report["issues"] if i["severity"] == "warning"}
        assert codes <= {"epubcheck-missing"}, codes
    else:
        assert code == 0
    assert (tmp_path / "out" / "b.epub").is_file()


def test_tracked_changes_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = fx.tracked().save(tmp_path / "t.docx")
    assert main([str(src), "--no-epubcheck"]) == 3
    assert "--accept-changes" in capsys.readouterr().err
    assert main([str(src), "--accept-changes", "--no-epubcheck"]) in (0, 1)


def test_usage_errors(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    for argv in (
        [str(src), "--ocr"],
        [str(src), "--chapter-regex", "("],
        [str(src), "--accept-changes", "--reject-changes"],
        [str(src), "-o", str(src)],
    ):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2, argv


def test_bad_meta_key_rejected(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    meta = tmp_path / "meta.toml"
    meta.write_text('titel = "Typo"\n', encoding="utf-8")
    assert main([str(src), "--meta", str(meta), "--no-epubcheck"]) == 3
