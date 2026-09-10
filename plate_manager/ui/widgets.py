"""Small reusable input widgets.

The two that matter:

* `SuggestLineEdit` - a text box that offers everything already typed into that
  field anywhere in the database, so a patient ID or medium is only ever spelled
  out once.
* `OptionalDateEdit` - a date box that can genuinely be empty, which the plain
  QDateEdit cannot.
"""
from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QDate, QStringListModel, Qt, Signal
from PySide6.QtWidgets import (QCompleter, QDateEdit, QHBoxLayout, QLineEdit, QSpinBox,
                               QToolButton, QWidget)

EMPTY_DATE = QDate(1900, 1, 1)


class SuggestLineEdit(QLineEdit):
    """Line edit with an inline, substring-matching suggestion list."""

    def __init__(self, provider: Callable[[], Iterable[str]] | None = None,
                 parent: QWidget | None = None, extra: Iterable[str] = ()):
        super().__init__(parent)
        self._provider = provider
        self._extra = list(extra)
        self._model = QStringListModel(self)
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setMaxVisibleItems(12)
        self.setCompleter(completer)

    def set_provider(self, provider: Callable[[], Iterable[str]]) -> None:
        self._provider = provider

    def refresh_suggestions(self) -> None:
        values = list(self._provider() if self._provider else [])
        for item in self._extra:
            if item not in values:
                values.append(item)
        self._model.setStringList(values)

    def focusInEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.refresh_suggestions()
        super().focusInEvent(event)


class OptionalDateEdit(QWidget):
    """Date picker that supports 'no date', plus one-click relative dates."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None, quick_days: Iterable[int] = ()):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.edit = QDateEdit(self)
        self.edit.setMinimumWidth(104)
        self.edit.setMaximumWidth(150)
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd")
        self.edit.setMinimumDate(EMPTY_DATE)
        self.edit.setSpecialValueText("—")   # em dash = empty
        self.edit.setDate(EMPTY_DATE)
        self.edit.dateChanged.connect(lambda _: self.changed.emit())
        layout.addWidget(self.edit)

        for days in quick_days:
            label = "Today" if days == 0 else f"+{days}d"
            button = QToolButton(self)
            button.setText(label)
            button.setAutoRaise(True)
            button.setFixedHeight(24)
            button.setFixedWidth(44 if days == 0 else 32)
            button.setToolTip("Set to today" if days == 0 else f"Set to {days} days from now")
            button.clicked.connect(lambda _=False, d=days: self.set_relative(d))
            layout.addWidget(button)

        clear = QToolButton(self)
        clear.setText("×")
        clear.setAutoRaise(True)
        clear.setFixedWidth(22)
        clear.setToolTip("Clear the date")
        clear.clicked.connect(self.clear_date)
        layout.addWidget(clear)
        layout.addStretch(1)

    def value(self) -> str:
        date = self.edit.date()
        return "" if date == EMPTY_DATE else date.toString("yyyy-MM-dd")

    def set_value(self, text: str | None) -> None:
        if not text:
            self.edit.setDate(EMPTY_DATE)
            return
        date = QDate.fromString(str(text)[:10], "yyyy-MM-dd")
        self.edit.setDate(date if date.isValid() else EMPTY_DATE)

    def set_relative(self, days: int) -> None:
        self.edit.setDate(QDate.currentDate().addDays(days))

    def clear_date(self) -> None:
        was_empty = self.edit.date() == EMPTY_DATE
        self.edit.setDate(EMPTY_DATE)
        if was_empty:
            # dateChanged did not fire, but the user did ask for "no date"
            self.changed.emit()

    def set_placeholder(self, text: str) -> None:
        """Used to show '(multiple values)' when several wells are selected."""
        self.edit.setSpecialValueText(text or "—")


class ConfluenceSpin(QSpinBox):
    """0-100 % with a real 'not recorded' state."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setRange(-1, 100)
        self.setValue(-1)
        self.setSpecialValueText("—")
        self.setSuffix(" %")
        self.setSingleStep(5)

    def value_or_blank(self) -> str:
        value = super().value()
        return "" if value < 0 else str(value)

    def set_value_or_blank(self, value) -> None:
        self.setValue(-1 if value in (None, "") else int(value))
