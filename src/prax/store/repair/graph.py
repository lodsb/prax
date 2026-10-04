"""The ailments of the graph: entities named after the prompt, mangled or
split names, edges that loop or point at a container, part_of the wrong
way round, functional relations with two values, slow walks, stray
ontology versions."""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

from prax.graph import ontology

from ..graph import (
    Edge,
    entities_with_degree,
    entity_named_in,
    link,
    part_of_roots,
    part_of_suspects,
    rename_entity,
    traverse_map,
)
from .common import (
    CAP,
    _entity_rows,
    _invalidate,
    _live_edge_counts,
    _repair_entities,
    _with_edge_counts,
)

# Words a model copies out of its own prompt instead of naming a thing.
# Conservative on purpose: anything that could be a real concept in a
# library about sound, text or making ("text", "value", "subject") is left
# out, because a wrong repair costs more than an unrepaired edge.
PLACEHOLDER_NAMES = frozenset(
    {
        "source name",
        "target name",
        "entity name",
        "concept name",
        "method name",
        "tool name",
        "author name",
        "document title",
        "paper title",
        "the document",
        "the source",
        "the target",
        "name",
        "title",
        "unknown",
        "unknown author",
        "n/a",
        "na",
        "none",
        "null",
        "nil",
        "tbd",
        "todo",
        "placeholder",
        "xxx",
        "...",
        "-",
        "--",
        "?",
        "no name",
        "not specified",
        "not stated",
        "not mentioned",
        "unnamed",
        "untitled",
    }
)


# A bracketed reference number is never a name (the ontology says so in as
# many words); neither is a figure, table or equation number on its own.
REFERENCE_NAME = re.compile(
    r"^(?:\[|\()?\s*"
    r"(?:ref\.?|reference|fig\.?|figure|table|eq\.?|equation|section|sec\.?"
    r"|chapter|ch\.?)?"
    r"\s*\d{1,4}(?:\s*[-–,]\s*\d{1,4})?\s*(?:\]|\))?$",
    re.IGNORECASE,
)


# Markup and line breaks a citation importer leaves in a title, as in
# "<i>The Origins of Music</i>" or a title broken across the lines of
# the XML it came from, indentation and all.
MARKUP = re.compile(r"<[^>]{1,40}>")


MANGLED = re.compile("<[^>]{1,40}>|[" + chr(10) + chr(13) + chr(9) + "]|  ")


# A name longer than this is a whole citation or a paragraph rather than a
# name — except for a claim, which the ontology defines as a sentence.
NAME_TOO_LONG = 300


# an extractor's own wire syntax glued to a name: a line the model wrote in
# the triple format and the parser took whole — "chord dst_type=concept
# (confidence=EXTRACTED evidence=…", "no_correspondence(dst=Sundberg)"
WIRE = re.compile(
    r"\s*\(?\b(?:dst_type|src_type|triple_src|confidence|evidence|evedence|src|dst"
    r"|rel|type)\s*=.*$",
    re.IGNORECASE | re.DOTALL,
)


SENTENCE_TYPES = ("claim",)


FUNCTIONAL_SHOWN = 200  # subjects a functional-conflicts finding lists


