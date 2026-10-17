"""Filesystem locations used by SwipeBuddy."""
from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "SWIPEBUDDY_HOME"


def app_dir() -> Path:
    """Return the application data directory, creating it if needed.

    ``SWIPEBUDDY_HOME`` overrides the default ``%LOCALAPPDATA%/SwipeBuddy``.
    """
    override = os.environ.get(HOME_ENV)
    if override:
        base = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) if local else Path.home() / "AppData" / "Local"
        base = root / "SwipeBuddy"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    """Path of config.json."""
    return app_dir() / "config.json"


def profile_dir() -> Path:
    """Browser profile directory."""
    return app_dir() / "browser-profile"


def models_dir() -> Path:
    """Model storage directory."""
    return app_dir() / "models"


def logs_dir() -> Path:
    """Log directory."""
    return app_dir() / "logs"


def stats_path() -> Path:
    """Path of stats.json."""
    return app_dir() / "stats.json"
