#!/usr/bin/env python3
"""Launcher so the app can be started with `python run.py` from a checkout."""
from plate_manager.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