def _backwards_part_of(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """The live ``part_of`` edges whose names say they are wrong
    (``ontology.part_of_suspect``): reversed, a misfit either way round,
    or doubtful, the verdict and its reason on each."""
    return [{**r, "id": r["edge_id"]} for r in part_of_suspects(con, limit=CAP)]


def _functional_conflicts(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """The subjects with two or more live values of a relation the ontology
    calls functional (``published_in``: a paper in one venue), each with
    its values and how many edges say each (stage AN: a constraint's
    breach is a finding for a person, never an edge). Two values where
    one is ``part_of`` the other are one answer, said finer and coarser: a
    paper in "NIME 2010" and in "NIME" is in one venue."""
    find = part_of_roots(con)
    out: list[dict[str, Any]] = []
    for rel in sorted(
        r.name for r in ontology.current().relations.values() if r.functional
    ):
        rows = con.execute(
            """
            SELECT cs.name AS subject, cs.type AS type, ct.id AS vid,
                   ct.name AS value, count(*) AS n
            FROM edges e
            JOIN entities s ON s.id = e.src
            JOIN entities cs ON cs.id = COALESCE(s.canonical_id, s.id)
            JOIN entities t ON t.id = e.dst
            JOIN entities ct ON ct.id = COALESCE(t.canonical_id, t.id)
            WHERE e.rel = ? AND e.valid_to IS NULL
            GROUP BY cs.id, ct.id
            """,
            (rel,),
        ).fetchall()
        by_subject: dict[tuple[str, str], list[dict[str, Any]]] = {}
        clusters: dict[tuple[str, str], set[int]] = {}
        for r in rows:
            key = (r["subject"], r["type"])
            by_subject.setdefault(key, []).append(
                {"value": r["value"], "edges": int(r["n"])}
            )
            clusters.setdefault(key, set()).add(find(int(r["vid"])))
        for (subject, etype), values in sorted(by_subject.items()):
            if len(clusters[(subject, etype)]) > 1:
                out.append(
                    {
                        "relation": rel,
                        "subject": subject,
                        "type": etype,
                        "values": values,
                    }
                )
                if len(out) >= FUNCTIONAL_SHOWN:
                    return out
    return out


def _placeholder_entities(con: sqlite3.Connection) -> list[dict[str, Any]]:
    marks = ",".join("?" * len(PLACEHOLDER_NAMES))
    return _entity_rows(
        con, f"lower(trim(name)) IN ({marks})", tuple(sorted(PLACEHOLDER_NAMES))
    )


def _reference_entities(con: sqlite3.Connection) -> list[dict[str, Any]]:
    short = con.execute(
        "SELECT id, name, type FROM entities WHERE length(name) <= 24"
    ).fetchall()
    return _with_edge_counts(
        con, [r for r in short if REFERENCE_NAME.match((r["name"] or "").strip())]
    )


def _unnamed_entities(con: sqlite3.Connection) -> list[dict[str, Any]]:
    marks = ",".join("?" * len(SENTENCE_TYPES))
    return _entity_rows(
        con,
        f"trim(name) = '' OR (length(name) > ? AND type NOT IN ({marks}))",
        (NAME_TOO_LONG, *SENTENCE_TYPES),
    )


def clean_name(name: str) -> str:
    """A name with the markup taken out and its whitespace collapsed."""
    return re.sub(r"\s+", " ", MARKUP.sub("", name)).strip()


def _mangled_names(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Only entities that are a name in their own right: an alias merged
    into another entity keeps the string it was found under, which is
    history rather than a name, and mending it would only collide with the
    clean one it already points at."""
    found = []
    for row in con.execute(
        "SELECT id, name, type FROM entities WHERE canonical_id IS NULL"
    ):
        if not MANGLED.search(row["name"] or ""):
            continue
        cleaned = clean_name(row["name"])
        if cleaned and cleaned != row["name"]:
            found.append({**dict(row), "cleaned": cleaned})
    counts = _live_edge_counts(con, [r["id"] for r in found])
    for row in found:
        row["edges"] = counts.get(row["id"], 0)
    return found[:CAP]


# the kinds a document is: a document beside its topic (the paper
# *Timbre*, the concept timbre) is two things, not one in pieces
DOCUMENT_KINDS = frozenset(
    {
        "paper",
        "document",
        "page",
        "project",
        "work",
        "manual",
        "article",
        "datasheet",
        "schematic",
        "recipe",
        "build",
    }
)


def _split_names(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """One name held by things of unrelated types: `SuperCollider` a tool
    with 65 edges and a method with 7, `TU Munich` an organization and an
    author. Mostly one thing the extraction typed differently from
    document to document, sometimes two things (the concept *music
    perception*, the journal *Music Perception*); which is a person's call.

    A type and its subtype, and one type twice, are left to resolution,
    which merges them on the door's clock; a document beside its topic is
    two things and is not listed. The biggest part first, with the others
    and their edges (docs/eval/fractured-names-2026-09-27.md)."""

    onto = ontology.current()

    def related(a: str, b: str) -> bool:
        return a == b or onto.is_a(a, b) or onto.is_a(b, a)

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in con.execute(
        "SELECT id, name, type FROM entities WHERE canonical_id IS NULL"
    ):
        if row["type"] in DOCUMENT_KINDS or not (row["name"] or "").strip():
            continue
        groups.setdefault(row["name"].casefold().strip(), []).append(dict(row))
    split = [
        g
        for g in groups.values()
        if len(g) > 1 and any(not related(a["type"], b["type"]) for a in g for b in g)
    ]
    counts = _live_edge_counts(con, [e["id"] for g in split for e in g])
    found = []
    for g in split:
        parts = sorted(
            ({**e, "edges": counts.get(e["id"], 0)} for e in g),
            key=lambda e: (-e["edges"], e["id"]),
        )
        parts = [e for e in parts if e["edges"]]
        if len({e["type"] for e in parts}) < 2:
            continue
        lead = parts[0]
        found.append(
            {
                "id": lead["id"],
                "name": lead["name"],
                "type": lead["type"],
                "edges": lead["edges"],
                "also": [
                    {"id": e["id"], "type": e["type"], "edges": e["edges"]}
                    for e in parts[1:]
                ],
            }
        )
    found.sort(key=lambda f: -(f["edges"] + sum(a["edges"] for a in f["also"])))
    return found[:CAP]


def split_names_page(
    con: sqlite3.Connection, *, offset: int = 0, limit: int = 30
) -> dict[str, Any]:
    """The names held by things of unrelated types (``split-names``) that a
    person has not settled: a group every pair of which was kept apart is
    left out, and one whose parts were merged is gone by itself. Each part
    with a document naming it, for the review page."""
    decided = {
        (int(r[0]), int(r[1]))
        for r in con.execute(
            "SELECT a, b FROM entity_candidates WHERE decided = 'different'"
        )
    }
    open_groups = []
    for g in _split_names(con):
        ids = [g["id"], *(a["id"] for a in g["also"])]
        pairs = {(min(x, y), max(x, y)) for x in ids for y in ids if x != y}
        if not pairs <= decided:
            open_groups.append(g)
    page = open_groups[max(0, offset) : max(0, offset) + max(1, min(limit, 200))]
    items = []
    for g in page:
        parts = [
            {"id": g["id"], "type": g["type"], "edges": g["edges"]},
            *g["also"],
        ]
        for part in parts:
            part["document"] = entity_named_in(con, int(part["id"]))
        items.append({"name": g["name"], "parts": parts})
    return {"total": len(open_groups), "items": items}


def _container_citations(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """`cites` edges whose target is the container a work appeared in — a
    proceedings volume, a journal, a conference series — rather than the
    work itself.

    The extraction reached for the volume on the reference line instead of
    the paper printed above it, so the citation points at a container and
    names no work. One volume collected 733 of these here and became the
    largest node in the library
    (``docs/eval/traverse-neighbourhood-2026-09-25.md``). The prompt now
    asks for the individual work (``prax.graph.extraction``), so a library
    started today grows none; this is what predates the prompt.

    Only the citation ends. The container is **not** retyped: 38 of the
    914 container-named entities here are real documents in the library —
    someone imported the whole volume — so their own edges are earned and
    a blanket retype would have ended 3,844 of them. The words are the
    ontology's (``lexicon.by_type``), not a pattern here.
    """

    lex = ontology.lexicon()
    found = []
    for row in con.execute(
        "SELECT e.id, e.src, e.dst, e.source_doc, s.name AS citing,"
        " d.name AS container FROM edges e"
        " JOIN entities s ON s.id = e.src JOIN entities d ON d.id = e.dst"
        " WHERE e.valid_to IS NULL AND e.rel = 'cites'"
    ):
        if lex.type_of(row["container"] or "") == "venue":
            found.append(dict(row))
    return found[:CAP]


def _wire_names(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Unmerged entities whose name holds the wire syntax, with the name
    cut at it (``cleaned``; empty when nothing but syntax was there)."""
    found = []
    for row in con.execute(
        "SELECT id, name, type FROM entities WHERE canonical_id IS NULL"
    ):
        if not WIRE.search(row["name"] or ""):
            continue
        cleaned = clean_name(WIRE.sub("", row["name"]).strip(" (,;:"))
        found.append({**dict(row), "cleaned": cleaned})
    counts = _live_edge_counts(con, [r["id"] for r in found])
    for row in found:
        row["edges"] = counts.get(row["id"], 0)
    # nothing but syntax and no live edge: already dealt with, invisible
    return [r for r in found if r["cleaned"] or r["edges"]][:CAP]


def _self_edges(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT g.id, g.rel, n.name, g.source_doc
        FROM edges g
        JOIN entities a ON a.id = g.src
        JOIN entities b ON b.id = g.dst
        JOIN entities n ON n.id = COALESCE(a.canonical_id, a.id)
        WHERE g.valid_to IS NULL
          AND COALESCE(a.canonical_id, a.id) = COALESCE(b.canonical_id, b.id)
        ORDER BY g.id LIMIT ?
        """,
        (CAP,),
    ).fetchall()
    return [dict(r) for r in rows]


# The graph's threshold (CLAUDE.md, "Decision thresholds"): a walk from
# one of the most connected entities, warm, past this many milliseconds
# for its number of hops. On 2026-09-30, at 160,571 entities and after
# migration 0031: one hop 3 to 41 ms (the route's default), two hops 34
# to 276 ms, over the ten most connected
SLOW_WALK_MS = {1: 50.0, 2: 500.0}


WALKS_TIMED = 10  # the most connected entities walked from


def _slow_walks(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """The walks that answer slower than ``SLOW_WALK_MS`` for their hops:
    each the best of two, so a cold page cache is not the finding."""
    top = sorted(entities_with_degree(con), key=lambda e: -int(e["degree"]))
    slow = []
    for e in top[:WALKS_TIMED]:
        for hops, limit in sorted(SLOW_WALK_MS.items()):
            best = float("inf")
            for _ in range(2):
                t0 = time.perf_counter()
                traverse_map(con, str(e["name"]), hops, type=str(e["type"]))
                best = min(best, (time.perf_counter() - t0) * 1000)
            if best > limit:
                slow.append(
                    {
                        "entity": e["name"],
                        "type": e["type"],
                        "degree": int(e["degree"]),
                        "hops": hops,
                        "ms": round(best, 1),
                    }
                )
    return slow


HEAL_PRODUCER = "heal:part_of-direction"


def _repair_part_of(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Turn a reversed ``part_of`` round (the old edge ended, the turned
    one written with its document and evidence, INFERRED, by this repair)
    and end a misfit. A doubtful one is left for a person: a topic is in
    an exercise sheet as often as a course is wrongly said to be. A turn
    the ontology does not take is left alone."""
    onto = ontology.current()
    done = 0
    for row in rows:
        if row["verdict"] == "misfit":
            done += _invalidate(con, [int(row["id"])])
            continue
        if row["verdict"] != "reversed":
            continue
        turned = Edge(
            row["dst"], row["dst_type"], "part_of", row["src"], row["src_type"]
        )
        try:
            onto.check_edge(turned.src_type, turned.rel, turned.dst_type)
        except ValueError:
            continue
        old = con.execute(
            "SELECT evidence, world_from, world_to FROM edges WHERE id = ?",
            (row["id"],),
        ).fetchone()
        if not _invalidate(con, [int(row["id"])]):
            continue
        link(
            con,
            turned,
            confidence="INFERRED",
            source_doc=row["source_doc"],
            evidence=old["evidence"] if old else None,
            producer=HEAL_PRODUCER,
            run=HEAL_PRODUCER,
            world_from=old["world_from"] if old else None,
            world_to=old["world_to"] if old else None,
        )
        done += 1
    return done


def _repair_names(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Clean the name in place, or merge into the entity that already
    carries the clean one (a paper cited twice, once with markup)."""
    done = 0
    for row in rows:
        try:
            how = rename_entity(con, row["id"], row["cleaned"])
        except KeyError:  # merged away by an earlier row of this pass
            continue
        except ValueError:
            # the clean name is already an alias of this one: an earlier
            # resolution pass merged them the other way round and the graph
            # already treats them as one thing. Only the display name is
            # ugly, and flipping which of the two is canonical is not
            # something a repair should do behind a person's back.
            continue
        if how != "unchanged":
            done += 1
    return done


def _repair_wire_names(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Mend the name where one is left after the cut (the edge under it
    is real); end the edges of the few that were nothing but syntax."""
    named = [r for r in rows if r["cleaned"]]
    junk = [r for r in rows if not r["cleaned"]]
    return _repair_names(con, named) + (_repair_entities(con, junk) if junk else 0)


# A composed version names its modules, each with its own version
# ("core4+research9"). A name there that is a file beside the modules
# (genres, subjects, the lexicon) was a door reading that file as a module
# before it knew its name: 2026-09-29, "+genres1" and "+subjects1".
_VERSION_PART = re.compile(r"(?P<name>[a-z_]+)(?P<version>\d+)")


def _without_stray(version: str) -> str | None:
    """The version without the parts that name no module, or None when
    every part names one."""
    parts = version.split("+")
    kept = [
        p
        for p in parts
        if not (
            (m := _VERSION_PART.fullmatch(p)) and m.group("name") in ontology.BESIDE
        )
    ]
    return "+".join(kept) if len(kept) < len(parts) else None


def _stray_version_modules(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table, live in (
        ("edges", "valid_to IS NULL"),
        ("review_queue", "resolved_at IS NULL"),
    ):
        for version, n in con.execute(
            f"SELECT ontology_version, count(*) FROM {table}"
            f" WHERE {live} AND ontology_version LIKE '%+%'"
            " GROUP BY ontology_version"
        ):
            right = _without_stray(str(version))
            if right is not None:
                rows.append(
                    {"table": table, "version": version, "right": right, "count": n}
                )
    return rows


def _repair_stray_versions(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    done = 0
    for r in rows:
        live = "valid_to IS NULL" if r["table"] == "edges" else "resolved_at IS NULL"
        cur = con.execute(
            f"UPDATE {r['table']} SET ontology_version = ?"
            f" WHERE ontology_version = ? AND {live}",
            (r["right"], r["version"]),
        )
        done += cur.rowcount
    con.commit()
    return done


NOT_VENUE_PRODUCER = "heal:not-venues"
# what a "published in" a non-venue was meant to say, by what the name is
NOT_VENUE_FACT = {
    "publisher": "published_by",
    "company": "published_by",
    "institution": "written_at",
}


def _not_venues(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Entities typed as a venue that a paper is said to be "published in"
    and whose name says they are not one (``venues.not_a_venue``): a
    publisher, a company, a university or institute, or nothing of the
    kind (a date, a semester, an exercise sheet, a licence). Each with
    what it is and how many ``published_in`` edges reach it."""
    from prax.graph import venues

    rows = con.execute(
        """
        SELECT COALESCE(t.canonical_id, t.id) AS id, count(*) AS edges
        FROM edges e JOIN entities t ON t.id = e.dst
        WHERE e.rel = 'published_in' AND e.valid_to IS NULL
        GROUP BY COALESCE(t.canonical_id, t.id)
        """
    ).fetchall()
    counts = {int(r["id"]): int(r["edges"]) for r in rows}
    out: list[dict[str, Any]] = []
    for i in range(0, len(counts), 500):
        part = sorted(counts)[i : i + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT id, name, type FROM entities WHERE id IN ({marks})", part
        ):
            if r["type"] != "venue":
                continue
            kind = venues.not_a_venue(str(r["name"]))
            if kind:
                out.append(
                    {
                        "id": int(r["id"]),
                        "name": r["name"],
                        "is": kind,
                        "edges": counts[int(r["id"])],
                    }
                )
    out.sort(key=lambda x: (-x["edges"], x["name"]))
    return out[:CAP]


def _repair_not_venues(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """End each ``published_in`` edge into such an entity, and write what
    it was meant to say beside it: ``published_by`` the publisher or the
    company, ``written_at`` the institution, both of an organization by
    the same name; for a date or an exercise sheet nothing. The new edge
    keeps the old one's document, evidence and world dates, INFERRED, by
    this repair (``heal:not-venues``), so the run is retired whole if it
    was wrong."""
    onto = ontology.current()
    done = 0
    for row in rows:
        edges = con.execute(
            """
            SELECT e.id, s.name AS src, s.type AS src_type, e.source_doc,
                   e.evidence, e.world_from, e.world_to
            FROM edges e JOIN entities t ON t.id = e.dst
            JOIN entities s0 ON s0.id = e.src
            JOIN entities s ON s.id = COALESCE(s0.canonical_id, s0.id)
            WHERE e.rel = 'published_in' AND e.valid_to IS NULL
              AND COALESCE(t.canonical_id, t.id) = ?
            """,
            (row["id"],),
        ).fetchall()
        rel = NOT_VENUE_FACT.get(row["is"])
        for e in edges:
            edge = (
                Edge(e["src"], e["src_type"], rel, row["name"], "organization")
                if rel
                else None
            )
            if edge is not None:
                try:
                    onto.check_edge(edge.src_type, edge.rel, edge.dst_type)
                except ValueError:
                    edge = None  # what it was meant to say does not fit: ended only
            if not _invalidate(con, [int(e["id"])]):
                continue
            if edge is not None:
                link(
                    con,
                    edge,
                    confidence="INFERRED",
                    source_doc=e["source_doc"],
                    evidence=e["evidence"],
                    producer=NOT_VENUE_PRODUCER,
                    run=NOT_VENUE_PRODUCER,
                    world_from=e["world_from"],
                    world_to=e["world_to"],
                )
            done += 1
    return done
