"""A window for converting a manuscript, for people who do not use a terminal.

It is a thin layer over the same pipeline as the command line: it fills in a
ConvertOptions, runs the conversion on a background thread so the window
stays responsive, and shows the same summary and report. Nothing here changes
how a book is converted or verified.

    novel2epub-gui [MANUSCRIPT]

A manuscript given on the command line (or dropped on the program) is filled
in. `--check MANUSCRIPT` converts it through the window without waiting for
a person and exits with the conversion's exit code; the Windows build uses
it to prove that the windowed program works.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel2epub import __version__
from novel2epub.metadata import is_language_tag
from novel2epub.pipeline import ConvertOptions, ExitCode, Outcome, convert

if TYPE_CHECKING:
    import tkinter as tk

TITLE = "novel2epub"
LANGUAGES = ["", "en-GB", "en-US"]

# Messages are written for the command line; in the window, name the controls.
PLAIN_WORDS = [
    ("pass --accept-changes or --reject-changes", "choose Accept all or Reject all"),
    ("--accept-changes and --reject-changes apply", "The tracked-changes choice applies"),
    ("--chapter-regex", "the chapter heading pattern"),
    ("--language", "the Language box"),
    ("--title", "the Title box"),
    ("--strict", "Strict"),
]


def plain(text: str) -> str:
    """`text` with command-line options replaced by the window's own words."""
    for flag, words in PLAIN_WORDS:
        text = text.replace(flag, words)
    return text


RESULT = {
    ExitCode.OK: ("Converted. Everything checked out.", "ok"),
    ExitCode.WARNINGS: ("Converted, with warnings. Read the report before publishing.", "warn"),
    ExitCode.INPUT_REJECTED: ("Not converted: the manuscript was rejected. See below.", "bad"),
    ExitCode.VERIFICATION_FAILED: (
        "Failed verification. Nothing was written at the chosen place; see the report.",
        "bad",
    ),
}


@dataclass
class Form:
    """What the person typed or chose, as text, before any checking."""

    input: str = ""
    output: str = ""
    title: str = ""
    authors: str = ""  # separated by semicolons
    language: str = ""
    cover: str = ""
    meta: str = ""
    changes: str = "fail"  # fail, accept, or reject
    smarten: bool = False
    literal_tags: bool = False
    chapter_regex: str = ""
    strict: bool = False


def to_options(form: Form, epubcheck: bool = True) -> ConvertOptions | str:
    """The conversion options for `form`, or a message saying what to fix."""
    if not form.input.strip():
        return "Choose a manuscript to convert."
    src = Path(form.input.strip())
    if not src.is_file():
        return f"The manuscript {src} cannot be found."
    if src.suffix.lower() not in (".docx", ".pdf"):
        return "The manuscript must be a .docx or .pdf file."
    output = Path(form.output.strip()) if form.output.strip() else src.with_suffix(".epub")
    if output.is_dir():
        output = output / f"{src.stem}.epub"
    if output.resolve() == src.resolve():
        return "The EPUB would overwrite the manuscript. Choose another name."
    if form.chapter_regex.strip():
        try:
            re.compile(form.chapter_regex.strip())
        except re.error as exc:
            return f"The chapter pattern is not valid: {exc}."
    language = form.language.strip()
    if language and not is_language_tag(language):
        return f"{language!r} is not a language tag. Use one such as en-GB or en-US."
    for label, value in (("cover image", form.cover), ("metadata file", form.meta)):
        if value.strip() and not Path(value.strip()).is_file():
            return f"The {label} {value.strip()} cannot be found."
    authors = [a.strip() for a in form.authors.split(";") if a.strip()]
    return ConvertOptions(
        input=src,
        output=output,
        meta=Path(form.meta.strip()) if form.meta.strip() else None,
        cover=Path(form.cover.strip()) if form.cover.strip() else None,
        revisions=form.changes,
        smarten=form.smarten,
        chapter_regex=form.chapter_regex.strip() or None,
        strict=form.strict,
        html_tags=not form.literal_tags,
        epubcheck=epubcheck,
        title=form.title.strip() or None,
        authors=authors or None,
        language=language or None,
    )


