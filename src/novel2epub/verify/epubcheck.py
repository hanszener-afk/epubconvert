"""Run EPUBCheck (Java) on a written EPUB and parse its JSON report.

Lookup order for the jar: an explicit path, the NOVEL2EPUB_EPUBCHECK
environment variable, the jar shipped inside the Windows build, then the jar
bundled in the `epubcheck` Python package (a development dependency). Java
comes from the runtime shipped inside the Windows build, then JAVA_HOME, then
PATH. The Windows build prefers what it ships, so its results do not depend on
whatever Java the computer happens to have.
EPUBCheck makes no network requests.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ENV_VAR = "NOVEL2EPUB_EPUBCHECK"
TIMEOUT_SECONDS = 300
REPORTED = ("FATAL", "ERROR", "WARNING")


@dataclass(frozen=True, slots=True)
class EpubCheckTool:
    java: Path
    jar: Path


@dataclass(frozen=True, slots=True)
class EpubCheckMessage:
    id: str
    severity: str
    message: str
    location: str

    def __str__(self) -> str:
        return f"{self.severity} {self.id} {self.location}: {self.message}"


@dataclass
class EpubCheckResult:
    ran: bool
    version: str = ""
    messages: list[EpubCheckMessage] = field(default_factory=list)
    detail: str = ""  # why it did not run, if it did not

    @property
    def errors(self) -> list[EpubCheckMessage]:
        return [m for m in self.messages if m.severity in ("FATAL", "ERROR")]

    @property
    def warnings(self) -> list[EpubCheckMessage]:
        return [m for m in self.messages if m.severity == "WARNING"]


JAVA = "java.exe" if sys.platform == "win32" else "java"


def _app_dir() -> Path | None:
    """Where a frozen (PyInstaller) build keeps its bundled files."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return None


def _find_java() -> Path | None:
    app = _app_dir()
    homes = [app / "runtime"] if app else []
    if os.environ.get("JAVA_HOME"):
        homes.append(Path(os.environ["JAVA_HOME"]))
    for home in homes:
        exe = home / "bin" / JAVA
        if exe.is_file():
            return exe
    found = shutil.which("java")
    return Path(found) if found else None


def _bundled_jar() -> Path | None:
    app = _app_dir()
    if app and (app / "epubcheck" / "epubcheck.jar").is_file():
        return app / "epubcheck" / "epubcheck.jar"
    spec = importlib.util.find_spec("epubcheck")  # located, not imported
    if spec is None or not spec.submodule_search_locations:
        return None
    for location in spec.submodule_search_locations:
        jar = Path(location) / "epubcheck.jar"
        if jar.is_file():
            return jar
    return None


def find_epubcheck(explicit: Path | None = None) -> EpubCheckTool | None:
    if explicit is not None:
        jar: Path | None = explicit if explicit.is_file() else None
    elif os.environ.get(ENV_VAR):
        candidate = Path(os.environ[ENV_VAR])
        jar = candidate if candidate.is_file() else None
    else:
        jar = _bundled_jar()
    java = _find_java()
    if jar is None or java is None:
        return None
    return EpubCheckTool(java, jar)


def run_epubcheck(tool: EpubCheckTool, epub: Path) -> EpubCheckResult:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        cmd = [
            str(tool.java), "-jar", str(tool.jar), str(epub),
            "--json", str(report), "--locale", "en",
        ]  # fmt: skip
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                cmd,
                # Explicit, because a windowed program has no console handles to pass on.
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                creationflags=flags,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return EpubCheckResult(ran=False, detail=f"EPUBCheck could not run: {exc}")
        if not report.is_file():
            tail = (proc.stderr or proc.stdout).strip()[-500:]
            return EpubCheckResult(ran=False, detail=f"EPUBCheck produced no report: {tail}")
        data = json.loads(report.read_text(encoding="utf-8"))
    messages = []
    for m in data.get("messages", []):
        if m.get("severity") not in REPORTED:
            continue
        locs = m.get("locations") or [{}]
        loc = locs[0]
        where = loc.get("path", "")
        if loc.get("line", -1) not in (-1, None):
            where += f":{loc['line']}:{loc.get('column', 0)}"
        messages.append(
            EpubCheckMessage(m.get("ID", ""), m["severity"], m.get("message", ""), where)
        )
    version = data.get("checker", {}).get("checkerVersion", "")
    return EpubCheckResult(ran=True, version=version, messages=messages)
