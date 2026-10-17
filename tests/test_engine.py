"""Engine tests using fakes for every dependency (no Qt, no browser, no network)."""
from __future__ import annotations

import logging
import queue
import random
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from swipebuddy.browser import BrowserState as S
from swipebuddy.config import AppConfig
from swipebuddy.detection import Box
from swipebuddy.engine import (
    AwaitingConfirm, CapReached, Confirm, Error, Evaluating, NeedsUser, Pacer, Paused,
    Skip, Status, Stopped, Swiped, SwipeEngine, VerdictReady, decide, image_hash,
)
from swipebuddy.logging_setup import setup_logging
from swipebuddy.stats import StatsStore
from swipebuddy.vlm import PASS_FALLBACK, Verdict

SECRET = "SECRETREASON-Anna"
READY = S.READY


def v(decision: str = "like", score: int = 90, valid: bool = True, reason: str = SECRET) -> Verdict:
    return Verdict(decision, score, reason, valid)  # type: ignore[arg-type]


def img(shade: int = 100) -> Image.Image:
    return Image.new("RGB", (100, 100), (shade, shade, shade))


class FakeBrowser:
    def __init__(self, states=None, images=None, default_state=READY) -> None:
        self.states = list(states or [])
        self.default_state = default_state
        self.images = images  # callable or None
        self.swipes: list[str] = []
        self.dismissed = 0
        self.captures = 0

    def state(self):
        return self.states.pop(0) if self.states else self.default_state

    def dismiss_modal(self) -> bool:
        self.dismissed += 1
        return True

    def capture_profile_images(self, n: int):
        self.captures += 1
        if self.images:
            return self.images(n, self.captures)
        return [img(self.captures)]

    def swipe(self, d: str) -> None:
        self.swipes.append(d)


class FakeDetector:
    def __init__(self, has_person: bool = True) -> None:
        self.has_person = has_person

    def detect(self, image: Image.Image):
        return [Box(0, 0, 90, 90, 0.9)] if self.has_person else []


class FakeJudge:
    def __init__(self, verdicts=None, exc: Exception | None = None) -> None:
        self.verdicts = list(verdicts or [v()])
        self.exc = exc
        self.calls: list[int] = []

    def judge(self, crops, prefs):
        self.calls.append(len(crops))
        if self.exc:
            raise self.exc
        if not crops:
            return PASS_FALLBACK("none")
        return self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]


class Sleeper:
    def __init__(self) -> None:
        self.total = 0.0
        self.calls: list[float] = []

    def __call__(self, s: float) -> None:
        self.total += s
        self.calls.append(s)


@pytest.fixture
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("SWIPEBUDDY_HOME", str(tmp_path / "home"))
    root = logging.getLogger()
    old = root.level
    h = setup_logging(logging.DEBUG)
    yield tmp_path / "home" / "logs" / "swipebuddy.log"
    root.removeHandler(h)
    h.close()
    root.setLevel(old)


def make(tmp_path: Path, *, mode="auto", cap=100, browser=None, judge=None, detector=None,
         pre=0, **cfgkw):
    cfg = replace(AppConfig(), mode=mode, daily_cap=cap, pause_every_min=1000,
                  pause_every_max=1000, **cfgkw)
    stats = StatsStore(tmp_path / "stats.json", today_fn=lambda: date(2026, 1, 1))
    for _ in range(pre):
        stats.record("pass")
    events: list = []
    cmds: queue.Queue = queue.Queue()
    br = browser or FakeBrowser()
    sl = Sleeper()
    eng = SwipeEngine(cfg, br, detector or FakeDetector(), judge or FakeJudge(), stats,
                      events.append, cmds, rng=random.Random(1), sleep=sl)
    return eng, br, events, cmds, stats, sl


def run(eng) -> threading.Event:
    stop = threading.Event()
    eng.run(stop)
    return stop


# ------------------------------------------------------------------ decide


