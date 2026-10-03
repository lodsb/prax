"""Finding things: the query, the two indexes, and their fusion.

Acronym expansion and the FTS5 match expression, BM25 over chunks and over
the document field, KNN over the chunk and document vectors (the usearch
files and their deltas), reciprocal rank fusion at document level, the
optional cross-encoder rerank, and the bookkeeping of which chunk has a
vector from which model.

A package of parts, each importing only from the ones before it in
``ORDER`` (it passed 2,000 lines on 2026-10-03, CLAUDE.md invariant 3);
this module re-exports every name, so a caller writes
``store.retrieval.name`` or ``store.name`` as before. The switches a test
or a host turns are attributes of ``knobs``, read at call time by the
part that uses them. Setting one on this package is refused rather than
silently missed (``_Package``).
"""

from __future__ import annotations

import sys
import types
from typing import Any

ORDER = (
    "knobs",
    "compounds",
    "query",
    "legs",
    "vectors",
    "fusion",
    "similar",
)

from .compounds import (  # noqa: F401
    _WORD,
    ENDINGS,
    FORM_RATIO,
    LINKS,
    MAX_FORMS,
    MIN_DOCS,
    MIN_FORM,
    MIN_PART,
    MIN_WORD,
    RARE_FORM,
    _docs,
    expand,
    forms,
    split,
    term_documents,
)
from .fusion import (  # noqa: F401
    ASIDE_PAGE_KINDS,
    PRIOR_DEPTH,
    PRIOR_MIN,
    PRIOR_SMALL,
    PRIOR_WEIGHT,
    _apply_rerank,
    _aside_documents,
    _by_score,
    _domain_prior,
    _drawn,
    _field_lists,
    _field_weight,
    _filter_domain,
    _finish,
    _fts_sensed,
    _holds_every_term,
    _published_bound,
    _search_hits,
    _Took,
    _without,
    published_dates,
    search,
)
from .knobs import (  # noqa: F401
    Knobs,
    knobs,
)
from .legs import (  # noqa: F401
    _SHARES,
    CHUNK_KINDS,
    FIELD_WEIGHT,
    FIELD_WEIGHT_WORDS,
    RERANK_DEPTH,
    RRF_DEPTH,
    RRF_K,
    SEARCH_MODES,
    VEC_SEARCH_CAP,
    WIDE_DRAW,
    WIDE_SCOPE,
    _field_fts_search,
    _field_hit,
    _field_vec_search,
    _fill_chunks,
    _filter_doctype,
    _fts_search,
    _fts_snippets,
    _in_domains,
    _rrf,
    _share,
    _vec_search,
)
from .query import (  # noqa: F401
    ACRONYM_EXPANSIONS,
    ACRONYM_MIN_DOCS,
    ALL_TERMS_WEIGHT,
    NAMED_AS,
    RARE_CHUNKS,
    RARE_MAX_LEN,
    RARE_TERMS_WEIGHT,
    STOPWORDS,
    VEC_EXPAND,
    Scope,
    _expr,
    _fts_query,
    _keyword,
    _lives_in,
    _named_as,
    _plain_terms,
    _rare_terms,
    _sense_terms,
    acronym_expansions,
    expand_query,
    expand_query_senses,
    expanded_text,
    keyword_terms,
    known_as,
    replace_acronyms,
)
from .similar import (  # noqa: F401
    CENTROID_CHUNKS,
    CENTROID_MIN_CHARS,
    CONTEXT_LIMIT,
    _similar_documents,
    phrase_languages,
    similar_documents,
)
from .vectors import (  # noqa: F401
    _BUILD_LOCK,
    _EMBEDDED,
    COMPACT_REBUILD,
    RECONCILE_SLACK,
    _adopt_batch,
    _all_chunks_embedded,
    _forget_embeddings,
    _index_has,
    _merge,
    _merge_now,
    _present,
    _save_delta,
    _vec_count,
    adopt_vectors,
    compact_vectors,
    count_pending_document_embeddings,
    count_pending_embeddings,
    delta_counts,
    fts_merge,
    merge_vectors,
    newest_chunk,
    pending_document_embeddings,
    pending_embeddings,
    reconcile_unsaved,
    save_document_vectors,
    save_vectors,
    store_document_embeddings,
    store_embeddings,
    vec_status,
    warm_fts,
    warm_indexes,
)

KNOBS = frozenset(vars(Knobs).get("__annotations__", {}))


class _Package(types.ModuleType):
    """The package refuses a knob set on itself: the parts read
    ``knobs``, so ``retrieval.SENSES = False`` would change nothing and a
    test would pass without testing."""

    def __setattr__(self, name: str, value: Any) -> None:
        if name in KNOBS:
            raise AttributeError(f"set retrieval.knobs.{name}, not retrieval.{name}")
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Package
