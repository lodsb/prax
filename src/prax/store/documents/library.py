"""Which documents the library holds: retiring and bringing back,
duplicates and the captures of one page."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from prax.text import chunking

from ..base import (
    _NOW,
    LIVE,
    MetaLike,
    NotFound,
    _end_edges,
    _read_archive,
    _reading,
    _restore_run,
    _serialized,
    document_hidden,
    meta_of,
    now,
    run_name,
)
from .meta import _put_meta, _refresh_document_field, get_meta
from .text import _write_chunks

# A document that should not be found any more (a duplicate capture, a
# page saved by mistake) is retired, not deleted: the row and the archived
# bytes stay, ``meta.retired`` says why and since when, its chunks and its
# retrieval field go (so search and the batch jobs pass it by), and its
# edges end (invariant 8) under a run of its own, ``meta.retired.run``.
# ``unretire_document`` brings the index back and states those edges again
# (``_restore_run``), what held them moved onto the new ones.


def is_retired(meta: MetaLike) -> bool:
    """The Python half of ``base.live``: a document is retired when its
    meta has the key, whatever it holds."""
    return meta.get("retired") is not None


@_serialized
def retire_document(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    reason: str,
    duplicate_of: int | None = None,
    replaced_by: int | None = None,
    carry: tuple[str, ...] = (),
    by: str = "human",
    run: str | None = None,
) -> dict[str, Any]:
    """Take a document out of the index and the graph, keeping row, bytes
    and text artifact. Returns what went: chunks, edges, review items.

    With ``duplicate_of`` the document is a second copy of ``duplicate_of``
    (a page sent twice, two Zotero snapshots): what it holds and the
    keeper lacks moves over first — edges with their evidence and
    producer, open review items, tags, domains, the summary, the
    extraction stamp — and only what both hold is ended here. Nothing is
    deleted; the union is what a person would have wanted from one
    document. Refused (ValueError): a ``duplicate_of`` that was itself
    retired into this document, near or far, which would leave a link to
    either with no current copy (``current_of``).

    With ``replaced_by`` the document is an older text of ``replaced_by``
    (a project's note synced again, a repository's README imported
    again): a link to it lands on the new one (``meta.retired.of``) and
    its nodes move there, but nothing of what was read from its text
    moves. Its facts end with it, and the new text is extracted and
    summarised from its own words. The facts of the producers in
    ``carry`` move to the new one: those the caller states again from
    every text itself (a sync's links and status, ``sync_facts``), which
    then keeps, ends or adds them against the new text. A union would
    have handed the new text the old extraction stamp, so a correction
    added to a note never reached the graph (the quality review of
    2026-11-11, finding 1).

    The edges are ended under ``run`` (a clean-up's, which retires a set
    under one) or a run of the retirement's own, kept as
    ``meta.retired.run``, which ``unretire_document`` undoes."""
    if duplicate_of is not None and replaced_by is not None:
        raise ValueError("a duplicate or a replacement, not both")
    of = duplicate_of if duplicate_of is not None else replaced_by
    meta = get_meta(con, doc_id)
    if of is not None and get_meta(con, of) is None:
        raise NotFound(f"no such document: {of}")
    if of is not None and doc_id in _replacements(con, of):
        raise ValueError(f"document {of} was retired into {doc_id}: unretire it first")
    moved: dict[str, int] = {"moved_edges": 0, "moved_items": 0}
    if duplicate_of is not None and duplicate_of != doc_id:
        moved = _join_duplicate(con, doc_id, duplicate_of)
        meta = get_meta(con, doc_id)
    elif replaced_by is not None and replaced_by != doc_id and carry:
        moved = {"moved_edges": _move_edges(con, doc_id, replaced_by, carry)}
    # retired again: its edges were ended under the first run, which is the
    # one the undo needs (the safety review of 2026-11-11)
    run = (
        (meta.get("retired") or {}).get("run")
        or run
        or run_name("retire", doc_id, unique=True)
    )
    meta["retired"] = {
        "at": now(),
        "reason": reason,
        "of": of,
        "by": by,
        "run": run,
    }
    chunks = con.execute(
        "SELECT count(*) FROM chunks WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    con.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
    con.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
    edges = len(
        _end_edges(
            con,
            [
                int(r[0])
                for r in con.execute(
                    "SELECT id FROM edges WHERE source_doc = ? AND valid_to IS NULL",
                    (doc_id,),
                )
            ],
            run=run,
        )
    )
    items = con.execute(
        f"UPDATE review_queue SET resolution = 'dropped', resolved_at = {_NOW}"
        " WHERE source_doc = ? AND resolution IS NULL",
        (doc_id,),
    ).rowcount
    _move_nodes_off(con, doc_id, of)
    _put_meta(con, doc_id, meta)
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
    moved_edges = _move_edges(con, doc_id, keeper)
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
    _put_meta(con, keeper, theirs)
    return {"moved_edges": moved_edges, "moved_items": moved_items}


def _move_edges(
    con: sqlite3.Connection,
    doc_id: int,
    keeper: int,
    producers: tuple[str, ...] | None = None,
) -> int:
    """Source ``doc_id``'s live edges (of ``producers``, or all) to
    ``keeper`` where the keeper does not state the same fact; returns how
    many moved. Does not commit."""
    by = f" AND e.producer IN ({','.join('?' * len(producers))})" if producers else ""
    moved = 0
    for e in con.execute(
        "SELECT e.id, e.src, e.rel, e.dst FROM edges e"
        " WHERE e.source_doc = ? AND e.valid_to IS NULL" + by,
        (doc_id, *(producers or ())),
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
            moved += 1
    return moved


REPLACED_HOPS = 20  # replacements a retired document's link follows, at most


@_reading
def current_of(con: sqlite3.Connection, doc_id: int) -> dict[str, Any] | None:
    """Where a link to a retired document lands now: ``{doc_id, title,
    hops}``, the end of the chain of documents that replaced it
    (``meta.retired.of``: a newer sync of a project's file, the copy a
    duplicate was retired into), or None for a document no one replaced.

    A synced note that changes is a new document (its id is its bytes,
    invariant 2), so a page that linked the old id would otherwise open a
    copy two syncs old. The chain stops before a document the viewer may
    not see. A chain that comes back on itself has no current copy (None):
    ``retire_document`` refuses to make one, and a loop written before it
    did would send a reader round it for ever."""
    if document_hidden(con, doc_id):
        return None
    chain = _replacements(con, doc_id)
    if chain and chain[-1] is None:
        return None
    at: int | None = None
    for nxt in chain:
        if nxt is None or document_hidden(con, nxt):
            break
        at = nxt
    if at is None:
        return None
    row = con.execute("SELECT title FROM documents WHERE id = ?", (at,)).fetchone()
    if row is None:
        return None
    return {"doc_id": at, "title": row[0], "hops": chain.index(at) + 1}


def _move_nodes_off(
    con: sqlite3.Connection, doc_id: int, duplicate_of: int | None
) -> int:
    """Move the nodes that are ``doc_id`` (``entities.doc_id``) to a live
    document of their name as it is retired: the copy it is a duplicate of
    (a re-synced note), else the live namesake of the lowest id. A node
    kept the retired copy, so a project's members listed the old version
    and its staleness was missed (the safety review of 2026-11-11). A node
    with no live namesake stays where it is. Does not commit."""
    n = 0
    for ent, name in con.execute(
        "SELECT id, name FROM entities WHERE doc_id = ?", (doc_id,)
    ).fetchall():
        row = con.execute(
            f"SELECT id FROM documents WHERE title = ? AND id != ? AND {LIVE}"
            " ORDER BY id = ? DESC, id LIMIT 1",
            (name, doc_id, duplicate_of),
        ).fetchone()
        if row is not None:
            con.execute(
                "UPDATE entities SET doc_id = ? WHERE id = ?", (int(row[0]), ent)
            )
            n += 1
    return n


def _replacements(con: sqlite3.Connection, doc_id: int) -> list[int | None]:
    """The documents that replaced ``doc_id`` in turn (``meta.retired.of``),
    at most ``REPLACED_HOPS``; a last None when the chain comes back on a
    document already in it."""
    seen = {doc_id}
    out: list[int | None] = []
    here = doc_id
    for _ in range(REPLACED_HOPS):
        row = con.execute(
            "SELECT json_extract(meta, '$.retired.of') FROM documents WHERE id = ?",
            (here,),
        ).fetchone()
        if row is None or row[0] is None:
            break
        here = int(row[0])
        if here in seen:
            out.append(None)
            break
        seen.add(here)
        out.append(here)
    return out


@_serialized
def unretire_document(con: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    """Bring a retired document back into the index (its text artifact is
    re-chunked) and state the edges its retirement ended again, as new
    edges (``_restore_run``: the ended ones stay history), with what held
    the old ids moved onto them. An edge a duplicate's retirement moved to
    its keeper stays there. A retirement from before its run was recorded
    (2026-11-10) brings no edges back: a new extraction pass re-reads it."""
    meta = get_meta(con, doc_id)
    retired = meta.pop("retired", None) or {}
    restored = (
        _restore_run(con, retired["run"], source_doc=doc_id)
        if retired.get("run")
        else {"restated": 0}
    )
    _put_meta(con, doc_id, meta)
    n = 0
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row and row["text_hash"]:
        text = _read_archive(row["text_hash"]).decode("utf-8")
        n = _write_chunks(con, doc_id, text)
        _refresh_document_field(con, doc_id)
    con.commit()
    return {"doc_id": doc_id, "chunks": n, "edges": restored["restated"]}


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
        f"SELECT id, meta FROM documents WHERE source_url = ? AND {LIVE} ORDER BY id",
        (url,),
    ):
        meta = meta_of(r["meta"])
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
    _put_meta(con, doc_id, meta)
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
        f" AND {LIVE} ORDER BY id"
    ):
        groups.setdefault(r["source_url"], []).append(
            {"doc_id": r["id"], "meta": meta_of(r["meta"])}
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


# ------------------------------------------------------------ clean-up
# Stage X: a set of documents chosen by a rule (``prax.text.clutter``),
# shown before anything happens, retired in one go under a run name and
# restored in one go by it. Retiring stays what it is: the row, the
# original and the text stay.
