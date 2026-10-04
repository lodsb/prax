"""``search``: the legs run and fused, the domain prior, the filters
(kind, domain, document type, publication date), the optional rerank,
and the hits as an agent reads them."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any
from urllib.parse import quote_plus

from prax import config
from prax.graph import ontology
from prax.ml import embeddings
from prax.ml import rerank as rerank_mod
from prax.text import dates

from ..base import (
    _has_vectors,
    _index_path,
    _reading,
    hidden_documents,
    holds_domain,
    vectors_available,
)
from ..documents import DOCTYPES
from .knobs import knobs
from .legs import (
    CHUNK_KINDS,
    FIELD_WEIGHT,
    FIELD_WEIGHT_WORDS,
    RERANK_DEPTH,
    RRF_DEPTH,
    RRF_K,
    SEARCH_MODES,
    _field_fts_search,
    _field_vec_search,
    _fill_chunks,
    _filter_doctype,
    _fts_search,
    _fts_snippets,
    _rrf,
    _share,
    _vec_search,
)
from .query import (
    ALL_TERMS_WEIGHT,
    RARE_TERMS_WEIGHT,
    STOPWORDS,
    VEC_EXPAND,
    Scope,
    _expr,
    _plain_terms,
    _rare_terms,
    _sense_terms,
    expand_query_senses,
    expanded_text,
    keyword_terms,
)
from .vectors import (
    _vec_count,
)


@_reading
def search(
    con: sqlite3.Connection,
    query: str,
    limit: int = 10,
    *,
    kind: str | None = None,
    mode: str = "hybrid",
    rerank: bool | None = None,
    doctype: str | None = None,
    domain: str | None = None,
    timing: dict[str, float] | None = None,
    published_since: str | None = None,
    published_before: str | None = None,
    include_stale: bool = False,
    cite: bool = False,
) -> list[dict[str, Any]]:
    """Search returning compact snippets + ids (agent-shaped). ``timing``,
    when given, is filled with the seconds each side took (``fts``,
    ``embed``, ``vec``, ``field``, ``dvec``, ``finish``): what the door's
    slow-request log says of a search that took long.

    ``hybrid`` fuses four rank lists at document level: chunk BM25, chunk
    KNN, and BM25 and KNN over the document field (title, kind, summary;
    ``documents_fts``), so a query that names what a document *is* finds it
    even when other documents mention the term more. ``doctype`` keeps
    documents of one type: ``pdf``, ``web``, ``image``, ``text``, ``note``.

    ``hybrid`` (default) runs FTS5/BM25 and vector KNN in parallel and fuses
    them with reciprocal rank fusion; each hit carries ``score`` (the fused
    score), ``fts_rank`` and ``vec_rank`` (None when absent from that list).
    It degrades to FTS-only when embeddings are disabled, no index exists
    or no vectors exist yet. ``fts`` and ``vec`` force one side.
    Every hit carries the chunk's ``kind``, section ``heading`` path and
    ``page``, and a figure chunk's ``figure`` reference (the image is
    ``GET /doc/{doc_id}/figure/{figure}``); ``kind`` filters to one kind.

    ``rerank`` rescores the top ``RERANK_DEPTH`` hits with the configured
    cross-encoder (``prax.ml.rerank``; None follows ``PRAX_RERANK``, which is
    off by default) and adds ``rerank_score``.

    Every hit says when its document was published (``published``, the
    date as precise as its source says it, or None; ``meta.published``).
    ``published_since`` and ``published_before`` keep documents published
    then or later, and before then: a date or a year, compared as written,
    so ``2019`` keeps a document of 2019 and one of 2019-07. A document
    that does not say when it was published is left out of such a search.

    A document that is no longer current (``staleness``: its own status
    says retired, superseded, invalid or deprecated, or another document
    supersedes or invalidates it) carries ``stale``, with the state, the
    date and what replaced it, and moves ``STALE_SHIFT`` places down: a
    preference, never a filter, as the domain prior. ``include_stale``
    keeps the order as it was.

    With ``cite``, a passage hit carries ``cite``: its link with words of
    it no other passage of the document holds (``cite_link``), the
    citation an agent pastes; within ``CITE_BUDGET`` a search.
    """
    if kind is not None and kind not in CHUNK_KINDS:
        raise ValueError(f"kind must be one of {CHUNK_KINDS}")
    if mode not in SEARCH_MODES:
        raise ValueError(f"mode must be one of {SEARCH_MODES}")
    if doctype is not None and doctype not in DOCTYPES:
        raise ValueError(f"doctype must be one of {tuple(DOCTYPES)}")
    reranker = rerank_mod.current() if rerank is None or rerank else None
    if rerank and reranker is None:
        raise ValueError("rerank requested but PRAX_RERANK names no model")
    if domain is not None and domain not in ontology.current().modules:
        raise ValueError(f"unknown domain {domain!r}")
    depth = config.number("rerank.depth", "PRAX_RERANK_DEPTH", RERANK_DEPTH)
    fetch = max(limit, int(depth)) if reranker else limit
    since = _published_bound(published_since, "published_since")
    before = _published_bound(published_before, "published_before")
    if domain:
        fetch *= 3
    if since or before:
        fetch *= 4
    hits = _search_hits(con, query, fetch + STALE_SPARE, kind, mode, doctype, timing)
    if domain:
        hits = _filter_domain(con, hits, domain)
    stamped = published_dates(con, hits)
    if since or before:
        hits = [
            h
            for h in hits
            if (d := stamped.get(h["doc_id"]))
            and (not since or d >= since)
            and (not before or d < before)
        ]
    if reranker is not None and hits:
        with _Took(timing)("rerank"):
            hits = _apply_rerank(con, reranker, query, hits)
    folded: dict[int, list[tuple[int, str]]] = {}  # passages folded once a search
    deadline = time.monotonic() + CITE_BUDGET
    stale = staleness(con, hits)
    if stale and not include_stale:
        hits = _shift_stale(hits, stale)
    hits = hits[:limit]
    for h in hits:
        h["published"] = stamped.get(h["doc_id"])
        if h["doc_id"] in stale:
            h["stale"] = stale[h["doc_id"]]
        if cite and h.get("chunk_id") is not None:
            h["cite"] = cite_link(
                con, int(h["doc_id"]), int(h["chunk_id"]), folded, deadline
            )
    return hits


# A passage's link that survives a re-chunk (AL step 2; the client's O2):
# the chunk id, which a re-index may hand to another passage, and a few
# words of the passage that occur in no other passage of the document,
# which the UI trusts over the id (``chunkTarget``). Chosen here, so no
# agent picks them by hand. Relative: the door's address is the same for
# every hit, and the agent has it.
CITE_WORDS = (4, 6)  # the phrase's lengths tried, shortest first
CITE_TRIES = 6  # places in the passage a phrase is tried at
CITE_SCAN = 300  # words of the passage looked at
CITE_MIN_WORDS = 8  # a shorter passage (a header, a caption line) gets the id alone
CITE_LOCAL = 3000  # passages a document may have to be checked in memory
CITE_INDEX_TRIES = 4  # phrase queries for a longer one (a common phrase is slow)
CITE_BUDGET = 0.1  # seconds a search spends on its links; later hits get the id
_FOLD = re.compile(r"[^\W_]+")  # the UI's norm: letters and digits, lowercased


def cite_link(
    con: sqlite3.Connection,
    doc_id: int,
    chunk_id: int,
    folded_docs: dict[int, list[tuple[int, str]]] | None = None,
    deadline: float | None = None,
) -> str:
    """``#doc/N?chunk=M&find=…``: the passage's link with words of it no
    other passage of the document holds, or with the id alone when none is
    found (a running header has none). The words are a run of the passage
    as the UI folds text, so its exact-match path finds them. A document
    of up to ``CITE_LOCAL`` passages is checked in memory with the UI's
    own rule (one passage holds the run); a longer one by a phrase query
    on the keyword index, limited to the document, at most
    ``CITE_INDEX_TRIES`` times (a boilerplate passage has no such words,
    and a try on a common phrase is slow). ``folded_docs`` keeps a
    document's folded passages for the other hits of one search; past
    ``deadline`` (a monotonic time) the link is the id alone."""
    base = f"#doc/{doc_id}?chunk={chunk_id}"
    row = con.execute("SELECT text FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    if row is None:
        return base
    words = _FOLD.findall(str(row[0] or "").lower())[:CITE_SCAN]
    if len(words) < CITE_MIN_WORDS:
        return base

    def late() -> bool:
        return deadline is not None and time.monotonic() > deadline

    count = con.execute(
        "SELECT count(*) FROM chunks WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    folded: list[tuple[int, str]] | None = None
    if late():
        return base
    if count <= CITE_LOCAL:
        cache = folded_docs if folded_docs is not None else {}
        if doc_id not in cache:
            cache[doc_id] = [
                (int(r[0]), " ".join(_FOLD.findall(str(r[1] or "").lower())))
                for r in con.execute(
                    "SELECT id, text FROM chunks WHERE doc_id = ?", (doc_id,)
                )
            ]
        folded = cache[doc_id]
    tries = 0
    for n in CITE_WORDS:
        if len(words) < n:
            continue
        step = max(1, (len(words) - n) // CITE_TRIES)
        for start in range(0, len(words) - n + 1, step)[:CITE_TRIES]:
            run = words[start : start + n]
            if sum(1 for w in run if len(w) > 3 and w not in STOPWORDS) < 2:
                continue  # "and the of a": words every passage has
            if late():
                return base
            phrase = " ".join(run)
            if folded is not None:
                holders = [i for i, t in folded if phrase in t]
            else:
                tries += 1
                if tries > CITE_INDEX_TRIES:
                    return base
                holders = [
                    int(r[0])
                    for r in con.execute(
                        "SELECT c.id FROM chunks_fts JOIN chunks c"
                        " ON c.id = chunks_fts.rowid"
                        " WHERE chunks_fts MATCH ? AND c.doc_id = ? LIMIT 2",
                        ('"' + phrase + '"', doc_id),
                    )
                ]
            if holders == [chunk_id]:
                return f"{base}&find={quote_plus(phrase)}"
    return base


STALE_SHIFT = 5  # places a stale hit moves down: the same with or without a reranker
STALE_SPARE = 5  # candidates fetched past the limit, so a current one can move up
STALE_STATES = ("retired", "superseded", "invalid", "deprecated")
STALE_RELS = {"supersedes": "superseded", "invalidates": "invalid"}


def _shift_stale(
    hits: list[dict[str, Any]], stale: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """The hits with each stale one ``STALE_SHIFT`` places further down;
    the others keep their order."""
    keyed = sorted(
        enumerate(hits),
        key=lambda ih: ih[0] + (STALE_SHIFT + 0.5 if ih[1]["doc_id"] in stale else 0),
    )
    return [h for _, h in keyed]


def staleness(
    con: sqlite3.Connection, hits: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Which of the hits' documents are no longer current, and why:
    ``{doc_id: {state, since, by, replaced_by: [{doc_id, title}]}}``. Its
    own status (``meta.status``, a project's sync reads it from the note)
    or a live ``supersedes``/``invalidates`` edge that reaches it (a sync,
    an agent's ``link``, an extraction). A replacement the viewer may not
    see is not named."""
    if not hits:
        return {}
    ids = sorted({int(h["doc_id"]) for h in hits})
    marks = ",".join("?" * len(ids))
    out: dict[int, dict[str, Any]] = {}
    for r in con.execute(
        f"SELECT id, json_extract(meta, '$.status') AS status FROM documents"
        f" WHERE id IN ({marks}) AND json_extract(meta, '$.status.state') IN"
        f" ({','.join('?' * len(STALE_STATES))})",
        (*ids, *STALE_STATES),
    ):
        said = json.loads(r["status"])
        out[int(r["id"])] = {
            "state": said.get("state"),
            "since": said.get("since"),
            "by": said.get("by"),
            "replaced_by": [],
        }
    titles: dict[str, list[int]] = {}
    for h in hits:
        if h.get("title"):
            titles.setdefault(str(h["title"]), []).append(int(h["doc_id"]))
    if not titles:
        return out
    hidden = hidden_documents(con)
    tmarks = ",".join("?" * len(titles))
    # from the titles (the name index), the entities merged into them
    # (idx_entities_canonical_id) and the live edges into either
    # (idx_edges_dst), in that order: every search asks this, and a plan
    # left to choose began from the edges and scanned them all (58 ms on
    # the library of 2026-10-04, 0.2 ms this way)
    rows = con.execute(
        f"""
        WITH t AS MATERIALIZED (SELECT id, name FROM entities WHERE name IN ({tmarks})),
        ids AS MATERIALIZED (
            SELECT id, name FROM t
            UNION ALL
            SELECT e.id, t.name FROM t CROSS JOIN entities e ON e.canonical_id = t.id
        )
        SELECT x.rel, x.world_from, x.source_doc, c.name AS src, ids.name AS dst
        FROM ids CROSS JOIN edges x ON x.dst = ids.id AND x.valid_to IS NULL
        JOIN entities s0 ON s0.id = x.src
        JOIN entities c ON c.id = COALESCE(s0.canonical_id, s0.id)
        WHERE x.rel IN ('supersedes', 'invalidates') AND c.name != ids.name
        ORDER BY x.id
        """,
        tuple(titles),
    ).fetchall()
    for r in rows:
        if r["source_doc"] is not None and int(r["source_doc"]) in hidden:
            continue  # what only a hidden document says is not said
        other = con.execute(
            "SELECT id FROM documents WHERE title = ?"
            " AND json_extract(meta, '$.retired') IS NULL ORDER BY id DESC LIMIT 1",
            (r["src"],),
        ).fetchone()
        for doc_id in titles.get(str(r["dst"]), []):
            entry = out.setdefault(
                doc_id,
                {
                    "state": STALE_RELS[str(r["rel"])],
                    "since": r["world_from"],
                    "by": "graph",
                    "replaced_by": [],
                },
            )
            if (
                other is not None
                and int(other[0]) not in hidden
                and int(other[0]) != doc_id
            ):
                named = {"doc_id": int(other[0]), "title": str(r["src"])}
                if named not in entry["replaced_by"]:
                    entry["replaced_by"].append(named)
    return out


