"""The research pack: knowledge only (docs/packs.md)."""

from __future__ import annotations

from prax.packs.base import Pack

MANIFEST = Pack(
    name="research",
    ontology=("research.yaml",),
    sameness=None,
    lexicon="lexicon.yaml",
    rules="rules.yaml",
)
