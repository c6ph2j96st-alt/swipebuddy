"""Background worker: owns the browser and runs the engine in one QThread.

Playwright's sync API is thread-bound, so every browser call happens inside
this worker's thread. The GUI talks to it via thread-safe methods and Qt signals.
"""
from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..config import AppConfig
from ..engine import Confirm, Decision, Error, EngineEvent, Skip, Status, Stopped, SwipeEngine
from ..logging_setup import log_event
from ..paths import profile_dir

TXT_BROWSER_OPEN = "Webbläsaren är öppen – logga in på Tinder och tryck Start"
TXT_BROWSER_FAIL = "Kunde inte öppna webbläsaren"
TXT_COMPONENTS_FAIL = "Kunde inte starta bedömningsmodulerna"

BrowserFactory = Callable[[AppConfig], Any]
ComponentsFactory = Callable[[AppConfig], tuple[Any, Any, Any, Callable[[], None]]]
EngineFactory = Callable[..., Any]


def default_browser_factory(cfg: AppConfig) -> Any:
    """Create the real TinderBrowser (not yet started)."""
    from ..browser import TinderBrowser

    return TinderBrowser(profile_dir(), cfg.browser_channel)


def default_components_factory(cfg: AppConfig) -> tuple[Any, Any, Any, Callable[[], None]]:
    """Build (detector, judge, stats, cleanup) from a config snapshot."""
    from ..detection import OnnxPersonDetector, default_model_path
    from ..stats import StatsStore
    from ..vlm import OllamaClient, VlmJudge

    detector = OnnxPersonDetector(default_model_path())
    client = OllamaClient(cfg.ollama_url)
    return detector, VlmJudge(client, cfg.vlm_model), StatsStore(), client.close


def default_engine_factory(cfg, browser, detector, judge, stats, events, commands) -> Any:
    """Create the real SwipeEngine."""
    return SwipeEngine(cfg, browser, detector, judge, stats, events, commands)


@dataclass(frozen=True)
class _Open:
    cfg: AppConfig


@dataclass(frozen=True)
class _Run:
    cfg: AppConfig
    stop: threading.Event


@dataclass(frozen=True)
class _Close:
    pass


@dataclass(frozen=True)
class _Shutdown:
    pass


class SessionWorker(QObject):
    """Command loop living in its own QThread; re-emits engine events as signals."""

    event = Signal(object)  # EngineEvent
    browser_open_changed = Signal(bool)

    def __init__(
        self,
        browser_factory: BrowserFactory = default_browser_factory,
        components_factory: ComponentsFactory = default_components_factory,
        engine_factory: EngineFactory = default_engine_factory,
    ) -> None:
        super().__init__()
        self._browser_factory = browser_factory
        self._components_factory = components_factory
        self._engine_factory = engine_factory
        self._cmds: queue.Queue = queue.Queue()
        self._engine_cmds: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._browser: Any = None
        self._thread: QThread | None = None

    # -- GUI-thread API ------------------------------------------------
    def start(self) -> None:
        """Start the worker thread (idempotent)."""
        if self._thread is not None:
            return
        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._loop)
        self._thread.start()

    def open_browser(self, cfg: AppConfig) -> None:
        self._cmds.put(_Open(cfg))

    def run_engine(self, cfg: AppConfig) -> None:
        """Queue an engine run using a config snapshot."""
        self._stop = threading.Event()
        self._cmds.put(_Run(cfg, self._stop))

    def stop_engine(self) -> None:
        self._stop.set()

    def close_browser(self) -> None:
        """Stop a running engine, then close the browser (a later Open/Run reopens it)."""
        self._stop.set()
        self._cmds.put(_Close())

    def send_confirm(self, decision: Decision) -> None:
        self._engine_cmds.put(Confirm(decision))

    def send_skip(self) -> None:
        self._engine_cmds.put(Skip())

    def shutdown(self, timeout_ms: int = 10000) -> bool:
        """Stop everything, close the browser and join the thread."""
        self._stop.set()
        self._cmds.put(_Shutdown())
        if self._thread is None:
            return True
        ok = self._thread.wait(timeout_ms)
        self._thread = None
        return bool(ok)

    # -- worker-thread side --------------------------------------------
    def _emit(self, ev: EngineEvent) -> None:
        self.event.emit(ev)

    def _ensure_browser(self, cfg: AppConfig) -> bool:
        if self._browser is not None:
            return True
        try:
            browser = self._browser_factory(cfg)
            browser.start()
        except Exception as exc:  # noqa: BLE001 - class name only
            log_event("error", error_type=type(exc).__name__)
            self._emit(Error(f"{TXT_BROWSER_FAIL} ({type(exc).__name__})"))
            return False
        self._browser = browser
        self.browser_open_changed.emit(True)
        return True

    @Slot()
    def _loop(self) -> None:
        try:
            while True:
                cmd = self._cmds.get()
                if isinstance(cmd, _Shutdown):
                    break
                if isinstance(cmd, _Open):
                    if self._ensure_browser(cmd.cfg):
                        self._emit(Status(TXT_BROWSER_OPEN))
                    else:
                        self._emit(Stopped())
                elif isinstance(cmd, _Run):
                    self._run(cmd)
                elif isinstance(cmd, _Close):
                    # Runs after any engine run has returned (same thread).
                    was_open = self._browser is not None
                    self._close_browser()
                    if not was_open:
                        self.browser_open_changed.emit(False)
        finally:
            self._close_browser()
            QThread.currentThread().quit()

    def _close_browser(self) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
            self.browser_open_changed.emit(False)

    def _run(self, cmd: _Run) -> None:
        if cmd.stop.is_set():
            self._emit(Stopped())
            return
        if not self._ensure_browser(cmd.cfg):
            self._emit(Stopped())
            return
        cleanup: Callable[[], None] = lambda: None  # noqa: E731
        try:
            detector, judge, stats, cleanup = self._components_factory(cmd.cfg)
            while True:  # discard stale commands from a previous run
                try:
                    self._engine_cmds.get_nowait()
                except queue.Empty:
                    break
            engine = self._engine_factory(
                cmd.cfg, self._browser, detector, judge, stats, self._emit, self._engine_cmds
            )
        except Exception as exc:  # noqa: BLE001
            log_event("error", error_type=type(exc).__name__)
            self._emit(Error(f"{TXT_COMPONENTS_FAIL} ({type(exc).__name__})"))
            self._emit(Stopped())
            return
        try:
            engine.run(cmd.stop)
        finally:
            try:
                cleanup()
            except Exception:  # noqa: BLE001
                pass
