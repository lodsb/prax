"""Finding things: the query, the two indexes, and their fusion.

Acronym expansion and the FTS5 match expression, BM25 over chunks and over
the document field, KNN over the chunk and document vectors (the usearch
files and their deltas), reciprocal rank fusion at document level, the
optional cross-encoder rerank, and the bookkeeping of which chunk has a
vector from which model.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from prax import chunking, config, embeddings, ontology
from prax import rerank as rerank_mod

from .base import (
    _NOW,
    _TOKEN,
    _delta,
    _delta_path,
    _doc_index_path,
    _drop_index_views,
    _get_vector,
    _has_vectors,
    _index,
    _index_path,
    _indexes,
    _knn,
    _open_index,
    _serialized,
    vectors_available,
)
from .documents import DOCTYPES, _chunk_shape

RERANK_DEPTH = 30  # hits rescored when reranking is on (rerank.depth)


RRF_K = 60  # reciprocal rank fusion constant


RRF_DEPTH = 100  # candidates per side before fusion (docs/eval: 30 vs 100 vs 300)


# The document-field BM25 list is short and precise (a field names what a
# document is); at equal weight a lone rank-1 field hit loses to any document
# two chunk lists agree on. A short query names a thing, so the field gets
# FIELD_WEIGHT there; a long paraphrase is about content, and the field's
# incidental word matches would mislead, so the weight fades to 1 by
# FIELD_WEIGHT_WORDS words (docs/eval/retrieval-field-2026-09-10.md).
FIELD_WEIGHT = 2.0


FIELD_WEIGHT_WORDS = 7


VEC_SEARCH_CAP = 4000  # widest KNN candidate set when post-filtering by kind


def _fts_query(query: str) -> str | None:
    """Build a safe FTS5 MATCH expression: every token quoted, joined by OR.

    User strings are never passed to MATCH raw; punctuation and FTS operators
    in the input cannot raise. OR keeps recall for natural multi-word queries;
    BM25 ranks chunks that match more (and rarer) terms first.
    """
    tokens = _TOKEN.findall(query)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


ACRONYM_MIN_DOCS = 1  # one definition is enough: the phrase only adds an alternative


ACRONYM_EXPANSIONS = 2


RARE_CHUNKS = 50  # a token in fewer chunks than this decides the ranking


RARE_MAX_LEN = 6  # longer tokens are words, not acronyms, unless the table knows them


# Measured on the 62 library queries (docs/eval/retrieval-acronyms-2026-09-12.md):
# keyword-side expansion alone lifts MRR 0.89 -> 0.905; expanding the embedder's
# input and a rank list of chunks holding every term both cost; the rare-terms
# list costs one query and is what makes "adaa iir" find the ADAA papers.
ALL_TERMS_WEIGHT = 0.0  # the rank list of chunks holding every query term; 0 = off


RARE_TERMS_WEIGHT = 3.0  # the rank list of chunks holding the rare terms


VEC_EXPAND = False  # embed the query as typed; expansions only on the keyword side


@_serialized
def replace_acronyms(con: sqlite3.Connection, rows: list[tuple[str, str, int]]) -> int:
    """Replace the acronyms table (the ``acronyms`` pass of ``store.maintain``):
    ``(acronym, expansion, documents)`` rows, lowercased."""
    con.execute("DELETE FROM acronyms")
    con.executemany(
        "INSERT INTO acronyms (acronym, expansion, docs) VALUES (?, ?, ?)",
        [(a.lower(), e.lower(), int(n)) for a, e, n in rows],
    )
    con.commit()
    return len(rows)


def acronym_expansions(
    con: sqlite3.Connection,
    token: str,
    *,
    min_docs: int = ACRONYM_MIN_DOCS,
    limit: int = ACRONYM_EXPANSIONS,
) -> list[str]:
    """The phrases the library defines ``token`` as, best attested first."""
    return [
        r[0]
        for r in con.execute(
            "SELECT expansion FROM acronyms WHERE acronym = ? AND docs >= ?"
            " ORDER BY docs DESC, expansion LIMIT ?",
            (token.lower(), min_docs, limit),
        )
    ]


def expand_query(con: sqlite3.Connection, query: str) -> list[list[str]]:
    """The query as terms, each a list of alternatives: the token itself and
    the phrases the library defines it as (``[["adaa", "antiderivative
    antialiasing"], ["iir"]]``). Tokens of nine or more characters, and
    digits, are never acronyms."""
    terms: list[list[str]] = []
    for tok in _TOKEN.findall(query):
        alts = [tok.lower()]
        if 2 <= len(tok) <= 8 and tok.isalpha():
            alts += [e for e in acronym_expansions(con, tok) if e != tok.lower()]
        terms.append(alts)
    return terms


def expanded_text(terms: list[list[str]]) -> str:
    """The query with its expansions, for the embedder."""
    return " ".join(alt for term in terms for alt in term)


def _expr(terms: list[list[str]], *, all_terms: bool) -> str | None:
    """MATCH expression: every alternative quoted (a phrase stays a phrase),
    alternatives OR-ed within a term, terms OR-ed (recall) or AND-ed (the
    tier that wants every term present)."""
    if not terms:
        return None
    groups = ["(" + " OR ".join(f'"{a}"' for a in term) + ")" for term in terms]
    return (" AND " if all_terms else " OR ").join(groups)


def _rare_terms(con: sqlite3.Connection, terms: list[list[str]]) -> list[list[str]]:
    """The acronym-shaped terms that match fewer than ``RARE_CHUNKS`` chunks
    (and at least one): a rare exact token like "adaa" should decide the
    ranking, not the common words around it, so those terms get a rank list
    of their own. Only short tokens, tokens with digits and known acronyms
    qualify: a rare inflection of an ordinary word ("reassigning",
    "upmixing") pulled paraphrase queries towards the wrong documents."""
    out = []
    for term in terms:
        tok = term[0]
        acronym_shaped = (
            len(tok) <= RARE_MAX_LEN or any(ch.isdigit() for ch in tok) or len(term) > 1
        )
        if not acronym_shaped:
            continue
        expr = _expr([term], all_terms=False)
        n = con.execute(
            "SELECT count(*) FROM (SELECT rowid FROM chunks_fts WHERE chunks_fts"
            " MATCH ? LIMIT ?)",
            (expr, RARE_CHUNKS),
        ).fetchone()[0]
        if 0 < n < RARE_CHUNKS:
            out.append(term)
    return out


SEARCH_MODES = ("hybrid", "fts", "vec")


def _fts_search(
    con: sqlite3.Connection,
    query: str,
    limit: int,
    kind: str | None,
    *,
    snippets: bool = True,
    expr: str | None = None,
) -> list[dict[str, Any]]:
    """BM25 over chunks. ``snippets=False`` skips the snippet() call, which
    reads every matched chunk's text and dominates the cost of deep lists;
    ``_fts_snippets`` fills them in for the few hits that survive fusion."""
    expr = expr or _fts_query(query)
    if expr is None:
        return []
    kind_clause = "AND c.kind = ?" if kind is not None else ""
    args: tuple[Any, ...] = (expr, kind, limit) if kind is not None else (expr, limit)
    snippet_col = (
        "snippet(chunks_fts, 0, '[', ']', '…', 12)"
        if snippets
        else "substr(c.text, 1, 160)"
    )
    rows = con.execute(
        f"""
        SELECT c.id AS chunk_id, c.doc_id, d.title,
               {snippet_col} AS snippet,
               bm25(chunks_fts) AS score,
               c.kind, c.locator, c.heading
        FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                        JOIN documents d ON d.id = c.doc_id
        WHERE chunks_fts MATCH ? {kind_clause}
        ORDER BY score LIMIT ?
        """,
        args,
    ).fetchall()
    out = []
    for r in rows:
        hit = {k: r[k] for k in ("chunk_id", "doc_id", "title", "snippet", "score")}
        hit.update(_chunk_shape(r))
        out.append(hit)
    return out


def _fts_snippets(
    con: sqlite3.Connection, query: str, chunk_ids: list[int], expr: str | None = None
) -> dict[int, str]:
    """Match-marked snippets for a handful of chunks (the fused hits)."""
    expr = expr or _fts_query(query)
    if expr is None or not chunk_ids:
        return {}
    marks = ",".join("?" * len(chunk_ids))
    rows = con.execute(
        f"""
        SELECT rowid, snippet(chunks_fts, 0, '[', ']', '…', 12) AS snippet
        FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid IN ({marks})
        """,
        (expr, *chunk_ids),
    ).fetchall()
    return {r["rowid"]: r["snippet"] for r in rows}


def _vec_search(
    con: sqlite3.Connection, vector: Any, limit: int, kind: str | None
) -> list[dict[str, Any]]:
    """KNN over the usearch index (cosine distance), joined to live chunks.

    The index has no filter of its own, so ``kind`` is applied after the
    search over a wider candidate set (chunks of one kind are a few percent
    of the index). Keys whose chunk no longer exists are dropped here.
    """
    model = embeddings.current().name  # type: ignore[union-attr]
    if not _has_vectors(_index_path(model)):
        return []
    want = limit * (25 if kind is not None else 3)
    found = _knn(_index_path(model), vector, min(max(want, limit), VEC_SEARCH_CAP))
    if not found:
        return []
    distance = dict(found)
    ids = list(distance)
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), 500):
        part = ids[i : i + 500]
        marks = ",".join("?" * len(part))
        kind_clause = "AND c.kind = ?" if kind is not None else ""
        args: tuple[Any, ...] = (*part, kind) if kind is not None else tuple(part)
        rows = con.execute(
            f"""
            SELECT c.id AS chunk_id, c.doc_id, d.title, c.kind, c.locator,
                   c.heading, substr(c.text, 1, 160) AS head
            FROM chunks c JOIN documents d ON d.id = c.doc_id
            WHERE c.id IN ({marks}) {kind_clause}
            """,
            args,
        ).fetchall()
        for r in rows:
            hit = {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "title": r["title"],
                "snippet": r["head"],
                "score": distance[r["chunk_id"]],
            }
            hit.update(_chunk_shape(r))
            out.append(hit)
    out.sort(key=lambda h: h["score"])
    return out[:limit]


def _field_fts_search(
    con: sqlite3.Connection, query: str, limit: int, expr: str | None = None
) -> list[dict[str, Any]]:
    """BM25 over the document field; hits carry no chunk yet."""
    expr = expr or _fts_query(query)
    if expr is None:
        return []
    rows = con.execute(
        """
        SELECT f.rowid AS doc_id, d.title,
               snippet(documents_fts, 0, '[', ']', '…', 14) AS snippet,
               bm25(documents_fts) AS score
        FROM documents_fts f JOIN documents d ON d.id = f.rowid
        WHERE documents_fts MATCH ? ORDER BY score LIMIT ?
        """,
        (expr, limit),
    ).fetchall()
    return [_field_hit(r["doc_id"], r["title"], r["snippet"], r["score"]) for r in rows]


def _field_vec_search(
    con: sqlite3.Connection, model: str, vector: Any, limit: int
) -> list[dict[str, Any]]:
    """KNN over the document-field index."""
    if not _has_vectors(_doc_index_path(model)):
        return []
    found = _knn(_doc_index_path(model), vector, limit)
    if not found:
        return []
    distance = dict(found)
    ids = list(distance)
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT d.id, d.title, f.field FROM documents d"
        f" JOIN documents_fts f ON f.rowid = d.id WHERE d.id IN ({marks})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [
        _field_hit(i, by_id[i]["title"], by_id[i]["field"][:160], distance[i])
        for i in ids
        if i in by_id
    ]


def _field_hit(doc_id: int, title: str, snippet: str, score: float) -> dict[str, Any]:
    return {
        "chunk_id": None,
        "doc_id": doc_id,
        "title": title,
        "snippet": snippet,
        "score": score,
        "kind": None,
        "heading": [],
        "page": None,
    }


def _fill_chunks(
    con: sqlite3.Connection,
    hits: list[dict[str, Any]],
    query: str,
    expr: str | None = None,
) -> None:
    """A hit that came from the document field alone gets the document's
    chunk that holds most of the query's terms (longer terms counting for
    more: "what", "is" and "a" carry no question), else its first chunk,
    so every hit opens somewhere; the field snippet stays, it says why
    the document matched. The chunk is chosen over the document's own
    rows: a MATCH over the whole index filtered to one document walks
    the posting lists of every common word in the question (seconds per
    hit on a million chunks; ``expr`` is kept for the signature)."""
    terms = {t.lower() for t in _TOKEN.findall(query)}
    for h in hits:
        if h.get("chunk_id") is not None:
            continue
        rows = con.execute(
            "SELECT id, kind, locator, heading, text FROM chunks WHERE doc_id = ?"
            " ORDER BY seq",
            (h["doc_id"],),
        ).fetchall()
        if not rows:
            continue
        best, score = rows[0], 0
        for r in rows:
            found = terms & set(_TOKEN.findall(r["text"].lower()))
            sc = sum(len(t) for t in found)
            if sc > score:
                best, score = r, sc
        h["chunk_id"] = best["id"]
        h.update(_chunk_shape(best))


def _filter_doctype(
    con: sqlite3.Connection, hits: list[dict[str, Any]], doctype: str
) -> list[dict[str, Any]]:
    if not hits:
        return hits
    ids = list({h["doc_id"] for h in hits})
    marks = ",".join("?" * len(ids))
    keep = {
        r[0]
        for r in con.execute(
            f"SELECT d.id FROM documents d WHERE d.id IN ({marks})"
            f" AND {DOCTYPES[doctype]}",
            ids,
        )
    }
    return [h for h in hits if h["doc_id"] in keep]


def _rrf(
    ranked: list[list[dict[str, Any]]],
    names: list[str],
    limit: int,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Reciprocal rank fusion at document level.

    Each side contributes a document's best chunk rank: score(doc) = sum over
    sides of 1/(RRF_K + rank of its first chunk in that list). Fusing chunk
    ids instead lets a long document's many chunks crowd each list and never
    adds one document's evidence across sides; measured on the library set
    that was worse than FTS alone (docs/eval/). One hit per document is
    returned, carrying the chunk that ranked best on the side that found it
    first, plus ``fts_rank`` / ``vec_rank`` (document ranks, None if absent).
    """
    fused: dict[int, dict[str, Any]] = {}
    for hits, name in zip(ranked, names, strict=True):
        weight = (weights or {}).get(name, 1.0)
        seen: set[int] = set()
        rank = 0
        for hit in hits:
            doc = hit["doc_id"]
            if doc in seen:
                continue
            seen.add(doc)
            rank += 1
            entry = fused.get(doc)
            if entry is None:
                entry = {**hit, "score": 0.0, **{f"{n}_rank": None for n in names}}
                fused[doc] = entry
            elif entry.get("chunk_id") is None and hit.get("chunk_id") is not None:
                # a field-only entry adopts the first chunk another side found
                for k in ("chunk_id", "kind", "heading", "page"):
                    entry[k] = hit[k]
            entry["score"] += weight / (RRF_K + rank)
            entry[f"{name}_rank"] = rank
    out = sorted(fused.values(), key=lambda h: -h["score"])
    return out[:limit]


@_serialized
def vec_status(con: sqlite3.Connection) -> dict[str, Any]:
    """Whether vectors are usable and how many exist, per model, plus the
    state of the current model's index file."""
    status: dict[str, Any] = {"available": vectors_available(), "rows": 0, "models": {}}
    rows = con.execute(
        "SELECT model, count(*) AS n FROM chunk_embeddings GROUP BY model"
    ).fetchall()
    status["models"] = {r["model"]: r["n"] for r in rows}
    status["rows"] = sum(status["models"].values())
    status["documents"] = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT model, count(*) FROM document_embeddings GROUP BY model"
        )
    }
    emb = embeddings.current()
    status["embedder"] = emb.name if emb else None
    status["index"] = None
    if emb is not None and vectors_available():
        idx = _index(emb.name, writable=False)
        if idx is not None:
            status["index"] = idx.stats()
        try:
            status["delta"] = delta_counts(emb.name)
        except Exception:  # noqa: BLE001 - a status must not fail on a stale file
            status["delta"] = None
        if status["index"] is not None and status["delta"]:
            # what a query can find: the main file plus the delta
            status["index"]["count"] += status["delta"]["chunks"]
    return status


