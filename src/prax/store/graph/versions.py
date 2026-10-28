"""Versions of a work (plan item AS): a preprint, the camera-ready paper
and the journal version are three documents of one work.

Three kinds of "the same paper" are kept apart. The same bytes are one
document with several places (AQ). The same text in other bytes is a
copy, which heal's ``twin-documents`` retires into its keeper. Another
text of one work is a version: each version is ``example_of`` the work
(core 8, ``schema:exampleOfWork``), a fact a person decided, sourced to
the version. A dependency is never a version: a journal paper that
extends a conference paper is another work (``extends``, ``cites``).

The ``versions`` pass of ``prax maintain`` finds the pairs (``find_versions``):
two live documents of one title key, or of one arXiv id, in a group of at
most ``VERSION_GROUP_MAX``, whose texts share at least ``VERSION_FLOOR`` of
their runs of words (``prax.text.shingles``). Measured on 2026-10-28 over
1,067 pairs of one title: 393 share under a tenth (different texts of one
title, "Introduction"), 184 at least nine tenths (``COPY_LINE``: a copy
the chunk hashes missed, offered as one), and the rest are the versions'
range. A shared author's surname is a reason shown, not a condition: of
those pairs, most have no author recorded on one side.

A person answers each pair on the Review page (``decide_version``):

- "version" writes both ``example_of`` the work, one either already is a
  version of, else a new one named by the later's title, under a run of
  the decision;
- "copy" retires the twin into the keeper, as heal does (more live edges,
  then the older);
- "different" is kept, so the pair is not asked again.

``undo_version`` takes an answer back. Which version is the latest is read,
not written (``retrieval.in_order``): by ``meta.published``, else by when
the library took it in. Two versions of one
title are one node of the graph (``document_node``), so a ``supersedes``
between them would be an edge from the node to itself; the order lives
with the decided versions (``versions_of``), and a search reads it
(``retrieval.later_versions``): it folds an older version under the latest.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from itertools import combinations
from typing import Any

from prax.text import arxiv, shingles

from ..base import _ASIDE, _reading, _serialized, hidden_documents, now, run_name
from ..documents import retire_document, unretire_document
from ..retrieval import EXAMPLE_OF, in_order, work_versions, works_of
from .edges import Edge, document_node, link, retire_run
from .labels import PERSON

VERSION_FLOOR = 0.1  # texts sharing less are different texts of one title
COPY_LINE = 0.9  # texts sharing this much are one text twice
VERSION_GROUP_MAX = 6  # a larger group of one title is a common title
VERSION_VERDICTS = ("version", "copy", "different")
VERSIONS_PAGE = 50

_TITLE_KEY = re.compile(r"[^\w]+")
TITLE_KEY_MIN = 9  # shorter titles pair by accident ("Untitled", "Notes")


def title_key(title: str | None) -> str:
    """A title as two documents of one work share it: lower-cased, every
    run of what is not a letter or a digit one space; empty when shorter
    than ``TITLE_KEY_MIN``."""
    key = _TITLE_KEY.sub(" ", (title or "").lower()).strip()
    return key if len(key) >= TITLE_KEY_MIN else ""


def _texts(con: sqlite3.Connection, doc_id: int) -> list[str]:
    return [
        str(r[0])
        for r in con.execute(
            f"SELECT c.text FROM chunks c WHERE c.doc_id = ?"
            f" AND (c.kind IS NULL OR c.kind != 'figure'){_ASIDE} ORDER BY c.id",
            (doc_id,),
        )
    ]


def _surnames(con: sqlite3.Connection, doc_id: int) -> set[str]:
    """The authors' surnames a document's facts or its record name."""
    out = {
        str(r[0]).split()[-1].lower()
        for r in con.execute(
            "SELECT t.name FROM edges e JOIN entities t ON t.id = e.dst"
            " WHERE e.source_doc = ? AND e.rel = 'authored_by' AND e.valid_to IS NULL",
            (doc_id,),
        )
        if str(r[0]).split()
    }
    row = con.execute(
        "SELECT json_extract(meta, '$.creators') FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    for c in json.loads(row[0] or "[]") if row else []:
        if isinstance(c, dict) and c.get("lastName"):
            out.add(str(c["lastName"]).lower())
    return out


def _groups(con: sqlite3.Connection) -> list[tuple[str, list[sqlite3.Row]]]:
    """Live documents with a text that share a title key (and the kind of
    file) or an arXiv id, in groups of two to ``VERSION_GROUP_MAX``."""
    by_title: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    by_arxiv: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in con.execute(
        "SELECT id, title, mime, text_hash, json_extract(meta, '$.arxiv') AS arxiv"
        " FROM documents"
        " WHERE text_hash IS NOT NULL AND json_extract(meta, '$.retired') IS NULL"
        " ORDER BY id"
    ):
        if key := title_key(r["title"]):
            by_title[(key, str(r["mime"] or "").split("/")[0])].append(r)
        if ident := arxiv.canonical(r["arxiv"]):
            by_arxiv[ident].append(r)
    out = [("title", rows) for rows in by_title.values()]
    out += [("arxiv", rows) for rows in by_arxiv.values()]
    return [(why, rows) for why, rows in out if 1 < len(rows) <= VERSION_GROUP_MAX]


def find_versions(con: sqlite3.Connection) -> dict[str, int]:
    """The pairs of live documents that are probably versions of one work
    or one text twice, kept as candidates for a person (the module's
    rule). A pair is measured again only when one of its texts changed;
    an open pair whose documents no longer pair is dropped, a decided one
    kept. The measuring reads, outside the store's lock (52 s over the
    library of 2026-10-28, 8 s with nothing to measure again); the pairs
    are written in one short write (``_save_versions``). Returns how many
    pairs are ``open``, ``new`` and ``dropped``."""
    known = {
        (int(r["a"]), int(r["b"])): r
        for r in con.execute("SELECT * FROM version_candidates")
    }
    seen: set[tuple[int, int]] = set()
    prints: dict[int, frozenset[int]] = {}
    found: list[tuple[Any, ...]] = []
    below: list[int] = []
    for why, rows in _groups(con):
        texts = {int(r["id"]): r["text_hash"] for r in rows}
        for x, y in combinations(sorted(texts), 2):
            pair = (x, y)
            if pair in seen:
                continue
            seen.add(pair)
            old = known.get(pair)
            if old is not None and (
                old["verdict"]
                or (old["a_text"] == texts[x] and old["b_text"] == texts[y])
            ):
                continue  # decided, or measured on these texts
            for d in pair:
                if d not in prints:
                    prints[d] = shingles.shingles(_texts(con, d))
            score = shingles.overlap(prints[x], prints[y])
            if score < VERSION_FLOOR:
                if old is not None:
                    below.append(int(old["id"]))
                continue
            shared = _surnames(con, x) & _surnames(con, y)
            reasons = {
                "by": why,
                "authors": sorted(shared)[:5],
                "suggest": "copy" if score >= COPY_LINE else "version",
            }
            found.append(
                (x, y, round(score, 4), json.dumps(reasons), texts[x], texts[y], now())
            )
    # an open pair no group holds any more (a document retired, renamed)
    gone = [
        int(r["id"])
        for pair, r in known.items()
        if pair not in seen and not r["verdict"]
    ]
    new = sum((f[0], f[1]) not in known for f in found)
    _save_versions(con, found, below + gone)
    still = con.execute(
        "SELECT count(*) FROM version_candidates WHERE verdict IS NULL"
    ).fetchone()[0]
    return {"open": int(still), "new": new, "dropped": len(below) + len(gone)}


@_serialized
def _save_versions(
    con: sqlite3.Connection, found: list[tuple[Any, ...]], drop: list[int]
) -> None:
    """The pairs a pass measured, written; the open ones it no longer
    pairs, dropped. An answer a person gave meanwhile is never touched."""
    con.executemany(
        "INSERT INTO version_candidates (a, b, overlap, reasons, a_text, b_text,"
        " found_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (a, b) DO UPDATE"
        " SET overlap = excluded.overlap, reasons = excluded.reasons,"
        " a_text = excluded.a_text, b_text = excluded.b_text",
        found,
    )
    con.executemany(
        "DELETE FROM version_candidates WHERE id = ? AND verdict IS NULL",
        [(i,) for i in drop],
    )
    con.commit()


def _side(con: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    row = con.execute(
        "SELECT id, title, mime, added_at, meta,"
        " (SELECT sum(length(text)) FROM chunks WHERE doc_id = documents.id) AS chars"
        " FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    meta = json.loads(row["meta"] or "{}")
    headings: list[str] = []
    for (h,) in con.execute(
        "SELECT DISTINCT json_extract(heading, '$[0]') FROM chunks"
        " WHERE doc_id = ? AND heading IS NOT NULL ORDER BY id",
        (doc_id,),
    ):
        if h and h not in headings:
            headings.append(str(h))
    return {
        "doc_id": doc_id,
        "title": row["title"],
        "published": (meta.get("published") or {}).get("date"),
        "added": row["added_at"],
        "chars": int(row["chars"] or 0),
        "headings": headings[:40],
        "retired": bool(meta.get("retired")),
    }


@_reading
def version_candidates(
    con: sqlite3.Connection,
    *,
    verdict: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """One page of pairs, the most alike first: open ones, or those given
    ``verdict``. Each with both sides (title, date, length, top-level
    headings), the overlap and the reasons; and the count of each state.
    A pair with a document this viewer may not see is left out."""
    if verdict is not None and verdict not in VERSION_VERDICTS:
        raise ValueError(f"verdict is one of {VERSION_VERDICTS}")
    hidden = hidden_documents(con)
    counts = {"open": 0, **dict.fromkeys(VERSION_VERDICTS, 0)}
    rows = []
    for r in con.execute(
        "SELECT * FROM version_candidates WHERE verdict IS ? ORDER BY overlap DESC, id"
        if verdict is None
        else "SELECT * FROM version_candidates WHERE verdict = ?"
        " ORDER BY decided_at DESC, id",
        (verdict,),
    ):
        if int(r["a"]) in hidden or int(r["b"]) in hidden:
            continue
        rows.append(r)
    for r in con.execute("SELECT verdict, a, b FROM version_candidates"):
        if int(r["a"]) in hidden or int(r["b"]) in hidden:
            continue
        counts[r["verdict"] or "open"] += 1
    page = rows[offset : offset + max(1, min(limit, VERSIONS_PAGE))]
    return {
        "verdict": verdict,
        "total": len(rows),
        "counts": counts,
        "items": [
            {
                "id": int(r["id"]),
                "overlap": float(r["overlap"]),
                "reasons": json.loads(r["reasons"] or "{}"),
                "verdict": r["verdict"],
                "decided_at": r["decided_at"],
                "a": _side(con, int(r["a"])),
                "b": _side(con, int(r["b"])),
            }
            for r in page
        ],
    }


def _keeper(con: sqlite3.Connection, a: int, b: int) -> tuple[int, int]:
    """Of two copies, the one to keep (more live edges, then the older)
    and the twin."""
    edges = {
        int(r[0]): int(r[1])
        for r in con.execute(
            "SELECT source_doc, count(*) FROM edges WHERE valid_to IS NULL"
            " AND source_doc IN (?, ?) GROUP BY source_doc",
            (a, b),
        )
    }
    keep = min((a, b), key=lambda d: (-edges.get(d, 0), d))
    return keep, b if keep == a else a


@_serialized
def decide_version(
    con: sqlite3.Connection, candidate_id: int, verdict: str, *, by: str = PERSON
) -> dict[str, Any]:
    """A person's answer for a pair (the module's rule). Returns the
    pair's ``verdict`` and ``run``; for a version the ``work`` (its entity
    id and name), for a copy the ``kept`` and ``retired`` documents."""
    if verdict not in VERSION_VERDICTS:
        raise ValueError(f"verdict is one of {VERSION_VERDICTS}")
    row = con.execute(
        "SELECT * FROM version_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    hidden = hidden_documents(con)
    if row is None or int(row["a"]) in hidden or int(row["b"]) in hidden:
        raise KeyError(f"no such pair: {candidate_id}")
    if row["verdict"]:
        raise ValueError(f"pair {candidate_id} is answered: {row['verdict']}")
    a, b = int(row["a"]), int(row["b"])
    run = run_name("versions", candidate_id, unique=True)
    out: dict[str, Any] = {"id": candidate_id, "verdict": verdict, "run": run}
    if verdict == "version":
        earlier, later = in_order(con, [a, b])
        work = _work_of(con, later) or _work_of(con, earlier)
        if work is not None:
            name = str(
                con.execute(
                    "SELECT name FROM entities WHERE id = ?", (work,)
                ).fetchone()[0]
            )
        else:
            name = str(document_node(con, later)[0])
            taken = con.execute(
                "SELECT id FROM entities WHERE name = ? AND type = 'work'", (name,)
            ).fetchone()
            if taken is not None:  # another work of that title
                name = f"{name} (#{later})"
        for doc in (earlier, later):
            node, ntype = document_node(con, doc)
            if _work_of(con, doc) is not None:
                continue
            link(
                con,
                Edge(node, ntype, EXAMPLE_OF, name, "work"),
                confidence="EXTRACTED",
                source_doc=doc,
                evidence=f"a version of {name!r}, as a person decided",
                producer=by,
                run=run,
            )
        found = con.execute(
            "SELECT id FROM entities WHERE name = ? AND type = 'work'", (name,)
        ).fetchone()
        out["work"] = {"id": int(found[0]) if found else None, "name": name}
    elif verdict == "copy":
        keep, twin = _keeper(con, a, b)
        retire_document(
            con,
            twin,
            reason=f"a copy of {keep} (a person, versions {candidate_id})",
            duplicate_of=keep,
            by=by,
        )
        out.update(kept=keep, retired=twin)
    con.execute(
        "UPDATE version_candidates SET verdict = ?, decided_at = ?, run = ?"
        " WHERE id = ?",
        (verdict, now(), run, candidate_id),
    )
    con.commit()
    return out


@_serialized
def undo_version(con: sqlite3.Connection, candidate_id: int) -> dict[str, Any]:
    """An answer taken back: a version's facts ended (its run), a copy's
    twin back in the index (the edges that moved to the keeper stay
    there), and the pair open again."""
    row = con.execute(
        "SELECT * FROM version_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such pair: {candidate_id}")
    ended = retire_run(con, run=row["run"]) if row["run"] else 0
    back = None
    if row["verdict"] == "copy":
        for doc in (int(row["a"]), int(row["b"])):
            meta = con.execute(
                "SELECT json_extract(meta, '$.retired.reason') FROM documents"
                " WHERE id = ?",
                (doc,),
            ).fetchone()
            if meta and meta[0] and f"versions {candidate_id})" in str(meta[0]):
                unretire_document(con, doc)
                back = doc
    con.execute(
        "UPDATE version_candidates SET verdict = NULL, decided_at = NULL, run = NULL"
        " WHERE id = ?",
        (candidate_id,),
    )
    con.commit()
    return {"id": candidate_id, "verdict": None, "ended": ended, "unretired": back}


def _work_of(con: sqlite3.Connection, doc_id: int) -> int | None:
    """The work a document is a live version of, if any."""
    return works_of(con, [doc_id]).get(doc_id)


@_reading
def versions_of(con: sqlite3.Connection, doc_id: int) -> dict[str, Any] | None:
    """The work a document is a version of and its live versions this
    viewer may see, the latest last (``retrieval.in_order``), or None:
    ``{work: {id, name}, versions: [{doc_id, title, published}],
    latest}``."""
    work = _work_of(con, doc_id)
    if work is None:
        return None
    order = work_versions(con, work)
    rows = {
        int(r["id"]): r
        for r in con.execute(
            f"SELECT id, title, json_extract(meta, '$.published.date') AS pub"
            f" FROM documents WHERE id IN ({','.join('?' * len(order))})",
            order,
        )
    }
    name = con.execute("SELECT name FROM entities WHERE id = ?", (work,)).fetchone()
    return {
        "work": {"id": work, "name": str(name[0]) if name else ""},
        "versions": [
            {"doc_id": d, "title": rows[d]["title"], "published": rows[d]["pub"]}
            for d in order
        ],
        "latest": order[-1] if order else None,
    }
