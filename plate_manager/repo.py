"""Data access.

One `Store` wraps the SQLite connection; the UI never writes SQL itself.
Text fields the user types (cell line, patient, experiment) are resolved to
lookup rows here, creating the lookup on first use - that is what makes the
autocomplete lists grow by themselves as the lab works.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Sequence

from . import db
from .models import now_iso, well_label

WELL_COLUMNS = (
    "status", "passage", "treatment", "medium", "seeded_on",
    "confluence", "due_date", "due_task", "notes",
)
# Virtual fields: the user types a name, we resolve/create the lookup row.
LOOKUP_FIELDS = ("cell_line", "patient_code", "experiment")
EDITABLE_FIELDS = WELL_COLUMNS + LOOKUP_FIELDS

FIELD_LABELS = {
    "cell_line": "Cell line",
    "patient_code": "Patient",
    "experiment": "Experiment",
    "status": "Status",
    "passage": "Passage",
    "treatment": "Treatment",
    "medium": "Medium",
    "seeded_on": "Seeded",
    "confluence": "Confluence",
    "due_date": "Due",
    "due_task": "Task",
    "notes": "Notes",
}

WELL_SELECT = """
SELECT w.*,
       c.name   AS cell_line,
       p.code   AS patient_code,
       e.name   AS experiment,
       e.colour AS experiment_colour,
       (SELECT COUNT(*) FROM photos ph WHERE ph.well_id = w.id) AS photo_count
