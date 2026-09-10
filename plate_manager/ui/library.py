"""Library: the patients, cell lines and experiments the wells refer to.

These rows are created automatically the first time a name is typed into a
well, so this tab is for tidying up - adding a diagnosis, linking a line to a
donor, renaming (every well follows the rename), retiring an old experiment.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
                               QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..repo import Store


class _LookupTable(QWidget):
    """One editable table over a lookup table; counts stay read-only."""

    changed = Signal()

    def __init__(self, store: Store, table: str, headers, fields, hint: str,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.table_name = table
        self.fields = fields          # column index -> field name, None = read-only
        self._loading = False

        layout = QVBoxLayout(self)
        note = QLabel(hint, self)
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.table = QTableWidget(0, len(headers), self)
        self.table.setHorizontalHeaderLabels(list(headers))
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        add = QPushButton("Add…", self)
        add.clicked.connect(self.add_row)
        delete = QPushButton("Delete", self)
        delete.clicked.connect(self.delete_row)
        buttons.addWidget(add)
        buttons.addWidget(delete)
        buttons.addStretch(1)
        self.extra_buttons = buttons
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ filling
    def fill(self, rows: list[dict]) -> None:
        self._loading = True
        try:
            self.table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                for column, field in enumerate(self.fields):
                    # a None field is a read-only column: a usage count or a link
                    value = row.get(field) if field else self._counter_text(row, column)
                    item = QTableWidgetItem("" if value is None else str(value))
                    if field is None:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                        item.setForeground(QColor("#7a828e"))
                    if column == 0:
                        item.setData(Qt.UserRole, row["id"])
                    self.table.setItem(index, column, item)
            self.table.resizeColumnsToContents()
        finally:
            self._loading = False

    def _counter_text(self, row: dict, column: int) -> str:
        raise NotImplementedError

    def _row_id(self, row: int) -> int | None:
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        field = self.fields[item.column()]
        if field is None:
            return
        row_id = self._row_id(item.row())
        if row_id is None:
            return
        try:
            self.store.update_lookup(self.table_name, row_id, **{field: item.text().strip()})
        except Exception as exc:                      # unique-name clashes land here
            QMessageBox.warning(self, "Could not save", str(exc))
        self.changed.emit()

    def selected_id(self) -> int | None:
        row = self.table.currentRow()
        return self._row_id(row) if row >= 0 else None

    def add_row(self) -> None:
        raise NotImplementedError

    def delete_row(self) -> None:
        row_id = self.selected_id()
        if row_id is None:
            return
        name = self.table.item(self.table.currentRow(), 0).text()
        answer = QMessageBox.question(
            self, "Delete entry",
            f"Delete “{name}”?\n\nWells that referred to it keep their other details"
            " but lose this link.")
        if answer == QMessageBox.Yes:
            self.store.delete_lookup(self.table_name, row_id)
            self.changed.emit()


class PatientsTable(_LookupTable):
    def __init__(self, store: Store, parent: QWidget | None = None):
        super().__init__(store, "patients",
                        ("Patient ID", "Diagnosis / detail", "Notes", "Cell lines", "Wells"),
                        ("code", "diagnosis", "notes", None, None),
                        "Use de-identified codes here - this is a lab tracking tool,"
                        " not a clinical record.", parent)

    def _counter_text(self, row: dict, column: int) -> str:
        return str(row["n_lines"] if column == 3 else row["n_wells"])

    def add_row(self) -> None:
        code, ok = QInputDialog.getText(self, "Add patient", "Patient / donor ID:")
        if ok and code.strip():
            self.store.ensure_patient(code)
            self.store.conn.commit()
            self.changed.emit()


class CellLinesTable(_LookupTable):
    def __init__(self, store: Store, parent: QWidget | None = None):
        super().__init__(store, "cell_lines",
                        ("Cell line", "Species", "Tissue", "Notes", "Patient", "Wells"),
                        ("name", "species", "tissue", "notes", None, None),
                        "Renaming a line here renames it everywhere it is used.", parent)
        link = QPushButton("Link to patient…", self)
        link.clicked.connect(self.link_patient)
        self.extra_buttons.insertWidget(2, link)

    def _counter_text(self, row: dict, column: int) -> str:
        return str(row["patient_code"] or "—") if column == 4 else str(row["n_wells"])

    def add_row(self) -> None:
        name, ok = QInputDialog.getText(self, "Add cell line", "Cell line name:")
        if ok and name.strip():
            self.store.ensure_cell_line(name)
            self.store.conn.commit()
            self.changed.emit()

    def link_patient(self) -> None:
        row_id = self.selected_id()
        if row_id is None:
            return
        codes = self.store.suggestions("patient_code")
        current = self.table.item(self.table.currentRow(), 4).text()
        code, ok = QInputDialog.getItem(self, "Link to patient",
                                        "Patient / donor ID (type a new one if needed):",
                                        codes or [""], max(codes.index(current)
                                                           if current in codes else 0, 0),
                                        True)
        if ok:
            self.store.set_cell_line_patient(row_id, code)
            self.changed.emit()


class ExperimentsTable(_LookupTable):
    def __init__(self, store: Store, parent: QWidget | None = None):
        super().__init__(store, "experiments",
                        ("Experiment", "Owner", "Notes", "Wells"),
                        ("name", "owner", "notes", None),
                        "Experiments are suggested in the well editor as soon as they"
                        " exist here.", parent)

    def _counter_text(self, row: dict, column: int) -> str:
        return str(row["n_wells"])

    def add_row(self) -> None:
        name, ok = QInputDialog.getText(self, "Add experiment", "Experiment name:")
        if ok and name.strip():
            self.store.ensure_experiment(name)
            self.store.conn.commit()
            self.changed.emit()


class LibraryTab(QWidget):
    """Tabs over the three lookup tables."""

    dataChanged = Signal()

    def __init__(self, store: Store, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        heading = QLabel("Library", self)
        heading.setObjectName("heading")
        layout.addWidget(heading)

        self.tabs = QTabWidget(self)
        self.patients = PatientsTable(store, self)
        self.cell_lines = CellLinesTable(store, self)
        self.experiments = ExperimentsTable(store, self)
        self.tabs.addTab(self.cell_lines, "Cell lines")
        self.tabs.addTab(self.patients, "Patients / donors")
        self.tabs.addTab(self.experiments, "Experiments")
        layout.addWidget(self.tabs, 1)

        for table in (self.patients, self.cell_lines, self.experiments):
            table.changed.connect(self._on_changed)
        self.refresh()

    def _on_changed(self) -> None:
        self.refresh()
        self.dataChanged.emit()

    def refresh(self) -> None:
        self.patients.fill(self.store.list_patients())
        self.cell_lines.fill(self.store.list_cell_lines())
        self.experiments.fill(self.store.list_experiments())
