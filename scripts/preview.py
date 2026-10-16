"""Render pages of an EPUB to PNG screenshots, to judge how the output looks.

    python scripts/preview.py book.epub OUT_DIR [--pages N,N,...] [--width 600]

Pages are spine positions (1 = first document). Rendering uses Chromium,
which is closer to Apple Books and Kobo than to Kindle; treat it as a
first look, not as proof of how every reading system displays the book.
Development only: needs the `playwright` package and a Chromium build.
"""

from __future__ import annotations

import argparse
import posixpath
import tempfile
import zipfile
from pathlib import Path

from lxml import etree


def spine(z: zipfile.ZipFile) -> list[str]:
    container = etree.fromstring(z.read("META-INF/container.xml"))
    opf_path = container.find(".//{*}rootfile").get("full-path")
    opf = etree.fromstring(z.read(opf_path))
    base = posixpath.dirname(opf_path)
    items = {i.get("id"): i.get("href") for i in opf.iterfind(".//{*}manifest/{*}item")}
    return [
        posixpath.join(base, items[r.get("idref")]) for r in opf.iterfind(".//{*}spine/{*}itemref")
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("epub", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--pages", default="1,2,3,4,5")
    parser.add_argument("--width", type=int, default=600)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument(
        "--chromium", help="path to a Chromium binary, if Playwright's own is absent"
    )
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(args.epub) as z:
        z.extractall(tmp)
        docs = spine(z)
        wanted = [int(n) for n in args.pages.split(",")]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=args.chromium)
            page = browser.new_page(viewport={"width": args.width, "height": args.height})
            for n in wanted:
                if not 1 <= n <= len(docs):
                    continue
                page.goto((Path(tmp) / docs[n - 1]).as_uri())
                target = args.out / f"{n:03d}-{Path(docs[n - 1]).stem}.png"
                page.screenshot(path=str(target))
                print("wrote", target)
            browser.close()


if __name__ == "__main__":
    main()
