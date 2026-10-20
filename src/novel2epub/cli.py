"""Command-line interface. No network access, no telemetry."""

from __future__ import annotations

import argparse
import contextlib
import sys
import traceback
from pathlib import Path

from novel2epub import __version__
from novel2epub.pipeline import ConvertOptions, ExitCode, Outcome, convert


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="novel2epub",
        description="Convert a DOCX, PDF, Markdown, or HTML novel manuscript into an EPUB 3 file, "
        "and check that no text was lost.",
        epilog="Exit codes: 0 success, 1 success with warnings, 2 usage error, "
        "3 input rejected, 4 verification failed (fidelity or EPUBCheck), "
        "5 internal error.",
    )
    p.add_argument("input", type=Path, help="the manuscript (.docx, .pdf, .md, or .html)")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        help="the EPUB to write, or a folder to write it in (default: next to the input)",
    )
    p.add_argument("--meta", type=Path, help="metadata file (TOML)")
    p.add_argument("--cover", type=Path, help="cover image (JPEG or PNG)")
    changes = p.add_mutually_exclusive_group()
    changes.add_argument("--accept-changes", action="store_true", help="accept all tracked changes")
    changes.add_argument("--reject-changes", action="store_true", help="reject all tracked changes")
    p.add_argument(
        "--ocr", action="store_true", help="OCR scanned PDFs (not available in this version)"
    )
    p.add_argument("--smarten", action="store_true", help="convert straight quotes to curly quotes")
    p.add_argument(
        "--chapter-regex", metavar="PATTERN", help="regular expression for chapter headings"
    )
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.add_argument(
        "--report-dir",
        type=Path,
        help="where to write NAME.report.json and NAME.report.md (default: next to the EPUB)",
    )
    p.add_argument("--title", help="book title (overrides the document and metadata file)")
    p.add_argument("--author", action="append", dest="authors", help="author (repeat for several)")
    p.add_argument("--language", help="BCP 47 language tag, for example en-GB")
    p.add_argument(
        "--threshold",
        type=float,
        default=0.1,
        metavar="PERCENT",
        help="maximum text loss before the run fails, in per cent (default 0.1)",
    )
    p.add_argument(
        "--literal-tags",
        action="store_true",
        help="keep typed HTML tags such as <i> as text instead of formatting",
    )
    p.add_argument("--epubcheck", type=Path, metavar="JAR", help="path to epubcheck.jar")
    p.add_argument("--no-epubcheck", action="store_true", help="skip EPUBCheck validation")
    p.add_argument("--version", action="version", version=f"novel2epub {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    _tolerant_streams()
    try:
        return _main(argv)
    finally:
        _pause_if_own_console()


def _main(argv: list[str] | None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.ocr:
        parser.exit(
            ExitCode.USAGE,
            "novel2epub: error: OCR is not available in this version; scanned PDFs cannot be "
            "converted.\n",
        )
    if args.chapter_regex:
        import re

        try:
            re.compile(args.chapter_regex)
        except re.error as exc:
            parser.exit(ExitCode.USAGE, f"novel2epub: error: invalid --chapter-regex: {exc}\n")
    if not 0 <= args.threshold <= 100:
        parser.exit(ExitCode.USAGE, "novel2epub: error: --threshold must be between 0 and 100\n")
    output = args.output or args.input.with_suffix(".epub")
    if output.is_dir():
        output = output / f"{args.input.stem}.epub"
    if output.resolve() == args.input.resolve():
        parser.exit(ExitCode.USAGE, "novel2epub: error: the output would overwrite the input\n")
    revisions = "accept" if args.accept_changes else "reject" if args.reject_changes else "fail"
    opts = ConvertOptions(
        input=args.input,
        output=output,
        meta=args.meta,
        cover=args.cover,
        revisions=revisions,
        smarten=args.smarten,
        chapter_regex=args.chapter_regex,
        strict=args.strict,
        report_dir=args.report_dir,
        threshold=args.threshold / 100,
        html_tags=not args.literal_tags,
        epubcheck=not args.no_epubcheck,
        epubcheck_jar=args.epubcheck,
        title=args.title,
        authors=args.authors,
        language=args.language,
    )
    try:
        outcome = convert(opts)
    except Exception:
        print(
            "novel2epub: internal error. This is a bug in novel2epub, not a problem with "
            "your manuscript. Nothing was written. Please report it with the details below.",
            file=sys.stderr,
        )
        traceback.print_exc()
        return int(ExitCode.INTERNAL_ERROR)
    _print_summary(outcome)
    return int(outcome.exit_code)


def _tolerant_streams() -> None:
    """Never crash on a character the console cannot show (Windows code pages)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def _pause_if_own_console() -> None:
    """Keep the window open when started by dropping a file on the .exe.

    Windows gives a program started from Explorer a console of its own, which
    closes, with the summary, as soon as the program ends. A program started
    from a terminal shares the terminal's console and must not pause.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import ctypes

    processes = (ctypes.c_uint * 2)()
    if ctypes.windll.kernel32.GetConsoleProcessList(processes, 2) == 1:  # type: ignore[attr-defined]
        with contextlib.suppress(EOFError):
            input("\nPress Enter to close this window.")


def summary_lines(outcome: Outcome) -> list[str]:
    """The end-of-run summary: problems, verification results, and where things went."""
    diag = outcome.diagnostics
    lines = []
    for issue in diag.issues:
        if issue.severity.value == "info" or issue.message in outcome.failures:
            continue
        where = f" ({issue.source})" if issue.source else ""
        lines.append(f"{issue.severity.value}: {issue.message}{where}")
    lines += [f"failed: {failure}" for failure in outcome.failures]
    fid = outcome.fidelity_main
    if fid is not None:
        lines.append(f"text fidelity: {fid.ratio:.4%} of {fid.reference_tokens} words matched")
    if outcome.epubcheck is not None and outcome.epubcheck.ran:
        ec = outcome.epubcheck
        lines.append(
            f"EPUBCheck {ec.version}: {len(ec.errors)} error(s), {len(ec.warnings)} warning(s)"
        )
    # The same count as the report's "Needs human review" section.
    review = sum(i.severity.value != "info" for i in diag.issues) + len(diag.needs_review())
    if review:
        lines.append(f"{review} item(s) need human review: see {outcome.report_md}")
    if outcome.written:
        lines.append(f"wrote {outcome.written}")
    return lines


def _print_summary(outcome: Outcome) -> None:
    for line in summary_lines(outcome):
        print(line, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
