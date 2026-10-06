"""A document's meta and its field, what a search reads a document as
(title, kind, summary, what its chapters are about), and the writes that
change them."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from prax.graph import ontology
from prax.text import dates, language, mimes

from ..base import (
    ASIDE_KINDS,
    _guards,
    _reading,
    _serialized,
    now,
)

# the last attempts of one extractor a parse history always keeps: what
# parsers.queue.seen counts refusals in a row over
HISTORY_TAIL = 5
# the entries a history keeps besides those its readers need (the user,
# 2026-09-30); every write of a document's meta holds it to that
HISTORY_KEEP = 20


def _history_key(key: str, entry: dict[str, Any]) -> tuple[Any, ...]:
    """What makes two entries of a history the same fact for its readers:
    an extractor and what came of it (``parsers.queue.seen``), a producer
    and the ontology it read under (``extracted_by``)."""
    if key == "parse_history":
        return (entry.get("extractor"), entry.get("outcome"), entry.get("error"))
    return (entry.get("extractor"), entry.get("ontology_version"))


def bounded_histories(meta: dict[str, Any], keep: int) -> dict[str, int]:
    """Drop from ``meta.parse_history`` and ``meta.extraction_history`` the
    entries no reader needs, in place, and say how many went of each.

    Kept: the last ``keep`` entries, the newest entry of each kind
    (``_history_key``), every entry that holds a text's hash (the only
    record of where an earlier text is in the archive), and the last
    ``HISTORY_TAIL`` attempts of each extractor. What goes are the repeats
    a loop writes: a book refused 902 times holds one refusal after it."""
    dropped: dict[str, int] = {}
    for key in ("parse_history", "extraction_history"):
        history = meta.get(key)
        if not isinstance(history, list) or len(history) <= keep:
            continue
        wanted = set(range(max(0, len(history) - keep), len(history)))
        newest: dict[tuple[Any, ...], int] = {}
        tails: dict[Any, list[int]] = {}
        for i, entry in enumerate(history):
            if not isinstance(entry, dict):
                wanted.add(i)
                continue
            if entry.get("text_hash"):
                wanted.add(i)
            newest[_history_key(key, entry)] = i
            if key == "parse_history":
                tails.setdefault(entry.get("extractor"), []).append(i)
        wanted |= set(newest.values())
        for positions in tails.values():
            wanted |= set(positions[-HISTORY_TAIL:])
        if len(wanted) < len(history):
            meta[key] = [e for i, e in enumerate(history) if i in wanted]
            dropped[key] = len(history) - len(wanted)
    return dropped


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


def _put_meta(con: sqlite3.Connection, doc_id: int, meta: dict[str, Any]) -> None:
    """Write a document's meta back, inside the caller's transaction: the
    read-modify-write of every store function that changes a key of it.
    It refreshes nothing; a caller that changes what the document field
    indexes (title, kind, summary) refreshes it itself."""
    bounded_histories(meta, HISTORY_KEEP)
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )


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
    bounded_histories(meta, HISTORY_KEEP)
    cur = con.execute(
        "UPDATE documents SET meta = ?, title = COALESCE(?, title),"
        " source_url = COALESCE(?, source_url) WHERE id = ?",
        (json.dumps(meta), title, source_url, doc_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no such document: {doc_id}")
    _refresh_document_field(con, doc_id)
    con.commit()


# what says when a document was published, the most trusted first
# (``prax.text.dates``): a person; the record it came with (Zotero's date,
# ``meta.date``); what the extension found on a paper's page
# (``meta.paper.date``); a page's citation tags; its schema.org markup; the
# arXiv id; a page's generic article and Dublin Core tags
PUBLISHED_BY = (
    "human",
    "record",
    "paper",
    "citation",
    "jsonld",
    "arxiv",
    "generic",
    "first-page",  # read from the first page by a model (the ``dates`` step)
)


def published_of(
    meta: dict[str, Any], page: dict[str, tuple[str, str]] | None = None
) -> dict[str, str] | None:
    """When the document says it was published, from the most trusted
    source that says it: ``{date, precision, by}``, or None. ``page`` is
    what its HTML says (``dates.from_html``). A person's date is never
    replaced: None then, as for a document that says nothing."""
    held = meta.get("published") or {}
    if held.get("by") == "human":
        return None
    page = page or {}
    found = {
        "record": dates.parse(meta.get("date")),
        "paper": dates.parse((meta.get("paper") or {}).get("date")),
        "citation": page.get("citation"),
        "jsonld": page.get("jsonld"),
        "arxiv": dates.from_arxiv(meta.get("arxiv")),
        "generic": page.get("generic"),
    }
    for by in PUBLISHED_BY[1:]:
        got = found.get(by)  # the model's date comes by its own step, not here
        if got:
            return {"date": got[0], "precision": got[1], "by": by}
    return None


@_serialized
def set_published(
    con: sqlite3.Connection,
    doc_id: int,
    date: str,
    *,
    by: str = "human",
    words: str | None = None,
    confidence: str | None = None,
    run: str | None = None,
) -> dict[str, str]:
    """A date a person or a source gives a document (``meta.published``).
    The date is read as ``dates.parse`` reads it; one that is not a date
    is refused (ValueError). ``words`` are the text that states it, and
    ``confidence`` how plainly (the ``dates`` step's). A source never
    replaces a more trusted one (``PUBLISHED_BY``); a person replaces any."""
    got = dates.parse(date)
    if got is None:
        raise ValueError(f"not a date: {date!r}")
    meta = get_meta(con, doc_id)
    held = meta.get("published") or {}
    rank = PUBLISHED_BY.index(by) if by in PUBLISHED_BY else len(PUBLISHED_BY)
    if held and by != "human":
        old = held.get("by")
        if old in PUBLISHED_BY and PUBLISHED_BY.index(old) < rank:
            return dict(held)  # a more trusted source said it already
    entry: dict[str, str] = {"date": got[0], "precision": got[1], "by": by, "at": now()}
    for key, value in (("words", words), ("confidence", confidence), ("run", run)):
        if value:
            entry[key] = str(value)
    meta["published"] = entry
    meta.pop("published_tried", None)
    _put_meta(con, doc_id, meta)
    con.commit()
    return dict(entry)


@_serialized
def published_tried(con: sqlite3.Connection, doc_id: int, run: str) -> None:
    """The ``dates`` step read the first page and found no date it could
    stand behind: not asked again by that step (a person still may set one)."""
    meta = get_meta(con, doc_id)
    meta["published_tried"] = {"run": run, "at": now()}
    _put_meta(con, doc_id, meta)
    con.commit()


@_reading
def dates_needed(con: sqlite3.Connection, limit: int = 200) -> list[int]:
    """Documents with a text that say nothing of when they were published,
    and that the ``dates`` step has not tried: newest first, so what just
    arrived is dated before the backlog."""
    return [
        r[0]
        for r in con.execute(
            "SELECT id FROM documents WHERE text_hash IS NOT NULL"
            " AND json_extract(meta, '$.published') IS NULL"
            " AND json_extract(meta, '$.published_tried') IS NULL"
            " AND json_extract(meta, '$.retired') IS NULL"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    ]


def keep_summary(
    meta: dict[str, Any], text: str, *, lang: str | None = None
) -> str | None:
    """File a summary in a document's ``meta`` and say what language it
    was filed under.

    ``meta.summary`` is the one the document field indexes and is English
    wherever an English one exists; ``meta.summaries`` holds every one we
    have, keyed by language, so the German summary of a German document
    is never lost to the translation that replaced it. A document whose
    only summary is German keeps it as the canonical one: worse for a
    search than English, better than no summary at all.

    A summary too short to place is filed without a language rather than
    as English, and ``meta.summary_lang`` stays absent: the pass that
    hands documents to a model asks for the ones known to be in another
    language, never for the ones nothing could read.
    """
    code = lang or language.detect(text)
    held = meta.setdefault("summaries", {})
    if isinstance(held, dict):
        # the one already there goes in first, under its own language. It
        # got here before ``meta.summaries`` existed, so nothing else
        # would file it, and the first batch of translations overwrote
        # eight German summaries that this line would have kept
        # (2026-09-24)
        there, there_lang = meta.get("summary"), meta.get("summary_lang")
        if there and there_lang and there_lang not in held:
            held[str(there_lang)] = there
        if code:
            held[code] = text
    if code == language.canonical() or not meta.get("summary"):
        meta["summary"] = text
        if code:
            meta["summary_lang"] = code
        else:
            meta.pop("summary_lang", None)
    return code


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
    English wherever an English one exists (``keep_summary``). The field
    is refreshed, so the new summary is searchable and the document vector
    is embedded again.
    """
    text = text.strip()
    if not text:
        raise ValueError("a summary cannot be empty")
    row = con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    meta = json.loads(row["meta"] or "{}")
    was = str(meta.get("summary") or "")
    code = keep_summary(meta, text, lang=lang)
    meta.pop("summary_tried", None)  # it worked this time
    meta["summary_source"] = source
    meta["summary_run"] = run
    _put_meta(con, doc_id, meta)
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
# what a document is, in the words a query would use (stage Z): the
# genre labels a person or the labeller gave, each as sure as GENRE_FIELD_P.
# The levels ("informational") say too little to help a search
GENRE_FIELD_P = 0.5
_GENRE_WORDS = {"qa": "question and answer", "source": "source code"}


def _genre_words(meta: dict[str, Any]) -> list[str]:
    levels = {lv.name for lv in ontology.genres().levels}
    out = []
    for g in meta.get("genres") or []:
        label = str((g or {}).get("genre") or "")
        if not label or label in levels:
            continue
        if float((g or {}).get("p", 1.0)) < GENRE_FIELD_P:
            continue
        out.append(_GENRE_WORDS.get(label, label))
    return out


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
    words += [w for w in _genre_words(meta) if w not in words]
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


# documents a hold of the write lock rebuilds; the lock is let go between
# batches. In one hold, the whole library's fields kept every other write
# of the door waiting for minutes, nightly (an agent's append, 120 s)
FIELD_BATCH = 200


def refresh_document_fields(
    con: sqlite3.Connection, doc_ids: list[int] | None = None
) -> int:
    """Rebuild the field of the given documents (all when None); returns
    how many changed. The backfill after the migration, and the repair
    after a change to ``document_field``. ``FIELD_BATCH`` documents at a
    time under the write lock, so the door's other writes go between."""
    ids = doc_ids or _document_ids(con)
    changed = 0
    for start in range(0, len(ids), FIELD_BATCH):
        changed += _refresh_fields_batch(con, ids[start : start + FIELD_BATCH])
    return changed


@_reading
def _document_ids(con: sqlite3.Connection) -> list[int]:
    return [int(r[0]) for r in con.execute("SELECT id FROM documents ORDER BY id")]


@_serialized
def _refresh_fields_batch(con: sqlite3.Connection, ids: list[int]) -> int:
    changed = sum(1 for doc_id in ids if _refresh_document_field(con, doc_id))
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
    _put_meta(con, doc_id, meta)
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
