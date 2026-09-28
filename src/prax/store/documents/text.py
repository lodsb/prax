"""A document's text: registering an original, indexing the text artifact
into chunks, the reference list's links, and the two ingest paths."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from prax import chunking, config, glyphs, language

from ..base import (
    _NOW,
    _SURROGATE,
    _TOKEN,
    ASIDE_KINDS,
    _archive_bytes,
    _archive_path,
    _read_archive,
    _reading,
    _serialized,
    now,
)
from .meta import _is_indexed, _refresh_document_field, get_meta


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
    ``meta.text_source`` in the same transaction. So is ``meta.lang``, the
    language the text is in (``prax.language``), which nothing recorded
    before and every side of retrieval had to guess.
    """
    exists = con.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if exists is None:
        raise KeyError(f"no such document: {doc_id}")
    text = _cleaned(text)
    data = text.encode("utf-8")
    text_hash = _archive_bytes(data)
    n_chunks = _write_chunks(con, doc_id, text)
    con.execute(
        f"UPDATE documents SET text_hash = ?, text_len = ?, parsed_at = {_NOW}"
        " WHERE id = ?",
        (text_hash, len(text), doc_id),
    )
    if text_source is not None:
        con.execute(
            "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
            " '$.text_source', ?) WHERE id = ?",
            (text_source, doc_id),
        )
    lang = language.detect(text)
    if lang:
        con.execute(
            "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
            " '$.lang', ?) WHERE id = ?",
            (lang, doc_id),
        )
    _refresh_document_field(con, doc_id)
    con.commit()
    # a bank statement from the NAS is closed off as it lands (stage V)
    suspect(con, doc_id, text)
    return {"doc_id": doc_id, "text_hash": text_hash, "n_chunks": n_chunks}


def _cleaned(text: str) -> str:
    """What every text goes through before it is stored: NUL bytes
    (pdftotext emits them for some page numbers) truncate SQLite's text
    functions and the FTS tokenizer; lone surrogates (MuPDF, broken fonts)
    cannot be encoded at all — neither carries content; ligature and
    Symbol-font code points become the letters they stand for
    (``prax.glyphs``)."""
    return glyphs.clean(_SURROGATE.sub("�", text.replace("\x00", "")))


