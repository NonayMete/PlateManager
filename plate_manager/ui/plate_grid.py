"""The plate itself: a drawn, clickable well grid.

Selection works the way a spreadsheet does - click, drag, shift-extend,
ctrl-toggle, click a row or column header to take the whole row or column - so
filling twelve wells with the same cell line is one drag and one edit.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..models import days_until, pretty_due, row_letters, well_label
from ..repo import well_is_empty
from . import theme

COLOUR_MODES = ("Status", "Experiment", "Cell line", "Patient", "Due date")

MAX_CELL = 96.0        # a 6-well plate should not fill the screen with 3 huge circles


class PlateGrid(QWidget):
    selectionChanged = Signal()
    wellDoubleClicked = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

        self._plate: dict | None = None
        self._wells: list[dict] = []
        self._by_rc: dict[tuple[int, int], dict] = {}
        self._selected: set[int] = set()
        self._anchor: tuple[int, int] | None = None
        self._colour_mode = "Status"
        self._drag_origin: tuple[int, int] | None = None
        self._drag_base: set[int] = set()
        self._dragging = False
        self._hover: tuple[int, int] | None = None

    # ------------------------------------------------------------------- data
    def set_plate(self, plate: dict | None, wells: list[dict]) -> None:
        keep = self._plate is not None and plate is not None \
            and plate["id"] == self._plate["id"]
        self._plate = plate
        self._wells = wells
        self._by_rc = {(w["row_idx"], w["col_idx"]): w for w in wells}
        ids = {w["id"] for w in wells}
        self._selected = (self._selected & ids) if keep else set()
        if not keep:
            self._anchor = None
        self.updateGeometry()
        self.update()
        self.selectionChanged.emit()

    def refresh_wells(self, wells: list[dict]) -> None:
        """Update well data in place, keeping the selection and staying quiet."""
        self._wells = wells
        self._by_rc = {(w["row_idx"], w["col_idx"]): w for w in wells}
        self._selected &= {w["id"] for w in wells}
        self.update()

    def plate(self) -> dict | None:
        return self._plate

    def wells(self) -> list[dict]:
        return list(self._wells)

    def set_colour_mode(self, mode: str) -> None:
        self._colour_mode = mode if mode in COLOUR_MODES else "Status"
        self.update()

    def colour_mode(self) -> str:
        return self._colour_mode

    # -------------------------------------------------------------- selection
    def selected_ids(self) -> list[int]:
        return [w["id"] for w in self._wells if w["id"] in self._selected]

    def selected_wells(self) -> list[dict]:
        return [w for w in self._wells if w["id"] in self._selected]

    def set_selection(self, ids) -> None:
        wanted = set(ids)
        if wanted == self._selected:
            return
        self._selected = wanted
        first = next((w for w in self._wells if w["id"] in wanted), None)
        if first is not None:
            self._anchor = (first["row_idx"], first["col_idx"])
        self.update()
        self.selectionChanged.emit()

    def select_all(self) -> None:
        self.set_selection({w["id"] for w in self._wells})

    def clear_selection(self) -> None:
        self.set_selection(set())

    def _apply(self, ids: set[int]) -> None:
        if ids != self._selected:
            self._selected = ids
            self.update()
            self.selectionChanged.emit()

    # --------------------------------------------------------------- geometry
    @property
    def _rows(self) -> int:
        return self._plate["n_rows"] if self._plate else 0

    @property
    def _cols(self) -> int:
        return self._plate["n_cols"] if self._plate else 0

    def _metrics(self) -> tuple[float, float, float, float, float]:
        """(origin_x, origin_y, cell, header_left, header_top)"""
        rows, cols = self._rows, self._cols
        if not rows or not cols:
            return 0.0, 0.0, 0.0, 0.0, 0.0
        header_left, header_top, margin = 30.0, 26.0, 10.0
        avail_w = max(self.width() - header_left - margin * 2, 10)
        avail_h = max(self.height() - header_top - margin * 2, 10)
        cell = min(max(min(avail_w / cols, avail_h / rows), 14.0), MAX_CELL)
        grid_w = cell * cols
        grid_h = cell * rows
        origin_x = header_left + margin + max((avail_w - grid_w) / 2, 0)
        origin_y = header_top + margin + min(max((avail_h - grid_h) / 2, 0), cell * 0.6)
        return origin_x, origin_y, cell, header_left, header_top

    def _cell_rect(self, row: int, col: int) -> QRectF:
        ox, oy, cell, _, _ = self._metrics()
        pad = max(cell * 0.06, 1.5)
        return QRectF(ox + col * cell + pad, oy + row * cell + pad,
                      cell - 2 * pad, cell - 2 * pad)

    def _rc_at(self, pos: QPoint) -> tuple[int, int] | None:
        ox, oy, cell, _, _ = self._metrics()
        if cell <= 0:
            return None
        col = int((pos.x() - ox) // cell)
        row = int((pos.y() - oy) // cell)
        if 0 <= row < self._rows and 0 <= col < self._cols:
            return row, col
        return None

    def _header_at(self, pos: QPoint) -> tuple[str, int] | None:
        """('row'|'col'|'all', index) when the click landed on a header."""
        ox, oy, cell, _, _ = self._metrics()
        if cell <= 0:
            return None
        in_row_band = ox - cell * 1.1 <= pos.x() < ox
        in_col_band = oy - cell * 1.1 <= pos.y() < oy
        if in_row_band and in_col_band:
            return "all", 0
        if in_row_band:
            row = int((pos.y() - oy) // cell)
            if 0 <= row < self._rows:
                return "row", row
        if in_col_band:
            col = int((pos.x() - ox) // cell)
            if 0 <= col < self._cols:
                return "col", col
        return None

    def sizeHint(self):  # noqa: N802
        if not self._plate:
            return QRect(0, 0, 520, 360).size()
        return QRect(0, 0, int(self._cols * 54 + 40), int(self._rows * 54 + 36)).size()

    def minimumSizeHint(self):  # noqa: N802
        if not self._plate:
            return QRect(0, 0, 320, 220).size()
        return QRect(0, 0, int(self._cols * 22 + 40), int(self._rows * 22 + 36)).size()

    # ---------------------------------------------------------------- painting
    def _well_colour(self, well: dict) -> QColor:
        if well_is_empty(well):
            return QColor(theme.EMPTY_WELL)
        mode = self._colour_mode
        if mode == "Status":
            return theme.status_colour(well.get("status"))
        if mode == "Experiment":
            return theme.palette_colour(well.get("experiment"))
        if mode == "Cell line":
            return theme.palette_colour(well.get("cell_line"))
        if mode == "Patient":
            return theme.palette_colour(well.get("patient_code"))
        if mode == "Due date":
            colour = theme.due_colour(days_until(well.get("due_date")))
            if colour is None:
                return QColor("#eceff3")
            colour = QColor(colour)
            colour.setAlpha(150)
            return colour
        return QColor(theme.EMPTY_WELL)

    def legend_entries(self) -> list[tuple[str, QColor]]:
        mode = self._colour_mode
        if mode == "Status":
            used = {w.get("status") or "" for w in self._wells if not well_is_empty(w)}
            return [(s or "No status", theme.status_colour(s))
                    for s in theme.STATUS_COLOURS if s in used or (s == "" and used)]
        if mode == "Due date":
            return [("Overdue", QColor(theme.OVERDUE)), ("Today", QColor(theme.DUE_TODAY)),
                    ("Next 3 days", QColor(theme.DUE_SOON)), ("Later", QColor(theme.DUE_LATER)),
                    ("No due date", QColor("#eceff3"))]
        key = {"Experiment": "experiment", "Cell line": "cell_line",
               "Patient": "patient_code"}[mode]
        seen: list[str] = []
        for well in self._wells:
            value = well.get(key)
            if value and value not in seen:
                seen.append(value)
        return [(v, theme.palette_colour(v)) for v in sorted(seen)]

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f7f8fa"))
        if not self._plate:
            painter.setPen(QColor(theme.GRID_TEXT))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "No plate selected.\nCreate one with  File ▸ New plate  (Ctrl+N).")
            return

        ox, oy, cell, _, _ = self._metrics()
        round_wells = self._plate["format"] != "Dish / flask"

        # headers
        label_font = QFont(self.font())
        label_font.setPointSizeF(max(min(cell * 0.30, 11.0), 6.5))
        painter.setFont(label_font)
        painter.setPen(QColor(theme.GRID_TEXT))
        for col in range(self._cols):
            rect = QRectF(ox + col * cell, oy - cell * 0.95, cell, cell * 0.9)
            painter.drawText(rect, Qt.AlignCenter | Qt.AlignBottom, str(col + 1))
        for row in range(self._rows):
            rect = QRectF(ox - cell * 0.98, oy + row * cell, cell * 0.9, cell)
            painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, row_letters(row))

        for (row, col), well in self._by_rc.items():
            rect = self._cell_rect(row, col)
            selected = well["id"] in self._selected
            empty = well_is_empty(well)
            painter.setBrush(self._well_colour(well))
            if selected:
                painter.setPen(QPen(QColor(theme.SELECTION),
                                    min(max(cell * 0.07, 2.0), 4.0)))
            elif self._hover == (row, col):
                painter.setPen(QPen(QColor("#7f8b9c"), min(max(cell * 0.045, 1.2), 2.5)))
            else:
                pen = QPen(QColor(theme.WELL_BORDER), 1.0)
                if empty:
                    pen.setStyle(Qt.DotLine)
                painter.setPen(pen)
            if round_wells:
                painter.drawEllipse(rect)
            else:
                painter.drawRoundedRect(rect, cell * 0.12, cell * 0.12)

            # due-date badge, top-right
            due = theme.due_colour(days_until(well.get("due_date")))
            if due is not None:
                radius = min(max(cell * 0.11, 3.0), 7.0)
                cx = rect.right() - radius * 1.2
                cy = rect.top() + radius * 1.2
                painter.setBrush(due)
                painter.setPen(QPen(QColor("#ffffff"), 1.0))
                painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

            # photo marker, bottom-right
            if well.get("photo_count"):
                radius = min(max(cell * 0.09, 2.5), 5.5)
                cx = rect.right() - radius * 1.4
                cy = rect.bottom() - radius * 1.4
                painter.setBrush(QColor("#4b5563"))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

            self._paint_well_text(painter, rect, well, cell)

        if self._dragging and self._drag_origin and self._hover:
            painter.setPen(QPen(QColor(theme.SELECTION), 1, Qt.DashLine))
            painter.setBrush(QColor(29, 111, 214, 26))
            r0, c0 = self._drag_origin
            r1, c1 = self._hover
            top_left = self._cell_rect(min(r0, r1), min(c0, c1))
            bottom_right = self._cell_rect(max(r0, r1), max(c0, c1))
            painter.drawRect(QRectF(top_left.topLeft(), bottom_right.bottomRight()))
        painter.end()

    def _paint_well_text(self, painter: QPainter, rect: QRectF, well: dict,
                         cell: float) -> None:
        if cell < 30:
            return
        lines: list[str] = []
        font = QFont(painter.font())
        font.setPointSizeF(max(min(cell * 0.17, 10.5), 6.0))
        painter.setFont(font)
        painter.setPen(QColor("#2c3444"))
        metrics = painter.fontMetrics()
        inner = rect.adjusted(rect.width() * 0.08, rect.height() * 0.12,
                              -rect.width() * 0.08, -rect.height() * 0.12)

        lines.append(well_label(well["row_idx"], well["col_idx"]))
        if cell >= 66:
            # a heavily truncated cell line says nothing, so fall back to the
            # patient code (short by nature) and leave the rest to the tooltip
            for candidate in (well.get("cell_line"), well.get("patient_code")):
                if candidate and metrics.horizontalAdvance(str(candidate)) <= inner.width():
                    lines.append(str(candidate))
                    break
            second = well.get("passage") or well.get("status") or ""
            if second:
                lines.append(str(second))
        line_h = metrics.height()
        total = line_h * len(lines)
        y = inner.center().y() - total / 2
        for text in lines:
            elided = metrics.elidedText(text, Qt.ElideRight, int(inner.width()))
            painter.drawText(QRectF(inner.left(), y, inner.width(), line_h),
                             Qt.AlignHCenter | Qt.AlignVCenter, elided)
            y += line_h

    # -------------------------------------------------------------- mouse/keys
    def _ids_in_rect(self, r0: int, c0: int, r1: int, c1: int) -> set[int]:
        rows = range(min(r0, r1), max(r0, r1) + 1)
        cols = range(min(c0, c1), max(c0, c1) + 1)
        return {self._by_rc[(r, c)]["id"] for r in rows for c in cols if (r, c) in self._by_rc}

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._plate:
            return
        pos = event.position().toPoint()
        modifiers = event.modifiers()
        additive = bool(modifiers & (Qt.ControlModifier | Qt.MetaModifier))

        header = self._header_at(pos)
        if header and event.button() in (Qt.LeftButton, Qt.RightButton):
            kind, index = header
            if kind == "all":
                ids = {w["id"] for w in self._wells}
            elif kind == "row":
                ids = self._ids_in_rect(index, 0, index, self._cols - 1)
            else:
                ids = self._ids_in_rect(0, index, self._rows - 1, index)
            self._apply((self._selected | ids) if additive else ids)
            self.setFocus()
            return

        rc = self._rc_at(pos)
        if rc is None:
            if event.button() == Qt.LeftButton and not additive:
                self._apply(set())
            return
        well = self._by_rc[rc]

        if event.button() == Qt.RightButton:
            if well["id"] not in self._selected:
                self._apply({well["id"]})
            return

        if event.button() != Qt.LeftButton:
            return
        self.setFocus()
        if modifiers & Qt.ShiftModifier and self._anchor:
            ids = self._ids_in_rect(*self._anchor, *rc)
            self._apply((self._selected | ids) if additive else ids)
        elif additive:
            selection = set(self._selected)
            selection.symmetric_difference_update({well["id"]})
            self._anchor = rc
            self._apply(selection)
        else:
            self._anchor = rc
            self._apply({well["id"]})
        self._drag_origin = rc
        self._drag_base = set(self._selected)
        self._dragging = False
        self._hover = rc

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._plate:
            return
        pos = event.position().toPoint()
        rc = self._rc_at(pos)
        if rc != self._hover:
            self._hover = rc
            self.update()
        if event.buttons() & Qt.LeftButton and self._drag_origin and rc:
            if rc != self._drag_origin:
                self._dragging = True
            ids = self._ids_in_rect(*self._drag_origin, *rc)
            additive = bool(event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))
            self._apply((self._drag_base | ids) if additive else ids)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None
        self._dragging = False
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        rc = self._rc_at(event.position().toPoint())
        if rc:
            self.wellDoubleClicked.emit(self._by_rc[rc]["id"])

    def event(self, event):
        if event.type() == event.Type.ToolTip:
            rc = self._rc_at(event.pos())
            if rc:
                QToolTip.showText(event.globalPos(), self._tooltip(self._by_rc[rc]), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def _tooltip(self, well: dict) -> str:
        label = well_label(well["row_idx"], well["col_idx"])
        rows = [
            ("Cell line", well.get("cell_line")),
            ("Patient", well.get("patient_code")),
            ("Experiment", well.get("experiment")),
            ("Status", well.get("status")),
            ("Passage", well.get("passage")),
            ("Treatment", well.get("treatment")),
            ("Medium", well.get("medium")),
            ("Seeded", well.get("seeded_on")),
            ("Confluence", None if well.get("confluence") is None
             else f"{well['confluence']} %"),
            ("Due", pretty_due(well.get("due_date"))
             + (f" — {well['due_task']}" if well.get("due_task") else "")),
            ("Photos", str(well["photo_count"]) if well.get("photo_count") else None),
        ]
        body = "".join(f"<tr><td style='color:#6b7480'>{k}</td>"
                       f"<td>&nbsp;{v}</td></tr>"
                       for k, v in rows if v)
        if not body:
            return f"<b>{label}</b><br><i>empty</i>"
        notes = well.get("notes")
        note_html = (f"<div style='margin-top:4px;max-width:280px'>{notes}</div>"
                     if notes else "")
        return f"<b>{label}</b><table>{body}</table>{note_html}"

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._plate:
            return
        key = event.key()
        if key == Qt.Key_A and event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.select_all()
            return
        if key == Qt.Key_Escape:
            self.clear_selection()
            return
        deltas = {Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1),
                  Qt.Key_Up: (-1, 0), Qt.Key_Down: (1, 0)}
        if key in deltas and self._anchor:
            dr, dc = deltas[key]
            row = min(max(self._anchor[0] + dr, 0), self._rows - 1)
            col = min(max(self._anchor[1] + dc, 0), self._cols - 1)
            if event.modifiers() & Qt.ShiftModifier:
                ids = self._ids_in_rect(*self._anchor, row, col)
                self._apply(self._selected | ids)
            else:
                self._anchor = (row, col)
                self._apply({self._by_rc[(row, col)]["id"]})
            return
        super().keyPressEvent(event)
