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
    report = json.loads((tmp_path / "book.report.json").read_text(encoding="utf-8"))
    assert report["status"] == "warnings"
    assert (tmp_path / "book.report.md").is_file()


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
        report = json.loads((tmp_path / "out" / "b.report.json").read_text(encoding="utf-8"))
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


def test_output_may_be_a_directory(tmp_path: Path) -> None:
    src = fx.styled().save(tmp_path / "book.docx")
    out = tmp_path / "out"
    out.mkdir()
    assert main([str(src), "-o", str(out), "--no-epubcheck"]) in (0, 1)
    assert (out / "book.epub").is_file()
    assert (out / "book.report.json").is_file()


def test_internal_error_has_its_own_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    src = fx.styled().save(tmp_path / "book.docx")

    def boom(_opts: object) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr("novel2epub.cli.convert", boom)
    assert main([str(src), "--no-epubcheck"]) == 5
    err = capsys.readouterr().err
    assert "internal error" in err and "RuntimeError: unexpected" in err


def test_failure_is_not_printed_twice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    assert main([str(bad), "--no-epubcheck"]) == 3
    err = capsys.readouterr().err
    assert err.count("is not a DOCX file") == 1


def test_unencodable_console_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    src = fx.styled().save(tmp_path / "böök ≈.docx")  # name echoed in messages
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
    monkeypatch.setattr("sys.stderr", stream)
    assert main([str(src), "--no-epubcheck"]) in (0, 1)
