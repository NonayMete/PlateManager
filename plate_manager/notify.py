"""Due-date reminders: a tray/menu-bar icon, a notification on opening, and a
periodic re-check while the app is left running.

Notifications are deliberately quiet - one summary, not one per well - and a
well is only announced once a day.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import APP_NAME
from .models import days_until, today_iso, well_label
from .repo import Store
from .ui import theme


class Reminders(QObject):
    """Owns the tray icon and decides when to speak up."""

    openRequested = Signal()
    checkRequested = Signal()

    def __init__(self, store: Store, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self.tray: QSystemTrayIcon | None = None
        self._announced: set[tuple[str, int]] = set()   # (date, well id)
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.check(quiet_if_none=True))

    # ------------------------------------------------------------------ setup
    def set_store(self, store: Store) -> None:
        self.store = store
        self._announced.clear()

    def enable_tray(self, enabled: bool) -> None:
        if enabled and self.tray is None and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(theme.app_icon(), self)
            self.tray.setToolTip(APP_NAME)
            menu = QMenu()
            menu.addAction(f"Open {APP_NAME}", self.openRequested.emit)
            menu.addAction("Check due wells now", lambda: self.check(force=True))
            self.tray.setContextMenu(menu)
            self._menu = menu           # keep a reference alive
            self.tray.activated.connect(self._on_activated)
            self.tray.show()
        elif not enabled and self.tray is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None

    def set_interval_hours(self, hours: float) -> None:
        if hours and hours > 0:
            self._timer.start(int(hours * 3600 * 1000))
        else:
            self._timer.stop()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.openRequested.emit()

    # ------------------------------------------------------------------ checks
    def pending(self, within_days: int = 3) -> list[dict]:
        return self.store.due_wells(within_days)

    def summary_text(self, within_days: int = 3) -> str:
        rows = self.pending(within_days)
        if not rows:
            return ""
        overdue = [w for w in rows if (days_until(w["due_date"]) or 0) < 0]
        today = [w for w in rows if days_until(w["due_date"]) == 0]
        soon = [w for w in rows if (days_until(w["due_date"]) or 0) > 0]
        parts = []
        if overdue:
            parts.append(f"{len(overdue)} overdue")
        if today:
            parts.append(f"{len(today)} due today")
        if soon:
            parts.append(f"{len(soon)} coming up")
        lines = [", ".join(parts)]
        for well in (overdue + today + soon)[:6]:
            lines.append(f"• {well['plate_name']} "
                         f"{well_label(well['row_idx'], well['col_idx'])}"
                         f" — {well.get('due_task') or 'check'} ({well['due_date']})")
        if len(rows) > 6:
            lines.append(f"…and {len(rows) - 6} more")
        return "\n".join(lines)

    def check(self, within_days: int = 3, force: bool = False,
              quiet_if_none: bool = False) -> list[dict]:
        """Notify about wells that are due; returns the rows considered."""
        rows = self.pending(within_days)
        stamp = today_iso()
        fresh = [w for w in rows if (stamp, w["id"]) not in self._announced]
        if not rows:
            if force and not quiet_if_none:
                self._show("Nothing due", "No wells need attention right now.")
            return rows
        if fresh or force:
            self._announced.update((stamp, w["id"]) for w in rows)
            title = f"{len(rows)} well{'s' if len(rows) != 1 else ''} need attention"
            self._show(title, self.summary_text(within_days))
        return rows

    def _show(self, title: str, message: str) -> None:
        if self.tray is not None and self.tray.isVisible():
            self.tray.showMessage(title, message, theme.app_icon(), 12000)
