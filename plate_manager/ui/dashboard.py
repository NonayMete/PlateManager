"""Due & alerts: everything that needs looking at, across every plate."""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..models import days_until, parse_date, well_label
from ..repo import Store
from . import theme

HORIZONS: tuple[tuple[str, int], ...] = (
    ("Overdue only", -1),
    ("Due today or earlier", 0),
    ("Next 3 days", 3),
    ("Next 7 days", 7),
    ("Next 14 days", 14),
    ("Next 30 days", 30),
)


class DashboardTab(QWidget):
    jumpToWell = Signal(int, int)     # plate_id, well_id
    dataChanged = Signal()

    HEADERS = ("Due", "Plate", "Well", "Task", "Cell line", "Patient",
               "Experiment", "Status", "Location")

    def __init__(self, store: Store, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self._rows: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        top = QHBoxLayout()
        heading = QLabel("Wells needing attention", self)
        heading.setObjectName("heading")
        top.addWidget(heading)
        top.addStretch(1)
        top.addWidget(QLabel("Show:", self))
        self.horizon = QComboBox(self)
        for label, _ in HORIZONS:
            self.horizon.addItem(label)
        self.horizon.setCurrentIndex(3)
        self.horizon.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.horizon)
        refresh_button = QPushButton("Refresh", self)
        refresh_button.clicked.connect(self.refresh)
        top.addWidget(refresh_button)
        layout.addLayout(top)

        self.summary = QLabel("", self)
        self.summary.setTextFormat(Qt.RichText)
        layout.addWidget(self.summary)

        splitter = QSplitter(Qt.Horizontal, self)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(self.HEADERS), left)
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._jump)
        left_layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.go_button = QPushButton("Go to well", left)
        self.go_button.setObjectName("primary")
        self.go_button.clicked.connect(self._jump)
        self.done_button = QPushButton("Mark done", left)
        self.done_button.setToolTip("Log the task in the well's history and clear its due date")
        self.done_button.clicked.connect(self.mark_done)
        actions.addWidget(self.go_button)
        actions.addWidget(self.done_button)
        for days, label in ((1, "Snooze 1 day"), (3, "Snooze 3 days"), (7, "Snooze 1 week")):
            button = QPushButton(label, left)
            button.clicked.connect(lambda _=False, d=days: self.snooze(d))
            actions.addWidget(button)
        actions.addStretch(1)
        left_layout.addLayout(actions)
        splitter.addWidget(left)

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        activity_label = QLabel("Recent activity", right)
        activity_label.setObjectName("heading")
        right_layout.addWidget(activity_label)
        self.activity = QListWidget(right)
        self.activity.setAlternatingRowColors(True)
        self.activity.setWordWrap(True)
        right_layout.addWidget(self.activity, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([980, 420])
        layout.addWidget(splitter, 1)

        self.refresh()

    # -------------------------------------------------------------------- data
    def refresh(self) -> None:
        within = HORIZONS[self.horizon.currentIndex()][1]
        self._rows = self.store.due_wells(within)
        locations = {p["id"]: (p["location"] or "")
                     for p in self.store.list_plates(include_archived=True)}
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._rows))
        for index, well in enumerate(self._rows):
            days = days_until(well.get("due_date"))
            values = (
                well.get("due_date") or "",
                well.get("plate_name") or "",
                well_label(well["row_idx"], well["col_idx"]),
                well.get("due_task") or "",
                well.get("cell_line") or "",
                well.get("patient_code") or "",
                well.get("experiment") or "",
                well.get("status") or "",
                locations.get(well["plate_id"], ""),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, (well["plate_id"], well["id"]))
                    colour = theme.due_colour(days)
                    if colour is not None and days is not None and days <= 0:
                        item.setForeground(QColor(colour))
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                    item.setText(f"{value}  ({self._when(days)})")
                self.table.setItem(index, column, item)
        self.table.setSortingEnabled(True)
        # most urgent first, whatever order the rows arrived in
        self.table.sortItems(0, Qt.AscendingOrder)
        self.table.resizeColumnsToContents()
        self._update_summary()
        self._load_activity()
        has_rows = bool(self._rows)
        for button in (self.go_button, self.done_button):
            button.setEnabled(has_rows)
        if has_rows:
            self.table.selectRow(0)

    @staticmethod
    def _when(days: int | None) -> str:
        if days is None:
            return ""
        if days < 0:
            return f"{-days}d overdue"
        if days == 0:
            return "today"
        if days == 1:
            return "tomorrow"
        return f"in {days}d"

    def _update_summary(self) -> None:
        stats = self.store.stats()
        week = len(self.store.due_wells(7))
        bits = []
        if stats["overdue"]:
            bits.append(f"<span style='color:{theme.OVERDUE};font-weight:600'>"
                        f"{stats['overdue']} overdue</span>")
        if stats["due_today"]:
            bits.append(f"<span style='color:{theme.DUE_TODAY};font-weight:600'>"
                        f"{stats['due_today']} due today</span>")
        bits.append(f"{week} due within a week")
        bits.append(f"{stats['plates']} active plates")
        self.summary.setText(" &nbsp;·&nbsp; ".join(bits))

    def _load_activity(self) -> None:
        self.activity.clear()
        for event in self.store.recent_events(120):
            plate = event.get("plate_name") or ""
            where = f"{plate} {event.get('well') or ''}".strip()
            detail = event["detail"]
            if len(detail) > 110:
                detail = detail[:107].rstrip(" ;") + "…"
            self.activity.addItem(f"{event['ts'][:16]}  {where}\n    {detail}")

    # ----------------------------------------------------------------- actions
    def _selected(self) -> list[dict]:
        wanted = set()
        for item in self.table.selectedItems():
            data = self.table.item(item.row(), 0).data(Qt.UserRole)
            if data:
                wanted.add(data)
        by_id = {(w["plate_id"], w["id"]): w for w in self._rows}
        return [by_id[key] for key in wanted if key in by_id]

    def _jump(self) -> None:
        selected = self._selected()
        if selected:
            self.jumpToWell.emit(selected[0]["plate_id"], selected[0]["id"])

    def mark_done(self) -> None:
        wells = self._selected()
        if not wells:
            return
        for well in wells:
            self.store.log_note(well["id"], f"Done: {well.get('due_task') or 'Check'}")
        self.store.update_wells([w["id"] for w in wells],
                                {"due_date": "", "due_task": ""})
        self.refresh()
        self.dataChanged.emit()

    def snooze(self, days: int) -> None:
        wells = self._selected()
        if not wells:
            return
        for well in wells:
            base = parse_date(well.get("due_date")) or date.today()
            new_date = max(base, date.today()) + timedelta(days=days)
            self.store.update_wells([well["id"]], {"due_date": new_date.isoformat()})
        self.refresh()
        self.dataChanged.emit()
