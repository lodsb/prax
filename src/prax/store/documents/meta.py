"""A document's meta and its field, what a search reads a document as
(title, kind, summary, what its chapters are about), and the writes that
change them."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from prax.text import mimes

from ..base import (
    ASIDE_KINDS,
    _guards,
    _reading,
    _serialized,
    now,
)


def _is_indexed(con: sqlite3.Connection, doc_id: int) -> bool:
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    return bool(row and row["text_hash"])


@_reading
def is_indexed(con: sqlite3.Connection, doc_id: int) -> bool:
    """True once ``index_text`` has stored a text artifact for the document."""
    return _is_indexed(con, doc_id)


@_reading
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
def set_summary(
    con: sqlite3.Connection,
    doc_id: int,
    text: str,
    *,
    lang: str | None = None,
    source: str,
    run: str | None = None,
) -> dict[str, Any]:
    """Put a summary on a document, keeping the ones already there.

    ``meta.summaries`` holds every summary we have keyed by language, so
    translating a German summary into English never loses the German one;
    ``meta.summary`` is the one the document field indexes, which is
    English wherever an English one exists (``prax.writing.summaries``). The field
    is refreshed, so the new summary is searchable and the document vector
    is embedded again.
    """
    from prax.writing import summaries

    text = text.strip()
    if not text:
        raise ValueError("a summary cannot be empty")
    row = con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    meta = json.loads(row["meta"] or "{}")
    was = str(meta.get("summary") or "")
    code = summaries.keep(meta, text, lang=lang)
    meta.pop("summary_tried", None)  # it worked this time
    meta["summary_source"] = source
    meta["summary_run"] = run
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    changed = _refresh_document_field(con, doc_id)
    con.commit()
    return {
        "doc_id": doc_id,
        "lang": code,
        "canonical": meta.get("summary") == text,
        "replaced": was,
        "field": changed,
    }


def summary_tried(
    con: sqlite3.Connection, doc_id: int, run: str | None, why: str
) -> None:
    """Note that a summary could not be translated, so the next pass does
    not hand out the same document to the same model for ever. A pass that
    wants them again clears ``meta.summary_tried``."""
    meta = get_meta(con, doc_id)
    meta["summary_tried"] = {"run": run, "why": why[:200], "at": now()}
    set_meta(con, doc_id, meta)


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
            "until": now(),
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


# What a document *is*, in a few lines, indexed apart from its chunks
# (migration 0005): title, kind words, creators, venue, the extraction
# summary, an image description's opening paragraph. A short field makes a
# match in it strong under BM25 and gives one vector per document, so a
# query naming a thing finds the document that is that thing, not the
# documents that mention it most.

DOCTYPES: dict[str, str] = {
    "pdf": "d.mime = 'application/pdf'",
    "web": "d.mime IN ('text/html', 'application/xhtml+xml')"
    " AND json_extract(d.meta, '$.video') IS NULL",
    "video": "json_extract(d.meta, '$.video') IS NOT NULL",
    "image": mimes.picture_sql("d.mime"),
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


@_guards("doc", lambda: None)
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
    if mimes.is_picture(mime):
        words.append("image")
    elif mime in mimes.DOCUMENT_IMAGES:
        words.append("scanned document")
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
    parts.extend(_section_lines(meta))
    return "\n".join(p for p in parts if p.strip())


SECTION_FIELD_CHARS = 3_000  # of section summaries in one document's field


def _section_lines(meta: dict[str, Any]) -> list[str]:
    """What a long document's parts are about, for its field.

    Capped: a book of ninety chapters would otherwise put thirty thousand
    characters in one FTS row, where BM25's length normalization buries
    it and one document vector over that much text means nothing. The
    longest sections first, which is the order they are stored in.
    """
    held = meta.get("sections") or {}
    items = held.get("items") if isinstance(held, dict) else None
    if not items:
        return []
    out: list[str] = []
    budget = SECTION_FIELD_CHARS
    for item in items:
        line = " ".join(
            str(x).strip()
            for x in (item.get("heading"), item.get("summary"))
            if str(x or "").strip()
        )
        if not line:
            continue
        if len(line) > budget:
            break
        out.append(line)
        budget -= len(line)
    return out


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


@_reading
def document_sections(
    con: sqlite3.Connection, doc_id: int, *, min_chars: int | None = None
) -> list[dict[str, Any]]:
    """The document's top-level sections with their text, longest first.

    A chapter in a book, a major section in a manual. Only where there is
    enough of it to be worth a sentence: a document whose headings carry
    a paragraph each is a paper, and its own summary already covers it
    (``prax.writing.sections``).
    """
    from prax.writing import sections as sec

    floor = sec.MIN_SECTION if min_chars is None else min_chars
    rows = con.execute(
        "SELECT json_extract(heading, '$[0]') AS top, seq, text FROM chunks"
        " WHERE doc_id = ? AND heading IS NOT NULL AND kind NOT IN"
        f" ({','.join('?' * len(ASIDE_KINDS))})"
        " ORDER BY seq",
        (doc_id, *ASIDE_KINDS),
    ).fetchall()
    parts: dict[str, list[str]] = {}
    order: list[str] = []
    for r in rows:
        top = r["top"]
        if not top:
            continue
        if top not in parts:
            parts[top] = []
            order.append(top)
        parts[top].append(r["text"])
    joiner = "\n\n"
    out = [
        {"heading": sec.clean_heading(h), "text": joiner.join(parts[h])}
        for h in order
        if sum(len(t) for t in parts[h]) >= floor
    ]
    out.sort(key=lambda s: -len(s["text"]))
    return out[: sec.MAX_SECTIONS]


@_serialized
def set_sections(
    con: sqlite3.Connection,
    doc_id: int,
    sections: list[dict[str, Any]],
    *,
    source: str,
    run: str | None = None,
) -> dict[str, Any]:
    """What a document's parts are about, kept in ``meta.sections`` with
    the text artifact they were read from — so a re-parse makes them
    stale rather than wrong. The document field is refreshed, which is
    how they reach a search."""
    row = con.execute(
        "SELECT meta, text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    meta = json.loads(row["meta"] or "{}")
    meta["sections"] = {
        "text_hash": row["text_hash"],
        "source": source,
        "run": run,
        "at": now(),
        "items": sections,
    }
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    changed = _refresh_document_field(con, doc_id)
    con.commit()
    return {"doc_id": doc_id, "sections": len(sections), "field": changed}


@_reading
def sections_needed(
    con: sqlite3.Connection, *, limit: int = 200, untried_only: bool = True
) -> list[int]:
    """Long documents whose sections nobody has read, or has read from a
    text that has since been replaced. Longest first: a book gains most."""
    from prax.writing import sections as sec

    rows = con.execute(
        "SELECT id FROM documents WHERE text_len >= ?"
        " AND json_extract(meta, '$.retired') IS NULL AND text_hash IS NOT NULL"
        " AND (json_extract(meta, '$.sections.text_hash') IS NULL"
        "      OR json_extract(meta, '$.sections.text_hash') != text_hash)"
        + (
            " AND json_extract(meta, '$.sections_tried') IS NULL"
            if untried_only
            else ""
        )
        + " ORDER BY text_len DESC LIMIT ?",
        (sec.MIN_DOCUMENT, max(1, limit)),
    ).fetchall()
    return [int(r["id"]) for r in rows]


@_serialized
def set_mime(con: sqlite3.Connection, doc_id: int, mime: str) -> None:
    """Correct a document's type (one registered as unknown bytes whose
    name says what it is); the parse queue then offers it to that type's
    parser."""
    cur = con.execute("UPDATE documents SET mime = ? WHERE id = ?", (mime, doc_id))
    if cur.rowcount == 0:
        raise KeyError(f"no such document: {doc_id}")
    con.commit()
