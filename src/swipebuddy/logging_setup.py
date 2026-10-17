"""Privacy-preserving logging.

Only whitelisted event names with integer counts are ever written. All other
records (free text, exception messages, third-party logs) are dropped.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from .paths import logs_dir

ALLOWED_EVENTS: frozenset[str] = frozenset(
    {
        "app_start", "app_stop", "engine_start", "engine_stop", "swipe", "pause",
        "cap_reached", "needs_user", "error", "setup_check", "model_download",
    }
)
EVENT_LOGGER_NAME = "swipebuddy.events"
LOG_FILENAME = "swipebuddy.log"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CLASS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_HANDLER_TAG = "_swipebuddy_handler"


def log_event(name: str, /, **counts: int) -> None:
    """Log a whitelisted event with integer fields (``error_type`` str for errors)."""
    logging.getLogger(EVENT_LOGGER_NAME).info(
        "event", extra={"event": name, "fields": dict(counts)}
    )


class PrivacyFilter(logging.Filter):
    """Allow only whitelisted events whose fields are ints; scrub everything else."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = getattr(record, "event", None)
        fields = getattr(record, "fields", None)
        if not isinstance(name, str) or name not in ALLOWED_EVENTS or not isinstance(fields, dict):
            return False
        parts: list[str] = []
        for key, value in fields.items():
            if not isinstance(key, str) or not _IDENT.match(key):
                return False
            if name == "error" and key == "error_type":
                if not isinstance(value, str) or not _CLASS_NAME.match(value):
                    return False
            elif isinstance(value, bool) or not isinstance(value, int):
                return False
            parts.append(f"{key}={value}")
        record.kv = " ".join(parts)
        # Never let message, args or tracebacks reach the formatter.
        record.msg, record.args = "event", None
        record.exc_info, record.exc_text, record.stack_info = None, None, None
        return True


def setup_logging(level: int = logging.INFO) -> logging.Handler:
    """Install the rotating, privacy-filtered file handler on the root logger."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_TAG, False):
            root.removeHandler(h)
            h.close()
    d = logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        d / LOG_FILENAME, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.addFilter(PrivacyFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(event)s %(kv)s"))
    setattr(handler, _HANDLER_TAG, True)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return handler
