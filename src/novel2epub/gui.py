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
import subprocess
import sys
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from novel2epub import __version__
from novel2epub.diagnostics import ConversionError
from novel2epub.gui_forms import (
    BOOK_FIELDS,
    FONT_LABELS,
    LANGUAGES,
    STYLE_LABELS,
    BookForm,
    Form,
    from_metafile,
    plain,
    to_metafile,
    to_options,
)
from novel2epub.images import SHORT, describe_size, measure, size_findings
from novel2epub.metadata import FONT_KEYS, load_meta_file, save_meta_file
from novel2epub.pipeline import ConvertOptions, ExitCode, Outcome, convert

__all__ = ["App", "BookForm", "Form", "from_metafile", "main", "plain", "to_metafile", "to_options"]

TITLE = "novel2epub"
STYLE_DEFAULTS = {
    "side_margin": "2.5", "top_margin": "1.5", "bottom_margin": "1.5", "indent": "1.5",
    "paragraph_gap": "0", "line_spacing": "reader's choice",
}  # fmt: skip

RESULT = {
    ExitCode.OK: ("Converted. Everything checked out.", "ok"),
    ExitCode.WARNINGS: ("Converted, with warnings. Read the report before publishing.", "warn"),
    ExitCode.INPUT_REJECTED: ("Not converted: the manuscript was rejected. See below.", "bad"),
    ExitCode.VERIFICATION_FAILED: (
        "Failed verification. Nothing was written at the chosen place; see the report.",
        "bad",
    ),
}


