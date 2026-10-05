"""The craft pack: knowledge only (docs/packs.md)."""

from __future__ import annotations

from prax.packs.base import Pack

MANIFEST = Pack(
    name="craft",
    ontology=("craft.yaml", "kitchen.yaml", "workshop.yaml"),
    sameness="sameness.yaml",
    lexicon="lexicon.yaml",
    rules="rules.yaml",
)
