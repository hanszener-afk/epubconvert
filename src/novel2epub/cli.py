"""Command-line interface. No network access, no telemetry."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from novel2epub import __version__
from novel2epub.pipeline import ConvertOptions, ExitCode, convert


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="novel2epub",
        description="Convert a DOCX or PDF novel manuscript into an EPUB 3 file, "
        "and check that no text was lost.",
        epilog="Exit codes: 0 success, 1 success with warnings, 2 usage error, "
        "3 input rejected, 4 verification failed (fidelity or EPUBCheck).",
    )
    p.add_argument("input", type=Path, help="the manuscript (.docx or .pdf)")
    p.add_argument("-o", "--output", type=Path, help="the EPUB to write (default: input name)")
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
    p.add_argument("--report-dir", type=Path, help="where to write report.json and report.md")
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
    outcome = convert(opts)
    _print_summary(outcome)
    return int(outcome.exit_code)


def _print_summary(outcome: object) -> None:
    from novel2epub.pipeline import Outcome

    assert isinstance(outcome, Outcome)
    out = sys.stderr
    diag = outcome.diagnostics
    for issue in diag.issues:
        if issue.severity.value == "info":
            continue
        where = f" ({issue.source})" if issue.source else ""
        print(f"{issue.severity.value}: {issue.message}{where}", file=out)
    for failure in outcome.failures:
        print(f"failed: {failure}", file=out)
    fid = outcome.fidelity_main
    if fid is not None:
        print(
            f"text fidelity: {fid.ratio:.4%} of {fid.reference_tokens} words matched",
            file=out,
        )
    if outcome.epubcheck is not None and outcome.epubcheck.ran:
        ec = outcome.epubcheck
        print(
            f"EPUBCheck {ec.version}: {len(ec.errors)} error(s), {len(ec.warnings)} warning(s)",
            file=out,
        )
    review = len(diag.needs_review())
    if review:
        print(f"{review} decision(s) need human review: see report.md", file=out)
    if outcome.written:
        print(f"wrote {outcome.written}", file=out)


if __name__ == "__main__":
    sys.exit(main())
