"""Tests for the plain-Edge login helper (no real browser is started)."""
from __future__ import annotations

from pathlib import Path

import pytest

from swipebuddy import login_browser as lb


def _make_edge(base: Path) -> Path:
    exe = base / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


def test_find_edge(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "a"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "b"))
    assert lb.find_edge() is None
    exe = _make_edge(tmp_path / "b")
    assert lb.find_edge() == exe


def test_launch_builds_argv(tmp_path: Path) -> None:
    calls: list[tuple] = []

    def fake_popen(args, **kw):
        calls.append((args, kw))
        return "proc"

    prof = tmp_path / "x" / "profile"
    edge = tmp_path / "msedge.exe"
    assert lb.launch_plain_edge(prof, edge, popen=fake_popen) == "proc"
    assert prof.is_dir()
    args, kw = calls[0]
    assert args == [
        str(edge), f"--user-data-dir={prof}", "--no-first-run",
        "--no-default-browser-check", "--new-window", lb.LOGIN_URL,
    ]
    assert kw["shell"] is False
    assert not any("remote-debugging" in a or "automation" in a for a in args)


def test_launch_without_edge_raises(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lb, "find_edge", lambda: None)
    with pytest.raises(FileNotFoundError, match="Edge"):
        lb.launch_plain_edge(tmp_path / "p", popen=lambda *a, **k: None)


def test_profile_in_use(tmp_path: Path) -> None:
    assert not lb.profile_in_use(tmp_path / "missing")
    assert not lb.profile_in_use(tmp_path)
    (tmp_path / "lockfile").write_bytes(b"")
    assert not lb.profile_in_use(tmp_path)  # stale, openable
    (tmp_path / "SingletonLock").write_bytes(b"")
    assert lb.profile_in_use(tmp_path)


def test_profile_in_use_locked_lockfile(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "lockfile").write_bytes(b"")

    def deny(self, *a, **k):
        raise PermissionError

    monkeypatch.setattr(Path, "open", deny)
    assert lb.profile_in_use(tmp_path)
