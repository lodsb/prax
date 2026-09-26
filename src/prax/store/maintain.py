"""The maintenance pass: what the store does to itself, without a model
and without a decision.

A library changes under its own passes: texts arrive, titles get fixed,
captures repeat. A few tables are derived from the rest and drift unless
they are rebuilt — the acronyms the library defines (what the keyword
search expands a query token to), the document retrieval field (title,
kind, summary as one searchable row), the domain set a document is read
against (the rules in ``prax.yaml``, for documents nobody assigned by
hand), the duplicate captures of one page, and the review queue's two
rule passes (a replay against the current ontology, then the typing
rules — what the door does for one document right after its extraction,
here for the whole queue), and the citations a document's own reference
list makes to documents in the library (``prax.references``: the entries
read by rules, matched by title, creators and year with a score —
``cites`` edges with the score as their confidence and evidence, for
the four documents in five that have no DOI for Crossref to answer).
None of that needs a model, none of it needs anyone to look first: it
is what a nightly pass runs after the worker's, and what ``prax
maintain`` runs on request. ``rechunk`` — every chunk rebuilt from its
text artifact after a change to the chunker — is a pass too, but only
when named: the nightly has no reason to.

What stays out on purpose: the repairs (``store.repair``: a person picks
the ailment), the readings and extractions (the worker, with a model),
entity resolution (the likely merges are a decision). Each pass is a
name in ``PASSES``; ``maintain(only=[...])`` runs a subset. The whole
thing is one job, so the Jobs view shows which pass it is on.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from prax import acronyms, config, references

from .base import _reading, now
from .documents import (
    assign_domains,
    dedupe_captures,
    fill_text_lengths,
    get_meta,
    rechunk,
    reference_chunks,
    refresh_document_fields,
    set_meta,
    set_reference_links,
)
from .graph import (
    Edge,
    corpus_rulings,
    find_edges,
    link,
    retire_reading,
    unmark_corpus_ruling,
)
from .jobs import Job
from .retrieval import fts_merge, replace_acronyms

Log = Callable[[str], None]

PASSES = (
    "acronyms",
    "fields",
    "domains",
    "dedupe",
    "review",
    "references",
    "proposes",
    "fts",
    "lengths",
    "languages",
    "names",
    "attachment",
)
# a pass only when named: the nightly has no reason to
ON_REQUEST = ("rechunk", "rejudge")


def _acronyms(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """Every text artifact read once for its "phrase (ACRONYM)" definitions
    (``prax.acronyms``); the table replaced. Minutes over a large library."""
    counts: Counter[tuple[str, str]] = Counter()
    rows = con.execute(
        "SELECT id, text_hash FROM documents WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ).fetchall()
    scanned = 0
    for n, r in enumerate(rows, 1):
        path = config.archive_dir() / r["text_hash"][:2] / r["text_hash"]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts.update(acronyms.find(text))
        scanned += 1
        if n % 500 == 0:
            job.update(
                done=n, total=len(rows), note=f"acronyms: {n} of {len(rows)} texts"
            )
    pairs = [(acr, exp, docs) for (acr, exp), docs in counts.items()]
    replace_acronyms(con, pairs)
    return {
        "documents": scanned,
        "pairings": len(pairs),
        "acronyms": len({p[0] for p in pairs}),
        "in_two_or_more": sum(1 for p in pairs if p[2] >= 2),
    }


def _fields(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The document retrieval field rebuilt for every document; how many
    changed (a title fixed, a summary written since)."""
    job.update(note="document fields")
    return {"changed": refresh_document_fields(con)}


