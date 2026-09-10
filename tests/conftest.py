"""Test fixtures: every test gets its own data folder, so nothing touches the
real one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plate_manager import paths          # noqa: E402
from plate_manager.repo import Store     # noqa: E402


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "data"
    monkeypatch.setenv(paths.ENV_VAR, str(root))
    paths.ensure_dirs()
    return root


@pytest.fixture()
def store(data_dir) -> Store:
    store = Store(paths.db_path())
    yield store
    store.close()


@pytest.fixture()
def plate(store) -> int:
    return store.create_plate("Test plate", "96-well", 8, 12, "Incubator 2", "JL")
