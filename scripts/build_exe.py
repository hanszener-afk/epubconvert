"""Build the portable program: a folder holding novel2epub.exe, zipped.

    python scripts/build_exe.py [--jdk PATH] [--no-smoke-test]

Needs the packages in packaging/requirements-build.txt and a full JDK 21
(for jlink and its jmods), found with --jdk or JAVA_HOME. Run it on Windows
to get the Windows program: PyInstaller does not cross-compile. On Linux it
builds the Linux equivalent, which is how the script itself is checked.

The folder contains:
    novel2epub.exe         the program
    _internal/             Python, the libraries, a trimmed Java runtime
                           (runtime/) and EPUBCheck (epubcheck/)
    README.txt, LICENSE.txt, THIRD-PARTY-NOTICES.txt, licences/

The smoke test converts one DOCX and one PDF fixture with the built program,
with JAVA_HOME and PATH cleared, and requires EPUBCheck to have run on both
with no errors or warnings: proof that the bundled Java and jar are used.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "novel2epub"
WINDOWS = sys.platform == "win32"
EXE = f"{NAME}.exe" if WINDOWS else NAME

# What EPUBCheck 5.3.0 needs, from `jdeps --print-module-deps` over its jar and
# libraries. Checked by comparing EPUBCheck's findings under this runtime and
# a full JDK on valid and invalid books.
JAVA_MODULES = [
    "java.base",
    "java.compiler",
    "java.desktop",
    "java.security.jgss",
    "java.sql",
    "jdk.unsupported",
    "jdk.xml.dom",
]

# Python distributions inside the program, for the notices.
BUNDLED = [
    "lxml",
    "pillow",
    "pdfminer.six",
    "charset-normalizer",
    "cryptography",
    "cffi",
    "pycparser",
    "pypdfium2",
]

NOTICES = """\
novel2epub bundles the following third-party software. Each is used under
its own licence; the full texts are in the licences folder.

Python {python}
    Python Software Foundation License. licences/python/

{packages}

