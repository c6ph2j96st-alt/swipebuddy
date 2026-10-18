"""Plain (non-automated) Edge for logging in to Tinder (no Qt).

Google/Facebook refuse to log in inside an automation-controlled browser. The
user therefore logs in in an ordinary Edge window that shares SwipeBuddy's
profile directory; the saved cookies are reused later by the automated Edge.
The program never sees or handles any credentials.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

LOGIN_URL = "https://tinder.com/"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
TXT_NO_EDGE = "Microsoft Edge hittades inte. Installera Edge för att kunna logga in."


def find_edge() -> Path | None:
    """Locate msedge.exe in a standard Program Files location, else None."""
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(var)
        if base:
            exe = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if exe.is_file():
                return exe
    return None


def launch_plain_edge(
    profile_dir: Path,
    edge: Path | None = None,
    popen=subprocess.Popen,
) -> subprocess.Popen:
    """Start an ordinary Edge window on ``profile_dir`` and return the process.

    No shell is used and no automation flags are passed. Raises
    FileNotFoundError (Swedish message) if Edge cannot be found.
    """
    exe = edge if edge is not None else find_edge()
    if exe is None:
        raise FileNotFoundError(TXT_NO_EDGE)
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(exe),
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--new-window",
        LOGIN_URL,
    ]
    return popen(  # noqa: S603
        args,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )


def profile_in_use(profile_dir: Path) -> bool:
    """Best-effort: True if a Chromium browser seems to be using ``profile_dir``.

    Windows Chromium keeps ``lockfile`` open exclusively while running, so
    opening it fails; on POSIX a ``SingletonLock`` entry (a symlink, hence
    lexists) is present. A stale file that can be opened does not count.
    Returns False whenever unsure.
    """
    d = Path(profile_dir)
    if os.path.lexists(d / "SingletonLock"):
        return True
    lock = d / "lockfile"
    if not lock.exists():
        return False
    try:
        with lock.open("rb"):
            return False
    except OSError:
        return True


_PS_LIST_EDGE = (
    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" "
    "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
)


def _norm_path(text: str) -> str:
    return text.strip().strip('"').replace("/", "\\").rstrip("\\").lower()


def _cmdline_uses_profile(cmdline: str, profile_dir: Path) -> bool:
    """True if ``cmdline`` has ``--user-data-dir=<profile_dir>`` (quoted or not).

    The path may contain spaces, so compare by prefix and require the path to
    end at a space, a quote or the end of the command line.
    """
    target = _norm_path(str(profile_dir))
    marker = "--user-data-dir="
    low = cmdline.lower()
    pos = low.find(marker)
    while pos != -1:
        rest = cmdline[pos + len(marker):].lstrip('"').replace("/", "\\").lower()
        if rest.startswith(target):
            tail = rest[len(target):]
            if tail.startswith("\\"):
                tail = tail[1:]
            if tail == "" or tail[0] in ' "':
                return True
        pos = low.find(marker, pos + len(marker))
    return False


def find_profile_edge_pids(profile_dir: Path, run=subprocess.run) -> list[int]:
    """PIDs of msedge.exe processes started with our ``--user-data-dir`` (else [])."""
    try:
        res = run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_LIST_EDGE],
            shell=False,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_NO_WINDOW,
        )
        out = (res.stdout or "").strip()
        if not out:
            return []
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        pids: list[int] = []
        for item in data:
            cmd = item.get("CommandLine") if isinstance(item, dict) else None
            pid = item.get("ProcessId") if isinstance(item, dict) else None
            if isinstance(cmd, str) and isinstance(pid, int) and _cmdline_uses_profile(cmd, profile_dir):
                pids.append(pid)
        return pids
    except Exception:  # noqa: BLE001 - best effort
        return []


def close_profile_edge(
    profile_dir: Path,
    graceful_wait_s: float = 20.0,
    force_wait_s: float = 5.0,
    run=subprocess.run,
    sleep=time.sleep,
    busy: Callable[[Path], bool] = profile_in_use,
    clock=time.monotonic,
) -> bool:
    """Close the Edge processes using ``profile_dir`` (and only those).

    First a graceful ``taskkill`` (WM_CLOSE, so cookies are flushed), then a
    forced ``taskkill /F /T``. Returns True once the profile is free. Never raises.
    """
    profile_dir = Path(profile_dir)

    def kill(pids: list[int], force: bool) -> None:
        for pid in pids:
            cmd = ["taskkill"] + (["/F", "/T"] if force else []) + ["/PID", str(pid)]
            try:
                run(cmd, shell=False, capture_output=True, timeout=15, creationflags=_NO_WINDOW)
            except Exception:  # noqa: BLE001 - keep going with the others
                pass

    def wait(limit_s: float) -> bool:
        deadline = clock() + limit_s
        while busy(profile_dir):
            if clock() >= deadline:
                return False
            sleep(0.5)
        return True

    try:
        if not busy(profile_dir):
            return True
        kill(find_profile_edge_pids(profile_dir, run=run), force=False)
        if wait(graceful_wait_s):
            return True
        kill(find_profile_edge_pids(profile_dir, run=run), force=True)
        wait(force_wait_s)
        return not busy(profile_dir)
    except Exception:  # noqa: BLE001
        return False
