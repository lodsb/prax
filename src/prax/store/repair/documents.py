"""The ailments of documents: twins, files that are no documents, texts
that failed to parse or to be read, readings never done, stale parses
and extractions, and the edges and review items of retired documents."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from prax.text import glyphs

from .. import documents as docs
from ..base import _ASIDE, _read_archive, archive_path, now
from ..documents import (
    DUPLICATE_THRESHOLD,
    chunk_fingerprint,
    retire_document,
    set_mime,
    similarity,
)
from .common import CAP

STALE_JOB_SECONDS = 86_400  # a day: the door reaps its own host in minutes


_TITLE_KEY = re.compile(r"[^\w]+")


TWIN_TITLE_MIN = 9  # shorter titles pair by accident ("Untitled", "Notes")


def _twin_documents(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Two live documents with one title and the same text (the chunk
    fingerprints at or above ``DUPLICATE_THRESHOLD``): a PDF downloaded
    twice, a book kept in two prints — different bytes, so the hash did
    not fold them. Each pair names the keeper (more live edges, then the
    older id) and the twin to retire into it."""
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for r in con.execute(
        "SELECT id, title, mime FROM documents WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ):
        key = _TITLE_KEY.sub(" ", (r["title"] or "").lower()).strip()
        if len(key) < TWIN_TITLE_MIN:
            continue
        groups.setdefault((key, (r["mime"] or "").split("/")[0]), []).append(r)
    found: list[dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        ids = [m["id"] for m in members]
        edges = _document_edge_counts(con, ids)
        prints = {i: chunk_fingerprint(con, i) for i in ids}
        taken: set[int] = set()
        ranked = sorted(ids, key=lambda i: (-edges.get(i, 0), i))
        for keeper in ranked:
            if keeper in taken:
                continue
            for other in ranked:
                if other == keeper or other in taken:
                    continue
                score = similarity(prints[keeper], prints[other])
                if score >= DUPLICATE_THRESHOLD:
                    taken.add(other)
                    found.append(
                        {
                            "id": other,
                            "title": members[0]["title"],
                            "duplicate_of": keeper,
                            "similarity": round(score, 3),
                            "edges": edges.get(other, 0),
                            "keeper_edges": edges.get(keeper, 0),
                        }
                    )
            taken.add(keeper)
        if len(found) >= CAP:
            break
    return found[:CAP]


def _document_edge_counts(con: sqlite3.Connection, ids: list[int]) -> dict[int, int]:
    marks = ",".join("?" * len(ids))
    return {
        int(r["source_doc"]): int(r["n"])
        for r in con.execute(
            f"SELECT source_doc, count(*) AS n FROM edges WHERE valid_to IS NULL"
            f" AND source_doc IN ({marks}) GROUP BY source_doc",
            ids,
        )
    }


def _repair_twins(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Retire each twin into its keeper: what it holds and the keeper
    lacks moves over first (``retire_document`` with ``duplicate_of``)."""
    done = 0
    for row in rows:
        try:
            retire_document(
                con,
                row["id"],
                reason=f"twin of {row['duplicate_of']} (heal: same title, same text)",
                duplicate_of=row["duplicate_of"],
                by="heal",
            )
        except KeyError:
            continue
        done += 1
    return done


# What an original that is not what its type says looks like in its first
# bytes: the AppleDouble fork macOS keeps beside a file (`._name.pdf`), a
# Windows shortcut, a program, a run of zeros a broken download leaves.
_FORKS = {
    b"\x00\x05\x16\x07": "a macOS resource fork (._file), not the file",
    b"IntxLNK": "a link a copy made into a file (Cygwin's), not the file",
    b"MZ": "a program, not a document",
    # an ownCloud server's encrypted copy: readable with the server's key only
    b"HBEGIN:oc_encryption_module": "encrypted by an ownCloud server, not readable",
}


def _not_a_document(mime: str, head: bytes) -> str | None:
    """Why the first bytes of an original cannot be a document of that
    type, or None when they may. Only the types whose head is fixed by
    their standard are judged (a PDF opens with ``%PDF-`` in its first
    kilobyte); everything else passes."""
    if not head:
        return "an empty file"
    for magic, why in _FORKS.items():
        if head.startswith(magic):
            return why
    if head[:64].count(0) == len(head[:64]):
        return "zeros where the file should be"
    if mime == "application/pdf" and b"%PDF-" not in head:
        return "no PDF header in the first kilobyte"
    return None


def _not_documents(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Live documents whose original cannot be what its type says (a
    resource fork, a link copied as a file, an empty file, a PDF without a
    header): registered from a folder that held them beside the real files;
    no extractor will ever read them. Every type without text, not PDFs
    only: a copied Cygwin link was filed as the RTF it pointed at."""

    out = []
    for r in con.execute(
        "SELECT id, hash, mime, title FROM documents"
        " WHERE json_extract(meta, '$.retired') IS NULL AND text_hash IS NULL"
        " ORDER BY id"
    ):
        try:
            with open(archive_path(r["hash"]), "rb") as f:
                head = f.read(1024)
        except OSError:
            continue
        why = _not_a_document(r["mime"] or "", head)
        if why:
            out.append(
                {"id": r["id"], "title": r["title"], "mime": r["mime"], "why": why}
            )
            if len(out) >= CAP:
                break
    return out


def _repair_not_documents(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Retire them: the row and the bytes stay, the index and the graph
    forget them, ``unreadable-documents`` stops offering OCR for them."""
    done = 0
    for row in rows:
        try:
            retire_document(
                con, row["id"], reason=f"not a document: {row['why']} (heal)", by="heal"
            )
        except KeyError:
            continue
        done += 1
    return done


def _edges_of_retired(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT g.id, g.rel, g.source_doc, d.title"
        " FROM edges g JOIN documents d ON d.id = g.source_doc"
        " WHERE g.valid_to IS NULL"
        "   AND json_extract(d.meta, '$.retired') IS NOT NULL"
        " ORDER BY g.id LIMIT ?",
        (CAP,),
    ).fetchall()
    return [dict(r) for r in rows]


def _review_of_retired(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT r.id, r.rel, r.src, r.dst, r.source_doc"
        " FROM review_queue r JOIN documents d ON d.id = r.source_doc"
        " WHERE r.resolution IS NULL"
        "   AND json_extract(d.meta, '$.retired') IS NOT NULL"
        " ORDER BY r.id LIMIT ?",
        (CAP,),
    ).fetchall()
    return [dict(r) for r in rows]


def _stale_jobs(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT id, name, host, pid, updated_at FROM jobs"
        " WHERE status = 'running'"
        "   AND (julianday('now') - julianday(updated_at)) * 86400 > ?"
        " ORDER BY id LIMIT ?",
        (STALE_JOB_SECONDS, CAP),
    ).fetchall()
    return [dict(r) for r in rows]


def _unparsable_documents(con: sqlite3.Connection) -> list[dict[str, Any]]:
    from prax import parsers  # heavy imports happen inside the extractors

    rows = con.execute(
        "SELECT mime, count(*) AS documents, min(id) AS first_id, min(title) AS title"
        " FROM documents WHERE text_hash IS NULL"
        "   AND json_extract(meta, '$.retired') IS NULL"
        " GROUP BY mime ORDER BY documents DESC LIMIT ?",
        (CAP,),
    ).fetchall()
    return [dict(r) for r in rows if not parsers.candidates((r["mime"] or "").strip())]


UNTYPED = ("application/octet-stream", "")


def _untyped_documents(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents registered as unknown bytes whose file name says what they
    are, and a parser here reads that: the machine that took them in had
    no type for the name (six DjVu books on 2026-09-28, before prax named
    the type itself)."""
    from prax import parsers

    out = []
    for r in con.execute(
        "SELECT id, title, original_path, mime FROM documents"
        " WHERE text_hash IS NULL AND coalesce(mime, '') IN (?, ?)"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id LIMIT ?",
        (*UNTYPED, CAP),
    ):
        name = r["original_path"] or r["title"] or ""
        mime = parsers.guess_mime(name, fallback="")
        if mime and mime not in UNTYPED and parsers.candidates(mime):
            out.append({"id": r["id"], "title": r["title"], "mime": mime})
    return out


def _repair_untyped(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:

    for r in rows:
        set_mime(con, int(r["id"]), str(r["mime"]))
    return len(rows)


def _extraction_failed(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents the extract step could not read under the current
    ontology (``meta.extraction_error``): a prompt the model's slot cannot
    hold even cut, a server error; left out of the selection until the
    ontology moves or a reading succeeds."""
    return [
        dict(r)
        for r in con.execute(
            "SELECT id, title,"
            " json_extract(meta, '$.extraction_error.extractor') AS extractor,"
            " json_extract(meta, '$.extraction_error.error') AS error,"
            " json_extract(meta, '$.extraction_error.at') AS at"
            " FROM documents WHERE json_extract(meta, '$.extraction_error') IS NOT NULL"
            " AND json_extract(meta, '$.retired') IS NULL ORDER BY id LIMIT ?",
            (CAP,),
        )
    ]


SERVER_FAULT = re.compile(
    r"ServerNotReady|Loading model|HTTP 503|URLError|ConnectionError|"
    r"actively refused|Connection refused|timed out",
    re.IGNORECASE,
)


def _repair_extraction_failed(
    con: sqlite3.Connection, rows: list[dict[str, Any]]
) -> int:
    """Forget the errors that were the model server's (loading, down,
    refused), so the extract step selects those documents again; an
    error the document caused (a prompt no slot holds) stays, as the
    ailment says."""

    done = 0
    for r in rows:
        if not SERVER_FAULT.search(str(r.get("error") or "")):
            continue
        meta = docs.get_meta(con, r["id"])
        if meta.pop("extraction_error", None) is not None:
            docs.set_meta(con, r["id"], meta)
            done += 1
    return done


def _unread_figures(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents holding a picture no model has read: the reference and
    the caption are in the text and nothing says what the picture shows,
    so a search cannot find it and a reading cannot use it.

    Only a figure with a ``ref`` counts. A caption whose image no
    extractor could pull out of the PDF is not a backlog — nothing can
    read it — and counting those made this ailment three times its true
    size (2026-09-24)."""
    rows = con.execute(
        "SELECT c.doc_id AS id, d.title, count(*) AS unread,"
        "       json_extract(d.meta, '$.source') AS source"
        " FROM chunks c JOIN documents d ON d.id = c.doc_id"
        " WHERE c.kind = 'figure'"
        "   AND json_extract(c.data, '$.ref') IS NOT NULL"
        "   AND json_extract(d.meta, '$.retired') IS NULL"
        "   AND coalesce("
        "         json_array_length(json_extract(c.data, '$.readings')), 0) = 0"
        " GROUP BY c.doc_id ORDER BY unread DESC, c.doc_id LIMIT ?",
        (CAP,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "unread": r["unread"],
            "source": r["source"],
        }
        for r in rows
    ]


def _unread_formulas(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents holding a display equation no model has read: the LaTeX
    is there and embeds to noise, and nothing says in words what the
    equation is, so a search for it by meaning finds nothing."""
    rows = con.execute(
        "SELECT c.doc_id AS id, d.title, count(*) AS unread,"
        "       json_extract(d.meta, '$.source') AS source"
        " FROM chunks c JOIN documents d ON d.id = c.doc_id"
        " WHERE c.kind = 'formula'"
        "   AND json_extract(d.meta, '$.retired') IS NULL"
        "   AND coalesce("
        "         json_array_length(json_extract(c.data, '$.readings')), 0) = 0"
        " GROUP BY c.doc_id ORDER BY unread DESC, c.doc_id LIMIT ?",
        (CAP,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "unread": r["unread"],
            "source": r["source"],
        }
        for r in rows
    ]


def _unreadable_documents(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents every extractor has tried and found no text in
    (``documents.unreadable_documents``), with what the last attempt said."""

    ids = docs.unreadable_documents(con, limit=CAP)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    out = []
    for r in con.execute(
        f"SELECT id, title, mime, meta FROM documents WHERE id IN ({marks})"
        " ORDER BY id",
        tuple(ids),
    ):
        last = (json.loads(r["meta"] or "{}").get("parse_history") or [{}])[-1]
        out.append(
            {
                "id": r["id"],
                "title": r["title"],
                "mime": r["mime"],
                "last": last.get("outcome") or last.get("error"),
            }
        )
    return out


def _uncounted_pages(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """PDFs with text and no page count in their metadata: parsed before
    the worker recorded one. ``thin-texts`` cannot weigh them."""

    ids = docs.uncounted_pages(con)  # all of them: the repair counts them all
    out: list[Any] = []
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        marks = ",".join("?" * len(part))
        out.extend(
            {"id": r["id"], "title": r["title"]}
            for r in con.execute(
                f"SELECT id, title FROM documents WHERE id IN ({marks}) ORDER BY id",
                tuple(part),
            )
        )
    return out


def _repair_uncounted_pages(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Count them all (not only the rows shown): each PDF opened once,
    ``meta.pages`` written. Nothing where pymupdf is not installed."""

    return docs.count_pages(con, docs.uncounted_pages(con))


def _labelled_summaries(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Summaries that begin with the words of the message that asked for
    them — "Document title: …", "Description: …".

    A model told to answer with the translation and nothing else fills in
    the form the message looked like instead, and the English underneath
    is usually right. ``summaries.parse`` is what takes the label off, so
    the mend costs no model call.
    """
    from prax.writing import summaries

    rows = con.execute(
        "SELECT id, title, json_extract(meta, '$.summary') AS summary"
        " FROM documents WHERE json_extract(meta, '$.summary') IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    ).fetchall()
    out = []
    for r in rows:
        if summaries.acceptable(r["summary"], r["summary"]) != "the prompt's labels":
            continue
        out.append(
            {
                "id": r["id"],
                "title": r["title"],
                "summary": r["summary"][:160],
            }
        )
    return out[:CAP]


def _repair_labelled_summaries(
    con: sqlite3.Connection, rows: list[dict[str, Any]]
) -> int:
    """The label taken off every one of them, not only the rows shown."""
    from prax.writing import summaries

    n = 0
    for r in _labelled_summaries(con):
        meta = docs.get_meta(con, r["id"])
        cleaned = summaries.parse(str(meta.get("summary") or ""))
        if not cleaned or cleaned == meta.get("summary"):
            continue
        docs.set_summary(
            con,
            r["id"],
            cleaned,
            lang=meta.get("summary_lang"),
            source="heal",
        )
        n += 1
    return n


def _thin_texts(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """PDFs read as if their cover were the book
    (``documents.thin_documents``): the longest first."""

    rows = docs.thin_documents(con, limit=CAP)
    if not rows:
        return []
    titles = {}
    ids = [o["id"] for o in rows]
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT id, title FROM documents WHERE id IN ({marks})", tuple(part)
        ):
            titles[r["id"]] = r["title"]
    return [{**o, "title": titles.get(o["id"])} for o in rows]


def _unpolished_transcripts(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Videos with an automatic transcript the polish has not written:
    captured before the step existed, or while its model was away."""

    ids = docs.select_for_reading(con, unpolished=True, limit=CAP)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [
        {"id": r["id"], "title": r["title"]}
        for r in con.execute(
            f"SELECT id, title FROM documents WHERE id IN ({marks}) ORDER BY id",
            tuple(ids),
        )
    ]


# what the glyph check found per document, by the text it looked at: a
# text that has not changed is not read again (a GLOB over every chunk of
# the library took 13 s of every GET /heal; the door lives long)
_glyphs_seen: dict[int, tuple[str, bool]] = {}

# the ligature and Symbol ranges, and the spacing accents (glyphs.ACCENTS)
_GLYPH_GLOB = "'*[\ufb00-\ufb06\uf020-\uf0fe" + "".join(glyphs.ACCENTS) + "]*'"


def _glyph_documents(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents whose text still holds ligature or Symbol-font code
    points, or accents beside their letters: indexed before
    ``prax.text.glyphs`` cleaned every text. Each document's chunks are
    read once per text (``_glyphs_seen``). An accent mark alone is no
    damage (a backtick in code, an apostrophe written as \u00b4): a passage
    counts when cleaning would change it."""
    out = []
    live: set[int] = set()
    for r in con.execute(
        "SELECT id, title, text_hash FROM documents WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ):
        live.add(r["id"])
        seen = _glyphs_seen.get(r["id"])
        if seen is None or seen[0] != r["text_hash"]:
            hit = any(
                glyphs.damaged(str(c[0]))
                for c in con.execute(
                    "SELECT text FROM chunks WHERE doc_id = ?"
                    f" AND text GLOB {_GLYPH_GLOB}",
                    (r["id"],),
                )
            )
            seen = (r["text_hash"], hit)
            _glyphs_seen[r["id"]] = seen
        if seen[1]:
            out.append({"id": r["id"], "title": r["title"]})
    for gone in [d for d in _glyphs_seen if d not in live]:
        del _glyphs_seen[gone]
    return out


def _repair_glyphs(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Re-index the document from its own artifact, cleaned: chunks whose
    text did not change keep their vectors."""

    done = 0
    for r in rows:
        row = con.execute(
            "SELECT text_hash, json_extract(meta, '$.text_source') AS src"
            " FROM documents WHERE id = ?",
            (r["id"],),
        ).fetchone()
        if row is None or not row["text_hash"]:
            continue
        text = _read_archive(row["text_hash"]).decode("utf-8")
        if not glyphs.damaged(text):
            continue
        docs.index_text(con, r["id"], text, text_source=row["src"])
        done += 1
    return done


def _stale_parses(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents read by an extractor prax has revised since; ``covered``
    names the stamp an annotation in the history already brought the
    text to, when one did."""
    from prax.parsers import queue

    ids = queue.stale(con, limit=CAP)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    out = []
    for r in con.execute(
        f"SELECT id, title, meta FROM documents WHERE id IN ({marks}) ORDER BY id",
        tuple(ids),
    ):
        meta = json.loads(r["meta"] or "{}")
        out.append(
            {
                "id": r["id"],
                "title": r["title"],
                "text_source": meta.get("text_source"),
                "covered": queue.covered_by_history(meta),
            }
        )
    return out


def _repair_stale_parses(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Move the stamp where an annotation already made the revision's
    change; the others wait for the backlog pass."""

    done = 0
    for r in rows:
        if not r.get("covered"):
            continue
        meta = docs.get_meta(con, r["id"])
        history = list(meta.get("parse_history", []))
        at = now()
        history.append({"at": at, "extractor": r["covered"], "outcome": "stamped"})
        meta["parse_history"] = history
        meta["text_source"] = r["covered"]
        docs.set_meta(con, r["id"], meta)
        done += 1
    return done


def _stale_extractions(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents whose extraction is stamped on a text a replacing read
    has since replaced (``parse_history``: an ``upgraded`` entry by a
    reader that is not an annotator, after ``extraction.at``): the graph
    speaks of a text that is gone, and the extract step does not know.
    From before the rule (2026-09-17) that unstamps on the way in."""

    not_annotator = " AND ".join(
        f"json_extract(h.value, '$.extractor') NOT LIKE '{a}/%'"
        for a in docs.ANNOTATORS
    )
    rows = con.execute(
        "SELECT DISTINCT d.id, d.title,"
        "       json_extract(d.meta, '$.extraction.at') AS extracted_at,"
        "       json_extract(d.meta, '$.extraction.extractor') AS extractor,"
        "       json_extract(h.value, '$.extractor') AS read_by,"
        "       json_extract(h.value, '$.at') AS read_at"
        " FROM documents d, json_each(d.meta, '$.parse_history') h"
        " WHERE json_extract(d.meta, '$.extraction') IS NOT NULL"
        "   AND json_extract(d.meta, '$.retired') IS NULL"
        "   AND json_extract(h.value, '$.outcome') = 'upgraded'"
        "   AND json_extract(h.value, '$.at') > json_extract(d.meta, '$.extraction.at')"
        f"  AND {not_annotator}"
        " ORDER BY d.id LIMIT ?",
        (CAP,),
    ).fetchall()
    seen: dict[int, dict[str, Any]] = {}
    for r in rows:
        seen[r["id"]] = {
            "id": r["id"],
            "title": r["title"],
            "extractor": r["extractor"],
            "extracted_at": r["extracted_at"],
            "read_by": r["read_by"],
            "read_at": r["read_at"],
        }
    return list(seen.values())


def _repair_stale_extractions(
    con: sqlite3.Connection, rows: list[dict[str, Any]]
) -> int:
    """Move the stamp aside so the extract step reads the new text; the
    old reading's edges go when the new one is applied."""

    return sum(
        1 for r in rows if docs.unstamp_extraction(con, r["id"], str(r["read_by"]))
    )


def _unembedded_chunks(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Chunks with no vector from any model. Asked of the bookkeeping table
    alone: a check must never load an embedder to answer a question about
    rows (it would fetch a model file to say "none missing")."""
    waiting = con.execute(
        "SELECT count(*) FROM chunks c"
        " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
        f" WHERE e.chunk_id IS NULL{_ASIDE}"
    ).fetchone()[0]
    if not waiting:
        return []
    models = ", ".join(
        r[0]
        for r in con.execute(
            "SELECT model, count(*) FROM chunk_embeddings GROUP BY model"
            " ORDER BY 2 DESC LIMIT 3"
        )
    )
    return [{"chunks": waiting, "model": models or "no vectors yet"}]


LOST_READINGS_PRODUCER = "heal:lost-figure-readings"


def _lost_figure_readings(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents whose text lacks figure readings an earlier text of theirs
    had, for figures the text still holds by hash: a replacing read (marker,
    the parser again) wrote its own text and dropped the readings, which
    were lines of the old one (35 documents and 710 readings on
    2026-10-06, nearly all marker's). The earlier texts are the ones the
    parse record names by hash (``earlier_texts``). Each with how many
    readings would come back."""
    from prax.parsers import figures

    out: list[dict[str, Any]] = []
    for r in con.execute(
        "SELECT id, title, text_hash FROM documents d WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
        " AND json_extract(meta, '$.parse_history') LIKE '%text_hash%'"
        " AND EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = d.id"
        " AND c.kind = 'figure')"
    ):
        earlier = docs.earlier_texts(con, int(r["id"]))
        if not earlier:
            continue
        try:
            current = _read_archive(str(r["text_hash"])).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        carried = figures.carry_readings([current, *earlier], current)
        gained = len(figures.READ_BY.findall(carried)) - len(
            figures.READ_BY.findall(current)
        )
        if gained > 0:
            out.append({"id": int(r["id"]), "title": r["title"], "readings": gained})
    out.sort(key=lambda x: (-x["readings"], x["id"]))
    return out[:CAP]


def _repair_lost_figure_readings(
    con: sqlite3.Connection, rows: list[dict[str, Any]]
) -> int:
    """Each text with its readings carried back in, through the parse path
    as an annotation (``parsers.queue.apply_parse``, ``keep_source``): the
    parse record says so under this repair's name, the text source stays,
    and a chunk whose text did not change keeps its vector."""
    from prax.parsers import figures, queue

    done = 0
    for row in rows:
        doc = docs.get_document(con, int(row["id"]))
        if doc is None:
            continue
        earlier = docs.earlier_texts(con, int(row["id"]))
        carried = figures.carry_readings([doc["text"], *earlier], doc["text"])
        if carried == doc["text"]:
            continue
        outcome = queue.apply_parse(
            con,
            int(row["id"]),
            stamp=LOST_READINGS_PRODUCER,
            text=carried,
            keep_source=True,
        )
        done += outcome == "upgraded"
    return done
