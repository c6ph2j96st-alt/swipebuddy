"""Tests for the browser module using fake pages (no real browser)."""
from __future__ import annotations

import io
import re
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from swipebuddy import tinder_selectors as sel
from swipebuddy.browser import BrowserState, TinderBrowser

FIXTURES = Path(__file__).parent / "fixtures"
APP_URL = "https://tinder.com/app/recs"


def png(color: tuple[int, int, int], size: tuple[int, int] = (60, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255), (10, 10, 10), (240, 240, 240)]


class FakeKeyboard:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)
        if key == "Space":
            self.page.index = min(self.page.index + 1, len(self.page.photos) - 1)


class FakeElement:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    def screenshot(self, **kw):
        assert "path" not in kw
        self.page.shots.append(("element", kw))
        return self.page.photos[self.page.index]

    def bounding_box(self):
        return {"x": 0, "y": 0, "width": 60, "height": 80}


class FakePage:
    def __init__(self, html: str = "", url: str = APP_URL, photos: list[bytes] | None = None,
                 viewport: dict | None = None) -> None:
        self.html, self.url = html, url
        self.photos = photos or [png(COLORS[0])]
        self.index = 0
        self.viewport_size = viewport or {"width": 1000, "height": 500}
        self.keyboard = FakeKeyboard(self)
        self.shots: list[tuple[str, dict]] = []
        self.waits: list[int] = []
        self.visited: list[str] = []

    def _match(self, s: str) -> bool:
        m = re.fullmatch(r'iframe\[src\*="(.+)"\]', s)
        if m:
            return re.search(r'<iframe[^>]*src="[^"]*' + re.escape(m[1]), self.html) is not None
        m = re.fullmatch(r'(a|button):has-text\("(.+)"\)', s)
        if m:
            return re.search(rf"<{m[1]}[^>]*>\s*{re.escape(m[2])}\s*<", self.html) is not None
        if s == '[role="dialog"]':
            return 'role="dialog"' in self.html
        if s == '[class*="recsCardboard"] [class*="Bdrs"]':
            return "recsCardboard" in self.html and "Bdrs" in self.html
        if s == 'div[aria-label*="Profile"]':
            return 'aria-label="Profile' in self.html
        return False

    def query_selector(self, s: str):
        return FakeElement(self) if self._match(s) else None

    def content(self) -> str:
        return self.html

    def screenshot(self, **kw):
        assert "path" not in kw
        self.shots.append(("page", kw))
        return self.photos[self.index]

    def goto(self, url: str) -> None:
        self.visited.append(url)

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


def fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make(page: FakePage) -> TinderBrowser:
    b = TinderBrowser(Path("unused"), page_factory=lambda: page)
    b.start()
    return b


def card_page(n_photos: int) -> FakePage:
    return FakePage(fx("tinder_ready.html"), photos=[png(COLORS[i]) for i in range(n_photos)])


# --- capture -----------------------------------------------------------
@pytest.mark.parametrize("n", [1, 3, 5])
def test_capture_count_equals_distinct_photos(n: int) -> None:
    page = card_page(n)
    imgs = make(page).capture_profile_images(8)
    assert len(imgs) == n
    assert [im.getpixel((0, 0)) for im in imgs] == COLORS[:n]
    assert all(im.mode == "RGB" for im in imgs)


def test_capture_capped_at_max() -> None:
    page = card_page(8)
    imgs = make(page).capture_profile_images(3)
    assert len(imgs) == 3
    assert page.keyboard.pressed.count("Space") == 2


def test_duplicate_is_discarded_and_stops() -> None:
    page = card_page(2)
    imgs = make(page).capture_profile_images(6)
    assert len(imgs) == 2
    assert page.keyboard.pressed == ["Space", "Space"]
    assert all(500 >= w >= 400 or w == 500 for w in page.waits)


def test_screenshots_never_get_path() -> None:
    page = card_page(3)
    make(page).capture_profile_images(6)
    assert page.shots
    assert all("path" not in kw and kw.get("type") == "png" for _, kw in page.shots)


def test_viewport_fallback_without_card() -> None:
    page = FakePage(fx("tinder_blank.html"), photos=[png(COLORS[0])])
    imgs = make(page).capture_profile_images(4)
    assert len(imgs) == 1
    kind, kw = page.shots[0]
    assert kind == "page"
    clip = kw["clip"]
    assert clip["width"] == pytest.approx(600) and clip["height"] == pytest.approx(400)
    assert clip["x"] == pytest.approx(200) and clip["y"] == pytest.approx(50)
    assert "path" not in kw


# --- swipe -------------------------------------------------------------
def test_swipe_keys_and_error() -> None:
    page = card_page(1)
    b = make(page)
    b.swipe("like")
    b.swipe("pass")
    assert page.keyboard.pressed == [sel.KEY_LIKE, sel.KEY_PASS] == ["ArrowRight", "ArrowLeft"]
    with pytest.raises(ValueError):
        b.swipe("superlike")  # type: ignore[arg-type]
    assert len(page.keyboard.pressed) == 2


