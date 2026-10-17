"""Plain (non-automated) Edge for logging in to Tinder (no Qt).

Google/Facebook refuse to log in inside an automation-controlled browser. The
user therefore logs in in an ordinary Edge window that shares SwipeBuddy's
profile directory; the saved cookies are reused later by the automated Edge.
The program never sees or handles any credentials.
"""
from __future__ import annotations

import os
import subprocess
import sys
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
