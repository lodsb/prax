"""Which documents the library holds: retiring and bringing back,
duplicates and the captures of one page."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from prax import chunking

from ..base import (
    _NOW,
    _read_archive,
    _serialized,
    now,
)
from .meta import _refresh_document_field, get_meta
from .text import _write_chunks

# A document that should not be found any more (a duplicate capture, a
# page saved by mistake) is retired, not deleted: the row and the archived
# bytes stay, ``meta.retired`` says why and since when, its chunks and its
# retrieval field go (so search and the batch jobs pass it by), and its
# edges end (invariant 8). ``unretire_document`` brings the index back.


def is_retired(meta: dict[str, Any]) -> bool:
    return bool(meta.get("retired"))


@_serialized
def retire_document(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    reason: str,
    duplicate_of: int | None = None,
    by: str = "human",
) -> dict[str, Any]:
    """Take a document out of the index and the graph, keeping row, bytes
    and text artifact. Returns what went: chunks, edges, review items.

    With ``duplicate_of`` the document is a second copy of ``duplicate_of``
    (a page sent twice, two Zotero snapshots): what it holds and the
    keeper lacks moves over first — edges with their evidence and
    producer, open review items, tags, domains, the summary, the
    extraction stamp — and only what both hold is ended here. Nothing is
    deleted; the union is what a person would have wanted from one
    document."""
    meta = get_meta(con, doc_id)
    if duplicate_of is not None and get_meta(con, duplicate_of) is None:
        raise KeyError(f"no such document: {duplicate_of}")
    moved: dict[str, int] = {"moved_edges": 0, "moved_items": 0}
    if duplicate_of is not None and duplicate_of != doc_id:
        moved = _join_duplicate(con, doc_id, duplicate_of)
        meta = get_meta(con, doc_id)
    meta["retired"] = {
        "at": now(),
        "reason": reason,
        "of": duplicate_of,
        "by": by,
    }
    chunks = con.execute(
        "SELECT count(*) FROM chunks WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    con.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
    con.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
    edges = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE source_doc = ? AND valid_to IS NULL",
        (doc_id,),
    ).rowcount
    items = con.execute(
        f"UPDATE review_queue SET resolution = 'dropped', resolved_at = {_NOW}"
        " WHERE source_doc = ? AND resolution IS NULL",
        (doc_id,),
    ).rowcount
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return {
        "doc_id": doc_id,
        "chunks": chunks,
        "edges": edges,
        "review_items": items,
        **moved,
    }


def _join_duplicate(
    con: sqlite3.Connection, doc_id: int, keeper: int
) -> dict[str, int]:
    """The union: what ``doc_id`` holds and ``keeper`` lacks becomes the
    keeper's (see ``retire_document``); returns what moved."""
    moved_edges = 0
    for e in con.execute(
        "SELECT e.id, e.src, e.rel, e.dst FROM edges e"
        " WHERE e.source_doc = ? AND e.valid_to IS NULL",
        (doc_id,),
    ).fetchall():
        held = con.execute(
            "SELECT 1 FROM edges WHERE source_doc = ? AND src = ? AND rel = ?"
            " AND dst = ? AND valid_to IS NULL LIMIT 1",
            (keeper, e["src"], e["rel"], e["dst"]),
        ).fetchone()
        if held is None:
            con.execute(
                "UPDATE edges SET source_doc = ? WHERE id = ?", (keeper, e["id"])
            )
            moved_edges += 1
    moved_items = 0
    for it in con.execute(
        "SELECT id, src, rel, dst FROM review_queue"
        " WHERE source_doc = ? AND resolved_at IS NULL",
        (doc_id,),
    ).fetchall():
        held = con.execute(
            "SELECT 1 FROM review_queue WHERE source_doc = ? AND src = ? AND rel = ?"
            " AND dst = ? AND resolved_at IS NULL LIMIT 1",
            (keeper, it["src"], it["rel"], it["dst"]),
        ).fetchone()
        if held is None:
            con.execute(
                "UPDATE review_queue SET source_doc = ? WHERE id = ?",
                (keeper, it["id"]),
            )
            moved_items += 1
    mine, theirs = get_meta(con, doc_id), get_meta(con, keeper)
    tags = list(theirs.get("tags") or [])
    for t in mine.get("tags") or []:
        if t not in tags:
            tags.append(t)
    if tags:
        theirs["tags"] = tags
    if mine.get("domains") and not theirs.get("domains"):
        theirs["domains"] = list(mine["domains"])
        theirs["domains_by"] = mine.get("domains_by", "rule")
    for key in ("summary", "extraction", "promote"):
        if mine.get(key) and not theirs.get(key):
            theirs[key] = mine[key]
    theirs.setdefault("recaptured", []).append(
        {
            "at": now(),
            "session": (mine.get("capture") or {}).get("session"),
            "by": (mine.get("capture") or {}).get("by"),
            "was": doc_id,
        }
    )
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(theirs), keeper)
    )
    return {"moved_edges": moved_edges, "moved_items": moved_items}


