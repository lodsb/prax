"""What follows from what is stated (stage AN): the closure of a transitive
relation, the converse of a symmetric one, the inverse of one that has
one (the relation's characteristics in the ontology's YAML).

A derived edge is INFERRED, signed by its rule (producer ``rule:<kind>``,
run ``rule:<relation>``), and written with its premises
(``edge_premises``), so "why is this here" has an answer. An asserted
edge always wins: what is stated is never written again as a derivation.
Premises come from the live asserted edges of open documents only (no
personal or suspected document, none retired): a derivation from a
personal note's facts would otherwise reach a viewer the note is hidden
from (stage U). A pass derives everything again and compares: a new
derivation is linked, one that still follows is kept (its premises
renewed), one that no longer follows is ended; nothing is deleted, and
retiring a run takes all of it back. ``RULE_CAP`` derivations a relation
at most, chains of ``RULE_DEPTH`` steps at most. A ``part_of`` whose names
say it runs the wrong way round (``ontology.part_of_suspect``) is no
premise: a closure carries one backwards fact into every chain through it.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from prax.graph import ontology

from ..base import _NOW, _reading, _scrubbed, _serialized
from .edges import Edge, invalidate_edge, link

RULE_CAP = 20_000  # derivations a relation at most
RULE_DEPTH = 6  # steps of a chain at most
RULE_BATCH = 500  # writes a hold of the write lock
EVIDENCE_CHARS = 300

Pair = tuple[int, int]
Plan = dict[Pair, list[int]]  # (src, dst) canonical entities -> premise edge ids


@_reading
def _premise_edges(
    con: sqlite3.Connection, rel: str
) -> list[tuple[int, int, int, str | None]]:
    """The live asserted edges of ``rel`` a rule may stand on: ``(edge id,
    src, dst, world_from)``, the ends as their canonical entities. Not a
    rule's own edge, not one from a document that is personal, suspected
    or retired."""
    rows = con.execute(
        """
        SELECT e.id, COALESCE(s.canonical_id, s.id) AS src,
               COALESCE(t.canonical_id, t.id) AS dst, e.world_from
        FROM edges e JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst
        LEFT JOIN documents d ON d.id = e.source_doc
        WHERE e.rel = ? AND e.valid_to IS NULL
          AND COALESCE(e.producer, '') NOT LIKE 'rule:%'
          AND (e.source_doc IS NULL OR (d.sensitivity IS NULL
               AND json_extract(d.meta, '$.retired') IS NULL))
        """,
        (rel,),
    ).fetchall()
    return [(int(r[0]), int(r[1]), int(r[2]), r[3]) for r in rows if r[1] != r[2]]


def closure(edges: list[tuple[int, int, int, str | None]], *, cap: int) -> Plan:
    """The pairs a transitive relation's chains reach that no edge states,
    each with the edges of one shortest chain: breadth first from every
    subject, ``RULE_DEPTH`` steps at most, ``cap`` pairs at most."""
    out_edges: dict[int, list[tuple[int, int]]] = defaultdict(list)
    stated: set[Pair] = set()
    for eid, a, b, _ in edges:
        out_edges[a].append((b, eid))
        stated.add((a, b))
    plan: Plan = {}
    for start in sorted(out_edges):
        # how each node was reached: (the node before, the edge)
        came: dict[int, tuple[int, int] | None] = {start: None}
        queue = deque([(start, 0)])
        while queue:
            node, depth = queue.popleft()
            if depth >= RULE_DEPTH:
                continue
            for nxt, eid in out_edges.get(node, ()):
                if nxt in came:
                    continue
                came[nxt] = (node, eid)
                queue.append((nxt, depth + 1))
        for node in came:
            if node == start or (start, node) in stated:
                continue
            chain: list[int] = []
            at = node
            while came[at] is not None:
                prev, eid = came[at]  # type: ignore[misc]
                chain.append(eid)
                at = prev
            plan[(start, node)] = list(reversed(chain))
            if len(plan) >= cap:
                return plan
    return plan


def converse(edges: list[tuple[int, int, int, str | None]], *, cap: int) -> Plan:
    """The other way round of each edge whose converse no edge states: a
    symmetric relation's, or (written under the other relation) an
    inverse's."""
    stated = {(a, b) for _, a, b, _ in edges}
    plan: Plan = {}
    for eid, a, b, _ in edges:
        if (b, a) not in stated and (b, a) not in plan:
            plan[(b, a)] = [eid]
            if len(plan) >= cap:
                break
    return plan


