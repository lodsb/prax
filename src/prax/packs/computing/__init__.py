"""The computing pack: knowledge only (docs/packs.md)."""

from __future__ import annotations

from prax.packs.base import Pack

MANIFEST = Pack(
    name="computing",
    ontology=("computing.yaml",),
    sameness=None,
    rules="rules.yaml",
)