def _published_bound(value: str | None, name: str) -> str | None:
    """A bound of the published filter as a date prefix, or None."""
    if not value:
        return None
    got = dates.parse(value)
    if got is None:
        raise ValueError(f"{name}: a date or a year, not {value!r}")
    return got[0]


def published_dates(
    con: sqlite3.Connection, hits: list[dict[str, Any]] | list[int]
) -> dict[int, str]:
    """When each document was published, as it says it (``meta.published``):
    of a list of hits or of document ids; the undated are left out."""
    ids = list({h if isinstance(h, int) else h["doc_id"] for h in hits})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {
        r[0]: r[1]
        for r in con.execute(
            "SELECT id, json_extract(meta, '$.published.date') FROM documents"
            f" WHERE id IN ({marks})",
            ids,
        )
        if r[1]
    }


def _filter_domain(
    con: sqlite3.Connection, hits: list[dict[str, Any]], domain: str
) -> list[dict[str, Any]]:
    """Keep hits whose document is in ``domain`` or a module built on it
    (``Ontology.within``); a document without a domain set is in every
    module and stays."""
    within = ontology.current().within(domain)
    ids = {h["doc_id"] for h in hits}
    if not ids:
        return hits
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        "SELECT id, json_extract(meta, '$.domains') FROM documents"
        f" WHERE id IN ({marks})",
        tuple(ids),
    ).fetchall()
    allowed = {r[0] for r in rows if holds_domain(r[1], within, unset=True)}
    return [h for h in hits if h["doc_id"] in allowed]


