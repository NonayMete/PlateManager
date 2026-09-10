"""Photo import and the export/import bundle round trip."""
from __future__ import annotations

import time
from datetime import date

import pytest
from PIL import Image

from plate_manager import archive, paths, photos
from plate_manager.repo import Store


def make_image(path, colour=(120, 160, 120)) -> str:
    Image.new("RGB", (240, 180), colour).save(path)
    return str(path)


def test_photo_is_copied_into_the_data_folder_with_a_date(store, plate, tmp_path):
    well = store.wells_for_plate(plate)[0]
    source = make_image(tmp_path / "capture.png")
    rel = photos.store_file(source, well["uid"])
    store.add_photo(well["id"], rel, photos.guess_taken_at(source))

    stored = store.photos_for_well(well["id"])
    assert len(stored) == 1
    assert photos.absolute(stored[0]["rel_path"]).exists()
    assert stored[0]["taken_at"] == date.today().isoformat()
    assert store.get_well(well["id"])["photo_count"] == 1
    assert photos.thumbnail(rel, 128) is not None


def test_photos_are_listed_oldest_first_with_undated_last(store, plate, tmp_path):
    well = store.wells_for_plate(plate)[0]
    for name, taken in (("b.png", "2026-03-02"), ("a.png", "2026-01-05"),
                        ("c.png", "")):
        rel = photos.store_file(make_image(tmp_path / name), well["uid"])
        store.add_photo(well["id"], rel, taken)
    assert [p["taken_at"] for p in store.photos_for_well(well["id"])] == [
        "2026-01-05", "2026-03-02", ""]


def test_deleting_a_photo_removes_the_file(store, plate, tmp_path):
    well = store.wells_for_plate(plate)[0]
    rel = photos.store_file(make_image(tmp_path / "x.png"), well["uid"])
    photo_id = store.add_photo(well["id"], rel)
    photos.thumbnail(rel, 128)
    removed = store.delete_photo(photo_id)
    photos.delete_files([removed])
    assert not photos.absolute(rel).exists()
    assert not photos.thumb_path(rel, 128).exists()
    assert store.photos_for_well(well["id"]) == []


def test_clearing_a_well_takes_its_photos(store, plate, tmp_path):
    well = store.wells_for_plate(plate)[0]
    rel = photos.store_file(make_image(tmp_path / "x.png"), well["uid"])
    store.add_photo(well["id"], rel)
    removed = store.clear_wells([well["id"]])
    photos.delete_files(removed)
    assert removed == [rel]
    assert not photos.absolute(rel).exists()


def _populate(store, plate, tmp_path):
    wells = store.wells_for_plate(plate)
    store.update_wells([w["id"] for w in wells[:12]],
                       {"cell_line": "PT-014 chondrocytes", "patient_code": "PT-014",
                        "experiment": "IL-1b dose", "status": "Growing",
                        "due_date": date.today().isoformat(), "due_task": "Change medium"})
    rel = photos.store_file(make_image(tmp_path / "day0.png"), wells[0]["uid"])
    store.add_photo(wells[0]["id"], rel, "2026-08-01", "day 0")
    return wells


def test_bundle_round_trip_into_an_empty_folder(store, plate, tmp_path, monkeypatch):
    _populate(store, plate, tmp_path)
    bundle = tmp_path / "out.plmz"
    manifest = archive.export_bundle(store, bundle)
    assert manifest["counts"]["plates"] == 1
    assert manifest["counts"]["photo_files"] == 1
    assert archive.read_manifest(bundle)["app"] == "Plate Manager"

    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path / "second"))
    paths.ensure_dirs()
    other = Store(paths.db_path())
    summary = archive.merge_bundle(other, bundle)
    assert summary["plates_added"] == 1
    assert summary["wells_added"] == 96
    assert summary["photos_added"] == 1

    plates = other.list_plates()
    assert len(plates) == 1 and plates[0]["name"] == "Test plate"
    filled = [w for w in other.wells_for_plate(plates[0]["id"]) if w["cell_line"]]
    assert len(filled) == 12
    photo_row = other.photos_for_well(
        [w for w in other.wells_for_plate(plates[0]["id"]) if w["photo_count"]][0]["id"])[0]
    assert photo_row["taken_at"] == "2026-08-01"
    assert photos.absolute(photo_row["rel_path"]).exists()
    other.close()


def test_merging_twice_changes_nothing_the_second_time(store, plate, tmp_path, monkeypatch):
    _populate(store, plate, tmp_path)
    bundle = tmp_path / "out.plmz"
    archive.export_bundle(store, bundle)
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path / "second"))
    paths.ensure_dirs()
    other = Store(paths.db_path())
    archive.merge_bundle(other, bundle)
    again = archive.merge_bundle(other, bundle)
    assert not [key for key in again if key.endswith(("_added", "_updated"))]
    other.close()


def test_the_more_recent_edit_wins(store, plate, tmp_path, monkeypatch):
    wells = _populate(store, plate, tmp_path)
    bundle = tmp_path / "out.plmz"
    archive.export_bundle(store, bundle)

    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path / "second"))
    paths.ensure_dirs()
    other = Store(paths.db_path())
    archive.merge_bundle(other, bundle)
    remote = [w for w in other.wells_for_plate(other.list_plates()[0]["id"])
              if (w["row_idx"], w["col_idx"]) == (0, 0)][0]

    # edit on the far machine, then on this one - this one is newer
    other.update_wells([remote["id"]], {"notes": "edited over there"})
    time.sleep(0.01)
    store.update_wells([wells[0]["id"]], {"notes": "edited here, later"})
    archive.export_bundle(store, bundle)
    archive.merge_bundle(other, bundle)
    assert other.get_well(remote["id"])["notes"] == "edited here, later"

    # and the other way round
    time.sleep(0.01)
    other.update_wells([remote["id"]], {"notes": "final word from over there"})
    assert other.get_well(remote["id"])["notes"] == "final word from over there"
    other.close()


def test_replace_keeps_a_backup(store, plate, tmp_path):
    _populate(store, plate, tmp_path)
    bundle = tmp_path / "out.plmz"
    archive.export_bundle(store, bundle)
    store.close()
    backup = archive.replace_from_bundle(bundle)
    assert (backup / "plates.db").exists()
    restored = Store(paths.db_path())
    assert len(restored.list_plates()) == 1
    restored.close()


def test_a_random_zip_is_rejected(tmp_path):
    import zipfile
    bad = tmp_path / "notes.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("hello.txt", "not a bundle")
    with pytest.raises(archive.BundleError):
        archive.read_manifest(bad)


def test_export_without_photos_is_records_only(store, plate, tmp_path):
    _populate(store, plate, tmp_path)
    bundle = tmp_path / "records.plmz"
    manifest = archive.export_bundle(store, bundle, include_photos=False)
    assert manifest["counts"]["photo_files"] == 0
    assert archive.read_manifest(bundle)["counts"]["photo_files"] == 0
