"""What a document is read against: its domains and the rules that assign
them, a request to extract it again, the promotion to the expensive
model, and the stamps of who extracted it."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from prax import ontology

from ..base import (
    _reading,
    _serialized,
    now,
)
from .meta import get_meta, set_meta

# Which ontology modules a document is read against (``meta.domains``): the
# research papers see the research module, the family photos the family
# module, a document that is both sees both. No domain set means every
# module, which is what the library had before modules existed. Set by a
# rule at assignment time (``assign_domains``, rules in prax.yaml), by hand
# (``set_domains``), or by an importer that knows its source.


def document_domains(con: sqlite3.Connection, doc_id: int) -> list[str] | None:
    """The document's domains, None when it belongs to every module."""
    row = con.execute(
        "SELECT json_extract(meta, '$.domains') FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    return list(json.loads(row[0])) if row[0] else None


def _check_domains(domains: list[str]) -> list[str]:
    modules = ontology.current().modules
    out = []
    for d in domains:
        if d == ontology.CORE or d not in modules:
            raise ValueError(
                f"unknown domain {d!r}; the modules are"
                f" {sorted(m for m in modules if m != ontology.CORE)}"
            )
        if d not in out:
            out.append(d)
    return out


@_serialized
def set_domains(
    con: sqlite3.Connection,
    doc_id: int,
    domains: list[str] | None,
    *,
    by: str = "human",
) -> list[str] | None:
    """Replace a document's domain set (None: every module). Names must be
    modules of the current ontology other than core. A change of the set
    by a person or an agent (``by``) under an extraction makes that
    reading stale (``_lens_changed``): the extract step takes the
    document again, first, and the new reading retires the old. A rule's
    or an importer's assignment leaves the stamp: the backlog pass
    re-selects a document whose subset's version moved in its own time."""
    meta = get_meta(con, doc_id)
    before = meta.get("domains")
    if domains is None:
        meta.pop("domains", None)
        meta.pop("domains_by", None)
    else:
        meta["domains"] = _check_domains(domains)
        meta["domains_by"] = by
    if meta.get("domains") != before and by in ("human", "agent"):
        _lens_changed(meta, by=by)
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return meta.get("domains")


def _lens_changed(meta: dict[str, Any], *, by: str) -> bool:
    """The document's domain set changed under an extraction made against
    the old set: the stamp goes to the history, ``meta.extraction_stale``
    says why, so the extract step selects the document before the
    backlog and ``extraction.apply`` retires the old reading (history
    kept). The change is a person's or an agent's, so it counts as a
    reading asked for (``requested``), which a worker scoped to the
    captures takes too: an upload re-tagged by hand otherwise waited for
    a pass over everything (six electronics documents, 2026-09-27).
    Nothing when the reading already carries the new subset's version
    (the set changed to one the same modules make up). True when a stamp
    moved."""
    gone = meta.get("extraction")
    if not gone:
        meta.pop("extraction_error", None)
        return False
    want = ontology.current().for_domains(meta.get("domains")).version
    if gone.get("ontology_version") == want:
        return False
    meta.pop("extraction")
    meta.pop("extraction_error", None)
    meta.setdefault("extraction_history", []).append(
        {**gone, "superseded_by": "domains"}
    )
    meta["extraction_stale"] = {
        "extractor": gone.get("extractor"),
        "run": gone.get("run"),
        "ontology_version": gone.get("ontology_version"),
        "domains_changed": True,
        "requested": {"by": by, "at": now()},
    }
    return True


@_serialized
def request_extraction(
    con: sqlite3.Connection, doc_id: int, *, by: str = "human"
) -> dict[str, Any]:
    """Ask for the document's graph to be read again by the extract
    step's model, before the backlog: the current stamp goes to
    ``meta.extraction_history`` and ``meta.extraction_stale`` says a
    person asked (``requested``), which is what the extract hand-out
    puts first whatever the scope. A document never extracted is marked
    the same way. The new reading retires the old one's edges
    (``extraction.apply``), history kept. Returns the stale record."""
    row = con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    meta = json.loads(row["meta"]) if row["meta"] else {}
    gone = meta.pop("extraction", None)
    meta.pop("extraction_error", None)
    if gone:
        meta.setdefault("extraction_history", []).append(
            {**gone, "superseded_by": "request"}
        )
    meta["extraction_stale"] = {
        **(
            {
                "extractor": gone.get("extractor"),
                "run": gone.get("run"),
                "ontology_version": gone.get("ontology_version"),
            }
            if gone
            else {}
        ),
        "requested": {
            "by": by,
            "at": now(),
        },
    }
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return meta["extraction_stale"]


def add_domain(
    con: sqlite3.Connection, doc_id: int, domain: str, *, by: str = "human"
) -> list[str]:
    """Add a domain to a document that keeps its others (a family photo
    that also matters to the research)."""
    current = document_domains(con, doc_id) or []
    if domain in current:
        return current
    return set_domains(con, doc_id, [*current, domain], by=by) or []


def remove_domain(
    con: sqlite3.Connection, doc_id: int, domain: str
) -> list[str] | None:
    """Take a domain away; the last one leaves the document in every module."""
    current = document_domains(con, doc_id)
    if not current or domain not in current:
        return current
    rest = [d for d in current if d != domain]
    return set_domains(con, doc_id, rest or None)


def documents_in_domain(con: sqlite3.Connection, domain: str) -> list[int]:
    """Documents whose domain set names ``domain`` (documents without a set
    are in every module but are not listed here: a re-run per domain means
    the documents that were assigned to it)."""
    return [
        r[0]
        for r in con.execute(
            "SELECT d.id FROM documents d, json_each(d.meta, '$.domains') j"
            " WHERE j.value = ? ORDER BY d.id",
            (domain,),
        )
    ]


def _rule_matches(rule: dict[str, Any], doc: dict[str, Any]) -> bool:
    meta = doc["meta"]
    m = rule.get("match") or {}
    if not m:
        return True
    if "source" in m and meta.get("source") != m["source"]:
        return False
    if "mime" in m and not (doc["mime"] or "").startswith(m["mime"]):
        return False
    if "path" in m and not (doc["original_path"] or "").lower().startswith(
        str(m["path"]).lower()
    ):
        return False
    if "collection" in m:
        names = [c.lower() for c in meta.get("collections") or []]
        if str(m["collection"]).lower() not in names:
            return False
    if "tag" in m:
        tags = [t.lower() for t in meta.get("tags") or []]
        if str(m["tag"]).lower() not in tags:
            return False
    return True


@_serialized
def assign_domains(
    con: sqlite3.Connection,
    rules: list[dict[str, Any]],
    *,
    force: bool = False,
    commit: bool = True,
    ids: list[int] | None = None,
) -> dict[str, int]:
    """Give every document without a domain set (all of them with ``force``;
    only ``ids`` when given) the domains of the first rule it matches. A
    rule is ``{match: {source, mime, path, collection, tag}, domains:
    [...]}``; a rule without ``match`` is the default. Documents whose set
    a person wrote by hand (``domains_by: human``) are never touched.
    Returns counts per rule index and ``unmatched``."""
    counts: dict[str, int] = {"unmatched": 0}
    for rule in rules:
        _check_domains(list(rule.get("domains") or []))
    sql = (
        "SELECT id, mime, original_path, meta FROM documents"
        " WHERE coalesce(json_extract(meta, '$.domains_by'), '') != 'human'"
    )
    args: tuple[Any, ...] = ()
    if not force:  # only the documents without a set (the index knows them)
        sql += " AND json_extract(meta, '$.domains') IS NULL"
    if ids is not None:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        args = tuple(ids)
    for r in con.execute(sql, args).fetchall():
        meta = json.loads(r["meta"] or "{}")
        doc = {"mime": r["mime"], "original_path": r["original_path"], "meta": meta}
        for i, rule in enumerate(rules):
            if _rule_matches(rule, doc):
                key = f"rule {i}"
                counts[key] = counts.get(key, 0) + 1
                if commit:
                    meta["domains"] = list(rule["domains"])
                    meta["domains_by"] = "rule"
                    con.execute(
                        "UPDATE documents SET meta = ? WHERE id = ?",
                        (json.dumps(meta), r["id"]),
                    )
                break
        else:
            counts["unmatched"] += 1
    if commit:
        con.commit()
    return counts


# A document worth the expensive model: flagged by a person, by Claude Code
# over MCP, or by the store itself when the document joins a project or
# becomes a synthesis source. The flag lives in ``meta.promote``; the pass
# is the ``promote`` work step (``prax work --steps promote --spend``) with
# the ``promote`` step's model, and a document counts as done when that
# producer's stamp is in its history.

PROMOTE_WEIGHTS = {"project": 5, "synthesis": 4, "page": 3, "cited": 1}


def _set_promote(
    con: sqlite3.Connection, doc_id: int, *, by: str, reason: str | None
) -> dict[str, Any] | None:
    meta = get_meta(con, doc_id)
    if meta.get("promote"):
        return None
    meta["promote"] = {
        "by": by,
        "reason": reason,
        "at": now(),
    }
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    return meta["promote"]


@_serialized
def promote(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    by: str = "human",
    reason: str | None = None,
) -> dict[str, Any]:
    """Flag a document for the expensive pass. Returns the flag; a document
    already flagged keeps its first flag."""
    if (
        con.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone()
        is None
    ):
        raise KeyError(f"no such document: {doc_id}")
    flag = _set_promote(con, doc_id, by=by, reason=reason)
    con.commit()
    return flag or get_meta(con, doc_id)["promote"]


@_serialized
def unpromote(con: sqlite3.Connection, doc_id: int) -> bool:
    meta = get_meta(con, doc_id)
    if not meta.pop("promote", None):
        return False
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return True


def expected_version(
    meta: dict[str, Any], onto: ontology.Ontology | None = None
) -> str:
    """The ontology version a reading of this document should be stamped
    with: the version of its own domains' subset, or of the whole ontology
    when it has no domain set."""
    onto = onto or ontology.current()
    return onto.for_domains(meta.get("domains") or None).version


def extracted_by(
    meta: dict[str, Any], producer: str, *, ontology_version: str | None = None
) -> bool:
    """Whether ``producer`` has read the document, now or in its history;
    with ``ontology_version``, only a reading under that version counts (a
    pass under an older ontology is not the pass being asked for)."""
    stamps = [meta.get("extraction") or {}, *(meta.get("extraction_history") or [])]
    return any(
        s.get("extractor") == producer
        and (ontology_version is None or s.get("ontology_version") == ontology_version)
        for s in stamps
    )


@_reading
def promoted_documents(
    con: sqlite3.Connection, *, producer: str | None = None
) -> list[dict[str, Any]]:
    """Flagged documents, oldest flag first; ``done`` says whether
    ``producer`` has read each one under the current ontology."""
    onto = ontology.current()
    out = []
    for r in con.execute(
        "SELECT id, title, meta FROM documents"
        " WHERE json_extract(meta, '$.promote') IS NOT NULL"
        " ORDER BY json_extract(meta, '$.promote.at'), id"
    ):
        meta = json.loads(r["meta"] or "{}")
        out.append(
            {
                "doc_id": r["id"],
                "title": r["title"],
                "promote": meta["promote"],
                "domains": meta.get("domains"),
                "done": bool(producer)
                and extracted_by(
                    meta, producer, ontology_version=expected_version(meta, onto)
                ),
            }
        )
    return out


@_reading
def promotion_candidates(
    con: sqlite3.Connection, *, limit: int = 30
) -> list[dict[str, Any]]:
    """Documents the library keeps coming back to, not yet flagged: scored
    by project membership, synthesis sources, notes on them, and citations
    from other library documents (weights ``PROMOTE_WEIGHTS``)."""
    titles: dict[str, int] = {}
    promoted: set[int] = set()
    for r in con.execute(
        "SELECT id, title, meta FROM documents WHERE title IS NOT NULL"
        " AND text_hash IS NOT NULL AND json_extract(meta, '$.retired') IS NULL"
        " AND coalesce(json_extract(meta, '$.source'), '') != 'wiki'"
    ):
        titles.setdefault(r["title"], r["id"])
        if json.loads(r["meta"] or "{}").get("promote"):
            promoted.add(r["id"])
    counts: dict[int, dict[str, int]] = {}

    def bump(name: str, key: str, n: int = 1) -> None:
        doc_id = titles.get(name)
        if doc_id is None or doc_id in promoted:
            return
        bucket = counts.setdefault(doc_id, {})
        bucket[key] = bucket.get(key, 0) + n

    for r in con.execute(
        "SELECT t.name AS name, count(DISTINCT x.source_doc) AS n FROM edges x"
        " JOIN entities t ON t.id = x.dst"
        " WHERE x.rel = 'cites' AND x.valid_to IS NULL AND t.type = 'paper'"
        " AND x.source_doc IS NOT NULL GROUP BY t.name"
    ):
        bump(r["name"], "cited", r["n"])
    for r in con.execute(
        "SELECT x.rel AS rel, t.name AS name, count(*) AS n FROM edges x"
        " JOIN entities t ON t.id = x.dst"
        " WHERE x.rel IN ('annotates', 'synthesizes') AND x.valid_to IS NULL"
        " GROUP BY x.rel, t.name"
    ):
        bump(r["name"], "synthesis" if r["rel"] == "synthesizes" else "page", r["n"])
    for r in con.execute(
        "SELECT s.name AS name, count(*) AS n FROM edges x"
        " JOIN entities s ON s.id = x.src"
        " WHERE x.rel = 'part_of' AND x.valid_to IS NULL AND x.producer = 'page'"
        " GROUP BY s.name"
    ):
        bump(r["name"], "project", r["n"])
    ranked = []
    for doc_id, c in counts.items():
        score = sum(PROMOTE_WEIGHTS[k] * v for k, v in c.items())
        ranked.append({"doc_id": doc_id, "score": score, **c})
    ranked.sort(key=lambda d: (-d["score"], d["doc_id"]))
    ranked = ranked[:limit]
    for d in ranked:
        d["title"] = con.execute(
            "SELECT title FROM documents WHERE id = ?", (d["doc_id"],)
        ).fetchone()[0]
    return ranked


@_serialized
def restamp_ontology(
    con: sqlite3.Connection, src: str, dst: str, *, commit: bool = True
) -> int:
    """Rewrite ``meta.extraction.ontology_version`` (and the history entries)
    from ``src`` to ``dst`` on every document: the ontology's version string
    changed shape without a change in what it accepts (the split into
    modules). Edges keep their version. Returns the documents touched."""
    n = 0
    for r in con.execute(
        "SELECT id, meta FROM documents WHERE meta LIKE ?",
        (f'%"ontology_version": "{src}"%',),
    ).fetchall():
        meta = json.loads(r["meta"] or "{}")
        changed = False
        for stamp in [
            meta.get("extraction") or {},
            *(meta.get("extraction_history") or []),
        ]:
            if stamp.get("ontology_version") == src:
                stamp["ontology_version"] = dst
                changed = True
        if changed:
            n += 1
            if commit:
                con.execute(
                    "UPDATE documents SET meta = ? WHERE id = ?",
                    (json.dumps(meta), r["id"]),
                )
    if commit:
        con.commit()
    return n


@_serialized
def unstamp_extraction(con: sqlite3.Connection, doc_id: int, stamp: str) -> bool:
    """The text an extraction was made from has been replaced (by the
    reader ``stamp``): the extraction stamp goes to the history, so the
    extract step selects the document again, and ``meta.extraction_stale``
    names the reading the next extraction supersedes (``extraction.apply``
    retires its edges, history kept). A failed extraction on the old text
    is forgotten too. True when there was a stamp to move."""
    meta = get_meta(con, doc_id)
    gone = meta.pop("extraction", None)
    failed = meta.pop("extraction_error", None)
    if not gone and not failed:
        return False
    if gone:
        meta.setdefault("extraction_history", []).append(
            {**gone, "superseded_by": stamp}
        )
        meta["extraction_stale"] = {
            "extractor": gone.get("extractor"),
            "run": gone.get("run"),
            "ontology_version": gone.get("ontology_version"),
            "text_read_by": stamp,
        }
    set_meta(con, doc_id, meta)
    return True
