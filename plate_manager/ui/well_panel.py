"""The editor for whatever is selected on the plate.

One well selected: fields save themselves half a second after you stop typing,
and the well's photo timeline and history are shown underneath.

Several wells selected: the fields show the shared value (or "multiple
values"), and only the fields you actually touch are written - so you can set
one experiment across a column without wiping the cell lines already in it.
"""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from ..models import DUE_TASKS, STATUSES, parse_date, pretty_due, well_label
from ..repo import EDITABLE_FIELDS, Store
from .photo_panel import PhotoPanel
from .widgets import ConfluenceSpin, OptionalDateEdit, SuggestLineEdit

MULTIPLE = "— multiple values —"
MULTIPLE_SHORT = "— multiple —"     # for the narrow date and percentage boxes
AUTOSAVE_MS = 500


class WellPanel(QWidget):
    dataChanged = Signal()          # something was written; refresh the plate view

    def __init__(self, store: Store, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self._wells: list[dict] = []
        self._loading = False
        self._dirty: set[str] = set()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(AUTOSAVE_MS)
        self._save_timer.timeout.connect(self._autosave)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.heading = QLabel("No well selected", self)
        self.heading.setObjectName("heading")
        outer.addWidget(self.heading)

        self.subheading = QLabel("Click a well on the plate. Drag, shift-click or"
                                 " click a row/column header to select several.", self)
        self.subheading.setObjectName("hint")
        self.subheading.setWordWrap(True)
        outer.addWidget(self.subheading)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget(scroll)
        self.body = body
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.fields: dict[str, QWidget] = {}
        self._placeholders: dict[str, str] = {}
        body_layout.addWidget(self._build_contents_group())
        body_layout.addWidget(self._build_action_group())
        body_layout.addWidget(self._build_notes_group())
        self.photo_group = self._build_photo_group()
        body_layout.addWidget(self.photo_group)
        self.history_group = self._build_history_group()
        body_layout.addWidget(self.history_group)
        body_layout.addStretch(1)
        for field, widget in self.fields.items():
            if hasattr(widget, "placeholderText"):
                self._placeholders[field] = widget.placeholderText()

        # bulk-apply bar
        self.apply_bar = QWidget(self)
        bar = QHBoxLayout(self.apply_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.apply_button = QPushButton("Apply to selection", self.apply_bar)
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.apply_changes)
        self.revert_button = QPushButton("Revert", self.apply_bar)
        self.revert_button.clicked.connect(self.revert)
        self.status_label = QLabel("", self.apply_bar)
        self.status_label.setObjectName("hint")
        bar.addWidget(self.status_label, 1)
        bar.addWidget(self.revert_button)
        bar.addWidget(self.apply_button)
        outer.addWidget(self.apply_bar)

        self.set_wells([])

    # ------------------------------------------------------------------ layout
    def _suggest(self, field: str, extra=()) -> SuggestLineEdit:
        widget = SuggestLineEdit(lambda f=field: self.store.suggestions(f), self, extra=extra)
        widget.textEdited.connect(lambda _, f=field: self._mark(f))
        widget.editingFinished.connect(self._flush_soon)
        self.fields[field] = widget
        return widget

    def _build_contents_group(self) -> QGroupBox:
        group = QGroupBox("Contents", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        cell_line = self._suggest("cell_line")
        cell_line.setPlaceholderText("e.g. PT-014 chondrocytes")
        cell_line.setToolTip("Naming a cell line that is already linked to a patient"
                             " fills the patient in automatically.")
        form.addRow("Cell line", cell_line)

        patient = self._suggest("patient_code")
        patient.setPlaceholderText("de-identified donor / patient ID")
        form.addRow("Patient", patient)

        experiment = self._suggest("experiment")
        experiment.setPlaceholderText("experiment running in this well")
        form.addRow("Experiment", experiment)

        status = QComboBox(group)
        for value in STATUSES:
            status.addItem(value or "—", value)
        status.activated.connect(lambda _: self._mark("status"))
        status.activated.connect(lambda _: self._flush_soon())
        self.fields["status"] = status
        form.addRow("Status", status)

        passage = self._suggest("passage")
        passage.setPlaceholderText("e.g. P3")
        form.addRow("Passage", passage)

        treatment = self._suggest("treatment")
        treatment.setPlaceholderText("e.g. IL-1β 10 ng/ml")
        form.addRow("Treatment", treatment)

        medium = self._suggest("medium")
        medium.setPlaceholderText("e.g. DMEM/F12 + 10 % FBS")
        form.addRow("Medium", medium)

        seeded = OptionalDateEdit(group, quick_days=(0,))
        seeded.changed.connect(lambda: self._mark("seeded_on"))
        seeded.changed.connect(self._flush_soon)
        self.fields["seeded_on"] = seeded
        form.addRow("Seeded", seeded)

        confluence = ConfluenceSpin(group)
        confluence.valueChanged.connect(lambda _: self._mark("confluence"))
        confluence.valueChanged.connect(lambda _: self._flush_soon())
        self.fields["confluence"] = confluence
        form.addRow("Confluence", confluence)
        return group

    def _build_action_group(self) -> QGroupBox:
        group = QGroupBox("Next action", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        due = OptionalDateEdit(group, quick_days=(0, 1, 3, 7))
        due.changed.connect(lambda: self._mark("due_date"))
        due.changed.connect(self._flush_soon)
        due.setToolTip("When this well needs looking at again. Wells due today or"
                       " overdue are flagged on the plate and in Due & alerts.")
        self.fields["due_date"] = due
        form.addRow("Due date", due)

        task = self._suggest("due_task", extra=DUE_TASKS)
        task.setPlaceholderText("what needs doing, e.g. Change medium")
        form.addRow("Task", task)

        self.due_summary = QLabel("", group)
        self.due_summary.setObjectName("hint")
        form.addRow("", self.due_summary)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.done_button = QPushButton("Mark done", group)
        self.done_button.setToolTip("Record the task as done in the well's history"
                                    " and clear the due date")
        self.done_button.clicked.connect(self.mark_done)
        row.addWidget(self.done_button)
        row.addWidget(QLabel("Snooze", group))
        for days, label in ((1, "+1d"), (3, "+3d"), (7, "+1w")):
            button = QToolButton(group)
            button.setText(label)
            button.setAutoRaise(True)
            button.setFixedWidth(34)
            button.setToolTip(f"Push the due date {days} day(s) further out")
            button.clicked.connect(lambda _=False, d=days: self.snooze(d))
            row.addWidget(button)
        row.addStretch(1)
        form.addRow("", row)
        return group

    def _build_notes_group(self) -> QGroupBox:
        group = QGroupBox("Notes", self)
        layout = QVBoxLayout(group)
        notes = QPlainTextEdit(group)
        notes.setPlaceholderText("Anything worth remembering about this well")
        notes.setFixedHeight(78)
        notes.textChanged.connect(lambda: self._mark("notes"))
        notes.textChanged.connect(self._flush_soon)
        self.fields["notes"] = notes
        layout.addWidget(notes)
        return group

    def _build_photo_group(self) -> QGroupBox:
        group = QGroupBox("Photo timeline", self)
        layout = QVBoxLayout(group)
        self.photo_panel = PhotoPanel(self.store, group)
        self.photo_panel.changed.connect(self._on_photos_changed)
        layout.addWidget(self.photo_panel)
        return group

    def _build_history_group(self) -> QGroupBox:
        group = QGroupBox("History", self)
        layout = QVBoxLayout(group)
        self.history = QListWidget(group)
        self.history.setMaximumHeight(150)
        self.history.setAlternatingRowColors(True)
        layout.addWidget(self.history)
        row = QHBoxLayout()
        note_button = QPushButton("Add note to history…", group)
        note_button.clicked.connect(self.add_history_note)
        row.addWidget(note_button)
        row.addStretch(1)
        layout.addLayout(row)
        return group

    # -------------------------------------------------------------------- load
    def set_wells(self, wells: list[dict]) -> None:
        self.flush()
        # re-read so counts/edits made elsewhere are current
        ids = [w["id"] for w in wells]
        self._wells = self.store.get_wells(ids) if ids else []
        self._dirty.clear()
        self._loading = True
        try:
            self._populate()
        finally:
            self._loading = False

    def revert(self) -> None:
        self._save_timer.stop()
        self._dirty.clear()
        self.set_wells(self._wells)

    def selected_ids(self) -> list[int]:
        return [w["id"] for w in self._wells]

    def _populate(self) -> None:
        count = len(self._wells)
        single = self._wells[0] if count == 1 else None
        multi = count > 1

        self.body.setEnabled(count > 0)
        self.apply_bar.setVisible(multi)
        self.photo_group.setVisible(count == 1)
        self.history_group.setVisible(count == 1)

        if count == 0:
            self.heading.setText("No well selected")
            self.subheading.setText("Click a well on the plate. Drag, shift-click or"
                                    " click a row/column header to select several.")
            self._clear_widgets()
            self.photo_panel.set_well(None)
            self.history.clear()
            self.due_summary.setText("")
            return

        if single is not None:
            label = well_label(single["row_idx"], single["col_idx"])
            self.heading.setText(f"Well {label}")
            self.subheading.setText("Changes save as you type.")
        else:
            labels = [well_label(w["row_idx"], w["col_idx"]) for w in self._wells]
            self.heading.setText(f"{count} wells selected")
            preview = ", ".join(labels[:8]) + (" …" if count > 8 else "")
            self.subheading.setText(preview)
            self.apply_button.setText(f"Apply to {count} wells")
            self.status_label.setText("Only the fields you change are written.")

        for field, widget in self.fields.items():
            values = {("" if w.get(field) in (None, "") else str(w[field]))
                      for w in self._wells}
            shared = values.pop() if len(values) == 1 else None
            self._set_widget(widget, field, shared)

        self.photo_panel.set_well(single)
        self._load_history(single)
        self._update_due_summary()

    def _set_widget(self, widget: QWidget, field: str, value: str | None) -> None:
        mixed = value is None
        if isinstance(widget, SuggestLineEdit):
            widget.setText("" if mixed else value)
            widget.setPlaceholderText(MULTIPLE if mixed
                                      else self._placeholders.get(field, ""))
        elif isinstance(widget, QComboBox):
            index = widget.findData("" if mixed else value)
            if mixed:
                if widget.findText(MULTIPLE) < 0:
                    widget.insertItem(0, MULTIPLE, None)
                widget.setCurrentIndex(widget.findText(MULTIPLE))
            else:
                if widget.findText(MULTIPLE) >= 0:
                    widget.removeItem(widget.findText(MULTIPLE))
                widget.setCurrentIndex(max(index, 0))
        elif isinstance(widget, OptionalDateEdit):
            widget.set_placeholder(MULTIPLE_SHORT if mixed else "—")
            widget.set_value(None if mixed else value)
        elif isinstance(widget, ConfluenceSpin):
            widget.setSpecialValueText(MULTIPLE_SHORT if mixed else "—")
            widget.set_value_or_blank(None if mixed else value)
        elif isinstance(widget, QPlainTextEdit):
            widget.setPlainText("" if mixed else value)
            widget.setPlaceholderText(MULTIPLE if mixed
                                      else self._placeholders.get(field, ""))

    def _clear_widgets(self) -> None:
        for field, widget in self.fields.items():
            self._set_widget(widget, field, "")

    def _load_history(self, well: dict | None) -> None:
        self.history.clear()
        if well is None:
            return
        for event in self.store.events_for_well(well["id"]):
            self.history.addItem(f"{event['ts'][:16]}  {event['detail']}")

    def _update_due_summary(self) -> None:
        if len(self._wells) == 1:
            self.due_summary.setText(pretty_due(self._wells[0].get("due_date")))
        else:
            due = [w for w in self._wells if w.get("due_date")]
            self.due_summary.setText(f"{len(due)} of {len(self._wells)} wells have a due date"
                                     if due else "")

    # ------------------------------------------------------------------ saving
    def _mark(self, field: str) -> None:
        if self._loading:
            return
        self._dirty.add(field)
        if len(self._wells) > 1:
            self.status_label.setText(f"{len(self._dirty)} field(s) changed — not saved yet")

    def _flush_soon(self) -> None:
        if self._loading or len(self._wells) != 1 or not self._dirty:
            return
        self._save_timer.start()

    def _value(self, field: str):
        widget = self.fields[field]
        if isinstance(widget, SuggestLineEdit):
            return widget.text().strip()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, OptionalDateEdit):
            return widget.value()
        if isinstance(widget, ConfluenceSpin):
            return widget.value_or_blank()
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText().strip()
        return None

    def _pending_changes(self) -> dict:
        changes = {}
        for field in self._dirty:
            if field not in EDITABLE_FIELDS:
                continue
            value = self._value(field)
            if value is None:      # "multiple values" placeholder still selected
                continue
            changes[field] = value
        return changes

    def _autosave(self) -> None:
        if len(self._wells) != 1:
            return
        self._write(self.selected_ids())

    def apply_changes(self) -> None:
        self._write(self.selected_ids())

    def _write(self, ids) -> None:
        changes = self._pending_changes()
        self._dirty.clear()
        if not changes or not ids:
            return
        self.store.update_wells(ids, changes)
        self._wells = self.store.get_wells(ids)
        self._loading = True
        try:
            if len(self._wells) == 1:
                # mid-typing: leave the fields (and the cursor) alone
                self._load_history(self._wells[0])
                self._update_due_summary()
            else:
                self._populate()
        finally:
            self._loading = False
        self.status_label.setText("Saved.")
        self.dataChanged.emit()

    def flush(self) -> None:
        """Write anything still pending (called before the selection changes)."""
        self._save_timer.stop()
        if self._dirty and len(self._wells) == 1:
            self._write(self.selected_ids())

    # ----------------------------------------------------------------- actions
    def mark_done(self) -> None:
        wells = [w for w in self._wells if w.get("due_date") or w.get("due_task")]
        if not wells:
            QMessageBox.information(self, "Nothing due",
                                    "None of the selected wells has a due date.")
            return
        for well in wells:
            task = well.get("due_task") or "Check"
            self.store.log_note(well["id"], f"Done: {task}")
        self.store.update_wells([w["id"] for w in wells], {"due_date": "", "due_task": ""})
        self.set_wells(self._wells)
        self.dataChanged.emit()

    def snooze(self, days: int) -> None:
        if not self._wells:
            return
        for well in self._wells:
            base = parse_date(well.get("due_date")) or date.today()
            new_date = max(base, date.today()) + timedelta(days=days)
            self.store.update_wells([well["id"]], {"due_date": new_date.isoformat()})
        self.set_wells(self._wells)
        self.dataChanged.emit()

    def add_history_note(self) -> None:
        if len(self._wells) != 1:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Add note to history",
                                                 "Note:", "")
        if ok and text.strip():
            self.store.log_note(self._wells[0]["id"], text)
            self._load_history(self._wells[0])

    def _on_photos_changed(self) -> None:
        ids = self.selected_ids()
        if ids:
            self._wells = self.store.get_wells(ids)
        self.dataChanged.emit()

    def refresh(self) -> None:
        self.set_wells(self._wells)
