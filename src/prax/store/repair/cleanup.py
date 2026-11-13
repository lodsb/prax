"""Cleaning up in bulk (stage X): a rule picks a set of documents, the
preview says what it would take, a run retires it in one go and a
restore brings it back with the facts it held: the set's run is the run
each retirement ends its facts under, which each unretire states again
(``retire_document``, ``unretire_document``)."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax.text import clutter

from ..base import LIVE, NotFound, _reading, _serialized, meta_of, rows_in, run_name
from ..documents import (
    live_photo_clip,
    record_old_endings,
    retire_document,
    unretire_document,
)


def _origin(row: sqlite3.Row) -> str:
    meta = meta_of(row["meta"])
    return str((meta.get("origin") or {}).get("path") or row["original_path"] or "")


def _only_clip(con: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """Whether a document is a recording every place of which is a Live
    Photo's clip (``live_photo_clip``): a place, not a document."""
    if not str(row["mime"] or "").startswith("video/"):
        return False
    where = con.execute(
        "SELECT p.root, p.path FROM places p JOIN documents d ON d.hash = p.sha256"
        " WHERE d.id = ? AND p.gone IS NULL",
        (row["id"],),
    ).fetchall()
    return bool(where) and all(live_photo_clip(con, w[0], w[1]) for w in where)


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
        "SELECT id, title, mime, original_path, text_hash, text_len, meta"
        f" FROM documents WHERE {LIVE} ORDER BY id"
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
    if rule == "unreadable":
        return [
            (int(r["id"]), None)
            for r in rows
            if (meta_of(r["meta"]).get("photo") or {}).get("error")
        ]
    if rule == "clips":
        return [(int(r["id"]), None) for r in rows if _only_clip(con, r)]
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
    edges = sum(
        r[0]
        for r in rows_in(
            con,
            "SELECT count(*) FROM edges WHERE valid_to IS NULL"
            " AND source_doc IN ({ids})",
            ids,
        )
    )
    personal = sum(
        r[0]
        for r in rows_in(
            con,
            "SELECT count(*) FROM documents WHERE sensitivity IS NOT NULL"
            " AND id IN ({ids})",
            ids,
        )
    )
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
    plainly. The facts a plain one held are ended under the run
    (``edge_endings``), so ``restore_set`` states exactly those again."""
    picked = cleanup_set(con, rule, folder=folder)
    run = run_name("cleanup", rule)
    reason = f"clean-up: {clutter.RULES[rule]}" + (f" ({folder})" if folder else "")
    retired = edges = moved = 0
    for doc_id, keeper in picked:
        got = retire_document(
            con, doc_id, reason=reason, duplicate_of=keeper, by=by, run=run
        )
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
        " FROM documents"
        " WHERE json_extract(meta, '$.retired.run') LIKE 'cleanup-%'"
        " GROUP BY run ORDER BY at DESC"
    ).fetchall()
    return [
        {"run": r["run"], "at": r["at"], "reason": r["reason"], "documents": r["n"]}
        for r in rows
    ]


@_serialized
def restore_set(con: sqlite3.Connection, run: str) -> dict[str, Any]:
    """Bring back every document a clean-up retired, with the facts it
    ended: stated again as new edges by ``restore_run``, so the ended ones
    keep their ending and ``held_at`` still says what prax held while the
    set was retired (invariant 8). A duplicate comes back without what
    moved to its first copy, which keeps it. A clean-up from before
    2026-11-07 kept its ended edges in ``meta.retired.ended``; they are
    recorded as the run's endings first."""
    rows = con.execute(
        "SELECT id, json_extract(meta, '$.retired.ended') AS ended,"
        " json_extract(meta, '$.retired.at') AS at"
        " FROM documents WHERE json_extract(meta, '$.retired.run') = ?",
        (run,),
    ).fetchall()
    if not rows:
        raise NotFound(f"no clean-up named {run}")
    record_old_endings(
        con, run, [{"ended": r["ended"] or "[]", "at": r["at"]} for r in rows]
    )
    # the first document's unretire states the whole set's edges again
    # (its run is the set's), the rest find them stated
    restated = sum(int(unretire_document(con, int(r["id"]))["edges"]) for r in rows)
    return {"run": run, "restored": len(rows), "edges_restated": restated}
