"""Helpers the tests in ``tests/`` share; the fixtures are in the
repository's root ``conftest.py``, where a pack's own tests reach them."""

from __future__ import annotations

from pathlib import Path


def copy_ontology(dest: Path, names: tuple[str, ...] | None = None) -> Path:
    """The repository's ontology as one directory a test may change: the
    core folder's files and every pack's module files (docs/packs.md).
    ``names`` keeps only those files (``core.yaml``, ``research.yaml``)."""
    from prax import config
    from prax.graph import ontology

    dest.mkdir(parents=True, exist_ok=True)
    files = [
        *Path(config.ONTOLOGY_PATH).glob("*.yaml"),
        *ontology.module_files().values(),
    ]
    for f in files:
        if names is None or f.name in names:
            (dest / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    return dest
