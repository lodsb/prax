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
at most, chains of ``RULE_DEPTH`` steps at most.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from prax.graph import ontology

from ..base import _reading, _scrubbed, _serialized
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


@_reading
def _rule_edges(con: sqlite3.Connection, run: str) -> dict[Pair, int]:
    """A run's live derived edges by their (canonical) ends."""
    rows = con.execute(
        "SELECT e.id, COALESCE(s.canonical_id, s.id), COALESCE(t.canonical_id, t.id)"
        " FROM edges e JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
        " WHERE e.run = ? AND e.valid_to IS NULL",
        (run,),
    ).fetchall()
    return {(int(r[1]), int(r[2])): int(r[0]) for r in rows}


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
        plan = (
            closure(edges, cap=cap)
            if kind == "transitive"
            else converse(edges, cap=cap)
        )
        if kind == "inverse":
            stated_inverse = {(a, b) for _, a, b, _ in _premise_edges(con, writes)}
            plan = {k: v for k, v in plan.items() if k not in stated_inverse}
        run = f"rule:{writes}" if kind != "inverse" else f"rule:{writes}<-{read}"
        have = _rule_edges(con, run)
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
        gone = [eid for k, eid in have.items() if k not in plan]
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
