"""What a document's own reference list cites in the library: the
entries read by rules (``prax.text.references``), matched by an id or by
title, creators and year with a score, written as ``cites`` edges with
the score as their confidence and evidence. The ``references`` pass of
``prax maintain`` runs it, for the four documents in five that have no
DOI for Crossref to answer. Moved out of ``store.maintain`` on 2026-11-09
(structure review finding 40), where its headings had drifted from the
chunker's: "Cited Literature" was a reference list to one and not the
other.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from typing import Any, Protocol

from prax.text import arxiv, references

from ..base import LIVE, _reading, live, meta_of, now
from ..documents import (
    get_meta,
    reference_chunks,
    set_reference_links,
    update_meta,
)
from .edges import Edge, document_node, find_edges, link, retire_reading

Log = Callable[[str], None]


class _Progress(Protocol):
    """What the matcher reports its progress to: a ``store.Job``, which
    lives in a later part of the store than this one."""

    def update(
        self,
        *,
        done: int | None = None,
        total: int | None = None,
        note: str | None = None,
    ) -> None: ...


REFERENCES_PRODUCER = "references"
_TITLE_TOKEN = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_SKIP_TOKENS = references._NOISE | {
    "for",
    "with",
    "from",
    "using",
    "based",
    "analysis",
    "approach",
    "method",
    "methods",
    "model",
    "models",
    "system",
    "systems",
    "study",
    "new",
    "via",
    "towards",
    "toward",
}
CANDIDATES = 10  # library documents scored per entry


@_reading
def bibliographies(
    con: sqlite3.Connection, *, ids: list[int] | None = None
) -> dict[int, tuple[str, str]]:
    """doc_id -> (text_hash, the text of its reference-list chunks): the
    chunks whose innermost heading is a References/Bibliography heading,
    in order."""
    sql = (
        "SELECT c.doc_id, c.text, d.text_hash,"
        " json_extract(c.heading, '$[#-1]') AS h"
        " FROM chunks c JOIN documents d ON d.id = c.doc_id"
        " WHERE c.kind IN ('text', 'reference') AND c.heading IS NOT NULL"
        f" AND {live('d')}"
    )
    args: list[Any] = []
    if ids:
        sql += f" AND c.doc_id IN ({','.join('?' * len(ids))})"
        args.extend(ids)
    sql += " ORDER BY c.doc_id, c.id"
    parts: dict[int, list[str]] = {}
    hashes: dict[int, str] = {}
    for r in con.execute(sql, args):
        if not references.is_bibliography_heading(r["h"] or ""):
            continue
        parts.setdefault(r["doc_id"], []).append(r["text"])
        hashes[r["doc_id"]] = r["text_hash"] or ""
    return {k: (hashes[k], "\n\n".join(v)) for k, v in parts.items()}


def _library(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Every live document's title, creators, year and ids, once per pass."""
    lib: dict[int, dict[str, Any]] = {}
    for r in con.execute(
        f"SELECT id, title, meta FROM documents WHERE title IS NOT NULL AND {LIVE}"
    ):
        meta = meta_of(r["meta"])
        year = None
        for key in ("year", "date", "published"):
            m = re.search(r"(?:19|20)\d{2}", str(meta.get(key) or ""))
            if m:
                year = int(m.group(0))
                break
        lib[r["id"]] = {
            "title": r["title"],
            "creators": [c.get("name", "") for c in meta.get("creators") or []],
            "year": year,
            "doi": (str(meta.get("doi") or "")).lower().strip() or None,
            "arxiv": arxiv.canonical(meta.get("arxiv")),
        }
    return lib


def _candidates(con: sqlite3.Connection, ref: references.Reference) -> list[int]:
    """Library documents whose field shares the reference's title words:
    the title's content tokens OR-ed over the document field, BM25's
    top ``CANDIDATES``."""
    toks = [t.lower() for t in _TITLE_TOKEN.findall(ref.title)]
    toks = [t for t in toks if t not in _SKIP_TOKENS]
    if len(toks) < 2:
        return []
    expr = " OR ".join(f'"{t}"' for t in toks[:12])
    try:
        rows = con.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?"
            " ORDER BY bm25(documents_fts) LIMIT ?",
            (expr, CANDIDATES),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r[0] for r in rows]


def resolve_references(
    con: sqlite3.Connection,
    doc_id: int,
    entries: list[str],
    lib: dict[int, dict[str, Any]],
    *,
    by_doi: dict[str, int],
    by_arxiv: dict[str, int],
) -> list[tuple[int, str, float, references.Reference]]:
    """The library documents a reference list names: (doc_id, how, score,
    the reference) per link — ``how`` "doi"/"arxiv" for an id match,
    "sure" for a title match over the threshold, "ambiguous" for each of
    several candidates within the margin (twins in the library).
    ``entries`` are the entries' texts (the reference chunks, or a
    bibliography's text split)."""
    out: list[tuple[int, str, float, references.Reference]] = []
    for entry in entries:
        ref = references.parse(entry)
        if not ref.title:
            continue
        if ref.doi and ref.doi in by_doi:
            out.append((by_doi[ref.doi], "doi", 1.0, ref))
            continue
        if ref.arxiv and ref.arxiv in by_arxiv:
            out.append((by_arxiv[ref.arxiv], "arxiv", 1.0, ref))
            continue
        scored = []
        for cid in _candidates(con, ref):
            d = lib.get(cid)
            if d is None or cid == doc_id:
                continue
            s = references.similarity(
                ref, title=d["title"], creators=d["creators"], year=d["year"]
            )
            scored.append((cid, s))
        targets, how = references.match(scored)
        scores = dict(scored)
        out.extend((t, how, scores[t], ref) for t in targets if t != doc_id)
    return out


