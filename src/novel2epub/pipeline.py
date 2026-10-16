"""The conversion pipeline: front end -> normalise -> EPUB -> verify -> report."""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from PIL import Image as PILImage

from novel2epub.diagnostics import ConversionError, Diagnostics
from novel2epub.docx import reader as docx_reader
from novel2epub.epub.writer import build_epub
from novel2epub.ir import Book, Cover, DocxRef, PdfRef, Resource
from novel2epub.metadata import apply_overrides, load_meta_file, resolve
from novel2epub.normalise.passes import normalise_book
from novel2epub.pdf import reader as pdf_reader
from novel2epub.verify.epubcheck import EpubCheckResult, find_epubcheck, run_epubcheck
from novel2epub.verify.fidelity import FidelityResult, compare, normalise, subtract_removals
from novel2epub.verify.reference_docx import docx_reference_text
from novel2epub.verify.reference_pdf import pdf_reference_pages
from novel2epub.verify.text import epub_text, ir_text

# Removal kinds whose text never appears in the reference text.
NOT_IN_REFERENCE = {
    "comment", "empty-paragraph", "ornament-image", "redundant-scene-break", "empty-section",
}  # fmt: skip


class ExitCode(IntEnum):
    OK = 0
    WARNINGS = 1
    USAGE = 2
    INPUT_REJECTED = 3
    VERIFICATION_FAILED = 4
    INTERNAL_ERROR = 5  # a bug in novel2epub, not a problem with the input


@dataclass
class ConvertOptions:
    input: Path
    output: Path
    meta: Path | None = None
    cover: Path | None = None
    revisions: str = "fail"
    smarten: bool = False
    chapter_regex: str | None = None
    strict: bool = False
    report_dir: Path | None = None
    threshold: float = 0.001
    html_tags: bool = True
    epubcheck: bool = True
    epubcheck_jar: Path | None = None
    title: str | None = None
    authors: list[str] | None = None
    language: str | None = None


@dataclass
class Outcome:
    exit_code: ExitCode
    diagnostics: Diagnostics
    options: ConvertOptions
    book: Book | None = None
    fidelity_main: FidelityResult | None = None
    fidelity_notes: FidelityResult | None = None
    fidelity_writer: FidelityResult | None = None
    epubcheck: EpubCheckResult | None = None
    written: Path | None = None
    failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"exit {self.exit_code.name}"]
        lines += [f"failure: {f}" for f in self.failures]
        for issue in self.diagnostics.issues:
            if issue.severity.value != "info":
                lines.append(f"{issue.severity.value}: {issue.code}: {issue.message}")
        for name, fid in (("main", self.fidelity_main), ("notes", self.fidelity_notes)):
            if fid is None:
                continue
            lines.append(f"fidelity {name}: ratio {fid.ratio:.5f}, loss {fid.loss:.5f}")
            lines += [f"  missing: {h.text!r} (after {h.before!r})" for h in fid.missing[:20]]
            lines += [f"  added: {h.text!r} (after {h.before!r})" for h in fid.added[:20]]
            lines += [f"  unmatched removal: {r!r}" for r in fid.unmatched_removals[:20]]
        if self.epubcheck:
            lines += [f"epubcheck: {m}" for m in self.epubcheck.messages]
        return "\n".join(lines)


def _removals(diag: Diagnostics, notes: bool) -> list[tuple[str, str]]:
    """Logged removals for one text stream, in document order."""
    picked = []
    for n, rm in enumerate(diag.removals):
        if rm.kind in NOT_IN_REFERENCE or not rm.text or not isinstance(rm.source, DocxRef):
            continue
        is_note = rm.source.part.endswith(("footnotes.xml", "endnotes.xml"))
        if is_note != notes:
            continue
        rank = 1 if rm.source.part.endswith("endnotes.xml") else 0
        picked.append(((rank, rm.source.paragraph, n), (rm.text, rm.context)))
    return [item for _, item in sorted(picked)]


