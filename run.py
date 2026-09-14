#!/usr/bin/env python3
"""Launcher so the app can be started with `python run.py` from a checkout.

Deliberately plain: no imports beyond the standard library until the
dependency check has run, so a machine missing them gets advice, not a
traceback.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plate_manager._deps import require_dependencies  # noqa: E402

if __name__ == "__main__":
    require_dependencies()
    from plate_manager.__main__ import main
    raise SystemExit(main())