@_serialized
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
) -> list[dict[str, Any]]:
    """Search returning compact snippets + ids (agent-shaped).

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
    ``page``; ``kind`` filters to one kind.

    ``rerank`` rescores the top ``RERANK_DEPTH`` hits with the configured
    cross-encoder (``prax.rerank``; None follows ``PRAX_RERANK``, which is
    off by default) and adds ``rerank_score``.
    """
    if kind is not None and kind not in chunking.KINDS:
        raise ValueError(f"kind must be one of {chunking.KINDS}")
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
    if domain:
        fetch *= 3
    hits = _search_hits(con, query, fetch, kind, mode, doctype)
    if domain:
        hits = _filter_domain(con, hits, domain)
    if reranker is not None and hits:
        hits = _apply_rerank(con, reranker, query, hits)
    return hits[:limit]


def _filter_domain(
    con: sqlite3.Connection, hits: list[dict[str, Any]], domain: str
) -> list[dict[str, Any]]:
    """Keep hits whose document is in ``domain``; a document without a
    domain set is in every module and stays."""
    ids = {h["doc_id"] for h in hits}
    if not ids:
        return hits
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        "SELECT id, json_extract(meta, '$.domains') FROM documents"
        f" WHERE id IN ({marks})",
        tuple(ids),
    ).fetchall()
    allowed = {r[0] for r in rows if not r[1] or domain in json.loads(r[1])}
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
) -> list[dict[str, Any]]:
    emb = embeddings.current() if mode != "fts" else None
    vectors_ready = (
        emb is not None
        and vectors_available()
        and _has_vectors(_index_path(emb.name))
        and _vec_count(con) > 0
    )
    if mode == "vec" and not vectors_ready:
        raise ValueError("vector search unavailable: no embedder, index or vectors")
    fetch = limit * 4 if doctype else limit
    # the query as terms with the library's own expansions of its acronyms;
    # one OR expression for recall, one AND expression for the tier that
    # wants every term present (only when there is more than one term)
    terms = expand_query(con, query)
    or_expr = _expr(terms, all_terms=False)
    and_expr = _expr(terms, all_terms=True) if len(terms) > 1 else None
    if mode == "fts":  # the raw chunk list, expanded but not fused
        hits = _fts_search(con, query, fetch, kind, expr=or_expr)
        return _finish(con, hits, query, limit, doctype, expr=or_expr)
    depth = max(limit * 3, RRF_DEPTH)
    # keyword lists: any term (recall), optionally every term, and the rare
    # terms alone: "adaa iir" must not be decided by the thousands of chunks
    # that say "iir"
    weights: dict[str, float] = {
        "fts_all": ALL_TERMS_WEIGHT,
        "fts_rare": RARE_TERMS_WEIGHT,
    }
    lists = [_fts_search(con, query, depth, kind, snippets=False, expr=or_expr)]
    names = ["fts"]
    if and_expr and ALL_TERMS_WEIGHT > 0:
        lists.append(
            _fts_search(con, query, depth, kind, snippets=False, expr=and_expr)
        )
        names.append("fts_all")
    rare = _rare_terms(con, terms) if RARE_TERMS_WEIGHT > 0 else []
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
            lists.append(_field_fts_search(con, query, depth, expr=or_expr))
            names.append("field")
            weights["field"] = _field_weight(query)
        fused = _rrf(lists, names, fetch, weights)
        return _finish(con, fused, query, limit, doctype, expr=or_expr)
    assert emb is not None
    vector = emb.embed_query(expanded_text(terms) if VEC_EXPAND else query)
    if mode == "vec":
        return _finish(
            con,
            _vec_search(con, vector, fetch, kind),
            query,
            limit,
            doctype,
            expr=or_expr,
        )
    lists.append(_vec_search(con, vector, depth, kind))
    names.append("vec")
    if kind is None:  # the field has no chunk kind to filter by
        lists.append(_field_fts_search(con, query, depth, expr=or_expr))
        names.append("field")
        weights["field"] = _field_weight(query)
        lists.append(_field_vec_search(con, emb.name, vector, depth))
        names.append("dvec")
    fused = _rrf(lists, names, fetch, weights)
    return _finish(con, fused, query, limit, doctype, expr=or_expr)


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
    _fill_chunks(con, hits, query, expr=expr)
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


