"""Decision engine: pure logic (no Qt) that drives one swiping session."""
from __future__ import annotations

import queue
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image

from .browser import BrowserState
from .config import AppConfig
from .detection import crop_person
from .logging_setup import log_event
from .vlm import Verdict

Decision = Literal["like", "pass"]
SLICE_S = 0.25
UNKNOWN_RETRIES = 3
UNKNOWN_SLEEP_S = 2.0
SKIP_POLL_S = 1.0

# Fixed Swedish UI strings (never profile data).
TXT_SKIP = "Hoppar över – swipea själv i webbläsaren"
TXT_ERROR = "Ett oväntat fel uppstod"
R_LOGIN = "inloggning krävs"
R_BLOCKED = "CAPTCHA eller verifiering"
R_NO_PROFILES = "inga fler profiler"
R_UNKNOWN = "okänt läge"
R_MODAL = "popup kunde inte stängas"


def decide(v: Verdict, threshold: int) -> Decision:
    """Like only if the verdict is valid, says like and meets the threshold."""
    return "like" if v.valid and v.decision == "like" and v.score >= threshold else "pass"


def image_hash(img: Image.Image) -> bytes:
    """16x16 grayscale fingerprint used to detect a changed card."""
    return img.convert("L").resize((16, 16)).tobytes()


# ---------------------------------------------------------------- events


@dataclass(frozen=True)
class Status:
    text: str


@dataclass(frozen=True)
class Evaluating:
    profile_no: int
    images: int


@dataclass(frozen=True)
class VerdictReady:
    verdict: Verdict
    final_decision: Decision
    mode: str


@dataclass(frozen=True)
class AwaitingConfirm:
    final_decision: Decision


@dataclass(frozen=True)
class Swiped:
    decision: Decision
    today_like: int
    today_pass: int
    today_total: int
    cap: int


@dataclass(frozen=True)
class Paused:
    seconds: float


@dataclass(frozen=True)
class CapReached:
    total: int
    cap: int


@dataclass(frozen=True)
class NeedsUser:
    reason: str


@dataclass(frozen=True)
class Stopped:
    pass


@dataclass(frozen=True)
class Error:
    message: str


EngineEvent = (
    Status | Evaluating | VerdictReady | AwaitingConfirm | Swiped | Paused
    | CapReached | NeedsUser | Stopped | Error
)


# -------------------------------------------------------------- commands


@dataclass(frozen=True)
class Confirm:
    decision: Decision


@dataclass(frozen=True)
class Skip:
    pass


# ----------------------------------------------------------------- pacer


class Pacer:
    """Random human-like delays and periodic longer pauses."""

    def __init__(self, cfg: AppConfig, rng: random.Random, sleep: Callable[[float], None]) -> None:
        self._cfg = cfg
        self._rng = rng
        self._sleep = sleep
        self._count = 0
        self._next_pause_at = self._draw_interval()

    def _draw_interval(self) -> int:
        c = self._cfg
        lo = max(1, c.pause_every_min)
        return self._rng.randint(lo, max(lo, c.pause_every_max))

    def next_delay(self) -> float:
        return self._rng.uniform(self._cfg.delay_min_s, self._cfg.delay_max_s)

    def _wait(self, seconds: float, stop: threading.Event) -> None:
        remaining = seconds
        while remaining > 0 and not stop.is_set():
            step = min(SLICE_S, remaining)
            self._sleep(step)
            remaining -= step

    def wait_before_swipe(self, stop: threading.Event) -> None:
        """Wait a random delay; returns early when ``stop`` is set."""
        self._wait(self.next_delay(), stop)

    def note_swipe(self) -> None:
        self._count += 1

    def maybe_pause(self, stop: threading.Event) -> float:
        """Pause if due; returns seconds paused (0 if not due)."""
        if self._count < self._next_pause_at:
            return 0.0
        self._count = 0
        self._next_pause_at = self._draw_interval()
        secs = self._rng.uniform(self._cfg.pause_min_s, self._cfg.pause_max_s)
        self._wait(secs, stop)
        return secs


# ---------------------------------------------------------------- engine


