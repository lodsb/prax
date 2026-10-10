"""Reading documents and chunks back: one document, a list, a chunk and its
neighbours, the equations near a place, an outline."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import quote_plus

from prax import packs
from prax.graph import ontology
from prax.text import dates

from ..base import (
    _TOKEN,
    UNASSIGNED,
    Document,
    SearchHit,
    _guards,
    _like_prefix,
    _read_archive,
    _reading,
    domain_clause,
    hidden_documents,
)


@_reading
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


@_reading
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


EARLIER_TEXTS = 10  # earlier texts a replacing read looks back over


@_guards("doc", list)
@_reading
def earlier_texts(
    con: sqlite3.Connection, doc_id: int, *, limit: int = EARLIER_TEXTS
) -> list[str]:
    """The document's earlier texts, newest first, as ``meta.parse_history``
    names them by hash (every text stays in the archive), the current one
    left out: what a replacing read looks back over for the figure
    readings the text it replaces had already lost
    (``figures.carry_readings``). A hash the archive no longer holds is
    skipped."""
    row = con.execute(
        "SELECT text_hash, meta FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        return []
    current = row["text_hash"]
    seen = {current}
    out: list[str] = []
    history = json.loads(row["meta"] or "{}").get("parse_history") or []
    for entry in reversed(history):
        digest = entry.get("text_hash") if isinstance(entry, dict) else None
        if not digest or digest in seen:
            continue
        seen.add(digest)
        try:
            out.append(_read_archive(str(digest)).decode("utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if len(out) >= limit:
            break
    return out


@_guards("doc", lambda: None)
@_reading
def get_document(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    offset: int = 0,
    max_chars: int | None = None,
) -> Document | None:
    """One document with its text read from the parsed-text artifact
    (``Document``).

    ``text`` is the window ``[offset, offset + max_chars)``; ``text_len`` and
    ``truncated`` tell the caller whether more remains. With ``max_chars=0``
    the artifact is not read when the row knows its length (every text
    indexed since migration 15; the ``lengths`` maintain pass fills the
    rest): the row alone, for the callers that want the row.
    """
    doc = con.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        return None
    row = dict(doc)
    row["meta"] = json.loads(row["meta"]) if row["meta"] else {}
    offset = max(0, offset)
    known = doc["text_len"] if doc["text_hash"] else 0
    if max_chars == 0 and known is not None:
        row.update(
            text="", text_len=int(known), offset=offset, truncated=offset < int(known)
        )
        return cast(Document, row)
    full = _read_archive(doc["text_hash"]).decode("utf-8") if doc["text_hash"] else ""
    end = len(full) if max_chars is None else min(len(full), offset + max(0, max_chars))
    row.update(
        text=full[offset:end],
        text_len=len(full),
        offset=offset,
        truncated=end < len(full),
    )
    # the row's columns as the table has them; a test holds the two together
    return cast(Document, row)


# What an agent reads of a document's meta (``GET /get?brief=true``, the
# MCP tool): what it is and where it belongs. The histories of its parses,
# titles and extractions, its summaries in every language and the Zotero
# record are the UI's; they were three kilobytes before the text.
BRIEF_META = (
    "source",
    "published",
    "domains",
    "tags",
    "collections",
    "lang",
    "summary",
    "pages",
    "doi",
    "arxiv",
    "creators",
    "paper",
    "origin",
    "video",
    "retired",
    # its lifecycle beside `published` and `retired`: what it says of itself
    # (a project note's status line, `prax.text.status`)
    "status",
)
BRIEF_ROW_DROP = ("hash", "text_hash", "parsed_at")
# what a search hit carries for the UI's "why this hit" line, and an
# agent never reads (``GET /search?brief=true``)
HIT_RANKS = ("score", "fts_rank", "vec_rank", "field_rank", "dvec_rank")


def brief_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    """A ``get_document`` result as an agent reads it: the meta cut to
    ``BRIEF_META``, the capture to who and when, the Zotero record to its
    keys, and the row without the hashes."""
    out = {k: v for k, v in doc.items() if k not in BRIEF_ROW_DROP}
    meta = doc.get("meta") or {}
    brief = {k: meta[k] for k in BRIEF_META if meta.get(k) not in (None, [], {}, "")}
    cap = meta.get("capture") or {}
    if cap:
        brief["capture"] = {k: cap[k] for k in ("at", "by") if cap.get(k)}
    keys = (meta.get("zotero") or {}).get("keys")
    if keys:
        brief["zotero"] = keys
    out["meta"] = brief
    return out


def brief_hit(hit: SearchHit) -> dict[str, Any]:
    """A search hit without its ranks (``HIT_RANKS`` and any other
    ``*_rank`` a rank list of its own adds) and empty fields."""
    return {
        k: v
        for k, v in hit.items()
        if k not in HIT_RANKS and not k.endswith("_rank") and v is not None
    }


ANNOTATORS = ("figures", "figure-refs", "formulas")  # readers that add to the text


HEADING_PART_CHARS = 120  # one level of a heading path, at most
HEADING_PARTS = 4  # levels of it, the nearest kept


def short_heading(path: list[str]) -> list[str]:
    """A heading path as a hit or a passage carries it: each level cut at a
    word to ``HEADING_PART_CHARS`` and marked "…", and only the
    ``HEADING_PARTS`` nearest levels. A parse that read a page's columns
    or a book's epigraph as a heading wrote 1,000 characters and more into
    1,044 chunks (2026-10-04), and every hit of those documents carried
    them (invariant 6). The chunk's text is not touched."""
    out = []
    for part in path[-HEADING_PARTS:]:
        part = str(part)
        if len(part) > HEADING_PART_CHARS:
            cut = part[:HEADING_PART_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:–-")
            part = (cut or part[:HEADING_PART_CHARS]) + "…"
        out.append(part)
    if len(path) > HEADING_PARTS:
        out[0] = "… " + out[0]
    return out


def _chunk_shape(row: sqlite3.Row) -> dict[str, Any]:
    """The structural fields of a chunk row, decoded (None for legacy
    rows), and for a figure chunk the ``figure`` reference, so a hit or a
    passage can show the image (``GET /doc/{id}/figure/{ref}``) beside
    its reading."""
    loc = json.loads(row["locator"]) if row["locator"] else {}
    out = {
        "kind": row["kind"],
        "heading": short_heading(json.loads(row["heading"])) if row["heading"] else [],
        "page": loc.get("page"),
        "time": loc.get("time"),  # seconds into a recording, a transcript's passage
        "figure": None,
    }
    if row["kind"] == "figure" and "data" in row.keys() and row["data"]:  # noqa: SIM118 - a Row iterates values, not keys
        with contextlib.suppress(ValueError, AttributeError):
            out["figure"] = json.loads(row["data"]).get("ref")
    return out


# a date, or a moment in the shape ``store.now()`` writes (CLAUDE.md)
_MOMENT = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?Z?)?")


