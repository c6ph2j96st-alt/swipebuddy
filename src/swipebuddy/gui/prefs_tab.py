"""Preferences tab: free text, attribute checkboxes and age hint."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import ATTRIBUTE_LABELS, MAX_FREE_TEXT, Preferences, load_config, save_config, validate

AGE_NONE = 17  # spinbox minimum, shown as "ingen"
AGE_MAX = 99


def _age_spin() -> QSpinBox:
    s = QSpinBox()
    s.setRange(AGE_NONE, AGE_MAX)
    s.setSpecialValueText("ingen")
    return s


class PrefsTab(QWidget):
    """Edits ``config.preferences`` and saves on demand."""

    saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.free_text = QPlainTextEdit()
        self.free_text.setPlaceholderText("Beskriv vad du gillar och inte gillar (max 2000 tecken)")
        self.free_text.textChanged.connect(self._limit_text)
        self.checks = {key: QCheckBox(label) for key, label in ATTRIBUTE_LABELS.items()}
        self.age_min = _age_spin()
        self.age_max = _age_spin()
        self.btn_save = QPushButton("Spara")
        self.btn_save.clicked.connect(self.save)
        self.note = QLabel("")

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Fritext"))
        lay.addWidget(self.free_text, 1)
        for c in self.checks.values():
            lay.addWidget(c)
        form = QFormLayout()
        form.addRow("Ålder från (hint)", self.age_min)
        form.addRow("Ålder till (hint)", self.age_max)
        lay.addLayout(form)
        lay.addWidget(self.btn_save)
        lay.addWidget(self.note)
        self.load()

    def _limit_text(self) -> None:
        text = self.free_text.toPlainText()
        if len(text) > MAX_FREE_TEXT:
            cursor = self.free_text.textCursor()
            self.free_text.setPlainText(text[:MAX_FREE_TEXT])
            self.free_text.setTextCursor(cursor)

    def load(self) -> None:
        """Populate widgets from the saved config."""
        p = load_config().preferences
        self.free_text.setPlainText(p.free_text)
        for key, box in self.checks.items():
            box.setChecked(bool(p.attributes.get(key, False)))
        self.age_min.setValue(p.age_hint_min if p.age_hint_min is not None else AGE_NONE)
        self.age_max.setValue(p.age_hint_max if p.age_hint_max is not None else AGE_NONE)

    def save(self) -> None:
        """Validate and write preferences, keeping all other settings."""
        cfg = load_config()
        lo, hi = self.age_min.value(), self.age_max.value()
        cfg.preferences = Preferences(
            free_text=self.free_text.toPlainText(),
            attributes={k: b.isChecked() for k, b in self.checks.items()},
            age_hint_min=None if lo == AGE_NONE else lo,
            age_hint_max=None if hi == AGE_NONE else hi,
        )
        save_config(validate(cfg))
        self.load()
        self.note.setText("Sparat – gäller från nästa Start")
        self.saved.emit()
