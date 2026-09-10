"""Export and import: moving the whole dataset between computers.

A bundle is a single `.plmz` file (a zip) holding

    manifest.json     what it is, when it was made, how much is inside
    plates.db         a consistent snapshot of the database
    photos/...        the photo originals

Two ways to bring one in:

* **Merge** - row-by-row, matched on the uids every row carries.  Rows you do
  not have are added, rows you both have keep whichever copy was edited last.
  Safe to run repeatedly, so two machines can pass a bundle back and forth.
* **Replace** - swap the local database and photos for the bundle's, keeping a
  timestamped backup of what was there before.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import APP_NAME, APP_VERSION, paths, photos
from .db import SCHEMA_VERSION
from .repo import Store

BUNDLE_SUFFIX = ".plmz"
BUNDLE_FILTER = f"Plate Manager bundle (*{BUNDLE_SUFFIX});;Zip archive (*.zip);;All files (*)"
DB_ENTRY = "plates.db"
MANIFEST_ENTRY = "manifest.json"

WELL_FIELDS = ("status", "passage", "treatment", "medium", "seeded_on",
               "confluence", "due_date", "due_task", "notes")


class BundleError(Exception):
    """The chosen file is not a usable Plate Manager bundle."""


def default_bundle_name() -> str:
    return f"plate-manager-{datetime.now():%Y%m%d-%H%M}{BUNDLE_SUFFIX}"


# --------------------------------------------------------------------- export
def export_bundle(store: Store, dest: str | Path, include_photos: bool = True) -> dict:
    """Write a bundle of everything in `store` to `dest`; returns the manifest."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    stats = store.stats()
    photo_paths = store.all_photo_paths() if include_photos else []
    manifest = {
        "app": APP_NAME,
        "app_version": APP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
        "photos_included": bool(include_photos),
        "counts": {**stats, "photo_files": len(photo_paths)},
    }
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / DB_ENTRY
        store.conn.commit()
        target = sqlite3.connect(str(snapshot))
        with target:
            store.conn.backup(target)
        target.close()
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_ENTRY, json.dumps(manifest, indent=2))
            zf.write(snapshot, DB_ENTRY)
            missing = 0
            for rel in photo_paths:
                src = photos.absolute(rel)
                if src.exists():
                    zf.write(src, f"photos/{rel}")
                else:
                    missing += 1
    manifest["counts"]["photo_files_missing"] = missing
    return manifest


def read_manifest(bundle: str | Path) -> dict:
    """Peek inside a bundle without importing it."""
    try:
        with zipfile.ZipFile(bundle) as zf:
            names = set(zf.namelist())
            if DB_ENTRY not in names:
                raise BundleError(f"{Path(bundle).name} has no {DB_ENTRY} inside it.")
            if MANIFEST_ENTRY in names:
                manifest = json.loads(zf.read(MANIFEST_ENTRY).decode("utf-8"))
            else:
                manifest = {"app": "unknown"}
            manifest.setdefault("counts", {})
            manifest["counts"]["photo_files"] = sum(
                1 for n in names if n.startswith("photos/") and not n.endswith("/"))
            return manifest
    except zipfile.BadZipFile as exc:
        raise BundleError(f"{Path(bundle).name} is not a Plate Manager bundle.") from exc


# --------------------------------------------------------------------- import
def merge_bundle(store: Store, bundle: str | Path) -> dict:
    """Merge a bundle into the open store.  Returns a summary of what changed."""
    tally: Counter[str] = Counter()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            with zipfile.ZipFile(bundle) as zf:
                zf.extractall(root)
        except zipfile.BadZipFile as exc:
            raise BundleError(f"{Path(bundle).name} is not a Plate Manager bundle.") from exc
        src_path = root / DB_ENTRY
        if not src_path.exists():
            raise BundleError(f"{Path(bundle).name} has no {DB_ENTRY} inside it.")
        src = sqlite3.connect(str(src_path))
        src.row_factory = sqlite3.Row
        try:
            _merge(store, src, root, tally)
        finally:
            src.close()
    store.conn.commit()
    return dict(tally)


def _merge(store: Store, src: sqlite3.Connection, bundle_root: Path,
           tally: Counter) -> None:
    conn = store.conn
    patients = _merge_patients(conn, src, tally)
    lines = _merge_cell_lines(conn, src, patients, tally)
    experiments = _merge_experiments(conn, src, tally)
    plates, wells = _merge_plates(conn, src, patients, lines, experiments, tally)
    _merge_photos(conn, src, bundle_root, wells, tally)
    _merge_events(conn, src, plates, wells, tally)