def _domains(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The ``domains:`` rules of ``prax.yaml`` over the documents without a
    set; nothing when the file has no rules."""
    from prax import models

    rules = list(models.load().get("domains") or [])
    if not rules:
        return {"rules": 0}
    job.update(note="domains from the rules")
    counts = assign_domains(con, rules, commit=True)
    assigned = sum(v for k, v in counts.items() if k.startswith("rule "))
    return {"rules": len(rules), "assigned": assigned, "unmatched": counts["unmatched"]}


def _dedupe(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """Duplicate captures of one page retired (``dedupe_captures``): row
    and file kept, ``meta.retired`` naming the keeper."""
    job.update(note="duplicate captures")
    report = dedupe_captures(con, commit=True)
    return {
        "groups": len(report.get("groups") or []),
        "retired": int(report.get("retired") or 0),
        "kept_apart": int(report.get("kept_apart") or 0),
    }


def _review(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The review queue against the current ontology (``review.replay``:
    a typed item the ontology accepts now becomes an edge), then the
    typing rules over every open item (``review.apply_typing_rules``)."""
    from prax import review

    job.update(note="review: replay against the ontology")
    replayed = review.replay(con)
    job.update(note="review: the typing rules")
    typed = review.apply_typing_rules(con, commit=True)
    return {
        "ontology": replayed.ontology_version,
        "replay": {
            "checked": replayed.checked,
            "linked": replayed.linked,
            "existing": replayed.existing,
        },
        "rules": {
            "checked": typed.checked,
            "linked": typed.linked,
            "dropped": typed.dropped,
            "existing": typed.existing,
            "still_open": typed.still_open,
            "run": typed.run,
        },
    }


# ---------------------------------------------------------------- references

REFERENCES_PRODUCER = "references"
REFERENCE_HEADINGS = ("references", "bibliography", "literatur", "works cited")
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
        " AND json_extract(d.meta, '$.retired') IS NULL"
    )
    args: list[Any] = []
    if ids:
        sql += f" AND c.doc_id IN ({','.join('?' * len(ids))})"
        args.extend(ids)
    sql += " ORDER BY c.doc_id, c.id"
    parts: dict[int, list[str]] = {}
    hashes: dict[int, str] = {}
    for r in con.execute(sql, args):
        h = (r["h"] or "").strip().lower()
        if not any(h.startswith(x) or h.endswith(x) for x in REFERENCE_HEADINGS):
            continue
        parts.setdefault(r["doc_id"], []).append(r["text"])
        hashes[r["doc_id"]] = r["text_hash"] or ""
    return {k: (hashes[k], "\n\n".join(v)) for k, v in parts.items()}


