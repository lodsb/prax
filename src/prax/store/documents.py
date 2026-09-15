"""Documents: taking one in, reading it back, and what is said about it.

The two-step ingest of invariant 3 (``register`` archives the original and
inserts the row, ``index_text`` stores the parsed text, chunks it and fills
FTS), the meta a document carries (source, capture, domains, promotion,
retirement, extraction stamps), and the document field — the few lines that
say what a document *is*, which the search ranks as its own list.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from prax import chunking, glyphs, ontology

from .base import (
    _NOW,
    _SURROGATE,
    _TOKEN,
    _archive_bytes,
    _archive_path,
    _like_prefix,
    _read_archive,
    _serialized,
)


@_serialized
def register(
    con: sqlite3.Connection,
    data: bytes,
    *,
    mime: str,
    title: str | None = None,
    source_url: str | None = None,
    meta: dict[str, Any] | None = None,
    original_path: str | None = None,
) -> dict[str, Any]:
    """Archive the original bytes and insert the document row.

    The hash is of the original bytes. Nothing is parsed or indexed here;
    ``parsed_at`` stays NULL until ``index_text`` runs.
    Returns ``{'doc_id', 'hash', 'created'}``; a known hash is a no-op.
    """
    digest = _archive_bytes(data)
    row = con.execute("SELECT id FROM documents WHERE hash = ?", (digest,)).fetchone()
    if row:
        return {"doc_id": row["id"], "hash": digest, "created": False}
    cur = con.execute(
        "INSERT INTO documents (hash, mime, title, source_url, original_path, meta)"
        " VALUES (?,?,?,?,?,?)",
        (digest, mime, title, source_url, original_path, json.dumps(meta or {})),
    )
    con.commit()
    return {"doc_id": cur.lastrowid, "hash": digest, "created": True}


@_serialized
def index_text(
    con: sqlite3.Connection,
    doc_id: int,
    text: str,
    *,
    text_source: str | None = None,
) -> dict[str, Any]:
    """Store parsed text as its own artifact, (re)build chunks and FTS rows.

    ``text_source`` names what produced the text (an extractor stamp such as
    ``"pymupdf4llm/0.0.27"`` or ``"zotero-ft-cache"``) and is written to
    ``meta.text_source`` in the same transaction.
    """
    exists = con.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if exists is None:
        raise KeyError(f"no such document: {doc_id}")
    text = _cleaned(text)
    data = text.encode("utf-8")
    text_hash = _archive_bytes(data)
    n_chunks = _write_chunks(con, doc_id, text)
    con.execute(
        f"UPDATE documents SET text_hash = ?, parsed_at = {_NOW} WHERE id = ?",
        (text_hash, doc_id),
    )
    if text_source is not None:
        con.execute(
            "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
            " '$.text_source', ?) WHERE id = ?",
            (text_source, doc_id),
        )
    _refresh_document_field(con, doc_id)
    con.commit()
    return {"doc_id": doc_id, "text_hash": text_hash, "n_chunks": n_chunks}


def _cleaned(text: str) -> str:
    """What every text goes through before it is stored: NUL bytes
    (pdftotext emits them for some page numbers) truncate SQLite's text
    functions and the FTS tokenizer; lone surrogates (MuPDF, broken fonts)
    cannot be encoded at all — neither carries content; ligature and
    Symbol-font code points become the letters they stand for
    (``prax.glyphs``)."""
    return glyphs.clean(_SURROGATE.sub("�", text.replace("\x00", "")))


def text_unchanged(con: sqlite3.Connection, doc_id: int, text: str) -> bool:
    """Whether ``text`` is byte for byte the document's current artifact
    (after the same cleaning ``index_text`` applies)."""
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None or not row["text_hash"]:
        return False
    digest = hashlib.sha256(_cleaned(text).encode("utf-8")).hexdigest()
    return digest == row["text_hash"]


@_serialized
def set_text_source(con: sqlite3.Connection, doc_id: int, stamp: str) -> None:
    """Record which extractor the current text is from, text untouched."""
    con.execute(
        "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
        " '$.text_source', ?) WHERE id = ?",
        (stamp, doc_id),
    )
    con.commit()


def _write_chunks(con: sqlite3.Connection, doc_id: int, text: str) -> int:
    """Replace a document's chunks with structure-aware ones (prax.chunking).

    A chunk whose text is unchanged keeps its id, and with it its vector
    (``chunk_embeddings`` is keyed by the id): a re-read that adds a figure
    or fixes a page re-embeds the chunks that changed, not the document.
    The others are deleted — their embedding rows cascade away; the index
    keeps stale keys that queries filter out and ``compact_vectors()``
    removes — and the new ones inserted."""
    rows = chunking.rows(chunking.chunk(text))
    old: dict[str, int] = {}
    for r in con.execute("SELECT id, text FROM chunks WHERE doc_id = ?", (doc_id,)):
        old.setdefault(r["text"], r["id"])
    kept: dict[int, int] = {}  # new seq -> old id
    used: set[int] = set()
    for i, r in enumerate(rows):
        oid = old.get(r[0])
        if oid is not None and oid not in used:
            kept[i] = oid
            used.add(oid)
    if used:
        marks = ",".join("?" * len(used))
        con.execute(
            f"DELETE FROM chunks WHERE doc_id = ? AND id NOT IN ({marks})",
            (doc_id, *used),
        )
        # seq is unique per document: park the kept rows below zero first
        con.executemany(
            "UPDATE chunks SET seq = ? WHERE id = ?",
            [(-(i + 1), oid) for i, oid in kept.items()],
        )
    else:
        con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    con.executemany(
        "INSERT INTO chunks (doc_id, seq, text, kind, locator, heading, data)"
        " VALUES (?,?,?,?,?,?,?)",
        [(doc_id, i, *r) for i, r in enumerate(rows) if i not in kept],
    )
    con.executemany(
        "UPDATE chunks SET seq = ?, kind = ?, locator = ?, heading = ?, data = ?"
        " WHERE id = ?",
        [(i, *rows[i][1:], oid) for i, oid in kept.items()],
    )
    return len(rows)


@_serialized
def rechunk(con: sqlite3.Connection, doc_id: int) -> int:
    """Rebuild a document's chunks from its text artifact; return the count.

    The artifact and everything else stay untouched (chunks are disposable,
    rationale R3). Used after a chunker change or a schema migration that
    added chunk columns.
    """
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    if not row["text_hash"]:
        return 0
    text = _read_archive(row["text_hash"]).decode("utf-8")
    n = _write_chunks(con, doc_id, text)
    con.commit()
    return n


def _is_indexed(con: sqlite3.Connection, doc_id: int) -> bool:
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    return bool(row and row["text_hash"])


@_serialized
def is_indexed(con: sqlite3.Connection, doc_id: int) -> bool:
    """True once ``index_text`` has stored a text artifact for the document."""
    return _is_indexed(con, doc_id)


@_serialized
def get_meta(con: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    """The document's ``meta`` JSON without touching the text artifact."""
    row = con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    return json.loads(row["meta"]) if row["meta"] else {}