def open_in_system(path: Path) -> None:
    """Show a file or folder with the computer's own program for it."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class App:
    def __init__(self, initial: str = "") -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title(f"{TITLE} {__version__}")
        self.root.minsize(620, 560)
        self.epubcheck_enabled = True
        self.report_path: Path | None = None
        self.output_path: Path | None = None
        self._results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._busy = False

        self.input_var = tk.StringVar(value=initial)
        self.output_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.authors_var = tk.StringVar()
        self.language_var = tk.StringVar()
        self.cover_var = tk.StringVar()
        self.meta_var = tk.StringVar()
        self.changes_var = tk.StringVar(value="fail")
        self.smarten_var = tk.BooleanVar()
        self.literal_var = tk.BooleanVar()
        self.regex_var = tk.StringVar()
        self.strict_var = tk.BooleanVar()
        self.status_var = tk.StringVar(value="Choose a manuscript, then press Convert.")
        self._build()

    # --- layout -------------------------------------------------------------

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(outer, text="Manuscript (.docx or .pdf)").grid(row=row, column=0, sticky="w")
        ttk.Entry(outer, textvariable=self.input_var).grid(row=row, column=1, sticky="ew", padx=6)
        ttk.Button(outer, text="Choose...", command=self._choose_input).grid(row=row, column=2)
        row += 1
        ttk.Label(outer, text="Save EPUB as").grid(row=row, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(outer, textvariable=self.output_var).grid(
            row=row, column=1, sticky="ew", padx=6, pady=(6, 0)
        )
        ttk.Button(outer, text="Choose...", command=self._choose_output).grid(
            row=row, column=2, pady=(6, 0)
        )
        row += 1
        ttk.Label(
            outer, text="Leave blank to save it next to the manuscript.", foreground="#555"
        ).grid(row=row, column=1, sticky="w", padx=6)

        row += 1
        book = ttk.LabelFrame(
            outer, text="Book details (optional: blank means use the manuscript's)", padding=8
        )
        book.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        book.columnconfigure(1, weight=1)
        self._field(book, 0, "Title", self.title_var)
        self._field(book, 1, "Author(s), separated by ;", self.authors_var)
        ttk.Label(book, text="Language").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Combobox(book, textvariable=self.language_var, values=LANGUAGES, width=10).grid(
            row=2, column=1, sticky="w", padx=6, pady=2
        )
        self._field(book, 3, "Cover image (JPEG or PNG)", self.cover_var, self._choose_cover)
        self._field(book, 4, "Metadata file (.toml)", self.meta_var, self._choose_meta)

        row += 1
        opts = ttk.LabelFrame(outer, text="Options", padding=8)
        opts.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(opts, text="Tracked changes in Word:").grid(row=0, column=0, sticky="w")
        for col, (label, value) in enumerate(
            (("Stop and tell me", "fail"), ("Accept all", "accept"), ("Reject all", "reject")),
            start=1,
        ):
            ttk.Radiobutton(opts, text=label, value=value, variable=self.changes_var).grid(
                row=0, column=col, sticky="w", padx=(6, 0)
            )
        ttk.Checkbutton(
            opts, text="Change straight quotes to curly quotes", variable=self.smarten_var
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Checkbutton(
            opts,
            text="Keep typed tags such as <i> as text (normally they become italics)",
            variable=self.literal_var,
        ).grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(
            opts, text="Treat warnings as failures (strict)", variable=self.strict_var
        ).grid(row=3, column=0, columnspan=4, sticky="w")
        ttk.Label(opts, text="Chapter heading pattern (advanced)").grid(
            row=4, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Entry(opts, textvariable=self.regex_var).grid(
            row=4, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=(4, 0)
        )

        row += 1
        actions = ttk.Frame(outer)
        actions.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)
        self.convert_button = ttk.Button(actions, text="Convert", command=self.start)
        self.convert_button.grid(row=0, column=0)
        self.progress = ttk.Progressbar(actions, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=8)

        row += 1
        self.status = ttk.Label(outer, textvariable=self.status_var, wraplength=560)
        self.status.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 4))

        row += 1
        outer.rowconfigure(row, weight=1)
        self.summary = tk.Text(outer, height=8, wrap="word", state="disabled", relief="flat")
        self.summary.grid(row=row, column=0, columnspan=3, sticky="nsew")

        row += 1
        after = ttk.Frame(outer)
        after.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.report_button = ttk.Button(
            after, text="Read the report", command=self.show_report, state="disabled"
        )
        self.report_button.grid(row=0, column=0)
        self.folder_button = ttk.Button(
            after, text="Open the folder", command=self._open_folder, state="disabled"
        )
        self.folder_button.grid(row=0, column=1, padx=(6, 0))

    def _field(
        self, parent: Any, row: int, label: str, var: tk.StringVar, choose: Any = None
    ) -> None:
        ttk = self.ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        if choose is not None:
            ttk.Button(parent, text="Choose...", command=choose).grid(row=row, column=2, pady=2)

    # --- choosing files -----------------------------------------------------

    def _choose_input(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Choose a manuscript",
            filetypes=[("Manuscripts", "*.docx *.pdf"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)

    def _choose_output(self) -> None:
        from tkinter import filedialog

        src = Path(self.input_var.get().strip() or "book.docx")
        path = filedialog.asksaveasfilename(
            title="Save the EPUB as",
            defaultextension=".epub",
            initialfile=f"{src.stem}.epub",
            initialdir=str(src.parent) if src.parent.is_dir() else None,
            filetypes=[("EPUB", "*.epub")],
        )
        if path:
            self.output_var.set(path)

    def _choose_cover(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Choose a cover image", filetypes=[("Images", "*.jpg *.jpeg *.png")]
        )
        if path:
            self.cover_var.set(path)

    def _choose_meta(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Choose a metadata file", filetypes=[("TOML", "*.toml"), ("All files", "*.*")]
        )
        if path:
            self.meta_var.set(path)

    # --- converting -----------------------------------------------------------

    def form(self) -> Form:
        return Form(
            input=self.input_var.get(),
            output=self.output_var.get(),
            title=self.title_var.get(),
            authors=self.authors_var.get(),
            language=self.language_var.get(),
            cover=self.cover_var.get(),
            meta=self.meta_var.get(),
            changes=self.changes_var.get(),
            smarten=self.smarten_var.get(),
            literal_tags=self.literal_var.get(),
            chapter_regex=self.regex_var.get(),
            strict=self.strict_var.get(),
        )

    def _prepare(self) -> ConvertOptions | None:
        opts = to_options(self.form(), epubcheck=self.epubcheck_enabled)
        if isinstance(opts, str):
            self._set_status(opts, "bad")
            return None
        self.report_path = None
        self.output_path = None
        self.report_button.configure(state="disabled")
        self.folder_button.configure(state="disabled")
        self._set_summary("")
        return opts

    def start(self) -> None:
        """Convert on a background thread, keeping the window responsive."""
        if self._busy:
            return
        opts = self._prepare()
        if opts is None:
            return
        self._busy = True
        self.convert_button.configure(state="disabled")
        self.progress.start(12)
        self._set_status("Converting and checking. A long PDF can take a few minutes.", "busy")
        threading.Thread(target=self._work, args=(opts,), daemon=True).start()
        self.root.after(100, self._poll)

    def _work(self, opts: ConvertOptions) -> None:
        try:
            self._results.put(("done", convert(opts)))
        except Exception:
            self._results.put(("crash", traceback.format_exc()))

    def _poll(self) -> None:
        try:
            kind, value = self._results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll)
            return
        self._busy = False
        self.progress.stop()
        self.convert_button.configure(state="normal")
        if kind == "crash":
            self._show_crash(value)
        else:
            self._show(value)

    def convert_now(self) -> Outcome | None:
        """Convert without a thread (for --check and tests); the same steps as start()."""
        opts = self._prepare()
        if opts is None:
            return None
        outcome = convert(opts)
        self._show(outcome)
        return outcome

    def _show(self, outcome: Outcome) -> None:
        from novel2epub.cli import summary_lines

        message, tone = RESULT.get(outcome.exit_code, (outcome.exit_code.name, "bad"))
        self._set_status(message, tone)
        self._set_summary(plain("\n".join(summary_lines(outcome))))
        self.report_path = outcome.report_md
        self.output_path = outcome.written
        if self.report_path is not None:
            self.report_button.configure(state="normal")
        if self.output_path is not None:
            self.folder_button.configure(state="normal")

    def _show_crash(self, details: str) -> None:
        self._set_status(
            "Something went wrong inside novel2epub. This is a bug, not a problem with your "
            "manuscript, and nothing was written. Please send the details below.",
            "bad",
        )
        self._set_summary(details)

    def _set_status(self, text: str, tone: str) -> None:
        colours = {"ok": "#1a7f37", "warn": "#9a6700", "bad": "#cf222e", "busy": "#333"}
        self.status_var.set(text)
        self.status.configure(foreground=colours.get(tone, "#333"))

    def _set_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def summary_text(self) -> str:
        return str(self.summary.get("1.0", "end")).strip()

    # --- after converting -----------------------------------------------------

    def show_report(self) -> None:
        """Show the report in a window of its own: Windows often has no program for .md."""
        if self.report_path is None or not self.report_path.is_file():
            return
        tk, ttk = self.tk, self.ttk
        win = tk.Toplevel(self.root)
        win.title(self.report_path.name)
        win.geometry("760x640")
        text = tk.Text(win, wrap="word", padx=10, pady=10)
        bar = ttk.Scrollbar(win, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.tag_configure("h1", font=("TkDefaultFont", 14, "bold"), spacing3=6)
        text.tag_configure("h2", font=("TkDefaultFont", 12, "bold"), spacing1=8, spacing3=4)
        for line in plain(self.report_path.read_text(encoding="utf-8")).splitlines():
            level = len(line) - len(line.lstrip("#"))
            tag = ("h1",) if level == 1 else ("h2",) if level > 1 else ()
            text.insert("end", line.lstrip("# ").replace("**", "") + "\n", tag)
        text.configure(state="disabled")

    def _open_folder(self) -> None:
        if self.output_path is not None:
            open_in_system(self.output_path.parent)

    def run(self) -> None:
        self.root.mainloop()


def _quiet_streams() -> None:
    """A windowed program has no console: give writes somewhere to go."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def main(argv: list[str] | None = None) -> int:
    _quiet_streams()
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--check"] and len(args) == 2:
        app = App(args[1])
        app.root.withdraw()
        try:
            outcome = app.convert_now()
        finally:
            app.root.destroy()
        return int(outcome.exit_code) if outcome is not None else int(ExitCode.USAGE)
    App(args[0] if args else "").run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
