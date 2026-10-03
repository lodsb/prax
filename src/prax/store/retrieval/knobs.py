"""The switches of retrieval that a test or a host turns: one object
every part reads at call time, so a switch set on ``knobs`` reaches the
part that reads it (a module flag set on the package would not, and a
test would pass without testing)."""

from __future__ import annotations


class Knobs:
    """The switches, as attributes of one instance (``knobs``)."""

    # A word the graph added to a query is searched only in the domains of the
    # thing it names (``expand_query_senses``). Off, every expansion is searched
    # everywhere, as before 2026-09-26: the switch the measurement flips.
    SENSES: bool = True

    # A query word also matches its other forms the library uses (``compounds.forms``).
    INFLECT: bool = True

    # A question for one of the small domains is recognised by its own
    # candidates: when several of the best fused hits are in a domain that
    # holds a sliver of the library, that domain's hits get one more vote.
    # "apple cake" put the Logic manuals first, which say Apple hundreds of
    # times, over the recipes the other word was asking for (2026-09-26).
    # The words' own domain statistics could not say it: a domain of thirty
    # documents gives any German word a lift of 20 to 90 by accident.
    DOMAIN_PRIOR: bool = True

    DELTA_MERGE_AT: int = 50_000  # vectors in the delta before a merge is due

    # or as many as the main file holds, once past this: the document index
    # (13,000 vectors) never reached fifty thousand, and its delta outgrew the
    # main file unmerged from 2026-09-12 (docs/research-database-layout.md)
    DELTA_MERGE_MIN: int = 1_000


knobs = Knobs()