@_serialized
def set_meta(
    con: sqlite3.Connection,
    doc_id: int,
    meta: dict[str, Any],
    *,
    title: str | None = None,
    source_url: str | None = None,
) -> None:
    """Replace ``meta`` (and optionally title / source_url) of a document.

    Importers use this to merge provenance when a known hash turns up again
    under another source record.
    """
    cur = con.execute(
        "UPDATE documents SET meta = ?, title = COALESCE(?, title),"
        " source_url = COALESCE(?, source_url) WHERE id = ?",
        (json.dumps(meta), title, source_url, doc_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no such document: {doc_id}")
    _refresh_document_field(con, doc_id)
    con.commit()


@_serialized
def retitle(
    con: sqlite3.Connection,
    doc_id: int,
    title: str,
    *,
    source: str,
    run: str | None = None,
    confidence: str | None = None,
) -> dict[str, Any]:
    """Change a document's title, keeping the old one.

    ``meta.title_history`` accumulates the replaced titles with their source;
    ``meta.title_source`` names who wrote the current one (an importer keeps
    its hands off a title it did not write). The ``paper`` entity carrying
    the old title follows: renamed when it is this document's alone, merged
    into the entity of the new title when one exists, left alone when other
    documents share the old title. The document field is refreshed, so the
    new title is searchable and the document vector is embedded again.
    """
    title = " ".join(title.split())
    if not title:
        raise ValueError("a title cannot be empty")
    row = con.execute(
        "SELECT title, meta FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    old = row["title"] or ""
    meta = json.loads(row["meta"] or "{}")
    if old == title:
        return {"doc_id": doc_id, "title": title, "changed": False, "entity": None}
    history = list(meta.get("title_history") or [])
    history.append(
        {
            "title": old,
            "source": meta.get("title_source"),
            "until": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    meta["title_history"] = history
    meta["title_source"] = source
    meta["title_run"] = run
    meta["title_confidence"] = confidence
    con.execute(
        "UPDATE documents SET title = ?, meta = ? WHERE id = ?",
        (title, json.dumps(meta), doc_id),
    )
    entity_action = None
    shared = con.execute(
        "SELECT count(*) FROM documents WHERE title = ? AND id != ?", (old, doc_id)
    ).fetchone()[0]
    if old and not shared:
        e = con.execute(
            "SELECT id FROM entities WHERE name = ? AND type = 'paper'", (old,)
        ).fetchone()
        if e is not None:
            target = con.execute(
                "SELECT id, canonical_id FROM entities"
                " WHERE name = ? AND type = 'paper'",
                (title,),
            ).fetchone()
            if target is None:
                con.execute(
                    "UPDATE entities SET name = ? WHERE id = ?", (title, e["id"])
                )
                entity_action = "renamed"
            elif target["id"] != e["id"]:
                survivor = target["canonical_id"] or target["id"]
                if survivor != e["id"]:
                    con.execute(
                        "UPDATE entities SET canonical_id = ?"
                        " WHERE id = ? OR canonical_id = ?",
                        (survivor, e["id"], e["id"]),
                    )
                    entity_action = "merged"
    _refresh_document_field(con, doc_id)
    con.commit()
    return {
        "doc_id": doc_id,
        "old": old,
        "title": title,
        "changed": True,
        "entity": entity_action,
    }


# A document that should not be found any more (a duplicate capture, a
# page saved by mistake) is retired, not deleted: the row and the archived
# bytes stay, ``meta.retired`` says why and since when, its chunks and its
# retrieval field go (so search and the batch jobs pass it by), and its
# edges end (invariant 8). ``unretire_document`` brings the index back.


def is_retired(meta: dict[str, Any]) -> bool:
    return bool(meta.get("retired"))


@_serialized
def retire_document(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    reason: str,
    duplicate_of: int | None = None,
    by: str = "human",
) -> dict[str, Any]:
    """Take a document out of the index and the graph, keeping row, bytes
    and text artifact. Returns what went: chunks, edges, review items.

    With ``duplicate_of`` the document is a second copy of ``duplicate_of``
    (a page sent twice, two Zotero snapshots): what it holds and the
    keeper lacks moves over first — edges with their evidence and
    producer, open review items, tags, domains, the summary, the
    extraction stamp — and only what both hold is ended here. Nothing is
    deleted; the union is what a person would have wanted from one
    document."""
    meta = get_meta(con, doc_id)
    if duplicate_of is not None and get_meta(con, duplicate_of) is None:
        raise KeyError(f"no such document: {duplicate_of}")
    moved: dict[str, int] = {"moved_edges": 0, "moved_items": 0}
    if duplicate_of is not None and duplicate_of != doc_id:
        moved = _join_duplicate(con, doc_id, duplicate_of)
        meta = get_meta(con, doc_id)
    meta["retired"] = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "reason": reason,
        "of": duplicate_of,
        "by": by,
    }
    chunks = con.execute(
        "SELECT count(*) FROM chunks WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    con.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
    con.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
    edges = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE source_doc = ? AND valid_to IS NULL",
        (doc_id,),
    ).rowcount
    items = con.execute(
        f"UPDATE review_queue SET resolution = 'dropped', resolved_at = {_NOW}"
        " WHERE source_doc = ? AND resolution IS NULL",
        (doc_id,),
    ).rowcount
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return {
        "doc_id": doc_id,
        "chunks": chunks,
        "edges": edges,
        "review_items": items,
        **moved,
    }


def _join_duplicate(
    con: sqlite3.Connection, doc_id: int, keeper: int
) -> dict[str, int]:
    """The union: what ``doc_id`` holds and ``keeper`` lacks becomes the
    keeper's (see ``retire_document``); returns what moved."""
    moved_edges = 0
    for e in con.execute(
        "SELECT e.id, e.src, e.rel, e.dst FROM edges e"
        " WHERE e.source_doc = ? AND e.valid_to IS NULL",
        (doc_id,),
    ).fetchall():
        held = con.execute(
            "SELECT 1 FROM edges WHERE source_doc = ? AND src = ? AND rel = ?"
            " AND dst = ? AND valid_to IS NULL LIMIT 1",
            (keeper, e["src"], e["rel"], e["dst"]),
        ).fetchone()
        if held is None:
            con.execute(
                "UPDATE edges SET source_doc = ? WHERE id = ?", (keeper, e["id"])
            )
            moved_edges += 1
    moved_items = 0
    for it in con.execute(
        "SELECT id, src, rel, dst FROM review_queue"
        " WHERE source_doc = ? AND resolved_at IS NULL",
        (doc_id,),
    ).fetchall():
        held = con.execute(
            "SELECT 1 FROM review_queue WHERE source_doc = ? AND src = ? AND rel = ?"
            " AND dst = ? AND resolved_at IS NULL LIMIT 1",
            (keeper, it["src"], it["rel"], it["dst"]),
        ).fetchone()
        if held is None:
            con.execute(
                "UPDATE review_queue SET source_doc = ? WHERE id = ?",
                (keeper, it["id"]),
            )
            moved_items += 1
    mine, theirs = get_meta(con, doc_id), get_meta(con, keeper)
    tags = list(theirs.get("tags") or [])
    for t in mine.get("tags") or []:
        if t not in tags:
            tags.append(t)
    if tags:
        theirs["tags"] = tags
    if mine.get("domains") and not theirs.get("domains"):
        theirs["domains"] = list(mine["domains"])
        theirs["domains_by"] = mine.get("domains_by", "rule")
    for key in ("summary", "extraction", "promote"):
        if mine.get(key) and not theirs.get(key):
            theirs[key] = mine[key]
    theirs.setdefault("recaptured", []).append(
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "session": (mine.get("capture") or {}).get("session"),
            "by": (mine.get("capture") or {}).get("by"),
            "was": doc_id,
        }
    )
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(theirs), keeper)
    )
    return {"moved_edges": moved_edges, "moved_items": moved_items}


@_serialized
def unretire_document(con: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    """Bring a retired document back into the index (its text artifact is
    re-chunked); the edges it lost stay history and a new extraction pass
    re-reads it."""
    meta = get_meta(con, doc_id)
    meta.pop("retired", None)
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    n = 0
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row and row["text_hash"]:
        text = _read_archive(row["text_hash"]).decode("utf-8")
        n = _write_chunks(con, doc_id, text)
        _refresh_document_field(con, doc_id)
    con.commit()
    return {"doc_id": doc_id, "chunks": n}


def fingerprint_text(text: str) -> frozenset[str]:
    """The chunk fingerprint of a text: a hash per chunk the chunker would
    make, so two captures of one page compare by what they say, not by
    their bytes (a page's markup changes between two visits, its text
    seldom does)."""
    rows = chunking.rows(chunking.chunk(text))
    # figure chunks are left out on both sides (``chunk_fingerprint``): a
    # page with ten figures compared at 35/45 before, and identical
    # captures of it counted as different pages
    return frozenset(
        hashlib.sha1(" ".join(str(r[0]).split()).encode("utf-8")).hexdigest()
        for r in rows
        if str(r[0]).strip() and r[1] != "figure"
    )


def chunk_fingerprint(con: sqlite3.Connection, doc_id: int) -> frozenset[str]:
    """The fingerprint of an indexed document, from its chunks."""
    return frozenset(
        hashlib.sha1(" ".join(t.split()).encode("utf-8")).hexdigest()
        for (t,) in con.execute(
            "SELECT text FROM chunks WHERE doc_id = ? AND kind != 'figure'", (doc_id,)
        )
        if t.strip()
    )


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two fingerprints; 1.0 for the same text."""
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


DUPLICATE_THRESHOLD = 0.9


def live_captures_of(con: sqlite3.Connection, url: str) -> list[dict[str, Any]]:
    """The live (not retired) captures of one canonical URL, oldest first."""
    out = []
    for r in con.execute(
        "SELECT id, meta FROM documents WHERE source_url = ?"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id",
        (url,),
    ):
        meta = json.loads(r["meta"] or "{}")
        out.append({"doc_id": r["id"], "meta": meta})
    return out


def capture_rank(meta: dict[str, Any]) -> tuple[int, int]:
    """Which of two captures of one page to keep: a snapshot over a bare
    DOM, an extracted one over one not yet read; ties go to the older."""
    mode = (meta.get("capture") or {}).get("mode")
    return (1 if mode == "snapshot" else 0, 1 if meta.get("extraction") else 0)


@_serialized
def note_recapture(
    con: sqlite3.Connection, doc_id: int, *, session: str | None, by: str | None
) -> None:
    """The page was sent again and said the same: remembered on the
    document, no new row."""
    meta = get_meta(con, doc_id)
    again = meta.setdefault("recaptured", [])
    again.append(
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "session": session,
            "by": by,
        }
    )
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()


def dedupe_captures(
    con: sqlite3.Connection,
    *,
    threshold: float = DUPLICATE_THRESHOLD,
    commit: bool = True,
) -> dict[str, Any]:
    """Among the live captures of each URL, keep one (``capture_rank``)
    and retire the others whose text fingerprint matches it. Two captures
    of a page that changed in between both stay. Returns the groups."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in con.execute(
        "SELECT id, source_url, meta FROM documents WHERE source_url IS NOT NULL"
        " AND json_extract(meta, '$.source') = 'capture'"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ):
        groups.setdefault(r["source_url"], []).append(
            {"doc_id": r["id"], "meta": json.loads(r["meta"] or "{}")}
        )
    report: dict[str, Any] = {"groups": [], "retired": 0, "kept_apart": 0}
    for url, docs in groups.items():
        if len(docs) < 2:
            continue
        keeper = max(docs, key=lambda d: (capture_rank(d["meta"]), -d["doc_id"]))
        kfp = chunk_fingerprint(con, keeper["doc_id"])
        gone, apart = [], []
        for d in docs:
            if d is keeper:
                continue
            s = similarity(kfp, chunk_fingerprint(con, d["doc_id"]))
            if s >= threshold:
                gone.append(d["doc_id"])
                if commit:
                    retire_document(
                        con,
                        d["doc_id"],
                        reason="duplicate capture",
                        duplicate_of=keeper["doc_id"],
                        by="dedupe",
                    )
            else:
                apart.append((d["doc_id"], round(s, 2)))
        report["groups"].append(
            {"url": url, "keep": keeper["doc_id"], "retire": gone, "apart": apart}
        )
        report["retired"] += len(gone)
        report["kept_apart"] += len(apart)
    return report


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
    modules of the current ontology other than core."""
    meta = get_meta(con, doc_id)
    if domains is None:
        meta.pop("domains", None)
        meta.pop("domains_by", None)
    else:
        meta["domains"] = _check_domains(domains)
        meta["domains_by"] = by
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return meta.get("domains")


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
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
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


# A reading request: a person (or an agent) asks for a named extractor on
# one document — the vision model over its scanned pages, a second reading
# of an image, OCR in another script, Docling — and a worker does it
# through the work protocol, which hands requests out before the pending
# captures. The request lives in ``meta.reading`` until the result comes
# back, then records the outcome, so the document page can say what
# happened and the Jobs page what is waiting.

READINGS = (
    "vision-pages",
    "vision",
    "figures",
    "figure-refs",
    "pymupdf4llm-ocr",
    "docling",
    "trafilatura",
    "pymupdf4llm",
)
# the setting a request may choose for one run, per extractor
MODES: dict[str, tuple[str, ...] | None] = {
    "vision-pages": ("scans", "all"),  # the pages without a text layer, or every page
    # the figures a caption claims or every image; "-again" reads the ones
    # this model has read before too, replacing its earlier reading
    "figures": ("captioned", "all", "again", "all-again"),
    "pymupdf4llm-ocr": None,  # the recognizer's script: ch, en, latin, arabic…
}
_MODE_WORD = re.compile(r"[a-z][a-z0-9_]*")


def check_mode(extractor: str, mode: str | None) -> None:
    """``mode`` is one the extractor takes (``MODES``): a fixed choice, or
    for OCR any language word the recognizer knows."""
    if extractor not in READINGS:
        raise ValueError(f"extractor must be one of {READINGS}")
    if mode is None:
        return
    if extractor not in MODES:
        raise ValueError(f"{extractor} takes no mode")
    allowed = MODES[extractor]
    if allowed is None:
        if not _MODE_WORD.fullmatch(mode):
            raise ValueError(f"a language word, not {mode!r}")
    elif mode not in allowed:
        raise ValueError(f"mode must be one of {allowed} for {extractor}")


@_serialized
def request_reading(
    con: sqlite3.Connection,
    doc_id: int,
    extractor: str,
    *,
    mode: str | None = None,
    by: str = "human",
) -> dict[str, Any]:
    """Ask for ``extractor`` (one of ``READINGS``) on a document; ``mode``
    is the extractor's setting for this run (``MODES``: ``vision-pages``
    reads the scans or every page, ``figures`` the captioned ones or every
    image). A request replaces an earlier one."""
    check_mode(extractor, mode)
    row = con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    meta = json.loads(row["meta"]) if row["meta"] else {}
    meta["reading"] = {
        "extractor": extractor,
        "mode": mode,
        "by": by,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state": "requested",
    }
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return meta["reading"]


def unreadable_documents(
    con: sqlite3.Connection, *, limit: int | None = None
) -> list[int]:
    """Documents without text whose every extractor has tried and found
    none (kept, empty or an error under its current stamp): scans without
    a text layer, mostly. The door does not hand them out again; a
    reading asked for — OCR, the vision model — is the way on."""
    from prax import parsers
    from prax.parsers import queue

    out: list[int] = []
    rows = con.execute(
        "SELECT id, mime, meta FROM documents WHERE text_hash IS NULL"
        "   AND json_extract(meta, '$.retired') IS NULL"
        "   AND json_extract(meta, '$.parse_history') IS NOT NULL"
        " ORDER BY id"
    )
    for r in rows:
        exts = parsers.candidates((r["mime"] or "").strip())
        meta = json.loads(r["meta"] or "{}")
        if exts and any(queue._seen(meta, e.stamp) for e in exts):
            out.append(r["id"])
            if limit and len(out) >= limit:
                break
    return out


def select_for_reading(
    con: sqlite3.Connection,
    *,
    ids: list[int] | None = None,
    mime: str | None = None,
    text_source: str | None = None,
    title: str | None = None,
    unreadable: bool = False,
    read_figures: bool = False,
    limit: int | None = None,
) -> list[int]:
    """The documents a reading is asked for at once: the ``ids`` given,
    narrowed by a MIME type or prefix (``application/pdf``, ``image/``),
    by the prefix of the text-source stamp (``pymupdf4llm/1.28.2``: what
    an old extractor read), by words the title contains, to the
    unreadable ones, and to the ones holding a figure a model has read
    (``read_figures``: what a better prompt or a better model goes over
    again); every filter given must hold. Retired documents are never
    selected."""
    sql = "SELECT id FROM documents WHERE json_extract(meta, '$.retired') IS NULL"
    args: list[Any] = []
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        args.extend(int(i) for i in ids)
    if mime:
        sql += " AND coalesce(mime, '') LIKE ? ESCAPE '!'"
        args.append(_like_prefix(mime))
    if title:
        sql += " AND lower(coalesce(title, '')) LIKE ? ESCAPE '!'"
        args.append("%" + _like_prefix(title.lower())[:-1] + "%")
    if text_source:
        sql += (
            " AND coalesce(json_extract(meta, '$.text_source'), '') LIKE ? ESCAPE '!'"
        )
        args.append(_like_prefix(text_source))
    sql += " ORDER BY id"
    chosen = [r[0] for r in con.execute(sql, args)]
    if unreadable:
        keep = set(unreadable_documents(con))
        chosen = [i for i in chosen if i in keep]
    if read_figures:
        rows = con.execute(
            "SELECT DISTINCT doc_id FROM chunks WHERE kind = 'figure'"
            " AND json_array_length(json_extract(data, '$.readings')) > 0"
        )
        read = {r[0] for r in rows}
        chosen = [i for i in chosen if i in read]
    return chosen[:limit] if limit else chosen


def request_readings(
    con: sqlite3.Connection,
    ids: list[int],
    extractor: str,
    *,
    mode: str | None = None,
    by: str = "human",
) -> dict[str, int]:
    """A reading request on each of ``ids`` (``request_reading``); a
    document the extractor does not read is skipped and counted."""
    from prax import parsers

    check_mode(extractor, mode)
    requested = skipped = 0
    for doc_id in ids:
        row = con.execute(
            "SELECT mime FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None or not parsers.candidates(
            row["mime"] or "", preferred=extractor
        ):
            skipped += 1
            continue
        request_reading(con, doc_id, extractor, mode=mode, by=by)
        requested += 1
    return {"selected": len(ids), "requested": requested, "skipped": skipped}


@_serialized
def cancel_reading(con: sqlite3.Connection, doc_id: int) -> bool:
    """Withdraw a request (or forget a finished one)."""
    meta = get_meta(con, doc_id)
    if not meta.pop("reading", None):
        return False
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return True


@_serialized
def finish_reading(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    outcome: str,
    stamp: str,
    error: str | None = None,
) -> None:
    """The worker's result for a requested reading: ``outcome`` is the
    parse action (``upgraded``, ``created``, ``kept``, ``empty``) or
    ``error`` with its message."""
    meta = get_meta(con, doc_id)
    reading = meta.get("reading")
    if not reading:
        return
    reading.update(
        state="error" if error else "done",
        outcome=outcome,
        stamp=stamp,
        error=error,
        finished_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()


def reading_requests(
    con: sqlite3.Connection, *, state: str | None = "requested", limit: int = 50
) -> list[dict[str, Any]]:
    """Documents with a reading request: the waiting ones (``state``
    ``requested``), or every state (None), newest first."""
    rows = con.execute(
        "SELECT id, title, mime, meta FROM documents"
        " WHERE json_extract(meta, '$.reading') IS NOT NULL"
        + (" AND json_extract(meta, '$.reading.state') = ?" if state else "")
        + " ORDER BY json_extract(meta, '$.reading.at') DESC LIMIT ?",
        ((state, limit) if state else (limit,)),
    ).fetchall()
    return [
        {
            "doc_id": r["id"],
            "title": r["title"],
            "mime": r["mime"],
            **json.loads(r["meta"])["reading"],
        }
        for r in rows
    ]


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


@_serialized
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


@_serialized
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


# What a document *is*, in a few lines, indexed apart from its chunks
# (migration 0005): title, kind words, creators, venue, the extraction
# summary, an image description's opening paragraph. A short field makes a
# match in it strong under BM25 and gives one vector per document, so a
# query naming a thing finds the document that is that thing, not the
# documents that mention it most.

DOCTYPES: dict[str, str] = {
    "pdf": "d.mime = 'application/pdf'",
    "web": "d.mime IN ('text/html', 'application/xhtml+xml')",
    "image": "d.mime LIKE 'image/%'",
    "text": "d.mime = 'text/plain'",
    "note": "json_extract(d.meta, '$.zotero.kind') = 'note'",
    "page": "json_extract(d.meta, '$.source') = 'wiki'",
}


_KIND_WORDS = {
    "application/pdf": "PDF document",
    "text/html": "web page",
    "application/xhtml+xml": "web page",
    "text/plain": "text",
}


def document_field(con: sqlite3.Connection, doc_id: int) -> str | None:
    """The retrieval field of a document, or None when it does not exist."""
    row = con.execute(
        """
        SELECT d.title, d.mime, d.meta,
               (SELECT kind FROM chunks c WHERE c.doc_id = d.id ORDER BY seq LIMIT 1)
                   AS first_kind
        FROM documents d WHERE d.id = ?
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    meta = json.loads(row["meta"] or "{}")
    if meta.get("retired"):
        return None  # a retired document has no retrieval field
    mime = row["mime"] or ""
    words: list[str] = []
    if mime.startswith("image/"):
        words.append("image")
    elif mime in _KIND_WORDS:
        words.append(_KIND_WORDS[mime])
    z = meta.get("zotero") or {}
    if z.get("kind") == "note":
        words.append("note")
    page = meta.get("page") or {}
    if page.get("kind"):
        words.append(
            {
                "addendum": "note page",
                "project": "project page",
                "synthesis": "synthesis page",
            }.get(page["kind"], "wiki page")
        )
    if row["first_kind"] == "code":
        words.append("source code")
    source = str(meta.get("text_source") or "")
    if source.startswith(("claude-vision", "vision/")):
        words.append("image description")
    parts = [row["title"] or "", " ".join(words)]
    creators = [c.get("name") for c in meta.get("creators", []) if c.get("name")]
    if creators:
        parts.append("by " + ", ".join(creators[:6]))
    fields = meta.get("fields") or {}
    venue = fields.get("publicationTitle") or fields.get("proceedingsTitle")
    bits = [b for b in (venue, str(meta.get("date") or "")[:4]) if b]
    if bits:
        parts.append(" ".join(bits))
    if meta.get("summary"):
        parts.append(str(meta["summary"]))
    if source.startswith(("claude-vision", "vision/")):
        shows = con.execute(
            "SELECT text FROM chunks WHERE doc_id = ?"
            " AND heading LIKE '%What it shows%' ORDER BY seq LIMIT 1",
            (doc_id,),
        ).fetchone()
        if shows:
            lines = [ln for ln in shows["text"].splitlines() if not ln.startswith("#")]
            parts.append(" ".join(lines).strip()[:1200])
    return "\n".join(p for p in parts if p.strip())