def _pdf_reference(path: Path, diag: Diagnostics) -> tuple[str, list[str]]:
    """The PDF reference text, with each page's logged removals cut from that page."""
    by_page: dict[int, list[tuple[str, str, str]]] = {}
    for rm in diag.removals:
        if rm.kind in NOT_IN_REFERENCE or not rm.text or not isinstance(rm.source, PdfRef):
            continue
        by_page.setdefault(rm.source.page, []).append((rm.text, rm.context, rm.replacement))
    texts: list[str] = []
    unmatched: list[str] = []
    for number, text in enumerate(pdf_reference_pages(path), start=1):
        remaining, missed = subtract_removals(text, by_page.get(number, []))
        texts.append(remaining)
        unmatched.extend(missed)
    return normalise(" ".join(texts)), unmatched


def _cover(path: Path, alt: str | None, title: str) -> tuple[Resource, Cover]:
    try:
        data = path.read_bytes()
        with PILImage.open(io.BytesIO(data)) as im:
            fmt = im.format
    except OSError as exc:
        raise ConversionError(
            "cover-invalid", f"Cannot read the cover image {path}: {exc}"
        ) from exc
    types = {"JPEG": "image/jpeg", "PNG": "image/png"}
    if fmt not in types:
        raise ConversionError("cover-format", f"The cover must be a JPEG or PNG image, not {fmt}.")
    return Resource("cover", types[fmt], data), Cover("cover", alt or f"Cover of {title}")


def convert(opts: ConvertOptions) -> Outcome:
    from novel2epub.report import write_reports

    diag = Diagnostics()
    outcome = Outcome(ExitCode.OK, diag, opts)
    report_dir = opts.report_dir or opts.output.parent
    try:
        data = _build(opts, diag, outcome)
    except ConversionError as exc:
        diag.error(exc.code, str(exc))
        outcome.exit_code = ExitCode.INPUT_REJECTED
        outcome.failures.append(str(exc))
        write_reports(outcome, report_dir)
        return outcome

    _verify(opts, diag, outcome, data)

    if opts.strict and diag.warnings:
        outcome.failures.append(f"--strict: {len(diag.warnings)} warning(s).")
    if outcome.failures:
        outcome.exit_code = ExitCode.VERIFICATION_FAILED
        report_dir.mkdir(parents=True, exist_ok=True)
        target = report_dir / f"{opts.output.stem}.failed.epub"
    else:
        outcome.exit_code = ExitCode.WARNINGS if diag.warnings else ExitCode.OK
        target = opts.output
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    outcome.written = target
    write_reports(outcome, report_dir)
    return outcome


def _build(opts: ConvertOptions, diag: Diagnostics, outcome: Outcome) -> bytes:
    suffix = opts.input.suffix.lower()
    if not opts.input.is_file():
        raise ConversionError("input-missing", f"{opts.input} does not exist.")
    if suffix not in (".docx", ".pdf"):
        raise ConversionError(
            "input-unsupported", f"Unsupported input type {suffix!r}: use .docx or .pdf."
        )

    meta_file = load_meta_file(opts.meta) if opts.meta else None
    result: docx_reader.DocxResult | pdf_reader.PdfResult
    if suffix == ".pdf":
        if opts.revisions != "fail":
            diag.warn("option-ignored", "--accept-changes and --reject-changes apply to DOCX only.")
        result = pdf_reader.read_pdf(opts.input, pdf_reader.PdfOptions(opts.chapter_regex), diag)
    else:
        result = docx_reader.read_docx(
            opts.input,
            docx_reader.DocxOptions(opts.revisions, opts.chapter_regex, opts.html_tags),  # type: ignore[arg-type]
            diag,
        )
    book = result.book
    outcome.book = book
    overrides: dict[str, object] = dict(meta_file.fields) if meta_file else {}
    cli = {"title": opts.title, "authors": opts.authors, "language": opts.language}
    overrides.update({k: v for k, v in cli.items() if v})
    book.metadata = apply_overrides(book.metadata, overrides)
    if overrides.get("title"):
        diag.issues = [i for i in diag.issues if i.code != "title-from-filename"]
    if overrides.get("language"):
        diag.issues = [i for i in diag.issues if i.code != "language-default"]

    cover_path = opts.cover or (meta_file.cover if meta_file else None)
    if cover_path:
        resource, cover = _cover(
            cover_path, meta_file.cover_alt if meta_file else None, book.metadata.title
        )
        book.resources[resource.id] = resource
        book.cover = cover

    normalise_book(book, diag, smarten=opts.smarten)
    book.metadata = resolve(book.metadata, result.source_date, diag)
    return build_epub(book, diag)


