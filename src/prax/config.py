"""Paths and settings. Env vars override for deployment (PRAX_DATA_DIR)."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PRAX_DATA_DIR", REPO_ROOT / "data"))
DB_PATH = DATA_DIR / "prax.db"
ARCHIVE_DIR = DATA_DIR / "archive"
SCHEMA_PATH = REPO_ROOT / "schema.sql"
