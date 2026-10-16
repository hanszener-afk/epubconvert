"""report.json and report.md. Deterministic: no timestamps or absolute paths.

Written as NAME.report.json and NAME.report.md, NAME being the EPUB's name
without .epub, so that several books can share a folder. The JSON report is
complete. The Markdown report is for a person: what needs checking comes
first, and long lists are cut short with a pointer to the JSON report.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel2epub import __version__
from novel2epub.diagnostics import REVIEW_THRESHOLD
from novel2epub.epub.xhtml import section_label
from novel2epub.ir import Book, SectionKind

if TYPE_CHECKING:
    from novel2epub.pipeline import Outcome
    from novel2epub.verify.fidelity import FidelityResult

# Decisions that say how a section was found, in order of preference.
_SECTION_DECISIONS = ("chapter-detection", "front-matter", "back-matter")
MD_LIMIT = 100  # items per list in the Markdown report
MD_REMOVED_LIMIT = 30  # distinct texts per removal kind in the Markdown report

_NUMERIC = re.compile(r"^\W*(\d+|[ivxlcdm]+)\W*$", re.IGNORECASE)

RESULT_WORDS = {
    "ok": "Converted",
    "warnings": "Converted, with warnings",
    "input_rejected": "Not converted: the input was rejected",
    "verification_failed": "Failed verification",
}


def _src(ref: object) -> str | None:
    return str(ref) if ref is not None else None


def table_cell(text: str) -> str:
    """Text made safe for one cell of a Markdown table."""
    return " ".join(text.split()).replace("|", "\\|")


def _fidelity(fid: FidelityResult | None) -> dict[str, Any] | None:
    if fid is None:
        return None
    return {
        "match_ratio": round(fid.ratio, 6),
        "loss": round(fid.loss, 6),
        "reference_words": fid.reference_tokens,
        "output_words": fid.output_tokens,
        "missing": [{"text": h.text, "before": h.before, "after": h.after} for h in fid.missing],
        "added": [{"text": h.text, "before": h.before, "after": h.after} for h in fid.added],
        "unmatched_removals": fid.unmatched_removals,
    }


def _structure(book: Book | None, decisions: dict[str, Any]) -> list[dict[str, Any]]:
    if book is None:
        return []
    out = []
    chapter = other = 0
    for part, sections in (("front", book.front), ("body", book.body), ("back", book.back)):
        for s in sections:
            if s.kind is SectionKind.CHAPTER:
                chapter += 1
                number = chapter
            else:
                other += 1
                number = other
            found = decisions.get(str(s.source)) if s.source is not None else None
            out.append({
                "part": part,
                "kind": s.kind.value,
                "label": section_label(s, number),
                "titled": bool(s.title),
                "blocks": len(s.blocks),
                "method": found.method if found else None,
                "confidence": found.confidence if found else None,
                "source": _src(s.source),
            })  # fmt: skip
    return out


def build_report(outcome: Outcome) -> dict[str, Any]:
    diag = outcome.diagnostics
    review: list[dict[str, Any]] = [
        {"kind": i.code, "description": i.message, "source": _src(i.source)}
        for i in diag.issues
        if i.severity.value != "info"
    ]
    review += [
        {"kind": d.kind, "description": d.description, "confidence": d.confidence,
         "method": d.method, "source": _src(d.source)}
        for d in diag.decisions if d.confidence < REVIEW_THRESHOLD
    ]  # fmt: skip

    by_source: dict[str, Any] = {}
    for kind in reversed(_SECTION_DECISIONS):  # preferred kinds overwrite
        for d in diag.decisions:
            if d.kind == kind and d.source is not None:
                by_source[str(d.source)] = d

    grouped: dict[str, list[Any]] = defaultdict(list)
    for d in diag.decisions:
        grouped[d.kind].append(d)
    decision_summary = [
        {
            "kind": kind,
            "count": len(items),
            "below_threshold": sum(d.confidence < REVIEW_THRESHOLD for d in items),
            "methods": dict(sorted(Counter(d.method for d in items if d.method).items())),
        }
        for kind, items in sorted(grouped.items())
    ]

    epubcheck = None
    if outcome.epubcheck is not None:
        epubcheck = {
            "ran": outcome.epubcheck.ran,
            "version": outcome.epubcheck.version,
            "errors": len(outcome.epubcheck.errors),
            "warnings": len(outcome.epubcheck.warnings),
            "messages": [str(m) for m in outcome.epubcheck.messages],
        }
    book = outcome.book
    structure = _structure(book, by_source)
    removed_counts = dict(sorted(Counter(r.kind for r in diag.removals).items()))
    main = _fidelity(outcome.fidelity_main)
    return {
        "tool": {"name": "novel2epub", "version": __version__},
        "input": outcome.options.input.name,
        "json_report": report_names(outcome.options.output)[0],
        "output": outcome.written.name if outcome.written else None,
        "status": outcome.exit_code.name.lower(),
        "exit_code": int(outcome.exit_code),
        "failures": outcome.failures,
        "metadata": {
            "title": book.metadata.title,
            "authors": book.metadata.authors,
            "language": book.metadata.language,
            "identifier": book.metadata.identifier,
        }
        if book
        else None,
        "summary": {
            "chapters": sum(s["kind"] == "chapter" for s in structure),
            "parts": sum(s["kind"] == "part" for s in structure),
            "other_sections": sum(s["kind"] not in ("chapter", "part") for s in structure),
            "words": main["output_words"] if main else 0,
            "removed": sum(removed_counts.values()),
            "warnings": len(diag.warnings),
            "needs_review": len(review),
        },
        "structure": structure,
        "decision_summary": decision_summary,
        "decisions": [
            {
                "kind": d.kind,
                "description": d.description,
                "confidence": d.confidence,
                "method": d.method,
                "source": _src(d.source),
            }
            for d in diag.decisions
        ],
        "removed": {
            "counts": removed_counts,
            "items": [
                {"kind": r.kind, "text": r.text, "source": _src(r.source)}
                for r in diag.removals
                if r.text
            ],
        },
        "issues": [
            {
                "severity": i.severity.value,
                "code": i.code,
                "message": i.message,
                "source": _src(i.source),
            }
            for i in diag.issues
        ],
        "fidelity": {
            "main": main,
            "notes": _fidelity(outcome.fidelity_notes),
            "writer": _fidelity(outcome.fidelity_writer),
        },
        "epubcheck": epubcheck,
        "needs_review": review,
    }


def report_names(output: Path) -> tuple[str, str]:
    """The JSON and Markdown report file names for the EPUB `output`."""
    return f"{output.stem}.report.json", f"{output.stem}.report.md"


def _json_name(report: dict[str, Any]) -> str:
    return str(report.get("json_report", "the JSON report"))


def _more(total: int, shown: int, what: str, json_name: str) -> list[str]:
    if total <= shown:
        return []
    return [f"- ...and {total - shown} more {what}: see {json_name}."]


def _head(report: dict[str, Any]) -> list[str]:
    lines = [f"# Conversion report: {report['input']}", ""]
    result = RESULT_WORDS.get(report["status"], report["status"])
    lines.append(f"**Result:** {result} (exit code {report['exit_code']})  ")
    if report["output"]:
        lines.append(f"**Output:** {report['output']}  ")
    meta = report["metadata"]
    if meta:
        authors = ", ".join(meta["authors"]) or "no author"
        lines.append(f"**Book:** {meta['title']}, by {authors} ({meta['language']})  ")
        lines.append(f"**Identifier:** {meta['identifier']}  ")
    lines.append(f"**Converter:** novel2epub {report['tool']['version']}")
    lines.append("")
    for failure in report["failures"]:
        lines.append(f"- **Failure:** {failure}")
    if report["failures"]:
        lines.append("")
    s = report["summary"]
    if report["structure"]:
        parts = f" in {s['parts']} part(s)" if s["parts"] else ""
        lines.append(
            f"{s['chapters']} chapter(s){parts} and {s['other_sections']} other section(s); "
            f"{s['words']} words. {s['removed']} item(s) removed, {s['warnings']} warning(s), "
            f"{s['needs_review']} item(s) to review."
        )
        lines.append("")
    return lines


def _review(report: dict[str, Any]) -> list[str]:
    items = report["needs_review"]
    lines = ["## Needs human review", ""]
    if not items:
        return [*lines, "Nothing.", ""]
    lines.append(
        "Warnings, then decisions the converter was unsure of "
        f"(confidence below {REVIEW_THRESHOLD:.2f}). Check each before publishing."
    )
    lines.append("")
    for item in items[:MD_LIMIT]:
        where = f" ({item['source']})" if item.get("source") else ""
        conf = f" [confidence {item['confidence']:.2f}]" if "confidence" in item else ""
        lines.append(f"- {item['kind']}: {item['description']}{conf}{where}")
    lines += _more(len(items), MD_LIMIT, "items", _json_name(report))
    return [*lines, ""]


def _fidelity_md(report: dict[str, Any]) -> list[str]:
    fid = report["fidelity"]
    if not fid["main"]:
        return []
    lines = ["## Text fidelity", ""]
    for name, label in (("main", "Main text"), ("notes", "Notes")):
        f = fid[name]
        if not f or not f["reference_words"]:
            continue
        lines.append(
            f"{label}: {f['match_ratio']:.4%} of {f['reference_words']} source words found "
            f"in the EPUB; {len(f['added'])} addition(s)."
        )
    writer = fid["writer"]
    if writer is not None:
        intact = not writer["missing"] and not writer["added"]
        lines.append(
            "EPUB writer: the converted text was reproduced exactly."
            if intact
            else "EPUB writer: the converted text was **not** reproduced exactly."
        )
    details: list[str] = []
    for name in ("main", "notes"):
        f = fid[name] or {}
        found = [("Missing", h) for h in f.get("missing", [])]
        found += [("Added", h) for h in f.get("added", [])]
        for what, h in found[:MD_LIMIT]:
            details.append(f'- {what} ({name}): "{h["text"]}" after "{h["before"]}"')
        details += _more(len(found), MD_LIMIT, "differences", _json_name(report))
        unmatched = f.get("unmatched_removals", [])
        if unmatched:
            details.append(
                f"- {len(unmatched)} logged removal(s) could not be located in the source "
                f"{name} text, so the figures above may be pessimistic."
            )
    if details:
        lines += ["", *details]
    return [*lines, ""]


def _structure_md(report: dict[str, Any]) -> list[str]:
    if not report["structure"]:
        return []
    lines = [
        "## Structure",
        "",
        "| Part | Kind | Label | Blocks | Found by | Confidence |",
        "|---|---|---|---|---|---|",
    ]
    for s in report["structure"]:
        conf = f"{s['confidence']:.2f}" if s["confidence"] is not None else ""
        lines.append(
            f"| {s['part']} | {s['kind']} | {table_cell(s['label'])} | {s['blocks']} "
            f"| {s['method'] or 'not recorded'} | {conf} |"
        )
    return [*lines, ""]


def _decisions_md(report: dict[str, Any]) -> list[str]:
    if not report["decision_summary"]:
        return []
    lines = [
        "## Decisions",
        "",
        "| Kind | Count | Below threshold | Methods |",
        "|---|---|---|---|",
    ]
    for d in report["decision_summary"]:
        methods = ", ".join(f"{m} ({n})" for m, n in d["methods"].items())
        lines.append(f"| {d['kind']} | {d['count']} | {d['below_threshold']} | {methods} |")
    return [*lines, ""]


def _removed_md(report: dict[str, Any]) -> list[str]:
    counts = report["removed"]["counts"]
    if not counts:
        return []
    lines = ["## Removed", "", "| Kind | Count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in counts.items()]
    lines.append("")
    texts: dict[str, Counter[str]] = defaultdict(Counter)
    first: dict[tuple[str, str], str | None] = {}
    for item in report["removed"]["items"]:
        text = " ".join(item["text"].split())
        texts[item["kind"]][text] += 1
        first.setdefault((item["kind"], text), item["source"])
    for kind in counts:
        if not texts[kind]:
            continue
        lines += [f"### {kind}", ""]
        if all(_NUMERIC.match(t) for t in texts[kind]):
            values = sorted(int(t) for t in texts[kind] if t.isdigit())
            span = f", from {values[0]} to {values[-1]}" if values else ""
            lines += [f"- {sum(texts[kind].values())} numbers{span}.", ""]
            continue
        distinct = sorted(texts[kind].items(), key=lambda kv: (-kv[1], kv[0]))
        for text, n in distinct[:MD_REMOVED_LIMIT]:
            shown = text if len(text) <= 120 else text[:117] + "..."
            times = f" ({n} times)" if n > 1 else ""
            where = first[(kind, text)]
            lines.append(f'- "{shown}"{times}' + (f", first at {where}" if where else ""))
        lines += _more(len(distinct), MD_REMOVED_LIMIT, "distinct texts", _json_name(report))
        lines.append("")
    return lines


def _epubcheck_md(report: dict[str, Any]) -> list[str]:
    ec = report["epubcheck"]
    lines = ["## EPUBCheck", ""]
    if ec is None:
        lines.append("Not run.")
    elif not ec["ran"]:
        lines.append("Could not run.")
    else:
        lines.append(
            f"EPUBCheck {ec['version']}: {ec['errors']} error(s), {ec['warnings']} warning(s)."
        )
        lines += [f"- {m}" for m in ec["messages"][:MD_LIMIT]]
        lines += _more(len(ec["messages"]), MD_LIMIT, "messages", _json_name(report))
    return [*lines, ""]


def render_markdown(report: dict[str, Any]) -> str:
    lines = _head(report)
    lines += _review(report)
    lines += _fidelity_md(report)
    lines += _structure_md(report)
    lines += _decisions_md(report)
    lines += _removed_md(report)
    lines += _epubcheck_md(report)
    return "\n".join(lines)


def write_reports(outcome: Outcome, directory: Path) -> Path:
    """Write both reports; return the Markdown report's path."""
    directory.mkdir(parents=True, exist_ok=True)
    report = build_report(outcome)
    json_name, md_name = report_names(outcome.options.output)
    (directory / json_name).write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (directory / md_name).write_text(render_markdown(report), encoding="utf-8")
    return directory / md_name
