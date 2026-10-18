"""Main window: tabs, worker wiring and status bar."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from ..config import load_config
from ..engine import Stopped, Swiped
from ..logging_setup import log_event
from ..login_browser import close_profile_edge, launch_plain_edge, profile_in_use
from ..paths import profile_dir
from ..stats import StatsStore
from .live_tab import TXT_LOGIN_CLOSING, TXT_LOGIN_DONE, TXT_LOGIN_HELP, LiveTab
from .prefs_tab import PrefsTab
from .settings_tab import SettingsTab
from .setup_dialog import SetupDialog
from .worker import SessionWorker

SHUTDOWN_TIMEOUT_MS = 10000
CLOSE_WAIT_MS = 15000
LOGIN_POLL_MS = 1000
# msedge.exe hands off to a broker process and exits at once, so liveness is
# judged from the profile lock; give Edge this long to take the lock.
LOGIN_GRACE_S = 10.0


class _CloseSignals(QObject):
    done = Signal(bool)


class _CloseJob(QRunnable):
    """Runs the blocking Edge close in a pool thread and reports success."""

    def __init__(self, fn: Callable[[], bool]) -> None:
        super().__init__()
        self.signals = _CloseSignals()
        self._fn = fn

    def run(self) -> None:
        try:
            ok = bool(self._fn())
        except Exception:  # noqa: BLE001
            ok = False
        try:
            self.signals.done.emit(ok)
        except RuntimeError:
            pass  # the window was closed meanwhile


class MainWindow(QMainWindow):
    """Hosts the Live, Preferenser and Inställningar tabs."""

    def __init__(
        self,
        worker_factory: Callable[[], Any] = SessionWorker,
        stats_factory: Callable[[], Any] = StatsStore,
        login_launcher: Callable[..., Any] = launch_plain_edge,
        edge_closer: Callable[..., bool] = close_profile_edge,
    ) -> None:
        super().__init__()
        self.setWindowTitle("SwipeBuddy")
        self.resize(760, 640)
        self._worker = worker_factory()
        self._worker_started = False
        self._stats_factory = stats_factory
        self._login_launcher = login_launcher
        self._edge_closer = edge_closer
        self._pool = QThreadPool()
        self._close_job: _CloseJob | None = None
        self._login_started = 0.0
        self._login_active = False
        self._login_grace_s = LOGIN_GRACE_S
        self._profile_busy: Callable[[Any], bool] = profile_in_use
        self._browser_open = False
        self._engine_running = False
        self._waiting_close = False
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self._on_close_timeout)
        self._login_timer = QTimer(self)
        self._login_timer.setInterval(LOGIN_POLL_MS)
        self._login_timer.timeout.connect(self._poll_login)

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
        self.live.login.connect(self._login)
        self.live.start.connect(self._start)
        self.live.stop.connect(self._worker.stop_engine)
        self.live.confirm.connect(self._worker.send_confirm)
        self.live.skip.connect(self._worker.send_skip)
        self._worker.event.connect(self._on_event)
        self._worker.browser_open_changed.connect(self._on_browser_open_changed)
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
        if self._close_login_first(self._do_open_browser):
            return
        self._do_open_browser()

    def _do_open_browser(self) -> None:
        self._ensure_worker()
        self._worker.open_browser(load_config())

    def _start(self) -> None:
        if self._close_login_first(self._do_start):
            return
        self._do_start()

    def _close_login_first(self, proceed: Callable[[], None]) -> bool:
        """If the login Edge may still hold the profile, close it in the background.

        Returns True when a close is running (``proceed`` runs after success).
        """
        if self._close_job is not None:
            return True
        if not (self._login_active or self._profile_busy(profile_dir())):
            return False
        self._login_timer.stop()
        self.live.set_closing(True)
        self.live.show_info(TXT_LOGIN_CLOSING)
        self.live.status_label.setText(TXT_LOGIN_CLOSING)
        job = _CloseJob(lambda: self._edge_closer(profile_dir()))
        job.signals.done.connect(lambda ok: self._on_login_closed(ok, proceed))
        self._close_job = job
        self._pool.start(job)
        return True

    def _on_login_closed(self, ok: bool, proceed: Callable[[], None]) -> None:
        self._close_job = None
        self.live.set_closing(False)
        if not ok:
            if self._login_active:
                self._login_timer.start()
            QMessageBox.warning(
                self,
                "Logga in",
                "Edge-fönstret för inloggningen är fortfarande öppet. Stäng det och försök igen.",
            )
            return
        self._login_active = False
        self.live.set_login_active(False)
        self.live.banner.hide()
        self.live.status_label.setText("Redo")
        proceed()

    def _do_start(self) -> None:
        self._ensure_worker()
        cfg = load_config()  # fresh snapshot: settings apply from this Start
        self.live.set_mode(cfg.mode, cfg.threshold)
        self.live.set_running(True)
        self._engine_running = True
        self._refresh_counts()
        self._worker.run_engine(cfg)

    def _on_event(self, ev: object) -> None:
        self.live.handle_event(ev)
        if isinstance(ev, Swiped):
            self._set_counts(ev.today_like, ev.today_pass, ev.cap)
        elif isinstance(ev, Stopped):
            self._engine_running = False
            self._refresh_counts()

    # -- plain-Edge login ------------------------------------------------
    def _on_browser_open_changed(self, is_open: bool) -> None:
        self._browser_open = is_open
        if not is_open and self._waiting_close:
            self._finish_login_launch()

    def _on_close_timeout(self) -> None:
        if self._waiting_close:
            self._finish_login_launch()

    def _login(self) -> None:
        """Close the automated browser, then open an ordinary Edge for manual login."""
        if load_config().browser_channel != "msedge":
            QMessageBox.information(
                self,
                "Logga in",
                "Inloggning via vanlig Edge kräver att browser_channel är 'msedge' i inställningarna "
                "– annars används inte den sparade inloggningen.",
            )
            return
        if self._engine_running:
            ans = QMessageBox.question(
                self, "Logga in", "Körningen pågår. Stoppa den och fortsätt till inloggning?"
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._login_active = True
        self.live.set_login_active(True)
        if self._browser_open or self._engine_running:
            self._waiting_close = True
            self._ensure_worker()
            self._worker.close_browser()
            self._close_timer.start(CLOSE_WAIT_MS)
        else:
            self._finish_login_launch()

    def _finish_login_launch(self) -> None:
        self._waiting_close = False
        self._close_timer.stop()
        # Edge from an earlier login may linger and hold the profile: close it first.
        if self._profile_busy(profile_dir()) and self._close_login_first(self._relaunch_login):
            return
        self._launch_login_now()

    def _relaunch_login(self) -> None:
        self._login_active = True
        self.live.set_login_active(True)
        self._launch_login_now()

    def _launch_login_now(self) -> None:
        try:
            self._login_launcher(profile_dir())
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "Microsoft Edge saknas",
                "Inloggningen kräver Microsoft Edge, som inte hittades. Installera Edge och försök igen.",
            )
            self._login_active = False
            self.live.set_login_active(False)
            return
        except Exception as exc:  # noqa: BLE001 - class name only
            log_event("error", error_type=type(exc).__name__)
            QMessageBox.warning(self, "Logga in", "Kunde inte öppna Edge.")
            self._login_active = False
            self.live.set_login_active(False)
            return
        log_event("login_browser")
        self.live.show_info(TXT_LOGIN_HELP)
        self._login_started = time.monotonic()
        self._login_timer.start()

    def _poll_login(self) -> None:
        in_grace = time.monotonic() - self._login_started < self._login_grace_s
        if in_grace or self._profile_busy(profile_dir()):
            return
        self._login_timer.stop()
        self._login_active = False
        self.live.set_login_active(False)
        self.live.show_info(TXT_LOGIN_DONE)

    def show_setup(self) -> None:
        """Open the installation check dialog."""
        SetupDialog(load_config(), parent=self).exec()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        self._login_timer.stop()
        self._close_timer.stop()
        self._pool.waitForDone(2000)
        self._worker.stop_engine()
        self._worker.shutdown(SHUTDOWN_TIMEOUT_MS)
        log_event("app_stop")
        super().closeEvent(event)
