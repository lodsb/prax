"""What a worker does, named once.

``STEPS`` is every step the door hands out and the worker knows how to
do. ``WATCHED_STEPS`` is what a run does when it names none: the passes
that cost nothing. The rest are asked for on purpose, which is why a
flagged document can sit at "pending" with nothing broken — nobody is
asking for that queue. The CLI's ``--steps`` default, the worker's own
default and the door's answer to "who would do this"
(``prax.work.who_runs``) read these same names.

Nothing here imports anything: the thin client reads it as cheaply as
the door does.
"""

from __future__ import annotations

STEPS = (
    "parse",
    "titles",
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

# what a worker asks for unless the run names its steps
WATCHED_STEPS = (
    "parse",
    "titles",
    "summaries",
    "vocabulary",
    "communities",
    "extract",
    "embed",
    "resolve",
    # free with a local model since 2026-09-28; a paid one is skipped with a
    # note, and the default (none) says nothing
    "adjudicate",
)

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
}


# which module holds each step's object: loaded when a step is first asked
# for, so the names above stay free of imports for the thin client
_HOMES = {
    "parse": "parse",
    "titles": "writing",
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
}


def get(name: str):
    """The step called ``name``: what the door hands out for it, what the
    worker does with that, what the door takes in (``prax.steps.base.Step``).
    """
    if name not in STEPS:
        raise ValueError(f"unknown step {name!r}; steps are {STEPS}")
    import importlib

    module = importlib.import_module(f"prax.steps.{_HOMES[name]}")
    return module.REGISTERED[name]