def _blank_fills(local: sqlite3.Row, row: sqlite3.Row, fields) -> dict:
    """Only ever fill in fields that are empty locally - never overwrite."""
    return {f: row[f] for f in fields
            if not (local[f] or "") and (row[f] or "")}


def _update(conn: sqlite3.Connection, table: str, row_id: int, values: dict) -> None:
    if not values:
        return
    assignments = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?",
                 (*values.values(), row_id))


def _merge_patients(conn, src, tally) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for row in src.execute("SELECT * FROM patients"):
        local = conn.execute(
            "SELECT * FROM patients WHERE uid = ? OR code = ? COLLATE NOCASE",
            (row["uid"], row["code"])).fetchone()
        if local:
            mapping[row["id"]] = local["id"]
            _update(conn, "patients", local["id"],
                    _blank_fills(local, row, ("diagnosis", "notes")))
        else:
            cur = conn.execute(
                "INSERT INTO patients(uid, code, diagnosis, notes, created_at)"
                " VALUES(?,?,?,?,?)",
                (row["uid"], row["code"], row["diagnosis"], row["notes"], row["created_at"]))
            mapping[row["id"]] = int(cur.lastrowid)
            tally["patients_added"] += 1
    return mapping


def _merge_cell_lines(conn, src, patients, tally) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for row in src.execute("SELECT * FROM cell_lines"):
        local = conn.execute(
            "SELECT * FROM cell_lines WHERE uid = ? OR name = ? COLLATE NOCASE",
            (row["uid"], row["name"])).fetchone()
        patient_id = patients.get(row["patient_id"])
        if local:
            mapping[row["id"]] = local["id"]
            fills = _blank_fills(local, row, ("species", "tissue", "notes"))
            if local["patient_id"] is None and patient_id is not None:
                fills["patient_id"] = patient_id
            _update(conn, "cell_lines", local["id"], fills)
        else:
            cur = conn.execute(
                "INSERT INTO cell_lines(uid, name, patient_id, species, tissue, notes,"
                " created_at) VALUES(?,?,?,?,?,?,?)",
                (row["uid"], row["name"], patient_id, row["species"], row["tissue"],
                 row["notes"], row["created_at"]))
            mapping[row["id"]] = int(cur.lastrowid)
            tally["cell_lines_added"] += 1
    return mapping


def _merge_experiments(conn, src, tally) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for row in src.execute("SELECT * FROM experiments"):
        local = conn.execute(
            "SELECT * FROM experiments WHERE uid = ? OR name = ? COLLATE NOCASE",
            (row["uid"], row["name"])).fetchone()
        if local:
            mapping[row["id"]] = local["id"]
            _update(conn, "experiments", local["id"],
                    _blank_fills(local, row, ("owner", "colour", "notes")))
        else:
            cur = conn.execute(
                "INSERT INTO experiments(uid, name, owner, colour, notes, active, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (row["uid"], row["name"], row["owner"], row["colour"], row["notes"],
                 row["active"], row["created_at"]))
            mapping[row["id"]] = int(cur.lastrowid)
            tally["experiments_added"] += 1
    return mapping


def _merge_plates(conn, src, patients, lines, experiments, tally):
    plate_map: dict[int, int] = {}
    well_map: dict[int, int] = {}
    for plate in src.execute("SELECT * FROM plates"):
        local = conn.execute("SELECT * FROM plates WHERE uid = ?", (plate["uid"],)).fetchone()
        if local is None:
            cur = conn.execute(
                "INSERT INTO plates(uid, name, format, n_rows, n_cols, location, owner,"
                " notes, archived, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (plate["uid"], plate["name"], plate["format"], plate["n_rows"],
                 plate["n_cols"], plate["location"], plate["owner"], plate["notes"],
                 plate["archived"], plate["created_at"], plate["updated_at"]))
            plate_id = int(cur.lastrowid)
            tally["plates_added"] += 1
        else:
            plate_id = local["id"]
            if (plate["updated_at"] or "") > (local["updated_at"] or ""):
                _update(conn, "plates", plate_id, {
                    "name": plate["name"], "location": plate["location"],
                    "owner": plate["owner"], "notes": plate["notes"],
                    "archived": plate["archived"], "updated_at": plate["updated_at"]})
                tally["plates_updated"] += 1
        plate_map[plate["id"]] = plate_id
        _merge_wells(conn, src, plate, plate_id, patients, lines, experiments,
                     well_map, tally)
    return plate_map, well_map


