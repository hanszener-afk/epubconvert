"""Front ends and the EPUB writer must not know about each other."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).parent.parent / "src" / "novel2epub"

FORBIDDEN = {
    "epub": ("novel2epub.docx", "novel2epub.pdf"),
    "docx": ("novel2epub.epub", "novel2epub.pdf"),
    "pdf": ("novel2epub.epub", "novel2epub.docx"),
}


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("subpackage", sorted(FORBIDDEN))
def test_no_cross_imports(subpackage: str) -> None:
    directory = PKG / subpackage
    if not directory.exists():
        pytest.skip(f"{subpackage} not written yet")
    for path in directory.rglob("*.py"):
        for name in imports(path):
            assert not name.startswith(FORBIDDEN[subpackage]), f"{path.name} imports {name}"
