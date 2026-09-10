"""Headless UI exercise.

Qt only reports a missing method when the code path actually runs, so this
walks the real widgets: build the window, edit wells through the panel, drive
the dashboard, library, copy/paste/clear, and open the photo viewer (including
a resize, which is its own code path).

Modal calls have to be defused two different ways.  QMessageBox's static
helpers and the app's own QDialog subclasses are ordinary Python attributes and
can be monkeypatched.  Methods on Qt's C++ classes cannot - assigning to
`QMenu.exec` looks like it worked but instance calls still enter the native
modal loop - so a popup menu is dismissed with a timer from inside that loop.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PIL import Image                                        # noqa: E402
from PySide6.QtCore import QSize, Qt, QTimer                  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog,             # noqa: E402
                               QMessageBox)

from plate_manager import archive, photos                     # noqa: E402
from plate_manager.demo import create_demo_data               # noqa: E402
from plate_manager.ui import dialogs, theme                   # noqa: E402
from plate_manager.ui.main_window import MainWindow           # noqa: E402
from plate_manager.ui.photo_panel import DateDialog, PhotoViewer   # noqa: E402

CONFIG = {"tray_icon": False, "recheck_hours": 0, "notify_on_start": False}
OUR_DIALOGS = (dialogs.NewPlateDialog, dialogs.PlatePropertiesDialog,
               dialogs.SettingsDialog, dialogs.ImportDialog, dialogs.SearchDialog,
               DateDialog, PhotoViewer)


def dismiss_popup() -> None:
    """Close whatever popup is open (called from inside its own event loop)."""
    popup = QApplication.activePopupWidget()
    if popup is not None:
        popup.close()


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.apply_to(instance)
    return instance


@pytest.fixture()
def answer_yes(monkeypatch):
    """Say yes to confirmations and accept the app's dialogs with their defaults."""
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "about", staticmethod(lambda *a, **k: None))
    for dialog in OUR_DIALOGS:
        monkeypatch.setattr(dialog, "exec", lambda self: QDialog.Accepted)


@pytest.fixture()
def window(app, store, answer_yes):
    create_demo_data(store)
    win = MainWindow(store, dict(CONFIG))
    win.resize(1400, 900)
    win.show()
    app.processEvents()
    yield win
    win.close()


def test_window_shows_the_plates(window, store):
    assert window.plate_list.count() == 3
    assert window.current_plate_id() is not None
    assert window.plate_title.text()
    assert "wells" in window.well_panel.heading.text().lower() \
        or window.well_panel.heading.text() == "No well selected"


def test_editing_a_single_well_saves_itself(window, store, app):
    wells = store.wells_for_plate(window.current_plate_id())
    window.grid.set_selection({wells[0]["id"]})
    app.processEvents()
    panel = window.well_panel
    panel.fields["treatment"].setText("IL-1b 10 ng/ml")
    panel._mark("treatment")
    panel.flush()
    app.processEvents()
    assert store.get_well(wells[0]["id"])["treatment"] == "IL-1b 10 ng/ml"


def test_bulk_edit_touches_only_edited_fields(window, store, app):
    wells = store.wells_for_plate(window.current_plate_id())
    column = [w for w in wells if w["col_idx"] == 0]
    before = {w["id"]: w["cell_line"] for w in column}
    window.grid.set_selection([w["id"] for w in column])
    app.processEvents()
    panel = window.well_panel
    assert panel.apply_bar.isVisible()
    panel.fields["experiment"].setText("Bulk assay")
    panel._mark("experiment")
    panel.apply_changes()
    app.processEvents()
    for well in store.get_wells(list(before)):
        assert well["experiment"] == "Bulk assay"
        assert well["cell_line"] == before[well["id"]]


def test_due_actions_from_the_grid_and_panel(window, store, app):
    wells = store.wells_for_plate(window.current_plate_id())
    window.grid.set_selection({wells[0]["id"]})
    app.processEvents()
    window._set_due(3)
    app.processEvents()
    assert store.get_well(wells[0]["id"])["due_date"]
    window.well_panel.snooze(1)
    window.well_panel.mark_done()
    app.processEvents()
    refreshed = store.get_well(wells[0]["id"])
    assert refreshed["due_date"] == ""
    assert any("Done" in e["detail"] for e in store.events_for_well(refreshed["id"]))


def test_copy_paste_and_clear(window, store, app):
    wells = store.wells_for_plate(window.current_plate_id())
    source, target = wells[0], wells[-1]
    window.grid.set_selection({source["id"]})
    window.copy_well()
    window.grid.set_selection({target["id"]})
    window.paste_well()
    app.processEvents()
    assert store.get_well(target["id"])["cell_line"] == source["cell_line"]
    window.clear_wells()
    app.processEvents()
    assert store.get_well(target["id"])["cell_line"] is None


def test_context_menu_and_colour_modes(window, app):
    from plate_manager.ui.plate_grid import COLOUR_MODES
    window.grid.select_all()
    app.processEvents()
    QTimer.singleShot(30, dismiss_popup)          # the menu is genuinely modal
    window._well_context_menu(window.grid.rect().center())
    for mode in COLOUR_MODES:
        window.colour_combo.setCurrentText(mode)
        app.processEvents()
        window.grid.legend_entries()
    window.grid.grab()          # a full paint pass
    assert window.legend.text()