class SwipeEngine:
    """Runs the per-profile loop; never starts or closes the browser."""

    def __init__(
        self,
        cfg: AppConfig,
        browser: Any,
        detector: Any,
        judge: Any,
        stats: Any,
        events: Callable[[EngineEvent], None],
        commands: queue.Queue,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cfg = cfg
        self._browser = browser
        self._detector = detector
        self._judge = judge
        self._stats = stats
        self._emit = events
        self._commands = commands
        self._sleep = sleep
        self._pacer = Pacer(cfg, rng or random.Random(), sleep)
        self._profile_no = 0

    def _needs_user(self, reason: str) -> None:
        log_event("needs_user")
        self._emit(NeedsUser(reason))

    def _drain_commands(self) -> None:
        while True:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                return

    def _resolve_state(self, stop: threading.Event) -> bool:
        """True if the page is ready; otherwise emit NeedsUser (unless stopped) and return False."""
        state = self._browser.state()
        retries = 0
        while state is BrowserState.UNKNOWN and retries < UNKNOWN_RETRIES and not stop.is_set():
            retries += 1
            self._sleep(UNKNOWN_SLEEP_S)
            state = self._browser.state()
        if stop.is_set():
            return False
        if state is BrowserState.MODAL:
            self._browser.dismiss_modal()
            state = self._browser.state()
            if state is BrowserState.MODAL:
                self._needs_user(R_MODAL)
                return False
        if state is BrowserState.READY:
            return True
        reasons = {
            BrowserState.LOGGED_OUT: R_LOGIN,
            BrowserState.BLOCKED: R_BLOCKED,
            BrowserState.NO_PROFILES: R_NO_PROFILES,
        }
        self._needs_user(reasons.get(state, R_UNKNOWN))
        return False

    def _await_user(self, final: Decision, stop: threading.Event) -> Decision | None:
        """Suggest mode: block for Confirm/Skip. None means stopped or skipped."""
        self._emit(AwaitingConfirm(final))
        while not stop.is_set():
            try:
                cmd = self._commands.get(timeout=SLICE_S)
            except queue.Empty:
                continue
            if isinstance(cmd, Confirm) and cmd.decision in ("like", "pass"):
                return cmd.decision
            if isinstance(cmd, Skip):
                self._wait_for_manual_swipe(stop)
                return None
        return None

    def _wait_for_manual_swipe(self, stop: threading.Event) -> None:
        """Poll until the first card image changes (user swiped manually) or stop."""
        self._emit(Status(TXT_SKIP))
        current = self._first_hash()
        while not stop.is_set():
            self._sleep(SKIP_POLL_S)
            if stop.is_set():
                return
            fresh = self._first_hash()
            if fresh is not None and fresh != current:
                return

    def _first_hash(self) -> bytes | None:
        imgs = self._browser.capture_profile_images(1)
        return image_hash(imgs[0]) if imgs else None

    def _iteration(self, stop: threading.Event) -> bool:
        """Process one profile. Returns False when the loop should end."""
        cfg = self._cfg
        total = self._stats.today_total()
        if total >= cfg.daily_cap:
            log_event("cap_reached", total=total, cap=cfg.daily_cap)
            self._emit(CapReached(total, cfg.daily_cap))
            return False
        if not self._resolve_state(stop):
            return False
        self._drain_commands()
        images = self._browser.capture_profile_images(cfg.max_images_per_profile)
        self._profile_no += 1
        self._emit(Evaluating(self._profile_no, len(images)))
        crops = []
        for img in images:
            crop = crop_person(img, self._detector.detect(img))
            if crop is not None:
                crops.append(crop)
        verdict = self._judge.judge(crops, cfg.preferences)
        final = decide(verdict, cfg.threshold)
        self._emit(VerdictReady(verdict, final, cfg.mode))
        decision: Decision | None = final
        if cfg.mode == "suggest":
            decision = self._await_user(final, stop)
            if decision is None:
                return not stop.is_set()
        if cfg.mode == "auto":
            # In suggest mode the user's own confirmation is the human pacing.
            self._pacer.wait_before_swipe(stop)
        if stop.is_set():
            return False
        self._browser.swipe(decision)
        self._stats.record(decision)
        self._pacer.note_swipe()
        log_event("swipe", like=int(decision == "like"), nope=int(decision == "pass"))
        t = self._stats.today()
        self._emit(Swiped(decision, t["like"], t["pass"], t["like"] + t["pass"], cfg.daily_cap))
        del images, crops
        paused = self._pacer.maybe_pause(stop)
        if paused:
            log_event("pause", seconds=int(paused))
            self._emit(Paused(paused))
        return True

    def run(self, stop: threading.Event) -> None:
        """Run until stopped, capped, blocked or failed. Always ends with Stopped."""
        log_event("engine_start")
        try:
            while not stop.is_set():
                if not self._iteration(stop):
                    break
        except Exception as exc:  # noqa: BLE001 - report class only, never text
            name = type(exc).__name__
            log_event("error", error_type=name)
            self._emit(Error(f"{TXT_ERROR} ({name})"))
        finally:
            log_event("engine_stop")
            self._emit(Stopped())
