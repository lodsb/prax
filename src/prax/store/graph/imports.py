"""Another library's entities and edges, met by their global ids (plan item
AP, ``docs/global-ids.md``, "The import"; migration 65).

A graph file of ``prax-graph/2`` names each entity by its ``gid``. The
import settles each one in this order (``settle_foreign``):

1. a ``gid`` this library minted (a file that went out and came back), or
   one of the rows folded into it, lands on its own row;
2. one already in ``foreign_entities`` for this source lands where it was
   decided;
3. otherwise a name here that answers to theirs (``entity_answering``)
   makes a ``likely`` row, which waits on the Review page; and when
   nothing answers, the entity is ``new`` here, decided by the import.

An edge with a ``likely`` end is held (``import_held``) until the row is
decided (``decide_foreign``): "theirs is our N", "a new entity", or "left
out". A decided row writes its held edges under the run that held them,
and ``undo_foreign`` takes the answer back: the edges written onto the
entity end and wait again.
An edge whose claim is already live here for its document, whoever
wrote it, is counted and not written again, so a second import of the same file writes
nothing (``import_edge``).

A friend's entity never meets this library's by an equal number, only
through a person's answer or a name nothing else holds.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..base import _NOW, _reading, _serialized, run_name
from .decisions import queue_review
from .edges import Edge, end_edges, link
from .labels import add_label
from .lookups import entities_by_gid, entity_answering

FOREIGN_STATES = ("likely", "same", "new", "left_out")
FOREIGN_VERDICTS = ("same", "new", "left_out")


@dataclass(frozen=True)
class Landing:
    """Where one of their entities lands here: ``state`` is ``own`` (a gid
    of this library's), or a ``foreign_entities`` state; ``entity_id`` is
    ours when there is one; ``name`` and ``type`` are what an edge to it
    is linked by."""

    gid: str
    state: str
    entity_id: int | None
    name: str
    type: str


def _local(con: sqlite3.Connection, gid: str, entity_id: int, state: str) -> Landing:
    row = con.execute(
        "SELECT name, type FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    return Landing(gid, state, entity_id, str(row["name"]), str(row["type"]))


def _landing(con: sqlite3.Connection, row: sqlite3.Row) -> Landing:
    if row["entity_id"] is not None:
        return _local(con, row["gid"], int(row["entity_id"]), row["state"])
    return Landing(row["gid"], row["state"], None, row["name"], row["type"])


def _found(
    con: sqlite3.Connection, source: str, entity: dict[str, Any]
) -> Landing | None:
    """Steps 1 and 2: their entity on a row of ours, or on a row of an
    earlier import of the source; None when neither."""
    gid = str(entity["gid"])
    own = entities_by_gid(con, [gid, *(entity.get("aliases") or [])])
    if own:
        ours = own.get(gid) or next(iter(own.values()))
        return _local(con, gid, ours, "own")
    row = con.execute(
        "SELECT * FROM foreign_entities WHERE source = ? AND gid = ?", (source, gid)
    ).fetchone()
    return _landing(con, row) if row is not None else None


@_reading
def foreign_state(
    con: sqlite3.Connection, source: str, entity: dict[str, Any]
) -> Landing:
    """Where their ``entity`` would land, writing nothing: a dry run's."""
    found = _found(con, source, entity)
    if found is not None:
        return found
    name, etype = str(entity["name"]), str(entity["type"])
    state = "new" if entity_answering(con, name, etype) is None else "likely"
    return Landing(str(entity["gid"]), state, None, name, etype)


@_serialized
def settle_foreign(
    con: sqlite3.Connection, source: str, entity: dict[str, Any], *, run: str
) -> Landing:
    """Where their ``entity`` line (``gid``, ``aliases``, ``name``, ``type``,
    ``labels``) lands, in the order of the module's docstring; writes the
    row for step 3. A ``new`` entity is not made here: its first edge makes
    it (``store.link``), so a thing nothing here holds an edge to makes no
    row of its own."""
    found = _found(con, source, entity)
    if found is not None:
        return found
    gid = str(entity["gid"])
    name, etype = str(entity["name"]), str(entity["type"])
    candidate = entity_answering(con, name, etype)
    state = "likely" if candidate is not None else "new"
    con.execute(
        "INSERT INTO foreign_entities (source, gid, name, type, labels, state,"
        " candidate_id, decided_by, run, decided_at)"
        f" VALUES (?,?,?,?,?,?,?,?,?, {'NULL' if candidate else _NOW})",
        (
            source,
            gid,
            name,
            etype,
            json.dumps(entity.get("labels") or [], ensure_ascii=False),
            state,
            candidate,
            None if candidate else "import",
            run,
        ),
    )
    con.commit()
    return Landing(gid, state, None, name, etype)


def _group(con: sqlite3.Connection, entity_id: int) -> list[int]:
    """An entity and the rows folded into it."""
    return [entity_id] + [
        int(r[0])
        for r in con.execute(
            "SELECT id FROM entities WHERE canonical_id = ?", (entity_id,)
        )
    ]


def _live_twin(
    con: sqlite3.Connection, ends: tuple[int, int], rel: str, doc: int | None
) -> int | None:
    """A live edge here that states the claim already: the same relation
    between the two ends (or rows folded into them) for that document,
    whoever wrote it. The claim is the edge's global id (``edge_gid``)."""
    src, dst = _group(con, ends[0]), _group(con, ends[1])
    row = con.execute(
        f"SELECT id FROM edges WHERE src IN ({','.join('?' * len(src))})"
        f" AND dst IN ({','.join('?' * len(dst))}) AND rel = ?"
        " AND source_doc IS ? AND valid_to IS NULL ORDER BY id LIMIT 1",
        (*src, *dst, rel, doc),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def _kept(record: dict[str, Any], titles: dict[str, str] | None) -> dict[str, Any]:
    """A held edge line, with its document's title from the file's document
    lines, which its evidence names when the document is not held here."""
    title = (titles or {}).get(record.get("source_hash") or "")
    return {**record, "source_title": title} if title else record


def _evidence(record: dict[str, Any], titles: dict[str, str], doc: int | None) -> str:
    origin = f"{record.get('producer') or '?'}/{record.get('run') or '?'}"
    doc_hash = record.get("source_hash")
    if doc_hash and doc is None:
        title = titles.get(doc_hash) or record.get("source_title") or doc_hash[:12]
        origin += f", from “{title}” (sha256 {doc_hash[:12]})"
    said = (record.get("evidence") or "").strip()
    return (said + f" [imported: {origin}]").strip()


@_serialized
def import_edge(
    con: sqlite3.Connection,
    record: dict[str, Any],
    ends: tuple[Landing, Landing],
    *,
    doc: int | None,
    source: str,
    producer: str,
    run: str,
    titles: dict[str, str] | None = None,
) -> tuple[str, int | None]:
    """One live edge line of their file between two landed ends: ``held``
    (a likely end), ``left_out`` (an end left out), ``same`` (the claim is
    live here for that document, ``_live_twin``), ``queued`` (the local
    ontology refuses it: the review queue) or ``linked``; with the edge's id
    for ``same`` and ``linked``."""
    src, dst = ends
    if "likely" in (src.state, dst.state):
        _hold(con, source, run, ends, doc, _kept(record, titles))
        con.commit()
        return "held", None
    if "left_out" in (src.state, dst.state):
        return "left_out", None
    if src.entity_id is not None and dst.entity_id is not None:
        twin = _live_twin(con, (src.entity_id, dst.entity_id), record["rel"], doc)
        if twin is not None:
            return "same", twin
    edge = Edge(src.name, src.type, record["rel"], dst.name, dst.type)
    evidence = _evidence(record, titles or {}, doc)
    try:
        made = link(
            con,
            edge,
            confidence=record.get("confidence") or "AMBIGUOUS",
            source_doc=doc,
            evidence=evidence,
            producer=producer,
            run=run,
        )
    except ValueError as exc:
        queue_review(
            con,
            src=edge.src,
            src_type=edge.src_type,
            rel=edge.rel,
            dst=edge.dst,
            dst_type=edge.dst_type,
            reason=f"{exc} (imported, written against"
            f" {record.get('ontology_version')})",
            source_doc=doc,
            evidence=evidence,
            ontology_version=record.get("ontology_version"),
        )
        return "queued", None
    row = con.execute("SELECT src, dst FROM edges WHERE id = ?", (made,)).fetchone()
    for end, made_id in ((src, row["src"]), (dst, row["dst"])):
        if end.state == "new" and end.entity_id is None:
            _made_new(con, source, end.gid, int(made_id), producer, run)
    if "same" in (src.state, dst.state) or "new" in (src.state, dst.state):
        # written onto an entity of theirs: what an undo of its answer ends
        _hold(con, source, run, ends, doc, _kept(record, titles), edge_id=made)
    return "linked", made


def _hold(
    con: sqlite3.Connection,
    source: str,
    run: str,
    ends: tuple[Landing, Landing],
    doc: int | None,
    record: dict[str, Any],
    *,
    edge_id: int | None = None,
) -> None:
    con.execute(
        "INSERT INTO import_held (source, run, src_gid, dst_gid, source_doc,"
        " record, edge_id) VALUES (?,?,?,?,?,?,?)",
        (
            source,
            run,
            ends[0].gid,
            ends[1].gid,
            doc,
            json.dumps(record, ensure_ascii=False),
            edge_id,
        ),
    )


def _made_new(
    con: sqlite3.Connection,
    source: str,
    gid: str,
    entity_id: int,
    producer: str,
    run: str,
) -> None:
    """A ``new`` row's entity, once its first edge made it, with their
    labels."""
    con.execute(
        "UPDATE foreign_entities SET entity_id = ? WHERE source = ? AND gid = ?",
        (entity_id, source, gid),
    )
    _add_labels(con, source, gid, entity_id, producer, run)


def _add_labels(
    con: sqlite3.Connection,
    source: str,
    gid: str,
    entity_id: int,
    producer: str,
    run: str,
) -> int:
    row = con.execute(
        "SELECT labels FROM foreign_entities WHERE source = ? AND gid = ?",
        (source, gid),
    ).fetchone()
    n = 0
    for label in json.loads(row["labels"] or "[]") if row is not None else []:
        if str(label.get("label") or "").strip():
            n += add_label(
                con,
                entity_id,
                str(label["label"]),
                lang=label.get("lang"),
                kind="alt",
                producer=producer,
                run=run,
            )
    return n


@_serialized
def add_foreign_labels(
    con: sqlite3.Connection, source: str, landing: Landing, *, run: str
) -> int:
    """Their labels of a landed entity, as alternative labels of ours: an
    own one's and a decided one's; a likely one's wait for the answer."""
    if landing.entity_id is None or landing.state not in ("own", "same", "new"):
        return 0
    return _add_labels(
        con, source, landing.gid, landing.entity_id, f"import:{source}", run
    )


@_serialized
def hold_again(con: sqlite3.Connection, source: str) -> int:
    """A newer import of ``source`` holds its own edges: the last one's
    waiting rows go, and the record of what was written stays. Returns how
    many went."""
    cur = con.execute(
        "DELETE FROM import_held WHERE source = ? AND edge_id IS NULL", (source,)
    )
    con.commit()
    return cur.rowcount


@_reading
def foreign_likely(
    con: sqlite3.Connection, *, offset: int = 0, limit: int = 30
) -> dict[str, Any]:
    """Their entities waiting for an answer, the most held edges first: each
    with the import, their name, type and labels, our entity whose name
    answers to theirs, and how many of their edges wait on it."""
    total = con.execute(
        "SELECT count(*) FROM foreign_entities WHERE state = 'likely'"
    ).fetchone()[0]
    rows = con.execute(
        """
        SELECT f.*, (SELECT count(*) FROM import_held h WHERE h.source = f.source
                     AND (h.src_gid = f.gid OR h.dst_gid = f.gid)
                     AND h.edge_id IS NULL) AS held
        FROM foreign_entities f WHERE f.state = 'likely'
        ORDER BY held DESC, f.source, f.name LIMIT ? OFFSET ?
        """,
        (max(1, min(limit, 100)), max(0, offset)),
    ).fetchall()
    items = []
    for r in rows:
        cand = con.execute(
            "SELECT c.id, c.name, c.type, (SELECT count(*) FROM edges x"
            " WHERE (x.src = c.id OR x.dst = c.id) AND x.valid_to IS NULL) AS edges"
            " FROM entities e JOIN entities c ON c.id = COALESCE(e.canonical_id, e.id)"
            " WHERE e.id = ?",
            (r["candidate_id"],),
        ).fetchone()
        items.append(
            {
                "source": r["source"],
                "gid": r["gid"],
                "name": r["name"],
                "type": r["type"],
                "labels": json.loads(r["labels"] or "[]"),
                "held": int(r["held"]),
                "candidate": dict(cand) if cand is not None else None,
            }
        )
    return {"total": int(total), "offset": offset, "items": items}


def _free_name(con: sqlite3.Connection, name: str, etype: str, source: str) -> str:
    """A name for a new entity that ours already answers to: theirs, said
    to be from the import, numbered past a second one."""
    for n in range(1, 100):
        cand = f"{name} ({source})" if n == 1 else f"{name} ({source} {n})"
        if entity_answering(con, cand, etype) is None:
            return cand
    raise ValueError(f"no free name for {name!r}")


@_serialized
def decide_foreign(
    con: sqlite3.Connection,
    source: str,
    gid: str,
    verdict: str,
    *,
    entity_id: int | None = None,
    by: str = "human",
) -> dict[str, Any]:
    """A person's answer, or a judge's, for one of their entities: ``same``
    (ours is ``entity_id``, by default the candidate), ``new`` (a thing of
    its own here; one whose name ours answers to is named for the import)
    or ``left_out``. A decided row is taken back first (``undo_foreign``).
    The held edges both of
    whose ends are now decided are written under the run that held them,
    and the ones with an end left out go. Returns the row's state, our
    entity and what became of the held edges."""
    if verdict not in FOREIGN_VERDICTS:
        raise ValueError(f"verdict must be one of {FOREIGN_VERDICTS}")
    row = con.execute(
        "SELECT * FROM foreign_entities WHERE source = ? AND gid = ?", (source, gid)
    ).fetchone()
    if row is None:
        raise KeyError(f"no entity {gid} from {source}")
    if row["state"] != "likely":
        raise ValueError(f"{gid} from {source} is answered ({row['state']}): undo it")
    producer = f"import:{source}"
    ours: int | None = None
    if verdict == "same":
        ours = entity_id or row["candidate_id"]
        if (
            ours is None
            or con.execute("SELECT 1 FROM entities WHERE id = ?", (ours,)).fetchone()
            is None
        ):
            raise KeyError(f"no such entity: {ours}")
        ours = int(
            con.execute(
                "SELECT COALESCE(canonical_id, id) FROM entities WHERE id = ?", (ours,)
            ).fetchone()[0]
        )
    elif verdict == "new":
        name = str(row["name"])
        if entity_answering(con, name, row["type"]) is not None:
            name = _free_name(con, name, row["type"], source)
        con.execute(
            "INSERT OR IGNORE INTO entities (name, type) VALUES (?, ?)",
            (name, row["type"]),
        )
        ours = int(
            con.execute(
                "SELECT id FROM entities WHERE name = ? AND type = ?",
                (name, row["type"]),
            ).fetchone()[0]
        )
        con.execute(
            "INSERT OR IGNORE INTO entity_labels (entity_id, label, kind, producer)"
            " VALUES (?, ?, 'pref', 'baseline')",
            (ours, name),
        )
    con.execute(
        "UPDATE foreign_entities SET state = ?, entity_id = ?, decided_by = ?,"
        f" decided_at = {_NOW} WHERE source = ? AND gid = ?",
        (verdict, ours, by, source, gid),
    )
    if ours is not None:
        _add_labels(con, source, gid, ours, producer, str(row["run"] or ""))
    con.commit()
    done = _release_held(con, source, gid, producer)
    con.commit()
    return {"state": verdict, "entity_id": ours, **done}


def _release_held(
    con: sqlite3.Connection, source: str, gid: str, producer: str
) -> dict[str, int]:
    """The held edges of ``gid`` whose other end is decided too: written,
    or dropped when an end is left out."""
    held = con.execute(
        "SELECT * FROM import_held WHERE source = ? AND (src_gid = ? OR dst_gid = ?)"
        " AND edge_id IS NULL",
        (source, gid, gid),
    ).fetchall()
    out = {"linked": 0, "same": 0, "queued": 0, "left_out": 0, "still_held": 0}
    for h in held:
        src = _held_end(con, source, h["src_gid"])
        dst = _held_end(con, source, h["dst_gid"])
        if src is None or dst is None or "likely" in (src.state, dst.state):
            out["still_held"] += 1
            continue
        con.execute("DELETE FROM import_held WHERE id = ?", (h["id"],))
        said, _ = import_edge(
            con,
            json.loads(h["record"]),
            (src, dst),
            doc=h["source_doc"],
            source=source,
            producer=producer,
            run=str(h["run"]),
        )
        out[said] = out.get(said, 0) + 1
    return out


@_serialized
def undo_foreign(con: sqlite3.Connection, source: str, gid: str) -> dict[str, Any]:
    """Take an answer for one of their entities back: the row waits again,
    and the edges written onto it from their file end (under a run of the
    undo, so ``restore_run`` can take that back too) and wait with it. A
    claim that was here already when they said it was not written, so it
    waits again only from the next import of the file. An entity made for a
    ``new`` answer stays, with nothing of theirs on it."""
    row = con.execute(
        "SELECT * FROM foreign_entities WHERE source = ? AND gid = ?", (source, gid)
    ).fetchone()
    if row is None:
        raise KeyError(f"no entity {gid} from {source}")
    if row["candidate_id"] is None or row["state"] == "likely":
        raise ValueError(f"{gid} from {source} has no answer to take back")
    run = run_name(f"undo-import-{source}", unique=True)
    written = con.execute(
        "SELECT h.id, h.edge_id, x.valid_to FROM import_held h"
        " JOIN edges x ON x.id = h.edge_id WHERE h.source = ?"
        " AND (h.src_gid = ? OR h.dst_gid = ?)",
        (source, gid, gid),
    ).fetchall()
    live = [int(w["edge_id"]) for w in written if w["valid_to"] is None]
    ended = end_edges(con, live, run=run)
    con.executemany(
        "UPDATE import_held SET edge_id = NULL WHERE id = ?",
        [(w["id"],) for w in written if w["valid_to"] is None],
    )
    con.executemany(
        "DELETE FROM import_held WHERE id = ?",
        [(w["id"],) for w in written if w["valid_to"] is not None],
    )
    con.execute(
        "UPDATE foreign_entities SET state = 'likely', entity_id = NULL,"
        " decided_by = NULL, decided_at = NULL WHERE source = ? AND gid = ?",
        (source, gid),
    )
    con.commit()
    return {"state": "likely", "ended": len(ended), "run": run}


def _held_end(con: sqlite3.Connection, source: str, gid: str) -> Landing | None:
    own = entities_by_gid(con, [gid])
    if own:
        return _local(con, gid, own[gid], "own")
    row = con.execute(
        "SELECT * FROM foreign_entities WHERE source = ? AND gid = ?", (source, gid)
    ).fetchone()
    return _landing(con, row) if row is not None else None


@_serialized
def end_unconfirmed(
    con: sqlite3.Connection, producer: str, keep: set[int], *, run: str
) -> int:
    """The producer's live edges a newer file of its source no longer has:
    ended under ``run``, so ``restore_run`` can take the ending back. What
    the file still says (``keep``) stays as it was written."""
    live = [
        int(r[0])
        for r in con.execute(
            "SELECT id FROM edges WHERE producer = ? AND valid_to IS NULL", (producer,)
        )
    ]
    return len(end_edges(con, [e for e in live if e not in keep], run=run))
