"""The studio pack: knowledge only (docs/packs.md)."""

from __future__ import annotations

from prax.packs.base import Pack

MANIFEST = Pack(
    name="studio",
    ontology=("studio.yaml", "electronics.yaml"),
    sameness="sameness.yaml",
    rules="rules.yaml",
)
