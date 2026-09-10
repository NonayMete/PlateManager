"""The main window: plate list on the left, the plate in the middle, the
selected well(s) on the right, with Due & alerts and Library as sibling tabs.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, APP_VERSION, archive, paths, photos
from ..models import well_label
from ..notify import Reminders
from ..repo import Store
from . import theme
from .dashboard import DashboardTab
from .dialogs import (ImportDialog, NewPlateDialog, PlatePropertiesDialog, SearchDialog,
                      SettingsDialog)
from .library import LibraryTab
from .plate_grid import COLOUR_MODES, PlateGrid
from .well_panel import WellPanel


class MainWindow(QMainWindow):
    def __init__(self, store: Store, config: dict):
        super().__init__()
        self.store = store
        self.config = config
        self._copied_well: int | None = None

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.resize(1440, 900)

        self.tabs = QTabWidget(self)
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_plates_tab(), "Plates")
        self.dashboard = DashboardTab(store, self)
        self.dashboard.jumpToWell.connect(self.jump_to_well)
        self.dashboard.dataChanged.connect(self.refresh_all)
        self.tabs.addTab(self.dashboard, "Due && alerts")
        self.library = LibraryTab(store, self)
        self.library.dataChanged.connect(self.refresh_all)
        self.tabs.addTab(self.library, "Library")

        self._build_menus()
        self._build_toolbar()

        self.reminders = Reminders(store, self)
        self.reminders.openRequested.connect(self._raise_window)
        self.reminders.enable_tray(bool(config.get("tray_icon", True)))
        self.reminders.set_interval_hours(float(config.get("recheck_hours", 4)))

        self.statusBar().showMessage(str(paths.data_dir()))
        self._restore_state()
        self.refresh_plates(select_id=config.get("last_plate"))
        self._update_status()

    # ------------------------------------------------------------------ layout
    def _build_plates_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Horizontal, self)

        # ---- left: plates
        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 4, 8)
        heading = QLabel("Plates", left)
        heading.setObjectName("heading")
        left_layout.addWidget(heading)
        self.plate_filter = QLineEdit(left)
        self.plate_filter.setPlaceholderText("Filter plates…")
        self.plate_filter.setClearButtonEnabled(True)
        self.plate_filter.textChanged.connect(lambda _: self.refresh_plates(keep=True))
        left_layout.addWidget(self.plate_filter)
        self.plate_list = QListWidget(left)
        self.plate_list.setAlternatingRowColors(True)
        self.plate_list.currentItemChanged.connect(self._on_plate_selected)
        left_layout.addWidget(self.plate_list, 1)
        self.show_archived = QCheckBox("Show archived plates", left)
        self.show_archived.toggled.connect(lambda _: self.refresh_plates(keep=True))
        left_layout.addWidget(self.show_archived)
        buttons = QHBoxLayout()
        new_button = QPushButton("New plate", left)
        new_button.setObjectName("primary")
        new_button.clicked.connect(self.new_plate)
        properties_button = QPushButton("Properties…", left)
        properties_button.clicked.connect(self.plate_properties)
        buttons.addWidget(new_button)
        buttons.addWidget(properties_button)
        left_layout.addLayout(buttons)
        splitter.addWidget(left)

        # ---- centre: the plate
        centre = QWidget(splitter)
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        self.plate_title = QLabel("No plate selected", centre)
        self.plate_title.setObjectName("heading")
        top.addWidget(self.plate_title, 1)
        top.addWidget(QLabel("Colour by:", centre))
        self.colour_combo = QComboBox(centre)
        self.colour_combo.addItems(COLOUR_MODES)
        self.colour_combo.currentTextChanged.connect(self._on_colour_mode)
        top.addWidget(self.colour_combo)
        centre_layout.addLayout(top)

        self.plate_subtitle = QLabel("", centre)
        self.plate_subtitle.setObjectName("hint")
        centre_layout.addWidget(self.plate_subtitle)

        self.grid = PlateGrid(centre)
        self.grid.selectionChanged.connect(self._on_selection_changed)
        self.grid.wellDoubleClicked.connect(self._on_well_double_clicked)
        self.grid.customContextMenuRequested.connect(self._well_context_menu)
        scroll = QScrollArea(centre)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.grid)
        centre_layout.addWidget(scroll, 1)

        self.legend = QLabel("", centre)
        self.legend.setWordWrap(True)
        self.legend.setTextFormat(Qt.RichText)
        centre_layout.addWidget(self.legend)
        splitter.addWidget(centre)

        # ---- right: the selected well(s)
        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 8, 8, 8)
        self.well_panel = WellPanel(self.store, right)
        self.well_panel.dataChanged.connect(self._on_well_data_changed)
        right_layout.addWidget(self.well_panel)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([250, 690, 520])
        self.splitter = splitter
        return splitter

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._add_action(file_menu, "&New plate…", self.new_plate, QKeySequence.New)
        self._add_action(file_menu, "&Duplicate plate…", self.duplicate_plate)
        self._add_action(file_menu, "Plate &properties…", self.plate_properties)
        self._add_action(file_menu, "&Archive / restore plate", self.toggle_archive)
        self._add_action(file_menu, "De&lete plate…", self.delete_plate)
        file_menu.addSeparator()
        self._add_action(file_menu, "&Export bundle…", self.export_bundle, "Ctrl+E")
        self._add_action(file_menu, "&Import bundle…", self.import_bundle, "Ctrl+I")
        file_menu.addSeparator()
        self._add_action(file_menu, "Open data &folder", self.open_data_folder)
        self._add_action(file_menu, "&Settings…", self.open_settings,
                         QKeySequence.Preferences)
        file_menu.addSeparator()
        self._add_action(file_menu, "&Quit", self.close, QKeySequence.Quit)

        edit_menu = bar.addMenu("&Edit")
        self._add_action(edit_menu, "Select &all wells", self.grid.select_all, "Ctrl+A")
        self._add_action(edit_menu, "&Copy well contents", self.copy_well, "Ctrl+Shift+C")
        self._add_action(edit_menu, "&Paste into selection", self.paste_well, "Ctrl+Shift+V")
        self._add_action(edit_menu, "C&lear selected wells", self.clear_wells, "Del")
        edit_menu.addSeparator()
        self._add_action(edit_menu, "&Find well…", self.search, QKeySequence.Find)

        view_menu = bar.addMenu("&View")
        colour_menu = view_menu.addMenu("&Colour wells by")
        group = QActionGroup(self)
        group.setExclusive(True)
        for mode in COLOUR_MODES:
            action = QAction(mode, self, checkable=True)
            action.triggered.connect(lambda _=False, m=mode: self.colour_combo.setCurrentText(m))
            group.addAction(action)
            colour_menu.addAction(action)
        self._colour_actions = {a.text(): a for a in group.actions()}
        view_menu.addSeparator()
        self._add_action(view_menu, "&Refresh", self.refresh_all, "F5")

        help_menu = bar.addMenu("&Help")
        self._add_action(help_menu, "Where is my data?", self.show_data_info)
        self._add_action(help_menu, f"About {APP_NAME}", self.show_about)

    def _add_action(self, menu, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        self.addAction(action)          # keep the shortcut alive on macOS
        return action

    def _build_toolbar(self) -> None:
        bar = self.addToolBar("Main")
        bar.setMovable(False)
        bar.addAction("New plate", self.new_plate)
        bar.addAction("Duplicate", self.duplicate_plate)
        bar.addSeparator()
        bar.addAction("Export…", self.export_bundle)
        bar.addAction("Import…", self.import_bundle)
        bar.addSeparator()
        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("Find a well: cell line, patient, experiment…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMaximumWidth(340)
        self.search_box.returnPressed.connect(self.search)
        bar.addWidget(self.search_box)
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        self.due_button = QPushButton("Due: —", self)
        self.due_button.setFlat(True)
        self.due_button.clicked.connect(lambda: self.tabs.setCurrentWidget(self.dashboard))
        bar.addWidget(self.due_button)

    # ------------------------------------------------------------------- state
    def _restore_state(self) -> None:
        geometry = self.config.get("geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromHex(geometry.encode("ascii")))
        sizes = self.config.get("splitter")
        if sizes:
            self.splitter.setSizes(sizes)
        self.show_archived.setChecked(bool(self.config.get("show_archived", False)))
        mode = self.config.get("colour_mode", "Status")
        self.colour_combo.setCurrentText(mode if mode in COLOUR_MODES else "Status")

    def _save_state(self) -> None:
        self.config.update({
            "geometry": bytes(self.saveGeometry().toHex()).decode("ascii"),
            "splitter": self.splitter.sizes(),
            "show_archived": self.show_archived.isChecked(),
            "colour_mode": self.colour_combo.currentText(),
            "last_plate": self.current_plate_id(),
        })
        stored = paths.read_config()
        stored.update(self.config)
        paths.write_config(stored)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.well_panel.flush()
        self._save_state()
        self.store.close()
        super().closeEvent(event)

    def _raise_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------- plate list
    def refresh_plates(self, select_id: int | None = None, keep: bool = False) -> None:
        if keep and select_id is None:
            select_id = self.current_plate_id()
        needle = self.plate_filter.text().strip().lower()
        plates = self.store.list_plates(include_archived=self.show_archived.isChecked())
        if needle:
            plates = [p for p in plates
                      if needle in (p["name"] + " " + (p["location"] or "")
                                    + " " + (p["owner"] or "")).lower()]
        self.plate_list.blockSignals(True)
        self.plate_list.clear()
        target_row = -1
        for index, plate in enumerate(plates):
            bits = [plate["format"], f"{plate['used_wells']} used"]
            if plate["photo_count"]:
                bits.append(f"{plate['photo_count']} photos")
            if plate["location"]:
                bits.append(plate["location"])
            if plate["overdue"]:
                bits.append(f"⚠ {plate['overdue']} due")
            title = plate["name"] + ("  (archived)" if plate["archived"] else "")
            item = QListWidgetItem(f"{title}\n{' · '.join(bits)}")
            item.setData(Qt.UserRole, plate["id"])
            self.plate_list.addItem(item)
            if plate["id"] == select_id:
                target_row = index
        self.plate_list.blockSignals(False)
        if self.plate_list.count():
            self.plate_list.setCurrentRow(target_row if target_row >= 0 else 0)
        else:
            self.load_plate(None)

    def current_plate_id(self) -> int | None:
        item = self.plate_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_plate_selected(self, current, _previous) -> None:
        self.load_plate(current.data(Qt.UserRole) if current else None)

    def load_plate(self, plate_id: int | None) -> None:
        self.well_panel.flush()
        plate = self.store.get_plate(plate_id) if plate_id else None
        wells = self.store.wells_for_plate(plate_id) if plate else []
        self.grid.set_plate(plate, wells)
        if plate:
            self.plate_title.setText(plate["name"])
            bits = [plate["format"], f"{plate['n_rows']}×{plate['n_cols']}"]
            if plate["location"]:
                bits.append(plate["location"])
            if plate["owner"]:
                bits.append(plate["owner"])
            bits.append(f"created {plate['created_at'][:10]}")
            subtitle = " · ".join(bits)
            if plate["notes"]:
                subtitle += f"  —  {plate['notes'].splitlines()[0][:120]}"
            self.plate_subtitle.setText(subtitle)
        else:
            self.plate_title.setText("No plate selected")
            self.plate_subtitle.setText("")
        self._update_legend()

    def reload_wells(self) -> None:
        """Refresh well data without disturbing the selection or the editor."""
        plate_id = self.current_plate_id()
        if plate_id:
            self.grid.refresh_wells(self.store.wells_for_plate(plate_id))
        self._update_legend()

    def refresh_all(self) -> None:
        self.reload_wells()
        self.refresh_plates(keep=True)
        self.dashboard.refresh()
        self.library.refresh()
        self._update_status()

    # ---------------------------------------------------------------- reactions
    def _on_selection_changed(self) -> None:
        self.well_panel.set_wells(self.grid.selected_wells())
        self._update_status()

    def _on_well_data_changed(self) -> None:
        self.reload_wells()
        self.refresh_plates(keep=True)
        self.dashboard.refresh()
        self._update_status()

    def _on_well_double_clicked(self, well_id: int) -> None:
        self.grid.set_selection({well_id})
        self.well_panel.photo_panel.open_viewer()

    def _on_colour_mode(self, mode: str) -> None:
        self.grid.set_colour_mode(mode)
        action = getattr(self, "_colour_actions", {}).get(mode)
        if action is not None:
            action.setChecked(True)
        self._update_legend()

    def _update_legend(self) -> None:
        entries = self.grid.legend_entries() if self.grid.plate() else []
        if not entries:
            self.legend.setText("")
            return
        chips = " &nbsp; ".join(
            f"<span style='color:{colour.name()}'>&#9632;</span> {label}"
            for label, colour in entries[:18])
        extra = "" if len(entries) <= 18 else f" &nbsp; +{len(entries) - 18} more"
        marks = ("<span style='color:#4b5563'>&#9679;</span> has photos"
                 " &nbsp; <span style='color:%s'>&#9679;</span> due / overdue"
                 % theme.OVERDUE)
        self.legend.setText(f"{chips}{extra}<br>{marks}")

    def _update_status(self) -> None:
        stats = self.store.stats()
        selected = len(self.grid.selected_ids())
        parts = [f"{stats['plates']} plates", f"{stats['cell_lines']} cell lines",
                 f"{stats['photos']} photos"]
        if selected:
            wells = self.grid.selected_wells()
            labels = [well_label(w["row_idx"], w["col_idx"]) for w in wells]
            parts.insert(0, f"{selected} well(s) selected: "
                            + ", ".join(labels[:6]) + (" …" if selected > 6 else ""))
        parts.append(str(paths.data_dir()))
        self.statusBar().showMessage("   |   ".join(parts))
        due_bits = []
        if stats["overdue"]:
            due_bits.append(f"{stats['overdue']} overdue")
        if stats["due_today"]:
            due_bits.append(f"{stats['due_today']} today")
        self.due_button.setText("Due: " + (", ".join(due_bits) if due_bits else "nothing"))
        self.due_button.setStyleSheet(
            f"color: {theme.OVERDUE}; font-weight: 600;" if stats["overdue"] else "")

    # ------------------------------------------------------------ plate actions
    def new_plate(self) -> None:
        count = len(self.store.list_plates(include_archived=True)) + 1
        dialog = NewPlateDialog(self.store, self, suggested_name=f"Plate {count}")
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        plate_id = self.store.create_plate(**values)
        self.refresh_plates(select_id=plate_id)
        self.tabs.setCurrentIndex(0)
        self._update_status()

    def duplicate_plate(self) -> None:
        plate_id = self.current_plate_id()
        if plate_id is None:
            return
        plate = self.store.get_plate(plate_id)
        answer = QMessageBox.question(
            self, "Duplicate plate",
            f"Copy the layout of “{plate['name']}” into a new plate?\n\n"
            "Yes — copy the layout and the well contents\n"
            "No — copy the empty layout only",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if answer == QMessageBox.Cancel:
            return
        new_id = self.store.duplicate_plate(plate_id, f"{plate['name']} (copy)",
                                            with_contents=answer == QMessageBox.Yes)
        self.refresh_plates(select_id=new_id)
        self.plate_properties()

    def plate_properties(self) -> None:
        plate_id = self.current_plate_id()
        if plate_id is None:
            return
        plate = self.store.get_plate(plate_id)
        dialog = PlatePropertiesDialog(self.store, plate, self)
        if dialog.exec() == QDialog.Accepted:
            self.store.update_plate(plate_id, **dialog.values())
            self.refresh_plates(select_id=plate_id)
            self.load_plate(plate_id)

    def toggle_archive(self) -> None:
        plate_id = self.current_plate_id()
        if plate_id is None:
            return
        plate = self.store.get_plate(plate_id)
        self.store.update_plate(plate_id, archived=0 if plate["archived"] else 1)
        self.refresh_plates(select_id=plate_id if plate["archived"] else None)
        self._update_status()

    def delete_plate(self) -> None:
        plate_id = self.current_plate_id()
        if plate_id is None:
            return
        plate = self.store.get_plate(plate_id)
        answer = QMessageBox.warning(
            self, "Delete plate",
            f"Delete “{plate['name']}” and everything recorded on it?\n\n"
            "Its wells, photos and history are removed permanently. Consider"
            " archiving the plate instead.",
            QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        removed = self.store.delete_plate(plate_id)
        photos.delete_files(removed)
        self.refresh_plates()
        self.refresh_all()

    # ------------------------------------------------------------- well actions
    def copy_well(self) -> None:
        ids = self.grid.selected_ids()
        if len(ids) != 1:
            QMessageBox.information(self, "Copy well",
                                    "Select exactly one well to copy from.")
            return
        self._copied_well = ids[0]
        well = self.store.get_well(ids[0])
        self.statusBar().showMessage(
            f"Copied {well_label(well['row_idx'], well['col_idx'])} — "
            "select wells and use Edit ▸ Paste into selection", 6000)

    def paste_well(self) -> None:
        if self._copied_well is None:
            QMessageBox.information(self, "Paste well",
                                    "Copy a well first (Ctrl+Shift+C).")
            return
        ids = self.grid.selected_ids()
        if not ids:
            return
        self.store.copy_well_contents(self._copied_well, ids)
        self.reload_wells()
        self.well_panel.set_wells(self.grid.selected_wells())
        self._on_well_data_changed()

    def clear_wells(self) -> None:
        ids = self.grid.selected_ids()
        if not ids:
            return
        answer = QMessageBox.question(
            self, "Clear wells",
            f"Clear {len(ids)} well(s)? Their details and photos are removed.")
        if answer != QMessageBox.Yes:
            return
        removed = self.store.clear_wells(ids)
        photos.delete_files(removed)
        self.reload_wells()
        self.well_panel.set_wells(self.grid.selected_wells())
        self._on_well_data_changed()

    def _well_context_menu(self, position) -> None:
        ids = self.grid.selected_ids()
        if not ids:
            return
        menu = QMenu(self)
        menu.addAction(f"Copy well contents ({len(ids)} selected)"
                       if len(ids) == 1 else "Copy well contents", self.copy_well)
        paste = menu.addAction("Paste into selection", self.paste_well)
        paste.setEnabled(self._copied_well is not None)
        menu.addSeparator()
        menu.addAction("Mark due task done", self.well_panel.mark_done)
        menu.addAction("Due today", lambda: self._set_due(0))
        menu.addAction("Due in 3 days", lambda: self._set_due(3))
        menu.addAction("Due in a week", lambda: self._set_due(7))
        menu.addSeparator()
        menu.addAction("Clear wells…", self.clear_wells)
        menu.exec(self.grid.mapToGlobal(position))

    def _set_due(self, days: int) -> None:
        ids = self.grid.selected_ids()
        if not ids:
            return
        self.store.update_wells(ids, {"due_date": (date.today()
                                                   + timedelta(days=days)).isoformat()})
        self.reload_wells()
        self.well_panel.set_wells(self.grid.selected_wells())
        self._on_well_data_changed()

    def jump_to_well(self, plate_id: int, well_id: int) -> None:
        self.tabs.setCurrentIndex(0)
        if self.current_plate_id() != plate_id:
            plate = self.store.get_plate(plate_id)
            if plate and plate["archived"] and not self.show_archived.isChecked():
                self.show_archived.setChecked(True)
            self.refresh_plates(select_id=plate_id)
        self.grid.set_selection({well_id})

    def search(self) -> None:
        dialog = SearchDialog(self.store, self.search_box.text(), self)
        if dialog.exec() == QDialog.Accepted and dialog.chosen:
            self.jump_to_well(*dialog.chosen)

    # ------------------------------------------------------- export and import
    def export_bundle(self) -> None:
        suggested = str(Path.home() / archive.default_bundle_name())
        path, _ = QFileDialog.getSaveFileName(self, "Export everything to a bundle",
                                              suggested, archive.BUNDLE_FILTER)
        if not path:
            return
        include = QMessageBox.question(
            self, "Include photos?",
            "Include the photo files in the bundle?\n\n"
            "Yes — a complete copy (larger file)\n"
            "No — records only",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            manifest = archive.export_bundle(self.store, path, include_photos=include)
        except (OSError, archive.BundleError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QApplication.restoreOverrideCursor()
        counts = manifest["counts"]
        size_mb = Path(path).stat().st_size / (1024 * 1024)
        QMessageBox.information(
            self, "Export complete",
            f"Wrote {Path(path).name} ({size_mb:.1f} MB)\n\n"
            f"{counts['plates']} plates · {counts['cell_lines']} cell lines ·"
            f" {counts['photo_files']} photo files\n\n"
            "Copy it to the other computer and use File ▸ Import bundle.")

    def import_bundle(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import a bundle", "",
                                              archive.BUNDLE_FILTER)
        if not path:
            return
        try:
            manifest = archive.read_manifest(path)
        except archive.BundleError as exc:
            QMessageBox.critical(self, "Cannot read bundle", str(exc))
            return
        dialog = ImportDialog(path, manifest, self)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.mode() == "merge":
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                summary = archive.merge_bundle(self.store, path)
            except (OSError, archive.BundleError) as exc:
                QApplication.restoreOverrideCursor()
                QMessageBox.critical(self, "Import failed", str(exc))
                return
            QApplication.restoreOverrideCursor()
            self.refresh_all()
            self.refresh_plates(keep=True)
            lines = "\n".join(f"{key.replace('_', ' ')}: {value}"
                              for key, value in sorted(summary.items())) or "Nothing new."
            QMessageBox.information(self, "Merge complete", lines)
        else:
            confirm = QMessageBox.warning(
                self, "Replace everything",
                "Replace the local database and photos with this bundle?\n\n"
                "The current data is moved to a backup folder first.",
                QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
            if confirm != QMessageBox.Yes:
                return
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.store.close()
                backup = archive.replace_from_bundle(path)
                self._set_store(Store(paths.db_path()))
            except (OSError, archive.BundleError) as exc:
                QApplication.restoreOverrideCursor()
                QMessageBox.critical(self, "Import failed", str(exc))
                return
            QApplication.restoreOverrideCursor()
            QMessageBox.information(self, "Import complete",
                                    f"Data replaced.\nPrevious data kept in:\n{backup}")

    def _set_store(self, store: Store) -> None:
        self.store = store
        self.well_panel.store = store
        self.well_panel.photo_panel.store = store
        self.dashboard.store = store
        self.library.store = store
        self.library.patients.store = store
        self.library.cell_lines.store = store
        self.library.experiments.store = store
        self.reminders.set_store(store)
        self.refresh_plates()
        self.refresh_all()

    # -------------------------------------------------------------- housekeeping
    def open_data_folder(self) -> None:
        paths.ensure_dirs()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir())))

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        old_dir = str(paths.data_dir())
        self.config.update({k: v for k, v in values.items() if k != "data_dir"})
        self.reminders.enable_tray(bool(values["tray_icon"]))
        self.reminders.set_interval_hours(float(values["recheck_hours"]))
        if values["data_dir"] != old_dir:
            answer = QMessageBox.question(
                self, "Change data folder",
                f"Use this folder from now on?\n\n{values['data_dir']}\n\n"
                "Existing data is not moved — export a bundle first if you want to"
                " take it with you.")
            if answer == QMessageBox.Yes:
                self.well_panel.flush()
                self.store.close()
                paths.set_data_dir(values["data_dir"])
                paths.ensure_dirs()
                self._set_store(Store(paths.db_path()))
                self.statusBar().showMessage(str(paths.data_dir()))
        self._save_state()
        self._update_status()

    def show_data_info(self) -> None:
        root = paths.data_dir()
        QMessageBox.information(
            self, "Where is my data?",
            f"Data folder:\n{root}\n\n"
            f"Database: {paths.db_path().name}\nPhotos: photos/\n\n"
            "Use File ▸ Export bundle to put everything in one file you can copy to"
            " another computer, then File ▸ Import bundle there. Or point both"
            " computers at the same synced folder in Settings (one at a time).")

    def show_about(self) -> None:
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {APP_VERSION}<br><br>"
            "Cell culture plate tracking: virtual plates, per-well records,"
            " photo timelines and due-date reminders.<br><br>"
            f"Data folder:<br>{paths.data_dir()}")

    def check_reminders_on_start(self) -> None:
        if not self.config.get("notify_on_start", True):
            return
        days = int(self.config.get("reminder_days", 3))
        rows = self.reminders.check(days)
        if rows:
            self.tabs.setCurrentWidget(self.dashboard)
