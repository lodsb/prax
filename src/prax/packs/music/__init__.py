"""The music pack: knowledge only, so far (docs/packs.md). Two modules: music
(theory, jazz harmony, orchestration, works) and sound (kinds of effect and
synthesis, what is heard and why, studio practice). Both are ``opt_in``: a
document is read against them only when its domain set names music or
sound, so a document with no set is read as before (its prompt would
otherwise be some 80% longer, and its reading due again)."""

from __future__ import annotations

from prax.packs.base import Pack

MANIFEST = Pack(
    name="music",
    ontology=("music.yaml", "sound.yaml"),
    rules="rules.yaml",
)
