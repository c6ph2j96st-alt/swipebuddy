"""Privacy tests: nothing but whitelisted events and integer counts reach the log."""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from swipebuddy import logging_setup
from swipebuddy.config import AppConfig, Preferences
from swipebuddy.logging_setup import ALLOWED_EVENTS, PrivacyFilter, log_event, setup_logging

SECRET = "SECRETNAME-Anna-likes-hiking"


@pytest.fixture
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("SWIPEBUDDY_HOME", str(tmp_path / "home"))
    root = logging.getLogger()
    old_level = root.level
    handler = setup_logging(logging.DEBUG)
    yield tmp_path / "home" / "logs" / "swipebuddy.log"
    root.removeHandler(handler)
    handler.close()
    root.setLevel(old_level)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_allowed_events_set() -> None:
    assert ALLOWED_EVENTS == {
        "app_start", "app_stop", "engine_start", "engine_stop", "swipe", "pause",
        "cap_reached", "needs_user", "error", "setup_check", "model_download",
    }


def test_event_with_ints_logged(log_file: Path) -> None:
    log_event("swipe", liked=1, score=83)
    log_event("app_start")
    text = read(log_file)
    lines = text.strip().splitlines()
    assert len(lines) == 2
    assert re.search(r"swipe liked=1 score=83$", lines[0])
    assert lines[1].endswith("app_start ")  or lines[1].endswith("app_start")


def test_rotation_config(log_file: Path) -> None:
    from logging.handlers import RotatingFileHandler

    h = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert h and h[-1].maxBytes == 1_000_000 and h[-1].backupCount == 3
    assert any(isinstance(f, PrivacyFilter) for f in h[-1].filters)


def test_setup_idempotent(log_file: Path) -> None:
    setup_logging()
    setup_logging()
    n = [h for h in logging.getLogger().handlers if getattr(h, "_swipebuddy_handler", False)]
    assert len(n) == 1
    log_event("pause", seconds=5)
    assert read(log_file).count("pause") == 1
    for h in n:
        logging.getLogger().removeHandler(h)
        h.close()


def test_arbitrary_text_dropped(log_file: Path) -> None:
    logging.getLogger("swipebuddy.engine").info("matched %s", SECRET)
    logging.getLogger("swipebuddy.engine").warning(SECRET)
    logging.getLogger("httpx").info("GET http://x/%s", SECRET)
    logging.getLogger().error(SECRET)
    assert read(log_file) == ""


def test_strings_in_counts_dropped(log_file: Path) -> None:
    log_event("swipe", reason=SECRET)  # type: ignore[arg-type]
    log_event("swipe", liked=1, name=SECRET)  # type: ignore[arg-type]
    log_event("swipe", ok=True)  # bool is not a count
    log_event("swipe", ratio=0.5)  # type: ignore[arg-type]
    assert read(log_file) == ""


def test_unknown_event_name_dropped(log_file: Path) -> None:
    log_event("profile_name_" + SECRET, n=1)
    log_event("not_allowed", n=1)
    assert read(log_file) == ""


def test_spoofed_extra_on_other_logger_needs_whitelist(log_file: Path) -> None:
    logging.getLogger("thirdparty").info(SECRET, extra={"event": "swipe", "fields": {"msg": SECRET}})
    logging.getLogger("thirdparty").info(SECRET, extra={"event": "swipe"})
    logging.getLogger("thirdparty").info(SECRET, extra={"event": SECRET, "fields": {}})
    assert read(log_file) == ""


def test_valid_event_message_text_not_written(log_file: Path) -> None:
    logging.getLogger("x").info(SECRET, extra={"event": "swipe", "fields": {"n": 1}})
    text = read(log_file)
    assert "swipe n=1" in text and SECRET not in text


def test_exception_messages_dropped(log_file: Path) -> None:
    try:
        raise RuntimeError(SECRET)
    except RuntimeError:
        logging.getLogger("swipebuddy.engine").exception("failed")
        logging.getLogger("swipebuddy.engine").info(
            "x", exc_info=True, extra={"event": "swipe", "fields": {"n": 1}}
        )
    text = read(log_file)
    assert SECRET not in text and "Traceback" not in text
    assert "swipe n=1" in text


def test_error_event_accepts_class_name_only(log_file: Path) -> None:
    log_event("error", error_type="TimeoutError", code=2)  # type: ignore[arg-type]
    log_event("error", error_type="playwright._impl._errors.Error")  # type: ignore[arg-type]
    log_event("error", error_type="Boom: " + SECRET)  # type: ignore[arg-type]
    log_event("error", error_type="has space")  # type: ignore[arg-type]
    log_event("error", error_type="")  # type: ignore[arg-type]
    log_event("error", error_type=5)  # type: ignore[arg-type]
    log_event("swipe", error_type="TimeoutError")  # type: ignore[arg-type]
    text = read(log_file)
    lines = text.strip().splitlines()
    assert len(lines) == 2
    assert "error_type=TimeoutError code=2" in lines[0]
    assert "playwright._impl._errors.Error" in lines[1]
    assert SECRET not in text


def test_error_from_exception_class_name(log_file: Path) -> None:
    try:
        raise ValueError(SECRET)
    except ValueError as exc:
        log_event("error", error_type=type(exc).__name__)  # type: ignore[arg-type]
    text = read(log_file)
    assert "error_type=ValueError" in text and SECRET not in text


def test_bad_field_key_dropped(log_file: Path) -> None:
    logging.getLogger("x").info("m", extra={"event": "swipe", "fields": {SECRET + " x": 1}})
    assert read(log_file) == ""


def test_config_free_text_and_reasons_never_in_log(log_file: Path) -> None:
    cfg = AppConfig(preferences=Preferences(free_text=SECRET))
    logging.getLogger("swipebuddy.config").info("loaded %r", cfg)
    logging.getLogger("swipebuddy.vlm").info("reason: %s", SECRET)
    log_event("setup_check", ok=1)
    assert SECRET not in read(log_file)
    assert "setup_check ok=1" in read(log_file)


def test_filter_unit() -> None:
    f = PrivacyFilter()

    def rec(**extra: object) -> logging.LogRecord:
        r = logging.LogRecord("n", logging.INFO, "p", 1, "text", None, None)
        r.__dict__.update(extra)
        return r

    assert not f.filter(rec())
    assert f.filter(rec(event="swipe", fields={"a": 1}))
    assert not f.filter(rec(event="swipe", fields={"a": "1"}))
    assert not f.filter(rec(event="swipe", fields=[1]))
    assert logging_setup.EVENT_LOGGER_NAME.startswith("swipebuddy")
