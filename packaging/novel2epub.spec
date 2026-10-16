# PyInstaller spec: two programs in one folder, sharing one set of libraries.
#   novel2epub      the command line (console)
#   novel2epub-gui  the window (no console)
# Run through scripts/build_exe.py, which sets the paths below.
# ruff: noqa
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = os.environ["N2E_ROOT"]
RUNTIME = os.environ["N2E_RUNTIME"]
EPUBCHECK = os.environ["N2E_EPUBCHECK"]

datas = [(RUNTIME, "runtime"), (EPUBCHECK, "epubcheck")]
datas += collect_data_files("novel2epub") + collect_data_files("pdfminer")
binaries = []
hiddenimports = []
for package in ("pypdfium2", "pypdfium2_raw"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h


def analyse(script, excludes):
    return Analysis(
        [os.path.join(ROOT, "packaging", script)],
        pathex=[os.path.join(ROOT, "src")],
        datas=datas,
        binaries=binaries,
        hiddenimports=hiddenimports,
        excludes=excludes,
    )


cli = analyse("entry.py", ["tkinter", "epubcheck"])
gui = analyse("entry_gui.py", ["epubcheck"])

cli_exe = EXE(
    PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="novel2epub", console=True
)
gui_exe = EXE(
    PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="novel2epub-gui", console=False
)
COLLECT(
    cli_exe, cli.binaries, cli.datas,
    gui_exe, gui.binaries, gui.datas,
    name="novel2epub",
)
