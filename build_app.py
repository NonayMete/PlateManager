#!/usr/bin/env python3
"""Build a double-clickable app with PyInstaller.

    pip install pyinstaller
    python build_app.py

Produces `dist/Plate Manager.exe` on Windows and `dist/Plate Manager.app` on
macOS.  Build on the platform you are targeting - PyInstaller does not
cross-compile.  The app keeps no data inside itself, so upgrading is just
replacing the file.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from plate_manager import APP_NAME

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "plate_manager" / "__main__.py"


def main() -> int:
    if shutil.which("pyinstaller") is None:
        print("PyInstaller is not installed. Run:  pip install pyinstaller")
        return 1

    command = [
        "pyinstaller",
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
        print(f"\nBuilt into {ROOT / 'dist'}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
