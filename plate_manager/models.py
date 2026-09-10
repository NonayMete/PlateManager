"""Small value helpers shared by the store and the UI."""
from __future__ import annotations

from datetime import date, datetime

# name -> (rows, cols)
PLATE_FORMATS: dict[str, tuple[int, int]] = {
    "Dish / flask": (1, 1),
    "4-well": (1, 4),
    "6-well": (2, 3),
    "12-well": (3, 4),
    "24-well": (4, 6),
    "48-well": (6, 8),
    "96-well": (8, 12),
    "384-well": (16, 24),
}

DEFAULT_FORMAT = "96-well"

# Ordered; the first entry means "nothing recorded".
STATUSES: list[str] = [
    "",
    "Seeded",
    "Growing",
    "Confluent",
    "Treated",
    "Passaged",
    "Fixed / stained",
    "Frozen",
    "Contaminated",
    "Discarded",
]

DUE_TASKS = [
    "Check confluence",
    "Change medium",
    "Passage",
    "Treat",
    "Image",
    "Harvest",
    "Fix / stain",
    "Freeze",
]


def now_iso() -> str:
    # milliseconds, so two edits in the same second still order correctly when
    # bundles from two computers are merged
    return datetime.now().isoformat(sep=" ", timespec="milliseconds")


def today_iso() -> str:
    return date.today().isoformat()


def row_letters(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA (for 384-well plates and beyond)."""
    label = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


def well_label(row_idx: int, col_idx: int) -> str:
    return f"{row_letters(row_idx)}{col_idx + 1}"


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def days_until(value: str | None) -> int | None:
    d = parse_date(value)
    return None if d is None else (d - date.today()).days


def pretty_due(value: str | None) -> str:
    """'2026-09-12 (in 2 days)' / '... (overdue by 3 days)' / '... (today)'."""
    days = days_until(value)
    if days is None:
        return ""
    if days == 0:
        return f"{value} (today)"
    if days < 0:
        n = -days
        return f"{value} (overdue by {n} day{'s' if n != 1 else ''})"
    return f"{value} (in {days} day{'s' if days != 1 else ''})"