def _refresh_document_field(con: sqlite3.Connection, doc_id: int) -> bool:
    """Rewrite the field row when it changed; a changed field also drops the
    document's vector bookkeeping so it is embedded again. No commit."""
    field = document_field(con, doc_id)
    if field is None:
        return False
    old = con.execute(
        "SELECT field FROM documents_fts WHERE rowid = ?", (doc_id,)
    ).fetchone()
    if old is not None and old[0] == field:
        return False
    con.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
    con.execute(
        "INSERT INTO documents_fts(rowid, field) VALUES (?, ?)", (doc_id, field)
    )
    con.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
    return True


@_serialized
def refresh_document_fields(
    con: sqlite3.Connection, doc_ids: list[int] | None = None
) -> int:
    """Rebuild the field of the given documents (all when None); returns
    how many changed. The backfill after the migration, and the repair
    after a change to ``document_field``."""
    ids = doc_ids or [r[0] for r in con.execute("SELECT id FROM documents ORDER BY id")]
    changed = 0
    for i, doc_id in enumerate(ids, 1):
        if _refresh_document_field(con, doc_id):
            changed += 1
        if i % 500 == 0:
            con.commit()
    con.commit()
    return changed


@_serialized
def get_original(con: sqlite3.Connection, doc_id: int) -> bytes:
    """The archived original bytes of a document (what its hash names)."""
    row = con.execute("SELECT hash FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    return _read_archive(row["hash"])


@_serialized
def select_documents(
    con: sqlite3.Connection,
    *,
    pending: bool = False,
    text_source_prefix: str | None = None,
    title: str | None = None,
    mime_prefix: str | None = None,
    limit: int | None = None,
) -> list[int]:
    """Document ids for batch jobs, oldest first.

    ``pending`` selects never-indexed documents (``parsed_at IS NULL``);
    ``text_source_prefix`` selects indexed ones whose ``meta.text_source``
    starts with the prefix (``"zotero-ft-cache"``, ``"pymupdf/"``);
    ``title`` selects by a case-insensitive substring of the title. The
    three are OR-ed when several are given. ``mime_prefix`` narrows any.
    """
    clauses: list[str] = []
    args: list[Any] = []
    if pending:
        clauses.append("parsed_at IS NULL")
    if text_source_prefix is not None:
        clauses.append("json_extract(meta, '$.text_source') LIKE ? ESCAPE '!'")
        args.append(_like_prefix(text_source_prefix))
    if title:
        clauses.append("title LIKE ? ESCAPE '!'")
        args.append("%" + _like_prefix(title)[:-1] + "%")
    if not clauses:
        return []
    sql = (
        f"SELECT id FROM documents WHERE ({' OR '.join(clauses)})"
        " AND json_extract(meta, '$.retired') IS NULL"
    )
    if mime_prefix is not None:
        sql += " AND mime LIKE ? ESCAPE '!'"
        args.append(_like_prefix(mime_prefix))
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [r["id"] for r in con.execute(sql, args)]


@_serialized
def meta_index(con: sqlite3.Connection, json_path: str) -> dict[str, int]:
    """Map every value found at ``json_path`` in any document's meta to its id.

    Array values are expanded, so ``"$.zotero.keys"`` yields one entry per
    key. Lets importers decide what is already imported without re-reading
    or re-hashing source files.
    """
    rows = con.execute(
        "SELECT d.id AS id, j.value AS value"
        " FROM documents d, json_each(json_extract(d.meta, ?)) j"
        " WHERE json_extract(d.meta, ?) IS NOT NULL",
        (json_path, json_path),
    ).fetchall()
    return {str(r["value"]): r["id"] for r in rows}


@_serialized
def ingest_file(
    con: sqlite3.Connection,
    data: bytes,
    *,
    mime: str,
    title: str | None = None,
    source_url: str | None = None,
    meta: dict[str, Any] | None = None,
    original_path: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    """Register a file; index it too when its text is known.

    ``text/*`` files are their own text. Anything else is left for the parse
    queue unless ``text`` is supplied by the caller (a parser).
    """
    result = register(
        con,
        data,
        mime=mime,
        title=title,
        source_url=source_url,
        meta=meta,
        original_path=original_path,
    )
    if text is None and mime.startswith("text/"):
        text = data.decode("utf-8", errors="replace")
    doc_id = result["doc_id"]
    if text is not None and (result["created"] or not _is_indexed(con, doc_id)):
        index_text(con, doc_id, text)
    return result


@_serialized
def ingest_text(
    con: sqlite3.Connection,
    text: str,
    *,
    title: str | None = None,
    source_url: str | None = None,
    mime: str = "text/plain",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest raw text: register its bytes and index it in one step."""
    return ingest_file(
        con,
        text.encode("utf-8"),
        mime=mime,
        title=title,
        source_url=source_url,
        meta=meta,
        text=text,
    )


@_serialized
def get_document(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    offset: int = 0,
    max_chars: int | None = None,
) -> dict[str, Any] | None:
    """One document with its text read from the parsed-text artifact.

    ``text`` is the window ``[offset, offset + max_chars)``; ``text_len`` and
    ``truncated`` tell the caller whether more remains.
    """
    doc = con.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        return None
    out = dict(doc)
    out["meta"] = json.loads(out["meta"]) if out["meta"] else {}
    full = _read_archive(doc["text_hash"]).decode("utf-8") if doc["text_hash"] else ""
    offset = max(0, offset)
    end = len(full) if max_chars is None else min(len(full), offset + max(0, max_chars))
    out.update(
        text=full[offset:end],
        text_len=len(full),
        offset=offset,
        truncated=end < len(full),
    )
    return out


def _chunk_shape(row: sqlite3.Row) -> dict[str, Any]:
    """The structural fields of a chunk row, decoded (None for legacy rows)."""
    loc = json.loads(row["locator"]) if row["locator"] else {}
    return {
        "kind": row["kind"],
        "heading": json.loads(row["heading"]) if row["heading"] else [],
        "page": loc.get("page"),
    }


@_serialized
def list_documents(
    con: sqlite3.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    title: str | None = None,
    source: str | None = None,
    mime_prefix: str | None = None,
    retired: bool = False,
    domain: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Documents without their text, newest first, for browsing.

    ``title`` is a case-insensitive substring; ``source`` matches
    ``meta.source``; ``mime_prefix`` a MIME type prefix; ``retired`` lists
    the retired documents instead of the live ones; ``domain`` keeps the
    documents of one ontology module (a document without a domain set is
    in every module and stays, as in search); ``tag`` keeps the documents
    carrying that tag (``project:synth``). Returns ``{"total", "items"}``
    where each item carries the row, its decoded ``meta`` and its chunk
    count.
    """
    clauses: list[str] = [
        "json_extract(d.meta, '$.retired') IS " + ("NOT NULL" if retired else "NULL")
    ]
    args: list[Any] = []
    if domain:
        clauses.append(
            "(json_extract(d.meta, '$.domains') IS NULL OR EXISTS"
            " (SELECT 1 FROM json_each(d.meta, '$.domains') WHERE value = ?))"
        )
        args.append(domain)
    if tag:
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(d.meta, '$.tags') WHERE value = ?)"
        )
        args.append(tag)
    if title:
        clauses.append("lower(d.title) LIKE ? ESCAPE '!'")
        args.append("%" + _like_prefix(title.lower())[:-1] + "%")
    if source:
        clauses.append("json_extract(d.meta, '$.source') = ?")
        args.append(source)
    if mime_prefix:
        clauses.append("d.mime LIKE ? ESCAPE '!'")
        args.append(_like_prefix(mime_prefix))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = con.execute(f"SELECT count(*) FROM documents d {where}", args).fetchone()[0]
    rows = con.execute(
        f"""
        SELECT d.id, d.title, d.mime, d.source_url, d.added_at, d.parsed_at, d.meta,
               (SELECT count(*) FROM chunks c WHERE c.doc_id = d.id) AS n_chunks
        FROM documents d {where}
        ORDER BY d.added_at DESC, d.id DESC LIMIT ? OFFSET ?
        """,
        (*args, max(1, min(limit, 500)), max(0, offset)),
    ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["meta"] = json.loads(item["meta"]) if item["meta"] else {}
        items.append(item)
    return {"total": total, "items": items}


@_serialized
def list_chunks(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    """A document as its chunks in order, with text and structure (the
    document view renders from this)."""
    rows = con.execute(
        "SELECT id AS chunk_id, doc_id, seq, text, kind, locator, heading, data"
        " FROM chunks WHERE doc_id = ? ORDER BY seq",
        (doc_id,),
    ).fetchall()
    out = []
    for r in rows:
        c = {k: r[k] for k in ("chunk_id", "doc_id", "seq", "text")}
        c.update(_chunk_shape(r))
        c["locator"] = json.loads(r["locator"]) if r["locator"] else None
        c["data"] = json.loads(r["data"]) if r["data"] else None
        out.append(c)
    return out


@_serialized
def read_chunks(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    after_seq: int = -1,
    max_chars: int = 1500,
    skip: set[int] | None = None,
) -> list[dict[str, Any]]:
    """The chunks that follow a point in a document, in order, as many as
    fit ``max_chars`` (the first always): what "read on" means for a
    passage, and the start of a document for ``after_seq=-1``. A figure a
    model has read comes along, its description being its text; an unread
    one does not (``READABLE``). ``skip`` leaves out chunk ids the caller
    has read already, so a reader that comes back to a document keeps
    making progress instead of meeting what it has seen. Each chunk
    carries ``chunk_id``, ``seq``, ``text``, ``kind``, ``heading`` and
    ``page``."""
    rows = con.execute(
        "SELECT id AS chunk_id, doc_id, seq, text, kind, locator, heading, data"
        f" FROM chunks WHERE doc_id = ? AND seq > ? AND {READABLE} ORDER BY seq",
        (doc_id, after_seq),
    )
    out: list[dict[str, Any]] = []
    used = 0
    for r in rows:
        if skip and r["chunk_id"] in skip:
            continue
        if out and used + len(r["text"]) > max_chars:
            break
        c = {k: r[k] for k in ("chunk_id", "doc_id", "seq", "text")}
        c.update(_chunk_shape(r))
        out.append(c)
        used += len(r["text"])
    return out


# A figure a model has read carries its description in its own text, so
# it reads like any other chunk; one nobody has read is an image line and
# a caption, which is noise in a reading and a useless snippet in a hit.
READABLE = (
    "(kind != 'figure' OR json_array_length(json_extract(data, '$.readings')) > 0)"
)


@_serialized
def find_chunk(
    con: sqlite3.Connection,
    doc_id: int,
    words: str,
) -> dict[str, Any] | None:
    """The chunk of one document that holds most of the words, a longer
    word counting for more ("what", "is" and "a" carry no question); the
    document's first chunk when none of them occurs in it, None when it
    has no chunks. This is how a hit that matched on the document field
    is opened somewhere, and how a reading lands on the part of a long
    document that was asked for, without a MATCH over the whole index
    filtered to one document (seconds per hit for a question full of
    common words). An unread figure is never the answer (``READABLE``).
    """
    rows = con.execute(
        "SELECT id AS chunk_id, doc_id, seq, text, kind, locator, heading, data"
        f" FROM chunks WHERE doc_id = ? AND {READABLE} ORDER BY seq",
        (doc_id,),
    ).fetchall()
    if not rows:
        return None
    terms = {t.lower() for t in _TOKEN.findall(words)}
    best, score = rows[0], 0
    for r in rows:
        found = terms & set(_TOKEN.findall(r["text"].lower()))
        weight = sum(len(t) for t in found)
        if weight > score:
            best, score = r, weight
    out = {k: best[k] for k in ("chunk_id", "doc_id", "seq", "text")}
    out.update(_chunk_shape(best))
    return out


@_serialized
def document_outline(
    con: sqlite3.Connection, doc_id: int, *, limit: int = 20
) -> list[str]:
    """The document's sections in order, as heading paths (" › " joined),
    at most ``limit``: what to name when a reading of it found nothing."""
    rows = con.execute(
        "SELECT heading, MIN(seq) AS seq FROM chunks"
        " WHERE doc_id = ? AND heading IS NOT NULL AND heading != '[]'"
        " GROUP BY heading ORDER BY seq LIMIT ?",
        (doc_id, max(1, limit)),
    ).fetchall()
    out = []
    for r in rows:
        path = json.loads(r["heading"])
        if path:
            out.append(" › ".join(path))
    return out


@_serialized
def document_titles(con: sqlite3.Connection, doc_ids: list[int]) -> dict[int, str]:
    """Titles by id, for naming documents in a result (unknown ids left
    out; an untitled document is an empty string)."""
    ids = list(dict.fromkeys(int(i) for i in doc_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT id, title FROM documents WHERE id IN ({marks})", ids
    ).fetchall()
    return {r["id"]: r["title"] or "" for r in rows}


@_serialized
def original_info(con: sqlite3.Connection, doc_id: int) -> dict[str, Any] | None:
    """MIME type, title and archive path of a document's original, for
    serving it; None when the document does not exist."""
    row = con.execute(
        "SELECT hash, mime, title, original_path FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "path": _archive_path(row["hash"]),
        "mime": row["mime"] or "application/octet-stream",
        "title": row["title"],
        "original_path": row["original_path"],
    }


@_serialized
def get_chunk(con: sqlite3.Connection, chunk_id: int) -> dict[str, Any] | None:
    """One chunk in full: text, kind, heading, locator and table ``data``."""
    r = con.execute(
        "SELECT id AS chunk_id, doc_id, seq, text, kind, locator, heading, data"
        " FROM chunks WHERE id = ?",
        (chunk_id,),
    ).fetchone()
    if r is None:
        return None
    out = {k: r[k] for k in ("chunk_id", "doc_id", "seq", "text")}
    out.update(_chunk_shape(r))
    out["locator"] = json.loads(r["locator"]) if r["locator"] else None
    out["data"] = json.loads(r["data"]) if r["data"] else None
    return out
