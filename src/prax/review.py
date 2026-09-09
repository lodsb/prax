"""The review queue's second life: replaying items against a newer ontology.

Extraction parks a triple whose types or relation the ontology of the day
rejected (invariant 9), with its evidence and source document. When the
ontology grows, ``replay`` re-checks every open item that has both types
and links the ones that now fit through ``store.link``, keeping evidence
and source; the item is closed as ``linked``. Nothing is re-extracted and
no model is called. Items still rejected stay open, and ``unmapped`` items
(no types) are never replayed: those need a person or a typing pass.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

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
                )
                rep.linked += 1
            store.resolve_review(con, it["id"], "linked")
            offset -= 1  # the item left the open set the listing pages over
    return rep