def _apply_rerank(
    con: sqlite3.Connection,
    reranker: rerank_mod.Reranker,
    query: str,
    hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = [h["chunk_id"] for h in hits]
    marks = ",".join("?" * len(ids))
    texts = {
        r["id"]: r["text"]
        for r in con.execute(f"SELECT id, text FROM chunks WHERE id IN ({marks})", ids)
    }
    scores = reranker.score(query, [texts.get(i, "") for i in ids])
    for h, s in zip(hits, scores, strict=True):
        h["rerank_score"] = float(s)
    return sorted(hits, key=lambda h: -h["rerank_score"])


def _search_hits(
    con: sqlite3.Connection,
    query: str,
    limit: int,
    kind: str | None,
    mode: str,
    doctype: str | None = None,
    timing: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    emb = embeddings.serving() if mode != "fts" else None
    vectors_ready = (
        emb is not None
        and vectors_available()
        and _has_vectors(_index_path(emb.name))
        and _vec_count(con) > 0
    )
    if mode == "vec" and not vectors_ready:
        raise ValueError("vector search unavailable: no embedder, index or vectors")
    fetch = limit * 4 if doctype else limit
    # the pages that are the model's own answers (a standing question, a
    # briefing) are never evidence: a question would find its own page
    # first and the model would cite itself. Nor is what the viewer may not
    # see (stage U): set aside the same way, before the lists are fused
    aside = _aside_documents(con) | hidden_documents(con)
    # the query as terms with the library's own expansions of its acronyms;
    # the keyword side without the stopwords (the embedder sees them all):
    # one OR expression for recall, one AND expression for the tier that
    # wants every term present (only when there is more than one term)
    terms, senses = expand_query_senses(con, query)
    if not knobs.SENSES:
        senses = {}
    keywords = keyword_terms(terms)
    # the senses are searched where they live, each scope a list of its
    # own; what is left is searched everywhere, as it always was
    plain = _plain_terms(keywords, senses) or keywords
    scopes = sorted(set(senses.values()), key=sorted)
    sensed = [
        (scope, _expr(_sense_terms(keywords, senses, scope), all_terms=False))
        for scope in scopes
    ]
    full_expr = _expr(keywords, all_terms=False)  # what a snippet marks
    or_expr = _expr(plain, all_terms=False)
    and_expr = _expr(plain, all_terms=True) if len(plain) > 1 else None
    if mode == "fts":  # the raw chunk list, expanded but not fused
        hits = _fts_sensed(
            con, query, fetch + len(aside), kind, or_expr, sensed, snippets=True
        )
        hits = _without(hits, aside)
        return _finish(con, hits, query, limit, doctype, expr=full_expr)
    depth = max(limit * 3, RRF_DEPTH)
    # keyword lists: any term (recall), optionally every term, and the rare
    # terms alone: "adaa iir" must not be decided by the thousands of chunks
    # that say "iir"
    weights: dict[str, float] = {
        "fts_all": ALL_TERMS_WEIGHT,
        "fts_rare": RARE_TERMS_WEIGHT,
    }
    took = _Took(timing)
    with took("fts"):
        lists = [_fts_sensed(con, query, depth, kind, or_expr, sensed)]
    names = ["fts"]
    if and_expr and ALL_TERMS_WEIGHT > 0:
        with took("fts"):
            lists.append(
                _fts_search(con, query, depth, kind, snippets=False, expr=and_expr)
            )
        names.append("fts_all")
    rare = _rare_terms(con, plain) if RARE_TERMS_WEIGHT > 0 else []
    if rare and len(rare) == len(terms):
        # the whole query is rare tokens ("adaa"): the keyword list carries
        # the weight itself, vector neighbours of letters must not outvote it
        weights["fts"] = RARE_TERMS_WEIGHT
    if rare and len(rare) < len(terms):
        rare_expr = _expr(rare, all_terms=True)
        lists.append(
            _fts_search(con, query, depth, kind, snippets=False, expr=rare_expr)
        )
        names.append("fts_rare")
    if not vectors_ready:
        if kind is None:  # hybrid without vectors: the field too
            _field_lists(con, query, depth, or_expr, sensed, lists, names, weights)
        fused = _rrf([_without(x, aside) for x in lists], names, _drawn(fetch), weights)
        fused = _domain_prior(con, fused, fetch, keywords)
        return _finish(con, fused, query, limit, doctype, expr=full_expr)
    assert emb is not None
    with took("embed"):
        vector = emb.embed_query(expanded_text(terms) if VEC_EXPAND else query)
    if mode == "vec":
        return _finish(
            con,
            _without(_vec_search(con, vector, fetch + len(aside), kind), aside),
            query,
            limit,
            doctype,
            expr=full_expr,
        )
    with took("vec"):
        lists.append(_vec_search(con, vector, depth, kind))
    names.append("vec")
    if kind is None:  # the field has no chunk kind to filter by
        with took("field"):
            _field_lists(con, query, depth, or_expr, sensed, lists, names, weights)
        with took("dvec"):
            lists.append(_field_vec_search(con, emb.name, vector, depth))
        names.append("dvec")
    fused = _rrf([_without(x, aside) for x in lists], names, _drawn(fetch), weights)
    fused = _domain_prior(con, fused, fetch, keywords)
    with took("finish"):
        return _finish(con, fused, query, limit, doctype, expr=full_expr)


PRIOR_DEPTH = 30  # the fused candidates looked at
PRIOR_MIN = 3  # of them in one small domain; chance gives it 0.2
PRIOR_SMALL = 0.2  # a domain holding less than this share of the documents
PRIOR_WEIGHT = 1.0  # the vote, as a list's weight in the fusion


def _drawn(fetch: int) -> int:
    """How deep the fusion is drawn: deep enough for the prior to see."""
    return max(fetch, PRIOR_DEPTH) if knobs.DOMAIN_PRIOR else fetch


def _domain_prior(
    con: sqlite3.Connection,
    fused: list[dict[str, Any]],
    fetch: int,
    terms: list[list[str]],
) -> list[dict[str, Any]]:
    """The fused hits with the vote of the small domain they point to, if
    they point to one, trimmed to ``fetch``.

    Pointing to one is two things: at least ``PRIOR_MIN`` of the best
    candidates are in it, and its candidates hold every word of the
    question. The count alone moved three recipes that say "apple" once
    above the manuals for "Apple Loops" in a small library; no recipe says
    "loops", so that is not a question for the kitchen.

    Nothing is filtered: a hit outside the domain keeps its score, and the
    domain's hits gain what a list ranking them would give. A research
    question never gets it, because research is nearly the whole library
    and no small domain gathers three of its candidates.
    """
    if not knobs.DOMAIN_PRIOR or not fused:
        return fused[:fetch]
    ids = sorted({h["doc_id"] for h in fused})
    marks = ",".join("?" * len(ids))
    domains: dict[int, list[str]] = {
        int(r[0]): json.loads(r[1]) if r[1] else []
        for r in con.execute(
            "SELECT id, json_extract(meta, '$.domains') FROM documents"
            f" WHERE id IN ({marks})",
            ids,
        )
    }
    counts: dict[str, int] = {}
    for h in fused[:PRIOR_DEPTH]:
        for d in domains.get(h["doc_id"], []):
            counts[d] = counts.get(d, 0) + 1
    small = {
        d: n
        for d, n in counts.items()
        if n >= PRIOR_MIN and _share(con, frozenset({d})) < PRIOR_SMALL
    }
    if not small:
        return fused[:fetch]
    best = max(small.values())
    chosen = [d for d, n in small.items() if n == best]
    if len(chosen) > 1:  # two domains as likely: no preference
        return fused[:fetch]
    domain = chosen[0]
    inside = [h["doc_id"] for h in fused if domain in domains.get(h["doc_id"], [])]
    if not _holds_every_term(con, inside, terms):
        return fused[:fetch]
    rank = 0
    for h in fused:
        if domain in domains.get(h["doc_id"], []):
            rank += 1
            h["score"] += PRIOR_WEIGHT / (RRF_K + rank)
            h["domain_rank"] = rank
    fused.sort(key=lambda h: -h["score"])
    return fused[:fetch]


def _holds_every_term(
    con: sqlite3.Connection, doc_ids: list[int], terms: list[list[str]]
) -> bool:
    """Do these documents between them hold every term (any of its
    alternatives)? Asked of their chunks by rowid, which the keyword index
    answers a chunk at a time rather than by scanning a common word."""
    rowids = [
        int(r[0])
        for r in con.execute(
            f"SELECT id FROM chunks WHERE doc_id IN ({','.join('?' * len(doc_ids))})",
            doc_ids,
        )
    ]
    if not rowids:
        return False
    marks = ",".join("?" * len(rowids))
    for term in terms:
        expr = _expr([term], all_terms=False)
        hit = con.execute(
            f"SELECT 1 FROM chunks_fts WHERE rowid IN ({marks})"
            " AND chunks_fts MATCH ? LIMIT 1",
            (*rowids, expr),
        ).fetchone()
        if hit is None:
            return False
    return True


def _field_lists(
    con: sqlite3.Connection,
    query: str,
    depth: int,
    or_expr: str | None,
    sensed: list[tuple[Scope, str | None]],
    lists: list[list[dict[str, Any]]],
    names: list[str],
    weights: dict[str, float],
) -> None:
    """The document field's keyword list, its senses counted where they
    live (``_by_score``)."""
    hits = _field_fts_search(con, query, depth, expr=or_expr)
    for scope, expr in sensed:
        hits += _field_fts_search(con, query, depth, expr=expr, scope=scope)
    lists.append(_by_score(hits, "doc_id", depth) if sensed else hits)
    names.append("field")
    weights["field"] = _field_weight(query)


def _fts_sensed(
    con: sqlite3.Connection,
    query: str,
    depth: int,
    kind: str | None,
    or_expr: str | None,
    sensed: list[tuple[Scope, str | None]],
    *,
    snippets: bool = False,
) -> list[dict[str, Any]]:
    """The chunk keyword list, its senses counted where they live."""
    hits = _fts_search(con, query, depth, kind, snippets=snippets, expr=or_expr)
    for scope, expr in sensed:
        hits += _fts_search(
            con, query, depth, kind, snippets=snippets, expr=expr, scope=scope
        )
    return _by_score(hits, "chunk_id", depth) if sensed else hits


def _by_score(hits: list[dict[str, Any]], key: str, depth: int) -> list[dict[str, Any]]:
    """One keyword list out of the plain one and the scoped ones, by BM25,
    each hit once at its best.

    A term's BM25 weight does not depend on the rest of the expression, so
    a chunk's score under the plain words and under the plain words with a
    sense differ by what the sense adds. Merged, a chunk in the sense's
    domains scores as it did before senses were scoped, and one outside
    scores as if the graph had not added the word. As extra rank lists the
    senses were extra votes instead: every document of the scope that
    matched any word was counted twice, and a German question about signal
    synthesis lost its paper to the other research documents
    (2026-09-26)."""
    seen: set[int] = set()
    out = []
    for h in sorted(hits, key=lambda h: h["score"]):
        if h[key] not in seen:
            seen.add(h[key])
            out.append(h)
    return out[:depth]


ASIDE_PAGE_KINDS = ("question", "briefing")


def _aside_documents(con: sqlite3.Connection) -> frozenset[int]:
    """The documents a search never returns: the pages that are the
    model's own answers (``ASIDE_PAGE_KINDS``)."""
    marks = ",".join("?" * len(ASIDE_PAGE_KINDS))
    return frozenset(
        r[0]
        for r in con.execute(
            f"SELECT doc_id FROM pages WHERE kind IN ({marks})", ASIDE_PAGE_KINDS
        )
    )


def _without(hits: list[dict[str, Any]], aside: frozenset[int]) -> list[dict[str, Any]]:
    if not aside:
        return hits
    return [h for h in hits if h["doc_id"] not in aside]


class _Took:
    """``with took("vec"):`` adds the block's seconds to ``timing["vec"]``
    (nothing when no dict was given)."""

    def __init__(self, timing: dict[str, float] | None) -> None:
        self.timing = timing
        self.name = ""
        self.t0 = 0.0

    def __call__(self, name: str) -> _Took:
        self.name = name
        return self

    def __enter__(self) -> None:
        self.t0 = time.perf_counter()

    def __exit__(self, *exc: object) -> None:
        if self.timing is not None:
            self.timing[self.name] = (
                self.timing.get(self.name, 0.0) + time.perf_counter() - self.t0
            )


def _field_weight(query: str) -> float:
    words = len(query.split())
    if words <= 3:
        return FIELD_WEIGHT
    span = max(1, FIELD_WEIGHT_WORDS - 3)
    return max(1.0, FIELD_WEIGHT - (FIELD_WEIGHT - 1.0) * (words - 3) / span)


def _finish(
    con: sqlite3.Connection,
    hits: list[dict[str, Any]],
    query: str,
    limit: int,
    doctype: str | None,
    expr: str | None = None,
) -> list[dict[str, Any]]:
    if doctype:
        hits = _filter_doctype(con, hits, doctype)
    hits = hits[:limit]
    field_only = {h["doc_id"] for h in hits if h.get("chunk_id") is None}
    _fill_chunks(con, hits, query)
    chunk_ids = [
        h["chunk_id"]
        for h in hits
        if h.get("chunk_id") is not None
        and h["doc_id"] not in field_only
        and h.get("fts_rank") is not None
    ]
    marked = _fts_snippets(con, query, chunk_ids, expr=expr) if chunk_ids else {}
    for h in hits:
        if h.get("chunk_id") in marked:
            h["snippet"] = marked[h["chunk_id"]]
    return hits
