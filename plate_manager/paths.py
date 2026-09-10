"""Where Plate Manager keeps its files.

Everything lives under one data folder so it can be moved, backed up or put in a
synced folder (Dropbox/OneDrive) to share between computers:

    <data_dir>/plates.db          the database
    <data_dir>/photos/...         imported well photos (originals)
    <data_dir>/thumbs/...         generated thumbnails (safe to delete)

The folder is chosen in this order:
    1. $PLATE_MANAGER_HOME
    2. "data_dir" in the config file (set via Settings in the app)
    3. the OS default per-user data directory
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from . import APP_NAME, APP_ORG

ENV_VAR = "PLATE_MANAGER_HOME"
DB_NAME = "plates.db"


def config_file() -> Path:
    return Path(user_config_dir(APP_NAME, APP_ORG)) / "config.json"


def read_config() -> dict:
    path = config_file()
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def write_config(cfg: dict) -> None:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), "utf-8")


def default_data_dir() -> Path:
    return Path(user_data_dir(APP_NAME, APP_ORG))


def data_dir() -> Path:
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    configured = read_config().get("data_dir")
    if configured:
        return Path(configured).expanduser()
    return default_data_dir()


def set_data_dir(path: str | os.PathLike[str] | None) -> None:
    """Persist the data folder choice (None restores the OS default)."""
    cfg = read_config()
    if path is None:
        cfg.pop("data_dir", None)
    else:
        cfg["data_dir"] = str(Path(path).expanduser())
    write_config(cfg)


def db_path() -> Path:
    return data_dir() / DB_NAME


def photos_dir() -> Path:
    return data_dir() / "photos"


def thumbs_dir() -> Path:
    return data_dir() / "thumbs"


def ensure_dirs() -> Path:
    root = data_dir()
    for d in (root, photos_dir(), thumbs_dir()):
        d.mkdir(parents=True, exist_ok=True)
    return root
