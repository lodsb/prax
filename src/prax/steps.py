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
    "extract",
    "promote",
    "typing",
    "embed",
    "resolve",
    "adjudicate",
)

# what a worker asks for unless the run names its steps
WATCHED_STEPS = ("parse", "titles", "extract", "embed", "resolve")

# the rest: named on the command line, and the paid ones want --spend
NAMED_ONLY = tuple(s for s in STEPS if s not in WATCHED_STEPS)