# --- state -------------------------------------------------------------
@pytest.mark.parametrize(
    ("html", "url", "expected"),
    [
        ("tinder_ready.html", APP_URL, BrowserState.READY),
        ("tinder_logged_out.html", APP_URL, BrowserState.LOGGED_OUT),
        ("tinder_ready.html", "https://tinder.com/", BrowserState.LOGGED_OUT),
        ("tinder_no_profiles.html", APP_URL, BrowserState.NO_PROFILES),
        ("tinder_match_modal.html", APP_URL, BrowserState.MODAL),
        ("tinder_captcha.html", APP_URL, BrowserState.BLOCKED),
        ("tinder_blank.html", APP_URL, BrowserState.UNKNOWN),
    ],
)
def test_state_detection(html: str, url: str, expected: BrowserState) -> None:
    assert make(FakePage(fx(html), url=url)).state() is expected


def test_priority_blocked_over_logged_out_and_modal() -> None:
    html = fx("tinder_captcha.html") + fx("tinder_logged_out.html") + fx("tinder_match_modal.html")
    assert make(FakePage(html, url="https://tinder.com/")).state() is BrowserState.BLOCKED


def test_priority_logged_out_over_modal_and_card() -> None:
    page = FakePage(fx("tinder_match_modal.html"), url="https://tinder.com/")
    assert make(page).state() is BrowserState.LOGGED_OUT


def test_priority_modal_over_no_profiles() -> None:
    html = fx("tinder_match_modal.html") + fx("tinder_no_profiles.html")
    assert make(FakePage(html)).state() is BrowserState.MODAL


def test_blocked_by_text_only() -> None:
    page = FakePage("<html><body>Please verify you are human</body></html>")
    assert make(page).state() is BrowserState.BLOCKED


def test_script_text_does_not_trigger_markers() -> None:
    page = FakePage("<html><script>var s=\"verify you are human\"</script></html>")
    assert make(page).state() is BrowserState.UNKNOWN


def test_dismiss_modal() -> None:
    page = FakePage(fx("tinder_match_modal.html"))
    b = make(page)
    assert b.dismiss_modal() is False
    assert page.keyboard.pressed == ["Escape"]
    page.html = fx("tinder_ready.html")
    assert b.dismiss_modal() is True


# --- lifecycle ---------------------------------------------------------
def test_close_idempotent_and_start_with_factory_skips_playwright(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)  # import would fail
    b = make(card_page(1))
    b.close()
    b.close()
    TinderBrowser(Path("x")).close()
    with pytest.raises(RuntimeError):
        b.state()


class _Ctx:
    def __init__(self) -> None:
        self.pages: list = []
        self.closed = 0
        self.page = FakePage()

    def new_page(self):
        return self.page

    def close(self) -> None:
        self.closed += 1
        raise RuntimeError("boom")  # close() must swallow


class _FakePW:
    def __init__(self, fail_channels: set) -> None:
        self.calls: list[dict] = []
        self.fail = fail_channels
        self.stopped = 0
        self.ctx = _Ctx()
        self.chromium = self

    def launch_persistent_context(self, **kw):
        self.calls.append(kw)
        if kw["channel"] in self.fail:
            raise RuntimeError("channel missing")
        return self.ctx

    def start(self):
        return self

    def stop(self) -> None:
        self.stopped += 1


def _install(monkeypatch, pw: _FakePW) -> None:
    mod = types.ModuleType("playwright.sync_api")
    mod.sync_playwright = lambda: pw  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", mod)


def test_msedge_falls_back_to_chromium(monkeypatch, tmp_path) -> None:
    pw = _FakePW({"msedge"})
    _install(monkeypatch, pw)
    b = TinderBrowser(tmp_path, channel="msedge")
    b.start()
    assert [c["channel"] for c in pw.calls] == ["msedge", None]
    assert b.channel_used == "chromium"
    kw = pw.calls[-1]
    assert kw["headless"] is False and kw["viewport"] is None
    assert kw["user_data_dir"] == str(tmp_path)
    assert "--disk-cache-size=1" in kw["args"]
    assert pw.ctx.page.visited == [sel.TINDER_URL]
    b.close()
    b.close()
    assert pw.ctx.closed == 1 and pw.stopped == 1


def test_msedge_used_when_available(monkeypatch, tmp_path) -> None:
    pw = _FakePW(set())
    _install(monkeypatch, pw)
    b = TinderBrowser(tmp_path)
    b.start()
    assert b.channel_used == "msedge" and len(pw.calls) == 1
    b.close()


def test_chromium_channel_maps_to_none(monkeypatch, tmp_path) -> None:
    pw = _FakePW(set())
    _install(monkeypatch, pw)
    b = TinderBrowser(tmp_path, channel="chromium")
    b.start()
    assert pw.calls[0]["channel"] is None and b.channel_used == "chromium"
    b.close()
