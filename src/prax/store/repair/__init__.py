"""The heal pass: what goes wrong often enough to have a name.

Extraction at scale leaves the same few kinds of damage behind. A model
copies a word out of its own prompt and "source name" becomes an entity
with a thousand edges. Two entities are merged and the edges between them
become loops from a thing to itself. A duplicate capture is retired and
its edges stay live. None of it is a bug to be fixed once: it is weather,
and this is the place that names each kind, finds it and repairs it.

Every repair goes through the store's own functions and takes the store's
rules with it: an edge is invalidated, never deleted (invariant 8), a
review item is resolved, a job row is closed. A pass is announced as a
job so the Jobs view shows it. Nothing is repaired unless a caller asks
for that ailment by name: `GET /heal` looks, `POST /heal` repairs, and
`prax heal` is the two of them with `--apply`.

The module is `store.repair` because `store.heal` is the function that
mends; `health` is the one that only looks.

Adding an ailment: write `find` (what is wrong, as rows a person can
read) and, when it can be repaired safely, `repair`; append an `Ailment`
to `AILMENTS`. An ailment whose repair is None is a report: it says what
to do rather than doing it.

A package of parts, each importing only from the ones before it in
``ORDER``; this module re-exports every name, so a caller outside writes
``store.repair.<name>`` or ``from .repair import name`` as before."""

from __future__ import annotations

ORDER = ("common", "graph", "documents", "ailments")

from .ailments import (  # noqa: F401
    AILMENTS,
    BY_NAME,
    _chosen,
    heal,
    health,
)
from .common import (  # noqa: F401
    CAP,
    EXAMPLES,
    Ailment,
    _edges_of_entities,
    _entity_rows,
    _invalidate,
    _live_edge_counts,
    _repair_edges,
    _repair_entities,
    _repair_jobs,
    _repair_review,
    _with_edge_counts,
)
from .documents import (  # noqa: F401
    _FORKS,
    _GLYPH_GLOB,
    _TITLE_KEY,
    SERVER_FAULT,
    STALE_JOB_SECONDS,
    TWIN_TITLE_MIN,
    UNTYPED,
    _document_edge_counts,
    _edges_of_retired,
    _extraction_failed,
    _glyph_documents,
    _glyphs_seen,
    _labelled_summaries,
    _not_a_document,
    _not_documents,
    _repair_extraction_failed,
    _repair_glyphs,
    _repair_labelled_summaries,
    _repair_not_documents,
    _repair_stale_extractions,
    _repair_stale_parses,
    _repair_twins,
    _repair_uncounted_pages,
    _repair_untyped,
    _review_of_retired,
    _stale_extractions,
    _stale_jobs,
    _stale_parses,
    _thin_texts,
    _twin_documents,
    _uncounted_pages,
    _unembedded_chunks,
    _unparsable_documents,
    _unpolished_transcripts,
    _unread_figures,
    _unread_formulas,
    _unreadable_documents,
    _untyped_documents,
)
from .graph import (  # noqa: F401
    _VERSION_PART,
    DOCUMENT_KINDS,
    FUNCTIONAL_SHOWN,
    HEAL_PRODUCER,
    MANGLED,
    MARKUP,
    NAME_TOO_LONG,
    PLACEHOLDER_NAMES,
    REFERENCE_NAME,
    SENTENCE_TYPES,
    SLOW_WALK_MS,
    WALKS_TIMED,
    WIRE,
    _backwards_part_of,
    _container_citations,
    _functional_conflicts,
    _mangled_names,
    _placeholder_entities,
    _reference_entities,
    _repair_names,
    _repair_part_of,
    _repair_stray_versions,
    _repair_wire_names,
    _self_edges,
    _slow_walks,
    _split_names,
    _stray_version_modules,
    _unnamed_entities,
    _wire_names,
    _without_stray,
    clean_name,
    split_names_page,
)