@_reading
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
    genre: str | None = None,
    subject: str | None = None,
    since: str | None = None,
    published_since: str | None = None,
    published_before: str | None = None,
) -> dict[str, Any]:
    """Documents without their text, newest first, for browsing.

    ``title`` is a case-insensitive substring; ``source`` matches
    ``meta.source``; ``mime_prefix`` a MIME type prefix; ``retired`` lists
    the retired documents instead of the live ones; ``domain`` keeps the
    documents of one ontology module and of the modules built on it (a
    kitchen document is a craft document too; one without a domain set is
    in every module and stays, as in search), or with ``unassigned`` the
    documents without a domain set alone; ``tag`` keeps the documents
    carrying that tag (``project:synth``); ``genre`` and ``subject`` the
    documents labelled so, by a person or a model (a level names every
    document labelled under it); ``since`` the documents added at that
    moment or later (``2026-10-03`` or ``2026-10-03T14:00:00Z``, UTC);
    ``published_since``/``published_before`` those published in a span
    (``meta.published``, compared as written), the undated left out.
    Returns ``{"total", "items"}``
    where each item carries the row, its decoded ``meta`` and its chunk
    count.
    """
    clauses: list[str] = [
        "json_extract(d.meta, '$.retired') IS " + ("NOT NULL" if retired else "NULL")
    ]
    args: list[Any] = []
    hidden = hidden_documents(con)
    if hidden:  # what the viewer may not see is not listed, nor counted
        clauses.append("d.id NOT IN (SELECT value FROM json_each(?))")
        args.append(json.dumps(sorted(hidden)))
    if domain:
        # a module holds its own, those of the modules built on it and the
        # documents no module was set for; "unassigned" is those alone

        names = () if domain == UNASSIGNED else ontology.current().within(domain)
        clause, more = domain_clause(con, names, unset=True)
        clauses.append(clause.removeprefix(" AND "))
        args.extend(more)
    if tag:
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(d.meta, '$.tags') WHERE value = ?)"
        )
        args.append(tag)
    for field, key, value in (
        ("genres", "genre", genre),
        ("subjects", "subject", subject),
    ):
        if value:
            clauses.append(
                f"EXISTS (SELECT 1 FROM json_each(d.meta, '$.{field}')"
                f" WHERE json_extract(value, '$.{key}') = ?)"
            )
            args.append(value)
    if title:
        clauses.append("lower(d.title) LIKE ? ESCAPE '!'")
        args.append("%" + _like_prefix(title.lower())[:-1] + "%")
    if source:
        clauses.append("json_extract(d.meta, '$.source') = ?")
        args.append(source)
    if mime_prefix:
        clauses.append("d.mime LIKE ? ESCAPE '!'")
        args.append(_like_prefix(mime_prefix))
    if since:
        if not _MOMENT.fullmatch(since):
            raise ValueError(f"since: a date or a UTC moment, not {since!r}")
        clauses.append("d.added_at >= ?")  # the stamps sort as moments
        args.append(since)
    for bound, op in ((published_since, ">="), (published_before, "<")):
        if bound:
            got = dates.parse(bound)
            if got is None:
                raise ValueError(f"published: a date or a year, not {bound!r}")
            clauses.append(f"json_extract(d.meta, '$.published.date') {op} ?")
            args.append(got[0])
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


