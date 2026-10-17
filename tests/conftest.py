"""Shared test setup: headless Qt and an isolated data directory."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _swipebuddy_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "sb_home"
    monkeypatch.setenv("SWIPEBUDDY_HOME", str(home))
    return home
