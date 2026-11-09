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
list makes to documents in the library (``prax.text.references``: the entries
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
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from prax import config
from prax.graph import ontology
from prax.ml import clusters, embeddings
from prax.text import acronyms, dates, language, quotes, schemaorg

from .base import (
    LIVE,
    _serialized,
    archive_path,
    domain_clause,
    meta_of,
    now,
    run_name,
    vectors_available,
)
from .documents import (
    HISTORY_KEEP,
    _put_meta,
    assign_domains,
    bounded_histories,
    dedupe_captures,
    fill_text_lengths,
    get_meta,
    original_file,
    published_of,
    quality_record,
    rechunk,
    refresh_document_fields,
    suspect,
)
from .graph import (
    MARKUP_PRODUCER,
    Edge,
    communities_input,
    corpus_rulings,
    derive_rules,
    document_node,
    find_conflicts,
    find_edges,
    find_event_candidates,
    find_versions,
    forget_resolved_reviews,
    in_english_text,
    languages_by_entity,
    library_sizes,
    link,
    link_references,
    markup_facts,
    rename_display_language,
    replace_communities,
    replace_face_groups,
    retire_reading,
    unmark_corpus_ruling,
)
from .jobs import Job
from .retrieval import (
    compact_vectors,
    face_index_path,
    face_model,
    face_neighbours,
    fts_merge,
    merge_vectors,
    replace_acronyms,
)

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
    "published",
    "places",
    "markup",
    "quality",
    "private",
    "names",
    "attachment",
    "rules",
    "conflicts",
    "communities",
    "histories",
    "events",
    "facegroups",
    "versions",
)
# a pass only when named: the nightly has no reason to
ON_REQUEST = ("rechunk", "rejudge", "vectors")


@_serialized
def _write(con: sqlite3.Connection, sql: str, rows: list[tuple[Any, ...]]) -> int:
    """What a pass has worked out, applied in one short write behind the
    store's lock and committed; how many rows it changed. A pass reads and
    computes holding nothing, so a capture, a worker's post or the backup
    never waits on a file read or a parse: the markup pass once held
    SQLite's write lock from one commit to the next, a hundred pages
    apart, 71 minutes in all, and the night's backup failed on "database
    is locked" (2026-10-07)."""
    changed = con.executemany(sql, rows).rowcount if rows else 0
    con.commit()
    return max(changed, 0)


_SET_MARKUP = (
    "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
    " '$.markup', json(?)) WHERE id = ?"
)
_SET_PUBLISHED = (
    "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
    " '$.published', json(?)) WHERE id = ?"
    # what the pass read: a date a person set while it ran stays theirs
    " AND json_extract(meta, '$.published') IS NULL"
)
_SET_LANG = (
    "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
    " '$.lang', ?) WHERE id = ? AND json_extract(meta, '$.lang') IS NULL"
)
_SET_QUALITY = (
    "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
    " '$.quality', json(?)) WHERE id = ? AND text_hash = ?"
)
_SET_PROFILE = (
    "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
    " '$.profile', json(?)) WHERE id = ?"
)
_SET_SUMMARY_LANG = (
    "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
    " '$.summary_lang', ?) WHERE id = ?"
    " AND json_extract(meta, '$.summary_lang') IS NULL"
    " AND json_extract(meta, '$.summary') = ?"
)
_SET_LABEL_LANG = "UPDATE OR IGNORE entity_labels SET lang = ? WHERE id = ?"
_SET_PLACE = (
    "UPDATE edges SET evidence_start = ?, evidence_end = ?,"
    " evidence_text_hash = ? WHERE id = ? AND evidence_text_hash IS NULL"
)
BATCH = 500  # rows a pass applies in one write


