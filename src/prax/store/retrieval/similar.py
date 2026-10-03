"""Documents like a document (its chunk vectors' centroid), the
documents a term occurs in, and the languages a phrase is used in."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax.ml import embeddings

from ..base import (
    _doc_index_path,
    _get_vector,
    _guards,
    _has_vectors,
    _index_path,
    _knn,
    _reading,
    _scrubbed,
)
from ..documents.reads import NOT_A_PAGE
from .fusion import (
    _filter_domain,
)
from .legs import (
    RRF_K,
    VEC_SEARCH_CAP,
)

CONTEXT_LIMIT = 8


CENTROID_CHUNKS = 64  # chunk vectors averaged for a document's similarity query


CENTROID_MIN_CHARS = 120  # shorter chunks are headers and template lines


@_guards("doc", list)
@_scrubbed
@_reading
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
    emb = embeddings.serving()
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
    fused = sorted(scores.items(), key=lambda kv: -kv[1])
    if domain:  # a wider slice, then the module's documents, then the cut
        fused = [
            (did, score)
            for did, score in fused[: max(limit * 5, 40)]
            if _filter_domain(con, [{"doc_id": did}], domain)
        ]
    out = []
    for did, score in fused[:limit]:
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


@_reading
def phrase_languages(con: sqlite3.Connection, match: str, lang: str) -> tuple[int, int]:
    """How many documents hold this MATCH expression in their chunks in
    ``lang``, and how many in another language that is known, prax's own
    pages left out. Raises ``sqlite3.OperationalError`` for an expression
    the index cannot parse."""

    row = con.execute(
        "SELECT count(DISTINCT d.id) FILTER (WHERE lang = ?),"
        " count(DISTINCT d.id) FILTER (WHERE lang <> ?)"
        " FROM (SELECT d.id, json_extract(d.meta, '$.lang') AS lang"
        " FROM chunks_fts f JOIN chunks c ON c.id = f.rowid"
        " JOIN documents d ON d.id = c.doc_id"
        f" WHERE chunks_fts MATCH ? AND {NOT_A_PAGE}) d",
        (lang, lang, match),
    ).fetchone()
    return int(row[0] or 0), int(row[1] or 0)
