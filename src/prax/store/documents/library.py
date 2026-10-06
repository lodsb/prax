"""Which documents the library holds: retiring and bringing back,
duplicates and the captures of one page."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from prax.text import chunking, clutter

from ..base import (
    _NOW,
    MetaLike,
    _read_archive,
    _reading,
    _serialized,
    now,
)
from .meta import _put_meta, _refresh_document_field, get_meta
from .text import _write_chunks

# A document that should not be found any more (a duplicate capture, a
# page saved by mistake) is retired, not deleted: the row and the archived
# bytes stay, ``meta.retired`` says why and since when, its chunks and its
# retrieval field go (so search and the batch jobs pass it by), and its
# edges end (invariant 8). ``unretire_document`` brings the index back.


def is_retired(meta: MetaLike) -> bool:
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
    _put_meta(con, keeper, theirs)
    return {"moved_edges": moved_edges, "moved_items": moved_items}


@_serialized
def unretire_document(con: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    """Bring a retired document back into the index (its text artifact is
    re-chunked); the edges it lost stay history and a new extraction pass
    re-reads it."""
    meta = get_meta(con, doc_id)
    meta.pop("retired", None)
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


# ------------------------------------------------------------ clean-up
# Stage X: a set of documents chosen by a rule (``prax.text.clutter``),
# shown before anything happens, retired in one go under a run name and
# restored in one go by it. Retiring stays what it is: the row, the
# original and the text stay.


def _origin(row: sqlite3.Row) -> str:
    meta = json.loads(row["meta"] or "{}")
    return str((meta.get("origin") or {}).get("path") or row["original_path"] or "")


def cleanup_set(
    con: sqlite3.Connection, rule: str, *, folder: str | None = None
) -> list[tuple[int, int | None]]:
    """The live documents a clean-up rule picks, each with the document it
    duplicates (``same-text``) or None, in id order."""
    if rule not in clutter.RULES:
        raise ValueError(f"rule is one of {sorted(clutter.RULES)}")
    if rule == "folder" and len((folder or "").strip("/")) < 3:
        raise ValueError("a folder rule needs a folder")
    rows = con.execute(
        "SELECT id, title, original_path, text_hash, text_len, meta FROM documents"
        " WHERE json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ).fetchall()
    if rule == "same-text":
        first: dict[str, int] = {}
        out: list[tuple[int, int | None]] = []
        for r in rows:
            if not r["text_hash"] or (r["text_len"] or 0) < 300:
                continue  # a scan's empty page is the same text as many
            keeper = first.setdefault(r["text_hash"], int(r["id"]))
            if keeper != r["id"]:
                out.append((int(r["id"]), keeper))
        return out
    if rule == "folder":
        want = (folder or "").replace("\\", "/")
        return [
            (int(r["id"]), None) for r in rows if want in _origin(r).replace("\\", "/")
        ]
    return [
        (int(r["id"]), None)
        for r in rows
        if rule in clutter.kinds(_origin(r), r["title"] or "")
    ]


@_reading
def cleanup_preview(
    con: sqlite3.Connection,
    rule: str,
    *,
    folder: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """What a clean-up would take, before it does: how many documents, the
    facts they carry, how many are suspected or marked personal, and a
    sample with where each came from."""
    picked = cleanup_set(con, rule, folder=folder)
    ids = [i for i, _ in picked]
    edges = personal = 0
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        marks = ",".join("?" * len(part))
        edges += con.execute(
            "SELECT count(*) FROM edges WHERE valid_to IS NULL"
            f" AND source_doc IN ({marks})",
            part,
        ).fetchone()[0]
        personal += con.execute(
            "SELECT count(*) FROM documents WHERE sensitivity IS NOT NULL"
            f" AND id IN ({marks})",
            part,
        ).fetchone()[0]
    step = max(1, len(picked) // max(1, limit))
    sample = picked[::step][:limit]
    items = []
    for doc_id, keeper in sample:
        r = con.execute(
            "SELECT id, title, mime, original_path, meta FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        items.append(
            {
                "id": doc_id,
                "title": r["title"],
                "mime": r["mime"],
                "path": _origin(r),
                "duplicate_of": keeper,
            }
        )
    return {
        "rule": rule,
        "about": clutter.RULES[rule],
        "folder": folder,
        "total": len(picked),
        "edges": edges,
        "personal": personal,
        "items": items,
    }


@_serialized
def retire_set(
    con: sqlite3.Connection,
    rule: str,
    *,
    folder: str | None = None,
    by: str = "human",
) -> dict[str, Any]:
    """Retire every document a rule picks, under one run name: a duplicate
    as a duplicate (what it holds moves to the first copy), the rest
    plainly. Each keeps the facts it ended, by id, so ``restore_set``
    reopens exactly those."""
    picked = cleanup_set(con, rule, folder=folder)
    run = f"cleanup-{now().replace(':', '').replace('-', '')}-{rule}"
    reason = f"clean-up: {clutter.RULES[rule]}" + (f" ({folder})" if folder else "")
    retired = edges = moved = 0
    for doc_id, keeper in picked:
        ended = [
            int(r[0])
            for r in con.execute(
                "SELECT id FROM edges WHERE source_doc = ? AND valid_to IS NULL",
                (doc_id,),
            )
        ]
        got = retire_document(con, doc_id, reason=reason, duplicate_of=keeper, by=by)
        meta = get_meta(con, doc_id)
        meta["retired"]["run"] = run
        if keeper is None:
            meta["retired"]["ended"] = ended
        _put_meta(con, doc_id, meta)
        con.commit()
        retired += 1
        edges += int(got["edges"])
        moved += int(got.get("moved_edges") or 0)
    return {
        "run": run,
        "rule": rule,
        "retired": retired,
        "edges_ended": edges,
        "edges_moved": moved,
    }


@_reading
def cleanup_runs(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """The clean-ups done, newest first: what a restore can take back."""
    rows = con.execute(
        "SELECT json_extract(meta, '$.retired.run') AS run,"
        " min(json_extract(meta, '$.retired.at')) AS at,"
        " min(json_extract(meta, '$.retired.reason')) AS reason, count(*) AS n"
        " FROM documents WHERE json_extract(meta, '$.retired.run') IS NOT NULL"
        " GROUP BY run ORDER BY at DESC"
    ).fetchall()
    return [
        {"run": r["run"], "at": r["at"], "reason": r["reason"], "documents": r["n"]}
        for r in rows
    ]


@_serialized
def restore_set(con: sqlite3.Connection, run: str) -> dict[str, Any]:
    """Bring back every document a clean-up retired, with the facts it
    ended: an undo of the clean-up's own invalidation, so the ended edges
    are reopened rather than extracted again. A duplicate comes back
    without what moved to its first copy, which keeps it."""
    rows = con.execute(
        "SELECT id FROM documents WHERE json_extract(meta, '$.retired.run') = ?",
        (run,),
    ).fetchall()
    if not rows:
        raise KeyError(f"no clean-up named {run}")
    restored = reopened = 0
    for r in rows:
        doc_id = int(r["id"])
        ended = list((get_meta(con, doc_id).get("retired") or {}).get("ended") or [])
        unretire_document(con, doc_id)
        for start in range(0, len(ended), 500):
            part = ended[start : start + 500]
            reopened += con.execute(
                "UPDATE edges SET valid_to = NULL WHERE source_doc = ? AND id IN"
                f" ({','.join('?' * len(part))})",
                [doc_id, *part],
            ).rowcount
        con.commit()
        restored += 1
    return {"run": run, "restored": restored, "edges_reopened": reopened}
