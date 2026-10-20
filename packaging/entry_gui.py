"""Entry point for the frozen (PyInstaller) windowed program."""

import sys

from novel2epub.gui import main

if __name__ == "__main__":
    sys.exit(main())
