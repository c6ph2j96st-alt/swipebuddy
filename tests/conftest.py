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


@pytest.fixture(autouse=True)
def _no_real_edge_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never spawn powershell/taskkill from tests; tests of it pass fake ``run``."""
    from swipebuddy import login_browser

    real = login_browser.find_profile_edge_pids

    def guarded(profile_dir, run=None):
        if run is None:
            return []
        return real(profile_dir, run=run)

    monkeypatch.setattr(login_browser, "find_profile_edge_pids", guarded)
