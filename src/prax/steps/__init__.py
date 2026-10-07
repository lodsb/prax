"""What a worker does, named once.

``STEPS`` is every step the door hands out and the worker knows how to
do. ``WATCHED_STEPS`` is what a run does when it names none: the passes
that cost nothing. The rest are asked for on purpose, which is why a
flagged document can sit at "pending" with nothing broken — nobody is
asking for that queue. The CLI's ``--steps`` default, the worker's own
default and the door's answer to "who would do this"
(``prax.work.who_runs``) read these same names.

Nothing here imports anything but the packs' manifests, which are data:
the thin client reads it as cheaply as the door does. A pack's steps and
readings join the core's (docs/packs.md); a name a pack repeats is
refused here, at import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prax import packs as _packs

if TYPE_CHECKING:
    from prax.steps.base import Step

CORE_STEPS = (
    "parse",
    "titles",
    "dates",
    "worlddates",
    "summaries",
    "vocabulary",
    "sections",
    "communities",
    "extract",
    "promote",
    "typing",
    "embed",
    "resolve",
    "adjudicate",
    "genres",
)
STEPS = CORE_STEPS + tuple(_packs.step_homes())

# what a worker asks for unless the run names its steps
WATCHED_STEPS = (
    "parse",
    "titles",
    # when a document was published, read from its first page (2026-10-03)
    "dates",
    # when a fact holds, from the sentences that date it (2026-10-06): off
    # until prax.yaml names a served model for it
    "worlddates",
    "summaries",
    "vocabulary",
    # what a long document's chapters are about (watched since 2026-10-07,
    # the user's word: a book that arrives gets them without a hand)
    "sections",
    "communities",
    "extract",
    "embed",
    "resolve",
    # free with a local model since 2026-09-28; a paid one is skipped with a
    # note, and the default (none) says nothing
    "adjudicate",
    # the small labeller on the CPU (2026-10-01): a new document gets its
    # genres within a pass, so the nightly rules can place it, and a new
    # labeller run relabels its predecessor's documents a batch at a time
    "genres",
) + _packs.watched()

# the rest: named on the command line, and the paid ones want --spend
NAMED_ONLY = tuple(s for s in STEPS if s not in WATCHED_STEPS)

# the step whose model a reading runs, where it runs one at all. What a
# reading costs is that step's model and never the reading's own name:
# `figures` and `vision-pages` are the vision step as much as `vision`
# is, and a guard that keyed on the name let them spend unasked
# (2026-09-23). A reading absent from here runs no model of its own.
READING_STEPS = {
    "vision": "vision",
    "vision-pages": "vision",
    "figures": "vision",
    "formulas": "formulas",
    "polish": "polish",
    **_packs.readings(),
}


# which module holds each step's object: loaded when a step is first asked
# for, so the names above stay free of imports for the thin client
_HOMES = {
    "parse": "parse",
    "titles": "writing",
    "dates": "writing",
    "worlddates": "extract",
    "summaries": "writing",
    "sections": "writing",
    "communities": "writing",
    "genres": "writing",
    "vocabulary": "vocabulary",
    "extract": "extract",
    "promote": "extract",
    "typing": "graph",
    "resolve": "graph",
    "adjudicate": "graph",
    "embed": "embed",
    **_packs.step_homes(),  # a pack's: the module's full name
}

_REPEATED = _packs.duplicates({"steps": CORE_STEPS})
if _REPEATED:
    raise ValueError(f"a pack repeats a name: {'; '.join(_REPEATED)}")


def get(name: str) -> Step:
    """The step called ``name``: what the door hands out for it, what the
    worker does with that, what the door takes in (``prax.steps.base.Step``).
    """
    if name not in STEPS:
        raise ValueError(f"unknown step {name!r}; steps are {STEPS}")
    import importlib

    home = _HOMES[name]
    module = importlib.import_module(home if "." in home else f"prax.steps.{home}")
    step: Step = module.REGISTERED[name]
    return step
