"""Check the third-party packages are there before anything imports them.

A traceback saying "No module named PySide6" is not much help on a lab machine
that has never had the app installed, so the checks below print the commands
that actually fix it, for the platform and interpreter in use.
"""
from __future__ import annotations

import sys
from importlib.util import find_spec
from pathlib import Path

MIN_PYTHON = (3, 10)

# import name -> what to install
REQUIRED = {
    "PySide6": "PySide6>=6.8",
    "platformdirs": "platformdirs>=4.0",
    "PIL": "Pillow>=10.0",
}


def missing_packages() -> list[str]:
    return [name for name in REQUIRED if find_spec(name) is None]


def _activate_line() -> str:
    if sys.platform == "win32":
        return r".venv\Scripts\activate"
    return "source .venv/bin/activate"


def _instructions(missing: list[str]) -> str:
    root = Path(__file__).resolve().parent.parent
    python = "py" if sys.platform == "win32" else "python3"
    names = ", ".join(missing)
    return f"""
Plate Manager needs {names}, which this Python does not have:

    {sys.executable}
    Python {sys.version.split()[0]}

From {root}, set up a virtual environment and install what it needs:

    {python} -m venv .venv
    {_activate_line()}
    pip install -r requirements.txt
    {python} run.py

Notes
  * Run the app with the same Python you installed into - inside the activated
    .venv, plain `python run.py` is that Python.
  * If pip says "externally-managed-environment", you are installing outside a
    virtual environment: create the .venv above first.
  * Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required. On macOS, `python` may be an old
    system build - use `python3`, or install a current one from python.org or
    with `brew install python`.
"""


def require_dependencies() -> None:
    """Exit with a readable explanation if the app cannot run here."""
    if sys.version_info < MIN_PYTHON:
        required = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
        running = sys.version.split()[0]
        print(f"\nPlate Manager needs Python {required} or newer; this is Python"
              f" {running} at\n    {sys.executable}\n", file=sys.stderr)
        raise SystemExit(1)
    missing = missing_packages()
    if missing:
        print(_instructions(missing), file=sys.stderr)
        raise SystemExit(1)
