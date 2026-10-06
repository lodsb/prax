"""An edge and the entities at its ends: linking, finding, invalidating,
retiring a producer's reading, the provenance every edge carries, and
which documents are due for extraction."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from prax.graph import ontology
from prax.text import dates, mimes, quotes

from ..base import (
    _NOW,
    _like_prefix,
    _read_archive,
    _reading,
    _serialized,
    document_hidden,
    domain_clause,
    hidden_documents,
)

# the second hop is reached through exactly one)


CONFIDENCE_LEVELS = ("EXTRACTED", "INFERRED", "AMBIGUOUS")


@_reading
def find_entities(
    con: sqlite3.Connection, q: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    """Entities whose name — or a name they are *known by* — contains
    ``q`` (case-insensitive), with their number of currently valid edges,
    most connected first.

    The labels matter as much as the names. A pass that renames
    ``Knoblauchzehen`` to ``garlic cloves`` leaves the German as a label,
    and a search that read only ``entities.name`` would answer nothing
    for the word the document actually used: 522 names reached nothing
    that way on 2026-09-24. A hit found only through a label says so
    (``as``), so a caller can show what it matched.

    The degree is two counts, one per end: an OR across ``src`` and
    ``dst`` made the subquery scan the edges for every matching name (340
    names, 14 s; two indexed counts, 46 ms, 2026-09-22).
    """
    pattern = "%" + _like_prefix(q.lower())[:-1] + "%"
    # SQLite's own lower() is ASCII-only: it leaves Ä and Ü alone, so
    # `lower(name) LIKE '%ästhetik%'` never matched "Ästhetik der Lüge" —
    # in a library a fifth of which is German, silently. Python's does the
    # whole of Unicode, and is used only when the query needs it, because
    # a callback per row costs a few times the scan
    lower = "lower"
    if not q.isascii():
        con.create_function("unicode_lower", 1, lambda s: s.lower() if s else s)
        lower = "unicode_lower"
    rows = con.execute(
        f"""
        WITH hit(id, matched) AS (
            SELECT id, NULL FROM entities
             WHERE {lower}(name) LIKE ? ESCAPE '!' AND canonical_id IS NULL
            UNION
            SELECT COALESCE(e.canonical_id, e.id), l.label
              FROM entity_labels l JOIN entities e ON e.id = l.entity_id
             WHERE {lower}(l.label) LIKE ? ESCAPE '!'
        )
        SELECT e.id, e.name, e.type, min(hit.matched) AS "as",
               (SELECT count(*) FROM edges x
                WHERE x.src = e.id AND x.valid_to IS NULL)
               + (SELECT count(*) FROM edges x
                  WHERE x.dst = e.id AND x.valid_to IS NULL) AS degree
        FROM hit JOIN entities e ON e.id = hit.id
        WHERE e.canonical_id IS NULL
        GROUP BY e.id
        ORDER BY degree DESC, e.name LIMIT ?
        """,
        (pattern, pattern, max(1, min(limit, 200))),
    ).fetchall()
    hidden = hidden_documents(con)
    out = []
    for r in rows:
        row = dict(r)
        if hidden and not _said_openly(con, int(row["id"]), hidden):
            continue  # only documents the viewer may not see name it
        # the name itself matched: nothing to explain
        if pattern.strip("%") in row["name"].lower():
            row.pop("as", None)
        out.append(row)
    return out


def _said_openly(
    con: sqlite3.Connection, entity_id: int, hidden: frozenset[int]
) -> bool:
    """Whether a live edge of the entity (or of its aliases) comes from a
    document the viewer may see, or from none."""
    group = [
        int(r[0])
        for r in con.execute(
            "SELECT id FROM entities WHERE id = ? OR canonical_id = ?",
            (entity_id, entity_id),
        )
    ]
    marks = ",".join("?" * len(group))
    for end in ("src", "dst"):
        for (doc,) in con.execute(
            f"SELECT DISTINCT source_doc FROM edges WHERE {end} IN ({marks})"
            " AND valid_to IS NULL",
            group,
        ):
            if doc is None or doc not in hidden:
                return True
    return False


@dataclass
class Edge:
    src: str
    src_type: str
    rel: str
    dst: str
    dst_type: str


def _entity_id(con: sqlite3.Connection, name: str, etype: str) -> int:
    """The entity of this type called ``name``, created if there is none.

    A name the graph already knows this thing by counts: ``Olivenöl`` is
    a label of ``olive oil``, so a German recipe naming it again lands on
    the entity the vocabulary pass folded it into rather than starting
    the split over (`docs/stratification.md`, step 5). Only an unmerged
    entity of the same type, and only when exactly one answers — two
    entities of one type sharing a label is a question, and a question is
    not resolved by picking the lower id.
    """
    row = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()
    if row is not None:
        return int(row["id"])
    known = con.execute(
        "SELECT DISTINCT e.id FROM entity_labels l JOIN entities e"
        " ON e.id = l.entity_id WHERE l.label = ? COLLATE NOCASE"
        " AND e.type = ? AND e.canonical_id IS NULL LIMIT 2",
        (name, etype),
    ).fetchall()
    if len(known) == 1:
        return int(known[0]["id"])
    con.execute(
        "INSERT OR IGNORE INTO entities (name, type) VALUES (?,?)", (name, etype)
    )
    made = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()["id"]
    # a new entity answers to its own name from the start, so the labels
    # are the whole truth about what a thing is called and the name
    # column can be rebuilt from them (migration 23, docs/identity.md)
    con.execute(
        "INSERT OR IGNORE INTO entity_labels (entity_id, label, kind, producer)"
        " VALUES (?, ?, 'pref', 'baseline')",
        (made, name),
    )
    return int(made)


@_serialized
def link(
    con: sqlite3.Connection,
    edge: Edge,
    *,
    confidence: str = "EXTRACTED",
    source_doc: int | None = None,
    ontology_version: str | None = None,
    evidence: str | None = None,
    producer: str | None = None,
    run: str | None = None,
    world_from: str | None = None,
    world_to: str | None = None,
    place: tuple[int, int, str] | None = None,
) -> int:
    """Insert a currently-valid edge; entities are created on demand.

    Types are validated against the current ontology (invariant 9); the edge
    is stamped with that ontology's version unless one is given. ``evidence``
    is a short quote from ``source_doc`` that supports the edge.

    ``world_from`` and ``world_to`` are when the fact holds in the world,
    as the source states it (a year, a month or a day, ``dates.parse``),
    beside ``valid_from``, which is when prax wrote it. ``world_to`` may be
    ``unknown``: ended, at a date nobody gives. A date that does not parse
    is refused (ValueError), so nothing guessed is stored.

    ``place`` is where ``evidence`` stood: its ``(start, end)`` in the text
    artifact of ``source_doc`` and that artifact's hash (migration 48,
    ``text.quotes.place``), so a re-read can say whether it still stands
    there (``evidence_place``).
    """
    if source_doc is not None and document_hidden(con, source_doc):
        # a restricted viewer writes to nothing it may not see: as if absent
        raise ValueError(f"no such document: {source_doc}")
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}")
    onto = ontology.current()
    onto.check_edge(edge.src_type, edge.rel, edge.dst_type)
    if ontology_version is None:
        ontology_version = onto.version
    begin = _world_date(world_from, "world_from")
    end = (
        (None, "unknown")
        if str(world_to or "").strip().lower() == "unknown"
        else _world_date(world_to, "world_to")
    )
    src = _entity_id(con, edge.src, edge.src_type)
    dst = _entity_id(con, edge.dst, edge.dst_type)
    cur = con.execute(
        "INSERT INTO edges (src, dst, rel, confidence, source_doc,"
        " ontology_version, evidence, producer, run, valid_from,"
        " world_from, world_from_precision, world_to, world_to_precision,"
        " evidence_start, evidence_end, evidence_text_hash)"
        f" VALUES (?,?,?,?,?,?,?,?,?, {_NOW}, ?,?,?,?, ?,?,?)",
        (
            src,
            dst,
            edge.rel,
            confidence,
            source_doc,
            ontology_version,
            evidence,
            producer,
            run,
            *begin,
            *end,
            *(place if place is not None else (None, None, None)),
        ),
    )
    con.commit()
    return int(cur.lastrowid or 0)


@_reading
def evidence_place(con: sqlite3.Connection, edge_id: int) -> dict[str, Any] | None:
    """Where an edge's quote stood and where it stands now: ``stood`` (its
    range in the text it was read from, and that text's hash), and
    ``stands``: ``where it stood`` (the document's text is the same one),
    ``moved`` (the text changed; the quote is found at ``now``) or
    ``gone`` (the quote is in the text no more). None for an edge with no
    place recorded or no document."""

    row = con.execute(
        "SELECT e.evidence, e.source_doc, e.evidence_start, e.evidence_end,"
        " e.evidence_text_hash, d.text_hash FROM edges e"
        " LEFT JOIN documents d ON d.id = e.source_doc WHERE e.id = ?",
        (edge_id,),
    ).fetchone()
    if row is None or row["evidence_text_hash"] is None:
        return None
    stood = {
        "start": row["evidence_start"],
        "end": row["evidence_end"],
        "text_hash": row["evidence_text_hash"],
    }
    if stood["start"] is None:
        # looked for (the ``places`` pass) and not found in that text
        return {"stood": stood, "stands": "not in the text it was looked for in"}
    if row["text_hash"] == row["evidence_text_hash"]:
        return {"stood": stood, "stands": "where it stood"}
    if row["text_hash"] is None:
        return {"stood": stood, "stands": "gone"}
    text = _read_archive(str(row["text_hash"])).decode("utf-8", "replace")
    now = quotes.place(text, row["evidence"])
    if now is None:
        return {"stood": stood, "stands": "gone"}
    return {
        "stood": stood,
        "stands": "moved",
        "now": {"start": now[0], "end": now[1], "text_hash": row["text_hash"]},
    }


@_reading
def run_edges(
    con: sqlite3.Connection, *, producer: str, run: str
) -> list[dict[str, Any]]:
    """The live edges one producer wrote in one run: ``id``, ``source_doc``,
    ``src``, ``rel`` and ``dst`` (the names they were written with). What a
    pass that keeps its own edges in step (a project's links) compares
    against before it adds or ends one."""
    rows = con.execute(
        "SELECT e.id, e.source_doc, s.name AS src, e.rel, t.name AS dst"
        " FROM edges e JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
        " WHERE e.producer = ? AND e.run = ? AND e.valid_to IS NULL ORDER BY e.id",
        (producer, run),
    ).fetchall()
    return [dict(r) for r in rows]


@_reading
def document_node(con: sqlite3.Connection, doc_id: int) -> tuple[str, str]:
    """A library document as a node of the graph, ``(name, type)``: its
    title, and the type its entity already has (a page is a ``page``; else
    the document type the most edges reach it as; else the one type its
    domains let a document be, which its extraction will name it by: a
    research document is a ``paper``; else ``document``). A
    document that does not exist, has no title or is hidden from this
    viewer is a KeyError, as if absent (the wall)."""

    row = con.execute(
        "SELECT title, json_extract(meta, '$.domains') AS domains FROM documents"
        " WHERE id = ?",
        (doc_id,),
    ).fetchone()
    if row is None or not row["title"] or document_hidden(con, doc_id):
        raise KeyError(f"no such document: {doc_id}")
    title = str(row["title"])
    if con.execute("SELECT 1 FROM pages WHERE doc_id = ?", (doc_id,)).fetchone():
        return title, "page"
    onto = ontology.current()
    best: tuple[int, str] | None = None
    for r in con.execute(
        "SELECT e.type, (SELECT count(*) FROM edges x WHERE (x.src = e.id"
        " OR x.dst = e.id) AND x.valid_to IS NULL) AS n FROM entities e"
        " WHERE e.name = ? AND e.canonical_id IS NULL",
        (title,),
    ):
        etype = str(r["type"])
        if (
            etype in onto.entity_types
            and onto.is_a(etype, "document")
            and (best is None or int(r["n"]) > best[0])
        ):
            best = (int(r["n"]), etype)
    if best:
        return title, best[1]
    domains = json.loads(row["domains"]) if row["domains"] else None
    kinds = onto.for_domains(domains).self_types if domains else ()
    return title, kinds[0] if len(kinds) == 1 else "document"


def _world_date(value: str | None, name: str) -> tuple[str | None, str | None]:
    """A world date and its precision, or two Nones for none."""
    if not value:
        return None, None
    got = dates.parse(value)
    if got is None:
        raise ValueError(f"{name}: a year, a month or a day, not {value!r}")
    return got


def held_at(alias: str, as_of: str | None = None) -> tuple[str, list[str]]:
    """The SQL that keeps the edges prax held at a moment (record time),
    and its arguments: written at or before ``as_of`` and not ended before
    it; without one, the edges in force now. One place says it (Utopia's
    ``record_axis``), so "as of" means one thing in every read that takes
    it. The world's time is another question (``world_from``/``world_to``).
    """
    if not as_of:
        return f"{alias}.valid_to IS NULL", []
    got = dates.parse(as_of)
    if got is None and not _MOMENT_ISO.fullmatch(as_of):
        raise ValueError(f"as_of: a date or a UTC moment, not {as_of!r}")
    moment = as_of if _MOMENT_ISO.fullmatch(as_of) else _end_of(got)
    held = (
        f"{alias}.valid_from <= ?"
        f" AND ({alias}.valid_to IS NULL OR {alias}.valid_to > ?)"
    )
    return held, [moment, moment]


def changed_between(
    alias: str, side: str, begin: str, end: str
) -> tuple[str, list[str], str]:
    """The SQL that keeps the edges prax wrote (``added``) or ended
    (``ended``) between two moments of record time, its arguments, and the
    column that says when. Beside ``held_at``, so a period on record time
    is said in one place too."""
    column = {"added": "valid_from", "ended": "valid_to"}[side]
    return (
        (
            f"{alias}.{column} IS NOT NULL AND {alias}.{column} >= ?"
            f" AND {alias}.{column} <= ?"
        ),
        [begin, end],
        f"{alias}.{column}",
    )


_MOMENT_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _end_of(got: tuple[str, str] | None) -> str:
    """A date as the last moment of its span, so "as of 2026-10" holds
    what prax held at the end of October."""
    assert got is not None
    date, precision = got
    if precision == "year":
        return f"{date}-12-31T23:59:59Z"
    if precision == "month":
        return f"{date}-31T23:59:59Z"  # sorts after every day of the month
    return f"{date}T23:59:59Z"


@_reading
def find_edges(con: sqlite3.Connection, edge: Edge) -> list[int]:
    """Ids of currently-valid edges with exactly this src, rel and dst.

    Lets importers seed edges idempotently without touching SQL themselves.
    """
    rows = con.execute(
        """
        SELECT e.id FROM edges e
        JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst
        WHERE s.name = ? AND s.type = ? AND e.rel = ?
          AND t.name = ? AND t.type = ? AND e.valid_to IS NULL
        ORDER BY e.id
        """,
        (edge.src, edge.src_type, edge.rel, edge.dst, edge.dst_type),
    ).fetchall()
    return [r["id"] for r in rows]


@_reading
def canonical_entity(con: sqlite3.Connection, entity_id: int) -> int:
    row = con.execute(
        "SELECT canonical_id FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    return row["canonical_id"] or entity_id


@_serialized
def invalidate_edge(
    con: sqlite3.Connection,
    edge_id: int,
    *,
    successor: Edge | None = None,
    confidence: str = "EXTRACTED",
    source_doc: int | None = None,
    evidence: str | None = None,
    producer: str | None = None,
    run: str | None = None,
) -> int | None:
    """End an edge's validity now (``valid_to``), optionally inserting the
    edge that supersedes it. The old edge stays as history (invariant 8).
    Returns the successor's id."""
    cur = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE id = ? AND valid_to IS NULL",
        (edge_id,),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no such currently valid edge: {edge_id}")
    con.commit()
    if successor is None:
        return None
    return link(
        con,
        successor,
        confidence=confidence,
        source_doc=source_doc,
        evidence=evidence,
        producer=producer,
        run=run,
    )


@_serialized
def retire_reading(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    producer: str,
    except_version: str,
    reviews: bool = True,
) -> int:
    """End the live edges ``producer`` wrote from this document under any
    ontology version but ``except_version``: a producer re-reading a
    document under its current subset (another domain, a grown module)
    supersedes its own earlier reading. Other producers' edges stay.
    History is kept (invariant 8); returns how many edges.

    ``reviews`` also drops the document's open review items, which the
    new reading queues again: right for an extraction, wrong for a pass
    that queues none (the markup and references passes), which would
    drop the extraction's misfits with nothing to put them back."""
    cur = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE valid_to IS NULL"
        " AND source_doc = ? AND producer = ?"
        " AND coalesce(ontology_version, '') != ?",
        (doc_id, producer, except_version),
    )
    # the earlier reading's open review items are superseded as well: the
    # new reading queues its own misfits against the ontology it was read
    # under (review items carry no producer, so this is per document)
    if reviews:
        con.execute(
            f"UPDATE review_queue SET resolution = 'dropped', resolved_at = {_NOW}"
            " WHERE source_doc = ? AND resolution IS NULL"
            " AND coalesce(ontology_version, '') != ?",
            (doc_id, except_version),
        )
    con.commit()
    return cur.rowcount


def hidden_by_premise(
    con: sqlite3.Connection, edge_ids: list[int], hidden: frozenset[int]
) -> set[int]:
    """Of these edges, the derived ones (``edge_premises``) that stand on a
    premise from a hidden document: a derivation says what its premises
    say, so a viewer who may not see one may not see it either (the
    review of 2026-10-04: a module-limited token read research facts in a
    rule edge's evidence)."""
    if not hidden or not edge_ids:
        return set()
    out: set[int] = set()
    for i in range(0, len(edge_ids), 500):
        part = edge_ids[i : i + 500]
        marks = ",".join("?" * len(part))
        for eid, doc in con.execute(
            "SELECT p.edge_id, e.source_doc FROM edge_premises p"
            f" JOIN edges e ON e.id = p.premise_id WHERE p.edge_id IN ({marks})",
            part,
        ):
            if doc is not None and int(doc) in hidden:
                out.add(int(eid))
    return out


@_reading
def edge_hidden(con: sqlite3.Connection, edge_id: int) -> bool:
    """Whether the viewer may not see this edge: there is none, its
    document is hidden, or it is derived from a premise that is."""
    hidden = hidden_documents(con)
    row = con.execute(
        "SELECT source_doc FROM edges WHERE id = ?", (edge_id,)
    ).fetchone()
    if row is None:
        return True
    if row[0] is not None and int(row[0]) in hidden:
        return True
    return bool(hidden_by_premise(con, [edge_id], hidden))


@_serialized
def record_ending(con: sqlite3.Connection, edge_id: int, run: str) -> None:
    """Note that ``run`` ended this edge, for ``restore_run``."""
    con.execute(
        "INSERT OR IGNORE INTO edge_endings (edge_id, run, ended_at)"
        f" VALUES (?, ?, {_NOW})",
        (edge_id, run),
    )
    con.commit()


@_serialized
def record_correction(
    con: sqlite3.Connection, edge_id: int, run: str, corrected_by: int
) -> None:
    """Note that the edge ``run`` ended is corrected by ``corrected_by``,
    the edge the run wrote in its place (migration 46)."""
    con.execute(
        "UPDATE edge_endings SET corrected_by = ? WHERE edge_id = ? AND run = ?",
        (corrected_by, edge_id, run),
    )
    con.commit()


@_reading
def corrections_of(con: sqlite3.Connection, edge_id: int) -> dict[str, Any]:
    """What a repair said of an edge: the edge that corrects it
    (``corrected_by``, with the run) and the edges it corrects
    (``corrects``); empty when neither."""
    out: dict[str, Any] = {}
    row = con.execute(
        "SELECT corrected_by, run FROM edge_endings"
        " WHERE edge_id = ? AND corrected_by IS NOT NULL",
        (edge_id,),
    ).fetchone()
    if row is not None:
        out["corrected_by"] = {"edge_id": int(row[0]), "run": row[1]}
    corrects = [
        int(r[0])
        for r in con.execute(
            "SELECT edge_id FROM edge_endings WHERE corrected_by = ?", (edge_id,)
        )
    ]
    if corrects:
        out["corrects"] = corrects
    return out


@_serialized
def restore_run(con: sqlite3.Connection, run: str) -> dict[str, int]:
    """Undo a repair run: its own live edges are ended (``retire_run``),
    and every edge it ended (``edge_endings``) is stated again as a new
    edge with the old one's fact, document, evidence, producer, run and
    world dates. The ended edge stays ended: when prax held what is
    history. A fact that is live again by other means is not doubled."""
    retired = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE run = ? AND valid_to IS NULL", (run,)
    ).rowcount
    rows = con.execute(
        """
        SELECT x.edge_id, e.*, s.name AS s_name, s.type AS s_type,
               t.name AS t_name, t.type AS t_type
        FROM edge_endings x JOIN edges e ON e.id = x.edge_id
        JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst
        WHERE x.run = ? AND x.restated_as IS NULL
        """,
        (run,),
    ).fetchall()
    restated = 0
    for r in rows:
        live = con.execute(
            "SELECT id FROM edges WHERE src = ? AND rel = ? AND dst = ?"
            " AND source_doc IS ? AND valid_to IS NULL",
            (r["src"], r["rel"], r["dst"], r["source_doc"]),
        ).fetchone()
        if live is None:
            cur = con.execute(
                "INSERT INTO edges (src, rel, dst, confidence, source_doc, evidence,"
                " ontology_version, producer, run, world_from, world_from_precision,"
                f" world_to, world_to_precision, valid_from, ingested_at)"
                f" VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, {_NOW}, {_NOW})",
                (
                    r["src"],
                    r["rel"],
                    r["dst"],
                    r["confidence"],
                    r["source_doc"],
                    r["evidence"],
                    r["ontology_version"],
                    r["producer"],
                    r["run"],
                    r["world_from"],
                    r["world_from_precision"],
                    r["world_to"],
                    r["world_to_precision"],
                ),
            )
            new_id = int(cur.lastrowid or 0)
            restated += 1
        else:
            new_id = int(live["id"])
        con.execute(
            "UPDATE edge_endings SET restated_as = ? WHERE edge_id = ? AND run = ?",
            (new_id, r["edge_id"], run),
        )
    con.commit()
    return {"retired": retired, "restated": restated}


