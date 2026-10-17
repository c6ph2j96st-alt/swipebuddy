"""Tests for the SessionWorker thread loop with fake browser/engine."""
from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QThread

from swipebuddy.config import AppConfig
from swipebuddy.engine import Confirm, Error, Status, Stopped
from swipebuddy.gui.worker import SessionWorker


class FakeBrowser:
    def __init__(self, log: list) -> None:
        self.log = log
        self.threads: set[int] = set()

    def start(self) -> None:
        self.threads.add(threading.get_ident())
        self.log.append("start")

    def close(self) -> None:
        self.threads.add(threading.get_ident())
        self.log.append("close")


class FakeEngine:
    def __init__(self, emit, commands, log) -> None:
        self.emit, self.commands, self.log = emit, commands, log

    def run(self, stop: threading.Event) -> None:
        self.log.append(("run", threading.get_ident()))
        self.emit(Status("running"))
        try:
            self.log.append(("cmd", self.commands.get(timeout=0.5)))
        except Exception:  # noqa: BLE001 - no command sent in this test
            pass
        stop.wait(5)
        self.emit(Stopped())


def _make(log: list, boom: bool = False):
    browser = FakeBrowser(log)
    engines: list[FakeEngine] = []

    def components(cfg):
        if boom:
            raise RuntimeError("secret detail")
        return object(), object(), object(), lambda: log.append("cleanup")

    def engine_factory(cfg, br, det, judge, stats, events, commands):
        e = FakeEngine(events, commands, log)
        engines.append(e)
        return e

    return SessionWorker(lambda cfg: browser, components, engine_factory), browser


def test_open_run_stop_shutdown(qtbot) -> None:
    log: list[Any] = []
    w, browser = _make(log)
    events: list[Any] = []
    w.event.connect(events.append)
    w.start()
    main = threading.get_ident()
    cfg = AppConfig()
    w.open_browser(cfg)
    qtbot.waitUntil(lambda: any(isinstance(e, Status) for e in events), timeout=5000)
    w.run_engine(cfg)
    qtbot.waitUntil(lambda: any(getattr(e, "text", "") == "running" for e in events), timeout=5000)
    w.send_confirm("like")
    qtbot.waitUntil(lambda: any(isinstance(x, tuple) and x[0] == "cmd" for x in log), timeout=5000)
    w.stop_engine()
    qtbot.waitUntil(lambda: any(isinstance(e, Stopped) for e in events), timeout=5000)
    assert w.shutdown(5000)
    assert log.count("start") == 1 and log.count("close") == 1
    assert "cleanup" in log
    assert ("cmd", Confirm("like")) in log
    assert main not in browser.threads
    assert len(browser.threads) == 1  # start and close in the same worker thread


def test_run_without_open_starts_browser_and_browser_reused(qtbot) -> None:
    log: list[Any] = []
    w, _ = _make(log)
    events: list[Any] = []
    w.event.connect(events.append)
    w.start()
    cfg = AppConfig()
    for i in range(2):
        w.run_engine(cfg)
        qtbot.waitUntil(lambda: any(getattr(e, "text", "") == "running" for e in events), timeout=5000)
        w.stop_engine()
        qtbot.waitUntil(lambda: any(isinstance(e, Stopped) for e in events), timeout=5000)
        events.clear()
    assert w.shutdown(5000)
    assert log.count("start") == 1 and log.count("close") == 1


def test_component_error_yields_error_and_stopped(qtbot) -> None:
    log: list[Any] = []
    w, _ = _make(log, boom=True)
    events: list[Any] = []
    w.event.connect(events.append)
    w.start()
    w.run_engine(AppConfig())
    qtbot.waitUntil(lambda: any(isinstance(e, Stopped) for e in events), timeout=5000)
    assert isinstance(events[-2], Error)
    assert "secret detail" not in events[-2].message
    assert w.shutdown(5000)
    assert log.count("close") == 1


def test_browser_start_failure(qtbot) -> None:
    class Bad:
        def start(self) -> None:
            raise OSError("nope")

        def close(self) -> None:
            pass

    w = SessionWorker(lambda cfg: Bad(), lambda c: None, lambda *a: None)
    events: list[Any] = []
    w.event.connect(events.append)
    w.start()
    w.open_browser(AppConfig())
    qtbot.waitUntil(lambda: any(isinstance(e, Stopped) for e in events), timeout=5000)
    assert isinstance(events[0], Error)
    assert w.shutdown(5000)
    assert QThread.currentThread() is not None


def test_close_browser_stops_engine_and_reopens(qtbot) -> None:
    log: list[Any] = []
    w, browser = _make(log)
    events: list[Any] = []
    opened: list[bool] = []
    w.event.connect(events.append)
    w.browser_open_changed.connect(opened.append)
    w.start()
    cfg = AppConfig()
    w.run_engine(cfg)
    qtbot.waitUntil(lambda: any(getattr(e, "text", "") == "running" for e in events), timeout=5000)
    w.close_browser()
    qtbot.waitUntil(lambda: opened[-1:] == [False], timeout=5000)
    assert any(isinstance(e, Stopped) for e in events)
    assert log.count("close") == 1
    w.open_browser(cfg)  # reopen after close
    qtbot.waitUntil(lambda: opened[-1:] == [True], timeout=5000)
    assert log.count("start") == 2
    w.close_browser()
    w.close_browser()  # never-open case still reports closed
    qtbot.waitUntil(lambda: opened.count(False) >= 3, timeout=5000)
    assert w.shutdown(5000)
