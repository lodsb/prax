"""The heal pass: what goes wrong often enough to have a name.

Extraction at scale leaves the same few kinds of damage behind. A model
copies a word out of its own prompt and "source name" becomes an entity
with a thousand edges. Two entities are merged and the edges between them
become loops from a thing to itself. A duplicate capture is retired and
its edges stay live. None of it is a bug to be fixed once: it is weather,
and this is the place that names each kind, finds it and repairs it.

Every repair goes through the store's own functions and takes the store's
rules with it: an edge is invalidated, never deleted (invariant 8), a
review item is resolved, a job row is closed. A pass is announced as a
job so the Jobs view shows it. Nothing is repaired unless a caller asks
for that ailment by name: `GET /heal` looks, `POST /heal` repairs, and
`prax heal` is the two of them with `--apply`.

The module is `store.repair` because `store.heal` is the function that
mends; `health` is the one that only looks.

Adding an ailment: write `find` (what is wrong, as rows a person can
read) and, when it can be repaired safely, `repair`; append an `Ailment`
to `AILMENTS`. An ailment whose repair is None is a report: it says what
to do rather than doing it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from prax import glyphs

from .base import _NOW, _reading
from .documents import (
    DUPLICATE_THRESHOLD,
    chunk_fingerprint,
    retire_document,
    similarity,
)
from .graph import invalidate_edge, rename_entity, resolve_review
from .jobs import Job, job_finish

# How many rows one pass looks at and repairs; a bigger mess is cleared by
# running it again, which keeps a single call short and a single lock brief.
CAP = 5000
EXAMPLES = 6

# Words a model copies out of its own prompt instead of naming a thing.
# Conservative on purpose: anything that could be a real concept in a
# library about sound, text or making ("text", "value", "subject") is left
# out, because a wrong repair costs more than an unrepaired edge.
PLACEHOLDER_NAMES = frozenset(
    {
        "source name",
        "target name",
        "entity name",
        "concept name",
        "method name",
        "tool name",
        "author name",
        "document title",
        "paper title",
        "the document",
        "the source",
        "the target",
        "name",
        "title",
        "unknown",
        "unknown author",
        "n/a",
        "na",
        "none",
        "null",
        "nil",
        "tbd",
        "todo",
        "placeholder",
        "xxx",
        "...",
        "-",
        "--",
        "?",
        "no name",
        "not specified",
        "not stated",
        "not mentioned",
        "unnamed",
        "untitled",
    }
)
# A bracketed reference number is never a name (the ontology says so in as
# many words); neither is a figure, table or equation number on its own.
REFERENCE_NAME = re.compile(
    r"^(?:\[|\()?\s*"
    r"(?:ref\.?|reference|fig\.?|figure|table|eq\.?|equation|section|sec\.?"
    r"|chapter|ch\.?)?"
    r"\s*\d{1,4}(?:\s*[-–,]\s*\d{1,4})?\s*(?:\]|\))?$",
    re.IGNORECASE,
)
# Markup and line breaks a citation importer leaves in a title, as in
# "<i>The Origins of Music</i>" or a title broken across the lines of
# the XML it came from, indentation and all.
MARKUP = re.compile(r"<[^>]{1,40}>")
MANGLED = re.compile("<[^>]{1,40}>|[" + chr(10) + chr(13) + chr(9) + "]|  ")
# A name longer than this is a whole citation or a paragraph rather than a
# name — except for a claim, which the ontology defines as a sentence.
NAME_TOO_LONG = 300
# an extractor's own wire syntax glued to a name: a line the model wrote in
# the triple format and the parser took whole — "chord dst_type=concept
# (confidence=EXTRACTED evidence=…", "no_correspondence(dst=Sundberg)"
WIRE = re.compile(
    r"\s*\(?\b(?:dst_type|src_type|triple_src|confidence|evidence|evedence|src|dst"
    r"|rel|type)\s*=.*$",
    re.IGNORECASE | re.DOTALL,
)
SENTENCE_TYPES = ("claim",)
STALE_JOB_SECONDS = 86_400  # a day: the door reaps its own host in minutes


@dataclass(frozen=True)
class Ailment:
    name: str
    what: str  # what is wrong, in a sentence
    fix: str  # what repairing does, or what to do about it by hand
    find: Callable[[sqlite3.Connection], list[dict[str, Any]]]
    repair: Callable[[sqlite3.Connection, list[dict[str, Any]]], int] | None = None
    # readings a person may ask for over everything found (a report-only
    # ailment's way on): each a label and the body of POST /readings/bulk
    offers: tuple[dict[str, Any], ...] = ()

    @property
    def repairable(self) -> bool:
        return self.repair is not None


# ------------------------------------------------------------------- find


def _live_edge_counts(con: sqlite3.Connection, ids: list[int]) -> dict[int, int]:
    """How many live edges each of these entities carries. Two indexed
    queries per batch: a correlated count with `src = ? OR dst = ?` cannot
    use either edge index and turns a check into a table scan per entity."""
    counts: dict[int, int] = {}
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        marks = ",".join("?" * len(batch))
        for side in ("src", "dst"):
            for entity_id, n in con.execute(
                f"SELECT {side}, count(*) FROM edges"
                f" WHERE valid_to IS NULL AND {side} IN ({marks})"
                f" GROUP BY {side}",
                batch,
            ):
                counts[entity_id] = counts.get(entity_id, 0) + n
    return counts


def _with_edge_counts(
    con: sqlite3.Connection, rows: list[sqlite3.Row]
) -> list[dict[str, Any]]:
    """Entity rows that carry at least one live edge, the busiest first."""
    counts = _live_edge_counts(con, [r["id"] for r in rows])
    found = [
        {"id": r["id"], "name": r["name"], "type": r["type"], "edges": counts[r["id"]]}
        for r in rows
        if counts.get(r["id"])
    ]
    return sorted(found, key=lambda r: -r["edges"])[:CAP]


def _entity_rows(
    con: sqlite3.Connection, where: str, args: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    return _with_edge_counts(
        con,
        con.execute(
            f"SELECT id, name, type FROM entities WHERE {where}", args
        ).fetchall(),
    )


def _placeholder_entities(con: sqlite3.Connection) -> list[dict[str, Any]]:
    marks = ",".join("?" * len(PLACEHOLDER_NAMES))
    return _entity_rows(
        con, f"lower(trim(name)) IN ({marks})", tuple(sorted(PLACEHOLDER_NAMES))
    )


def _reference_entities(con: sqlite3.Connection) -> list[dict[str, Any]]:
    short = con.execute(
        "SELECT id, name, type FROM entities WHERE length(name) <= 24"
    ).fetchall()
    return _with_edge_counts(
        con, [r for r in short if REFERENCE_NAME.match((r["name"] or "").strip())]
    )


def _unnamed_entities(con: sqlite3.Connection) -> list[dict[str, Any]]:
    marks = ",".join("?" * len(SENTENCE_TYPES))
    return _entity_rows(
        con,
        f"trim(name) = '' OR (length(name) > ? AND type NOT IN ({marks}))",
        (NAME_TOO_LONG, *SENTENCE_TYPES),
    )


def clean_name(name: str) -> str:
    """A name with the markup taken out and its whitespace collapsed."""
    return re.sub(r"\s+", " ", MARKUP.sub("", name)).strip()


def _mangled_names(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Only entities that are a name in their own right: an alias merged
    into another entity keeps the string it was found under, which is
    history rather than a name, and mending it would only collide with the
    clean one it already points at."""
    found = []
    for row in con.execute(
        "SELECT id, name, type FROM entities WHERE canonical_id IS NULL"
    ):
        if not MANGLED.search(row["name"] or ""):
            continue
        cleaned = clean_name(row["name"])
        if cleaned and cleaned != row["name"]:
            found.append({**dict(row), "cleaned": cleaned})
    counts = _live_edge_counts(con, [r["id"] for r in found])
    for row in found:
        row["edges"] = counts.get(row["id"], 0)
    return found[:CAP]