A Java runtime, trimmed with jlink from OpenJDK {java}
    GNU General Public License, version 2, with the Classpath Exception.
    _internal/runtime/legal/ (one folder per module)
    The Classpath Exception allows this runtime to be distributed with
    programs under other licences. Its source code is available from the
    OpenJDK project (https://openjdk.org) and from the distributor of the
    JDK used for this build ({java_vendor}).

EPUBCheck 5.3.0 and the Java libraries it includes
    BSD 3-Clause (EPUBCheck); the libraries it includes are under the BSD,
    MIT, Apache 2.0, MPL 2.0, and W3C licences. licences/epubcheck/
    (THIRD-PARTY.txt there lists each library and its licence.)

The SCOWL English word list (used to decide line-end hyphens)
    A permissive licence; see licences/scowl/SCOWL-COPYRIGHT.txt.

The program was packaged with PyInstaller, whose bootloader is distributed
under the GNU General Public License with an exception that allows it to be
used in programs under any licence.
"""

README = """\
novel2epub {version}

Converts DOCX and PDF novel manuscripts into EPUB 3, and checks that no text
was lost on the way. No installation and no Java needed: everything is in
this folder. It makes no network connections.

The quick way
    Drag a .docx or .pdf file onto novel2epub.exe. A window opens, shows a
    summary, and waits for Enter. The EPUB and the reports (report.md,
    report.json) are written next to the manuscript.

From a terminal (Command Prompt or PowerShell), for all the options
    novel2epub.exe manuscript.docx -o book.epub --meta book.toml --cover cover.jpg
    novel2epub.exe --help

Read report.md after every conversion, especially the "Needs human review"
section. No conversion is perfect, and PDF conversion is reconstruction.

Exit codes: 0 success, 1 success with warnings, 2 usage error, 3 input
rejected, 4 verification failed, 5 internal error.

Licence: MIT (LICENSE.txt). Third-party software: THIRD-PARTY-NOTICES.txt.
"""


def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=True, text=True, **kwargs)  # type: ignore[call-overload]


def jlink_runtime(jdk: Path, out: Path) -> str:
    jlink = jdk / "bin" / ("jlink.exe" if WINDOWS else "jlink")
    if not jlink.is_file() or not (jdk / "jmods").is_dir():
        sys.exit(f"{jdk} is not a full JDK with jlink and jmods.")
    shutil.rmtree(out, ignore_errors=True)
    run([
        str(jlink), "--add-modules", ",".join(JAVA_MODULES),
        "--strip-debug", "--no-header-files", "--no-man-pages",
        "--compress=zip-6", "--output", str(out),
    ])  # fmt: skip
    release = dict(
        line.split("=", 1) for line in (out / "release").read_text().splitlines() if "=" in line
    )
    return release.get("JAVA_VERSION", "?").strip('"')


def epubcheck_files(out: Path) -> Path:
    import epubcheck  # the PyPI package; only its jar and licence files are used

    src = Path(epubcheck.__file__).parent
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copy2(src / "epubcheck.jar", out)
    shutil.copytree(src / "lib", out / "lib")
    return src


def pyinstaller(runtime: Path, epubcheck: Path) -> Path:
    import PyInstaller.__main__

    sep = os.pathsep
    work = BUILD / "pyinstaller"
    PyInstaller.__main__.run([
        str(ROOT / "packaging" / "entry.py"),
        "--name", NAME, "--onedir", "--console", "--noconfirm", "--clean",
        "--distpath", str(DIST), "--workpath", str(work), "--specpath", str(work),
        "--add-data", f"{runtime}{sep}runtime",
        "--add-data", f"{epubcheck}{sep}epubcheck",
        "--collect-data", "novel2epub",
        "--collect-data", "pdfminer",
        "--collect-all", "pypdfium2",
        "--collect-all", "pypdfium2_raw",
        "--exclude-module", "tkinter",
        "--exclude-module", "epubcheck",
    ])  # fmt: skip
    return DIST / NAME


def licences(folder: Path, epubcheck_src: Path, java: str) -> None:
    from novel2epub import __version__

    lic = folder / "licences"
    shutil.rmtree(lic, ignore_errors=True)
    lic.mkdir()
    shutil.copy2(ROOT / "LICENSE", folder / "LICENSE.txt")

    python_licence = Path(sys.base_prefix) / "LICENSE.txt"
    (lic / "python").mkdir()
    if python_licence.is_file():
        shutil.copy2(python_licence, lic / "python" / "LICENSE.txt")
    else:
        (lic / "python" / "LICENSE.txt").write_text(
            "Python Software Foundation License Version 2: "
            "https://docs.python.org/3/license.html\n",
            encoding="utf-8",
        )

    lines = []
    for name in BUNDLED:
        dist = importlib.metadata.distribution(name)
        meta = dist.metadata
        licence = meta.get("License-Expression") or meta.get("License") or ""
        if not licence or len(licence) > 80:
            classifiers = meta.get_all("Classifier") or []
            licence = "; ".join(
                c.split(" :: ")[-1] for c in classifiers if c.startswith("License ::")
            )
        lines.append(f"{meta['Name']} {meta['Version']}\n    {licence}. licences/{name}/")
        target = lic / name
        target.mkdir()
        copied = 0
        for f in dist.files or []:
            parts = f.parts
            in_dist_info = parts[0].endswith(".dist-info")
            named = f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE"))
            if in_dist_info and (named or "licenses" in parts[1:-1]):
                dest = target.joinpath(*parts[1:])
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(str(dist.locate_file(f))), dest)
                copied += 1
        if not copied:
            sys.exit(f"No licence file found for {name}; add it by hand before distributing.")

    (lic / "epubcheck").mkdir()
    for f in ("LICENSE.txt", "COPYING.txt", "THIRD-PARTY.txt"):
        if (epubcheck_src / f).is_file():
            shutil.copy2(epubcheck_src / f, lic / "epubcheck" / f)
    shutil.copytree(epubcheck_src / "licenses", lic / "epubcheck" / "licenses")

    (lic / "scowl").mkdir()
    shutil.copy2(ROOT / "src" / NAME / "data" / "SCOWL-COPYRIGHT.txt", lic / "scowl")

    vendor = os.environ.get("JAVA_VENDOR", "see _internal/runtime/release")
    (folder / "THIRD-PARTY-NOTICES.txt").write_text(
        NOTICES.format(
            python=sys.version.split()[0],
            packages="\n\n".join(lines),
            java=java,
            java_vendor=vendor,
        ),
        encoding="utf-8",
    )
    (folder / "README.txt").write_text(README.format(version=__version__), encoding="utf-8")


def smoke_test(folder: Path) -> None:
    exe = folder / EXE
    env = {k: v for k, v in os.environ.items() if k not in ("JAVA_HOME", "NOVEL2EPUB_EPUBCHECK")}
    env["PATH"] = os.environ.get("SYSTEMROOT", "/nonexistent")  # no java on PATH
    env.pop("JAVA_TOOL_OPTIONS", None)
    fixtures = [
        ROOT / "tests" / "fixtures" / "docx" / "styled.docx",
        ROOT / "tests" / "fixtures" / "pdf" / "clean.pdf",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        for src in fixtures:
            out = Path(tmp) / src.stem
            proc = subprocess.run(
                [str(exe), str(src), "-o", str(out / f"{src.stem}.epub"), "--language", "en-GB"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            print(proc.stderr)
            if proc.returncode not in (0, 1):
                sys.exit(f"Smoke test failed on {src.name}: exit code {proc.returncode}.")
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            ec = report["epubcheck"]
            if not ec or not ec["ran"] or ec["errors"] or ec["warnings"]:
                sys.exit(f"Smoke test failed on {src.name}: EPUBCheck result {ec}.")
            if report["fidelity"]["main"]["match_ratio"] != 1.0:
                sys.exit(f"Smoke test failed on {src.name}: text fidelity below 100%.")
    print("Smoke test passed: both fixtures converted, verified, and validated.")


def zip_folder(folder: Path) -> Path:
    from novel2epub import __version__

    platform = "windows" if WINDOWS else sys.platform
    target = DIST / f"{NAME}-{__version__}-{platform}.zip"
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, Path(NAME) / f.relative_to(folder))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--jdk", type=Path, default=os.environ.get("JAVA_HOME"))
    parser.add_argument("--no-smoke-test", action="store_true")
    args = parser.parse_args()
    if args.jdk is None:
        sys.exit("No JDK: pass --jdk or set JAVA_HOME.")
    BUILD.mkdir(exist_ok=True)
    java = jlink_runtime(Path(args.jdk), BUILD / "runtime")
    src = epubcheck_files(BUILD / "epubcheck")
    folder = pyinstaller(BUILD / "runtime", BUILD / "epubcheck")
    licences(folder, src, java)
    if not args.no_smoke_test:
        smoke_test(folder)
    archive = zip_folder(folder)
    size = archive.stat().st_size / 1e6
    print(f"Built {archive} ({size:.0f} MB).")


if __name__ == "__main__":
    main()
