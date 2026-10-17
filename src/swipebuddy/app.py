"""Application entry point."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

SELFTEST_MODULES = (
    "PIL", "httpx", "numpy", "onnxruntime", "playwright.sync_api",
    "swipebuddy.browser", "swipebuddy.detection", "swipebuddy.vlm", "swipebuddy.engine",
    "swipebuddy.setup_check", "swipebuddy.gui.worker", "swipebuddy.gui.main_window",
    "swipebuddy.gui.setup_dialog",
)


def resource_path(relative: str) -> Path:
    """Resolve a bundled resource (PyInstaller aware)."""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parents[2]
    return root / relative


def _app_icon():
    from PySide6.QtGui import QIcon

    p = resource_path("assets/icon.png")
    return QIcon(str(p)) if p.is_file() else QIcon()


def _selftest() -> int:
    """Import everything and build the window without starting the worker."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if not os.environ.get("SWIPEBUDDY_HOME"):
        os.environ["SWIPEBUDDY_HOME"] = tempfile.mkdtemp(prefix="swipebuddy-selftest-")
    for name in SELFTEST_MODULES:
        importlib.import_module(name)
    from PySide6.QtWidgets import QApplication

    from .gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.close()
    del app
    return 0


def _needs_setup(cfg) -> bool:
    from . import setup_check
    from .vlm import OllamaClient

    if not cfg.disclaimer_accepted:
        return True
    client = OllamaClient(cfg.ollama_url, timeout_s=10)
    try:
        return not setup_check.is_ready(setup_check.collect_status(cfg, client))
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    """Start SwipeBuddy. ``--selftest`` verifies imports and window construction."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        return _selftest()

    from PySide6.QtWidgets import QApplication

    from .config import load_config
    from .gui.main_window import MainWindow
    from .gui.setup_dialog import SetupDialog
    from .logging_setup import log_event, setup_logging

    setup_logging()
    log_event("app_start")
    app = QApplication.instance() or QApplication([sys.argv[0], *args])
    app.setWindowIcon(_app_icon())
    cfg = load_config()
    if _needs_setup(cfg) and SetupDialog(cfg).exec() != SetupDialog.DialogCode.Accepted:
        log_event("app_stop")
        return 0
    window = MainWindow()
    window.show()
    return app.exec()