@_scrubbed
@_reading
def part_of_suspects(
    con: sqlite3.Connection, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """The live ``part_of`` edges whose names say they are wrong
    (``ontology.part_of_suspect``): ``edge_id, src, src_type, dst,
    dst_type, source_doc, producer, verdict, reason``, the rule pass's own
    edges left out."""
    onto = ontology.current()
    rows = con.execute(
        """
        SELECT e.id, s.name AS src, s.type AS src_type, t.name AS dst,
               t.type AS dst_type, e.source_doc, e.producer
        FROM edges e JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst
        WHERE e.rel = 'part_of' AND e.valid_to IS NULL
          AND COALESCE(e.producer, '') NOT LIKE 'rule:%'
        ORDER BY e.id
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        said = ontology.part_of_suspect(
            onto, r["src"], r["src_type"], r["dst"], r["dst_type"]
        )
        if said:
            out.append(
                {**dict(r), "edge_id": r["id"], "verdict": said[0], "reason": said[1]}
            )
            if limit and len(out) >= limit:
                break
    for o in out:
        o.pop("id", None)
    return out


@_reading
def _rule_edges(con: sqlite3.Connection, run: str) -> dict[Pair, list[int]]:
    """A run's live derived edges by their (canonical) ends: a merge can
    fold two of them onto one pair, and both must be seen to end one."""
    rows = con.execute(
        "SELECT e.id, COALESCE(s.canonical_id, s.id), COALESCE(t.canonical_id, t.id)"
        " FROM edges e JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
        " WHERE e.run = ? AND e.valid_to IS NULL",
        (run,),
    ).fetchall()
    out: dict[Pair, list[int]] = defaultdict(list)
    for r in rows:
        out[(int(r[1]), int(r[2]))].append(int(r[0]))
    return dict(out)


@_reading
def _names(con: sqlite3.Connection, ids: set[int]) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    items = sorted(ids)
    for i in range(0, len(items), 500):
        part = items[i : i + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT id, name, type FROM entities WHERE id IN ({marks})", part
        ):
            out[int(r[0])] = (str(r[1]), str(r[2]))
    return out


@_reading
def _fact_lines(con: sqlite3.Connection, ids: list[int]) -> dict[int, str]:
    """``A rel B`` for each premise, for a derived edge's evidence."""
    out: dict[int, str] = {}
    for i in range(0, len(ids), 500):
        part = ids[i : i + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT e.id, s.name, e.rel, t.name FROM edges e"
            f" JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
            f" WHERE e.id IN ({marks})",
            part,
        ):
            out[int(r[0])] = f"{r[1]} {r[2]} {r[3]}"
    return out


@_serialized
def _write(
    con: sqlite3.Connection,
    rel: str,
    kind: str,
    run: str,
    items: list[tuple[Pair, list[int], str | None]],
    names: dict[int, tuple[str, str]],
    lines: dict[int, str],
) -> tuple[int, int]:
    """Link one batch of derivations with their premises; ``(written,
    refused)``, refused being those the ontology does not take."""
    written = refused = 0
    for (a, b), premises, world_from in items:
        if a not in names or b not in names:
            continue
        (sa, ta), (sb, tb) = names[a], names[b]
        because = "; ".join(lines.get(p, "?") for p in premises)
        try:
            eid = link(
                con,
                Edge(sa, ta, rel, sb, tb),
                confidence="INFERRED",
                evidence=f"follows from: {because}"[:EVIDENCE_CHARS],
                producer=f"rule:{kind}",
                run=run,
                world_from=world_from,
            )
        except ValueError:
            refused += 1  # a chain across types the relation does not join
            continue
        con.executemany(
            "INSERT OR IGNORE INTO edge_premises (edge_id, premise_id) VALUES (?, ?)",
            [(eid, p) for p in premises],
        )
        written += 1
    con.commit()
    return written, refused


@_serialized
def _end(con: sqlite3.Connection, edge_ids: list[int]) -> None:
    for eid in edge_ids:
        invalidate_edge(con, eid)
    con.commit()


@_serialized
def _renew(con: sqlite3.Connection, kept: dict[int, list[int]]) -> None:
    """A kept derivation's premises as the pass found them this time."""
    for eid, premises in kept.items():
        con.execute("DELETE FROM edge_premises WHERE edge_id = ?", (eid,))
        con.executemany(
            "INSERT INTO edge_premises (edge_id, premise_id) VALUES (?, ?)",
            [(eid, p) for p in premises],
        )
    con.commit()


def derive_rules(
    con: sqlite3.Connection,
    *,
    cap: int = RULE_CAP,
    log: Callable[[str], None] | None = None,
) -> dict[str, dict[str, int]]:
    """One pass of every rule the ontology's characteristics give, kept in
    step with what is stated: per relation, how many derivations were
    added, kept, ended and refused (a chain the relation's types do not
    take), and whether the cap stopped it."""
    say = log or (lambda _t: None)
    onto = ontology.current()
    jobs: list[tuple[str, str, str]] = []  # (relation read, relation written, kind)
    for r in onto.relations.values():
        if r.transitive:
            jobs.append((r.name, r.name, "transitive"))
        if r.symmetric:
            jobs.append((r.name, r.name, "symmetric"))
        if r.inverse_of and r.inverse_of in onto.relations:
            jobs.append((r.name, r.inverse_of, "inverse"))
    report: dict[str, dict[str, int]] = {}
    for read, writes, kind in sorted(jobs):
        edges = _premise_edges(con, read)
        if read == "part_of":
            doubted = {int(x["edge_id"]) for x in part_of_suspects(con)}
            edges = [e for e in edges if e[0] not in doubted]
        plan = (
            closure(edges, cap=cap)
            if kind == "transitive"
            else converse(edges, cap=cap)
        )
        if kind == "inverse":
            stated_inverse = {(a, b) for _, a, b, _ in _premise_edges(con, writes)}
            plan = {k: v for k, v in plan.items() if k not in stated_inverse}
        run = f"rule:{writes}" if kind != "inverse" else f"rule:{writes}<-{read}"
        held = _rule_edges(con, run)
        # one edge stands for a pair; a second one a merge folded onto it
        # is ended with those that no longer follow
        have = {k: ids[0] for k, ids in held.items()}
        doubled = [eid for ids in held.values() for eid in ids[1:]]
        world = {eid: wf for eid, _, _, wf in edges}
        new = [k for k in plan if k not in have]
        names = _names(con, {x for k in new for x in k})
        lines = _fact_lines(con, sorted({p for k in new for p in plan[k]}))
        added = refused = 0
        for i in range(0, len(new), RULE_BATCH):
            batch = [
                (
                    k,
                    plan[k],
                    max((w for p in plan[k] if (w := world.get(p))), default=None),
                )
                for k in new[i : i + RULE_BATCH]
            ]
            got = _write(con, writes, kind, run, batch, names, lines)
            added += got[0]
            refused += got[1]
        gone = [eid for k, eid in have.items() if k not in plan] + doubled
        for i in range(0, len(gone), RULE_BATCH):
            _end(con, gone[i : i + RULE_BATCH])
        kept = {eid: plan[k] for k, eid in have.items() if k in plan}
        kept_items = list(kept.items())
        for i in range(0, len(kept_items), RULE_BATCH):
            _renew(con, dict(kept_items[i : i + RULE_BATCH]))
        report[run] = {
            "premises": len(edges),
            "added": added,
            "kept": len(kept),
            "ended": len(gone),
            "refused": refused,
            "capped": int(len(plan) >= cap),
        }
        say(f"{run}: {report[run]}")
    return report


@_scrubbed
@_reading
def edge_premises(con: sqlite3.Connection, edge_id: int) -> list[dict[str, Any]]:
    """What a derived edge follows from: its premises as edges (``edge_id,
    src, rel, dst, source_doc, evidence``), in the order of the chain."""
    rows = con.execute(
        "SELECT e.id, s.name AS src, e.rel, t.name AS dst, e.source_doc, e.evidence"
        " FROM edge_premises p JOIN edges e ON e.id = p.premise_id"
        " JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
        " WHERE p.edge_id = ? ORDER BY p.rowid",
        (edge_id,),
    ).fetchall()
    return [
        {
            "edge_id": int(r["id"]),
            "src": r["src"],
            "rel": r["rel"],
            "dst": r["dst"],
            "source_doc": r["source_doc"],
            "evidence": r["evidence"],
        }
        for r in rows
    ]


CONFLICT_PAIRS = 6  # pairs a subject's values make at most
CONFLICT_PRODUCER = "rule:functional"


@_reading
def part_of_roots(con: sqlite3.Connection) -> Callable[[int], int]:
    """The root of each entity under the live ``part_of`` edges: two
    values of a functional relation with one root are one answer said
    finer and coarser ("NIME 2010" and "NIME")."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in con.execute(
        "SELECT COALESCE(s.canonical_id, s.id), COALESCE(t.canonical_id, t.id)"
        " FROM edges e JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
        " WHERE e.rel = 'part_of' AND e.valid_to IS NULL"
    ):
        parent[find(int(a))] = find(int(b))
    return find


@_reading
def _functional_pairs(
    con: sqlite3.Connection, rel: str, root: Callable[[int], int]
) -> set[tuple[int, int]]:
    """The pairs of live asserted edges of ``rel`` that disagree: one
    subject, two values with different roots. One edge stands for each
    value (an EXTRACTED one first, then the oldest), and a subject makes
    ``CONFLICT_PAIRS`` pairs at most."""
    rows = con.execute(
        """
        SELECT e.id, COALESCE(s.canonical_id, s.id) AS subject,
               COALESCE(t.canonical_id, t.id) AS value, e.confidence
        FROM edges e JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst
        WHERE e.rel = ? AND e.valid_to IS NULL
          AND COALESCE(e.producer, '') NOT LIKE 'rule:%'
        ORDER BY e.id
        """,
        (rel,),
    ).fetchall()
    by_subject: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    for r in rows:
        key = root(int(r["value"]))
        rank = (0 if r["confidence"] == "EXTRACTED" else 1, int(r["id"]))
        held = by_subject[int(r["subject"])].get(key)
        if held is None or rank < held:
            by_subject[int(r["subject"])][key] = rank
    out: set[tuple[int, int]] = set()
    for values in by_subject.values():
        if len(values) < 2:
            continue
        ids = sorted(eid for _, eid in values.values())
        pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]]
        out.update(pairs[:CONFLICT_PAIRS])
    return out


@_serialized
def _write_conflicts(
    con: sqlite3.Connection,
    rel: str,
    added: list[tuple[int, int]],
    ended: list[int],
) -> None:
    run = f"conflicts:{rel}"
    con.executemany(
        "INSERT INTO edge_conflicts (edge_a, edge_b, rel, producer, run, found_at)"
        f" VALUES (?, ?, ?, ?, ?, {_NOW})",
        [(a, b, rel, CONFLICT_PRODUCER, run) for a, b in added],
    )
    con.executemany(
        f"UPDATE edge_conflicts SET ended_at = {_NOW} WHERE id = ?",
        [(i,) for i in ended],
    )
    con.commit()


def find_conflicts(
    con: sqlite3.Connection, *, log: Callable[[str], None] | None = None
) -> dict[str, dict[str, int]]:
    """One pass over every relation the ontology calls functional, kept in
    step with the edges: pairs that disagree now and were not recorded are
    added, recorded ones that no longer disagree (an edge ended, a merge,
    a ``part_of`` that joined the two values) are ended. Nothing is
    deleted and no fact is ended: a disagreement is shown, a person
    decides."""
    say = log or (lambda _t: None)
    root = part_of_roots(con)
    report: dict[str, dict[str, int]] = {}
    for rel in sorted(
        r.name for r in ontology.current().relations.values() if r.functional
    ):
        now = _functional_pairs(con, rel, root)
        held = {
            (int(r[1]), int(r[2])): int(r[0])
            for r in con.execute(
                "SELECT id, edge_a, edge_b FROM edge_conflicts"
                " WHERE rel = ? AND ended_at IS NULL",
                (rel,),
            )
        }
        added = sorted(now - held.keys())
        ended = [i for pair, i in held.items() if pair not in now]
        _write_conflicts(con, rel, added, ended)
        report[rel] = {"open": len(now), "added": len(added), "ended": len(ended)}
        say(f"conflicts {rel}: {report[rel]}")
    return report


@_scrubbed
@_reading
def edge_conflicts(con: sqlite3.Connection, edge_id: int) -> list[dict[str, Any]]:
    """The facts an edge disagrees with (open conflicts): each the other
    edge as ``edge_id, src, rel, dst, source_doc, evidence``."""
    rows = con.execute(
        """
        SELECT o.id, s.name AS src, o.rel, t.name AS dst, o.source_doc, o.evidence
        FROM edge_conflicts c
        JOIN edges o ON o.id = CASE WHEN c.edge_a = ? THEN c.edge_b ELSE c.edge_a END
        JOIN entities s0 ON s0.id = o.src
        JOIN entities s ON s.id = COALESCE(s0.canonical_id, s0.id)
        JOIN entities t0 ON t0.id = o.dst
        JOIN entities t ON t.id = COALESCE(t0.canonical_id, t0.id)
        WHERE (c.edge_a = ? OR c.edge_b = ?) AND c.ended_at IS NULL
        ORDER BY o.id
        """,
        (edge_id, edge_id, edge_id),
    ).fetchall()
    return [
        {
            "edge_id": int(r["id"]),
            "src": r["src"],
            "rel": r["rel"],
            "dst": r["dst"],
            "source_doc": r["source_doc"],
            "evidence": r["evidence"],
        }
        for r in rows
    ]
