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
here for the whole queue). None of that needs a model, none of it needs
anyone to look first: it is what a nightly pass runs after the worker's,
and what ``prax maintain`` runs on request. ``rechunk`` — every chunk
rebuilt from its text artifact after a change to the chunker — is a
pass too, but only when named: the nightly has no reason to.

What stays out on purpose: the repairs (``store.repair``: a person picks
the ailment), the readings and extractions (the worker, with a model),
entity resolution (the likely merges are a decision). Each pass is a
name in ``PASSES``; ``maintain(only=[...])`` runs a subset. The whole
thing is one job, so the Jobs view shows which pass it is on.
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from prax import acronyms, config

from .documents import (
    assign_domains,
    dedupe_captures,
    rechunk,
    refresh_document_fields,
)
from .jobs import Job
from .retrieval import replace_acronyms

Log = Callable[[str], None]

PASSES = ("acronyms", "fields", "domains", "dedupe", "review")
ON_REQUEST = ("rechunk",)  # a pass only when named: the nightly has no reason to


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


_RUN = {
    "acronyms": _acronyms,
    "fields": _fields,
    "domains": _domains,
    "dedupe": _dedupe,
    "review": _review,
    "rechunk": _rechunk,
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
