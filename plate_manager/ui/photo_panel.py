"""Photos for one well, in date order: the visual timeline.

Photos can be dropped straight from Explorer/Finder or pasted from the
clipboard (which is how most microscope software hands over an image).  Each
photo's date is read from EXIF where possible and stays editable, because
"the picture from last Tuesday" is the useful handle, not the file name.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from .. import photos
from ..models import parse_date, today_iso, well_label
from ..repo import Store
from .widgets import OptionalDateEdit

THUMB = 116


class PhotoPanel(QWidget):
    """Thumbnail strip + actions for the photos attached to a single well."""

    changed = Signal()

    def __init__(self, store: Store, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self._well: dict | None = None
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.list = QListWidget(self)
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setGridSize(QSize(THUMB + 22, THUMB + 40))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.setMinimumHeight(THUMB + 60)
        self.list.itemDoubleClicked.connect(lambda _: self.open_viewer())
        self.list.itemSelectionChanged.connect(self._update_actions)
        layout.addWidget(self.list, 1)

        self.hint = QLabel("Drag image files here, or use Add photos.", self)
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.add_button = QPushButton("Add photos…", self)
        self.add_button.clicked.connect(self.add_photos)
        self.paste_button = QPushButton("Paste image", self)
        self.paste_button.setToolTip("Paste an image from the clipboard")
        self.paste_button.clicked.connect(self.paste_image)
        self.date_button = QPushButton("Set date…", self)
        self.date_button.clicked.connect(self.edit_date)
        self.caption_button = QPushButton("Caption…", self)
        self.caption_button.clicked.connect(self.edit_caption)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(self.remove_selected)
        for button in (self.add_button, self.paste_button, self.date_button,
                       self.caption_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.set_well(None)

    # -------------------------------------------------------------------- data
    def set_well(self, well: dict | None) -> None:
        self._well = well
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        enabled = self._well is not None
        for button in (self.add_button, self.paste_button):
            button.setEnabled(enabled)
        if not enabled:
            self.hint.setText("Select a single well to see and add its photos.")
            self._update_actions()
            return
        rows = self.store.photos_for_well(self._well["id"])
        for row in rows:
            item = QListWidgetItem(row["taken_at"] or "undated")
            thumb = photos.thumbnail(row["rel_path"], 256)
            if thumb is not None:
                item.setIcon(QIcon(QPixmap(str(thumb))))
            else:
                item.setIcon(QIcon())
                item.setText((row["taken_at"] or "undated") + "\n(file missing)")
            caption = row["caption"]
            if caption:
                item.setText(f"{item.text()}\n{caption}")
            item.setData(Qt.UserRole, row)
            item.setToolTip(f"{row['taken_at'] or 'undated'}"
                            f"{chr(10) + caption if caption else ''}")
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.list.addItem(item)
        label = well_label(self._well["row_idx"], self._well["col_idx"])
        if rows:
            dated = [r["taken_at"] for r in rows if r["taken_at"]]
            span = f" · {dated[0]} → {dated[-1]}" if len(dated) > 1 else ""
            self.hint.setText(f"{len(rows)} photo{'s' if len(rows) != 1 else ''}"
                              f" for {label}{span}. Double-click to open.")
        else:
            self.hint.setText(f"No photos for {label} yet — drag files here,"
                              " paste, or use Add photos.")
        self._update_actions()

    def _update_actions(self) -> None:
        has_selection = bool(self.list.selectedItems())
        for button in (self.date_button, self.caption_button, self.remove_button):
            button.setEnabled(has_selection)

    def _selected_rows(self) -> list[dict]:
        return [item.data(Qt.UserRole) for item in self.list.selectedItems()]

    # ----------------------------------------------------------------- actions
    def add_photos(self) -> None:
        if self._well is None:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Add photos to this well", "",
                                                photos.IMAGE_FILTER)
        if files:
            self._import_files(files)

    def _import_files(self, files) -> None:
        if self._well is None:
            return
        added = 0
        for path in files:
            if not photos.is_image(path):
                continue
            try:
                rel = photos.store_file(path, self._well["uid"])
            except OSError as exc:
                QMessageBox.warning(self, "Could not copy photo", str(exc))
                continue
            self.store.add_photo(self._well["id"], rel, photos.guess_taken_at(path),
                                 "")
            added += 1
        if added:
            self.refresh()
            self.changed.emit()
        elif files:
            QMessageBox.information(self, "Nothing added",
                                    "Those files are not image types the app can read.")

    def paste_image(self) -> None:
        if self._well is None:
            return
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            QMessageBox.information(self, "Clipboard empty",
                                    "There is no image on the clipboard.")
            return
        tmp = Path(tempfile.gettempdir()) / f"pm-paste-{self._well['uid'][:8]}.png"
        if not image.save(str(tmp), "PNG"):
            QMessageBox.warning(self, "Paste failed", "The image could not be saved.")
            return
        rel = photos.store_file(tmp, self._well["uid"])
        tmp.unlink(missing_ok=True)
        self.store.add_photo(self._well["id"], rel, today_iso(), "Pasted image")
        self.refresh()
        self.changed.emit()

    def edit_date(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        dialog = DateDialog(rows[0]["taken_at"], self)
        if dialog.exec() == QDialog.Accepted:
            for row in rows:
                self.store.update_photo(row["id"], taken_at=dialog.value())
            self.refresh()
            self.changed.emit()

    def edit_caption(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        text, ok = QInputDialog.getText(self, "Photo caption", "Caption:",
                                        QLineEdit.Normal, rows[0]["caption"])
        if ok:
            for row in rows:
                self.store.update_photo(row["id"], caption=text)
            self.refresh()

    def remove_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        answer = QMessageBox.question(
            self, "Remove photos",
            f"Remove {len(rows)} photo{'s' if len(rows) != 1 else ''} from this well?\n"
            "The image files are deleted from the data folder.")
        if answer != QMessageBox.Yes:
            return
        removed = []
        for row in rows:
            rel = self.store.delete_photo(row["id"])
            if rel:
                removed.append(rel)
        photos.delete_files(removed)
        self.refresh()
        self.changed.emit()

    def open_viewer(self) -> None:
        if self._well is None:
            return
        rows = self.store.photos_for_well(self._well["id"])
        if not rows:
            return
        current = 0
        selected = self._selected_rows()
        if selected:
            ids = [r["id"] for r in rows]
            current = ids.index(selected[0]["id"]) if selected[0]["id"] in ids else 0
        viewer = PhotoViewer(self.store, rows, current,
                             well_label(self._well["row_idx"], self._well["col_idx"]), self)
        viewer.exec()
        self.refresh()
        self.changed.emit()

    # ------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._well is not None and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._well is not None and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._import_files(paths)
            event.acceptProposedAction()


class DateDialog(QDialog):
    """Pick (or clear) the date a photo was taken."""

    def __init__(self, value: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Photo date")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("When was this photo taken?", self))
        self.date = OptionalDateEdit(self, quick_days=(0,))
        self.date.set_value(value)
        layout.addWidget(self.date)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self.date.value()


class PhotoViewer(QDialog):
    """Full-size photo with its date and caption; arrow keys walk the timeline."""

    def __init__(self, store: Store, rows: list[dict], index: int, well: str,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.rows = rows
        self.index = max(0, min(index, len(rows) - 1))
        self.setWindowTitle(f"Photos — well {well}")
        self._loading = True          # resizing while building must not repaint
        self._pixmap = QPixmap()
        self._pixmap_for: int | None = None

        layout = QVBoxLayout(self)
        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumHeight(420)
        self.image.setStyleSheet("background:#1f2430; border-radius:6px; color:#cfd6e4;")
        layout.addWidget(self.image, 1)

        self.caption = QLineEdit(self)
        self.caption.setPlaceholderText("Caption (optional)")
        self.caption.editingFinished.connect(self._save_caption)

        row = QHBoxLayout()
        self.prev_button = QPushButton("◀ Previous", self)
        self.prev_button.clicked.connect(lambda: self.step(-1))
        self.next_button = QPushButton("Next ▶", self)
        self.next_button.clicked.connect(lambda: self.step(1))
        self.counter = QLabel(self)
        self.date = OptionalDateEdit(self, quick_days=(0,))
        self.date.changed.connect(self._save_date)
        open_button = QPushButton("Open in viewer", self)
        open_button.clicked.connect(self._open_external)
        row.addWidget(self.prev_button)
        row.addWidget(self.next_button)
        row.addWidget(self.counter)
        row.addStretch(1)
        row.addWidget(QLabel("Taken:", self))
        row.addWidget(self.date)
        row.addWidget(open_button)
        layout.addLayout(row)
        layout.addWidget(self.caption)

        close = QDialogButtonBox(QDialogButtonBox.Close, self)
        close.rejected.connect(self.accept)
        layout.addWidget(close)
        self._loading = False
        self.resize(880, 700)
        self.load()

    def current(self) -> dict:
        return self.rows[self.index]

    def _render_image(self) -> None:
        """Draw the current photo scaled to the space available.

        The file is read once per photo and rescaled from that copy, so
        resizing the window does not re-decode it.
        """
        row = self.current()
        path = photos.absolute(row["rel_path"])
        if not path.exists():
            self.image.setPixmap(QPixmap())
            self.image.setText(f"Missing file:\n{row['rel_path']}")
            return
        if self._pixmap_for != row["id"]:
            self._pixmap = QPixmap(str(path))
            self._pixmap_for = row["id"]
        if self._pixmap.isNull():
            self.image.setPixmap(QPixmap())
            self.image.setText("This file is not a readable image.")
            return
        self.image.setPixmap(self._pixmap.scaled(
            self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def load(self) -> None:
        self._loading = True
        row = self.current()
        self._render_image()
        self.date.set_value(row["taken_at"])
        self.caption.setText(row["caption"] or "")
        dated = parse_date(row["taken_at"])
        when = dated.strftime("%a %d %b %Y") if dated else "no date"
        self.counter.setText(f"{self.index + 1} of {len(self.rows)}  ·  {when}")
        self.prev_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(self.rows) - 1)
        self._loading = False

    def step(self, delta: int) -> None:
        new_index = self.index + delta
        if 0 <= new_index < len(self.rows):
            self.index = new_index
            self.load()

    def _save_date(self) -> None:
        if self._loading:
            return
        row = self.current()
        value = self.date.value()
        if value != (row["taken_at"] or ""):
            self.store.update_photo(row["id"], taken_at=value)
            row["taken_at"] = value
            self.load()

    def _save_caption(self) -> None:
        if self._loading:
            return
        row = self.current()
        text = self.caption.text().strip()
        if text != (row["caption"] or ""):
            self.store.update_photo(row["id"], caption=text)
            row["caption"] = text

    def _open_external(self) -> None:
        path = photos.absolute(self.current()["rel_path"])
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._loading:
            self._render_image()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Left, Qt.Key_Up):
            self.step(-1)
        elif event.key() in (Qt.Key_Right, Qt.Key_Down):
            self.step(1)
        else:
            super().keyPressEvent(event)
