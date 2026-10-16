"""report.json and report.md. Deterministic: no timestamps or absolute paths."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel2epub import __version__
from novel2epub.diagnostics import REVIEW_THRESHOLD
from novel2epub.epub.xhtml import section_label
from novel2epub.ir import Book, SectionKind

if TYPE_CHECKING:
    from novel2epub.pipeline import Outcome
    from novel2epub.verify.fidelity import FidelityResult


def _src(ref: object) -> str | None:
    return str(ref) if ref is not None else None


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


def _structure(book: Book | None) -> list[dict[str, Any]]:
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
            out.append({
                "part": part,
                "kind": s.kind.value,
                "label": section_label(s, number),
                "titled": bool(s.title),
                "blocks": len(s.blocks),
                "source": _src(s.source),
            })  # fmt: skip
    return out


def build_report(outcome: Outcome) -> dict[str, Any]:
    diag = outcome.diagnostics
    review = [
        {"kind": d.kind, "description": d.description, "confidence": d.confidence,
         "method": d.method, "source": _src(d.source)}
        for d in diag.decisions if d.confidence < REVIEW_THRESHOLD
    ]  # fmt: skip
    review += [
        {"kind": i.code, "description": i.message, "source": _src(i.source)}
        for i in diag.issues
        if i.severity.value != "info"
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
    return {
        "tool": {"name": "novel2epub", "version": __version__},
        "input": outcome.options.input.name,
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
        "structure": _structure(book),
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
            "counts": dict(sorted(Counter(r.kind for r in diag.removals).items())),
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
            "main": _fidelity(outcome.fidelity_main),
            "notes": _fidelity(outcome.fidelity_notes),
            "writer": _fidelity(outcome.fidelity_writer),
        },
        "epubcheck": epubcheck,
        "needs_review": review,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Conversion report: {report['input']}", ""]
    lines.append(
        f"**Result:** {report['status'].replace('_', ' ')} (exit code {report['exit_code']})"
    )
    if report["output"]:
        lines.append(f"**Output:** {report['output']}")
    lines.append("")
    for failure in report["failures"]:
        lines.append(f"- **Failure:** {failure}")
    if report["failures"]:
        lines.append("")
    fid = report["fidelity"]["main"]
    if fid:
        lines += [
            "## Text fidelity",
            "",
            f"Main text: {fid['match_ratio']:.4%} of {fid['reference_words']} words matched.",
        ]
        notes = report["fidelity"]["notes"]
        if notes and notes["reference_words"]:
            lines.append(
                f"Notes: {notes['match_ratio']:.4%} of {notes['reference_words']} words matched."
            )
        for name in ("main", "notes"):
            f = report["fidelity"][name]
            for h in (f or {}).get("missing", [])[:50]:
                lines.append(f'- Missing ({name}): "{h["text"]}" after "{h["before"]}"')
            for h in (f or {}).get("added", [])[:50]:
                lines.append(f'- Added ({name}): "{h["text"]}" after "{h["before"]}"')
        lines.append("")
    if report["structure"]:
        lines += ["## Structure", "", "| Part | Kind | Label | Blocks |", "|---|---|---|---|"]
        for s in report["structure"]:
            lines.append(f"| {s['part']} | {s['kind']} | {s['label']} | {s['blocks']} |")
        lines.append("")
        methods = Counter(
            d["method"] for d in report["decisions"] if d["kind"] == "chapter-detection"
        )
        if methods:
            lines.append(
                "Chapter detection: "
                + ", ".join(f"{m} ({n})" for m, n in sorted(methods.items()))
                + "."
            )
            lines.append("")
    counts = report["removed"]["counts"]
    if counts:
        lines += ["## Removed", "", "| Kind | Count |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in counts.items()]
        lines.append("")
    ec = report["epubcheck"]
    lines += ["## EPUBCheck", ""]
    if ec is None:
        lines.append("Not run.")
    elif not ec["ran"]:
        lines.append("Could not run.")
    else:
        lines.append(
            f"EPUBCheck {ec['version']}: {ec['errors']} error(s), {ec['warnings']} warning(s)."
        )
        lines += [f"- {m}" for m in ec["messages"]]
    lines.append("")
    if report["needs_review"]:
        lines += ["## Needs human review", ""]
        for item in report["needs_review"]:
            where = f" ({item['source']})" if item.get("source") else ""
            conf = f" [confidence {item['confidence']:.2f}]" if "confidence" in item else ""
            lines.append(f"- {item['kind']}: {item['description']}{conf}{where}")
        lines.append("")
    return "\n".join(lines)


def write_reports(outcome: Outcome, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    report = build_report(outcome)
    (directory / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (directory / "report.md").write_text(render_markdown(report), encoding="utf-8")