def _merge_wells(conn, src, plate, plate_id, patients, lines, experiments,
                 well_map, tally) -> None:
    for well in src.execute("SELECT * FROM wells WHERE plate_id = ?", (plate["id"],)):
        local = conn.execute("SELECT * FROM wells WHERE uid = ?", (well["uid"],)).fetchone()
        if local is None:
            local = conn.execute(
                "SELECT * FROM wells WHERE plate_id = ? AND row_idx = ? AND col_idx = ?",
                (plate_id, well["row_idx"], well["col_idx"])).fetchone()
        values = {f: well[f] for f in WELL_FIELDS}
        values["cell_line_id"] = lines.get(well["cell_line_id"])
        values["patient_id"] = patients.get(well["patient_id"])
        values["experiment_id"] = experiments.get(well["experiment_id"])
        values["updated_at"] = well["updated_at"]
        if local is None:
            columns = ["uid", "plate_id", "row_idx", "col_idx", *values]
            marks = ",".join("?" * len(columns))
            cur = conn.execute(
                f"INSERT INTO wells({','.join(columns)}) VALUES({marks})",
                (well["uid"], plate_id, well["row_idx"], well["col_idx"], *values.values()))
            well_map[well["id"]] = int(cur.lastrowid)
            tally["wells_added"] += 1
        else:
            well_map[well["id"]] = local["id"]
            if (well["updated_at"] or "") > (local["updated_at"] or ""):
                _update(conn, "wells", local["id"], values)
                tally["wells_updated"] += 1


def _merge_photos(conn, src, bundle_root, well_map, tally) -> None:
    for row in src.execute("SELECT * FROM photos"):
        well_id = well_map.get(row["well_id"])
        if well_id is None:
            continue
        if conn.execute("SELECT 1 FROM photos WHERE uid = ?", (row["uid"],)).fetchone():
            continue
        source = bundle_root / "photos" / row["rel_path"]
        if not source.exists():
            tally["photos_missing"] += 1
            continue
        dest = photos.absolute(row["rel_path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(source, dest)
        conn.execute(
            "INSERT INTO photos(uid, well_id, rel_path, taken_at, caption, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (row["uid"], well_id, row["rel_path"], row["taken_at"], row["caption"],
             row["created_at"]))
        tally["photos_added"] += 1


def _merge_events(conn, src, plate_map, well_map, tally) -> None:
    for row in src.execute("SELECT * FROM events"):
        if conn.execute("SELECT 1 FROM events WHERE uid = ?", (row["uid"],)).fetchone():
            continue
        well_id = well_map.get(row["well_id"]) if row["well_id"] else None
        plate_id = plate_map.get(row["plate_id"]) if row["plate_id"] else None
        if well_id is None and plate_id is None:
            continue
        conn.execute(
            "INSERT INTO events(uid, well_id, plate_id, ts, kind, detail) VALUES(?,?,?,?,?,?)",
            (row["uid"], well_id, plate_id, row["ts"], row["kind"], row["detail"]))
        tally["history_entries_added"] += 1


def replace_from_bundle(bundle: str | Path) -> Path:
    """Swap the local data folder's contents for the bundle's.

    The caller must close its Store first.  Returns the folder the previous
    database and photos were moved to, so nothing is ever simply thrown away.
    """
    manifest = read_manifest(bundle)  # validates the file
    del manifest
    root = paths.ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / "backups" / f"before-import-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    db_file = paths.db_path()
    for suffix in ("", "-wal", "-shm"):
        old = Path(str(db_file) + suffix)
        if old.exists():
            shutil.move(str(old), str(backup / old.name))
    photo_root = paths.photos_dir()
    if photo_root.exists() and any(photo_root.iterdir()):
        shutil.move(str(photo_root), str(backup / "photos"))
    photo_root.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(paths.thumbs_dir(), ignore_errors=True)
    paths.thumbs_dir().mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        extracted = Path(tmp)
        with zipfile.ZipFile(bundle) as zf:
            zf.extractall(extracted)
        shutil.copy2(extracted / DB_ENTRY, db_file)
        src_photos = extracted / "photos"
        if src_photos.exists():
            for item in src_photos.iterdir():
                dest = photo_root / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
    return backup
