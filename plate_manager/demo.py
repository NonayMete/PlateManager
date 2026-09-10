"""Sample data (`plate-manager --demo`) so the app can be tried out with
something in it.  Everything it creates is ordinary data - delete the plates or
the whole data folder afterwards.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import paths, photos
from .models import today_iso
from .repo import Store


def _offset(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def create_demo_data(store: Store) -> list[int]:
    """Build three plausible plates; returns the new plate ids."""
    plate_ids = []

    # ---- a 6-well expansion plate, one donor, mid-passage
    expansion = store.create_plate("PT-014 expansion P4", "6-well", 2, 3,
                                   "Incubator 2, shelf B", "JL",
                                   "Thawed 3 vials, expanding for the IL-1b study.")
    plate_ids.append(expansion)
    wells = store.wells_for_plate(expansion)
    store.update_wells([w["id"] for w in wells], {
        "cell_line": "PT-014 chondrocytes",
        "patient_code": "PT-014",
        "passage": "P4",
        "medium": "DMEM/F12 + 10 % FBS",
        "seeded_on": _offset(-6),
        "status": "Growing",
    })
    store.update_wells([wells[0]["id"]], {
        "confluence": 80, "status": "Confluent",
        "due_date": _offset(-1), "due_task": "Passage",
        "notes": "Ready to split - looked crowded yesterday.",
    })
    store.update_wells([wells[1]["id"]], {
        "confluence": 55, "due_date": today_iso(), "due_task": "Change medium"})
    store.update_wells([wells[2]["id"]], {"confluence": 40, "due_date": _offset(3),
                                          "due_task": "Check confluence"})
    store.update_wells([wells[4]["id"]], {"status": "Contaminated",
                                          "notes": "Cloudy on day 3 - discarded."})

    # a small photo timeline on the first well
    first = wells[0]
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        Image = None
    if Image is not None:
        for index, (days, shade, label) in enumerate((
                (-6, (232, 238, 228), "day 0 - seeded"),
                (-3, (206, 226, 205), "day 3 - 40 %"),
                (-1, (176, 214, 178), "day 5 - 80 %"))):
            image = Image.new("RGB", (480, 360), shade)
            draw = ImageDraw.Draw(image)
            for x in range(20, 460, 40):
                for y in range(20, 340, 40):
                    if (x + y + index * 40) % 120 < 60:
                        draw.ellipse([x, y, x + 26, y + 18], outline=(90, 110, 95), width=2)
            draw.text((16, 330), label, fill=(60, 70, 60))
            temp = paths.data_dir() / f"_demo_{index}.png"
            image.save(temp)
            rel = photos.store_file(temp, first["uid"])
            temp.unlink(missing_ok=True)
            store.add_photo(first["id"], rel, _offset(days), label)

    # ---- a 96-well dose response across three donors
    assay = store.create_plate("IL-1b dose response", "96-well", 8, 12,
                               "Incubator 1", "JL",
                               "Rows A-F: three donors in duplicate. Columns 1-6 dose"
                               " series, 7-12 untreated controls.")
    plate_ids.append(assay)
    wells = {(w["row_idx"], w["col_idx"]): w for w in store.wells_for_plate(assay)}
    donors = (("PT-014", "PT-014 chondrocytes"), ("PT-021", "PT-021 chondrocytes"),
              ("PT-033", "PT-033 synoviocytes"))
    doses = ("0.1 ng/ml", "0.3 ng/ml", "1 ng/ml", "3 ng/ml", "10 ng/ml", "30 ng/ml")
    for donor_index, (patient, line) in enumerate(donors):
        for repeat in range(2):
            row = donor_index * 2 + repeat
            for col in range(12):
                well = wells.get((row, col))
                if well is None:
                    continue
                treated = col < 6
                store.update_wells([well["id"]], {
                    "cell_line": line,
                    "patient_code": patient,
                    "experiment": "IL-1b dose response",
                    "passage": "P4",
                    "medium": "DMEM/F12 + 1 % FBS",
                    "seeded_on": _offset(-2),
                    "status": "Treated" if treated else "Seeded",
                    "treatment": f"IL-1b {doses[col]}" if treated else "Untreated control",
                    "due_date": _offset(1) if row == 0 else "",
                    "due_task": "Harvest supernatant" if row == 0 else "",
                })

    # ---- scaffold work, one flask-style plate
    scaffold = store.create_plate("Scaffold seeding trial", "24-well", 4, 6,
                                  "Incubator 2, shelf A", "team",
                                  "Comparing two scaffold coatings.")
    plate_ids.append(scaffold)
    wells = store.wells_for_plate(scaffold)
    for index, well in enumerate(wells[:12]):
        store.update_wells([well["id"]], {
            "cell_line": "PT-021 chondrocytes",
            "patient_code": "PT-021",
            "experiment": "Scaffold coating comparison",
            "treatment": "Collagen coating" if index % 2 == 0 else "Fibronectin coating",
            "seeded_on": _offset(-9),
            "status": "Growing",
            "due_date": _offset(2) if index < 4 else _offset(6),
            "due_task": "Image" if index < 4 else "Fix / stain",
        })

    store.update_lookup("patients", store.ensure_patient("PT-014"),
                        diagnosis="OA, knee (femoral condyle)")
    store.update_lookup("patients", store.ensure_patient("PT-021"),
                        diagnosis="OA, hip")
    store.update_lookup("patients", store.ensure_patient("PT-033"),
                        diagnosis="RA, knee synovium")
    for name in ("PT-014 chondrocytes", "PT-021 chondrocytes", "PT-033 synoviocytes"):
        store.update_lookup("cell_lines", store.ensure_cell_line(name),
                            species="Human", tissue="Cartilage" if "chondro" in name
                            else "Synovium")
    store.conn.commit()
    return plate_ids
