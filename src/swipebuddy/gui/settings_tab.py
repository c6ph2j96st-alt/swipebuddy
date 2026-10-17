"""Settings tab: threshold, mode, caps, pacing, model and browser."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import load_config, save_config, validate

VLM_MODELS = ["qwen2.5vl:7b", "qwen2.5vl:3b", "qwen3-vl:8b"]
AUTO_WARNING = (
    "Auto-läget swipar utan att du bekräftar varje profil. Automatisering strider mot "
    "Tinders användarvillkor och kontot kan stängas av. Använd på egen risk.\n\n"
    "Vill du aktivera Auto-läget?"
)


def _dspin(lo: float, hi: float, suffix: str = " s") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(1)
    s.setSuffix(suffix)
    return s


def _ispin(lo: int, hi: int) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    return s


class SettingsTab(QWidget):
    """Edits the non-preference settings; changes apply from the next Start."""

    saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.threshold = _ispin(0, 100)
        self.slider.valueChanged.connect(self.threshold.setValue)
        self.threshold.valueChanged.connect(self.slider.setValue)
        self.radio_suggest = QRadioButton("Förslag (du bekräftar varje swipe)")
        self.radio_auto = QRadioButton("Auto (swipar själv)")
        self.radio_auto.toggled.connect(self._on_auto_toggled)
        self.daily_cap = _ispin(1, 1000)
        self.delay_min = _dspin(0, 600)
        self.delay_max = _dspin(0, 600)
        self.pause_every_min = _ispin(0, 1000)
        self.pause_every_max = _ispin(0, 1000)
        self.pause_min = _dspin(0, 3600)
        self.pause_max = _dspin(0, 3600)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(VLM_MODELS)
        self.channel = QComboBox()
        self.channel.addItems(["msedge", "chromium"])
        self.btn_save = QPushButton("Spara")
        self.btn_save.clicked.connect(self.save)
        self.note = QLabel("Ändringar gäller från nästa Start.")

        form = QFormLayout()
        row = QHBoxLayout()
        row.addWidget(self.slider, 1)
        row.addWidget(self.threshold)
        form.addRow("Tröskel (0–100)", row)
        form.addRow("Läge", self.radio_suggest)
        form.addRow("", self.radio_auto)
        form.addRow("Dagstak", self.daily_cap)
        form.addRow("Fördröjning min", self.delay_min)
        form.addRow("Fördröjning max", self.delay_max)
        form.addRow("Paus var min (profiler)", self.pause_every_min)
        form.addRow("Paus var max (profiler)", self.pause_every_max)
        form.addRow("Pauslängd min", self.pause_min)
        form.addRow("Pauslängd max", self.pause_max)
        form.addRow("VLM-modell", self.model)
        form.addRow("Webbläsare", self.channel)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.btn_save)
        lay.addWidget(self.note)
        lay.addStretch(1)
        self.load()

    def _on_auto_toggled(self, checked: bool) -> None:
        if not checked:
            return
        answer = QMessageBox.warning(
            self, "Auto-läge", AUTO_WARNING,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.radio_suggest.setChecked(True)

    def load(self) -> None:
        """Populate widgets from the saved config (no Auto prompt)."""
        c = load_config()
        self.radio_auto.blockSignals(True)
        (self.radio_auto if c.mode == "auto" else self.radio_suggest).setChecked(True)
        self.radio_auto.blockSignals(False)
        self.threshold.setValue(c.threshold)
        self.daily_cap.setValue(c.daily_cap)
        self.delay_min.setValue(c.delay_min_s)
        self.delay_max.setValue(c.delay_max_s)
        self.pause_every_min.setValue(c.pause_every_min)
        self.pause_every_max.setValue(c.pause_every_max)
        self.pause_min.setValue(c.pause_min_s)
        self.pause_max.setValue(c.pause_max_s)
        self.model.setCurrentText(c.vlm_model)
        self.channel.setCurrentText(c.browser_channel)

    def save(self) -> None:
        """Validate (clamping ranges) and write settings, then reload the clamped values."""
        c = load_config()
        c.threshold = self.threshold.value()
        c.mode = "auto" if self.radio_auto.isChecked() else "suggest"
        c.daily_cap = self.daily_cap.value()
        c.delay_min_s, c.delay_max_s = self.delay_min.value(), self.delay_max.value()
        c.pause_every_min, c.pause_every_max = self.pause_every_min.value(), self.pause_every_max.value()
        c.pause_min_s, c.pause_max_s = self.pause_min.value(), self.pause_max.value()
        c.vlm_model = self.model.currentText()
        c.browser_channel = self.channel.currentText()  # type: ignore[assignment]
        save_config(validate(c))
        self.load()
        self.note.setText("Sparat – gäller från nästa Start.")
        self.saved.emit()