@_serialized
def fill_text_lengths(con: sqlite3.Connection, *, limit: int | None = None) -> int:
    """``documents.text_len`` for the rows indexed before the column (NULL
    with an artifact): each artifact read once. Returns how many were
    filled."""
    sql = (
        "SELECT id, text_hash FROM documents WHERE text_hash IS NOT NULL"
        " AND text_len IS NULL ORDER BY id"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = con.execute(sql).fetchall()
    for r in rows:
        try:
            n = len(_read_archive(r["text_hash"]).decode("utf-8"))
        except OSError:  # an artifact gone from the archive: heal's business
            continue
        con.execute("UPDATE documents SET text_len = ? WHERE id = ?", (n, r["id"]))
    con.commit()
    return len(rows)


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
    # a kept chunk that became a kind the search sets aside gives up its
    # vector (the bookkeeping row; the index key is filtered until compacted)
    aside = [oid for i, oid in kept.items() if rows[i][1] in ASIDE_KINDS]
    if aside:
        con.executemany(
            "DELETE FROM chunk_embeddings WHERE chunk_id = ?", [(o,) for o in aside]
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
    links = (get_meta(con, doc_id).get("references") or {}).get("links") or []
    if links:  # the same text: what the references pass matched still holds
        _apply_reference_links(con, doc_id, links)
    con.commit()
    return n


def reference_chunks(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    """A document's reference chunks (one per entry of its reference list),
    ``{chunk_id, text, data}`` in order."""
    return [
        {
            "chunk_id": r["id"],
            "text": r["text"],
            "data": json.loads(r["data"]) if r["data"] else {},
        }
        for r in con.execute(
            "SELECT id, text, data FROM chunks WHERE doc_id = ? AND kind = 'reference'"
            " ORDER BY seq",
            (doc_id,),
        )
    ]


def _reference_key(data: dict[str, Any]) -> str:
    """What a link is matched to a chunk by: the entry's number, else its
    title normalised."""
    if data.get("number") is not None:
        return f"#{data['number']}"
    return "t:" + " ".join(_TOKEN.findall(str(data.get("title") or "").lower()))


def _apply_reference_links(
    con: sqlite3.Connection, doc_id: int, links: list[dict[str, Any]]
) -> int:
    """Write the references pass's matches (``meta.references.links``:
    number or title, the cited document, score, how) into the reference
    chunks' ``data.cited``; a chunk no link names loses a stale one.
    Chunks are disposable, the links are not: a rechunk calls this."""
    by_key: dict[str, list[dict[str, Any]]] = {}
    for link in links:  # keyed by the entry's number, else the entry's title
        key = _reference_key({"number": link.get("number"), "title": link.get("entry")})
        by_key.setdefault(key, []).append(link)
    n = 0
    for c in reference_chunks(con, doc_id):
        data = dict(c["data"])
        found = by_key.get(_reference_key(data))
        if found:
            best = max(found, key=lambda l: float(l.get("score") or 0))
            cited = {
                "doc_id": best["doc_id"],
                "title": best.get("title"),
                "score": best.get("score"),
                "how": best.get("how"),
            }
            if len(found) > 1:
                cited["also"] = [
                    {"doc_id": o["doc_id"], "title": o.get("title")}
                    for o in found
                    if o is not best
                ]
            if data.get("cited") == cited:
                continue
            data["cited"] = cited
        elif "cited" in data:
            data.pop("cited")
        else:
            continue
        con.execute(
            "UPDATE chunks SET data = ? WHERE id = ?",
            (json.dumps(data), c["chunk_id"]),
        )
        n += 1
    return n


@_serialized
def set_reference_links(
    con: sqlite3.Connection, doc_id: int, links: list[dict[str, Any]]
) -> int:
    """Keep the pass's matches on the document (``meta.references.links``)
    and on its reference chunks; returns how many chunks changed."""
    meta = get_meta(con, doc_id)
    stamp = dict(meta.get("references") or {})
    stamp["links"] = links
    meta["references"] = stamp
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    n = _apply_reference_links(con, doc_id, links)
    con.commit()
    return n


def archive_blob(data: bytes) -> str:
    """Content-address ``data`` into the archive (a filed picture: a
    parser's crop of a scanned page, which no original holds); its sha."""
    return _archive_bytes(data)


@_reading
def get_original(con: sqlite3.Connection, doc_id: int) -> bytes:
    """The archived original bytes of a document (what its hash names)."""
    row = con.execute("SELECT hash FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    return _read_archive(row["hash"])


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


@_reading
def text_hashes(con: sqlite3.Connection, doc_ids: list[int]) -> dict[int, str]:
    """The text artifact's hash by id, one query (unknown ids left out; a
    document without text is an empty string): what a standing question
    keeps to tell a re-read source from an unchanged one."""
    ids = list(dict.fromkeys(int(i) for i in doc_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT id, text_hash FROM documents WHERE id IN ({marks})", ids
    ).fetchall()
    return {r["id"]: r["text_hash"] or "" for r in rows}


@_reading
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


def _person_decided(meta: dict[str, Any]) -> bool:
    """Whether a person has said what this document is, either way: the
    rules never overrule that."""
    return (meta.get("sensitivity") or {}).get("by") == "human"


@_serialized
def suspect(
    con: sqlite3.Connection,
    doc_id: int,
    text: str | None = None,
    *,
    rules: Any = None,
) -> list[str] | None:
    """The personal-document rules over one document (stage V,
    ``prax.private``): the cues it shows, and, when they are enough, the
    document marked ``suspected`` with them in ``meta.private``. Only an
    open document no person has decided about is looked at (None
    otherwise); ``meta.private.rules`` records which rules looked at a
    document with cues, so the nightly pass does not read it again. ``text`` is the text
    when the caller has it; otherwise the head of the artifact is read."""
    from prax import private

    rules = rules or private.rules()
    row = con.execute(
        "SELECT sensitivity, title, original_path, source_url, text_hash, meta"
        " FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    meta = json.loads(row["meta"] or "{}")
    if row["sensitivity"] is not None or _person_decided(meta):
        return None
    if text is None and row["text_hash"]:
        path = config.archive_dir() / row["text_hash"][:2] / row["text_hash"]
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                text = fh.read(rules.head)
        except OSError:
            text = ""
    paths = [
        row["original_path"] or "",
        row["source_url"] or "",
        str((meta.get("origin") or {}).get("path") or ""),
    ]
    cues = rules.cues(row["title"] or "", paths, text or "")
    if not cues:
        # nothing to keep: a document with no cue carries no mark, and the
        # nightly pass reads it again (a file read, no write)
        return cues
    meta["private"] = {
        "rules": rules.stamp(),
        "cues": cues,
        "at": now(),
        "read": bool(text) or bool(row["text_hash"]),
    }
    state = None
    if rules.suspect(cues):
        state = "suspected"
        meta["sensitivity"] = {"state": state, "by": "rules", "at": now()}
    con.execute(
        "UPDATE documents SET sensitivity = ?, meta = ? WHERE id = ?",
        (state, json.dumps(meta), doc_id),
    )
    con.commit()
    return cues
