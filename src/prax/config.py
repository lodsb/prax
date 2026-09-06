"""Paths and settings.

``PRAX_DATA_DIR`` overrides the data directory. It is read at call time, not
import time, so tests can point every module at a temporary directory by
setting the variable before the first store call.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schema.sql"


def data_dir() -> Path:
    return Path(os.environ.get("PRAX_DATA_DIR", REPO_ROOT / "data"))


def db_path() -> Path:
    return data_dir() / "prax.db"


def archive_dir() -> Path:
    return data_dir() / "archive"
