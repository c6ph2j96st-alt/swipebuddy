"""First-run / "Kontrollera installation" dialog."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import setup_check as sc
from ..config import AppConfig, load_config, save_config
from ..detection import default_model_path

DISCLAIMER = (
    "Viktigt innan du börjar\n\n"
    "• Automatisering bryter mot Tinders användarvillkor. Ditt konto kan stängas av.\n"
    "• Du använder programmet helt på egen risk.\n"
    "• Bilder på andra personer bearbetas lokalt på din dator och sparas aldrig."
)
GB = 1024 ** 3
REFRESH_MS = 3000


class _Signals(QObject):
    progress = Signal(int, int)
    line = Signal(str)
    done = Signal(object)  # None on success, else an Exception


class _Job(QRunnable):
    """Runs ``fn(signals)`` in the global thread pool and reports completion."""

    def __init__(self, fn: Callable[[_Signals], Any]) -> None:
        super().__init__()
        self.signals = _Signals()
        self._fn = fn

    def run(self) -> None:
        try:
            result: Any = self._fn(self.signals)
        except Exception as exc:  # noqa: BLE001
            result = exc
        try:
            self.signals.done.emit(result)
        except RuntimeError:
            pass  # the dialog was closed while the job ran


def default_status_provider(cfg: AppConfig) -> sc.SetupStatus:
    """Collect the real status (blocking; called from a pool thread)."""
    from ..vlm import OllamaClient

    client = OllamaClient(cfg.ollama_url, timeout_s=10)
    try:
        return sc.collect_status(cfg, client)
    finally:
        client.close()


class SetupDialog(QDialog):
    """Disclaimer plus a checklist of what is installed, with fix-it buttons."""

    _status_ready = Signal(object)

    def __init__(
        self,
        cfg: AppConfig | None = None,
        status_provider: Callable[[AppConfig], sc.SetupStatus] = default_status_provider,
        parent: QWidget | None = None,
        auto_refresh: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("SwipeBuddy – installation")
        self.setMinimumWidth(560)
        self._cfg = cfg or load_config()
        self._provider = status_provider
        self._status = sc.SetupStatus()
        self._busy = False
        self._cancel = threading.Event()
        self._pool = QThreadPool()
        self._jobs: set[_Job] = set()
        self._status_ready.connect(self._apply_status)

        self.disclaimer_check = QCheckBox("Jag förstår")
        self.disclaimer_check.setChecked(self._cfg.disclaimer_accepted)
        self.disclaimer_check.toggled.connect(self._update_done)
        grid = QGridLayout()
        self._marks: dict[str, QLabel] = {}
        self._texts: dict[str, QLabel] = {}
        self.btn_ollama_start = QPushButton("Starta Ollama")
        self.btn_ollama_get = QPushButton("Hämta Ollama")
        self.btn_model = QPushButton("Ladda ner")
        self.btn_detector = QPushButton("Ladda ner")
        self.btn_chromium = QPushButton("Installera Chromium")
        buttons = {
            "ollama": (self.btn_ollama_start, self.btn_ollama_get),
            "gpu": (), "model": (self.btn_model,), "detector": (self.btn_detector,),
            "browser": (self.btn_chromium,),
        }
        titles = {"ollama": "Ollama", "gpu": "Grafikkort", "model": "Språkmodell",
                  "detector": "Detektormodell", "browser": "Webbläsare"}
        for row, key in enumerate(titles):
            mark, text = QLabel("✗"), QLabel(titles[key])
            text.setWordWrap(True)
            self._marks[key], self._texts[key] = mark, text
            grid.addWidget(mark, row, 0)
            grid.addWidget(text, row, 1)
            for i, b in enumerate(buttons[key]):
                grid.addWidget(b, row, 2 + i)
        self.btn_ollama_start.clicked.connect(self._start_ollama)
        self.btn_ollama_get.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(sc.OLLAMA_DOWNLOAD_URL))
        )
        self.btn_model.clicked.connect(self._pull_model)
        self.btn_detector.clicked.connect(self._download_detector)
        self.btn_chromium.clicked.connect(self._install_chromium)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.progress_text = QLabel("")
        self.btn_cancel = QPushButton("Avbryt")
        self.btn_cancel.hide()
        self.btn_cancel.clicked.connect(self._cancel.set)
        self.btn_recheck = QPushButton("Kontrollera igen")
        self.btn_recheck.clicked.connect(self.refresh)
        self.buttons = QDialogButtonBox()
        self.btn_done = self.buttons.addButton("Klar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton("Avbryt", QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self._finish)
        self.buttons.rejected.connect(self.reject)

        intro = QLabel(DISCLAIMER)
        intro.setWordWrap(True)
        lay = QVBoxLayout(self)
        for w in (intro, self.disclaimer_check):
            lay.addWidget(w)
        lay.addLayout(grid)
        for w in (self.progress, self.progress_text, self.btn_cancel, self.btn_recheck, self.buttons):
            lay.addWidget(w)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._auto_refresh)
        self._apply_status(self._status)
        self._show_checking()
        self.refresh()
        if auto_refresh:
            self._timer.start()

    # -- status --------------------------------------------------------
    def _show_checking(self) -> None:
        """Neutral placeholder until the first background check has finished."""
        for key in self._marks:
            self._marks[key].setText("…")
            self._texts[key].setText("Kontrollerar…")
        for b in (self.btn_ollama_start, self.btn_ollama_get, self.btn_model,
                  self.btn_detector, self.btn_chromium):
            b.setVisible(False)

    def refresh(self) -> None:
        """Re-run all checks in a background thread."""
        cfg = self._cfg
        self._run(lambda s: self._provider(cfg), self._on_status_job, track_busy=False)

    def _auto_refresh(self) -> None:
        if not self._busy and not self._status.ollama_running:
            self.refresh()

    def _on_status_job(self, result: object) -> None:
        if isinstance(result, sc.SetupStatus):
            self._status_ready.emit(result)

    def _apply_status(self, st: sc.SetupStatus) -> None:
        self._status = st
        warn = sc.gpu_warning(st.gpu)
        rows = {
            "ollama": (st.ollama_running, f"Ollama kör (version {st.ollama_version or '?'})"
                       if st.ollama_running else "Ollama körs inte – starta det eller installera från ollama.com"),
            "gpu": (warn is None, " – ".join(
                x for x in ((f"{st.gpu.name}, {st.gpu.vram_mb} MB" if st.gpu else None), warn) if x)),
            "model": (st.model_present, f"Språkmodell {self._cfg.vlm_model} " + ("finns" if st.model_present else "saknas")),
            "detector": (st.detector_present, "Detektormodell " + ("finns" if st.detector_present else "saknas")),
            "browser": (st.edge, "Microsoft Edge hittades" if st.edge else "Edge saknas – Chromium behöver installeras"),
        }
        for key, (ok, text) in rows.items():
            self._marks[key].setText("✓" if ok else "✗")
            self._texts[key].setText(text)
        # GPU is only a warning: show a neutral mark rather than failing the checklist.
        if st.gpu is not None and warn:
            self._marks["gpu"].setText("!")
        idle = not self._busy
        self.btn_ollama_start.setVisible(not st.ollama_running and sc.find_ollama_app() is not None)
        self.btn_ollama_get.setVisible(not st.ollama_running)
        self.btn_model.setVisible(st.ollama_running and not st.model_present)
        self.btn_detector.setVisible(not st.detector_present)
        self.btn_chromium.setVisible(not st.edge)
        for b in (self.btn_ollama_start, self.btn_ollama_get, self.btn_model, self.btn_detector, self.btn_chromium):
            b.setEnabled(idle)
        self._update_done()

    def _update_done(self, *_: object) -> None:
        self.btn_done.setEnabled(
            self.disclaimer_check.isChecked() and sc.is_ready(self._status) and not self._busy
        )

    def _finish(self) -> None:
        self._cancel.set()
        cfg = load_config()
        cfg.disclaimer_accepted = True
        save_config(cfg)
        self._timer.stop()
        self.accept()

    def reject(self) -> None:
        self._cancel.set()
        self._timer.stop()
        super().reject()

    # -- jobs ----------------------------------------------------------
    def _run(self, fn: Callable[[_Signals], Any], on_done: Callable[[object], None], track_busy: bool = True) -> _Job:
        job = _Job(fn)
        job.setAutoDelete(False)
        self._jobs.add(job)
        if track_busy:
            self._busy = True
            self._cancel.clear()
            self._apply_status(self._status)
        queued = Qt.ConnectionType.QueuedConnection
        job.signals.progress.connect(self._on_progress, queued)
        job.signals.line.connect(self.progress_text.setText, queued)

        def finished(result: object) -> None:
            self._jobs.discard(job)
            if track_busy:
                self._busy = False
                self.progress.hide()
                self.btn_cancel.hide()
            on_done(result)
            if track_busy:
                self.refresh()

        job.signals.done.connect(finished, queued)
        self._pool.start(job)
        return job

    def _show_progress(self, indeterminate: bool, cancellable: bool) -> None:
        self.progress.setRange(0, 0 if indeterminate else 1000)
        self.progress.setValue(0)
        self.progress.show()
        self.btn_cancel.setVisible(cancellable)
        self.progress_text.setText("")

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(done * 1000 / total)))
            self.progress_text.setText(f"{done / GB:.2f} / {total / GB:.2f} GB")

    def _report(self, result: object) -> None:
        if isinstance(result, Exception):
            self.progress_text.setText(f"Misslyckades: {type(result).__name__}")

    def _start_ollama(self) -> None:
        app = sc.find_ollama_app()
        if app is not None:
            try:
                sc.start_ollama(app)
                self.progress_text.setText("Startar Ollama …")
            except OSError:
                self.progress_text.setText("Kunde inte starta Ollama")

    def _pull_model(self) -> None:
        cfg = self._cfg

        def work(sig: _Signals) -> None:
            from ..vlm import OllamaClient

            client = OllamaClient(cfg.ollama_url, timeout_s=30)
            try:
                client.pull(cfg.vlm_model, lambda d, t, s: sig.progress.emit(d, t), self._cancel)
            finally:
                client.close()

        self._show_progress(False, True)
        self._run(work, self._report)

    def _download_detector(self) -> None:
        def work(sig: _Signals) -> None:
            sc.download_detector(default_model_path(), sig.progress.emit, self._cancel)

        self._show_progress(False, True)
        self._run(work, self._report)

    def _install_chromium(self) -> None:
        def work(sig: _Signals) -> None:
            if sc.install_chromium(sig.line.emit) != 0:
                raise RuntimeError("install failed")
            cfg = load_config()
            cfg.browser_channel = "chromium"
            save_config(cfg)

        self._show_progress(True, False)
        self._run(work, self._report)
