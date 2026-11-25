"""The music pack: knowledge only, so far (docs/packs.md). Two modules: music
(theory, jazz harmony, orchestration, works) and sound (kinds of effect and
synthesis, what is heard and why, studio practice). Not in ``PACKS`` yet: a
document with no domain is read against every module, and these two would
make that prompt some 60% longer; how a module is read only where a
document's domain asks for it comes first (docs/PLAN.md)."""

from __future__ import annotations

from prax.packs.base import Pack

MANIFEST = Pack(
    name="music",
    ontology=("music.yaml", "sound.yaml"),
)