def _vec_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]


@_serialized
def pending_embeddings(
    con: sqlite3.Connection, model: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Chunks without a vector from ``model``: ``{chunk_id, kind, text}``."""
    sql = (
        "SELECT c.id AS chunk_id, c.kind, c.text FROM chunks c"
        " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
        " WHERE e.chunk_id IS NULL OR e.model != ? ORDER BY c.id"
    )
    args: tuple[Any, ...] = (model,)
    if limit is not None:
        sql += " LIMIT ?"
        args = (model, limit)
    return [dict(r) for r in con.execute(sql, args)]


@_serialized
def count_pending_embeddings(con: sqlite3.Connection, model: str) -> int:
    """How many chunks ``pending_embeddings`` would return, without the text."""
    return con.execute(
        "SELECT count(*) FROM chunks c"
        " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
        " WHERE e.chunk_id IS NULL OR e.model != ?",
        (model,),
    ).fetchone()[0]


@_serialized
def store_embeddings(
    con: sqlite3.Connection,
    items: list[tuple[int, str | None, Any]],
    model: str,
) -> int:
    """Add ``(chunk_id, kind, vector)`` rows for ``model`` to its index
    (in memory until ``save_vectors``) and record them in ``chunk_embeddings``.
    Requires the usearch library; the batch job calls this."""
    if not vectors_available():
        raise RuntimeError("usearch is not installed; vectors cannot be stored")
    if not items:
        return 0
    idx = _delta(_index_path(model))
    ids = [cid for cid, _, _ in items]
    import numpy as np

    idx.add(ids, np.vstack([np.asarray(v, dtype=np.float32) for _, _, v in items]))
    con.executemany(
        "INSERT INTO chunk_embeddings (chunk_id, model) VALUES (?, ?)"
        " ON CONFLICT(chunk_id) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(cid, model) for cid in ids],
    )
    con.commit()
    return len(items)


@_serialized
def pending_document_embeddings(
    con: sqlite3.Connection, model: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Document fields without a vector from ``model``: ``{doc_id, text}``."""
    sql = (
        "SELECT f.rowid AS doc_id, f.field AS text FROM documents_fts f"
        " LEFT JOIN document_embeddings e ON e.doc_id = f.rowid"
        " WHERE e.doc_id IS NULL OR e.model != ? ORDER BY f.rowid"
    )
    args: tuple[Any, ...] = (model,)
    if limit is not None:
        sql += " LIMIT ?"
        args = (model, limit)
    return [dict(r) for r in con.execute(sql, args)]


@_serialized
def count_pending_document_embeddings(con: sqlite3.Connection, model: str) -> int:
    return con.execute(
        "SELECT count(*) FROM documents_fts f"
        " LEFT JOIN document_embeddings e ON e.doc_id = f.rowid"
        " WHERE e.doc_id IS NULL OR e.model != ?",
        (model,),
    ).fetchone()[0]


@_serialized
def store_document_embeddings(
    con: sqlite3.Connection, items: list[tuple[int, Any]], model: str
) -> int:
    """Add ``(doc_id, vector)`` rows to the document index of ``model`` (in
    memory until ``save_document_vectors``) and record them."""
    if not vectors_available():
        raise RuntimeError("usearch is not installed; vectors cannot be stored")
    if not items:
        return 0
    idx = _delta(_doc_index_path(model))
    import numpy as np

    ids = [d for d, _ in items]
    idx.add(ids, np.vstack([np.asarray(v, dtype=np.float32) for _, v in items]))
    con.executemany(
        "INSERT INTO document_embeddings (doc_id, model) VALUES (?, ?)"
        " ON CONFLICT(doc_id) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(d, model) for d in ids],
    )
    con.commit()
    return len(items)


DELTA_MERGE_AT = 50_000  # vectors in the delta before a merge is due


def _save_delta(path: Path) -> dict[str, Any]:
    """Write the delta beside ``path`` (small, nobody maps it) and merge it
    into the main file when it has grown past ``DELTA_MERGE_AT``."""
    delta = _delta(path)
    delta.save()
    if len(delta) >= DELTA_MERGE_AT or not path.exists():
        return _merge(path)  # a large delta, or no main file yet
    main = _open_index(path, writable=False)
    return {
        "count": (len(main) if main is not None else 0) + len(delta),
        "delta": len(delta),
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def _merge(path: Path) -> dict[str, Any]:
    """Fold the delta into the main file: load the main index writable, add
    the delta's vectors, save, drop this process's views, start an empty
    delta. Needs the main index in memory for the duration."""
    delta = _delta(path)
    main = _open_index(path, writable=True)
    assert main is not None
    n = len(delta)
    if n:
        import numpy as np

        keys = [int(k) for k in delta.all_keys()]
        vecs = np.vstack([delta.get(k) for k in keys])
        main.add(keys, vecs)
    for key in [(str(path), False), (str(_delta_path(path)), True)]:
        idx = _indexes.pop(key, None)
        if idx is not None:
            idx.close()
    main.save()
    _indexes.pop((str(path), True), None)
    main.close()
    dpath = _delta_path(path)
    if dpath.exists():
        dpath.unlink()
    reopened = _open_index(path, writable=False)
    return {
        "count": len(reopened) if reopened is not None else 0,
        "merged": n,
        "delta": 0,
        "bytes": path.stat().st_size,
    }


@_serialized
def save_document_vectors(model: str) -> dict[str, Any]:
    return _save_delta(_doc_index_path(model))


@_serialized
def save_vectors(model: str) -> dict[str, Any]:
    """Write ``model``'s new vectors (the delta) to disk; the main file is
    rewritten only when the delta is large (``merge_vectors``). The
    bookkeeping rows are committed as they are written, so a crash between
    two saves leaves rows that the next ``pending_embeddings`` run will not
    repeat; ``compact_vectors`` reconciles the two."""
    return _save_delta(_index_path(model))


@_serialized
def merge_vectors(model: str) -> dict[str, Any]:
    """Fold both deltas of ``model`` into their main files now."""
    return {
        "chunks": _merge(_index_path(model)),
        "documents": _merge(_doc_index_path(model)),
    }


@_serialized
def delta_counts(model: str) -> dict[str, int]:
    out = {}
    paths = (("chunks", _index_path(model)), ("documents", _doc_index_path(model)))
    for name, path in paths:
        dpath = _delta_path(path)
        idx = _indexes.get((str(dpath), True))
        if idx is None and dpath.exists():
            idx = _delta(path)
        out[name] = len(idx) if idx is not None else 0
    return out


@_serialized
def compact_vectors(con: sqlite3.Connection, model: str) -> dict[str, int]:
    """Reconcile the index with the bookkeeping: drop keys whose chunk is
    gone, and forget bookkeeping rows whose vector is missing from the
    index (so they get embedded again). Saves the index."""
    _merge(_index_path(model))
    idx = _index(model, writable=True)
    assert idx is not None
    live = {r[0] for r in con.execute("SELECT id FROM chunks")}
    booked = {
        r[0]
        for r in con.execute(
            "SELECT chunk_id FROM chunk_embeddings WHERE model = ?", (model,)
        )
    }
    keys = {int(k) for k in idx.all_keys()}
    stale = keys - live
    removed = idx.remove(stale)
    missing = booked - keys
    if missing:
        ids = list(missing)
        for i in range(0, len(ids), 500):
            part = ids[i : i + 500]
            marks = ",".join("?" * len(part))
            con.execute(
                f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({marks})", part
            )
        con.commit()
    _drop_index_views(model)
    idx.save()
    count = len(idx)
    _indexes.pop((str(_index_path(model)), True), None)
    idx.close()  # the writable copy is not kept around: the delta takes new vectors
    return {"removed_stale": removed, "forgot_missing": len(missing), "count": count}


CONTEXT_LIMIT = 8


CENTROID_CHUNKS = 64  # chunk vectors averaged for a document's similarity query


CENTROID_MIN_CHARS = 120  # shorter chunks are headers and template lines


@_serialized
def similar_documents(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    limit: int = CONTEXT_LIMIT,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Documents nearest to this one in vector space: the centroid of up to
    ``CENTROID_CHUNKS`` of its text chunks' vectors (spread over the
    document), one KNN, grouped by document, scored by the best hit.
    ``domain`` keeps the neighbours of one ontology module."""
    return _similar_documents(con, doc_id, limit=limit, domain=domain)


def _similar_documents(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    limit: int = CONTEXT_LIMIT,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Two views fused by reciprocal rank: the document-field vector's
    neighbours (what the document is; one KNN over the document index) and
    the chunk-centroid neighbours (what it says; one KNN over the chunk
    index, grouped by document). The centroid skips chunks under
    ``CENTROID_MIN_CHARS`` (boilerplate headers) that pull it toward every
    document sharing the same template."""
    emb = embeddings.current()
    if emb is None:
        return []
    import numpy as np  # the embed extra; only reachable when an index exists

    depth = max(40, 5 * limit)
    lists: list[list[int]] = []
    dpath = _doc_index_path(emb.name)
    if _has_vectors(dpath) and (fv := _get_vector(dpath, doc_id)) is not None:
        found = _knn(dpath, fv, depth + 1)
        lists.append([int(k) for k, _ in found if int(k) != doc_id][:depth])
    cpath = _index_path(emb.name)
    if _has_vectors(cpath):
        rows = con.execute(
            "SELECT c.id FROM chunks c JOIN chunk_embeddings e ON e.chunk_id = c.id"
            " WHERE c.doc_id = ? AND e.model = ? AND c.kind = 'text'"
            " AND length(c.text) >= ? ORDER BY c.seq",
            (doc_id, emb.name, CENTROID_MIN_CHARS),
        ).fetchall()
        ids = [r["id"] for r in rows]
        step = max(1, len(ids) // CENTROID_CHUNKS)
        vecs = [
            v
            for k in ids[::step][:CENTROID_CHUNKS]
            if (v := _get_vector(cpath, k)) is not None
        ]
        if vecs:
            centroid = np.mean(np.stack(vecs), axis=0)
            norm = float(np.linalg.norm(centroid)) or 1.0
            found = _knn(
                cpath,
                (centroid / norm).astype(np.float32),
                min(VEC_SEARCH_CAP, 40 * limit),
            )
            keys = [int(k) for k, _ in found]
            order: list[int] = []
            for i in range(0, len(keys), 500):
                part = keys[i : i + 500]
                marks = ",".join("?" * len(part))
                by_chunk = {
                    r["id"]: r["doc_id"]
                    for r in con.execute(
                        f"SELECT id, doc_id FROM chunks WHERE id IN ({marks})", part
                    )
                }
                for k in part:
                    d = by_chunk.get(k)
                    if d is not None and d != doc_id and d not in order:
                        order.append(d)
            lists.append(order[:depth])
    if not lists:
        return []
    scores: dict[int, float] = {}
    for ranked in lists:
        for rank, d in enumerate(ranked, 1):
            scores[d] = scores.get(d, 0.0) + 1.0 / (RRF_K + rank)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if domain:  # a wider slice, then the module's documents, then the cut
        ranked = [
            (did, score)
            for did, score in ranked[: max(limit * 5, 40)]
            if _filter_domain(con, [{"doc_id": did}], domain)
        ]
    out = []
    for did, score in ranked[:limit]:
        d = con.execute(
            "SELECT title, mime FROM documents WHERE id = ?", (did,)
        ).fetchone()
        if d is not None:
            out.append(
                {
                    "doc_id": did,
                    "title": d["title"],
                    "mime": d["mime"],
                    "score": round(score, 4),
                }
            )
    return out