@_serialized
def unretire_document(con: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    """Bring a retired document back into the index (its text artifact is
    re-chunked); the edges it lost stay history and a new extraction pass
    re-reads it."""
    meta = get_meta(con, doc_id)
    meta.pop("retired", None)
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    n = 0
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row and row["text_hash"]:
        text = _read_archive(row["text_hash"]).decode("utf-8")
        n = _write_chunks(con, doc_id, text)
        _refresh_document_field(con, doc_id)
    con.commit()
    return {"doc_id": doc_id, "chunks": n}


def fingerprint_text(text: str) -> frozenset[str]:
    """The chunk fingerprint of a text: a hash per chunk the chunker would
    make, so two captures of one page compare by what they say, not by
    their bytes (a page's markup changes between two visits, its text
    seldom does)."""
    rows = chunking.rows(chunking.chunk(text))
    # figure chunks are left out on both sides (``chunk_fingerprint``): a
    # page with ten figures compared at 35/45 before, and identical
    # captures of it counted as different pages
    return frozenset(
        hashlib.sha1(" ".join(str(r[0]).split()).encode("utf-8")).hexdigest()
        for r in rows
        if str(r[0]).strip() and r[1] != "figure"
    )


def chunk_fingerprint(con: sqlite3.Connection, doc_id: int) -> frozenset[str]:
    """The fingerprint of an indexed document, from its chunks."""
    return frozenset(
        hashlib.sha1(" ".join(t.split()).encode("utf-8")).hexdigest()
        for (t,) in con.execute(
            "SELECT text FROM chunks WHERE doc_id = ? AND kind != 'figure'", (doc_id,)
        )
        if t.strip()
    )


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two fingerprints; 1.0 for the same text."""
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


DUPLICATE_THRESHOLD = 0.9


def live_captures_of(con: sqlite3.Connection, url: str) -> list[dict[str, Any]]:
    """The live (not retired) captures of one canonical URL, oldest first."""
    out = []
    for r in con.execute(
        "SELECT id, meta FROM documents WHERE source_url = ?"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id",
        (url,),
    ):
        meta = json.loads(r["meta"] or "{}")
        out.append({"doc_id": r["id"], "meta": meta})
    return out


def capture_rank(meta: dict[str, Any]) -> tuple[int, int]:
    """Which of two captures of one page to keep: a snapshot over a bare
    DOM, an extracted one over one not yet read; ties go to the older."""
    mode = (meta.get("capture") or {}).get("mode")
    return (1 if mode == "snapshot" else 0, 1 if meta.get("extraction") else 0)


@_serialized
def note_recapture(
    con: sqlite3.Connection, doc_id: int, *, session: str | None, by: str | None
) -> None:
    """The page was sent again and said the same: remembered on the
    document, no new row."""
    meta = get_meta(con, doc_id)
    again = meta.setdefault("recaptured", [])
    again.append(
        {
            "at": now(),
            "session": session,
            "by": by,
        }
    )
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()


def dedupe_captures(
    con: sqlite3.Connection,
    *,
    threshold: float = DUPLICATE_THRESHOLD,
    commit: bool = True,
) -> dict[str, Any]:
    """Among the live captures of each URL, keep one (``capture_rank``)
    and retire the others whose text fingerprint matches it. Two captures
    of a page that changed in between both stay. Returns the groups."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in con.execute(
        "SELECT id, source_url, meta FROM documents WHERE source_url IS NOT NULL"
        " AND json_extract(meta, '$.source') = 'capture'"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ):
        groups.setdefault(r["source_url"], []).append(
            {"doc_id": r["id"], "meta": json.loads(r["meta"] or "{}")}
        )
    report: dict[str, Any] = {"groups": [], "retired": 0, "kept_apart": 0}
    for url, docs in groups.items():
        if len(docs) < 2:
            continue
        keeper = max(docs, key=lambda d: (capture_rank(d["meta"]), -d["doc_id"]))
        kfp = chunk_fingerprint(con, keeper["doc_id"])
        gone, apart = [], []
        for d in docs:
            if d is keeper:
                continue
            s = similarity(kfp, chunk_fingerprint(con, d["doc_id"]))
            if s >= threshold:
                gone.append(d["doc_id"])
                if commit:
                    retire_document(
                        con,
                        d["doc_id"],
                        reason="duplicate capture",
                        duplicate_of=keeper["doc_id"],
                        by="dedupe",
                    )
            else:
                apart.append((d["doc_id"], round(s, 2)))
        report["groups"].append(
            {"url": url, "keep": keeper["doc_id"], "retire": gone, "apart": apart}
        )
        report["retired"] += len(gone)
        report["kept_apart"] += len(apart)
    return report
