#!/usr/bin/env python3
"""Build a double-clickable app with PyInstaller.

    pip install pyinstaller
    python build_app.py

Produces `dist/Plate Manager.app` on macOS and a `dist/Plate Manager/` folder
containing `Plate Manager.exe` on Windows (copy the whole folder to distribute
it).  Build on the platform you are targeting - PyInstaller does not
cross-compile.  The app keeps no data inside itself, so upgrading it is just
replacing what is in dist/.
"""
from __future__ import annotations

import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

from plate_manager import APP_NAME

ROOT = Path(__file__).resolve().parent
# PyInstaller runs its entry as a top-level script, so it has to be the
# launcher (absolute imports), not the package's __main__.py, whose relative
# imports need a parent package.
ENTRY = ROOT / "run.py"


def main() -> int:
    if find_spec("PyInstaller") is None:
        print(f"PyInstaller is not installed for {sys.executable}.\n"
              f"Run:  {sys.executable} -m pip install pyinstaller")
        return 1

    command = [
        # this interpreter, so the build uses this environment's packages
        # rather than whichever pyinstaller happens to be first on PATH
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",                     # no console window
        "--name", APP_NAME,
        "--osx-bundle-identifier", "com.arthrolase.platemanager",
        # Qt modules we do not use; leaving them out keeps the build much smaller
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtCharts",
        "--exclude-module", "tkinter",
        str(ENTRY),
    ]
    print(" ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode == 0:
        built = f"{APP_NAME}.app" if sys.platform == "darwin" else APP_NAME
        print(f"\nBuilt: {ROOT / 'dist' / built}")
        if sys.platform == "darwin":
            print(f"macOS will not open {APP_NAME}.app until you right-click it"
                  " and choose Open - the build is unsigned.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