def _library(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Every live document's title, creators, year and ids, once per pass."""
    lib: dict[int, dict[str, Any]] = {}
    for r in con.execute(
        "SELECT id, title, meta FROM documents WHERE title IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    ):
        meta = json.loads(r["meta"] or "{}")
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
            "arxiv": (str(meta.get("arxiv") or "")).lower().strip() or None,
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
    job: Job | None = None,
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
    stats = Counter()
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
            con, doc_id, producer=REFERENCES_PRODUCER, except_version=""
        )
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
            edge = Edge(title, "paper", "cites", name, "paper")
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
        meta = get_meta(con, doc_id)
        meta["references"] = {
            "text_hash": text_hash,
            "entries": len(entries),
            "linked": linked,
            "ambiguous": ambiguous,
            "at": at,
            "run": run,
        }
        set_meta(con, doc_id, meta)
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


def _fts(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The keyword index's segments merged a little (``fts_merge``): a
    term read from one place instead of two dozen."""
    job.update(note="fts: merging segments")
    return fts_merge(con)


def _references(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The citations a document's reference list makes to the library
    (``link_references``): the documents whose text changed since their
    last reading, or never were read."""
    run = "references-" + time.strftime("%Y%m%dT%H%M%S")
    return {"run": run, **link_references(con, run=run, job=job)}


def _has_authors(con: sqlite3.Connection, doc_id: int, name: str) -> bool:
    """Whether the document behaves like a published work: does the graph
    know who wrote it?

    A title of one or two words is usually a lecture handout or a chapter
    *about* the thing, not a paper proposing it — "Expertise", "Computer
    Graphics", "Sparse grids" were all in the first run. Counting words
    is a proxy for nothing; authorship is the evidence that this is a
    work with contributors, and it cut 326 edges to the 192 with a reason
    behind them.
    """
    return bool(
        con.execute(
            "SELECT 1 FROM edges x JOIN entities s ON s.id = x.src"
            " WHERE x.rel = 'authored_by' AND x.valid_to IS NULL"
            " AND x.source_doc = ? AND s.name = ? LIMIT 1",
            (doc_id, name),
        ).fetchone()
    )


def _proposes(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The `proposes` edge between a paper and the thing named after it.

    5,746 names in the library are carried by more than one type, and
    they are three different problems wearing one shape: a mistype, a
    subtype the fold already handles, and *polysemy* — "Fractional
    wavelet transform" is a paper and the method that paper introduced.
    The third is not a duplicate and must never be merged
    (`docs/normalization.md`); what the graph is missing is the relation
    between the two.

    Only where the library itself is the evidence: the document-side name
    has to be the title of a document the store holds, so the document
    entity is a fact rather than the extractor's guess. A title nobody
    holds is left alone — 3,006 clashes have a document side and only 523
    have a document behind it.

    The relation is the ontology's own, so its domain and range decide
    what may take it: a method, a tool, a concept or a claim. An
    organization or an author of the same name is a different question
    and stays open.

    A known limit, 11 of 192 on the live library: where the title names a
    document kind — "KSP Reference Manual", "More Feedback Machine User
    Guide" — the *thing* side is the mistake, because nothing is a tool
    called that. The edge is written anyway rather than guarded by
    another word list: it is INFERRED and signed with a run, the typing
    of that entity is the real fault, and a rule that is wrong more often
    than not is worse than no rule
    (`docs/eval/typing-rules-2026-09-24.md`).
    """
    from prax import ontology
    from prax.resolution import SELF_KINDS

    onto = ontology.current()
    proposes = onto.relations.get("proposes")
    if proposes is None:
        return {"skipped": "this ontology has no proposes relation"}
    titles: dict[str, int] = {}
    for doc_id, title in con.execute(
        "SELECT id, title FROM documents WHERE title IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    ):
        titles.setdefault(title.lower(), doc_id)
    by_name: dict[str, list[tuple[str, str]]] = {}
    for name, etype in con.execute(
        "SELECT name, type FROM entities WHERE canonical_id IS NULL"
    ):
        by_name.setdefault(name.lower(), []).append((etype, name))

    run = "polysemy-" + time.strftime("%Y%m%dT%H%M%S")
    made = 0
    existing = 0
    pairs: Counter[str] = Counter()
    todo = [(low, m) for low, m in by_name.items() if low in titles and len(m) > 1]
    for n, (low, members) in enumerate(todo, 1):
        types = {t for t, _ in members}
        name = members[0][1]
        docs = sorted(t for t in types if onto.is_a(t, "document"))
        things = sorted(
            t for t in types if onto._allowed(proposes.range, t) and t not in docs
        )
        if not docs or not things:
            continue
        # never a page or a project of your own: "Ableton Live 7 Reference
        # Manual" does not propose Ableton Live, it is about it, and the
        # six such cases were all manuals typed as pages
        src_type = next(
            (
                t
                for t in docs
                if onto._allowed(proposes.domain, t) and t not in SELF_KINDS
            ),
            None,
        )
        if src_type is None:
            continue
        if not _has_authors(con, titles[low], name):
            continue
        for thing in things:
            edge = Edge(name, src_type, "proposes", name, thing)
            if find_edges(con, edge):
                existing += 1
                continue
            link(
                con,
                edge,
                confidence="INFERRED",
                source_doc=titles[low],
                evidence="the library holds a document of this title",
                producer="polysemy",
                run=run,
            )
            made += 1
            pairs[f"{src_type}->{thing}"] += 1
        if n % 100 == 0:
            job.update(done=n, total=len(todo), note=f"proposes: {made} edges")
    con.commit()
    return {
        "run": run,
        "linked": made,
        "existing": existing,
        **dict(pairs.most_common()),
    }


def _attachment(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """How much of the library each mechanism actually holds.

    Two invariants cannot be tested, only measured (CLAUDE.md): 6, that an
    answer stays small, and 7, that the serving path stays under a
    gigabyte. A number nobody takes is a wish, and invariant 6 was
    breached for months on exactly that account.

    The rest of this is the lesson of one question in German
    (`docs/eval/apfelkuchen-2026-09-26.md`). The route from `Apfel` to an
    English recipe was built and measured on 2026-09-24, and almost
    nothing was attached to it — 38 ingredient lists in ten thousand
    documents — so it failed like a mechanism that does not work.
    **A mechanism that works and is unattached is indistinguishable from
    a broken one from outside**, and the only way to tell is to count what
    uses it. So this pass counts, every night, and writes the counts on
    its job row where the Jobs view shows them.
    """
    out: dict[str, Any] = {}
    live = " AND json_extract(meta,'$.retired') IS NULL"
    counts = {
        "documents": f"SELECT COUNT(*) FROM documents WHERE 1=1{live}",
        "with a summary": "SELECT COUNT(*) FROM documents"
        f" WHERE json_extract(meta,'$.summary') IS NOT NULL{live}",
        "with a language": "SELECT COUNT(*) FROM documents"
        f" WHERE json_extract(meta,'$.lang') IS NOT NULL{live}",
        "with section summaries": "SELECT COUNT(*) FROM documents"
        f" WHERE json_extract(meta,'$.sections.text_hash') = text_hash{live}",
        "long enough for sections": "SELECT COUNT(*) FROM documents"
        f" WHERE text_len >= 60000{live}",
        "with an ingredient list": "SELECT COUNT(DISTINCT doc_id) FROM chunks"
        " WHERE kind = 'ingredients'",
        "in the kitchen domain": "SELECT COUNT(*) FROM documents"
        f" WHERE json_extract(meta,'$.domains') LIKE '%kitchen%'{live}",
        "with a reference list": "SELECT COUNT(DISTINCT doc_id) FROM chunks"
        " WHERE kind = 'reference'",
        "entities": "SELECT COUNT(*) FROM entities WHERE canonical_id IS NULL",
        "named in a second language": "SELECT COUNT(DISTINCT entity_id) FROM"
        " entity_labels WHERE lang IS NOT NULL AND lang != 'en'",
    }
    for name, sql in counts.items():
        try:
            out[name] = int(con.execute(sql).fetchone()[0])
        except sqlite3.Error:  # a count nobody can take is not a failed pass
            out[name] = -1
    # the pairs worth reading as a ratio, because the bare count hides the gap
    for whole, part in (
        ("long enough for sections", "with section summaries"),
        ("in the kitchen domain", "with an ingredient list"),
    ):
        if out.get(whole, 0) > 0 and out.get(part, -1) >= 0:
            out[f"{part} / {whole}"] = f"{100 * out[part] / out[whole]:.0f}%"
    job.note(
        ", ".join(
            f"{k} {v}"
            for k, v in out.items()
            if k.endswith("%") or k.startswith(("with an", "named in"))
        )
    )
    return out


def _names(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """Every entity's shown name rebuilt from its labels.

    `entities.name` is a cache of the preferred label in the language
    this host shows (`graph.language`; docs/identity.md). The labels say
    what a thing is called; this is what a join reads. Idempotent and a
    few seconds over 144,000 entities, because it writes only where the
    two have drifted — a pass that gave an entity a preferred name in
    another language, or a host that changed which language it shows.
    """
    from .graph import rename_display_language

    job.update(note="names from the labels")
    return rename_display_language(con)


def _lengths(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """``documents.text_len`` filled for the texts indexed before the
    column existed (``fill_text_lengths``); nothing once it is."""
    job.update(note="lengths: reading the artifacts without a length")
    return {"filled": fill_text_lengths(con)}


def _languages(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The language of every document that does not say yet
    (``prax.language`` over the head of its text artifact), and of every
    summary that does not say yet.

    Cheap and idempotent: a document with ``meta.lang`` is passed over, so
    the nightly only reads what arrived since. The summary's language is
    read from the summary itself and costs no file at all; it is what
    tells the ``summaries`` step which of them are not in the language the
    document field is written in.
    """
    from prax import language

    rows = con.execute(
        "SELECT id, text_hash FROM documents WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.lang') IS NULL"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ).fetchall()
    found: Counter[str] = Counter()
    unsure = 0
    for n, r in enumerate(rows, 1):
        path = config.archive_dir() / r["text_hash"][:2] / r["text_hash"]
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                text = fh.read(language.SAMPLE)
        except OSError:
            continue
        code = language.detect(text)
        if code is None:
            unsure += 1
            continue
        found[code] += 1
        con.execute(
            "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
            " '$.lang', ?) WHERE id = ?",
            (code, r["id"]),
        )
        if n % 200 == 0:
            con.commit()
            job.update(done=n, total=len(rows), note=f"languages: {n} of {len(rows)}")
    con.commit()

    written = con.execute(
        "SELECT id, json_extract(meta, '$.summary') AS summary FROM documents"
        " WHERE json_extract(meta, '$.summary') IS NOT NULL"
        " AND json_extract(meta, '$.summary_lang') IS NULL"
    ).fetchall()
    said: Counter[str] = Counter()
    for n, r in enumerate(written, 1):
        code = language.detect(r["summary"])
        if code is None:
            continue
        said[code] += 1
        con.execute(
            "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
            " '$.summary_lang', ?) WHERE id = ?",
            (code, r["id"]),
        )
        if n % 500 == 0:
            con.commit()
    con.commit()
    # the labels that never got one. A name is one to three words, under
    # the detector's floor, so `language.detect` says nothing for nearly
    # all of them; the document that named the entity knows
    from .graph import _named_by_language

    placed = 0
    for r in con.execute(
        "SELECT id, entity_id FROM entity_labels WHERE lang IS NULL"
    ).fetchall():
        code = _named_by_language(con, r["entity_id"])
        if code:
            con.execute(
                "UPDATE entity_labels SET lang = ? WHERE id = ?", (code, r["id"])
            )
            placed += 1
    con.commit()

    return {
        "read": len(rows),
        "unsure": unsure,
        "labels_placed": placed,
        **{k: v for k, v in found.most_common()},
        "summaries_read": len(written),
        "summaries_to_translate": sum(
            v for k, v in said.items() if k != language.canonical()
        ),
    }


def _rechunk(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """Every indexed document's chunks rebuilt from its text artifact
    (``documents.rechunk``); chunks whose text did not change keep their
    ids and vectors."""
    ids = [
        r[0]
        for r in con.execute(
            "SELECT id FROM documents WHERE text_hash IS NOT NULL"
            " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
        )
    ]
    chunks = 0
    for n, doc_id in enumerate(ids, 1):
        chunks += rechunk(con, doc_id)
        if n % 100 == 0:
            job.update(done=n, total=len(ids), note=f"rechunk: {n} of {len(ids)}")
    return {"documents": len(ids), "chunks": chunks}


def _rejudge(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The corpus's rulings asked again under the test as it stands.

    Until 2026-09-26 a name was the library's word if any English
    document used it, and `Mehl` was ruled English on a programming
    textbook. The ruling is recorded so the pass need not ask twice, which
    also means a better test never sees the names the old one let out.
    This asks it again, and a name it overturns goes back in the queue.
    On request only: it reads every ruled name, a minute or so.
    """
    from prax import vocabulary

    rulings = corpus_rulings(con)
    sizes = vocabulary.library_sizes(con)
    overturned = []
    for n, (entity_id, name) in enumerate(rulings, 1):
        if not vocabulary.in_english_text(con, name, sizes=sizes):
            overturned.append((entity_id, name))
        if n % 500 == 0:
            job.update(done=n, total=len(rulings), note=f"rejudge: {n}")
    unmark_corpus_ruling(con, overturned)
    return {"rulings": len(rulings), "overturned": len(overturned)}


_RUN = {
    "acronyms": _acronyms,
    "fields": _fields,
    "domains": _domains,
    "dedupe": _dedupe,
    "review": _review,
    "references": _references,
    "proposes": _proposes,
    "fts": _fts,
    "lengths": _lengths,
    "languages": _languages,
    "names": _names,
    "attachment": _attachment,
    "rechunk": _rechunk,
    "rejudge": _rejudge,
}


def maintain(
    con: sqlite3.Connection,
    *,
    only: list[str] | None = None,
    job: Job | None = None,
    log: Log | None = None,
) -> dict[str, Any]:
    """Run the maintenance passes (``PASSES``, or ``only`` those named —
    ``ON_REQUEST`` ones only that way) as one job; returns what each did
    and how long it took."""
    chosen = list(only) if only else list(PASSES)
    unknown = [p for p in chosen if p not in _RUN]
    if unknown:
        raise ValueError(
            f"no such pass: {unknown}; passes are {PASSES} and {ON_REQUEST}"
        )
    if job is not None:
        return _run(con, chosen, job, log)
    with Job(con, "maintain", note=", ".join(chosen)) as own:
        return _run(con, chosen, own, log)


def _run(
    con: sqlite3.Connection, chosen: list[str], job: Job, log: Log | None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in chosen:
        t0 = time.monotonic()
        job.update(note=name)
        result = _RUN[name](con, job)
        result["seconds"] = round(time.monotonic() - t0, 1)
        out[name] = result
        if log:
            log(f"{name}: {result}")
    job.update(
        note="done: " + ", ".join(f"{k} {v['seconds']} s" for k, v in out.items())
    )
    return out