@pytest.mark.parametrize("score,expected", [(69, "pass"), (70, "like"), (71, "like")])
def test_decide_threshold_boundaries(score, expected):
    assert decide(v("like", score), 70) == expected


def test_decide_invalid_and_pass():
    assert decide(v("like", 100, valid=False), 70) == "pass"
    assert decide(v("pass", 100), 70) == "pass"


# ------------------------------------------------------------------- pacer


def test_pacer_delay_bounds():
    cfg = AppConfig()
    p = Pacer(cfg, random.Random(3), Sleeper())
    for _ in range(500):
        assert cfg.delay_min_s <= p.next_delay() <= cfg.delay_max_s


def test_pacer_wait_slices_and_total():
    sl = Sleeper()
    p = Pacer(AppConfig(), random.Random(3), sl)
    p.wait_before_swipe(threading.Event())
    assert all(0 < c <= 0.25 for c in sl.calls)
    assert 3.0 <= sl.total <= 9.0 + 1e-6


def test_pacer_pause_cadence_and_range():
    cfg = replace(AppConfig(), pause_every_min=3, pause_every_max=5, pause_min_s=10, pause_max_s=20)
    p = Pacer(cfg, random.Random(5), Sleeper())
    stop = threading.Event()
    gaps, since = [], 0
    for _ in range(200):
        p.note_swipe()
        since += 1
        secs = p.maybe_pause(stop)
        if secs:
            assert 10 <= secs <= 20
            gaps.append(since)
            since = 0
    assert gaps and all(3 <= g <= 5 for g in gaps)
    assert len(set(gaps)) > 1  # re-drawn


def test_pacer_interruptible():
    stop = threading.Event()
    calls = []

    def sleep(s):
        calls.append(s)
        stop.set()

    p = Pacer(AppConfig(), random.Random(1), sleep)
    p.wait_before_swipe(stop)
    assert len(calls) == 1
    cfg = replace(AppConfig(), pause_every_min=1, pause_every_max=1)
    stop2 = threading.Event()
    calls2 = []
    p2 = Pacer(cfg, random.Random(1), lambda s: (calls2.append(s), stop2.set()))
    p2.note_swipe()
    assert p2.maybe_pause(stop2) > 0
    assert len(calls2) == 1


# -------------------------------------------------------------- auto flow


def test_auto_swipes_until_cap(tmp_path):
    eng, br, ev, _, stats, _ = make(tmp_path, cap=5, judge=FakeJudge([v("like", 90)]))
    run(eng)
    assert br.swipes == ["like"] * 5
    assert stats.today_total() == 5
    assert isinstance(ev[-2], CapReached) and ev[-2].total == 5 and ev[-2].cap == 5
    assert isinstance(ev[-1], Stopped)
    swiped = [e for e in ev if isinstance(e, Swiped)]
    assert swiped[-1].today_total == 5 and swiped[-1].today_like == 5


def test_auto_cap_already_reached(tmp_path):
    eng, br, ev, *_ = make(tmp_path, cap=3, pre=3)
    run(eng)
    assert br.swipes == []
    assert [type(e) for e in ev] == [CapReached, Stopped]


def test_auto_uses_threshold(tmp_path):
    eng, br, *_ = make(tmp_path, cap=2, judge=FakeJudge([v("like", 69)]))
    run(eng)
    assert br.swipes == ["pass", "pass"]


def test_no_crops_passes(tmp_path):
    eng, br, ev, *_ = make(tmp_path, cap=1, detector=FakeDetector(False))
    run(eng)
    assert br.swipes == ["pass"]
    assert [e for e in ev if isinstance(e, Evaluating)][0].images == 1


def test_pause_event(tmp_path):
    eng, br, ev, *_ = make(tmp_path, cap=3)
    eng._cfg = replace(eng._cfg, pause_every_min=1, pause_every_max=1, pause_min_s=5, pause_max_s=6)
    eng._pacer = Pacer(eng._cfg, random.Random(2), eng._sleep)
    run(eng)
    pauses = [e for e in ev if isinstance(e, Paused)]
    assert len(pauses) == 3 and all(5 <= p.seconds <= 6 for p in pauses)