def _acronyms(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """Every text artifact read once for its "phrase (ACRONYM)" definitions
    (``prax.text.acronyms``); the table replaced. Minutes over a large library."""
    counts: Counter[tuple[str, str]] = Counter()
    rows = con.execute(
        "SELECT id, text_hash FROM documents WHERE text_hash IS NOT NULL"
        f" AND {LIVE} ORDER BY id"
    ).fetchall()
    scanned = 0
    for n, r in enumerate(rows, 1):
        path = archive_path(r["text_hash"])
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
    counts = assign_domains(con, rules)
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
    typing rules over every open item (``review.apply_typing_rules``),
    then the items resolved more than ``REVIEW_KEEP_DAYS`` ago deleted."""
    from prax.graph import review

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
        "forgotten": forget_resolved_reviews(con),
    }


# ---------------------------------------------------------------- references


def _fts(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The keyword index's segments merged a little (``fts_merge``): a
    term read from one place instead of two dozen."""
    job.update(note="fts: merging segments")
    return fts_merge(con)


def _references(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The citations a document's reference list makes to the library
    (``link_references``): the documents whose text changed since their
    last reading, or never were read."""
    run = run_name("references")
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

    onto = ontology.current()
    proposes = onto.relations.get("proposes")
    if proposes is None:
        return {"skipped": "this ontology has no proposes relation"}
    titles: dict[str, int] = {}
    for doc_id, title in con.execute(
        f"SELECT id, title FROM documents WHERE title IS NOT NULL AND {LIVE}"
    ):
        titles.setdefault(title.lower(), doc_id)
    by_name: dict[str, list[tuple[str, str]]] = {}
    for name, etype in con.execute(
        "SELECT name, type FROM entities WHERE canonical_id IS NULL"
    ):
        by_name.setdefault(name.lower(), []).append((etype, name))

    run = run_name("polysemy")
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
                if onto._allowed(proposes.domain, t) and t not in ontology.SELF_KINDS
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
    live = f" AND {LIVE}"
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
    # the kitchen's documents by their domain set; a substring match until
    # 2026-11-09 counted any module whose name held "kitchen" (structure
    # review finding 10)
    clause, args = domain_clause(con, ["kitchen"], unset=False)
    out["in the kitchen domain"] = int(
        con.execute(
            f"SELECT COUNT(*) FROM documents d WHERE 1=1{clause}{live}", args
        ).fetchone()[0]
    )
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

    job.update(note="names from the labels")
    return rename_display_language(con)


def _lengths(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """``documents.text_len`` filled for the texts indexed before the
    column existed (``fill_text_lengths``); nothing once it is."""
    job.update(note="lengths: reading the artifacts without a length")
    return {"filled": fill_text_lengths(con)}


def _private(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The personal-document rules (``prax.wall.private``, stage V) over every
    open document no person has decided about and these rules have not
    looked at: a new rule in prax.yaml reads the library again, an
    unchanged one what arrived and what showed no cue (a file read and no
    write each). A document with no text yet is
    looked at by its title and paths, and again once it is read."""
    from prax.wall import private

    rules = private.rules()
    stamp = rules.stamp()
    rows = con.execute(
        "SELECT id FROM documents WHERE sensitivity IS NULL"
        f" AND {LIVE}"
        " AND coalesce(json_extract(meta, '$.sensitivity.by'), '') != 'human'"
        " AND (coalesce(json_extract(meta, '$.private.rules'), '') != ?"
        "  OR (text_hash IS NOT NULL AND json_extract(meta, '$.private.read') IS NULL))"
        " ORDER BY id",
        (stamp,),
    ).fetchall()
    suspected = 0
    for n, r in enumerate(rows, 1):
        cues = suspect(con, int(r["id"]), rules=rules)
        if cues is not None and rules.suspect(cues):
            suspected += 1
        if n % 200 == 0:
            job.update(done=n, total=len(rows), note=f"private: {n} of {len(rows)}")
    return {"looked_at": len(rows), "suspected": suspected, "rules": stamp}


def _markup(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The facts a web page's own schema.org markup states (JSON-LD, no
    model): authors, publisher, a recipe's ingredients and cuisine
    (``markup_facts``), EXTRACTED with producer ``jsonld``. Stamped with the
    original's hash (``meta.markup``), so the nightly reads a page once and
    again only when a re-capture replaced it, the earlier reading then
    ended. A fact the ontology does not take is counted, not queued."""
    rows = con.execute(
        "SELECT id, hash, title, meta FROM documents"
        " WHERE mime IN ('text/html', 'application/xhtml+xml') AND title IS NOT NULL"
        f" AND {LIVE}"
        " AND COALESCE(json_extract(meta, '$.markup.hash'), '') != hash ORDER BY id"
    ).fetchall()
    onto = ontology.current()
    # the ingredient words of each language the craft pack's lexicon has
    by_lang = ontology.lexicon().section("ingredients") or {}
    # a page whose own type names a genre is read too (a Q&A page, a thesis)
    standards = ontology.genres().standards()
    counts: Counter[str] = Counter()
    for n, r in enumerate(rows, 1):
        try:
            original = original_file(con, r["hash"]).read_bytes()
            page = schemaorg.of_page(original, standards)
        except OSError:
            continue
        # the markup queues no review items: the extraction's stay open
        retired = retire_reading(
            con, r["id"], producer=MARKUP_PRODUCER, except_version="", reviews=False
        )
        counts["ended"] += retired
        stamp: dict[str, Any] = {"hash": r["hash"], "at": now(), "facts": 0}
        if page is not None:
            stamp["type"] = page.own.kind if page.own else "Recipe"
            genre = ontology.genres().label_for(
                "Recipe" if len(page.recipes) == 1 else stamp["type"]
            )
            if genre:
                stamp["genre"] = genre
            try:
                name, etype = document_node(con, r["id"])
            except KeyError:
                name, etype = str(r["title"]), "document"
            # the page's language; for a page never indexed, the recipe
            # lines', detected or else by whose ingredient words they use
            lines = [line for x in page.recipes for line in x.ingredient_lines]
            lang = (
                meta_of(r["meta"]).get("lang")
                or language.detect(" ".join(lines))
                or schemaorg.likely_language(lines, by_lang)
            )
            words = schemaorg.ingredient_words(by_lang.get(lang or ""))
            for edge, evidence, world in markup_facts(name, etype, page, words):
                try:
                    onto.check_edge(edge.src_type, edge.rel, edge.dst_type)
                    onto.check_names(
                        edge.src, edge.src_type, edge.rel, edge.dst, edge.dst_type
                    )
                except ValueError:
                    counts["refused"] += 1
                    continue
                if find_edges(con, edge):
                    counts["existing"] += 1
                    continue
                link(
                    con,
                    edge,
                    source_doc=r["id"],
                    evidence=evidence,
                    producer=MARKUP_PRODUCER,
                    run=MARKUP_PRODUCER,
                    world_from=world,
                )
                stamp["facts"] += 1
                counts[edge.rel] += 1
            counts["pages"] += 1
        _write(con, _SET_MARKUP, [(json.dumps(stamp), r["id"])])
        if n % 100 == 0:
            job.update(done=n, total=len(rows), note=f"markup: {n} of {len(rows)}")
    return {"read": len(rows), **dict(counts)}


def _published(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """When every document that does not say yet was published
    (``store.published_of``): its record's date, what the extension found,
    the arXiv id, and for a web page the tags and markup at the head of
    its original. Idempotent: a document with ``meta.published`` is passed
    over, so the nightly reads only what arrived since; one that says
    nothing is read again each night, which costs a page's head."""
    rows = con.execute(
        "SELECT id, hash, mime, meta FROM documents"
        " WHERE json_extract(meta, '$.published') IS NULL"
        f" AND {LIVE} ORDER BY id"
    ).fetchall()
    found: Counter[str] = Counter()
    pending: list[tuple[Any, ...]] = []
    for n, r in enumerate(rows, 1):
        meta = meta_of(r["meta"])
        page = None
        if (r["mime"] or "") in ("text/html", "application/xhtml+xml"):
            try:
                with original_file(con, r["hash"]).open("rb") as fh:
                    page = dates.from_html(fh.read(dates.HEAD_BYTES))
            except OSError:
                page = None
        published = published_of(meta, page)
        if published is None:
            continue
        found[published["by"]] += 1
        pending.append((json.dumps({**published, "at": now()}), r["id"]))
        if len(pending) >= BATCH:
            _write(con, _SET_PUBLISHED, pending)
            pending = []
            job.update(done=n, total=len(rows), note=f"published: {n} of {len(rows)}")
    _write(con, _SET_PUBLISHED, pending)
    return {"read": len(rows), "dated": sum(found.values()), "by": dict(found)}


def _quality(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The checks of every text not checked yet, or checked under an older
    version of them or another text (``parsers.quality``, stage AO), kept
    in ``meta.quality``. Nothing is asked for: a person, or the door when a
    new text lands, decides what to read again.

    A PDF's original is profiled once (its first pages, no layout
    analysis) and the profile kept in ``meta.profile`` by its hash
    (``quality.assess``): 38 minutes over the library's 11,493 on the
    desktop, 2026-10-18, which a bump of the checks' version no longer
    repeats. A text that changed while the pass read it is left for the
    next."""
    from prax.parsers import quality

    rows = con.execute(
        "SELECT id, hash, mime, text_hash, meta FROM documents"
        f" WHERE text_hash IS NOT NULL AND {LIVE}"
        " AND (COALESCE(json_extract(meta, '$.quality.text_hash'), '') != text_hash"
        " OR COALESCE(json_extract(meta, '$.quality.version'), 0) < ?) ORDER BY id",
        (quality.VERSION,),
    ).fetchall()
    found: Counter[str] = Counter()
    pending: list[tuple[Any, ...]] = []
    profiled: list[tuple[Any, ...]] = []
    read_originals = 0
    for n, r in enumerate(rows, 1):
        try:
            text = archive_path(r["text_hash"]).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue

        def read(h: str = r["hash"]) -> bytes | None:
            try:
                return original_file(con, h).read_bytes()
            except OSError:
                return None

        meta = meta_of(r["meta"])
        got = quality.assess(
            text,
            stamp=str(meta.get("text_source") or ""),
            mime=r["mime"],
            original=r["hash"],
            meta=meta,
            read=read,
        )
        found.update(f.check for f in got.findings)
        held = quality_record(
            [f.as_dict() for f in got.findings], r["text_hash"], quality.VERSION
        )
        pending.append((json.dumps(held), r["id"], r["text_hash"]))
        if got.keep is not None:
            read_originals += 1
            profiled.append((json.dumps(got.keep), r["id"]))
        if len(pending) >= BATCH:
            _write(con, _SET_QUALITY, pending)
            _write(con, _SET_PROFILE, profiled)
            pending, profiled = [], []
            job.update(done=n, total=len(rows), note=f"quality: {n} of {len(rows)}")
    _write(con, _SET_QUALITY, pending)
    _write(con, _SET_PROFILE, profiled)
    return {"documents": len(rows), "profiled": read_originals, "found": dict(found)}


def _languages(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The language of every document that does not say yet
    (``prax.text.language`` over the head of its text artifact), and of every
    summary that does not say yet.

    Cheap and idempotent: a document with ``meta.lang`` is passed over, so
    the nightly only reads what arrived since. The summary's language is
    read from the summary itself and costs no file at all; it is what
    tells the ``summaries`` step which of them are not in the language the
    document field is written in.
    """

    rows = con.execute(
        "SELECT id, text_hash FROM documents WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.lang') IS NULL"
        f" AND {LIVE} ORDER BY id"
    ).fetchall()
    found: Counter[str] = Counter()
    unsure = 0
    pending: list[tuple[Any, ...]] = []
    for n, r in enumerate(rows, 1):
        path = archive_path(r["text_hash"])
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
        pending.append((code, r["id"]))
        if len(pending) >= BATCH:
            _write(con, _SET_LANG, pending)
            pending = []
            job.update(done=n, total=len(rows), note=f"languages: {n} of {len(rows)}")
    _write(con, _SET_LANG, pending)

    written = con.execute(
        "SELECT id, json_extract(meta, '$.summary') AS summary FROM documents"
        " WHERE json_extract(meta, '$.summary') IS NOT NULL"
        " AND json_extract(meta, '$.summary_lang') IS NULL"
    ).fetchall()
    said: Counter[str] = Counter()
    pending = []
    for r in written:
        code = language.detect(r["summary"])
        if code is None:
            continue
        said[code] += 1
        pending.append((code, r["id"], r["summary"]))
        if len(pending) >= BATCH:
            _write(con, _SET_SUMMARY_LANG, pending)
            pending = []
    _write(con, _SET_SUMMARY_LANG, pending)
    # the labels that never got one. A name is one to three words, under
    # the detector's floor, so `language.detect` says nothing for nearly
    # all of them; the document that named the entity knows. All entities
    # at once, and a commit every thousand labels: asked label by label it
    # held the write lock for 76 minutes and no capture could land
    # (2026-09-29). A label no single language names stays unplaced and
    # is looked at again the next night, which now costs seconds.

    known = languages_by_entity(con)
    # a label whose twin already carries that language (the same entity,
    # the same words), or a second preferred one in it, keeps no language:
    # the twin says the name already. Set anyway, it failed the whole pass
    # every night (2026-10-04); what the IGNORE left is the twins
    labels = [
        (code, int(r["id"]))
        for r in con.execute(
            "SELECT id, entity_id FROM entity_labels WHERE lang IS NULL"
        ).fetchall()
        if (code := known.get(int(r["entity_id"])))
    ]
    placed = sum(
        _write(con, _SET_LABEL_LANG, labels[i : i + BATCH])
        for i in range(0, len(labels), BATCH)
    )
    twinned = len(labels) - placed

    return {
        "read": len(rows),
        "unsure": unsure,
        "labels_placed": placed,
        "labels_twinned": twinned,
        **dict(found.most_common()),
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
            f" AND {LIVE} ORDER BY id"
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
    rulings = corpus_rulings(con)
    sizes = library_sizes(con)
    overturned = []
    for n, (entity_id, name) in enumerate(rulings, 1):
        if not in_english_text(con, name, sizes=sizes):
            overturned.append((entity_id, name))
        if n % 500 == 0:
            job.update(done=n, total=len(rulings), note=f"rejudge: {n}")
    unmark_corpus_ruling(con, overturned)
    return {"rulings": len(rulings), "overturned": len(overturned)}


def _rules(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """What follows from what is stated: the closure of each transitive
    relation, the converse of each symmetric one, kept in step with the
    asserted edges (``store.derive_rules``, stage AN). Nightly since its
    premises are checked (``part_of_suspects``): 18 of 20 right, measured
    on the library 2026-10-04."""
    job.update(note="rules over the graph")
    return dict(derive_rules(con))


def _conflicts(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """Two facts that cannot both hold: a functional relation with two
    values for one subject, neither part of the other, kept in
    ``edge_conflicts`` and in step with the edges (``store.find_conflicts``).
    Shown, never resolved: no fact is ended."""
    job.update(note="facts that disagree")
    return dict(find_conflicts(con))


def _histories(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The parse and extraction histories held to ``HISTORY_KEEP`` entries
    (``bounded_histories``), for documents written before the bound or by
    a writer that sets one key. Reads only the documents past it."""
    rows = con.execute(
        "SELECT id FROM documents"
        " WHERE json_array_length(meta, '$.parse_history') > ?"
        "    OR json_array_length(meta, '$.extraction_history') > ?",
        (HISTORY_KEEP, HISTORY_KEEP),
    ).fetchall()
    return {"documents": _bound_histories(con, [int(r[0]) for r in rows])}


@_serialized
def _bound_histories(con: sqlite3.Connection, ids: list[int]) -> dict[str, int]:
    # a document past the bound may hold only entries its readers need:
    # read every night, changed never
    dropped: Counter[str] = Counter()
    changed = 0
    for doc_id in ids:
        meta = get_meta(con, doc_id)
        gone = bounded_histories(meta, HISTORY_KEEP)
        if gone:
            dropped.update(gone)
            changed += 1
            _put_meta(con, doc_id, meta)
    con.commit()
    return {"read": len(ids), "changed": changed, **dropped}


def _vectors(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The embedder's vector files: both deltas merged, and the chunk index
    rid of the vectors of chunks that are gone, built anew when they are a
    tenth of it (``compact_vectors``). On request only: it rewrites a file
    of a gigabyte or more, minutes when the graph is rebuilt."""
    emb = embeddings.current()
    if emb is None or not vectors_available():
        return {"skipped": "no embedder or no usearch"}
    job.update(note=f"vectors: merging {emb.name}")
    merged = merge_vectors(emb.name)
    job.update(note=f"vectors: compacting {emb.name}")
    return {
        "merged": {k: v.get("merged", 0) for k, v in merged.items()},
        **compact_vectors(con, emb.name),
    }


def _communities(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The regions of the library rebuilt: the topical entities partitioned
    at two levels (``prax.graph.communities``), each new community keeping the
    id and summary of the old one it overlaps most, a summary whose
    members moved marked stale for the summaries step. Seconds."""
    from prax.graph import communities

    job.update(note="communities: the graph")
    docs, direct = communities_input(con, communities.topical_types())
    pairs = communities.graph_of(docs.values(), direct)
    job.update(note=f"communities: partitioning {len(pairs)} pairs")
    found = communities.partition(pairs)
    run = run_name("communities")
    kept = replace_communities(con, found.regions, found.parts, found.weight, run=run)
    return {
        "entities": found.entities,
        "pairs": found.pairs,
        "modularity": found.modularity,
        "regions": len(found.regions),
        "parts": len(found.parts),
        "carried": kept["carried"],
        "stale": kept["stale"],
        "new": kept["new"],
    }


def _places(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """Where each live edge's quote stands, for the edges written before
    extraction recorded it (migration 48): each document's text read once
    and every quote of its edges placed in it (``quotes.place``). A quote
    not in the text gets the text's hash and no range, so it is not looked
    for again each night. A place filled here is where the quote stood
    when this pass looked, which may be a later text than the one it was
    read from."""
    docs = [
        (int(r[0]), str(r[1]))
        for r in con.execute(
            "SELECT DISTINCT d.id, d.text_hash FROM edges e"
            " JOIN documents d ON d.id = e.source_doc"
            " WHERE e.valid_to IS NULL AND e.evidence IS NOT NULL"
            " AND e.evidence_text_hash IS NULL AND d.text_hash IS NOT NULL"
        )
    ]
    placed = missing = 0
    pending: list[tuple[Any, ...]] = []
    for n, (doc_id, text_hash) in enumerate(docs, 1):
        try:
            text = archive_path(text_hash).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for edge_id, evidence in con.execute(
            "SELECT id, evidence FROM edges WHERE source_doc = ? AND valid_to IS NULL"
            " AND evidence IS NOT NULL AND evidence_text_hash IS NULL",
            (doc_id,),
        ).fetchall():
            at = quotes.place(text, evidence)
            pending.append(
                (at[0] if at else None, at[1] if at else None, text_hash, edge_id)
            )
            placed += at is not None
            missing += at is None
        if len(pending) >= BATCH:
            _write(con, _SET_PLACE, pending)
            pending = []
            job.update(done=n, total=len(docs))
    _write(con, _SET_PLACE, pending)
    return {"documents": len(docs), "placed": placed, "not_in_text": missing}


def _events(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """Groups of photos taken close together, found again as candidates
    for the Review page's events tab (``store.find_event_candidates``):
    no model, and what a person decided is kept."""
    return find_event_candidates(con)


def _versions(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """Pairs of documents that are probably versions of one work, or one
    text twice, found again for the Review page's versions tab
    (``store.find_versions``): no model; a pair measured again only when a
    text changed, and what a person decided is kept."""
    return find_versions(con)


def _facegroups(con: sqlite3.Connection, job: Job) -> dict[str, Any]:
    """The faces that are probably one person, grouped again by density
    over the face vectors (``prax.ml.clusters``, HDBSCAN over each face's
    nearest faces in other pictures) for the Review page; a group keeps
    the id of the old one it overlaps most. Seconds; the settings are
    ``faces.groups`` in prax.yaml."""
    if not vectors_available() or not face_index_path().exists():
        return {"skipped": "no face index"}
    k = config.whole("faces.groups.neighbours", default=15)
    job.update(note=f"facegroups: the {k} nearest faces of each")
    ids, near = face_neighbours(con, k=k)
    job.update(note=f"facegroups: grouping {len(ids)} faces")
    found = clusters.group(
        near,
        min_size=config.whole("faces.groups.min_size", default=3),
        min_samples=config.whole("faces.groups.min_samples", default=3),
        floor=config.number("faces.groups.floor", default=0.3),
    )
    run = run_name("facegroups")
    kept = replace_face_groups(
        con,
        ids,
        found.labels,
        found.strength,
        found.persistence,
        model=face_model(),
        run=run,
    )
    grouped = sum(1 for g in found.labels if g != clusters.NOISE)
    return {"faces": len(ids), "grouped": grouped, **kept}


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
    "published": _published,
    "places": _places,
    "markup": _markup,
    "quality": _quality,
    "private": _private,
    "names": _names,
    "attachment": _attachment,
    "communities": _communities,
    "rechunk": _rechunk,
    "rejudge": _rejudge,
    "vectors": _vectors,
    "histories": _histories,
    "rules": _rules,
    "conflicts": _conflicts,
    "events": _events,
    "facegroups": _facegroups,
    "versions": _versions,
}


def pass_notes() -> dict[str, str]:
    """Each pass and the first sentence of what it does, from its own
    docstring, for the Jobs view's list: said once, where the pass is."""
    out = {}
    for name, fn in _RUN.items():
        # the docstring's markup is for the source, not the page
        doc = " ".join((fn.__doc__ or "").replace("`", "").split())
        out[name] = doc.split(". ")[0].rstrip(".") + "." if doc else ""
    return out


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