@_serialized
def retire_run(
    con: sqlite3.Connection,
    *,
    producer: str | None = None,
    run: str | None = None,
) -> int:
    """End every live edge a producer or a run wrote (both when both are
    given): what "upgrade" means once a better extractor has re-read the
    documents. History is kept (invariant 8); returns how many edges."""
    if producer is None and run is None:
        raise ValueError("retire_run needs a producer or a run")
    clauses = ["valid_to IS NULL"]
    args: list[Any] = []
    if producer is not None:
        clauses.append("producer = ?")
        args.append(producer)
    if run is not None:
        clauses.append("run = ?")
        args.append(run)
    cur = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE " + " AND ".join(clauses), args
    )
    con.commit()
    return cur.rowcount


@_reading
def provenance_summary(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Live and retired edge counts per producer and run."""
    rows = con.execute(
        """
        SELECT producer, run, sum(valid_to IS NULL) AS live,
               sum(valid_to IS NOT NULL) AS retired, min(ingested_at) AS first_at
        FROM edges GROUP BY producer, run ORDER BY first_at
        """
    ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def backfill_provenance(con: sqlite3.Connection) -> dict[str, int]:
    """Fill ``producer`` and ``run`` on edges written before migration 0007,
    from the evidence prefix (citations, pages) and the source document's
    stamps (Zotero seeds, extraction). Idempotent: only NULL producers."""
    counts: dict[str, int] = {}

    def tag(
        where: str, producer: str, run_expr: str, args: tuple[Any, ...] = ()
    ) -> None:
        cur = con.execute(
            f"UPDATE edges SET producer = ?, run = {run_expr}"
            f" WHERE producer IS NULL AND {where}",
            (producer, *args),
        )
        counts[producer] = counts.get(producer, 0) + cur.rowcount

    tag("evidence LIKE 'crossref %'", "crossref", "'backfill'")
    tag("evidence LIKE 'openalex %'", "openalex", "'backfill'")
    tag("evidence LIKE 'page %' OR evidence LIKE 'project %'", "page", "'backfill'")
    tag(
        "evidence IS NULL AND rel IN ('authored_by', 'published_in') AND source_doc IN"
        " (SELECT id FROM documents WHERE json_extract(meta, '$.source') = 'zotero')",
        "zotero",
        "'backfill'",
    )
    # extraction: the source document's stamp names the model; the edge's
    # ontology version names the run
    cur = con.execute(
        """
        UPDATE edges SET
            producer = COALESCE(
                (SELECT json_extract(meta, '$.extraction.extractor') FROM documents d
                 WHERE d.id = edges.source_doc), 'extraction'),
            run = 'ontology-v' || COALESCE(ontology_version, '?')
        WHERE producer IS NULL AND evidence IS NOT NULL
        """
    )
    counts["extraction"] = cur.rowcount
    cur = con.execute(
        "UPDATE edges SET producer = 'manual', run = 'backfill' WHERE producer IS NULL"
    )
    counts["manual"] = cur.rowcount
    con.commit()
    return counts


@_reading
def select_for_extraction(
    con: sqlite3.Connection,
    *,
    ontology_version: str,
    limit: int | None = None,
    mime_prefix: str | None = None,
    min_chars: int = 0,
    domain: str | None = None,
    onto: ontology.Ontology | None = None,
    sources: tuple[str, ...] | None = None,
    skip_mime_prefix: str | None = None,
) -> list[int]:
    """Indexed documents not yet extracted under ``ontology_version``
    (``meta.extraction.ontology_version``): the ones whose text was read
    again out from under an extraction first, then oldest first. ``min_chars``
    skips documents whose chunks hold less text than that (Zotero notes,
    scans without a text layer): nothing to extract, a call wasted.
    ``domain`` keeps the documents assigned to that module; with ``onto``
    a document with a domain set is compared against the version of its
    own subset of the ontology, not the whole (one query: a CASE over
    the domain sets in use). ``sources`` keeps documents of those
    ``meta.source`` values (captures); ``skip_mime_prefix`` drops images
    and the like."""
    stamp = "json_extract(meta, '$.extraction.ontology_version')"
    base = (
        "SELECT id FROM documents WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    )
    sql = ""  # the predicates every variant shares, after the scope's own
    args: list[Any] = []
    want, want_args = _subset_version_case(con, onto, ontology_version)
    sql += f" AND ({stamp} IS NULL OR {stamp} != {want})"
    args.extend(want_args)
    # a reading that failed under this version is not tried every pass
    # (extraction.note_failure); the version moving on re-selects it
    failed = "json_extract(meta, '$.extraction_error.ontology_version')"
    sql += f" AND ({failed} IS NULL OR {failed} != {want})"
    args.extend(want_args)
    if skip_mime_prefix:
        # a DjVu book has an image type and is a document: extracted
        kept = ",".join("?" * len(mimes.DOCUMENT_IMAGES))
        sql += f" AND (coalesce(mime, '') NOT LIKE ? ESCAPE '!' OR mime IN ({kept}))"
        args.append(_like_prefix(skip_mime_prefix))
        args.extend(mimes.DOCUMENT_IMAGES)
    if domain:  # the documents assigned to that module, and only those
        clause, held = domain_clause(con, [domain], unset=False, alias="documents")
        sql += clause
        args.extend(held)
    if min_chars > 0:
        # the chunks' text is never longer than the whole text, so the
        # column rules out the short ones for free; the sum over the
        # chunks is taken below, for candidates in order, until the limit
        # is filled (for every candidate before the sort it cost 1.5 s
        # after an ontology bump, 2026-09-30)
        sql += " AND text_len >= ?"
        args.append(min_chars)
    if mime_prefix:
        sql += " AND mime LIKE ? ESCAPE '!'"
        args.append(_like_prefix(mime_prefix))
    # a document whose text was read again out from under its extraction
    # (meta.extraction_stale) goes before the never-extracted backlog:
    # someone cared enough to re-read it, and the graph speaks of a text
    # that is gone until it is read again
    sql += " ORDER BY json_extract(meta, '$.extraction_stale') IS NULL, id"

    def enough(doc_id: int) -> bool:
        if min_chars <= 0:
            return True
        held = con.execute(
            "SELECT coalesce(sum(length(text)), 0) FROM chunks WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()[0]
        return int(held) >= min_chars

    # a scope of sources (the captures): the source index answers it. A
    # document whose reading a person asked for is due whatever its
    # source, like a promote flag; it goes first, from its own partial
    # index (an OR of the two turned the source index into a scan)
    asked = " AND json_extract(meta, '$.extraction_stale.requested') IS NOT NULL"
    marks = ",".join("?" * len(sources or ()))
    by_source = f" AND json_extract(meta, '$.source') IN ({marks})"
    queries = (
        [(base + sql, args)]
        if sources is None
        else [(base + asked + sql, args), (base + by_source + sql, [*sources, *args])]
    )
    out: list[int] = []
    seen: set[int] = set()
    for query, query_args in queries:
        for (doc_id,) in con.execute(query, query_args).fetchall():
            if doc_id in seen or not enough(doc_id):
                continue
            seen.add(doc_id)
            out.append(doc_id)
            if limit is not None and len(out) >= limit:
                return out
    return out


def _subset_version_case(
    con: sqlite3.Connection, onto: ontology.Ontology | None, whole: str
) -> tuple[str, list[Any]]:
    """The SQL for "the version this document's reading should carry": a
    CASE over the domain sets in use (few) mapping each to the version of
    its subset of ``onto``, ``whole`` for a document without a set. Without
    ``onto`` every document is held to ``whole``."""
    if onto is None:
        return "?", [whole]
    whens: list[str] = []
    args: list[Any] = []
    for (raw,) in con.execute(
        "SELECT DISTINCT json_extract(meta, '$.domains') FROM documents"
        " WHERE json_extract(meta, '$.domains') IS NOT NULL"
    ):
        try:
            domains = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not domains:
            continue
        whens.append("WHEN ? THEN ?")
        args.extend([raw, onto.for_domains(domains).version])
    if not whens:
        return "?", [whole]
    args.append(whole)
    return f"CASE json_extract(meta, '$.domains') {' '.join(whens)} ELSE ? END", args


@_reading
def entity_name(con: sqlite3.Connection, entity_id: int) -> str | None:
    """The name an entity shows."""
    row = con.execute("SELECT name FROM entities WHERE id = ?", (entity_id,)).fetchone()
    return str(row["name"]) if row else None


@_reading
def entities_with_degree(
    con: sqlite3.Connection, etype: str | None = None
) -> list[dict[str, Any]]:
    """``id, name, type, degree`` of every canonical entity (of ``etype``),
    oldest first; ``degree`` counts its live edges either way."""
    degree: dict[int, int] = {}
    for col in ("src", "dst"):  # two indexed group-bys instead of an OR per entity
        for r in con.execute(
            f"SELECT {col} AS id, count(*) AS n FROM edges WHERE valid_to IS NULL"
            f" GROUP BY {col}"
        ):
            degree[r["id"]] = degree.get(r["id"], 0) + r["n"]
    where = "WHERE canonical_id IS NULL" + (" AND type = ?" if etype else "")
    args: tuple[Any, ...] = (etype,) if etype else ()
    rows = con.execute(
        f"SELECT e.id, e.name, e.type FROM entities e {where} ORDER BY e.id", args
    ).fetchall()
    return [{**dict(r), "degree": degree.get(r["id"], 0)} for r in rows]
