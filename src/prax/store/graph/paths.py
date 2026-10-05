"""How is A connected to B (stage AM): the store's half of the path index.

``prax.graph.paths`` is the search; this part feeds it the live edges and
keeps the index it builds, rebuilt when the edges have changed and the
one held is older than ``PATH_REFRESH`` seconds. A walk as of a moment
builds its own from the edges held then (``held_at``) and keeps none.
The rule pass's derivations are no hop: a path already composes facts.
Nor is a briefing page: it links what arrived on one day.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from ..base import _reading, hidden_documents
from .edges import held_at
from .traversal import _choose

PATH_REFRESH = 300  # seconds an index serves after the edges changed
EVIDENCE_CHARS = 160

_HELD: dict[str, dict[str, Any]] = {}  # database file -> index, stamp, at
_HELD_LOCK = threading.Lock()


def _stamp(con: sqlite3.Connection) -> tuple[Any, ...]:
    """What moves when an edge is written or ended. Two scalar reads, each
    on an index: one SELECT of both maxima scanned the edges table, 62 ms
    on every call (the quality review of 2026-10-05)."""
    row = con.execute(
        "SELECT (SELECT max(id) FROM edges),"
        " (SELECT max(valid_to) FROM edges WHERE valid_to IS NOT NULL)"
    ).fetchone()
    return tuple(row)


REFERENCES = "references"  # the producer of a reference list's citations
SURE_CITATION = 0.95  # a title match this sure counts as stated, for a path


def _rows(con: sqlite3.Connection, as_of: str | None) -> list[tuple[Any, ...]]:
    held, args = held_at("e", as_of)
    briefings = [
        int(r[0])
        for r in con.execute(
            "SELECT DISTINCT COALESCE(s.canonical_id, s.id) FROM pages p"
            " JOIN documents d ON d.id = p.doc_id"
            " JOIN entities s ON s.name = d.title AND s.type = 'page'"
            " WHERE json_extract(d.meta, '$.page.kind') = 'briefing'"
        )
    ]
    rows = con.execute(
        "SELECT e.id, COALESCE(a.canonical_id, a.id), e.rel,"
        " COALESCE(b.canonical_id, b.id),"
        # a citation the references pass matched by title with a sure
        # score is as good as one matched by its DOI (AL step 9, G1: three
        # clean 3-hop chains through resolved citations cost 6.45)
        " CASE WHEN e.producer = ? AND e.confidence = 'INFERRED'"
        " AND e.evidence LIKE '%, score _.__'"
        " AND CAST(substr(e.evidence, -4) AS REAL) >= ?"
        " THEN 'EXTRACTED' ELSE e.confidence END,"
        " e.source_doc"
        " FROM edges e JOIN entities a ON a.id = e.src JOIN entities b ON b.id = e.dst"
        f" WHERE {held} AND COALESCE(e.producer, '') NOT LIKE 'rule:%'",
        (REFERENCES, SURE_CITATION, *args),
    ).fetchall()
    skip = set(briefings)
    return [tuple(r) for r in rows if r[1] not in skip and r[3] not in skip]


AS_OF_KEPT = 2  # indexes of past moments kept beside the current one


def path_index(con: sqlite3.Connection, as_of: str | None = None) -> Any:
    """The index of the edges held now (kept, refreshed after a change once
    ``PATH_REFRESH`` has passed) or as of a moment (the last
    ``AS_OF_KEPT`` kept, by moment and stamp). Built one at a time under
    the lock: a build peaks near 150 MB, and two at once would hold two
    (the quality review of 2026-10-05)."""
    from prax.graph import paths

    # one index per database file: a test's store, a copy, the library
    where = str(con.execute("PRAGMA database_list").fetchone()[2])
    with _HELD_LOCK:
        if as_of:
            stamp = _stamp(con)
            past = _HELD.setdefault(f"{where}#as_of", {})
            key = f"{as_of}|{stamp}"
            if key not in past:
                while len(past) >= AS_OF_KEPT:
                    past.pop(next(iter(past)))
                past[key] = paths.build(_rows(con, as_of=as_of))
            return past[key]
        held = _HELD.get(where)
        if held is not None and time.monotonic() - held["at"] < PATH_REFRESH:
            return held["index"]  # fresh: not even the stamp is read
        stamp = _stamp(con)
        if held is not None and held["stamp"] == stamp:
            held["at"] = time.monotonic()
            return held["index"]
        index = paths.build(_rows(con, None))
        _HELD[where] = {"index": index, "stamp": stamp, "at": time.monotonic()}
        return index


def _ends(
    con: sqlite3.Connection, name: str, etype: str | None
) -> tuple[list[int], Any]:
    ids, senses = _choose(con, name, etype)
    return ids, (senses if len(senses) > 1 else None)


@_reading
def connect_entities(
    con: sqlite3.Connection,
    a: str,
    b: str,
    *,
    type_a: str | None = None,
    type_b: str | None = None,
    max_hops: int = 4,
    relations: list[str] | None = None,
    as_of: str | None = None,
    weak: bool = False,
    k: int = 3,
) -> dict[str, Any]:
    """The best paths from ``a`` to ``b`` (names; a name that reaches
    several things takes the one of its type, or the most connected, and
    ``senses`` names them), each hop with its relation as stated, the
    documents behind it, one of them and its evidence. Only sound paths
    (``paths.SOUND``) unless ``weak``; then a weak one is marked so. With
    no sound path the answer says how many weak ones were left out and
    the best cost, so "no sound connection" is an answer of its own."""
    from prax.graph import paths

    max_hops = max(1, min(int(max_hops), paths.MAX_HOPS))
    starts, senses_a = _ends(con, a, type_a)
    ends, senses_b = _ends(con, b, type_b)
    out: dict[str, Any] = {"a": a, "b": b}
    if senses_a:
        out["senses_a"] = senses_a
    if senses_b:
        out["senses_b"] = senses_b
    if not starts or not ends:
        out["unknown"] = [n for n, ids in ((a, starts), (b, ends)) if not ids]
        return out
    index = path_index(con, as_of=as_of)
    hidden = hidden_documents(con)
    found = paths.connect(
        index,
        starts,
        ends,
        max_hops=max_hops,
        k=k,
        relations=relations,
        hidden=hidden,
    )
    sound = [p for p in found if p.cost <= paths.SOUND]
    shown = found if weak else sound
    names: dict[int, tuple[str, str]] = {}
    wanted = {
        int(index.ids[n])
        for p in shown
        for f in p.facts
        for n in (index.src[f], index.dst[f])
    }
    if wanted:
        marks = ",".join("?" * len(wanted))
        for r in con.execute(
            f"SELECT id, name, type FROM entities WHERE id IN ({marks})", sorted(wanted)
        ):
            names[int(r[0])] = (str(r[1]), str(r[2]))
    out["paths"] = []
    for p in shown:
        hops = []
        for f in p.facts:
            behind = [
                (e, d) for e, d in index.edges_of(f) if d is None or d not in hidden
            ]
            eid, doc = behind[0]
            ev = con.execute(
                "SELECT evidence FROM edges WHERE id = ?", (eid,)
            ).fetchone()
            src = names.get(int(index.ids[index.src[f]]), ("?", "?"))
            dst = names.get(int(index.ids[index.dst[f]]), ("?", "?"))
            hop: dict[str, Any] = {
                "src": src[0],
                "rel": index.rels[index.rel[f]],
                "dst": dst[0],
                "edge_id": eid,
                "confidence": paths.CONFIDENCES[index.conf[f]],
                "documents": len({d for _, d in behind if d is not None}),
            }
            if doc is not None:
                hop["source_doc"] = doc
            if ev and ev[0]:
                hop["evidence"] = str(ev[0])[:EVIDENCE_CHARS]
            hops.append(hop)
        path: dict[str, Any] = {"cost": p.cost, "hops": hops}
        if p.cost > paths.SOUND:
            path["weak"] = True
        out["paths"].append(path)
    if not sound:
        out["weak_left_out"] = 0 if weak else len(found)
        if found:
            out["best_cost"] = found[0].cost
    out["sound_at"] = paths.SOUND
    return out