def test_stop_during_pacing_no_swipe(tmp_path):
    eng, br, ev, *_ = make(tmp_path)
    stop = threading.Event()
    eng._sleep = lambda s: stop.set()
    eng._pacer = Pacer(eng._cfg, random.Random(1), eng._sleep)
    eng.run(stop)
    assert br.swipes == []
    assert isinstance(ev[-1], Stopped)


# ----------------------------------------------------------- suggest flow


def start_thread(eng):
    stop = threading.Event()
    t = threading.Thread(target=eng.run, args=(stop,), daemon=True)
    t.start()
    return stop, t


def wait_for(ev, cls, timeout=5.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if any(isinstance(e, cls) for e in list(ev)):
            return
        time.sleep(0.01)
    raise AssertionError(f"timeout waiting for {cls}")


def test_suggest_confirm_overrides(tmp_path):
    eng, br, ev, cmds, stats, _ = make(tmp_path, mode="suggest", cap=1,
                                        judge=FakeJudge([v("pass", 10)]))
    stop, t = start_thread(eng)
    wait_for(ev, AwaitingConfirm)
    assert [e for e in ev if isinstance(e, AwaitingConfirm)][0].final_decision == "pass"
    cmds.put(Confirm("like"))
    t.join(5)
    assert not t.is_alive()
    assert br.swipes == ["like"]
    assert isinstance(ev[-1], Stopped)
    assert any(isinstance(e, CapReached) for e in ev)


def test_suggest_stop_while_waiting(tmp_path):
    eng, br, ev, *_ = make(tmp_path, mode="suggest")
    stop, t = start_thread(eng)
    wait_for(ev, AwaitingConfirm)
    stop.set()
    t.join(5)
    assert not t.is_alive()
    assert br.swipes == []
    assert isinstance(ev[-1], Stopped)


def test_suggest_stale_confirm_drained(tmp_path):
    eng, br, ev, cmds, *_ = make(tmp_path, mode="suggest", cap=1)
    cmds.put(Confirm("like"))  # stale, before the profile
    stop, t = start_thread(eng)
    wait_for(ev, AwaitingConfirm)
    assert br.swipes == []
    cmds.put(Confirm("pass"))
    t.join(5)
    assert br.swipes == ["pass"]


def test_suggest_skip_waits_for_card_change(tmp_path):
    # captures: 1 = evaluation, 2 = baseline for skip, 3 = same, 4 = changed
    shades = {1: 10, 2: 10, 3: 10, 4: 200}
    br = FakeBrowser(images=lambda n, c: [img(shades.get(c, 200))])
    # Use gradient-free solid colors; hash differs between 10 and 200.
    assert image_hash(img(10)) != image_hash(img(200))
    eng, br, ev, cmds, stats, sl = make(tmp_path, mode="suggest", cap=1, browser=br)
    stop, t = start_thread(eng)
    wait_for(ev, AwaitingConfirm)
    cmds.put(Skip())
    wait_for(ev, Status)
    t.join(5)
    # after skip, cap not reached (no record) -> loop continues with next profile
    assert br.swipes == []
    assert Status("Hoppar över – swipea själv i webbläsaren") in ev
    assert 1.0 in sl.calls
    stop.set()
    cmds.put(Confirm("pass"))
    t.join(5)
    assert not t.is_alive()


def test_skip_stop_exits(tmp_path):
    br = FakeBrowser(images=lambda n, c: [img(10)])
    eng, br, ev, cmds, *_ = make(tmp_path, mode="suggest", browser=br)
    stop, t = start_thread(eng)
    wait_for(ev, AwaitingConfirm)
    cmds.put(Skip())
    wait_for(ev, Status)
    stop.set()
    t.join(5)
    assert not t.is_alive()
    assert isinstance(ev[-1], Stopped) and br.swipes == []


# --------------------------------------------------------------- states


@pytest.mark.parametrize("state,reason", [
    (S.LOGGED_OUT, "inloggning krävs"),
    (S.BLOCKED, "CAPTCHA eller verifiering"),
    (S.NO_PROFILES, "inga fler profiler"),
])
def test_needs_user_states(tmp_path, state, reason):
    eng, br, ev, *_ = make(tmp_path, browser=FakeBrowser(default_state=state))
    run(eng)
    assert ev == [NeedsUser(reason), Stopped()]
    assert br.swipes == []


def test_unknown_retries_then_needs_user(tmp_path):
    eng, br, ev, _, _, sl = make(tmp_path, browser=FakeBrowser(default_state=S.UNKNOWN))
    run(eng)
    assert ev == [NeedsUser("okänt läge"), Stopped()]
    assert sl.calls.count(2.0) == 3


def test_unknown_recovers(tmp_path):
    br = FakeBrowser(states=[S.UNKNOWN, READY])
    eng, br, ev, *_ = make(tmp_path, cap=1, browser=br)
    run(eng)
    assert br.swipes == ["like"]


def test_modal_dismissed_and_continues(tmp_path):
    br = FakeBrowser(states=[S.MODAL, READY])
    eng, br, ev, *_ = make(tmp_path, cap=1, browser=br)
    run(eng)
    assert br.dismissed == 1 and br.swipes == ["like"]


def test_modal_not_dismissable(tmp_path):
    br = FakeBrowser(states=[S.MODAL, S.MODAL])
    eng, br, ev, *_ = make(tmp_path, browser=br)
    run(eng)
    assert ev == [NeedsUser("popup kunde inte stängas"), Stopped()]


# ----------------------------------------------------------------- errors


def test_judge_exception_gives_error_and_stops(tmp_path):
    eng, br, ev, *_ = make(tmp_path, judge=FakeJudge(exc=RuntimeError(SECRET)))
    run(eng)
    assert br.swipes == []
    errs = [e for e in ev if isinstance(e, Error)]
    assert len(errs) == 1 and "RuntimeError" in errs[0].message and SECRET not in errs[0].message
    assert isinstance(ev[-1], Stopped)


def test_browser_exception_stops(tmp_path):
    class Boom(FakeBrowser):
        def swipe(self, d):
            raise OSError(SECRET)

    eng, br, ev, *_ = make(tmp_path, browser=Boom())
    run(eng)
    assert any(isinstance(e, Error) and "OSError" in e.message for e in ev)
    assert isinstance(ev[-1], Stopped)


# ---------------------------------------------------------------- privacy


def test_reason_only_in_verdict_event_and_not_logged(tmp_path, log_file):
    eng, br, ev, *_ = make(tmp_path, cap=3, judge=FakeJudge([v("like", 90)]))
    run(eng)
    for e in ev:
        if isinstance(e, VerdictReady):
            assert e.verdict.reason == SECRET
        else:
            assert SECRET not in repr(e)
    text = log_file.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "swipe" in text and "engine_start" in text and "engine_stop" in text
    assert "cap_reached" in text


def test_stopped_is_last_on_stop_before_start(tmp_path):
    eng, br, ev, *_ = make(tmp_path)
    stop = threading.Event()
    stop.set()
    eng.run(stop)
    assert ev == [Stopped()]


def test_suggest_confirm_swipes_without_pacing_delay(tmp_path):
    eng, br, ev, cmds, stats, sl = make(tmp_path, mode="suggest", cap=1)
    stop, t = start_thread(eng)
    wait_for(ev, AwaitingConfirm)
    cmds.put(Confirm("pass"))
    t.join(5)
    assert br.swipes == ["pass"]
    assert sl.total < 1.0  # no 3-9 s delay after the user's own confirmation