def link_references(
    con: sqlite3.Connection,
    *,
    run: str,
    ids: list[int] | None = None,
    again: bool = False,
    job: _Progress | None = None,
    log: Log | None = None,
) -> dict[str, Any]:
    """``paper --cites--> paper`` edges from every document's own reference
    list to the library documents it names. A document is read once per
    text (``meta.references.text_hash`` is the stamp; ``again`` reads all),
    and a re-read retires this producer's earlier edges from it first
    (history kept). An id match is ``EXTRACTED``; a sure title match is
    ``INFERRED`` with its score in the evidence; each of several tied
    candidates is ``AMBIGUOUS``. A triple already live (Crossref found
    it) is left as it is."""
    lib = _library(con)
    by_doi = {v["doi"]: k for k, v in lib.items() if v["doi"]}
    by_arxiv = {v["arxiv"]: k for k, v in lib.items() if v["arxiv"]}
    bibs = bibliographies(con, ids=ids)
    stats: Counter[str] = Counter()
    todo = []
    for doc_id, (text_hash, text) in bibs.items():
        stamp = get_meta(con, doc_id).get("references") or {}
        # the same text, read by a pass that kept its links: nothing to do
        # (a stamp without links is the first pass's, before the chunks
        # carried what they cite: read again once)
        if not again and stamp.get("text_hash") == text_hash and "links" in stamp:
            stats["unchanged"] += 1
            continue
        todo.append((doc_id, text_hash, text))
    if job is not None:
        job.update(total=len(todo), done=0, note="references")
    at = now()
    for n, (doc_id, text_hash, text) in enumerate(todo, 1):
        title = lib.get(doc_id, {}).get("title")
        if not title:
            continue
        retired = retire_reading(
            con, doc_id, producer=REFERENCES_PRODUCER, except_version="", reviews=False
        )
        # one node per document, whatever links it (``document_node``)
        try:
            me = document_node(con, doc_id)
        except KeyError:
            me = (title, "paper")
        stats["retired"] += retired
        # the entries: the reference chunks where the chunker cut them,
        # the bibliography's text split where it has not (before a rechunk)
        chunks = reference_chunks(con, doc_id)
        entries = [c["text"] for c in chunks] or references.entries(text)
        found = resolve_references(
            con, doc_id, entries, lib, by_doi=by_doi, by_arxiv=by_arxiv
        )
        linked = ambiguous = existing = 0
        links: list[dict[str, Any]] = []
        for target, how, score, ref in found:
            name = lib[target]["title"]
            if not name or name == title:
                continue
            links.append(
                {
                    "number": ref.number,
                    "entry": ref.title,
                    "doc_id": target,
                    "title": name,
                    "score": round(score, 3),
                    "how": how,
                }
            )
            try:
                it = document_node(con, target)
            except KeyError:
                it = (name, "paper")
            edge = Edge(*me, "cites", *it)
            if find_edges(con, edge):
                existing += 1
                continue
            confidence = (
                "EXTRACTED"
                if how in ("doi", "arxiv")
                else "AMBIGUOUS"
                if how == "ambiguous"
                else "INFERRED"
            )
            number = f"[{ref.number}] " if ref.number else ""
            evidence = (
                f"references: {number}{ref.title[:120]!r}"
                + (f" ({ref.year})" if ref.year else "")
                + (f" by {how}" if how in ("doi", "arxiv") else f", score {score:.2f}")
            )
            link(
                con,
                edge,
                confidence=confidence,
                source_doc=doc_id,
                evidence=evidence,
                producer=REFERENCES_PRODUCER,
                run=run,
            )
            if how == "ambiguous":
                ambiguous += 1
            else:
                linked += 1
        stamp = {
            "text_hash": text_hash,
            "entries": len(entries),
            "linked": linked,
            "ambiguous": ambiguous,
            "at": at,
            "run": run,
        }
        update_meta(con, doc_id, keys={"references": stamp})
        # what each entry cites, on the document and on its chunks (a
        # rechunk re-applies it): the link's title is the cited document's
        set_reference_links(con, doc_id, links)
        stats["documents"] += 1
        stats["linked"] += linked
        stats["ambiguous"] += ambiguous
        stats["existing"] += existing
        if job is not None and (n % 20 == 0 or n == len(todo)):
            job.update(done=n, note=f"references: {stats['linked']} linked")
        if log and n % 200 == 0:
            log(f"references: {n}/{len(todo)} documents, {stats['linked']} linked")
    return dict(stats)