def test_dashboard_and_library(window, store, app):
    dashboard = window.dashboard
    for index in range(dashboard.horizon.count()):
        dashboard.horizon.setCurrentIndex(index)
        app.processEvents()
    dashboard.horizon.setCurrentIndex(5)
    app.processEvents()
    assert dashboard.table.rowCount() > 0
    dashboard.table.selectRow(0)
    plate_id, well_id = dashboard.table.item(0, 0).data(Qt.UserRole)
    dashboard.snooze(3)
    dashboard.table.selectRow(0)
    dashboard.mark_done()
    app.processEvents()
    window.jump_to_well(plate_id, well_id)
    app.processEvents()
    assert window.current_plate_id() == plate_id
    assert window.grid.selected_ids() == [well_id]

    window.library.refresh()
    window.library.cell_lines.table.item(0, 1).setText("Human")
    app.processEvents()
    assert any(line["species"] == "Human" for line in store.list_cell_lines())


def test_photo_viewer_steps_and_survives_a_resize(store, plate, app, tmp_path):
    """The resize path is separate from loading - it used to crash here."""
    well = store.wells_for_plate(plate)[0]
    for index, colour in enumerate(((180, 120, 120), (120, 150, 180))):
        path = tmp_path / f"shot{index}.png"
        Image.new("RGB", (320, 240), colour).save(path)
        store.add_photo(well["id"], photos.store_file(path, well["uid"]),
                        f"2026-0{index + 1}-15")
    rows = store.photos_for_well(well["id"])
    viewer = PhotoViewer(store, rows, 0, "A1")
    viewer.show()
    app.processEvents()
    assert viewer.image.pixmap() is not None and not viewer.image.pixmap().isNull()

    viewer.resize(QSize(600, 500))
    app.processEvents()
    viewer.resize(QSize(1000, 820))
    app.processEvents()

    viewer.step(1)
    app.processEvents()
    assert viewer.index == 1
    assert "2 of 2" in viewer.counter.text()

    viewer.caption.setText("mid-treatment")
    viewer._save_caption()
    viewer.date.set_value("2026-03-01")
    viewer._save_date()
    app.processEvents()
    updated = store.photos_for_well(well["id"])
    assert any(p["caption"] == "mid-treatment" for p in updated)
    assert any(p["taken_at"] == "2026-03-01" for p in updated)
    viewer.close()


def test_photo_viewer_reports_a_missing_file(store, plate, app):
    well = store.wells_for_plate(plate)[0]
    store.add_photo(well["id"], "nowhere/gone.png", "2026-01-01")
    viewer = PhotoViewer(store, store.photos_for_well(well["id"]), 0, "A1")
    viewer.show()
    app.processEvents()
    assert "Missing file" in viewer.image.text()
    viewer.close()


def test_photo_panel_imports_and_removes(window, store, app, tmp_path, answer_yes):
    wells = store.wells_for_plate(window.current_plate_id())
    window.grid.set_selection({wells[1]["id"]})
    app.processEvents()
    panel = window.well_panel.photo_panel
    path = tmp_path / "new.png"
    Image.new("RGB", (200, 150), (200, 200, 160)).save(path)
    panel._import_files([str(path)])
    app.processEvents()
    assert store.get_well(wells[1]["id"])["photo_count"] == 1
    panel.list.setCurrentRow(0)
    panel.remove_selected()
    app.processEvents()
    assert store.get_well(wells[1]["id"])["photo_count"] == 0


def test_dialogs_build_and_report_their_values(store, plate, app, tmp_path):
    new_plate = dialogs.NewPlateDialog(store, None, "Plate 9")
    new_plate.format.setCurrentText(dialogs.CUSTOM_FORMAT)
    new_plate.rows.setValue(3)
    new_plate.cols.setValue(5)
    values = new_plate.values()
    assert values["n_rows"] == 3 and values["n_cols"] == 5
    assert "custom" in values["fmt"]

    properties = dialogs.PlatePropertiesDialog(store, store.get_plate(plate), None)
    properties.archived.setChecked(True)
    assert properties.values()["archived"] == 1

    settings = dialogs.SettingsDialog({"reminder_days": 5, "recheck_hours": 2}, None)
    assert settings.values()["reminder_days"] == 5

    bundle = tmp_path / "b.plmz"
    archive.export_bundle(store, bundle)
    import_dialog = dialogs.ImportDialog(str(bundle), archive.read_manifest(bundle), None)
    assert import_dialog.mode() == "merge"
    import_dialog.replace.setChecked(True)
    assert import_dialog.mode() == "replace"

    store.update_wells([store.wells_for_plate(plate)[0]["id"]],
                       {"cell_line": "PT-099 cells"})
    search = dialogs.SearchDialog(store, "PT-099", None)
    assert search.table.rowCount() == 1
    search._accept_row()
    assert search.chosen is not None

    from plate_manager.ui.photo_panel import DateDialog
    date_dialog = DateDialog("2026-05-05", None)
    assert date_dialog.value() == "2026-05-05"
    date_dialog.date.clear_date()
    assert date_dialog.value() == ""


def test_export_and_merge_through_the_window(window, store, app, tmp_path, monkeypatch):
    bundle = tmp_path / "window.plmz"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(bundle), "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(bundle), "")))
    window.export_bundle()
    assert bundle.exists()
    monkeypatch.setattr(dialogs.ImportDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(dialogs.ImportDialog, "mode", lambda self: "merge")
    window.import_bundle()          # merging its own bundle changes nothing
    app.processEvents()
    assert len(store.list_plates()) == 3


def test_plate_lifecycle_through_the_window(window, store, app):
    start = len(store.list_plates(include_archived=True))
    window.duplicate_plate()        # answers Yes: copy contents
    app.processEvents()
    assert len(store.list_plates(include_archived=True)) == start + 1
    window.toggle_archive()
    app.processEvents()
    window.show_archived.setChecked(True)
    app.processEvents()
    window.delete_plate()
    app.processEvents()
    assert len(store.list_plates(include_archived=True)) == start
    window.show_data_info()
    window.show_about()
    window.refresh_all()