def _wire_names(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Unmerged entities whose name holds the wire syntax, with the name
    cut at it (``cleaned``; empty when nothing but syntax was there)."""
    found = []
    for row in con.execute(
        "SELECT id, name, type FROM entities WHERE canonical_id IS NULL"
    ):
        if not WIRE.search(row["name"] or ""):
            continue
        cleaned = clean_name(WIRE.sub("", row["name"]).strip(" (,;:"))
        found.append({**dict(row), "cleaned": cleaned})
    counts = _live_edge_counts(con, [r["id"] for r in found])
    for row in found:
        row["edges"] = counts.get(row["id"], 0)
    # nothing but syntax and no live edge: already dealt with, invisible
    return [r for r in found if r["cleaned"] or r["edges"]][:CAP]


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
    b"IntxLNK": "a Windows shortcut, not the file",
    b"MZ": "a program, not a document",
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
    resource fork, a shortcut, an empty file, a PDF without a header):
    registered from a folder that held them beside the real files; no
    extractor will ever read them."""
    from .base import _archive_path

    out = []
    for r in con.execute(
        "SELECT id, hash, mime, title FROM documents"
        " WHERE json_extract(meta, '$.retired') IS NULL AND text_hash IS NULL"
        "   AND mime IN ('application/pdf') ORDER BY id"
    ):
        try:
            with open(_archive_path(r["hash"]), "rb") as f:
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


def _self_edges(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT g.id, g.rel, n.name, g.source_doc
        FROM edges g
        JOIN entities a ON a.id = g.src
        JOIN entities b ON b.id = g.dst
        JOIN entities n ON n.id = COALESCE(a.canonical_id, a.id)
        WHERE g.valid_to IS NULL
          AND COALESCE(a.canonical_id, a.id) = COALESCE(b.canonical_id, b.id)
        ORDER BY g.id LIMIT ?
        """,
        (CAP,),
    ).fetchall()
    return [dict(r) for r in rows]


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
    from prax.store import documents as docs

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
    """Documents holding a figure no model has read: the picture is in
    the text as a reference and a caption, and nothing says what it
    shows, so a search cannot find it and a reading cannot use it."""
    rows = con.execute(
        "SELECT c.doc_id AS id, d.title, count(*) AS unread,"
        "       json_extract(d.meta, '$.source') AS source"
        " FROM chunks c JOIN documents d ON d.id = c.doc_id"
        " WHERE c.kind = 'figure'"
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
    from prax.store import documents as docs

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
    from prax.store import documents as docs

    ids = docs.uncounted_pages(con, limit=CAP)
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


def _repair_uncounted_pages(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Count them all (not only the rows shown): each PDF opened once,
    ``meta.pages`` written. Nothing where pymupdf is not installed."""
    from prax.store import documents as docs

    return docs.count_pages(con, docs.uncounted_pages(con))


def _thin_texts(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """PDFs read as if their cover were the book
    (``documents.thin_documents``): the longest first."""
    from prax.store import documents as docs

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
    from prax.store import documents as docs

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


def _glyph_documents(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents whose text still holds ligature or Symbol-font code
    points: indexed before ``prax.glyphs`` cleaned every text."""
    out = []
    rows = con.execute(
        "SELECT DISTINCT c.doc_id AS id, d.title FROM chunks c"
        " JOIN documents d ON d.id = c.doc_id"
        " WHERE c.text GLOB '*[\ufb00-\ufb06\uf020-\uf0fe]*' ORDER BY c.doc_id"
    ).fetchall()
    for r in rows:
        out.append({"id": r["id"], "title": r["title"]})
    return out


def _repair_glyphs(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Re-index the document from its own artifact, cleaned: chunks whose
    text did not change keep their vectors."""
    from prax.store import documents as docs

    done = 0
    for r in rows:
        row = con.execute(
            "SELECT text_hash, json_extract(meta, '$.text_source') AS src"
            " FROM documents WHERE id = ?",
            (r["id"],),
        ).fetchone()
        if row is None or not row["text_hash"]:
            continue
        text = docs._read_archive(row["text_hash"]).decode("utf-8")
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
    from prax.store import documents as docs

    done = 0
    for r in rows:
        if not r.get("covered"):
            continue
        meta = docs.get_meta(con, r["id"])
        history = list(meta.get("parse_history", []))
        at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    from prax.store import documents as docs

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
    from prax.store import documents as docs

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
        " WHERE e.chunk_id IS NULL"
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


# ----------------------------------------------------------------- repair


def _invalidate(con: sqlite3.Connection, edge_ids: list[int]) -> int:
    done = 0
    for edge_id in edge_ids:
        try:
            invalidate_edge(con, edge_id)
        except (KeyError, ValueError):  # another pass ended it first
            continue
        done += 1
    return done


def _edges_of_entities(con: sqlite3.Connection, ids: list[int]) -> list[int]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [
        r[0]
        for r in con.execute(
            f"SELECT id FROM edges WHERE valid_to IS NULL"
            f" AND (src IN ({marks}) OR dst IN ({marks}))",
            (*ids, *ids),
        )
    ]


def _repair_entities(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """End every live edge these entities carry. The entities themselves
    stay: an entity with no live edges is invisible, and its name is the
    record of what the extraction did."""
    return _invalidate(con, _edges_of_entities(con, [r["id"] for r in rows]))


def _repair_edges(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    return _invalidate(con, [r["id"] for r in rows])


def _repair_names(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Clean the name in place, or merge into the entity that already
    carries the clean one (a paper cited twice, once with markup)."""
    done = 0
    for row in rows:
        try:
            how = rename_entity(con, row["id"], row["cleaned"])
        except KeyError:  # merged away by an earlier row of this pass
            continue
        except ValueError:
            # the clean name is already an alias of this one: an earlier
            # resolution pass merged them the other way round and the graph
            # already treats them as one thing. Only the display name is
            # ugly, and flipping which of the two is canonical is not
            # something a repair should do behind a person's back.
            continue
        if how != "unchanged":
            done += 1
    return done


def _repair_wire_names(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Mend the name where one is left after the cut (the edge under it
    is real); end the edges of the few that were nothing but syntax."""
    named = [r for r in rows if r["cleaned"]]
    junk = [r for r in rows if not r["cleaned"]]
    return _repair_names(con, named) + (_repair_entities(con, junk) if junk else 0)


def _repair_review(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    done = 0
    for row in rows:
        try:
            resolve_review(con, row["id"], "dropped")
        except (KeyError, ValueError):
            continue
        done += 1
    return done


def _repair_jobs(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    done = 0
    for row in rows:
        try:
            job_finish(con, row["id"], status="failed", note="closed by the heal pass")
        except (KeyError, ValueError):
            continue
        done += 1
    return done


# --------------------------------------------------------------- the list

AILMENTS: tuple[Ailment, ...] = (
    Ailment(
        name="placeholder-entities",
        what=(
            "entities named after the prompt rather than after a thing"
            " ('source name', 'unknown', 'n/a'), carrying live edges"
        ),
        fix="end every edge they carry; the entity stays as the record",
        find=_placeholder_entities,
        repair=_repair_entities,
    ),
    Ailment(
        name="reference-number-entities",
        what=(
            "entities whose name is a reference, figure or table number"
            " ('[12]', 'fig. 3'), which the ontology says is never a name"
        ),
        fix="end every edge they carry",
        find=_reference_entities,
        repair=_repair_entities,
    ),
    Ailment(
        name="mangled-names",
        what=(
            "entity names with markup or line breaks in them, as a citation"
            " importer leaves them ('<i>The Origins of Music</i>')"
        ),
        fix=(
            "clean the name, or merge into the entity that already carries"
            " the clean one"
        ),
        find=_mangled_names,
        repair=_repair_names,
    ),
    Ailment(
        name="wire-names",
        what=(
            "entity names an extractor's own wire syntax got glued to"
            " ('chord dst_type=concept(confidence=EXTRACTED evidence=…',"
            " 'no_correspondence(dst=Sundberg)'): a line the model wrote"
            " in the triple format and the parser took whole"
        ),
        fix=(
            "cut the name at the syntax and clean it, or merge into the"
            " entity that already carries it; a name that was nothing but"
            " syntax has its edges ended"
        ),
        find=_wire_names,
        repair=_repair_wire_names,
    ),
    Ailment(
        name="unnamed-entities",
        what=(
            "entities with no name at all, or with a whole citation or"
            " paragraph as one (a claim is a sentence and is left alone)"
        ),
        fix="end every edge they carry",
        find=_unnamed_entities,
        repair=_repair_entities,
    ),
    Ailment(
        name="twin-documents",
        what=(
            "two live documents with one title and the same text — a PDF"
            " downloaded twice, a book kept in two prints; different bytes,"
            " so the hash did not fold them"
        ),
        fix=(
            "retire the twin into the keeper (more live edges, then the older"
            " one): what it holds and the keeper lacks moves over first"
        ),
        find=_twin_documents,
        repair=_repair_twins,
    ),
    Ailment(
        name="self-edges",
        what=(
            "live edges from a thing to itself, usually what is left after"
            " two names were merged into one"
        ),
        fix="end them; they say nothing about the neighbourhood",
        find=_self_edges,
        repair=_repair_edges,
    ),
    Ailment(
        name="edges-of-retired-documents",
        what="live edges whose source document was retired (a duplicate capture)",
        fix="end them; the surviving capture's reading stands",
        find=_edges_of_retired,
        repair=_repair_edges,
    ),
    Ailment(
        name="review-of-retired-documents",
        what="open review items from documents that were retired",
        fix="resolve them as dropped",
        find=_review_of_retired,
        repair=_repair_review,
    ),
    Ailment(
        name="stale-jobs",
        what="jobs still marked running whose heartbeat stopped a day ago",
        fix="close them as failed (the door reaps its own host within minutes)",
        find=_stale_jobs,
        repair=_repair_jobs,
    ),
    Ailment(
        name="documents-without-an-extractor",
        what=(
            "documents waiting for text of a kind nothing here can read"
            " (a .doc without LibreOffice, a zip, a video)"
        ),
        fix=(
            "install what reads them (howto 3b) or retire them; nothing to"
            " repair in the store"
        ),
        find=_unparsable_documents,
    ),
    Ailment(
        name="extraction-failed",
        what=(
            "documents the extract step could not read under the current"
            " ontology (the error is kept): left out of the passes until the"
            " ontology moves or a reading succeeds"
        ),
        fix=(
            "a bigger slot on the model server, or the promote step for the"
            " few that matter. Repairing forgets only the errors that were the"
            " server's (loading its model, down, refused: a pass ran while"
            " llama-server was paused), so those are selected again"
        ),
        find=_extraction_failed,
        repair=_repair_extraction_failed,
    ),
    Ailment(
        name="not-documents",
        what=(
            "originals that cannot be what their type says — a macOS resource"
            " fork (._file), a Windows shortcut, a program, an empty file, a"
            " PDF without its header — registered from a folder that held"
            " them beside the real files; nothing will ever read them"
        ),
        fix=(
            "retire them: row and bytes stay, index and graph forget them,"
            " and the unreadable list is the scans again"
        ),
        find=_not_documents,
        repair=_repair_not_documents,
    ),
    Ailment(
        name="uncounted-pages",
        what=(
            "PDFs with text whose page count no parse recorded (read before"
            " the worker kept one): thin-texts cannot weigh them"
        ),
        fix=(
            "count them: each PDF opened once, meta.pages written (needs"
            " pymupdf on the door; the worker records it for every parse since)"
        ),
        find=_uncounted_pages,
        repair=_repair_uncounted_pages,
    ),
    Ailment(
        name="thin-texts",
        what=(
            "PDFs of five pages or more with under 100 bytes of text a page:"
            " scans whose text layer is the cover's, read as if it were the"
            " book (the front matter of a Google Books scan, say); only the"
            " PDFs with a page count are weighed (uncounted-pages)"
        ),
        fix=(
            "ask for OCR over all of them (`prax reread --extractor"
            " pymupdf4llm-ocr --thin`; the OCR page budget, parse.ocr_max_pages,"
            " must cover the longest) or the vision model; nothing to repair"
            " in the store"
        ),
        find=_thin_texts,
        offers=(
            {
                "label": "ask OCR for all of them",
                "extractor": "pymupdf4llm-ocr",
                "thin": 100,
            },
        ),
    ),
    Ailment(
        name="unreadable-documents",
        what=(
            "documents every extractor here has tried and found no text in"
            " (scans without a text layer): they wait, and are not tried again"
        ),
        fix=(
            "ask for OCR or the vision model on the document's page ('read"
            " again…', howto 3b) or over all of them at once (`prax reread"
            " --unreadable`), or retire it; nothing to repair in the store"
        ),
        find=_unreadable_documents,
        offers=(
            {
                "label": "ask OCR for all of them",
                "extractor": "pymupdf4llm-ocr",
                "unreadable": True,
            },
            {
                "label": "ask the vision model for their scanned pages",
                "extractor": "vision-pages",
                "mode": "scans",
                "unreadable": True,
            },
        ),
    ),
    Ailment(
        name="stale-extractions",
        what=(
            "documents whose extraction was made from a text a later read has"
            " replaced (marker over a pymupdf4llm text, OCR over a scan): the"
            " graph speaks of a text that is gone, and the extract step does"
            " not select them again"
        ),
        fix=(
            "move the stamp to the history so the extract step reads the new"
            " text (`prax work --steps extract --scope all`, or the nightly"
            " pass); the old reading's edges are retired when the new one is"
            " applied. A read that replaces the text does this on the way in"
            " now; these are from before"
        ),
        find=_stale_extractions,
        repair=_repair_stale_extractions,
    ),
    Ailment(
        name="unread-formulas",
        what=(
            "documents holding a display equation no model has read: the LaTeX"
            " is there and embeds to noise, and nothing says in words what the"
            " equation is, so a search for it by meaning finds nothing"
        ),
        fix=(
            "ask the formulas model for them — on one document's page ('read"
            " again… → formulas'), or over all of them at once (`prax reread"
            " --extractor formulas --unread-formulas`, howto 3h). steps.formulas"
            " in prax.yaml names the model; a local one costs only time"
        ),
        find=_unread_formulas,
        offers=(
            {
                "label": "read the formulas",
                "extractor": "formulas",
                "unread_formulas": True,
            },
        ),
    ),
    Ailment(
        name="unpolished-transcripts",
        what=(
            "videos whose transcript is the automatic one as it came — no"
            " sentences, no capitals, every filler — and the polish has not"
            " written it yet (captured before the step existed, or while its"
            " model was away)"
        ),
        fix=(
            "ask the polish for them (`prax reread --extractor polish"
            " --unpolished`; steps.polish in prax.yaml names the model — a"
            " local one costs only seconds a talk); the door asks for it by"
            " itself for every new capture"
        ),
        find=_unpolished_transcripts,
        offers=(
            {
                "label": "polish all of them",
                "extractor": "polish",
                "unpolished": True,
            },
        ),
    ),
    Ailment(
        name="unread-figures",
        what=(
            "documents holding a figure no model has read: the text has the"
            " picture and its caption, but nothing that says what it shows,"
            " so a search cannot find it and an answer cannot use it"
        ),
        fix=(
            "ask the vision model for them — on one document's page ('read"
            " again… → figures'), or over all of them at once (`prax reread"
            " --extractor figures --unread-figures`, howto 3b). A local"
            " model costs only time, about four seconds a figure; nothing"
            " is wrong in the store"
        ),
        find=_unread_figures,
        offers=(
            {
                "label": "read the captioned ones",
                "extractor": "figures",
                "unread_figures": True,
            },
            {
                "label": "read every image",
                "extractor": "figures",
                "mode": "all",
                "unread_figures": True,
            },
        ),
    ),
    Ailment(
        name="unmapped-glyphs",
        what=(
            "documents whose text holds ligature glyphs (ﬁ, ﬂ) or Symbol-font"
            " code points (=, ∈, α as private-use characters) from before"
            " every text was cleaned: boxes on screen, words search cannot match"
        ),
        fix=(
            "re-index each from its own text, cleaned (prax.glyphs); unchanged"
            " chunks keep their vectors"
        ),
        find=_glyph_documents,
        repair=_repair_glyphs,
    ),
    Ailment(
        name="stale-parses",
        what=(
            "documents whose text came from an extractor prax has revised"
            " since (the figures it finds now, a cleaner reading): a re-read"
            " would produce something new, or say 'same'"
        ),
        fix=(
            "moves the stamp where an annotation in the history already"
            " made the revision's change (figure references placed); the"
            " rest a backlog pass reads a few at a time (`prax work --scope"
            " all`, nightly)"
        ),
        find=_stale_parses,
        repair=_repair_stale_parses,
    ),
    Ailment(
        name="chunks-without-vectors",
        what="chunks the current embedding model has no vector for",
        fix="run a worker (`prax work`); nothing to repair in the store",
        find=_unembedded_chunks,
    ),
)

BY_NAME = {a.name: a for a in AILMENTS}


def _chosen(only: list[str] | None) -> list[Ailment]:
    if not only:
        return list(AILMENTS)
    unknown = [n for n in only if n not in BY_NAME]
    if unknown:
        raise ValueError(
            f"no such ailment: {', '.join(unknown)}; known: {', '.join(BY_NAME)}"
        )
    return [BY_NAME[n] for n in only]


@_reading
def health(
    con: sqlite3.Connection, *, only: list[str] | None = None, examples: int = EXAMPLES
) -> dict[str, Any]:
    """What is wrong with the store right now: every ailment, how many rows
    it finds and a few of them to look at. Reads only."""
    found = []
    for ailment in _chosen(only):
        rows = ailment.find(con)
        found.append(
            {
                "name": ailment.name,
                "what": ailment.what,
                "fix": ailment.fix,
                "repairable": ailment.repairable,
                "offers": list(ailment.offers),
                "count": len(rows),
                "capped": len(rows) >= CAP,
                "examples": rows[:examples],
            }
        )
    return {
        "ailments": found,
        "found": sum(1 for f in found if f["count"]),
        "checked_at": con.execute(f"SELECT {_NOW}").fetchone()[0],
    }


def heal(con: sqlite3.Connection, *, only: list[str] | None = None) -> dict[str, Any]:
    """Repair what the named ailments find, through the store's own
    functions: edges are invalidated (never deleted), review items are
    resolved, job rows are closed. Announces itself as a job.

    ``only`` names the ailments to repair; without it every repairable one
    runs. A report-only ailment is skipped and says so."""
    chosen = _chosen(only)
    out: dict[str, Any] = {}
    done = 0
    with Job(con, "heal", note=", ".join(a.name for a in chosen)) as job:
        for ailment in chosen:
            rows = ailment.find(con)
            if not rows:
                continue
            if ailment.repair is None:  # a report: it says what to do
                out[ailment.name] = f"{len(rows)} to look at — {ailment.fix}"
                continue
            repaired = ailment.repair(con, rows)
            done += repaired
            out[ailment.name] = {"found": len(rows), "repaired": repaired}
            if repaired < len(rows):
                out[ailment.name]["left alone"] = len(rows) - repaired
            job.update(done=done, note=ailment.name)
    return out
