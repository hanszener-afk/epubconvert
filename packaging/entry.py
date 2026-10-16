"""Entry point for the frozen (PyInstaller) build."""

import sys

from novel2epub.cli import main

if __name__ == "__main__":
    sys.exit(main())
