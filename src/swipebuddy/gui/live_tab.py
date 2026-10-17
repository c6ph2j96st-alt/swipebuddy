"""Live tab: session controls, current verdict and suggestion buttons."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..engine import (
    AwaitingConfirm,
    CapReached,
    Error,
    Evaluating,
    NeedsUser,
    Paused,
    Status,
    Stopped,
    Swiped,
    VerdictReady,
)

BANNER_WARN = "background:#fff3a3;color:#5c4a00;padding:8px;border:1px solid #d4b900;font-weight:bold;"
BANNER_ERR = "background:#ffd6d6;color:#7a0000;padding:8px;border:1px solid #c00000;font-weight:bold;"
COLOR_LIKE = "color:#1b8a3a;"
COLOR_PASS = "color:#c0392b;"


class LiveTab(QWidget):
    """Shows engine state; emits user intents as signals."""

    open_browser = Signal()
    start = Signal()
    stop = Signal()
    confirm = Signal(str)
    skip = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._threshold = 70
        self.btn_open = QPushButton("Öppna webbläsare")
        self.btn_start = QPushButton("Start")
        self.btn_stop = QPushButton("Stopp")
        self.btn_stop.setEnabled(False)
        self.mode_label = QLabel()
        self.status_label = QLabel("Redo")
        self.status_label.setWordWrap(True)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.hide()
        self.decision_label = QLabel("–")
        self.decision_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.decision_label.setStyleSheet("font-size:48px;font-weight:bold;")
        self.score_bar = QProgressBar()
        self.score_bar.setRange(0, 100)
        self.score_bar.setValue(0)
        self.score_bar.setFormat("Score –")
        self.reason_label = QLabel()
        self.reason_label.setWordWrap(True)
        self.reason_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_like = QPushButton("👍 Like")
        self.btn_pass = QPushButton("👎 Pass")
        self.btn_skip = QPushButton("Hoppa över")
        self.counter_label = QLabel("Idag: 👍 0   👎 0")
        self._suggest_buttons = (self.btn_like, self.btn_pass, self.btn_skip)

        top = QHBoxLayout()
        for b in (self.btn_open, self.btn_start, self.btn_stop):
            top.addWidget(b)
        top.addStretch(1)
        top.addWidget(self.mode_label)
        sug = QHBoxLayout()
        for b in self._suggest_buttons:
            sug.addWidget(b)
        lay = QVBoxLayout(self)
        for item in (top, self.banner, self.status_label, self.decision_label, self.score_bar,
                     self.reason_label, sug, self.counter_label):
            lay.addLayout(item) if hasattr(item, "addWidget") else lay.addWidget(item)
        lay.addStretch(1)

        self.btn_open.clicked.connect(self.open_browser)
        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_like.clicked.connect(lambda: self.confirm.emit("like"))
        self.btn_pass.clicked.connect(lambda: self.confirm.emit("pass"))
        self.btn_skip.clicked.connect(self.skip)
        self.set_mode("suggest", 70)
        self.set_running(False)

    # -- state setters -------------------------------------------------
    def set_mode(self, mode: str, threshold: int) -> None:
        """Update the mode indicator and the threshold shown in the score text."""
        self._threshold = threshold
        if mode == "auto":
            self.mode_label.setText("Läge: AUTO")
            self.mode_label.setStyleSheet("color:#c00000;font-weight:bold;")
        else:
            self.mode_label.setText("Läge: Förslag")
            self.mode_label.setStyleSheet("")

    def set_running(self, running: bool) -> None:
        """Enable/disable controls depending on whether an engine run is active."""
        self.btn_start.setEnabled(not running)
        self.btn_open.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        if not running:
            self._set_suggest_enabled(False)

    def update_counts(self, like: int, pass_: int, cap: int | None = None) -> None:
        tail = f"   Tak: {cap}" if cap is not None else ""
        self.counter_label.setText(f"Idag: 👍 {like}   👎 {pass_}{tail}")

    def _set_suggest_enabled(self, on: bool) -> None:
        for b in self._suggest_buttons:
            b.setEnabled(on)

    def _banner(self, text: str, style: str) -> None:
        self.banner.setStyleSheet(style)
        self.banner.setText(text)
        self.banner.show()

    def _clear_verdict(self) -> None:
        self.decision_label.setText("–")
        self.decision_label.setStyleSheet("font-size:48px;font-weight:bold;")
        self.score_bar.setValue(0)
        self.score_bar.setFormat("Score –")
        self.reason_label.clear()

    # -- engine events -------------------------------------------------
    def handle_event(self, ev: object) -> None:
        """Update widgets from an engine event."""
        if isinstance(ev, Status):
            self.status_label.setText(ev.text)
        elif isinstance(ev, Evaluating):
            self.banner.hide()
            self._clear_verdict()
            self._set_suggest_enabled(False)
            self.status_label.setText(f"Bedömer profil {ev.profile_no} ({ev.images} bilder)")
        elif isinstance(ev, VerdictReady):
            like = ev.final_decision == "like"
            self.decision_label.setText("LIKE" if like else "PASS")
            self.decision_label.setStyleSheet(
                "font-size:48px;font-weight:bold;" + (COLOR_LIKE if like else COLOR_PASS)
            )
            self.score_bar.setValue(max(0, min(100, ev.verdict.score)))
            self.score_bar.setFormat(f"Score %v / 100 (tröskel {self._threshold})")
            self.reason_label.setText(ev.verdict.reason)
            self.set_mode(ev.mode, self._threshold)
        elif isinstance(ev, AwaitingConfirm):
            self._set_suggest_enabled(True)
            self.status_label.setText("Väntar på ditt val")
        elif isinstance(ev, Swiped):
            self._set_suggest_enabled(False)
            self.update_counts(ev.today_like, ev.today_pass, ev.cap)
            self.status_label.setText("Swipead " + ("höger" if ev.decision == "like" else "vänster"))
        elif isinstance(ev, Paused):
            self.status_label.setText(f"Pausar i {int(ev.seconds)} s")
        elif isinstance(ev, CapReached):
            self._banner(f"Dagstaket är nått ({ev.total} av {ev.cap}). Stoppar.", BANNER_WARN)
            self.status_label.setText("Dagstak nått")
        elif isinstance(ev, NeedsUser):
            self._banner(f"Din hjälp behövs: {ev.reason}", BANNER_WARN)
        elif isinstance(ev, Error):
            self._banner(ev.message, BANNER_ERR)
        elif isinstance(ev, Stopped):
            self.set_running(False)
            self.status_label.setText("Stoppad")
