"""Paths and settings.

``PRAX_DATA_DIR`` says where the store is; it is read at call time, not
import time, so tests point every module at a temporary directory by
setting the variable before the first store call. Everything else a host
chooses lives in ``prax.yaml`` in that directory (``PRAX_CONFIG`` names
another file): which model does which step (``prax.models``), which
modules a document is read against (``domains``), and the sections read
here — the embedder, the vector index, the reranker, parsing, citations
and the door itself.

A setting may still be given as an environment variable for one run
(``PRAX_EMBED=hash pytest``); the variable wins over the file. What
belongs in the environment and nowhere else: ``PRAX_DATA_DIR``,
``PRAX_CONFIG``, ``PRAX_TOKEN`` (a secret), ``PRAX_DOOR`` (which door a
client talks to), and the per-run switches ``PRAX_<STEP>``,
``PRAX_OFFLINE`` and ``PRAX_DEBUG``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
ONTOLOGY_PATH = REPO_ROOT / "ontology"  # a directory of module files
CONFIG_NAME = "prax.yaml"
SECTIONS = (
    "models",  # named models (prax.models)
    "steps",  # which model does which step (prax.models)
    "domains",  # rules giving documents their ontology modules
    "embeddings",  # model, variant, providers, threads
    "vectors",  # dtype, ef
    "rerank",  # model, variant, providers
    "parse",  # ocr_max_pages, max_layout_mb, max_layout_pages
    "citations",  # mailto
    "door",  # cors_origins, inbox_scan_seconds
    "ontology",  # dir
    "paths",  # models (where fetched model files go)
)


class ConfigError(ValueError):
    """prax.yaml says something the code cannot follow."""


def data_dir() -> Path:
    return Path(os.environ.get("PRAX_DATA_DIR", REPO_ROOT / "data"))


def db_path() -> Path:
    return data_dir() / "prax.db"


def archive_dir() -> Path:
    return data_dir() / "archive"


def config_path() -> Path:
    return Path(os.environ.get("PRAX_CONFIG") or data_dir() / CONFIG_NAME)


def document() -> dict[str, Any]:
    """``prax.yaml`` parsed, ``{}`` when there is none. Read on every call:
    the file is a page long, and a person editing it expects the next pass
    to see the change."""
    path = config_path()
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top")
    unknown = [k for k in data if k not in SECTIONS]
    if unknown:
        raise ConfigError(
            f"{path}: unknown section{'s' if len(unknown) > 1 else ''}"
            f" {', '.join(sorted(unknown))}; known: {', '.join(SECTIONS)}"
        )
    return data


def setting(dotted: str, env: str | None = None, default: Any = None) -> Any:
    """One setting: the environment variable when it is set (one run),
    then ``prax.yaml`` by dotted path (``embeddings.variant``), then the
    default."""
    if env:
        raw = os.environ.get(env)
        if raw not in (None, ""):
            return raw
    node: Any = document()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node


def number(dotted: str, env: str | None = None, default: float = 0.0) -> float:
    value = setting(dotted, env, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{dotted}: {value!r} is not a number") from exc


def whole(dotted: str, env: str | None = None, default: int = 0) -> int:
    return int(number(dotted, env, default))


def words(
    dotted: str, env: str | None = None, default: tuple[str, ...] = ()
) -> list[str]:
    """A list setting: a YAML list, or a comma-separated string (which is
    what an environment variable can carry)."""
    value = setting(dotted, env, None)
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    raise ConfigError(f"{dotted}: expected a list or a comma-separated string")
