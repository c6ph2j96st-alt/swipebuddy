"""Main window: tabs, worker wiring and status bar."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QMainWindow, QTabWidget

from ..config import load_config
from ..engine import Stopped, Swiped
from ..logging_setup import log_event
from ..stats import StatsStore
from .live_tab import LiveTab
from .prefs_tab import PrefsTab
from .settings_tab import SettingsTab
from .setup_dialog import SetupDialog
from .worker import SessionWorker

SHUTDOWN_TIMEOUT_MS = 10000


class MainWindow(QMainWindow):
    """Hosts the Live, Preferenser and Inställningar tabs."""

    def __init__(
        self,
        worker_factory: Callable[[], Any] = SessionWorker,
        stats_factory: Callable[[], Any] = StatsStore,
    ) -> None:
        super().__init__()
        self.setWindowTitle("SwipeBuddy")
        self.resize(760, 640)
        self._worker = worker_factory()
        self._worker_started = False
        self._stats_factory = stats_factory

        self.live = LiveTab()
        self.prefs = PrefsTab()
        self.settings = SettingsTab()
        tabs = QTabWidget()
        tabs.addTab(self.live, "Live")
        tabs.addTab(self.prefs, "Preferenser")
        tabs.addTab(self.settings, "Inställningar")
        self.setCentralWidget(tabs)

        action = QAction("Kontrollera installation", self)
        action.triggered.connect(self.show_setup)
        self.menuBar().addMenu("Verktyg").addAction(action)

        self.live.open_browser.connect(self._open_browser)
        self.live.start.connect(self._start)
        self.live.stop.connect(self._worker.stop_engine)
        self.live.confirm.connect(self._worker.send_confirm)
        self.live.skip.connect(self._worker.send_skip)
        self._worker.event.connect(self._on_event)
        self.settings.saved.connect(self._refresh_mode)
        self._refresh_mode()
        self._refresh_counts()

    # -- helpers -------------------------------------------------------
    def _ensure_worker(self) -> None:
        if not self._worker_started:
            self._worker.start()
            self._worker_started = True

    def _refresh_mode(self) -> None:
        cfg = load_config()
        self.live.set_mode(cfg.mode, cfg.threshold)

    def _refresh_counts(self) -> None:
        try:
            t = self._stats_factory().today()
            cap = load_config().daily_cap
        except Exception:  # noqa: BLE001 - status bar is cosmetic
            return
        self._set_counts(t["like"], t["pass"], cap)

    def _set_counts(self, like: int, pass_: int, cap: int) -> None:
        self.live.update_counts(like, pass_, cap)
        self.statusBar().showMessage(f"Idag: {like} like, {pass_} pass, tak {cap}")

    # -- actions -------------------------------------------------------
    def _open_browser(self) -> None:
        self._ensure_worker()
        self._worker.open_browser(load_config())

    def _start(self) -> None:
        self._ensure_worker()
        cfg = load_config()  # fresh snapshot: settings apply from this Start
        self.live.set_mode(cfg.mode, cfg.threshold)
        self.live.set_running(True)
        self._refresh_counts()
        self._worker.run_engine(cfg)

    def _on_event(self, ev: object) -> None:
        self.live.handle_event(ev)
        if isinstance(ev, Swiped):
            self._set_counts(ev.today_like, ev.today_pass, ev.cap)
        elif isinstance(ev, Stopped):
            self._refresh_counts()

    def show_setup(self) -> None:
        """Open the installation check dialog."""
        SetupDialog(load_config(), parent=self).exec()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        self._worker.stop_engine()
        self._worker.shutdown(SHUTDOWN_TIMEOUT_MS)
        log_event("app_stop")
        super().closeEvent(event)
