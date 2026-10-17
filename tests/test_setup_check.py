"""Tests for swipebuddy.setup_check (no network, no real subprocesses)."""
from __future__ import annotations

import hashlib
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from swipebuddy import setup_check as sc
from swipebuddy.config import AppConfig


def _run(stdout: str = "", rc: int = 0):
    return lambda *a, **k: SimpleNamespace(returncode=rc, stdout=stdout)


def test_gpu_ok() -> None:
    info = sc.check_gpu(_run("NVIDIA GeForce RTX 3060, 12288\n"))
    assert info == sc.GpuInfo("NVIDIA GeForce RTX 3060", 12288)
    assert sc.gpu_warning(info) is None


def test_gpu_missing_and_failures() -> None:
    def boom(*a, **k):
        raise FileNotFoundError

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("x", 5)

    assert sc.check_gpu(boom) is None
    assert sc.check_gpu(slow) is None
    assert sc.check_gpu(_run("", rc=9)) is None


@pytest.mark.parametrize("out", ["", "garbage", "name, notanumber", ", 100", "x, 0"])
def test_gpu_garbage(out: str) -> None:
    assert sc.check_gpu(_run(out)) is None


def test_gpu_warnings() -> None:
    assert "Ingen NVIDIA-GPU" in sc.gpu_warning(None)
    assert sc.gpu_warning(sc.GpuInfo("x", 6144))
    assert sc.gpu_warning(sc.GpuInfo("x", 8192)) is None


PAYLOAD = b"x" * 200_000


def _factory(handler):
    return lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw)


@pytest.fixture
def ok_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    good = hashlib.sha256(PAYLOAD).hexdigest()
    monkeypatch.setattr(
        sc, "verify_model_file", lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == good
    )


def test_download_happy(tmp_path: Path, ok_hash) -> None:
    calls: list[tuple[int, int]] = []
    dest = tmp_path / "m" / "d.onnx"
    f = _factory(lambda req: httpx.Response(200, content=PAYLOAD))
    assert sc.download_detector(dest, lambda d, t: calls.append((d, t)), threading.Event(), f)
    assert dest.read_bytes() == PAYLOAD
    assert not dest.with_suffix(".part").exists()
    assert calls[-1][0] == len(PAYLOAD) and calls[-1][1] == len(PAYLOAD)


def test_download_hash_mismatch(tmp_path: Path, ok_hash) -> None:
    dest = tmp_path / "d.onnx"
    f = _factory(lambda req: httpx.Response(200, content=b"bad"))
    with pytest.raises(ValueError):
        sc.download_detector(dest, lambda d, t: None, threading.Event(), f)
    assert not dest.exists() and not dest.with_suffix(".part").exists()


def test_download_cancel(tmp_path: Path, ok_hash) -> None:
    dest = tmp_path / "d.onnx"
    cancel = threading.Event()
    f = _factory(lambda req: httpx.Response(200, content=PAYLOAD))
    assert sc.download_detector(dest, lambda d, t: cancel.set(), cancel, f) is False
    assert not dest.exists() and not dest.with_suffix(".part").exists()


def test_download_http_error_cleans_up(tmp_path: Path) -> None:
    dest = tmp_path / "d.onnx"
    f = _factory(lambda req: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        sc.download_detector(dest, lambda d, t: None, threading.Event(), f)
    assert not dest.with_suffix(".part").exists()


class FakeClient:
    def __init__(self, running: bool, has: bool) -> None:
        self.running, self.has = running, has

    def is_running(self) -> bool:
        return self.running

    def version(self) -> str | None:
        return "0.5.0" if self.running else None

    def has_model(self, name: str) -> bool:
        return self.has


@pytest.mark.parametrize(
    "running,has,det,ready",
    [(1, 1, 1, True), (0, 1, 1, False), (1, 0, 1, False), (1, 1, 0, False), (0, 0, 0, False)],
)
def test_is_ready(monkeypatch, tmp_path, running, has, det, ready) -> None:
    monkeypatch.setattr(sc, "verify_model_file", lambda p: bool(det))
    st = sc.collect_status(
        AppConfig(), FakeClient(bool(running), bool(has)), tmp_path / "x", lambda: None, lambda: False
    )
    assert st.gpu is None and st.edge is False
    assert sc.is_ready(st) is ready


def test_is_ready_ignores_gpu() -> None:
    st = sc.SetupStatus(True, "1", True, True, None, False)
    assert sc.is_ready(st)


def test_edge_available(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    assert not sc.edge_available()
    exe = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert sc.edge_available()
