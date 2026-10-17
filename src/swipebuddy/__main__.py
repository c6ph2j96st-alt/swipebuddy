"""Entry point for ``python -m swipebuddy`` and the PyInstaller build."""
from __future__ import annotations

# Absolute import: PyInstaller runs this file as a top-level script.
from swipebuddy.app import main

if __name__ == "__main__":
    raise SystemExit(main())
