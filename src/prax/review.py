"""The review queue's second life: replaying items against a newer ontology.

Extraction parks a triple whose types or relation the ontology of the day
rejected (invariant 9), with its evidence and source document. When the
ontology grows, ``replay`` re-checks every open item that has both types
and links the ones that now fit through ``store.link``, keeping evidence
and source; the item is closed as ``linked``. Nothing is re-extracted and
no model is called. Items still rejected stay open, and ``unmapped`` items
(no types) are never replayed: those need a person or a typing pass.

``apply_typing_rules`` is that typing pass for the systematic misfits a
model produces (the document typed as what it is about, ``authored_by``
reversed, ``cites`` for a tool it uses): rules retype, flip, rename or
drop, never invent, and write what they link as INFERRED edges with the
producer ``typing-rules``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prax import ontology, store


@dataclass
class ReplayReport:
    checked: int = 0
    linked: int = 0
    existing: int = 0  # the edge was already in the graph; item closed
    still_open: int = 0
    ontology_version: str = ""


def replay(
    con: sqlite3.Connection, *, onto: ontology.Ontology | None = None
) -> ReplayReport:
    onto = onto or ontology.current()
    rep = ReplayReport(ontology_version=onto.version)
    offset = 0
    while True:
        items = store.list_review(con, limit=500, offset=offset, unmapped=False)
        if not items:
            break
        offset += len(items)
        for it in items:
            if not (it["src_type"] and it["dst_type"] and it["rel"]):
                continue
            rep.checked += 1
            try:
                onto.check_edge(it["src_type"], it["rel"], it["dst_type"])
            except ValueError:
                rep.still_open += 1
                continue
            edge = store.Edge(
                it["src"], it["src_type"], it["rel"], it["dst"], it["dst_type"]
            )
            if store.find_edges(con, edge):
                rep.existing += 1
            else:
                store.link(
                    con,
                    edge,
                    confidence="EXTRACTED",
                    source_doc=it["source_doc"],
                    evidence=it["evidence"],
                    producer="replay",
                    run=f"ontology-v{onto.version}",
                )
                rep.linked += 1
            store.resolve_review(con, it["id"], "linked")
            offset -= 1  # the item left the open set the listing pages over
    return rep


# ------------------------------------------------------------ typing rules
# A local model's misfits are systematic (docs/eval/extractors-local-2026-09-11.md
# and the queue after the backlog): it writes the document under the type
# of what the document is about ("this manual" as a tool), reverses
# authored_by, says "cites" for a tool or method it references, and "about"
# for a claim it makes or a paper it discusses. Each of those is a rule:
# what the model meant is recoverable from the item itself and the source
# document's title. Rules never invent content: they retype, flip or
# rename a relation, or drop what no relation can hold; everything else
# stays in the queue for a person or a bigger ontology.

RULES_PRODUCER = "typing-rules"
_MALFORMED = re.compile(r"(src_type=|dst_type=|confidence=|evidence=|\trel=)")
# (rel, src_type, dst_type) -> new relation, after the self-name retyping
REMAP: dict[tuple[str, str, str], str] = {
    ("cites", "paper", "tool"): "uses",
    ("cites", "paper", "method"): "uses",
    ("cites", "paper", "dataset"): "uses",
    ("cites", "paper", "concept"): "about",
    ("about", "paper", "claim"): "proposes",
    ("about", "paper", "paper"): "cites",
    ("about", "tool", "concept"): "implements",
    ("about", "tool", "method"): "implements",
    ("about", "method", "concept"): "implements",
    ("about", "method", "method"): "implements",
    ("implements", "paper", "method"): "uses",
    ("implements", "paper", "concept"): "about",
}
# (rel, src_type, dst_type) with "*" as a wildcard -> dropped
DROP: set[tuple[str, str, str]] = {
    ("cites", "paper", "author"),
    ("cites", "paper", "venue"),
    ("cites", "paper", "claim"),
    ("cites", "paper", "project"),
    ("cites", "paper", "page"),
    ("about", "paper", "author"),
    ("about", "author", "*"),
    ("about", "concept", "*"),
    ("about", "venue", "*"),
    ("uses", "author", "*"),
    ("authored_by", "paper", "paper"),
}


@dataclass
class TypingReport:
    checked: int = 0
    linked: int = 0
    dropped: int = 0
    existing: int = 0
    still_open: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)
    run: str = ""

    def hit(self, rule: str) -> None:
        self.by_rule[rule] = self.by_rule.get(rule, 0) + 1


def _doc_titles(con: sqlite3.Connection, ids: set[int]) -> dict[int, tuple[str, str]]:
    """``{doc_id: (title, entity type)}``: a page's own type is page or project."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out: dict[int, tuple[str, str]] = {}
    for r in con.execute(
        f"SELECT d.id, d.title, p.kind FROM documents d LEFT JOIN pages p"
        f" ON p.doc_id = d.id WHERE d.id IN ({marks})",
        tuple(ids),
    ):
        kind = (
            "project" if r["kind"] == "project" else ("page" if r["kind"] else "paper")
        )
        out[r["id"]] = (r["title"] or "", kind)
    return out


