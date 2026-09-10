"""Colours, the app icon and the handful of shared style rules."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap

# Status -> (fill, text)
STATUS_COLOURS: dict[str, str] = {
    "": "#f4f6f8",
    "Seeded": "#cfe4ff",
    "Growing": "#c7ebc9",
    "Confluent": "#7fcf98",
    "Treated": "#dcd0f7",
    "Passaged": "#bfe7e4",
    "Fixed / stained": "#ffe0b3",
    "Frozen": "#dbeeff",
    "Contaminated": "#ffc9c4",
    "Discarded": "#dfe1e5",
}

EMPTY_WELL = "#fbfcfd"
WELL_BORDER = "#c3c9d2"
GRID_TEXT = "#5b6472"
SELECTION = "#1d6fd6"

OVERDUE = "#d93025"
DUE_TODAY = "#f29900"
DUE_SOON = "#f7c948"
DUE_LATER = "#8ab4f8"

# Distinct, low-saturation fills used when colouring by experiment or cell line.
PALETTE = [
    "#a8c8f0", "#f3c0a8", "#b8ddb0", "#e0b7d8", "#f2d79b", "#a9d9d4",
    "#c9c2ea", "#f0b8b8", "#bfd8a8", "#d6c8b0", "#a8d4ec", "#e8bcd0",
]

STYLESHEET = """
QMainWindow, QDialog { background: #f7f8fa; }
QMenuBar, QToolBar { background: #ffffff; border-bottom: 1px solid #e2e5ea; spacing: 4px; }
QToolBar { padding: 4px 6px; }
QStatusBar { background: #ffffff; border-top: 1px solid #e2e5ea; color: #5b6472; }
QTabWidget::pane { border: 1px solid #e2e5ea; background: #f7f8fa; }
QTabBar::tab:selected { background: #ffffff; border: 1px solid #e2e5ea; border-bottom: none; }
QGroupBox {
    font-weight: 600; border: 1px solid #e2e5ea; border-radius: 6px;
    margin-top: 16px; padding: 14px 8px 8px 8px; background: #ffffff;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 0 5px; color: #45506b; background: #ffffff;
}
QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox {
    border: 1px solid #ccd2db; border-radius: 5px; padding: 4px 6px; background: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {
    border: 1px solid #1d6fd6;
}
QComboBox::drop-down, QDateEdit::drop-down { border: none; width: 20px; }
QSpinBox::up-button, QSpinBox::down-button { width: 16px; border: none; }
QPushButton {
    border: 1px solid #ccd2db; border-radius: 5px; padding: 5px 12px; background: #ffffff;
}
QPushButton:hover { background: #eef3fb; }
QPushButton:pressed { background: #e0e8f5; }
QPushButton:disabled { color: #9aa2ae; background: #f2f3f5; }
QPushButton#primary {
    background: #1d6fd6; color: #ffffff; border: 1px solid #1a63bf; font-weight: 600;
}
QPushButton#primary:hover { background: #1a63bf; }
QPushButton#primary:disabled { background: #b8cbe8; border-color: #b8cbe8; color: #f0f4fa; }
QListWidget, QTableWidget, QTreeWidget {
    border: 1px solid #e2e5ea; border-radius: 6px; background: #ffffff;
}
QHeaderView::section {
    background: #f0f2f5; border: none; border-right: 1px solid #e2e5ea;
    border-bottom: 1px solid #e2e5ea; padding: 5px;
}
QTabBar::tab { padding: 7px 16px; }
QLabel#hint { color: #6b7480; }
QLabel#heading { font-size: 15px; font-weight: 600; color: #22293a; }
"""


def light_palette() -> QPalette:
    """A light palette applied over the whole app.

    The well colours are chosen for a light background, and half-inheriting a
    system dark theme leaves labels unreadable - so the app commits to light
    rather than picking up whatever the OS is set to.
    """
    palette = QPalette()
    pairs = {
        QPalette.Window: "#f7f8fa",
        QPalette.WindowText: "#22293a",
        QPalette.Base: "#ffffff",
        QPalette.AlternateBase: "#f4f6f8",
        QPalette.Text: "#22293a",
        QPalette.PlaceholderText: "#9aa2ae",
        QPalette.Button: "#ffffff",
        QPalette.ButtonText: "#22293a",
        QPalette.ToolTipBase: "#ffffff",
        QPalette.ToolTipText: "#22293a",
        QPalette.Highlight: SELECTION,
        QPalette.HighlightedText: "#ffffff",
        QPalette.Link: SELECTION,
        QPalette.BrightText: "#d93025",
    }
    for role, colour in pairs.items():
        palette.setColor(role, QColor(colour))
    for role, colour in ((QPalette.Text, "#9aa2ae"), (QPalette.ButtonText, "#9aa2ae"),
                         (QPalette.WindowText, "#9aa2ae")):
        palette.setColor(QPalette.Disabled, role, QColor(colour))
    return palette


def apply_to(app) -> None:
    """Give the application its style, palette, stylesheet and icon."""
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(app_icon())


def status_colour(status: str | None) -> QColor:
    return QColor(STATUS_COLOURS.get(status or "", EMPTY_WELL))


def palette_colour(key: str | None) -> QColor:
    """Stable colour for an arbitrary label, so a cell line keeps its colour."""
    if not key:
        return QColor(EMPTY_WELL)
    index = sum(ord(ch) * (i + 7) for i, ch in enumerate(key)) % len(PALETTE)
    return QColor(PALETTE[index])


def due_colour(days: int | None) -> QColor | None:
    if days is None:
        return None
    if days < 0:
        return QColor(OVERDUE)
    if days == 0:
        return QColor(DUE_TODAY)
    if days <= 3:
        return QColor(DUE_SOON)
    return QColor(DUE_LATER)


def app_icon() -> QIcon:
    """A little 6-well plate, drawn rather than shipped as a binary asset."""
    icon = QIcon()
    for size in (16, 32, 64, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#1d6fd6"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.18, size * 0.18)
        painter.setBrush(QColor("#ffffff"))
        pad, cols, rows = size * 0.14, 3, 2
        cell_w = (size - 2 * pad) / cols
        cell_h = (size - 2 * pad) / rows
        radius = min(cell_w, cell_h) * 0.36
        for row in range(rows):
            for col in range(cols):
                cx = pad + cell_w * (col + 0.5)
                cy = pad + cell_h * (row + 0.5)
                painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))
        painter.end()
        icon.addPixmap(pixmap)
    return icon
