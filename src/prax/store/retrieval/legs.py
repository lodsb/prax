"""The search legs: BM25 and KNN over the chunks and over the document
field, each a ranked list, and the reciprocal rank fusion that merges
them at document level."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax import packs
from prax.ml import embeddings
from prax.text import chunking

from ..base import (
    _ASIDE,
    _doc_index_path,
    _has_vectors,
    _index_path,
    _knn,
    domain_clause,
)
from ..documents import DOCTYPES, _chunk_shape, find_chunk
from .query import (
    Scope,
    _fts_query,
)

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


SEARCH_MODES = ("hybrid", "fts", "vec")


# the kinds a chunk may have: the chunker's and the packs' (docs/packs.md)
CHUNK_KINDS = chunking.KINDS + packs.kinds()

WIDE_SCOPE = 0.5  # a scope holding this share of the documents is filtered after
WIDE_DRAW = 3  # and the ranked list is drawn this many times deeper for it

_SHARES: dict[tuple[Scope, int], float] = {}


def _share(con: sqlite3.Connection, scope: Scope) -> float:
    """The share of the library's documents a scope holds, remembered
    while the library holds as many documents."""
    total = int(con.execute("SELECT count(*) FROM documents").fetchone()[0] or 0)
    key = (scope, total)
    if key not in _SHARES:
        where, args = _in_domains(con, scope)
        n = con.execute(
            f"SELECT count(*) FROM documents d WHERE 1 = 1{where}", args
        ).fetchone()[0]
        _SHARES[key] = (n or 0) / max(total, 1)
    return _SHARES[key]


def _in_domains(
    con: sqlite3.Connection, scope: Scope | None
) -> tuple[str, tuple[str, ...]]:
    """A WHERE clause keeping the documents of ``scope`` (and those with
    no domain set, which are in every module), and its arguments."""
    if not scope:
        return "", ()
    clause, args = domain_clause(con, scope, unset=True)
    return clause, tuple(args)


def _fts_search(
    con: sqlite3.Connection,
    query: str,
    limit: int,
    kind: str | None,
    *,
    snippets: bool = True,
    expr: str | None = None,
    scope: Scope | None = None,
) -> list[dict[str, Any]]:
    """BM25 over chunks. ``snippets=False`` skips the snippet() call, which
    reads every matched chunk's text and dominates the cost of deep lists;
    ``_fts_snippets`` fills them in for the few hits that survive fusion.
    ``scope`` keeps the chunks of documents in those domains, inside the
    ranking: a sense like `apple` matches thousands of chunks elsewhere,
    and a filter after the ranking would leave none."""
    expr = expr or _fts_query(query)
    if expr is None:
        return []
    snippet_col = (
        "snippet(chunks_fts, 0, '[', ']', '…', 12)"
        if snippets
        else "substr(c.text, 1, 160)"
    )
    where, where_args = _in_domains(con, scope)
    # a scope that is most of the library is dense among the best matches:
    # rank in the index, draw deeper, filter after. Inside the ranking it
    # joined every matched chunk first, and "signal synthesis" scoped to
    # research took 940 ms against 83 (2026-09-26). A small scope (the
    # kitchen, where `apple` has thousands of chunks elsewhere) keeps the
    # filter inside
    wide = scope is not None and bool(scope) and _share(con, scope) >= WIDE_SCOPE
    if kind is None and not snippets and (not scope or wide):
        # rank inside the keyword index alone, then join the survivors: the
        # joins to chunks and documents ran for every matched row before
        # the sort, and doubled a common query's cost (51 ms against 25 on
        # "feedback delay network" over 3.4 M chunks, 2026-09-22). The
        # aside kinds are dropped after; the inner list is three times as
        # deep so they seldom cost a slot (they are few: reference entries
        # and ask blocks)
        sql = f"""
            SELECT c.id AS chunk_id, c.doc_id, d.title,
                   {snippet_col} AS snippet,
                   f.score,
                   c.kind, c.locator, c.heading, c.data
            FROM (SELECT rowid, bm25(chunks_fts) AS score FROM chunks_fts
                  WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?) f
            JOIN chunks c ON c.id = f.rowid
            JOIN documents d ON d.id = c.doc_id
            WHERE 1 = 1 {_ASIDE}{where}
            ORDER BY f.score LIMIT ?
            """
        deeper = limit * (3 * WIDE_DRAW if wide else 3)
        args: tuple[Any, ...] = (expr, deeper, *where_args, limit)
    else:
        # a kind asked for is a small share of the chunks (tables, figures),
        # so its filter has to sit inside the ranking; snippet() needs the
        # keyword index in the outer query (the raw keyword mode): the join
        # stays there for both
        kind_clause = "AND c.kind = ?" if kind is not None else _ASIDE
        sql = f"""
            SELECT c.id AS chunk_id, c.doc_id, d.title,
                   {snippet_col} AS snippet,
                   bm25(chunks_fts) AS score,
                   c.kind, c.locator, c.heading, c.data
            FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                            JOIN documents d ON d.id = c.doc_id
            WHERE chunks_fts MATCH ? {kind_clause}{where}
            ORDER BY score LIMIT ?
            """
        args = (
            (expr, kind, *where_args, limit)
            if kind is not None
            else (expr, *where_args, limit)
        )
    rows = con.execute(sql, args).fetchall()
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
        kind_clause = "AND c.kind = ?" if kind is not None else _ASIDE
        args: tuple[Any, ...] = (*part, kind) if kind is not None else tuple(part)
        rows = con.execute(
            f"""
            SELECT c.id AS chunk_id, c.doc_id, d.title, c.kind, c.locator,
                   c.heading, c.data, substr(c.text, 1, 160) AS head
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
    con: sqlite3.Connection,
    query: str,
    limit: int,
    expr: str | None = None,
    scope: Scope | None = None,
) -> list[dict[str, Any]]:
    """BM25 over the document field; hits carry no chunk yet."""
    expr = expr or _fts_query(query)
    if expr is None:
        return []
    where, where_args = _in_domains(con, scope)
    rows = con.execute(
        f"""
        SELECT f.rowid AS doc_id, d.title,
               snippet(documents_fts, 0, '[', ']', '…', 14) AS snippet,
               bm25(documents_fts) AS score
        FROM documents_fts f JOIN documents d ON d.id = f.rowid
        WHERE documents_fts MATCH ?{where} ORDER BY score LIMIT ?
        """,
        (expr, *where_args, limit),
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
        "figure": None,
    }


def _fill_chunks(
    con: sqlite3.Connection,
    hits: list[dict[str, Any]],
    query: str,
) -> None:
    """A hit that came from the document field alone gets the document's
    chunk that speaks to the query (``documents.find_chunk``), so every
    hit opens somewhere; the field snippet stays, it says why the
    document matched."""
    for h in hits:
        if h.get("chunk_id") is not None:
            continue
        found = find_chunk(con, h["doc_id"], query)
        if found is None:
            continue
        h["chunk_id"] = found["chunk_id"]
        for k in ("kind", "heading", "page", "figure"):
            h[k] = found[k]


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
                for k in ("chunk_id", "kind", "heading", "page", "figure"):
                    entry[k] = hit.get(k)
            entry["score"] += weight / (RRF_K + rank)
            entry[f"{name}_rank"] = rank
    out = sorted(fused.values(), key=lambda h: -h["score"])
    return out[:limit]