@_guards("doc", lambda: None)
@_reading
def document_fingerprint(con: sqlite3.Connection, doc_id: int) -> str | None:
    """What a document's page shows, in a dozen characters: it moves when
    the document's row, its chunks, the facts read from it or its readings
    move, and only then. The UI asks it every few seconds while the
    document is open and draws the page again only when it moved; the
    store's change stamp moves with every write anywhere, and re-reading a
    whole document on each was most of what a live page cost (2026-10-10).
    Five lookups on indexes. None for a document that is not there."""
    row = con.execute(
        "SELECT title, text_hash, meta FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        return None
    parts = [
        tuple(row),
        con.execute(
            "SELECT count(*), max(id) FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchone(),
        con.execute(
            "SELECT count(*), max(id), max(valid_to) FROM edges WHERE source_doc = ?",
            (doc_id,),
        ).fetchone(),
        con.execute(
            "SELECT count(*), max(id), max(finished_at) FROM readings WHERE doc_id = ?",
            (doc_id,),
        ).fetchone(),
    ]
    blob = json.dumps([list(p) for p in parts], default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


@_guards("doc", list)
@_reading
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


@_guards("doc", list)
@_reading
def text_chunks(con: sqlite3.Connection, doc_id: int) -> list[str]:
    """The texts of a document's ``text`` chunks in order: its prose,
    without references, tables, figures, formulas or what is set aside."""
    return [
        r[0]
        for r in con.execute(
            "SELECT text FROM chunks WHERE doc_id = ? AND kind = 'text' ORDER BY seq",
            (doc_id,),
        )
    ]


@_guards("doc", list)
@_reading
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


@_guards("doc", lambda: None)
@_reading
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


NEARBY_BEFORE = 2  # equations named around a formula hit
NEARBY_AFTER = 3
HEAD_CHARS = 72


def _equation_head(data: str | None, text: str) -> str:
    """A few words naming an equation: its reading's first sentence, else
    its LaTeX cut short."""
    latex, reading = "", ""
    if data:
        try:
            d = json.loads(data)
            latex = str(d.get("latex") or "")
            readings = d.get("readings") or []
            reading = str(readings[0].get("text") or "") if readings else ""
        except (ValueError, AttributeError):
            pass
    head = reading.split(". ")[0] if reading else latex or text.split("\n")[0]
    head = " ".join(head.split())
    return head if len(head) <= HEAD_CHARS else head[: HEAD_CHARS - 1].rstrip() + "…"


@_reading
def chunks_to_mark(
    con: sqlite3.Connection,
    *,
    kind: str,
    key: str,
    field: str,
    version: int,
    limit: int = 50,
    skip: tuple[int, ...] = (),
) -> list[dict[str, Any]]:
    """Chunks of ``kind`` with a ``data.<field>`` that a pack has not
    marked at this ``version`` (``data.<key>.v``): ``{chunk_id, doc_id,
    value}``, oldest first. A re-chunk drops a mark, and the chunk comes
    back here (the maths pack's check of a formula's LaTeX)."""
    if key not in packs.chunk_mark_keys():
        raise ValueError(f"no pack marks chunks under {key!r}")
    # leased chunks left out; "NOT IN (NULL)" would leave out everything
    gaps = f" AND id NOT IN ({','.join('?' * len(skip))})" if skip else ""
    rows = con.execute(
        "SELECT id, doc_id, json_extract(data, ?) AS value FROM chunks"
        " WHERE kind = ? AND json_extract(data, ?) IS NOT NULL"
        " AND coalesce(json_extract(data, ?), 0) != ?"
        f"{gaps} ORDER BY id LIMIT ?",
        (f"$.{field}", kind, f"$.{field}", f"$.{key}.v", version, *skip, limit),
    ).fetchall()
    return [
        {"chunk_id": r["id"], "doc_id": r["doc_id"], "value": r["value"]} for r in rows
    ]


def _marks(data: str | None) -> dict[str, Any]:
    """What packs keep on a chunk (``packs.chunk_mark_keys``)."""
    if not data:
        return {}
    with contextlib.suppress(ValueError, AttributeError):
        got = json.loads(data)
        return {k: got[k] for k in packs.chunk_mark_keys() if k in got}
    return {}


@_guards("chunk", list)
def equations_near(
    con: sqlite3.Connection,
    chunk_id: int,
    *,
    before: int = NEARBY_BEFORE,
    after: int = NEARBY_AFTER,
) -> list[dict[str, Any]]:
    """The numbered equations around a chunk in its document — the
    neighbourhood a formula hit sits in, so a reader knows that the
    kernel is the next equation after the integral it holds. Each has
    its ``number`` (None when the paper gave it none), ``chunk_id``,
    ``seq``, a ``head`` naming it, and ``here`` for the chunk itself."""
    row = con.execute(
        "SELECT doc_id, seq FROM chunks WHERE id = ?", (chunk_id,)
    ).fetchone()
    if row is None:
        return []
    rows = con.execute(
        "SELECT id, seq, data, text FROM chunks WHERE doc_id = ? AND kind = 'formula'"
        " ORDER BY seq",
        (row["doc_id"],),
    ).fetchall()
    if len(rows) < 2:
        return []
    seqs = [r["seq"] for r in rows]
    # the chunk itself when it is an equation, else the equations around its place
    at = next((i for i, r in enumerate(rows) if r["id"] == chunk_id), None)
    if at is None:
        at = sum(1 for s_ in seqs if s_ < row["seq"])
        lo, hi = max(0, at - before), min(len(rows), at + after)
    else:
        lo, hi = max(0, at - before), min(len(rows), at + after + 1)
    out = []
    for r in rows[lo:hi]:
        number = None
        if r["data"]:
            with contextlib.suppress(ValueError, AttributeError):
                number = json.loads(r["data"]).get("number")
        out.append(
            {
                "chunk_id": r["id"],
                "seq": r["seq"],
                "number": number,
                "head": _equation_head(r["data"], r["text"]),
                "here": r["id"] == chunk_id,
                # what packs keep on it (the maths pack's check of its links)
                "marks": _marks(r["data"]),
            }
        )
    return out


@_guards("doc", lambda: None)
def formula_by_number(
    con: sqlite3.Connection, doc_id: int, number: str
) -> dict[str, Any] | None:
    """The formula chunk a paper calls ``(number)`` — how a reader opens
    "equation (2)" the way the prose refers to it."""
    rows = con.execute(
        "SELECT id AS chunk_id, doc_id, seq, text, kind, locator, heading, data"
        " FROM chunks WHERE doc_id = ? AND kind = 'formula' ORDER BY seq",
        (doc_id,),
    ).fetchall()
    want = number.strip("() ").lower()
    for r in rows:
        if not r["data"]:
            continue
        try:
            got = json.loads(r["data"]).get("number")
        except (ValueError, AttributeError):
            continue
        if got is not None and str(got).lower() == want:
            out = {k: r[k] for k in ("chunk_id", "doc_id", "seq", "text")}
            out.update(_chunk_shape(r))
            return out
    return None


@_guards("doc", list)
@_reading
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


def document_titles(con: sqlite3.Connection, doc_ids: list[int]) -> dict[int, str]:
    hidden = hidden_documents(con)
    doc_ids = [d for d in doc_ids if d not in hidden]
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


@_guards("chunk", lambda: None)
@_reading
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


# ------------------------------------------------ the questions other modules ask
#
# Each of these was a query in the module that needed it (the pipeline,
# the inbox, the importers, the routes panel): 26 on 2026-09-22 and 38 three
# days later, growing because a query written where it is needed is the
# easy way. A store read per question is the one place a schema change has
# to look (docs/audit/engineering-2026-09-25.md, finding 3).

CAPTURE_SOURCES = ("upload", "capture", "inbox")


@_reading
def document_source(con: sqlite3.Connection, doc_id: int) -> str | None:
    """Where a document came from (``meta.source``): a capture, an upload,
    an import."""
    row = con.execute(
        "SELECT json_extract(meta, '$.source') FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    return row[0] if row else None


@_reading
def document_state(con: sqlite3.Connection, doc_id: int) -> dict[str, Any] | None:
    """What the routes panel reads a document's state from: its MIME type,
    whether it has text, its meta, and how many characters its chunks
    hold. None when there is no such document."""
    row = con.execute(
        "SELECT mime, text_hash, meta FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        return None
    chars = con.execute(
        "SELECT coalesce(sum(length(text)), 0) FROM chunks WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    return {
        "mime": row["mime"],
        "text_hash": row["text_hash"],
        "meta": json.loads(row["meta"] or "{}"),
        "text_len": int(chars),
    }


@_reading
def figure_and_formula_data(
    con: sqlite3.Connection, doc_id: int
) -> list[tuple[str, dict[str, Any]]]:
    """``(kind, data)`` of a document's figure and formula chunks."""
    return [
        (str(r["kind"]), json.loads(r["data"]) if r["data"] else {})
        for r in con.execute(
            "SELECT kind, data FROM chunks"
            " WHERE doc_id = ? AND kind IN ('figure', 'formula')",
            (doc_id,),
        )
    ]


@_reading
def untexted_captures(con: sqlite3.Connection) -> list[int]:
    """Captures the door only registered (PDFs, images), oldest first:
    what a worker parses."""
    marks = ",".join("?" * len(CAPTURE_SOURCES))
    return [
        r[0]
        for r in con.execute(
            "SELECT id FROM documents WHERE text_hash IS NULL"
            " AND json_extract(meta, '$.retired') IS NULL"
            f" AND json_extract(meta, '$.source') IN ({marks}) ORDER BY id",
            CAPTURE_SOURCES,
        )
    ]


@_reading
def recent_captures(
    con: sqlite3.Connection, *, limit: int = 50
) -> list[dict[str, Any]]:
    """The latest live captures, newest first: ``id, title, mime,
    source_url, text_hash`` and ``meta``."""
    marks = ",".join("?" * len(CAPTURE_SOURCES))
    rows = con.execute(
        "SELECT id, title, mime, source_url, text_hash, meta FROM documents"
        f" WHERE json_extract(meta, '$.source') IN ({marks})"
        " AND json_extract(meta, '$.retired') IS NULL"
        " ORDER BY json_extract(meta, '$.capture.at') DESC, id DESC LIMIT ?",
        (*CAPTURE_SOURCES, limit),
    ).fetchall()
    return [{**dict(r), "meta": json.loads(r["meta"] or "{}")} for r in rows]


@_reading
def previous_capture(con: sqlite3.Connection, url: str, *, exclude: int) -> int | None:
    """The newest live document captured from ``url`` other than
    ``exclude``."""
    row = con.execute(
        "SELECT id FROM documents WHERE source_url = ? AND id != ?"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id DESC LIMIT 1",
        (url, exclude),
    ).fetchone()
    return row[0] if row else None


@_reading
def text_sources(con: sqlite3.Connection) -> list[tuple[int, str]]:
    """``(doc_id, meta.text_source)`` for every live document with text,
    oldest first: which extractor its text came from."""
    return [
        (int(r["id"]), str(r["src"] or ""))
        for r in con.execute(
            "SELECT id, json_extract(meta, '$.text_source') AS src FROM documents"
            " WHERE text_hash IS NOT NULL AND json_extract(meta, '$.retired') IS NULL"
            " ORDER BY id"
        )
    ]


def _summary_where(
    untried_only: bool, ids: list[int] | None
) -> tuple[str, tuple[Any, ...]]:
    where = (
        " WHERE json_extract(meta, '$.summary') IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    )
    if untried_only:
        where += " AND json_extract(meta, '$.summary_tried') IS NULL"
    if ids is None:
        return where, ()
    return where + f" AND id IN ({','.join('?' * len(ids))})", tuple(ids)


@_reading
def summaries_in_other_languages(
    con: sqlite3.Connection,
    lang: str,
    *,
    untried_only: bool = False,
    ids: list[int] | None = None,
) -> list[tuple[int, str]]:
    """``(doc_id, summary_lang)`` for the live documents whose summary is
    placed in a language other than ``lang``."""
    where, args = _summary_where(untried_only, ids)
    return [
        (int(r["id"]), str(r["lang"]))
        for r in con.execute(
            "SELECT id, json_extract(meta, '$.summary_lang') AS lang FROM documents"
            + where
            + " AND json_extract(meta, '$.summary_lang') IS NOT NULL"
            " AND json_extract(meta, '$.summary_lang') != ?",
            (*args, lang),
        )
    ]


@_reading
def translated_summaries(
    con: sqlite3.Connection,
    lang: str,
    *,
    untried_only: bool = False,
    ids: list[int] | None = None,
) -> list[tuple[int, str, dict[str, Any]]]:
    """``(doc_id, summary, summaries)`` for the live documents whose
    summary is in ``lang`` and that keep summaries in other languages:
    the ones a translation was made for."""
    where, args = _summary_where(untried_only, ids)
    return [
        (int(r["id"]), str(r["summary"]), json.loads(r["held"] or "{}"))
        for r in con.execute(
            "SELECT id, json_extract(meta, '$.summary') AS summary,"
            " json_extract(meta, '$.summaries') AS held FROM documents"
            + where
            + " AND json_extract(meta, '$.summaries') IS NOT NULL"
            " AND json_extract(meta, '$.summary_lang') = ?",
            (*args, lang),
        )
    ]


@_reading
def title_rows(
    con: sqlite3.Connection, ids: list[int] | None = None
) -> list[dict[str, Any]]:
    """``id, title, text_hash, meta`` of the live documents (or of
    ``ids``), oldest first: what a title is judged from."""
    sql = "SELECT id, title, text_hash, meta FROM documents"
    args: tuple[Any, ...] = ()
    if ids is not None:
        sql += f" WHERE id IN ({','.join('?' * len(ids))})"
        args = tuple(ids)
    out = []
    for r in con.execute(sql + " ORDER BY id", args):
        meta = json.loads(r["meta"] or "{}")
        if not meta.get("retired"):
            out.append({**dict(r), "meta": meta})
    return out


@_reading
def titled_documents(con: sqlite3.Connection) -> list[tuple[int, str]]:
    """``(doc_id, title)`` of every document that has one."""
    return [
        (int(r["id"]), str(r["title"]))
        for r in con.execute("SELECT id, title FROM documents WHERE title IS NOT NULL")
    ]


@_reading
def document_dois(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """``(doi, title)`` as the documents carry them, for every titled
    document with a DOI; normalising them is the caller's."""
    return [
        (str(r["doi"]), str(r["title"]))
        for r in con.execute(
            "SELECT title, json_extract(meta, '$.doi') AS doi FROM documents"
            " WHERE json_extract(meta, '$.doi') IS NOT NULL AND title IS NOT NULL"
        )
    ]


@_reading
def citation_candidates(
    con: sqlite3.Connection,
    *,
    limit: int | None = None,
    refresh: bool = False,
    doi_only: bool = False,
) -> list[int]:
    """Titled documents whose citations have not been fetched (all of them
    with ``refresh``), those with a DOI first."""
    sql = "SELECT id FROM documents WHERE title IS NOT NULL"
    if doi_only:
        sql += " AND json_extract(meta, '$.doi') IS NOT NULL"
    if not refresh:
        sql += " AND json_extract(meta, '$.citations.fetched_at') IS NULL"
    sql += " ORDER BY (json_extract(meta, '$.doi') IS NULL), id"
    args: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        args = (int(limit),)
    return [int(r["id"]) for r in con.execute(sql, args)]


KNOWN_BATCH = 1000  # hashes one question may ask about


@_reading
def known_hashes(con: sqlite3.Connection, hashes: list[str]) -> list[str]:
    """Which of these sha256 hashes of original bytes the store holds, a
    retired document's too (its bytes are archived; sending them again
    adds nothing). What a sender on another machine asks before it sends
    (``prax_send.py``), so a tree of thousands of files costs a request
    per thousand, not an upload each."""
    wanted = sorted({h.lower() for h in hashes})[:KNOWN_BATCH]
    if not wanted:
        return []
    marks = ",".join("?" * len(wanted))
    return [
        r[0]
        for r in con.execute(
            f"SELECT hash FROM documents WHERE hash IN ({marks})", wanted
        )
    ]


@_reading
def document_by_zotero_key(con: sqlite3.Connection, key: str) -> int | None:
    """The document a Zotero item key was imported as."""
    row = con.execute(
        "SELECT id FROM documents WHERE EXISTS"
        " (SELECT 1 FROM json_each(meta, '$.zotero.keys') WHERE value = ?)"
        " ORDER BY id LIMIT 1",
        (key,),
    ).fetchone()
    return int(row[0]) if row else None


@_reading
def newest_document(con: sqlite3.Connection) -> int:
    """The highest document id: the library's high-water mark."""
    return int(con.execute("SELECT coalesce(max(id), 0) FROM documents").fetchone()[0])


@_reading
def documents_added(
    con: sqlite3.Connection, since: str, until: str
) -> list[dict[str, Any]]:
    """``id, title, mime`` of the live documents added after ``since`` and
    up to ``until``, pages left out, oldest first."""
    return [
        dict(r)
        for r in con.execute(
            "SELECT id, title, mime FROM documents WHERE added_at > ?"
            " AND added_at <= ? AND json_extract(meta, '$.retired') IS NULL"
            " AND json_extract(meta, '$.page') IS NULL ORDER BY id",
            (since, until),
        )
    ]


@_reading
def documents_by_language(con: sqlite3.Connection) -> dict[str, int]:
    """How many documents are in each language that is known."""
    return {
        str(r[0]): int(r[1])
        for r in con.execute(
            "SELECT json_extract(meta, '$.lang'), count(*) FROM documents"
            " WHERE json_extract(meta, '$.lang') IS NOT NULL GROUP BY 1"
        )
    }


# prax's own pages are not the library's evidence about its words: a
# briefing that says "Olivenöl sits beside olive oil" is an English
# document containing the German word
NOT_A_PAGE = "NOT EXISTS (SELECT 1 FROM pages p WHERE p.doc_id = d.id)"


@_reading
def language_split(con: sqlite3.Connection, lang: str) -> tuple[int, int]:
    """How many documents, prax's pages left out, are in ``lang`` and how
    many in another language that is known."""
    row = con.execute(
        "SELECT count(*) FILTER (WHERE lang = ?), count(*) FILTER (WHERE lang <> ?)"
        " FROM (SELECT json_extract(d.meta, '$.lang') AS lang FROM documents d"
        f" WHERE {NOT_A_PAGE})",
        (lang, lang),
    ).fetchone()
    return int(row[0] or 0), int(row[1] or 0)


SHARED_TITLE = 3  # live documents carrying one title before it names none of them


@_reading
def shared_titles(con: sqlite3.Connection, *, at_least: int = SHARED_TITLE) -> set[str]:
    """The titles ``at_least`` live documents with text carry: a volume's
    name on 118 of its papers, a course's on its exercise sheets, a
    template's sample title on 89 documents (2026-09-27). A title so many
    different texts share names the collection they came in, and each
    document's own entity, named by it, is merged with the others'."""
    return {
        str(r[0])
        for r in con.execute(
            "SELECT title FROM documents WHERE title IS NOT NULL"
            " AND text_hash IS NOT NULL AND json_extract(meta, '$.retired') IS NULL"
            " GROUP BY title HAVING count(*) >= ?",
            (at_least,),
        )
    }


@_reading
def documents_of_key(con: sqlite3.Connection, source: str, key: str) -> list[int]:
    """The live documents an importer made of ``key`` (``meta.<source>.key``),
    oldest first: what a refresh of one item replaces."""
    if not source.isidentifier():
        raise ValueError(f"not a source name: {source!r}")
    rows = con.execute(
        "SELECT id FROM documents WHERE json_extract(meta, '$.source') = ?"
        f" AND json_extract(meta, '$.{source}.key') = ?"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id",
        (source, key),
    )
    return [int(r[0]) for r in rows]


# ---------------------------------------------------------- bibliographies
#
# A paper's own reference list, as the reference chunks hold it (an entry
# each, parsed by ``prax.text.references``, matched to the library by the
# ``references`` pass of ``prax maintain`` as ``data.cited``). Read from
# the entries and never from ``cites`` edges: an extraction's ``cites``
# can name a program a paper mentions ("OnsetDetector.LL"), an entry
# cannot (AL step 7).

MISSING_LIMIT = 30  # works a cited-but-missing answer names
MISSING_SET = 500  # documents one question may span


def _entry_links(data: dict[str, Any], *, search: bool = False) -> dict[str, str]:
    """Where an entry can be read: its DOI (``doi.org``, which resolves to
    the publisher; open access or not) and its arXiv id (open access)."""
    out: dict[str, str] = {}
    if data.get("doi"):
        out["doi"] = f"https://doi.org/{data['doi']}"
    if data.get("arxiv"):
        out["arxiv"] = f"https://arxiv.org/abs/{data['arxiv']}"
    if search and not out and data.get("title"):
        # no id printed: where to look it up (AL step 9, N4: no links came
        # back for most entries)
        out["search"] = "https://search.crossref.org/?q=" + quote_plus(
            str(data["title"])
        )
    return out


@_guards("doc", list)
@_reading
def references_of(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    """A document's reference list in order: each entry's ``n`` (its
    number, when the list numbers them), ``title``, ``authors``, ``year``,
    ``doi`` and ``arxiv`` as printed, ``in_library`` (the library document
    it cites, with the score and how it was matched) or ``links`` to read
    it elsewhere, and the entry's ``text`` (cut at 300 characters, at 160
    when a title was read from it). Numbered entries in their numbers'
    order: a list read in two columns came back 1-16, 31-41, 17-30 (the
    client's page, 2026-10-04)."""
    hidden = hidden_documents(con)
    out: list[dict[str, Any]] = []
    for r in con.execute(
        "SELECT id, text, data FROM chunks WHERE doc_id = ? AND kind = 'reference'"
        " ORDER BY seq",
        (doc_id,),
    ):
        data = json.loads(r["data"]) if r["data"] else {}
        cited = data.get("cited") or None
        if cited and int(cited.get("doc_id") or 0) in hidden:
            cited = None
        entry: dict[str, Any] = {
            "n": data.get("number"),
            "chunk_id": int(r["id"]),
            "title": data.get("title"),
            "authors": data.get("surnames") or [],
            "year": data.get("year"),
            "doi": data.get("doi"),
            "arxiv": data.get("arxiv"),
            "in_library": cited,
            "text": str(r["text"] or "")[: 160 if data.get("title") else 300],
        }
        links = _entry_links(data)
        if links and not cited:
            entry["links"] = links
        out.append({k: v for k, v in entry.items() if v not in (None, [], {})})
    numbered = [e for e in out if isinstance(e.get("n"), int)]
    if len(numbered) > len(out) / 2:
        out.sort(key=lambda e: (not isinstance(e.get("n"), int), e.get("n") or 0))
    return out


def _work_key(data: dict[str, Any]) -> str | None:
    """One cited work however its entries write it: its DOI, else its arXiv
    id, else its title folded (a short or wordless title is no key)."""
    if data.get("doi"):
        return "doi:" + str(data["doi"]).lower().rstrip(".")
    if data.get("arxiv"):
        return "arxiv:" + str(data["arxiv"]).split("v")[0]
    words = _TOKEN.findall(str(data.get("title") or "").lower())
    if len(words) < 3 or sum(len(w) for w in words) < 15:
        return None
    return "title:" + " ".join(words)


HELD_MIN_WORDS = 4  # a title one of the library's extends counts as held from this


def _title_held(title: str, held: set[str], held_sorted: list[str]) -> bool:
    """Whether the library holds a work of this folded title: the same
    title, one that extends it ("… onset detection (superflux)"), or one
    it extends, by whole words and at least ``HELD_MIN_WORDS`` of them.
    What the matching pass will link on its next run, not counted as
    missing meanwhile."""
    import bisect

    if title in held:
        return True
    words = title.split()
    if len(words) < HELD_MIN_WORDS:
        return False
    at = bisect.bisect_left(held_sorted, title + " ")
    if at < len(held_sorted) and held_sorted[at].startswith(title + " "):
        return True
    return any(" ".join(words[:n]) in held for n in range(HELD_MIN_WORDS, len(words)))


CITERS_SCANNED = 500  # reference entries read to count a work's citers


def _library_citers(
    con: sqlite3.Connection, title: str | None, hidden: frozenset[int] | set[int]
) -> int | None:
    """How many documents of the library cite a work, by its title as a
    phrase over the reference entries (the first eight words), counted
    only where the viewer sees the citing document; None for a title too
    short to say."""
    words = _TOKEN.findall(str(title or "").lower())[:8]
    if len(words) < 3:
        return None
    rows = con.execute(
        "SELECT c.doc_id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
        " WHERE chunks_fts MATCH ? AND c.kind = 'reference' LIMIT ?",
        ('"' + " ".join(words) + '"', CITERS_SCANNED),
    ).fetchall()
    return len({int(r[0]) for r in rows} - set(hidden))


@_reading
def cited_but_missing(
    con: sqlite3.Connection,
    doc_ids: list[int],
    *,
    limit: int = MISSING_LIMIT,
    min_count: int = 1,
) -> dict[str, Any]:
    """What a set of documents cites that the library does not hold: the
    unmatched entries of their reference lists, one work per DOI, arXiv id
    or folded title, ranked by how many of the documents cite it. Each
    work names its ``title``, ``authors``, ``year``, ``doi``/``arxiv`` and
    ``links``, and ``cited_by``. ``looked_at`` is how many of the documents
    have a reference list; a title the library holds exactly counts as
    held (the matching pass may not have run since it arrived).

    ``min_count`` leaves out what fewer of the documents cite. Among works
    cited equally often, the one the rest of the library cites less comes
    first, and each says how often the library cites it
    (``cited_in_library``, what the viewer sees): a reference every field
    cites (Adam, an acoustics textbook) is rarely what a topic lacks (AL
    step 9, N4)."""
    hidden = hidden_documents(con)
    ids = [int(i) for i in dict.fromkeys(doc_ids) if int(i) not in hidden][:MISSING_SET]
    if not ids:
        return {"looked_at": 0, "documents": 0, "missing": []}
    held = {
        " ".join(_TOKEN.findall(str(r[0]).lower()))
        for r in con.execute("SELECT title FROM documents WHERE title IS NOT NULL")
    }
    held_sorted = sorted(held)
    marks = ",".join("?" * len(ids))
    works: dict[str, dict[str, Any]] = {}
    with_lists: set[int] = set()
    # the reference chunks by document (migration 45): the kind index
    # walked every reference chunk of the library for a handful of
    # documents (the quality review of 2026-10-05)
    for r in con.execute(
        "SELECT doc_id, data FROM chunks INDEXED BY idx_chunks_reference"
        f" WHERE kind = 'reference' AND doc_id IN ({marks})",
        ids,
    ):
        with_lists.add(int(r["doc_id"]))
        data = json.loads(r["data"]) if r["data"] else {}
        if data.get("cited"):
            continue
        key = _work_key(data)
        if key is None:
            continue
        if key.startswith("title:") and _title_held(key[6:], held, held_sorted):
            continue
        work = works.setdefault(
            key,
            {
                "title": data.get("title"),
                "authors": data.get("surnames") or [],
                "year": data.get("year"),
                "doi": data.get("doi"),
                "arxiv": data.get("arxiv"),
                "cited_by": [],
            },
        )
        if int(r["doc_id"]) not in work["cited_by"]:
            work["cited_by"].append(int(r["doc_id"]))
        for k in ("year", "doi", "arxiv"):
            work[k] = work[k] or data.get(k)
    ranked = sorted(
        (w for w in works.values() if len(w["cited_by"]) >= min_count),
        key=lambda w: (-len(w["cited_by"]), str(w["title"])),
    )[: limit * 2]
    for w in ranked:
        w["cited_in_library"] = _library_citers(con, w.get("title"), hidden)
    ranked.sort(
        key=lambda w: (
            -len(w["cited_by"]),
            w["cited_in_library"] if w["cited_in_library"] is not None else 0,
            str(w["title"]),
        )
    )
    missing = []
    for w in ranked[:limit]:
        links = _entry_links(w, search=True)
        item = {**w, "count": len(w["cited_by"])}
        if links:
            item["links"] = links
        missing.append({k: v for k, v in item.items() if v not in (None, [], {})})
    return {"looked_at": len(with_lists), "documents": len(ids), "missing": missing}
