"""New plate, plate properties, settings, import and search dialogs."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QRadioButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import paths
from ..models import DEFAULT_FORMAT, PLATE_FORMATS, pretty_due, well_label
from ..repo import Store
from .widgets import SuggestLineEdit

CUSTOM_FORMAT = "Custom…"


class NewPlateDialog(QDialog):
    """Name, size and where the plate lives."""

    def __init__(self, store: Store, parent: QWidget | None = None,
                 suggested_name: str = ""):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("New plate")
        self.setMinimumWidth(420)

        form = QFormLayout()
        self.name = QLineEdit(suggested_name, self)
        self.name.setPlaceholderText("e.g. PT-014 expansion P3")
        self.name.selectAll()
        form.addRow("Name", self.name)

        self.format = QComboBox(self)
        for label in PLATE_FORMATS:
            self.format.addItem(label)
        self.format.addItem(CUSTOM_FORMAT)
        self.format.setCurrentText(DEFAULT_FORMAT)
        self.format.currentTextChanged.connect(self._on_format)
        form.addRow("Format", self.format)

        size_row = QHBoxLayout()
        self.rows = QSpinBox(self)
        self.rows.setRange(1, 26)
        self.cols = QSpinBox(self)
        self.cols.setRange(1, 48)
        self.rows.setPrefix("rows ")
        self.cols.setPrefix("columns ")
        size_row.addWidget(self.rows)
        size_row.addWidget(self.cols)
        self.size_widget = QWidget(self)
        self.size_widget.setLayout(size_row)
        form.addRow("", self.size_widget)

        self.location = SuggestLineEdit(lambda: store.suggestions("location"), self)
        self.location.setPlaceholderText("e.g. Incubator 2, shelf B")
        form.addRow("Location", self.location)

        self.owner = SuggestLineEdit(lambda: store.suggestions("owner"), self)
        self.owner.setPlaceholderText("who is looking after it")
        form.addRow("Owner", self.owner)

        self.notes = QPlainTextEdit(self)
        self.notes.setFixedHeight(64)
        form.addRow("Notes", self.notes)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("Create plate")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._on_format(self.format.currentText())

    def _on_format(self, text: str) -> None:
        custom = text == CUSTOM_FORMAT
        self.size_widget.setVisible(custom)
        if not custom:
            rows, cols = PLATE_FORMATS[text]
            self.rows.setValue(rows)
            self.cols.setValue(cols)

    def values(self) -> dict:
        fmt = self.format.currentText()
        rows, cols = (self.rows.value(), self.cols.value())
        if fmt == CUSTOM_FORMAT:
            fmt = f"{rows}×{cols} custom"
        return {
            "name": self.name.text().strip() or "Untitled plate",
            "fmt": fmt,
            "n_rows": rows,
            "n_cols": cols,
            "location": self.location.text().strip(),
            "owner": self.owner.text().strip(),
            "notes": self.notes.toPlainText().strip(),
        }


class PlatePropertiesDialog(QDialog):
    def __init__(self, store: Store, plate: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Plate properties — {plate['name']}")
        self.setMinimumWidth(420)
        form = QFormLayout()
        self.name = QLineEdit(plate["name"], self)
        form.addRow("Name", self.name)
        self.location = SuggestLineEdit(lambda: store.suggestions("location"), self)
        self.location.setText(plate["location"] or "")
        form.addRow("Location", self.location)
        self.owner = SuggestLineEdit(lambda: store.suggestions("owner"), self)
        self.owner.setText(plate["owner"] or "")
        form.addRow("Owner", self.owner)
        self.notes = QPlainTextEdit(plate["notes"] or "", self)
        self.notes.setFixedHeight(72)
        form.addRow("Notes", self.notes)
        self.archived = QCheckBox("Archived (hidden from the plate list)", self)
        self.archived.setChecked(bool(plate["archived"]))
        form.addRow("", self.archived)
        info = QLabel(f"{plate['format']} · {plate['n_rows']}×{plate['n_cols']}"
                      f" · created {plate['created_at'][:16]}", self)
        info.setObjectName("hint")
        form.addRow("", info)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "name": self.name.text().strip() or "Untitled plate",
            "location": self.location.text().strip(),
            "owner": self.owner.text().strip(),
            "notes": self.notes.toPlainText().strip(),
            "archived": 1 if self.archived.isChecked() else 0,
        }


class SettingsDialog(QDialog):
    """Where the data lives and how reminders behave."""

    def __init__(self, config: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)

        folder_group = QGroupBox("Data folder", self)
        folder_layout = QVBoxLayout(folder_group)
        explain = QLabel(
            "Everything - database and photos - lives in this folder. Point it at a"
            " synced folder (OneDrive, Dropbox, a network share) to work from more"
            " than one computer, but only run the app on one machine at a time.", self)
        explain.setWordWrap(True)
        explain.setObjectName("hint")
        folder_layout.addWidget(explain)
        row = QHBoxLayout()
        self.folder = QLineEdit(str(paths.data_dir()), self)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._browse)
        default = QPushButton("Use default", self)
        default.clicked.connect(lambda: self.folder.setText(str(paths.default_data_dir())))
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        row.addWidget(default)
        folder_layout.addLayout(row)
        layout.addWidget(folder_group)

        reminder_group = QGroupBox("Reminders", self)
        reminder_form = QFormLayout(reminder_group)
        self.notify_on_start = QCheckBox("Show a reminder when the app opens", self)
        self.notify_on_start.setChecked(config.get("notify_on_start", True))
        reminder_form.addRow("", self.notify_on_start)
        self.tray = QCheckBox("Keep an icon in the system tray / menu bar", self)
        self.tray.setChecked(config.get("tray_icon", True))
        reminder_form.addRow("", self.tray)
        self.horizon = QSpinBox(self)
        self.horizon.setRange(0, 60)
        self.horizon.setSuffix(" days ahead")
        self.horizon.setValue(int(config.get("reminder_days", 3)))
        reminder_form.addRow("Warn about wells due", self.horizon)
        self.interval = QSpinBox(self)
        self.interval.setRange(0, 24)
        self.interval.setSuffix(" hours (0 = off)")
        self.interval.setValue(int(config.get("recheck_hours", 4)))
        reminder_form.addRow("Re-check while running every", self.interval)
        layout.addWidget(reminder_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose a data folder",
                                                  self.folder.text())
        if chosen:
            self.folder.setText(chosen)

    def values(self) -> dict:
        return {
            "data_dir": str(Path(self.folder.text()).expanduser()),
            "notify_on_start": self.notify_on_start.isChecked(),
            "tray_icon": self.tray.isChecked(),
            "reminder_days": self.horizon.value(),
            "recheck_hours": self.interval.value(),
        }


class ImportDialog(QDialog):
    """Choose how an exported bundle should be brought in."""

    def __init__(self, bundle: str, manifest: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Import bundle")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        counts = manifest.get("counts", {})
        summary = (f"<b>{Path(bundle).name}</b><br>"
                   f"Exported {manifest.get('exported_at', 'unknown date')} by"
                   f" {manifest.get('app', 'unknown app')}"
                   f" {manifest.get('app_version', '')}<br>"
                   f"{counts.get('plates', '?')} plates ·"
                   f" {counts.get('cell_lines', '?')} cell lines ·"
                   f" {counts.get('patients', '?')} patients ·"
                   f" {counts.get('photo_files', '?')} photo files")
        header = QLabel(summary, self)
        header.setWordWrap(True)
        layout.addWidget(header)

        self.merge = QRadioButton("Merge into what I already have (recommended)", self)
        self.merge.setChecked(True)
        merge_hint = QLabel("Adds plates, wells and photos you do not have. Where both"
                            " copies have the same well, the one edited most recently"
                            " wins. Safe to run more than once.", self)
        self.replace = QRadioButton("Replace everything with this bundle", self)
        replace_hint = QLabel("Your current database and photos are moved to a"
                              " timestamped backup folder first.", self)
        for hint in (merge_hint, replace_hint):
            hint.setWordWrap(True)
            hint.setObjectName("hint")
            hint.setContentsMargins(22, 0, 0, 8)
        layout.addWidget(self.merge)
        layout.addWidget(merge_hint)
        layout.addWidget(self.replace)
        layout.addWidget(replace_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("Import")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def mode(self) -> str:
        return "merge" if self.merge.isChecked() else "replace"


class SearchDialog(QDialog):
    """Find a well anywhere in the database."""

    HEADERS = ("Plate", "Well", "Cell line", "Patient", "Experiment", "Status", "Due")

    def __init__(self, store: Store, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Search wells")
        self.resize(860, 520)
        self.chosen: tuple[int, int] | None = None

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit(text, self)
        self.query.setPlaceholderText("cell line, patient, experiment, plate, notes…")
        self.query.textChanged.connect(self.refresh)
        row.addWidget(self.query, 1)
        layout.addLayout(row)

        self.count = QLabel("", self)
        self.count.setObjectName("hint")
        layout.addWidget(self.count)

        self.table = QTableWidget(0, len(self.HEADERS), self)
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._accept_row)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        go = buttons.addButton("Go to well", QDialogButtonBox.AcceptRole)
        go.setObjectName("primary")
        go.clicked.connect(self._accept_row)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        rows = self.store.search_wells(self.query.text())
        self.table.setRowCount(len(rows))
        for index, well in enumerate(rows):
            values = (
                well.get("plate_name") or "",
                well_label(well["row_idx"], well["col_idx"]),
                well.get("cell_line") or "",
                well.get("patient_code") or "",
                well.get("experiment") or "",
                well.get("status") or "",
                pretty_due(well.get("due_date")),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, (well["plate_id"], well["id"]))
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        query = self.query.text().strip()
        self.count.setText(f"{len(rows)} matching well{'s' if len(rows) != 1 else ''}"
                           if query else "Type to search.")

    def _accept_row(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        self.chosen = item.data(Qt.UserRole) if item else None
        if self.chosen:
            self.accept()
