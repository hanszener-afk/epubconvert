"""Collector for warnings, removals, and heuristic decisions during a run."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from novel2epub.ir import SourceRef


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(slots=True)
class Issue:
    code: str  # stable machine-readable code, e.g. "image-no-alt"
    message: str
    severity: Severity
    source: SourceRef | None = None


@dataclass(slots=True)
class Removal:
    """Something deliberately not carried into the EPUB, with its text."""

    kind: str  # e.g. "running-header", "page-number", "comment", "empty-paragraph"
    text: str
    source: SourceRef | None = None
    context: str = ""  # source text just before the removal, to locate it precisely
    replacement: str = ""  # for a deliberate rewrite (such as a drop cap joined to its word)


@dataclass(slots=True)
class Decision:
    """A heuristic choice. Low-confidence decisions need human review."""

    kind: str  # e.g. "chapter-detection", "dehyphenation"
    description: str
    confidence: float  # 0.0..1.0
    method: str = ""
    source: SourceRef | None = None


REVIEW_THRESHOLD = 0.75


class ConversionError(Exception):
    """Fatal problem with the input or options; maps to a non-zero exit."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Diagnostics:
    issues: list[Issue] = field(default_factory=list)
    removals: list[Removal] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)

    def info(self, code: str, message: str, source: SourceRef | None = None) -> None:
        self.issues.append(Issue(code, message, Severity.INFO, source))

    def warn(self, code: str, message: str, source: SourceRef | None = None) -> None:
        self.issues.append(Issue(code, message, Severity.WARNING, source))

    def error(self, code: str, message: str, source: SourceRef | None = None) -> None:
        self.issues.append(Issue(code, message, Severity.ERROR, source))

    def removed(
        self,
        kind: str,
        text: str,
        source: SourceRef | None = None,
        context: str = "",
        replacement: str = "",
    ) -> None:
        self.removals.append(Removal(kind, text, source, context[-40:], replacement))

    def decided(
        self,
        kind: str,
        description: str,
        confidence: float,
        method: str = "",
        source: SourceRef | None = None,
    ) -> None:
        self.decisions.append(Decision(kind, description, confidence, method, source))

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    def needs_review(self) -> list[Decision]:
        return [d for d in self.decisions if d.confidence < REVIEW_THRESHOLD]
