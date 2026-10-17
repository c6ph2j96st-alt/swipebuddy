"""GUI smoke tests (offscreen) with a fake worker."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from swipebuddy import app as app_mod
from swipebuddy import setup_check as sc
from swipebuddy.config import AppConfig, load_config, save_config
from swipebuddy.engine import (
    AwaitingConfirm,
    CapReached,
    Error,
    Evaluating,
    NeedsUser,
    Stopped,
    Swiped,
    VerdictReady,
)
from swipebuddy.gui.main_window import MainWindow
from swipebuddy.gui.setup_dialog import SetupDialog
from swipebuddy.vlm import Verdict


class FakeWorker(QObject):
    event = Signal(object)
    browser_open_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    def start(self) -> None:
        self.calls.append(("start",))

    def open_browser(self, cfg) -> None:
        self.calls.append(("open",))

    def run_engine(self, cfg) -> None:
        self.calls.append(("run", cfg.mode))

    def stop_engine(self) -> None:
        self.calls.append(("stop",))

    def close_browser(self) -> None:
        self.calls.append(("close",))

    def send_confirm(self, d) -> None:
        self.calls.append(("confirm", d))

    def send_skip(self) -> None:
        self.calls.append(("skip",))

    def shutdown(self, timeout_ms: int = 0) -> bool:
        self.calls.append(("shutdown",))
        return True


class FakeStats:
    def today(self) -> dict[str, int]:
        return {"like": 2, "pass": 3}


@pytest.fixture
def win(qtbot):
    w = MainWindow(FakeWorker, FakeStats)
    qtbot.addWidget(w)
    return w


def test_window_constructs_with_status_bar(win) -> None:
    assert win.windowTitle() == "SwipeBuddy"
    assert "2 like" in win.statusBar().currentMessage()
    assert win.live.mode_label.text() == "Läge: Förslag"


def test_event_flow_updates_ui(win) -> None:
    live, w = win.live, win._worker
    w.event.emit(Evaluating(1, 3))
    assert "profil 1" in live.status_label.text()
    assert live.reason_label.text() == ""
    v = Verdict("like", 82, "Trevligt leende", True)
    w.event.emit(VerdictReady(v, "like", "suggest"))
    assert live.decision_label.text() == "LIKE"
    assert live.score_bar.value() == 82
    assert "tröskel 70" in live.score_bar.format()
    assert live.reason_label.text() == "Trevligt leende"
    assert not live.btn_like.isEnabled()
    w.event.emit(AwaitingConfirm("like"))
    assert live.btn_like.isEnabled() and live.btn_pass.isEnabled() and live.btn_skip.isEnabled()
    w.event.emit(Swiped("like", 3, 3, 6, 100))
    assert not live.btn_like.isEnabled()
    assert "3" in live.counter_label.text()
    assert "3 like, 3 pass, tak 100" in win.statusBar().currentMessage()
    w.event.emit(Evaluating(2, 1))
    assert live.reason_label.text() == "" and live.decision_label.text() == "–"
    w.event.emit(VerdictReady(Verdict("pass", 10, "x", True), "pass", "auto"))
    assert live.decision_label.text() == "PASS"
    assert live.mode_label.text() == "Läge: AUTO"


def test_banners_and_stop(win) -> None:
    live, w = win.live, win._worker
    w.event.emit(NeedsUser("inloggning krävs"))
    assert not live.banner.isHidden() and "inloggning krävs" in live.banner.text()
    w.event.emit(Evaluating(1, 1))
    assert live.banner.isHidden()
    w.event.emit(CapReached(100, 100))
    assert "100" in live.banner.text()
    w.event.emit(Error("Ett oväntat fel"))
    assert live.banner.text() == "Ett oväntat fel"
    live.set_running(True)
    w.event.emit(Stopped())
    assert live.btn_start.isEnabled() and not live.btn_stop.isEnabled()
    assert live.status_label.text() == "Stoppad"


def test_buttons_call_worker(win, qtbot) -> None:
    w = win._worker
    w.event.emit(AwaitingConfirm("pass"))
    qtbot.mouseClick(win.live.btn_like, qtbot_left())
    qtbot.mouseClick(win.live.btn_pass, qtbot_left())
    qtbot.mouseClick(win.live.btn_skip, qtbot_left())
    assert ("confirm", "like") in w.calls and ("confirm", "pass") in w.calls and ("skip",) in w.calls
    qtbot.mouseClick(win.live.btn_start, qtbot_left())
    assert ("start",) in w.calls and ("run", "suggest") in w.calls
    qtbot.mouseClick(win.live.btn_stop, qtbot_left())
    assert ("stop",) in w.calls


def qtbot_left():
    from PySide6.QtCore import Qt

    return Qt.MouseButton.LeftButton


def test_close_shuts_down_worker(win) -> None:
    win.close()
    assert ("stop",) in win._worker.calls and ("shutdown",) in win._worker.calls


def test_prefs_roundtrip(qtbot) -> None:
    from swipebuddy.gui.prefs_tab import PrefsTab

    t = PrefsTab()
    qtbot.addWidget(t)
    t.free_text.setPlainText("gillar hundar")
    t.checks["glasses_ok"].setChecked(False)
    t.age_min.setValue(25)
    t.age_max.setValue(35)
    t.save()
    p = load_config().preferences
    assert p.free_text == "gillar hundar" and p.attributes["glasses_ok"] is False
    assert (p.age_hint_min, p.age_hint_max) == (25, 35)
    t.age_min.setValue(t.age_min.minimum())
    t.save()
    assert load_config().preferences.age_hint_min is None
    t2 = PrefsTab()
    qtbot.addWidget(t2)
    assert t2.free_text.toPlainText() == "gillar hundar" and t2.age_max.value() == 35
    t2.free_text.setPlainText("x" * 3000)
    assert len(t2.free_text.toPlainText()) == 2000


def test_settings_clamp_and_auto_confirmation(qtbot, monkeypatch) -> None:
    from swipebuddy.gui.settings_tab import SettingsTab

    t = SettingsTab()
    qtbot.addWidget(t)
    t.delay_min.setValue(10)
    t.delay_max.setValue(5)
    t.threshold.setValue(55)
    assert t.slider.value() == 55
    t.model.setCurrentText("qwen2.5vl:3b")
    t.save()
    c = load_config()
    assert c.delay_min_s == 10 and c.delay_max_s == 10 and c.threshold == 55
    assert c.vlm_model == "qwen2.5vl:3b"
    assert t.delay_max.value() == 10

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.No)
    t.radio_auto.setChecked(True)
    assert t.radio_suggest.isChecked()
    t.save()
    assert load_config().mode == "suggest"

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Yes)
    t.radio_auto.setChecked(True)
    assert t.radio_auto.isChecked()
    t.save()
    assert load_config().mode == "auto"


def _status(**kw) -> sc.SetupStatus:
    base = dict(ollama_running=True, ollama_version="0.5", model_present=True,
                detector_present=True, gpu=sc.GpuInfo("RTX", 12000), edge=True)
    base.update(kw)
    return sc.SetupStatus(**base)


def test_setup_dialog_done_gating(qtbot) -> None:
    st = {"v": _status(detector_present=False)}
    d = SetupDialog(AppConfig(), lambda cfg: st["v"], auto_refresh=False)
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: d._marks["ollama"].text() == "✓", timeout=3000)
    assert not d.btn_done.isEnabled()  # not ready, disclaimer unchecked
    d.disclaimer_check.setChecked(True)
    assert not d.btn_done.isEnabled()  # detector missing
    assert d._marks["detector"].text() == "✗"
    st["v"] = _status()
    d.refresh()
    qtbot.waitUntil(lambda: d.btn_done.isEnabled(), timeout=3000)
    d.disclaimer_check.setChecked(False)
    assert not d.btn_done.isEnabled()
    d.disclaimer_check.setChecked(True)
    d.btn_done.click()
    assert load_config().disclaimer_accepted is True
    d._pool.waitForDone(2000)


def test_setup_dialog_gpu_only_warns(qtbot) -> None:
    d = SetupDialog(AppConfig(), lambda cfg: _status(gpu=None), auto_refresh=False)
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: d._marks["ollama"].text() == "✓", timeout=3000)
    d.disclaimer_check.setChecked(True)
    assert d.btn_done.isEnabled()
    assert "Ingen NVIDIA-GPU" in d._texts["gpu"].text()
    d._pool.waitForDone(2000)


def test_selftest_returns_zero(qapp, capsys) -> None:
    assert app_mod.main(["--selftest"]) == 0
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    save_config(load_config())  # config module still usable afterwards


class FakeProc:
    def __init__(self) -> None:
        self.alive = True

    def poll(self):
        return None if self.alive else 0


def _login_win(qtbot, launcher):
    w = MainWindow(FakeWorker, FakeStats, login_launcher=launcher)
    qtbot.addWidget(w)
    w._login_timer.setInterval(20)
    w._login_grace_s = 0.0
    return w


def test_login_button_flow(qtbot) -> None:
    proc = FakeProc()
    launched: list[object] = []

    def launcher(path):
        launched.append(path)
        return proc

    win = _login_win(qtbot, launcher)
    live, worker = win.live, win._worker
    worker.browser_open_changed.emit(True)
    qtbot.mouseClick(live.btn_login, qtbot_left())
    assert ("close",) in worker.calls
    assert not launched  # waits for the browser to close
    assert not live.btn_start.isEnabled()
    worker.browser_open_changed.emit(False)
    assert len(launched) == 1
    assert "Logga in på Tinder" in live.banner.text()
    for b in (live.btn_start, live.btn_open, live.btn_login):
        assert not b.isEnabled()
    proc.alive = False
    qtbot.waitUntil(lambda: live.btn_start.isEnabled(), timeout=3000)
    assert live.btn_open.isEnabled() and live.btn_login.isEnabled()
    assert "sparad" in live.banner.text()


def test_login_without_open_browser_launches_immediately(qtbot) -> None:
    launched: list[object] = []
    win = _login_win(qtbot, lambda p: launched.append(p) or FakeProc())
    qtbot.mouseClick(win.live.btn_login, qtbot_left())
    assert len(launched) == 1


def test_login_confirms_when_running(qtbot, monkeypatch) -> None:
    launched: list[object] = []
    win = _login_win(qtbot, lambda p: launched.append(p) or FakeProc())
    win._start()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    win._login()
    assert not launched and ("close",) not in win._worker.calls
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._login()
    assert not launched and ("close",) in win._worker.calls
    win._on_event(Stopped())
    win._worker.browser_open_changed.emit(False)
    assert len(launched) == 1


def test_login_edge_missing_shows_message(qtbot, monkeypatch) -> None:
    shown: list[str] = []

    def launcher(path):
        raise FileNotFoundError("x")

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[1]))
    win = _login_win(qtbot, launcher)
    qtbot.mouseClick(win.live.btn_login, qtbot_left())
    assert shown and "Edge" in shown[0]
    assert win.live.btn_start.isEnabled()


def test_login_requires_msedge_channel(qtbot, monkeypatch) -> None:
    cfg = load_config()
    cfg.browser_channel = "chromium"
    save_config(cfg)
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    launched: list[object] = []
    win = _login_win(qtbot, lambda p: launched.append(p))
    win._login()
    assert shown and "msedge" in shown[0] and not launched


def test_needs_login_banner_mentions_button(win) -> None:
    win._worker.event.emit(NeedsUser("inloggning krävs"))
    assert "Logga in (vanlig Edge)" in win.live.banner.text()


def test_login_waits_for_profile_lock_after_launcher_exits(qtbot) -> None:
    """Real msedge.exe exits at once; the window stays busy while the profile is locked."""
    busy = {"v": True}

    class ExitedProc:
        def poll(self):
            return 0

    win = _login_win(qtbot, lambda p: ExitedProc())
    win._profile_busy = lambda p: busy["v"] and win._login_proc is not None
    win._login()
    qtbot.wait(100)
    assert not win.live.btn_start.isEnabled()  # Edge still holds the profile
    busy["v"] = False
    qtbot.waitUntil(lambda: win.live.btn_start.isEnabled(), timeout=2000)
