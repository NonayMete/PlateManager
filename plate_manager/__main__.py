"""Entry point: python -m plate_manager  (or the `plate-manager` command).

Also survives being run as a plain file - `python plate_manager/__main__.py` -
which Python would otherwise reject, because relative imports need a package to
resolve against and a script has none.
"""
from __future__ import annotations

import argparse
import sys
import traceback

if __package__ in (None, ""):            # started as a file, not as a module
    import importlib
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    importlib.import_module("plate_manager")   # the parent the imports below need
    __package__ = "plate_manager"

from . import APP_NAME, APP_ORG, APP_VERSION
from ._deps import require_dependencies

# Qt, platformdirs and Pillow are imported inside main(), after the check below,
# so a machine without them gets an explanation rather than an import traceback.


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="plate-manager", description=APP_NAME)
    parser.add_argument("--data-dir", help="use this folder for the database and photos"
                                           " (this run only)")
    parser.add_argument("--demo", action="store_true",
                        help="add sample plates (only if the data folder is empty)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    return parser.parse_args(argv)


def _install_error_dialog(QMessageBox) -> None:
    """Show unexpected errors instead of vanishing - lab machines rarely have a console."""
    def hook(kind, value, tb) -> None:
        traceback.print_exception(kind, value, tb)
        QMessageBox.critical(None, f"{APP_NAME} — unexpected error",
                             f"{kind.__name__}: {value}\n\n"
                             "The action was cancelled. Your data folder is unchanged.")
    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    require_dependencies()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import db, paths
    from .repo import Store
    from .ui import theme
    from .ui.main_window import MainWindow

    if args.data_dir:
        import os
        os.environ[paths.ENV_VAR] = args.data_dir

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setApplicationVersion(APP_VERSION)
    theme.apply_to(app)                          # same look on Windows and macOS
    app.setQuitOnLastWindowClosed(True)
    _install_error_dialog(QMessageBox)

    paths.ensure_dirs()
    try:
        store = Store(paths.db_path())
    except Exception as exc:                     # unreadable/locked database
        QMessageBox.critical(None, f"{APP_NAME} — cannot open data",
                             f"{paths.db_path()}\n\n{exc}")
        return 1

    version = db.get_meta(store.conn, "schema_version")
    if version and int(version) > db.SCHEMA_VERSION:
        QMessageBox.warning(None, "Newer data file",
                            f"This data folder was written by a newer version of"
                            f" {APP_NAME} (format {version}, this build reads"
                            f" {db.SCHEMA_VERSION}).\n\nUpdate the app before making"
                            " changes, or some details may not be shown.")

    if args.demo:
        if store.list_plates(include_archived=True):
            print("--demo skipped: this data folder already has plates.")
        else:
            from .demo import create_demo_data
            create_demo_data(store)

    config = paths.read_config()
    window = MainWindow(store, config)
    window.show()
    QTimer.singleShot(700, window.check_reminders_on_start)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