def _size_check(path: Path, width: str) -> tuple[str, list[str]]:
    """A short note for the Size column, and the full problems for the status line."""
    try:
        data = path.read_bytes()
    except OSError:
        return "cannot be read", [f"{path} cannot be read."]
    size = measure(data)
    if size is None:
        return "not an image", [f"{path.name} is not an image novel2epub can read."]
    try:
        display = float(width) if width else None
    except ValueError:
        display = None
    found = size_findings(size[0], size[1], len(data), display)
    note = "; ".join([describe_size(size[0], size[1], len(data)), *(SHORT[c] for c, _ in found)])
    return note, [f"{path.name}: {text}." for _, text in found]


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
        self.root.geometry("800x700")
        self.root.minsize(720, 600)
        self.epubcheck_enabled = True
        self.report_path: Path | None = None
        self.output_path: Path | None = None
        self.book_path: Path | None = None
        self._results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._busy = False

        self.input_var = tk.StringVar(value=initial)
        self.output_var = tk.StringVar()
        self.changes_var = tk.StringVar(value="fail")
        self.smarten_var = tk.BooleanVar()
        self.literal_var = tk.BooleanVar()
        self.regex_var = tk.StringVar()
        self.strict_var = tk.BooleanVar()
        self.status_var = tk.StringVar(value="Choose a manuscript, then press Convert.")
        self.book_file_var = tk.StringVar(value="No book file: details are used only for now.")
        self.book_vars: dict[str, Any] = {
            name: tk.BooleanVar() if name == "block_paragraphs" else tk.StringVar()
            for name in BOOK_FIELDS
            if name not in ("description", "images")
        }
        # (file, description, width in per cent), in the order added
        self.images: list[tuple[str, str, str]] = []
        self.image_description_var = tk.StringVar()
        self.image_width_var = tk.StringVar()
        self._build()
        if initial:
            self.manuscript_chosen()

    # --- layout -------------------------------------------------------------

    def _build(self) -> None:
        ttk = self.ttk
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        bar = ttk.Frame(outer)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.book_file_var, foreground="#555").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(bar, text="Open book file...", command=self._open_dialog).grid(row=0, column=1)
        ttk.Button(bar, text="Save", command=self._save).grid(row=0, column=2, padx=(4, 0))
        ttk.Button(bar, text="Save as...", command=self._save_as).grid(row=0, column=3, padx=(4, 0))

        self.tabs = tabs = ttk.Notebook(outer)
        tabs.grid(row=1, column=0, sticky="nsew")
        convert_tab = ttk.Frame(tabs, padding=10)
        details_tab = ttk.Frame(tabs, padding=10)
        images_tab = ttk.Frame(tabs, padding=10)
        look_tab = ttk.Frame(tabs, padding=10)
        tabs.add(convert_tab, text="Convert")
        tabs.add(details_tab, text="Book details")
        tabs.add(images_tab, text="Images")
        tabs.add(look_tab, text="Appearance")
        self._build_convert(convert_tab)
        self._build_details(details_tab)
        self._build_images(images_tab)
        self._build_appearance(look_tab)

        self.status = ttk.Label(outer, textvariable=self.status_var, wraplength=740)
        self.status.grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _build_convert(self, tab: Any) -> None:
        tk, ttk = self.tk, self.ttk
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text="Manuscript").grid(row=0, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(tab, text="Choose...", command=self._choose_input).grid(row=0, column=2)
        ttk.Label(tab, text="A .docx, .pdf, .md, or .html file.", foreground="#555").grid(
            row=1, column=1, sticky="w", padx=6
        )
        ttk.Label(tab, text="Save EPUB as").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(tab, textvariable=self.output_var).grid(
            row=2, column=1, sticky="ew", padx=6, pady=(6, 0)
        )
        ttk.Button(tab, text="Choose...", command=self._choose_output).grid(
            row=2, column=2, pady=(6, 0)
        )
        ttk.Label(
            tab, text="Leave blank to save it next to the manuscript.", foreground="#555"
        ).grid(row=3, column=1, sticky="w", padx=6)

        opts = ttk.LabelFrame(tab, text="Options", padding=8)
        opts.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        opts.columnconfigure(3, weight=1)
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

        actions = ttk.Frame(tab)
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)
        self.convert_button = ttk.Button(actions, text="Convert", command=self.start)
        self.convert_button.grid(row=0, column=0)
        self.progress = ttk.Progressbar(actions, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=8)

        tab.rowconfigure(6, weight=1)
        self.summary = tk.Text(tab, height=8, wrap="word", state="disabled", relief="flat")
        self.summary.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        after = ttk.Frame(tab)
        after.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.report_button = ttk.Button(
            after, text="Read the report", command=self.show_report, state="disabled"
        )
        self.report_button.grid(row=0, column=0)
        self.folder_button = ttk.Button(
            after, text="Open the folder", command=self._open_folder, state="disabled"
        )
        self.folder_button.grid(row=0, column=1, padx=(6, 0))

    def _entry(self, parent: Any, row: int, col: int, label: str, name: str, **kw: Any) -> Any:
        ttk = self.ttk
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", pady=2, padx=(0, 6))
        entry = ttk.Entry(parent, textvariable=self.book_vars[name], **kw)
        entry.grid(row=row, column=col + 1, sticky="ew", pady=2, padx=(0, 12))
        return entry

    def _build_details(self, tab: Any) -> None:
        tk, ttk = self.tk, self.ttk
        ttk.Label(
            tab,
            text="Blank fields are taken from the manuscript where it has them.",
            foreground="#555",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        for col in (1, 3):
            tab.columnconfigure(col, weight=1)
        left = [("Title", "title"), ("Subtitle", "subtitle"),
                ("Author(s), separated by ;", "authors"), ("Publisher", "publisher"),
                ("Rights", "rights")]  # fmt: skip
        right = [("Publication date", "date"), ("Series", "series"),
                 ("Number in series", "series_index"), ("ISBN", "isbn")]  # fmt: skip
        for row, (label, name) in enumerate(left, start=1):
            self._entry(tab, row, 0, label, name)
        for row, (label, name) in enumerate(right, start=1):
            self._entry(tab, row, 2, label, name)
        ttk.Label(tab, text="Language").grid(row=5, column=2, sticky="w", pady=2)
        ttk.Combobox(tab, textvariable=self.book_vars["language"], values=LANGUAGES, width=10).grid(
            row=5, column=3, sticky="w", pady=2
        )

        ttk.Label(tab, text="Identifier").grid(row=6, column=0, sticky="w", pady=2)
        ident = ttk.Frame(tab)
        ident.grid(row=6, column=1, columnspan=3, sticky="ew")
        ident.columnconfigure(0, weight=1)
        ttk.Entry(ident, textvariable=self.book_vars["identifier"]).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(ident, text="Generate", command=self._new_identifier).grid(
            row=0, column=1, padx=(6, 0)
        )

        ttk.Label(tab, text="Description").grid(row=7, column=0, sticky="nw", pady=2)
        self.description = tk.Text(tab, height=4, wrap="word")
        self.description.grid(row=7, column=1, columnspan=3, sticky="ew", pady=2)

        ttk.Label(tab, text="Cover image (JPEG or PNG)").grid(row=8, column=0, sticky="w")
        cover = ttk.Frame(tab)
        cover.grid(row=8, column=1, columnspan=3, sticky="ew", pady=2)
        cover.columnconfigure(0, weight=1)
        ttk.Entry(cover, textvariable=self.book_vars["cover"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(cover, text="Choose...", command=self._choose_cover).grid(
            row=0, column=1, padx=(6, 0)
        )
        self._entry(tab, 9, 0, "Cover description (alt text)", "cover_alt")

        a11y = ttk.LabelFrame(tab, text="Accessibility", padding=8)
        a11y.grid(row=10, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        a11y.columnconfigure(1, weight=1)
        self._entry(a11y, 0, 0, "Summary", "a11y_summary")
        self._entry(a11y, 1, 0, "Conforms to", "conforms_to")
        self._entry(a11y, 2, 0, "Certified by", "certified_by")
        ttk.Label(
            a11y,
            text="Fill in 'Conforms to' only if the book has been checked against that "
            "standard: it is a claim the publisher must be able to stand behind.",
            foreground="#555",
            wraplength=660,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_images(self, tab: Any) -> None:
        ttk = self.ttk
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(
            tab,
            text="To put a picture between two paragraphs, type a line of its own in the "
            "manuscript:\n\n"
            "        [image: map.png]\n\n"
            "or, with a description for readers who cannot see it:\n\n"
            "        [image: map.png | A map of the harbour at low tide]\n\n"
            "and, if it should not fill the width, how much of it to take:\n\n"
            "        [image: map.png | A map of the harbour at low tide | 60%]\n\n"
            "The picture is looked for among the images added below, by file name, and then "
            "in the manuscript's folder. What is typed in the manuscript wins over what is "
            "given here. A placeholder inside a sentence is left as text. Size shows each "
            "picture's pixels and file size, and anything a store or reader may object to.",
            justify="left",
            wraplength=740,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        columns = ("file", "size", "width", "description")
        tree = ttk.Treeview(tab, columns=columns, show="headings", height=6)
        tree.heading("file", text="Image file")
        tree.heading("size", text="Size")
        tree.heading("width", text="Width")
        tree.heading("description", text="Description (alt text)")
        tree.column("file", width=150)
        tree.column("size", width=310)
        tree.column("width", width=60, anchor="center")
        tree.column("description", width=220)
        tree.grid(row=1, column=0, sticky="nsew")
        bar = ttk.Scrollbar(tab, command=tree.yview)
        bar.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=bar.set)
        tree.bind("<<TreeviewSelect>>", self._image_selected)
        self.image_tree = tree

        actions = ttk.Frame(tab)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        actions.columnconfigure(3, weight=1)
        ttk.Button(actions, text="Add images...", command=self._choose_images).grid(row=0, column=0)
        ttk.Button(actions, text="Remove", command=self._remove_selected).grid(
            row=0, column=1, padx=(6, 12)
        )
        ttk.Label(actions, text="Description").grid(row=0, column=2)
        ttk.Entry(actions, textvariable=self.image_description_var).grid(
            row=0, column=3, sticky="ew", padx=6
        )
        ttk.Label(actions, text="Width %").grid(row=0, column=4)
        ttk.Spinbox(
            actions, textvariable=self.image_width_var, from_=5, to=100, increment=5, width=5
        ).grid(row=0, column=5, padx=6)
        ttk.Button(actions, text="Set", command=self._describe_selected).grid(row=0, column=6)
        ttk.Label(
            actions,
            text="Leave the width blank to show the picture at its natural size, never wider "
            "than the text.",
            foreground="#555",
        ).grid(row=1, column=0, columnspan=7, sticky="w", pady=(4, 0))

    def add_images(self, paths: list[Path]) -> None:
        known = {file for file, _, _ in self.images}
        for path in paths:
            if str(path) not in known:
                self.images.append((str(path), "", ""))
                known.add(str(path))
        self._refresh_images()

    def remove_image(self, file: str) -> None:
        self.images = [entry for entry in self.images if entry[0] != file]
        self._refresh_images()

    def describe_image(self, file: str, description: str, width: str = "") -> None:
        self.images = [
            (f, description, width) if f == file else (f, d, w) for f, d, w in self.images
        ]
        self._refresh_images()

    def _refresh_images(self) -> None:
        tree = self.image_tree
        tree.delete(*tree.get_children())
        for file, description, width in self.images:
            shown = f"{width}%" if width else "natural"
            tree.insert(
                "", "end", iid=file,
                values=(Path(file).name, _size_check(Path(file), width)[0], shown, description),
            )  # fmt: skip

    def _selected_image(self) -> str | None:
        selection = self.image_tree.selection()
        return str(selection[0]) if selection else None

    def _image_selected(self, _event: Any = None) -> None:
        file = self._selected_image()
        entry = next((e for e in self.images if e[0] == file), None)
        self.image_description_var.set(entry[1] if entry else "")
        self.image_width_var.set(entry[2] if entry else "")
        if entry:
            problems = _size_check(Path(entry[0]), entry[2])[1]
            if problems:
                self._set_status(" ".join(problems), "warn")
            else:
                self._set_status(f"{Path(entry[0]).name}: no size problems found.", "ok")

    def _choose_images(self) -> None:
        from tkinter import filedialog

        paths = filedialog.askopenfilenames(
            title="Add images",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.gif *.webp"), ("All files", "*.*")],
        )
        if paths:
            self.add_images([Path(p) for p in paths])

    def _remove_selected(self) -> None:
        file = self._selected_image()
        if file:
            self.remove_image(file)

    def _describe_selected(self) -> None:
        file = self._selected_image()
        if file:
            self.describe_image(
                file,
                self.image_description_var.get().strip(),
                self.image_width_var.get().strip().rstrip("%"),
            )

    def _build_appearance(self, tab: Any) -> None:
        ttk = self.ttk
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        ttk.Label(
            tab,
            text="Blank means the default. Sizes are in em (the width of the letter M), so "
            "they grow with the reader's text size.",
            foreground="#555",
            wraplength=740,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        def spin(parent: Any, row: int, name: str, step: float, high: float) -> None:
            ttk.Label(parent, text=STYLE_LABELS[name]).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Spinbox(
                parent, textvariable=self.book_vars[name], from_=0, to=high, increment=step,
                width=7,
            ).grid(row=row, column=1, sticky="w", padx=6, pady=2)  # fmt: skip
            ttk.Label(parent, text=f"({STYLE_DEFAULTS[name]})", foreground="#555").grid(
                row=row, column=2, sticky="w"
            )

        margins = ttk.LabelFrame(tab, text="Margins", padding=8)
        margins.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        spin(margins, 0, "side_margin", 0.5, 10)
        spin(margins, 1, "top_margin", 0.5, 10)
        spin(margins, 2, "bottom_margin", 0.5, 10)
        ttk.Label(
            margins,
            text="Top and bottom apply where each chapter starts and ends; reading apps "
            "set the margins on every page.",
            foreground="#555",
            wraplength=330,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

        paras = ttk.LabelFrame(tab, text="Paragraphs and lines", padding=8)
        paras.grid(row=1, column=1, sticky="nsew")
        ttk.Radiobutton(
            paras, text="Indented, no gaps (usual for fiction)", value=False,
            variable=self.book_vars["block_paragraphs"],
        ).grid(row=0, column=0, columnspan=3, sticky="w")  # fmt: skip
        ttk.Radiobutton(
            paras, text="No indents, a gap between paragraphs", value=True,
            variable=self.book_vars["block_paragraphs"],
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 4))  # fmt: skip
        spin(paras, 2, "indent", 0.25, 5)
        spin(paras, 3, "paragraph_gap", 0.25, 3)
        spin(paras, 4, "line_spacing", 0.1, 3)
        ttk.Label(
            paras,
            text="Line spacing is best left to the reader: Kindle handles a fixed one badly.",
            foreground="#555",
            wraplength=330,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        fonts = ttk.LabelFrame(tab, text="Fonts (.ttf or .otf)", padding=8)
        fonts.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        fonts.columnconfigure(1, weight=1)
        for row, key in enumerate(FONT_KEYS):
            name = f"font_{key}"
            ttk.Label(fonts, text=FONT_LABELS[key]).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(fonts, textvariable=self.book_vars[name]).grid(
                row=row, column=1, sticky="ew", padx=6, pady=2
            )
            ttk.Button(fonts, text="Choose...", command=lambda n=name: self._choose_font(n)).grid(
                row=row, column=2, pady=2
            )
            ttk.Button(fonts, text="Clear", command=lambda n=name: self.book_vars[n].set("")).grid(
                row=row, column=3, padx=(4, 0), pady=2
            )
        ttk.Label(
            fonts,
            text="Only the body text font is needed; without italic and bold files, readers "
            "imitate them. Make sure the font's licence allows embedding in e-books. "
            "Kindle uses embedded fonts only when the reader picks 'Publisher font'.",
            foreground="#555",
            wraplength=700,
        ).grid(row=len(FONT_KEYS), column=0, columnspan=4, sticky="w", pady=(4, 0))

    # --- the book form ----------------------------------------------------

    def book_form(self) -> BookForm:
        values: dict[str, Any] = {name: var.get() for name, var in self.book_vars.items()}
        values["description"] = str(self.description.get("1.0", "end")).rstrip("\n")
        values["images"] = tuple(self.images)
        return BookForm(**values)

    def set_book_form(self, form: BookForm) -> None:
        for name, var in self.book_vars.items():
            var.set(getattr(form, name))
        self.description.delete("1.0", "end")
        self.description.insert("1.0", form.description)
        self.images = list(form.images)
        self._refresh_images()

    def open_book_file(self, path: Path) -> bool:
        try:
            meta = load_meta_file(path)
        except ConversionError as exc:
            self._set_status(str(exc), "bad")
            return False
        self.set_book_form(from_metafile(meta))
        self._set_book_path(path)
        self._set_status(f"Opened the book file {path.name}.", "ok")
        return True

    def save_book_file(self, path: Path) -> bool:
        meta = to_metafile(self.book_form())
        if isinstance(meta, str):
            self._set_status(meta, "bad")
            return False
        try:
            save_meta_file(meta, path)
        except OSError as exc:
            self._set_status(f"The book file could not be saved: {exc}", "bad")
            return False
        self._set_book_path(path)
        self._set_status(f"Saved the book file {path.name}.", "ok")
        return True

    def _set_book_path(self, path: Path) -> None:
        self.book_path = path
        self.book_file_var.set(f"Book file: {path}")

    def manuscript_chosen(self) -> None:
        """Open the book file kept beside the manuscript, unless details are already typed."""
        src = Path(self.input_var.get().strip())
        candidate = src.with_suffix(".toml")
        blank = self.book_form().is_blank() and self.book_path is None
        if candidate.is_file() and blank and self.open_book_file(candidate):
            self._set_status(f"Opened {candidate.name}, the book file beside the manuscript.", "ok")

    def _new_identifier(self) -> None:
        self.book_vars["identifier"].set(f"urn:uuid:{uuid.uuid4()}")
        self._set_status("New identifier made. Save the book file to keep it.", "busy")

    # --- choosing files -----------------------------------------------------

    def _choose_input(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Choose a manuscript",
            filetypes=[
                ("Manuscripts", "*.docx *.pdf *.md *.markdown *.html *.htm"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.input_var.set(path)
            self.manuscript_chosen()

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
            self.book_vars["cover"].set(path)

    def _choose_font(self, name: str) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Choose a font", filetypes=[("Fonts", "*.ttf *.otf")]
        )
        if path:
            self.book_vars[name].set(path)

    def _open_dialog(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Open a book file", filetypes=[("Book files", "*.toml"), ("All files", "*.*")]
        )
        if path:
            self.open_book_file(Path(path))

    def _save(self) -> None:
        if self.book_path is not None:
            self.save_book_file(self.book_path)
        else:
            self._save_as()

    def _save_as(self) -> None:
        from tkinter import filedialog

        src = Path(self.input_var.get().strip()) if self.input_var.get().strip() else None
        path = filedialog.asksaveasfilename(
            title="Save the book file as",
            defaultextension=".toml",
            initialfile=f"{src.stem}.toml" if src else "book.toml",
            initialdir=str(src.parent) if src and src.parent.is_dir() else None,
            filetypes=[("Book files", "*.toml")],
        )
        if path:
            self.save_book_file(Path(path))

    # --- converting -----------------------------------------------------------

    def form(self) -> Form:
        return Form(
            input=self.input_var.get(),
            output=self.output_var.get(),
            changes=self.changes_var.get(),
            smarten=self.smarten_var.get(),
            literal_tags=self.literal_var.get(),
            chapter_regex=self.regex_var.get(),
            strict=self.strict_var.get(),
        )

    def _prepare(self) -> ConvertOptions | None:
        opts = to_options(self.form(), self.book_form(), epubcheck=self.epubcheck_enabled)
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
        text = tk.Text(win, wrap="word", padx=10, pady=10, font="TkDefaultFont")
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
