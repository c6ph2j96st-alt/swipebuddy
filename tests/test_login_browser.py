"""Tests for the plain-Edge login helper (no real browser is started)."""
from __future__ import annotations

from pathlib import Path

import json
import subprocess

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
        "--no-default-browser-check", "--disable-background-mode", "--new-window", lb.LOGIN_URL,
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


class _Res:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


def _ps_run(payload: str):
    def run(args, **kw):
        assert args[0] == "powershell" and kw["shell"] is False and kw["timeout"] == 15
        return _Res(payload)

    return run


def test_find_pids_filters_by_profile(tmp_path: Path) -> None:
    prof = tmp_path / "My Profile"
    other = tmp_path / "other"
    procs = [
        {"ProcessId": 1, "CommandLine": f'"msedge.exe" --user-data-dir={prof} --no-first-run'},
        {"ProcessId": 2, "CommandLine": f'"msedge.exe" "--user-data-dir={prof}" --type=renderer'},
        {"ProcessId": 3, "CommandLine": f"msedge.exe --user-data-dir={other}"},
        {"ProcessId": 4, "CommandLine": "msedge.exe --profile-directory=Default"},
        {"ProcessId": 5, "CommandLine": f"msedge.exe --user-data-dir={str(prof).upper()}"},
        {"ProcessId": 6, "CommandLine": f"msedge.exe --user-data-dir={prof}-x"},
        {"ProcessId": 7, "CommandLine": None},
    ]
    assert lb.find_profile_edge_pids(prof, run=_ps_run(json.dumps(procs))) == [1, 2, 5]


def test_find_pids_single_object_and_empty(tmp_path: Path) -> None:
    one = {"ProcessId": 9, "CommandLine": f"msedge.exe --user-data-dir={tmp_path}"}
    assert lb.find_profile_edge_pids(tmp_path, run=_ps_run(json.dumps(one))) == [9]
    assert lb.find_profile_edge_pids(tmp_path, run=_ps_run("")) == []
    assert lb.find_profile_edge_pids(tmp_path, run=_ps_run("  ")) == []


def test_find_pids_failures(tmp_path: Path) -> None:
    assert lb.find_profile_edge_pids(tmp_path, run=_ps_run("not json")) == []

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("powershell", 15)

    assert lb.find_profile_edge_pids(tmp_path, run=boom) == []


class _Sim:
    """Fake run/sleep/clock/busy for close_profile_edge."""

    def __init__(self, prof: Path, release_on: str | None) -> None:
        self.prof = prof
        self.release_on = release_on  # "graceful", "force" or None
        self.busy_now = True
        self.taskkills: list[list[str]] = []
        self.t = 0.0

    def run(self, args, **kw):
        assert kw["shell"] is False
        if args[0] == "powershell":
            cmd = f"msedge.exe --user-data-dir={self.prof}"
            return _Res(json.dumps([{"ProcessId": 42, "CommandLine": cmd}]))
        self.taskkills.append(args)
        force = "/F" in args
        if (self.release_on == "graceful" and not force) or (self.release_on == "force" and force):
            self.busy_now = False
        return _Res()

    def sleep(self, s: float) -> None:
        self.t += s

    def clock(self) -> float:
        return self.t

    def busy(self, p: Path) -> bool:
        return self.busy_now


def _close(sim: _Sim) -> bool:
    return lb.close_profile_edge(
        sim.prof, 2.0, 1.0, run=sim.run, sleep=sim.sleep, busy=sim.busy, clock=sim.clock
    )


def test_close_not_busy_does_nothing(tmp_path: Path) -> None:
    sim = _Sim(tmp_path, None)
    sim.busy_now = False
    assert _close(sim) is True
    assert sim.taskkills == []


def test_close_graceful(tmp_path: Path) -> None:
    sim = _Sim(tmp_path, "graceful")
    assert _close(sim) is True
    assert sim.taskkills == [["taskkill", "/PID", "42"]]


def test_close_force(tmp_path: Path) -> None:
    sim = _Sim(tmp_path, "force")
    assert _close(sim) is True
    assert sim.taskkills[0] == ["taskkill", "/PID", "42"]
    assert sim.taskkills[-1] == ["taskkill", "/F", "/T", "/PID", "42"]


def test_close_never_released(tmp_path: Path) -> None:
    sim = _Sim(tmp_path, None)
    assert _close(sim) is False
    assert any("/F" in c for c in sim.taskkills)


def test_close_never_raises(tmp_path: Path) -> None:
    def bad_busy(p):
        raise OSError

    assert lb.close_profile_edge(tmp_path, busy=bad_busy) is False
