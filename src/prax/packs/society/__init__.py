"""The society pack: knowledge only (docs/packs.md)."""

from __future__ import annotations

from prax.packs.base import Pack

MANIFEST = Pack(
    name="society",
    ontology=("society.yaml",),
    sameness=None,
    rules="rules.yaml",
)
