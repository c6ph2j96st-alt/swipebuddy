"""First-run checks and helpers (no Qt): GPU, Ollama, detector model, browser."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import AppConfig
from .detection import MODEL_URL, default_model_path, verify_model_file

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/windows"
MIN_VRAM_MB = 8000
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_DETACHED = (
    getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if sys.platform == "win32"
    else 0
)


@dataclass(frozen=True)
class GpuInfo:
    """Name and total VRAM of the first NVIDIA GPU."""

    name: str
    vram_mb: int


def check_gpu(run: Callable[..., Any] = subprocess.run) -> GpuInfo | None:
    """Query nvidia-smi; any failure yields None."""
    try:
        res = run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_NO_WINDOW,
        )
        if res.returncode != 0:
            return None
        line = next(ln for ln in str(res.stdout).splitlines() if ln.strip())
        name, _, mem = line.rpartition(",")
        name = name.strip()
        vram = int(float(mem.strip()))
        if not name or vram <= 0:
            return None
        return GpuInfo(name, vram)
    except Exception:  # noqa: BLE001 - nvidia-smi missing, timeout, garbage
        return None


def gpu_warning(info: GpuInfo | None) -> str | None:
    """Swedish warning text, or None if the GPU looks sufficient."""
    if info is None:
        return "Ingen NVIDIA-GPU hittades – modellen blir mycket långsam på CPU"
    if info.vram_mb < MIN_VRAM_MB:
        return f"Lite grafikminne ({info.vram_mb} MB) – välj gärna den mindre modellen (3b)"
    return None


def find_ollama_app() -> Path | None:
    """Locate the Ollama GUI app or ollama.exe on PATH."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "Programs" / "Ollama" / "ollama app.exe"
        if p.is_file():
            return p
    found = shutil.which("ollama")
    return Path(found) if found else None


def start_ollama(path: Path) -> None:
    """Launch Ollama detached, without a shell."""
    args = [str(path)] if path.name.lower().startswith("ollama app") else [str(path), "serve"]
    subprocess.Popen(  # noqa: S603
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_DETACHED | _NO_WINDOW,
        close_fds=True,
    )


def download_detector(
    dest: Path,
    on_progress: Callable[[int, int], None],
    cancel: threading.Event,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> bool:
    """Download the detector to ``dest``. True on success, False if cancelled.

    Raises ValueError if the downloaded file fails verification.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    try:
        with client_factory(follow_redirects=True, timeout=httpx.Timeout(30.0, read=60.0)) as c:
            with c.stream("GET", MODEL_URL) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                done = 0
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        if cancel.is_set():
                            break
                        f.write(chunk)
                        done += len(chunk)
                        on_progress(done, total)
        if cancel.is_set():
            part.unlink(missing_ok=True)
            return False
        if not verify_model_file(part):
            raise ValueError("Nedladdad detektormodell har fel kontrollsumma")
        os.replace(part, dest)
        return True
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def edge_available() -> bool:
    """True if msedge.exe exists in a standard install location."""
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(var)
        if base and (Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe").is_file():
            return True
    return False


def install_chromium(on_line: Callable[[str], None]) -> int:
    """Run Playwright's ``install chromium``, streaming output. Returns exit code."""
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    driver = compute_driver_executable()
    cmd = [str(p) for p in driver] if isinstance(driver, tuple) else [str(driver)]
    proc = subprocess.Popen(  # noqa: S603
        [*cmd, "install", "chromium"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=get_driver_env(),
        creationflags=_NO_WINDOW,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.strip():
            on_line(line.rstrip())
    return proc.wait()


@dataclass(frozen=True)
class SetupStatus:
    """Snapshot of everything the setup dialog checks."""

    ollama_running: bool = False
    ollama_version: str | None = None
    model_present: bool = False
    detector_present: bool = False
    gpu: GpuInfo | None = None
    edge: bool = False


def collect_status(
    cfg: AppConfig,
    client: Any,
    detector_path: Path | None = None,
    gpu_fn: Callable[[], GpuInfo | None] = check_gpu,
    edge_fn: Callable[[], bool] = edge_available,
) -> SetupStatus:
    """Gather the current status; never raises."""
    running = bool(client.is_running())
    version = client.version() if running else None
    try:
        model = running and bool(client.has_model(cfg.vlm_model))
    except Exception:  # noqa: BLE001
        model = False
    return SetupStatus(
        ollama_running=running,
        ollama_version=version,
        model_present=model,
        detector_present=verify_model_file(detector_path or default_model_path()),
        gpu=gpu_fn(),
        edge=edge_fn(),
    )


def is_ready(status: SetupStatus) -> bool:
    """Ollama running, VLM model and detector present. GPU is only a warning."""
    return status.ollama_running and status.model_present and status.detector_present