def _verify(opts: ConvertOptions, diag: Diagnostics, outcome: Outcome, data: bytes) -> None:
    assert outcome.book is not None
    produced = epub_text(data)
    fold = opts.smarten
    if opts.input.suffix.lower() == ".pdf":
        main, unmatched = _pdf_reference(opts.input, diag)
        outcome.fidelity_main = compare(main, produced.main, None, fold, pdf_mode=True)
        outcome.fidelity_main.unmatched_removals = unmatched
        # Footnote lines were logged as removed from the main text; those logged
        # lines, located in the PDFium text above, are the notes' reference.
        notes_ref = " ".join(r.text for r in diag.removals if r.kind == "footnote")
        outcome.fidelity_notes = compare(notes_ref, produced.notes, None, fold, pdf_mode=True)
    else:
        reference = docx_reference_text(opts.input, opts.revisions)
        outcome.fidelity_main = compare(reference.main, produced.main, _removals(diag, False), fold)
        outcome.fidelity_notes = compare(
            reference.notes, produced.notes, _removals(diag, True), fold
        )
    ir = ir_text(outcome.book)
    outcome.fidelity_writer = compare(
        ir.main + " " + ir.notes, produced.main + " " + produced.notes
    )

    for name, fid in (("main text", outcome.fidelity_main), ("notes", outcome.fidelity_notes)):
        if not fid.passes(opts.threshold):
            outcome.failures.append(
                f"Text fidelity ({name}): {fid.missing_tokens} of {fid.reference_tokens} words "
                f"missing ({fid.loss:.3%}), above the {opts.threshold:.3%} threshold."
            )
        elif fid.missing:
            diag.warn(
                "fidelity-missing", f"{fid.missing_tokens} word(s) of the {name} are missing."
            )
        if fid.added:
            diag.warn(
                "fidelity-added", f"{fid.added_tokens} word(s) in the {name} are not in the source."
            )
        if fid.unmatched_removals:
            diag.warn(
                "removal-unmatched",
                f"{len(fid.unmatched_removals)} logged removal(s) could not be located in the "
                f"source {name}; the fidelity figures may be pessimistic.",
            )
    if outcome.fidelity_writer.missing or outcome.fidelity_writer.added:
        outcome.failures.append("The EPUB writer did not reproduce the converted text exactly.")

    if not opts.epubcheck:
        return
    tool = find_epubcheck(opts.epubcheck_jar)
    if tool is None:
        diag.warn(
            "epubcheck-missing", "EPUBCheck (or Java) was not found; the EPUB was not validated."
        )
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "book.epub"
        path.write_bytes(data)
        outcome.epubcheck = run_epubcheck(tool, path)
    if not outcome.epubcheck.ran:
        diag.warn("epubcheck-failed", outcome.epubcheck.detail)
    elif outcome.epubcheck.errors:
        outcome.failures.append(f"EPUBCheck reported {len(outcome.epubcheck.errors)} error(s).")
    elif outcome.epubcheck.warnings:
        diag.warn(
            "epubcheck-warnings",
            f"EPUBCheck reported {len(outcome.epubcheck.warnings)} warning(s).",
        )