FROM wells w
LEFT JOIN cell_lines  c ON c.id = w.cell_line_id
LEFT JOIN patients    p ON p.id = w.patient_id
LEFT JOIN experiments e ON e.id = w.experiment_id
"""


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def well_is_empty(well: dict) -> bool:
    """True when nothing has been recorded in the well."""
    if well.get("photo_count"):
        return False
    for key in EDITABLE_FIELDS:
        if well.get(key) not in (None, ""):
            return False
    return True


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn = db.connect(self.path)
        self._closed = False

    def close(self) -> None:
        """Commit and close; safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        try:
            self.conn.commit()
        finally:
            self.conn.close()

    # ------------------------------------------------------------------ plates
    def list_plates(self, include_archived: bool = False) -> list[dict]:
        where = "" if include_archived else "WHERE p.archived = 0"
        sql = f"""
        SELECT p.*,
          (SELECT COUNT(*) FROM wells w WHERE w.plate_id = p.id
             AND (w.cell_line_id IS NOT NULL OR w.status <> ''
                  OR w.notes <> '' OR w.experiment_id IS NOT NULL
                  OR w.patient_id IS NOT NULL OR w.treatment <> '')) AS used_wells,
          (SELECT COUNT(*) FROM wells w WHERE w.plate_id = p.id
             AND w.due_date <> ''
             AND date(w.due_date) <= date('now','localtime')) AS overdue,
          (SELECT COUNT(*) FROM photos ph JOIN wells w ON w.id = ph.well_id
             WHERE w.plate_id = p.id) AS photo_count
        FROM plates p
        {where}
        ORDER BY p.archived, p.created_at DESC
        """
        return _rows(self.conn.execute(sql))

    def get_plate(self, plate_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM plates WHERE id = ?", (plate_id,)).fetchone()
        return dict(row) if row else None

    def create_plate(self, name: str, fmt: str, n_rows: int, n_cols: int,
                     location: str = "", owner: str = "", notes: str = "") -> int:
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO plates(uid, name, format, n_rows, n_cols, location, owner, notes,"
            " archived, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,0,?,?)",
            (db.new_uid(), name.strip() or "Untitled plate", fmt, n_rows, n_cols,
             location.strip(), owner.strip(), notes.strip(), ts, ts),
        )
        plate_id = int(cur.lastrowid)
        self.conn.executemany(
            "INSERT INTO wells(uid, plate_id, row_idx, col_idx, updated_at) VALUES(?,?,?,?,?)",
            [(db.new_uid(), plate_id, r, c, ts)
             for r in range(n_rows) for c in range(n_cols)],
        )
        self._log(None, plate_id, "plate", f"Created {fmt} plate {name!r}")
        self.conn.commit()
        return plate_id

    def update_plate(self, plate_id: int, **fields: Any) -> None:
        allowed = ("name", "format", "location", "owner", "notes", "archived")
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        sets["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(f"UPDATE plates SET {assignments} WHERE id = ?",
                          (*sets.values(), plate_id))
        self.conn.commit()

    def delete_plate(self, plate_id: int) -> list[str]:
        """Delete a plate with its wells/photos; returns removed photo rel_paths."""
        paths = [r["rel_path"] for r in self.conn.execute(
            "SELECT ph.rel_path FROM photos ph JOIN wells w ON w.id = ph.well_id"
            " WHERE w.plate_id = ?", (plate_id,))]
        self.conn.execute("DELETE FROM plates WHERE id = ?", (plate_id,))
        self.conn.commit()
        return paths

    def duplicate_plate(self, plate_id: int, new_name: str,
                        with_contents: bool = True) -> int:
        """Copy a plate's layout (and optionally its well contents) into a new plate.

        Photos, due dates and history stay with the original - a new plate is a
        fresh passage, not a copy of last week's observations.
        """
        src = self.get_plate(plate_id)
        if src is None:
            raise ValueError("plate not found")
        new_id = self.create_plate(new_name, src["format"], src["n_rows"], src["n_cols"],
                                   src["location"], src["owner"], src["notes"])
        if with_contents:
            carried = ("cell_line_id", "patient_id", "experiment_id",
                       "passage", "treatment", "medium", "notes")
            src_wells = {(w["row_idx"], w["col_idx"]): w for w in self.wells_for_plate(plate_id)}
            ts = now_iso()
            for well in self.wells_for_plate(new_id):
                origin = src_wells.get((well["row_idx"], well["col_idx"]))
                if origin is None:
                    continue
                assignments = ", ".join(f"{k} = ?" for k in carried)
                self.conn.execute(
                    f"UPDATE wells SET {assignments}, updated_at = ? WHERE id = ?",
                    (*(origin[k] for k in carried), ts, well["id"]))
            self.conn.commit()
        return new_id

    # ------------------------------------------------------------------- wells
    def wells_for_plate(self, plate_id: int) -> list[dict]:
        return _rows(self.conn.execute(
            WELL_SELECT + " WHERE w.plate_id = ? ORDER BY w.row_idx, w.col_idx", (plate_id,)))

    def get_well(self, well_id: int) -> dict | None:
        row = self.conn.execute(WELL_SELECT + " WHERE w.id = ?", (well_id,)).fetchone()
        return dict(row) if row else None

    def get_wells(self, well_ids: Sequence[int]) -> list[dict]:
        if not well_ids:
            return []
        marks = ",".join("?" * len(well_ids))
        return _rows(self.conn.execute(
            WELL_SELECT + f" WHERE w.id IN ({marks}) ORDER BY w.row_idx, w.col_idx",
            tuple(well_ids)))

    def update_wells(self, well_ids: Sequence[int], changes: dict[str, Any]) -> None:
        """Apply `changes` to every listed well.

        `changes` may hold any of EDITABLE_FIELDS; '' clears a field.  The
        per-well history records what actually changed.
        """
        if not well_ids or not changes:
            return
        before = {w["id"]: w for w in self.get_wells(well_ids)}
        sets: dict[str, Any] = {}

        if "cell_line" in changes:
            name = (changes["cell_line"] or "").strip()
            # naming a line and a patient in one edit links the two for next time
            patient_hint = (changes.get("patient_code") or "").strip()
            sets["cell_line_id"] = (self.ensure_cell_line(name, patient_hint)
                                    if name else None)
        if "patient_code" in changes:
            code = (changes["patient_code"] or "").strip()
            sets["patient_id"] = self.ensure_patient(code) if code else None
        if "experiment" in changes:
            name = (changes["experiment"] or "").strip()
            sets["experiment_id"] = self.ensure_experiment(name) if name else None
        for key in WELL_COLUMNS:
            if key in changes:
                value = changes[key]
                if key == "confluence":
                    sets[key] = None if value in ("", None) else int(value)
                else:
                    sets[key] = "" if value is None else str(value).strip()

        # Naming a cell line that belongs to a patient fills the patient in too,
        # unless the same edit set the patient explicitly.
        if sets.get("cell_line_id") and "patient_code" not in changes:
            row = self.conn.execute("SELECT patient_id FROM cell_lines WHERE id = ?",
                                    (sets["cell_line_id"],)).fetchone()
            if row and row["patient_id"]:
                sets["patient_id"] = row["patient_id"]

        if not sets:
            return
        sets["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in sets)
        marks = ",".join("?" * len(well_ids))
        self.conn.execute(f"UPDATE wells SET {assignments} WHERE id IN ({marks})",
                          (*sets.values(), *well_ids))

        after = {w["id"]: w for w in self.get_wells(well_ids)}
        for well_id in well_ids:
            diff = self._describe_diff(before.get(well_id, {}), after.get(well_id, {}))
            if diff:
                self._log(well_id, after[well_id]["plate_id"], "edit", diff)
        self._touch_plates(well_ids)
        self.conn.commit()

    def clear_wells(self, well_ids: Sequence[int]) -> list[str]:
        """Wipe well contents and their photos; returns removed photo paths."""
        if not well_ids:
            return []
        marks = ",".join("?" * len(well_ids))
        paths = [r["rel_path"] for r in self.conn.execute(
            f"SELECT rel_path FROM photos WHERE well_id IN ({marks})", tuple(well_ids))]
        self.conn.execute(f"DELETE FROM photos WHERE well_id IN ({marks})", tuple(well_ids))
        self.conn.execute(
            f"""UPDATE wells SET cell_line_id = NULL, patient_id = NULL, experiment_id = NULL,
                status = '', passage = '', treatment = '', medium = '', seeded_on = '',
                confluence = NULL, due_date = '', due_task = '', notes = '', updated_at = ?
                WHERE id IN ({marks})""", (now_iso(), *well_ids))
        for well_id in well_ids:
            self._log(well_id, None, "edit", "Well cleared")
        self._touch_plates(well_ids)
        self.conn.commit()
        return paths

    def copy_well_contents(self, source_id: int, target_ids: Sequence[int]) -> None:
        src = self.get_well(source_id)
        if not src:
            return
        targets = [t for t in target_ids if t != source_id]
        self.update_wells(targets, {k: src.get(k) for k in EDITABLE_FIELDS})

    def _touch_plates(self, well_ids: Sequence[int]) -> None:
        marks = ",".join("?" * len(well_ids))
        self.conn.execute(
            f"UPDATE plates SET updated_at = ? WHERE id IN "
            f"(SELECT plate_id FROM wells WHERE id IN ({marks}))", (now_iso(), *well_ids))

    def _describe_diff(self, before: dict, after: dict) -> str:
        parts = []
        for key in EDITABLE_FIELDS:
            old, new = before.get(key) or "", after.get(key) or ""
            if str(old) != str(new):
                parts.append(f"{FIELD_LABELS.get(key, key)}: {old or '-'} -> {new or '-'}")
        return "; ".join(parts)

    def search_wells(self, text: str, limit: int = 300) -> list[dict]:
        if not text.strip():
            return []
        term = f"%{text.strip()}%"
        rows = _rows(self.conn.execute(
            WELL_SELECT + """
            WHERE (c.name LIKE ? OR p.code LIKE ? OR e.name LIKE ? OR w.notes LIKE ?
                   OR w.treatment LIKE ? OR w.passage LIKE ?
                   OR w.plate_id IN (SELECT id FROM plates WHERE name LIKE ?))
            ORDER BY w.plate_id, w.row_idx, w.col_idx LIMIT ?""",
            (term, term, term, term, term, term, term, limit)))
        for row in rows:
            row["plate_name"] = self.plate_name(row["plate_id"])
        return rows

    def due_wells(self, within_days: int = 7) -> list[dict]:
        rows = _rows(self.conn.execute(
            WELL_SELECT + """
            WHERE w.due_date <> ''
              AND date(w.due_date) <= date('now', 'localtime', ?)
              AND w.plate_id IN (SELECT id FROM plates WHERE archived = 0)
            ORDER BY date(w.due_date), w.plate_id, w.row_idx, w.col_idx""",
            (f"{int(within_days):+d} days",)))
        for row in rows:
            row["plate_name"] = self.plate_name(row["plate_id"])
        return rows

    def plate_name(self, plate_id: int) -> str:
        row = self.conn.execute("SELECT name FROM plates WHERE id = ?", (plate_id,)).fetchone()
        return row["name"] if row else ""

    # ------------------------------------------------------------------ photos
    def photos_for_well(self, well_id: int) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM photos WHERE well_id = ?"
            " ORDER BY CASE WHEN taken_at = '' THEN 1 ELSE 0 END, taken_at, created_at",
            (well_id,)))

    def add_photo(self, well_id: int, rel_path: str, taken_at: str = "",
                  caption: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO photos(uid, well_id, rel_path, taken_at, caption, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (db.new_uid(), well_id, rel_path, taken_at, caption, now_iso()))
        suffix = f" ({taken_at})" if taken_at else ""
        self._log(well_id, None, "photo", f"Photo added{suffix}")
        self.conn.commit()
        return int(cur.lastrowid)

    def update_photo(self, photo_id: int, taken_at: str | None = None,
                     caption: str | None = None) -> None:
        sets: dict[str, Any] = {}
        if taken_at is not None:
            sets["taken_at"] = taken_at.strip()
        if caption is not None:
            sets["caption"] = caption.strip()
        if not sets:
            return
        assignments = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(f"UPDATE photos SET {assignments} WHERE id = ?",
                          (*sets.values(), photo_id))
        self.conn.commit()

    def delete_photo(self, photo_id: int) -> str | None:
        row = self.conn.execute("SELECT rel_path, well_id FROM photos WHERE id = ?",
                                (photo_id,)).fetchone()
        if row is None:
            return None
        self.conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        self._log(row["well_id"], None, "photo", "Photo removed")
        self.conn.commit()
        return row["rel_path"]

    def all_photo_paths(self) -> list[str]:
        return [r["rel_path"] for r in self.conn.execute("SELECT rel_path FROM photos")]

    # ------------------------------------------------------------------ events
    def _log(self, well_id: int | None, plate_id: int | None, kind: str, detail: str) -> None:
        if plate_id is None and well_id is not None:
            row = self.conn.execute("SELECT plate_id FROM wells WHERE id = ?",
                                    (well_id,)).fetchone()
            plate_id = row["plate_id"] if row else None
        self.conn.execute(
            "INSERT INTO events(uid, well_id, plate_id, ts, kind, detail) VALUES(?,?,?,?,?,?)",
            (db.new_uid(), well_id, plate_id, now_iso(), kind, detail))

    def events_for_well(self, well_id: int, limit: int = 60) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM events WHERE well_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (well_id, limit)))

    def recent_events(self, limit: int = 150) -> list[dict]:
        rows = _rows(self.conn.execute(
            "SELECT e.*, p.name AS plate_name, w.row_idx, w.col_idx FROM events e"
            " LEFT JOIN plates p ON p.id = e.plate_id"
            " LEFT JOIN wells w ON w.id = e.well_id"
            " ORDER BY e.ts DESC, e.id DESC LIMIT ?", (limit,)))
        for row in rows:
            row["well"] = ("" if row["row_idx"] is None
                           else well_label(row["row_idx"], row["col_idx"]))
        return rows

    def log_note(self, well_id: int, text: str) -> None:
        self._log(well_id, None, "note", text.strip())
        self.conn.commit()

    # ----------------------------------------------------------------- lookups
    def ensure_patient(self, code: str) -> int:
        code = code.strip()
        row = self.conn.execute("SELECT id FROM patients WHERE code = ? COLLATE NOCASE",
                                (code,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute("INSERT INTO patients(uid, code, created_at) VALUES(?,?,?)",
                                (db.new_uid(), code, now_iso()))
        return int(cur.lastrowid)

    def ensure_cell_line(self, name: str, patient_code: str = "") -> int:
        name = name.strip()
        row = self.conn.execute(
            "SELECT id, patient_id FROM cell_lines WHERE name = ? COLLATE NOCASE",
            (name,)).fetchone()
        if row:
            # fill in a missing patient link, but never silently re-point one
            if patient_code.strip() and row["patient_id"] is None:
                self.conn.execute("UPDATE cell_lines SET patient_id = ? WHERE id = ?",
                                  (self.ensure_patient(patient_code), row["id"]))
            return row["id"]
        patient_id = self.ensure_patient(patient_code) if patient_code.strip() else None
        cur = self.conn.execute(
            "INSERT INTO cell_lines(uid, name, patient_id, created_at) VALUES(?,?,?,?)",
            (db.new_uid(), name, patient_id, now_iso()))
        return int(cur.lastrowid)

    def ensure_experiment(self, name: str) -> int:
        name = name.strip()
        row = self.conn.execute("SELECT id FROM experiments WHERE name = ? COLLATE NOCASE",
                                (name,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute("INSERT INTO experiments(uid, name, created_at) VALUES(?,?,?)",
                                (db.new_uid(), name, now_iso()))
        return int(cur.lastrowid)

    def list_patients(self) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT p.*,"
            " (SELECT COUNT(*) FROM cell_lines c WHERE c.patient_id = p.id) AS n_lines,"
            " (SELECT COUNT(*) FROM wells w WHERE w.patient_id = p.id) AS n_wells"
            " FROM patients p ORDER BY p.code COLLATE NOCASE"))

    def list_cell_lines(self) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT c.*, p.code AS patient_code,"
            " (SELECT COUNT(*) FROM wells w WHERE w.cell_line_id = c.id) AS n_wells"
            " FROM cell_lines c LEFT JOIN patients p ON p.id = c.patient_id"
            " ORDER BY c.name COLLATE NOCASE"))

    def list_experiments(self) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT e.*, (SELECT COUNT(*) FROM wells w WHERE w.experiment_id = e.id) AS n_wells"
            " FROM experiments e ORDER BY e.active DESC, e.name COLLATE NOCASE"))

    LOOKUP_TABLES = {
        "patients": ("code", "diagnosis", "notes"),
        "cell_lines": ("name", "species", "tissue", "notes", "patient_id"),
        "experiments": ("name", "owner", "colour", "notes", "active"),
    }

    def update_lookup(self, table: str, row_id: int, **fields: Any) -> None:
        allowed = self.LOOKUP_TABLES[table]
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        assignments = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?",
                          (*sets.values(), row_id))
        self.conn.commit()

    def delete_lookup(self, table: str, row_id: int) -> None:
        if table not in self.LOOKUP_TABLES:
            raise ValueError(table)
        self.conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        self.conn.commit()

    def set_cell_line_patient(self, cell_line_id: int, patient_code: str) -> None:
        patient_id = self.ensure_patient(patient_code) if patient_code.strip() else None
        self.conn.execute("UPDATE cell_lines SET patient_id = ? WHERE id = ?",
                          (patient_id, cell_line_id))
        self.conn.commit()

    # ------------------------------------------------------- autocomplete feed
    SUGGESTION_SQL = {
        "cell_line": "SELECT name FROM cell_lines ORDER BY name COLLATE NOCASE",
        "patient_code": "SELECT code FROM patients ORDER BY code COLLATE NOCASE",
        "experiment": "SELECT name FROM experiments ORDER BY active DESC, name COLLATE NOCASE",
        "treatment": "SELECT DISTINCT treatment FROM wells WHERE treatment <> ''"
                     " ORDER BY treatment COLLATE NOCASE",
        "medium": "SELECT DISTINCT medium FROM wells WHERE medium <> ''"
                  " ORDER BY medium COLLATE NOCASE",
        "passage": "SELECT DISTINCT passage FROM wells WHERE passage <> ''"
                   " ORDER BY passage COLLATE NOCASE",
        "due_task": "SELECT DISTINCT due_task FROM wells WHERE due_task <> ''"
                    " ORDER BY due_task COLLATE NOCASE",
        "location": "SELECT DISTINCT location FROM plates WHERE location <> ''"
                    " ORDER BY location COLLATE NOCASE",
        "owner": "SELECT DISTINCT owner FROM plates WHERE owner <> ''"
                 " ORDER BY owner COLLATE NOCASE",
        "species": "SELECT DISTINCT species FROM cell_lines WHERE species <> ''"
                   " ORDER BY species COLLATE NOCASE",
        "tissue": "SELECT DISTINCT tissue FROM cell_lines WHERE tissue <> ''"
                  " ORDER BY tissue COLLATE NOCASE",
    }

    def suggestions(self, field: str) -> list[str]:
        """Every value already used for a field, for type-ahead."""
        sql = self.SUGGESTION_SQL.get(field)
        if sql is None:
            return []
        return [r[0] for r in self.conn.execute(sql) if r[0]]

    def stats(self) -> dict:
        def count(sql: str) -> int:
            return int(self.conn.execute(sql).fetchone()[0])
        return {
            "plates": count("SELECT COUNT(*) FROM plates WHERE archived = 0"),
            "archived": count("SELECT COUNT(*) FROM plates WHERE archived = 1"),
            "cell_lines": count("SELECT COUNT(*) FROM cell_lines"),
            "patients": count("SELECT COUNT(*) FROM patients"),
            "photos": count("SELECT COUNT(*) FROM photos"),
            "overdue": count("SELECT COUNT(*) FROM wells w JOIN plates p ON p.id = w.plate_id"
                             " WHERE p.archived = 0 AND w.due_date <> ''"
                             " AND date(w.due_date) < date('now','localtime')"),
            "due_today": count("SELECT COUNT(*) FROM wells w JOIN plates p ON p.id = w.plate_id"
                               " WHERE p.archived = 0 AND w.due_date <> ''"
                               " AND date(w.due_date) = date('now','localtime')"),
        }