def decide(
    item: dict[str, Any], doc: tuple[str, str] | None
) -> tuple[str, list[store.Edge], str | None]:
    """What the rules say about one typed item: ``("link", edges, rule)``,
    ``("drop", [], rule)`` or ``("open", [], None)``. Pure; the ontology
    check happens in ``apply_typing_rules``."""
    src, dst = item["src"], item["dst"]
    rel, st, dt = item["rel"], item["src_type"], item["dst_type"]
    if _MALFORMED.search(src) or _MALFORMED.search(dst):
        return "drop", [], "malformed-name"
    title, own = doc or ("", "paper")
    rule = None
    # the document under a wrong type: it is itself
    if title and src == title and st != own:
        st, rule = own, "self-name"
    # authored_by written backwards, or with authors on both ends
    if rel == "authored_by" and st == "author":
        if dt == own and title and dst == title:
            return (
                "link",
                [store.Edge(title, own, "authored_by", src, "author")],
                "flip-authored_by",
            )
        if dt == "author" and title:
            names = [src] if src == dst else [src, dst]
            return (
                "link",
                [store.Edge(title, own, "authored_by", n, "author") for n in names],
                "authors-both-ends",
            )
    if (rel, st, dt) in REMAP:
        return (
            "link",
            [store.Edge(src, st, REMAP[(rel, st, dt)], dst, dt)],
            f"{rel}->{REMAP[(rel, st, dt)]}",
        )
    if (rel, st, dt) in DROP or (rel, st, "*") in DROP:
        return "drop", [], f"drop-{rel}-{st}-{dt}"
    if rule == "self-name":
        return "link", [store.Edge(src, st, rel, dst, dt)], rule
    return "open", [], None


def apply_typing_rules(
    con: sqlite3.Connection,
    *,
    commit: bool = True,
    onto: ontology.Ontology | None = None,
) -> TypingReport:
    """Run the rules over every open typed item. Links carry the item's
    evidence and source document, confidence INFERRED (a rule read what
    the model meant), producer ``typing-rules`` and one run id per pass;
    an edge already in the graph closes the item as ``linked`` too. With
    ``commit=False`` nothing is written and the report says what would be."""
    onto = onto or ontology.current()
    rep = TypingReport(run="typing-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S"))
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list_review(con, limit=1000, offset=offset, unmapped=False)
        if not page:
            break
        offset += len(page)
        items.extend(it for it in page if it["src_type"] and it["dst_type"])
    docs = _doc_titles(con, {it["source_doc"] for it in items if it["source_doc"]})
    for it in items:
        rep.checked += 1
        action, edges, rule = decide(it, docs.get(it["source_doc"]))
        if action == "open":
            rep.still_open += 1
            continue
        if action == "drop":
            rep.dropped += 1
            rep.hit(rule or "drop")
            if commit:
                store.resolve_review(con, it["id"], "dropped")
            continue
        try:
            for e in edges:
                onto.check_edge(e.src_type, e.rel, e.dst_type)
        except ValueError:
            rep.still_open += 1
            continue
        rep.hit(rule or "link")
        wrote = False
        for e in edges:
            if store.find_edges(con, e):
                continue
            wrote = True
            if commit:
                store.link(
                    con,
                    e,
                    confidence="INFERRED",
                    source_doc=it["source_doc"],
                    evidence=it["evidence"],
                    producer=RULES_PRODUCER,
                    run=rep.run,
                )
        if wrote:
            rep.linked += 1
        else:
            rep.existing += 1
        if commit:
            store.resolve_review(con, it["id"], "linked")
    return rep
