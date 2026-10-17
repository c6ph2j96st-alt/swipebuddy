"""Playwright wrapper around Tinder Web. Images live in RAM only.

Never reads or writes form fields, never types text, never logs profile data.
"""
from __future__ import annotations

import enum
import io
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from PIL import Image

from . import tinder_selectors as sel

_log = logging.getLogger(__name__)

_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_HASH_SIZE = (16, 16)
_PHOTO_WAIT_MS = 500
_SWIPE_WAIT_MS = 300
_DISMISS_WAIT_MS = 400
_VIEWPORT_FRACTION = (0.6, 0.8)


class BrowserState(enum.Enum):
    """What the Tinder page currently shows."""

    LOGGED_OUT = "logged_out"
    READY = "ready"
    NO_PROFILES = "no_profiles"
    MODAL = "modal"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


def _visible_text(html: str) -> str:
    """Lower-cased text of ``html`` without scripts, styles and tags."""
    text = _TAGS.sub(" ", _SCRIPT_STYLE.sub(" ", html))
    return _WS.sub(" ", text).lower()


def _image_hash(img: Image.Image) -> bytes:
    """Small grayscale fingerprint used to detect an unchanged photo."""
    return img.convert("L").resize(_HASH_SIZE).tobytes()


class TinderBrowser:
    """Drives a persistent Chromium/Edge profile showing Tinder Web."""

    def __init__(
        self,
        profile_dir: Path,
        channel: str = "msedge",
        page_factory: Callable[[], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.channel = channel
        self.channel_used: str | None = None
        self._page_factory = page_factory
        self._sleep = sleep
        self._page: Any = None
        self._context: Any = None
        self._playwright: Any = None

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        """Open the browser (or use the injected page) and go to Tinder."""
        if self._page_factory is not None:
            self._page = self._page_factory()
            self.channel_used = "fake"
            return
        from playwright.sync_api import sync_playwright  # lazy import

        self._playwright = sync_playwright().start()
        try:
            try:
                self._context = self._launch(None if self.channel == "chromium" else self.channel)
                self.channel_used = self.channel
            except Exception:
                if self.channel == "chromium":
                    raise
                _log.warning("browser channel launch failed; falling back to chromium")
                self._context = self._launch(None)
                self.channel_used = "chromium"
            pages = list(self._context.pages)
            self._page = pages[0] if pages else self._context.new_page()
            self._page.goto(sel.TINDER_URL)
        except Exception:
            self.close()
            raise

    def _launch(self, channel: str | None) -> Any:
        return self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel=channel,
            headless=False,
            viewport=None,
            args=["--disk-cache-size=1", "--media-cache-size=1"],
        )

    def close(self) -> None:
        """Close context and Playwright; idempotent, never raises."""
        context, playwright = self._context, self._playwright
        self._context = self._playwright = self._page = None
        for owner, name in ((context, "close"), (playwright, "stop")):
            closer = getattr(owner, name, None)
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass

    # -- state ---------------------------------------------------------
    def _require_page(self) -> Any:
        if self._page is None:
            raise RuntimeError("TinderBrowser not started")
        return self._page

    def _first(self, selectors: tuple[str, ...]) -> Any:
        page = self._require_page()
        for s in selectors:
            try:
                el = page.query_selector(s)
            except Exception:  # noqa: BLE001 - bad/unsupported selector
                continue
            if el is not None:
                return el
        return None

    def state(self) -> BrowserState:
        """Classify the page: BLOCKED > LOGGED_OUT > MODAL > NO_PROFILES > READY."""
        page = self._require_page()
        try:
            text = _visible_text(page.content())
        except Exception:  # noqa: BLE001 - page navigating/closed
            text = ""
        if self._first(sel.BLOCKED_SELECTORS) is not None or any(
            m in text for m in sel.BLOCKED_TEXT_MARKERS
        ):
            return BrowserState.BLOCKED
        host = urlsplit(page.url or "")
        on_tinder = host.hostname is not None and (
            host.hostname == sel.TINDER_HOST or host.hostname.endswith("." + sel.TINDER_HOST)
        )
        if (on_tinder and not host.path.startswith(sel.APP_PATH_PREFIX)) or (
            self._first(sel.LOGGED_OUT_MARKERS) is not None
        ):
            return BrowserState.LOGGED_OUT
        if self._first(sel.MODAL_SELECTORS) is not None or any(
            m in text for m in sel.MODAL_TEXT_MARKERS
        ):
            return BrowserState.MODAL
        if any(m in text for m in sel.NO_PROFILES_MARKERS):
            return BrowserState.NO_PROFILES
        if self._first(sel.CARD_SELECTORS) is not None:
            return BrowserState.READY
        return BrowserState.UNKNOWN

    def dismiss_modal(self) -> bool:
        """Press Escape and report whether the modal is gone."""
        page = self._require_page()
        page.keyboard.press(sel.KEY_DISMISS)
        page.wait_for_timeout(_DISMISS_WAIT_MS)
        return self.state() != BrowserState.MODAL

    # -- capture -------------------------------------------------------
    def _screenshot_card(self) -> Image.Image:
        page = self._require_page()
        card = self._first(sel.CARD_SELECTORS)
        if card is not None:
            data = card.screenshot(type="png")
        else:
            vp = page.viewport_size or {"width": 1280, "height": 720}
            w, h = vp["width"], vp["height"]
            cw, ch = w * _VIEWPORT_FRACTION[0], h * _VIEWPORT_FRACTION[1]
            clip = {"x": (w - cw) / 2, "y": (h - ch) / 2, "width": cw, "height": ch}
            data = page.screenshot(type="png", clip=clip)
        buf = io.BytesIO(data)
        try:
            img = Image.open(buf).convert("RGB")
            img.load()
        finally:
            buf.close()
        return img

    def capture_profile_images(self, max_images: int) -> list[Image.Image]:
        """Screenshot the card, flip through photos with Space, stop on repeat."""
        page = self._require_page()
        images: list[Image.Image] = []
        last: bytes | None = None
        while len(images) < max_images:
            img = self._screenshot_card()
            h = _image_hash(img)
            if h == last:
                img.close()
                break
            images.append(img)
            last = h
            if len(images) >= max_images:
                break
            page.keyboard.press(sel.KEY_NEXT_PHOTO)
            page.wait_for_timeout(_PHOTO_WAIT_MS)
        return images

    def swipe(self, decision: Literal["like", "pass"]) -> None:
        """Like (ArrowRight) or pass (ArrowLeft) the current profile."""
        if decision == "like":
            key = sel.KEY_LIKE
        elif decision == "pass":
            key = sel.KEY_PASS
        else:
            raise ValueError(f"invalid decision: {decision!r}")
        page = self._require_page()
        page.keyboard.press(key)
        page.wait_for_timeout(_SWIPE_WAIT_MS)
