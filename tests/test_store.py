"""Store behaviour: plates, wells, lookups, due dates, history."""
from __future__ import annotations

from datetime import date, timedelta

from plate_manager.models import days_until, pretty_due, row_letters, well_label
from plate_manager.repo import well_is_empty


def test_new_plate_has_one_row_per_well(store, plate):
    wells = store.wells_for_plate(plate)
    assert len(wells) == 96
    assert {(w["row_idx"], w["col_idx"]) for w in wells} == {
        (r, c) for r in range(8) for c in range(12)}
    assert all(well_is_empty(w) for w in wells)


def test_well_labels():
    assert well_label(0, 0) == "A1"
    assert well_label(7, 11) == "H12"
    assert row_letters(25) == "Z"
    assert row_letters(26) == "AA"


def test_editing_a_well_creates_the_lookups_it_names(store, plate):
    well = store.wells_for_plate(plate)[0]
    store.update_wells([well["id"]], {"cell_line": "PT-014 chondrocytes",
                                      "patient_code": "PT-014",
                                      "experiment": "IL-1b dose"})
    assert store.suggestions("cell_line") == ["PT-014 chondrocytes"]
    assert store.suggestions("patient_code") == ["PT-014"]
    assert store.suggestions("experiment") == ["IL-1b dose"]
    # naming the same line again is not a duplicate
    other = store.wells_for_plate(plate)[1]
    store.update_wells([other["id"]], {"cell_line": "pt-014 CHONDROCYTES"})
    assert len(store.list_cell_lines()) == 1


def test_cell_line_carries_its_patient_to_the_next_well(store, plate):
    wells = store.wells_for_plate(plate)
    store.update_wells([wells[0]["id"]], {"cell_line": "PT-021 chondrocytes",
                                          "patient_code": "PT-021"})
    store.update_wells([wells[5]["id"]], {"cell_line": "PT-021 chondrocytes"})
    assert store.get_well(wells[5]["id"])["patient_code"] == "PT-021"


def test_bulk_edit_only_writes_the_fields_given(store, plate):
    wells = store.wells_for_plate(plate)
    column = [w["id"] for w in wells if w["col_idx"] == 0]
    store.update_wells(column, {"cell_line": "Line A", "passage": "P2"})
    store.update_wells(column, {"experiment": "Assay 1"})
    for well in store.get_wells(column):
        assert well["cell_line"] == "Line A"
        assert well["passage"] == "P2"
        assert well["experiment"] == "Assay 1"


def test_clearing_empties_the_well_and_logs_it(store, plate):
    well = store.wells_for_plate(plate)[0]
    store.update_wells([well["id"]], {"cell_line": "Line A", "notes": "x"})
    store.clear_wells([well["id"]])
    assert well_is_empty(store.get_well(well["id"]))
    assert any("cleared" in e["detail"].lower() for e in store.events_for_well(well["id"]))


def test_history_records_what_changed(store, plate):
    well = store.wells_for_plate(plate)[0]
    store.update_wells([well["id"]], {"status": "Seeded"})
    store.update_wells([well["id"]], {"status": "Confluent"})
    details = [e["detail"] for e in store.events_for_well(well["id"])]
    assert any("Seeded -> Confluent" in d for d in details)


def test_due_wells_respects_the_horizon(store, plate):
    wells = store.wells_for_plate(plate)
    today = date.today()
    store.update_wells([wells[0]["id"]], {"due_date": (today - timedelta(days=2)).isoformat()})
    store.update_wells([wells[1]["id"]], {"due_date": today.isoformat()})
    store.update_wells([wells[2]["id"]], {"due_date": (today + timedelta(days=5)).isoformat()})
    store.update_wells([wells[3]["id"]], {"due_date": (today + timedelta(days=40)).isoformat()})

    assert len(store.due_wells(-1)) == 1        # overdue only
    assert len(store.due_wells(0)) == 2         # plus today
    assert len(store.due_wells(7)) == 3
    assert len(store.due_wells(60)) == 4
    stats = store.stats()
    assert stats["overdue"] == 1 and stats["due_today"] == 1


def test_archived_plates_do_not_raise_alerts(store, plate):
    well = store.wells_for_plate(plate)[0]
    store.update_wells([well["id"]], {"due_date": date.today().isoformat()})
    assert len(store.due_wells(0)) == 1
    store.update_plate(plate, archived=1)
    assert store.due_wells(0) == []
    assert store.list_plates() == []
    assert len(store.list_plates(include_archived=True)) == 1


def test_duplicate_plate_copies_contents_but_not_due_dates(store, plate):
    wells = store.wells_for_plate(plate)
    store.update_wells([wells[0]["id"]], {"cell_line": "Line A", "passage": "P2",
                                          "due_date": date.today().isoformat()})
    copy_id = store.duplicate_plate(plate, "Copy")
    copied = store.wells_for_plate(copy_id)[0]
    assert copied["cell_line"] == "Line A" and copied["passage"] == "P2"
    assert copied["due_date"] == ""


def test_copy_well_contents(store, plate):
    wells = store.wells_for_plate(plate)
    store.update_wells([wells[0]["id"]], {"cell_line": "Line A", "treatment": "IL-1b"})
    store.copy_well_contents(wells[0]["id"], [w["id"] for w in wells[1:4]])
    for well in store.get_wells([w["id"] for w in wells[1:4]]):
        assert well["cell_line"] == "Line A" and well["treatment"] == "IL-1b"


def test_search_finds_wells_by_any_name(store, plate):
    well = store.wells_for_plate(plate)[0]
    store.update_wells([well["id"]], {"cell_line": "PT-014 chondrocytes",
                                      "notes": "vial 3"})
    assert len(store.search_wells("PT-014")) == 1
    assert len(store.search_wells("vial")) == 1
    assert store.search_wells("nothing here") == []
    assert store.search_wells("  ") == []


def test_renaming_a_cell_line_follows_every_well(store, plate):
    well = store.wells_for_plate(plate)[0]
    store.update_wells([well["id"]], {"cell_line": "Old name"})
    line_id = store.list_cell_lines()[0]["id"]
    store.update_lookup("cell_lines", line_id, name="New name")
    assert store.get_well(well["id"])["cell_line"] == "New name"


def test_deleting_a_lookup_leaves_the_well_intact(store, plate):
    well = store.wells_for_plate(plate)[0]
    store.update_wells([well["id"]], {"cell_line": "Line A", "notes": "keep me"})
    store.delete_lookup("cell_lines", store.list_cell_lines()[0]["id"])
    remaining = store.get_well(well["id"])
    assert remaining["cell_line"] is None
    assert remaining["notes"] == "keep me"


def test_date_helpers():
    today = date.today()
    assert days_until(today.isoformat()) == 0
    assert days_until((today + timedelta(days=3)).isoformat()) == 3
    assert days_until("") is None
    assert "today" in pretty_due(today.isoformat())
    assert "overdue by 2 days" in pretty_due((today - timedelta(days=2)).isoformat())
