"""What every part of the store shares: the vector dimension, the chunk
kinds set aside, the spelling of a live document, the shapes a read hands
out (``SearchHit``, ``Document``), the declared meta keys and their
check, the clock's one shape, and batching ids into ``IN`` lists."""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Required, TypedDict

from prax import packs

VEC_DIM = 384  # dimension of the vector index; another dimension is a new index file


# chunk kinds the search leaves out unless asked for by kind, and the
# embed step never vectorises: a reference entry matches every author,
# venue and year in the library and says nothing the cited paper does
# not say better — the references pass turns it into an edge instead;
# an ask block in a page is the model's own answer, and an answer must
# never be its own evidence the next time the question is asked; an
# advertisement and a comment section are what a captured page carries
# that is not the document (prax.text.furniture), and neither should answer
# a question put to the library
ASIDE_KINDS = ("reference", "ask", "ad", "comment", "figure-text") + packs.aside()
_ASIDE = (  # a legacy row without a kind is text
    " AND (c.kind IS NULL OR c.kind NOT IN ("
    + ", ".join(f"'{k}'" for k in ASIDE_KINDS)
    + "))"
)


def live(alias: str | None = None) -> str:
    """The SQL test that a document is live, not retired: the one spelling,
    which is also the WHERE of the partial index ``idx_documents_live``
    (migration 32), so a query that writes it is served by that index.
    ``alias`` is the documents table's name in the query (``d``)."""
    meta = f"{alias}.meta" if alias else "meta"
    return f"json_extract({meta}, '$.retired') IS NULL"


LIVE = live()

# a page of a list a store read answers at most, where it has no cap of
# its own: the door types those routes' limit with it, so the schema a
# client reads and what the store does agree (quality review
# 2026-11-11, finding 18)
LIST_PAGE = 200


_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"  # the same shape as now(), in SQL


class SearchHit(TypedDict, total=False):
    """One hit of ``store.search``: a passage, or a document found by its
    field alone (``chunk_id`` then the passage that holds the query's words,
    when one does). The legs build it, the fusion and the search's last
    steps add to it, and the door, ``ask``, the surfer and the evaluation
    read it. A key not listed here is a typo for mypy."""

    doc_id: Required[int]
    chunk_id: int | None
    title: str | None
    snippet: str
    score: float  # the fused score
    kind: str | None  # the chunk's kind
    heading: list[str]  # its heading path, ``short_heading``
    page: int | None
    time: int | None  # seconds into a recording
    figure: str | None  # a figure chunk's reference
    # each rank list's rank of it, None when absent from that list
    fts_rank: int | None
    fts_all_rank: int | None
    fts_rare_rank: int | None
    vec_rank: int | None
    field_rank: int | None
    dvec_rank: int | None
    domain_rank: int  # its rank in the domain prior's vote
    rerank_score: float
    published: str | None  # ``meta.published``, as precise as its source
    stale: dict[str, Any]  # ``staleness``: the state, its date, the replacement
    versions: list[int]  # older versions of its work folded under it
    cite: str  # ``cite_link``, with ``cite=``
    domains: list[str] | None  # the door's addition: the document's modules


class Document(TypedDict):
    """One document as ``get_document`` reads it: the ``documents`` row,
    its ``meta`` parsed, and a window of its text. ``text`` is
    ``[offset, offset + max_chars)``; ``text_len`` and ``truncated`` say
    whether more remains."""

    id: int
    hash: str  # sha256 of the original bytes (invariant 2)
    mime: str | None
    title: str | None
    source_url: str | None
    original_path: str | None
    text_hash: str | None  # the parsed-text artifact
    added_at: str
    parsed_at: str | None
    meta: dict[str, Any]
    text_len: int  # the column may be NULL; get_document always says it
    sensitivity: str | None  # the wall: open, suspected, personal
    held: int  # 1 when the archive holds the original, 0 when a place does
    text: str
    offset: int
    truncated: bool


class DocumentMeta(TypedDict, total=False):
    """What ``documents.meta`` may hold: every top-level key, with the
    shape of its value and who writes it. ``meta`` is an open JSON object
    on purpose (a new source adds keys before it earns a column), so this
    is a catalogue rather than a schema: a key the code writes that is not
    here is refused in the test suite (``checks.strict_meta``) and logged
    once on a host. ``docs/meta.md`` is the reference, and a test holds
    the two to the same keys. A nested object's own keys are its writer's
    business, typed ``dict[str, Any]`` until one earns a shape."""

    # where it came from: the importer or capture, at register
    source: str  # zotero, capture, drop, feed, github, project, claude, citations…
    origin: dict[str, Any]  # a drop folder's host and path
    capture: dict[str, Any]  # the extension's at, session, by, note, mode
    requested_url: str  # the URL asked for, where the capture landed elsewhere
    previous_capture: int  # the document this capture of the same URL follows
    recaptured: list[dict[str, Any]]  # later captures folded into this one
    parser: str  # the parser a document names for itself (video)
    video: dict[str, Any]  # the player: provider, id, url, chapters, captions…
    polished: bool  # an automatic transcript punctuated (the polish reading)
    zotero: dict[str, Any]  # the Zotero item and attachment, importers.zotero
    fields: dict[str, Any]  # Zotero's item fields, as Zotero names them
    creators: list[dict[str, Any]]  # Zotero's creators
    collections: list[str]  # Zotero's collection paths
    date: str | None  # the record's date as written (Zotero)
    abstract: str | None  # the record's abstract
    doi: str | None  # a printed or recorded DOI (Zotero, a capture's paper)
    arxiv: str  # an arXiv id
    paper: dict[str, Any]  # the extension's paper: journal, date, pdf_url
    project: dict[str, Any]  # a project's sync: key, name, path, version
    tags: list[str]  # a capture's, an importer's, a person's
    citations: dict[str, Any]  # importers.citations: OpenAlex's record
    # an importer's own block, under its source's name (importers.feed)
    github: dict[str, Any]  # importers.github: the repository
    claude: dict[str, Any]  # importers.claude: a Claude project's document
    chat: dict[str, Any]  # importers.chats: a conversation
    links: dict[str, Any]  # importers.links: a link list's entry
    # the text: parsers.queue, store.index_text
    text_source: str | None  # the parser and its version that wrote the text
    parse_history: list[dict[str, Any]]  # earlier parses (bounded)
    pages: int  # the original's page count, at parse
    ocr: dict[str, Any]  # an OCR reading's pages and progress
    reading: dict[str, Any]  # the last reading that finished (readings queue)
    lang: str  # ISO 639-1, prax.text.language
    quality: dict[str, Any]  # what the checks of the text found (parsers.quality)
    profile: dict[str, Any]  # a PDF original's profile, by its hash (quality.assess)
    markup: dict[str, Any]  # the schema.org pass: hash, facts, type, genre
    references: dict[str, Any]  # the references pass: entries, links
    private: dict[str, Any]  # the rules' cues for "personal?" (stage V)
    sensitivity: dict[str, Any]  # a decision: state, by, at; a group's adds run, before
    # a picture's own facts: the photos step (store.apply_photo)
    photo: dict[str, Any]  # v, by, at, camera, lens, gps, size, thumb, preview
    taken: dict[str, Any]  # when it was taken: date, precision, by
    # where it was taken, from its GPS position: the geocode step (store.apply_place)
    place: dict[str, Any]  # v, by, at, name, region, country, cc, km, id; or none
    # where the faces are: the faces step (store.apply_faces, the faces table)
    faces: dict[str, Any]  # v, by, at, n, from, width, height
    # what it is: the domains, the genres (stage Z)
    domains: list[str]  # the modules it is read against (invariant 9)
    domains_by: str  # a person, a rule, the labeller
    genres: list[dict[str, Any]]  # labels with p (ontology/genres.yaml)
    subjects: list[dict[str, Any]]  # labels with p (ontology/subjects.yaml)
    genres_at: str  # when they were given
    genres_by: str  # human, claude, the labeller
    genres_run: str  # the labeller's or the step's batch
    genres_model: dict[str, Any]  # a model's labels a person's save replaced
    genres_note: str  # a person's note on the genre tab
    genres_skip: str  # a person skipped it on the genre tab
    genres_tried: dict[str, Any]  # the genres step could not
    # what the model steps wrote
    title_source: str  # what gave the title: the record, the titles step, a person
    title_run: str | None  # the titles step's batch
    title_confidence: str | None  # the titles step's own
    title_history: list[dict[str, Any]]  # the titles it replaced
    titles_tried: dict[str, Any]  # the titles step found none better
    summary: str  # the summary the document field indexes
    summaries: dict[str, str]  # every summary there is, by language
    summary_lang: str  # the language ``summary`` is in
    summary_source: str  # what wrote it: an extraction, the summaries step
    summary_run: str | None  # that step's batch
    summary_tried: dict[str, Any]  # the summaries step could not
    sections: dict[str, Any]  # a long document's chapter summaries
    published: dict[str, Any]  # date, precision, by, words, confidence
    published_tried: dict[str, Any]  # the dates step found no date
    extraction: dict[str, Any]  # the last reading of the graph, its stamp
    extraction_history: list[dict[str, Any]]  # earlier readings' stamps
    extraction_stale: dict[str, Any]  # the text read is replaced, or asked again
    extraction_error: dict[str, Any]  # the last reading failed: who, under what
    promote: dict[str, Any]  # flagged for the expensive pass
    world_dates: dict[str, Any]  # the worlddates step's stamp
    # its life: retired, superseded, stale
    retired: dict[str, Any]  # at, reason, of, by, run
    status: dict[str, Any]  # a project note's own status line
    # a page (pages are documents too)
    page: dict[str, Any]  # slug, kind, revision, author; parent, parent_by; split
    asks: dict[str, Any]  # a page's ask blocks by id
    briefing: dict[str, Any]  # a briefing page's window and documents
    question: dict[str, Any]  # a question page's question


# what a writer of meta takes: the declared shape, or a plain dict a
# caller built (an importer's, a test's)
MetaLike = DocumentMeta | dict[str, Any]


# the keys of ``DocumentMeta`` a client may send in its meta: where a
# document came from and what its record says. Every other key is the
# door's own (``DOOR_WRITTEN_META``), so a key a new step declares is
# refused from a client until it is named here: several name a file of
# the archive or decide what the wall shows (the reviews of 2026-10-28).
# An importer's own block, keyed by its ``source``, is the client's too.
CLIENT_META = frozenset(
    {
        "source",
        "origin",
        "capture",
        "requested_url",
        "previous_capture",
        "parser",
        "video",
        "zotero",
        "fields",
        "creators",
        "collections",
        "date",
        "abstract",
        "doi",
        "arxiv",
        "paper",
        "project",
        "tags",
        "citations",
        "github",
        "claude",
        "chat",
        "links",
        "lang",
        "status",
    }
)
DOOR_WRITTEN_META = frozenset(DocumentMeta.__annotations__) - CLIENT_META


def door_written(meta: MetaLike | None) -> list[str]:
    """The keys of a client's ``meta`` it may not send: every key but those
    of ``CLIENT_META`` and its own importer's block (``meta[source]``)."""
    meta = meta or {}
    own = meta.get("source")
    return sorted(k for k in meta if k not in CLIENT_META and k != own)


def meta_of(raw: str | None) -> dict[str, Any]:
    """A document's ``meta`` column decoded: ``{}`` for none."""
    return dict(json.loads(raw)) if raw else {}


def undeclared_meta(meta: MetaLike) -> list[str]:
    """The top-level keys of ``meta`` that ``DocumentMeta`` does not name."""
    return sorted(k for k in meta if k not in DocumentMeta.__annotations__)


class Checks:
    """What the store checks harder in a test than on a host: one object
    read at call time, like ``retrieval.knobs``."""

    # a write of a meta key ``DocumentMeta`` does not name raises; off, it
    # is logged once a key (a host keeps what a source gave it)
    strict_meta: bool = False


checks = Checks()
_meta_logged: set[str] = set()


def check_meta(meta: MetaLike) -> None:
    """Refuse (in the tests) or log (on a host) a key nobody declared."""
    keys = undeclared_meta(meta)
    if not keys:
        return
    if checks.strict_meta:
        raise ValueError(f"meta keys not in store.DocumentMeta: {keys}")
    for k in keys:
        if k not in _meta_logged:
            _meta_logged.add(k)
            logging.getLogger("prax.store").warning("meta key %r is undeclared", k)


def now() -> str:
    """The moment, as every timestamp prax writes: UTC, ISO-8601 to the
    second, ``Z`` (``2026-09-22T01:49:03Z``). One shape, so the strings
    sort as the moments do."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def moment(epoch: float) -> str:
    """A UNIX time (a file's mtime) in the shape of ``now``."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def ago(seconds: float) -> str:
    """The moment ``seconds`` before now, in the shape of ``now``: what a
    cutoff compares a stored timestamp with. Until 2026-11-09 six callers
    wrote it by hand (structure review finding 53)."""
    return moment(time.time() - seconds)


def first_fresh_id(con: sqlite3.Connection, kind: str, *, above: int = 0) -> int:
    """The first id no row of ``kind`` was ever given, for the rows that are
    replaced whole (communities, face groups, event candidates): a page or
    an agent names one by its id, and a row that dissolved must not lend
    its id to another (``id_marks``, migration 59). ``above`` is the
    highest id the table holds now; ``mark_ids`` records what was given."""
    mark = con.execute("SELECT last FROM id_marks WHERE name = ?", (kind,)).fetchone()
    return max(int(mark[0]) if mark else 0, above) + 1


def mark_ids(con: sqlite3.Connection, kind: str, last: int) -> None:
    """Record that ids of ``kind`` up to ``last`` were given (``first_fresh_id``)."""
    con.execute(
        "INSERT INTO id_marks (name, last) VALUES (?, ?)"
        " ON CONFLICT (name) DO UPDATE SET last = max(last, excluded.last)",
        (kind, last),
    )


# ids bound in one query: under the 999 variables an older SQLite allows,
# which a distribution's build may still have (3.32 raised it to 32,766)
ID_BATCH = 500


def marks(n: int) -> str:
    """``n`` placeholders, for an ``IN (…)`` list of ``n`` values."""
    return ",".join("?" * n)


def rows_in(
    con: sqlite3.Connection,
    sql: str,
    ids: Iterable[Any],
    *,
    before: Sequence[Any] = (),
    after: Sequence[Any] = (),
    size: int = ID_BATCH,
) -> list[sqlite3.Row]:
    """The rows ``sql`` selects for every value of ``ids``, asked ``size``
    at a time. ``sql`` holds ``{ids}`` where the list's placeholders go,
    once or more (each binds the batch again, in turn), and ``before`` and
    ``after`` are the parameters on either side of them. The rows come
    batch by batch, so an ``ORDER BY`` or a ``LIMIT`` holds within a batch
    only. No ids, no rows and no query."""
    ids = list(ids)
    times = sql.count("{ids}")
    rows: list[sqlite3.Row] = []
    for start in range(0, len(ids), size):
        part = ids[start : start + size]
        rows.extend(
            con.execute(
                sql.replace("{ids}", marks(len(part))),
                (*before, *(part * times), *after),
            )
        )
    return rows


def run_name(kind: str, *after: object, unique: bool = False, sep: str = "-") -> str:
    """A run's name: its kind, the second it began (``now`` without its
    separators) and what follows (``cleanup-20261028T005739Z-clips``).
    ``unique`` adds a few random letters, for an action a person makes:
    two in one second would share a run, and one undo (``unmerge_run``,
    ``restore_run``) would take both back."""
    bits = [f"{kind}{sep}{now().replace(':', '').replace('-', '')}", *map(str, after)]
    if unique:
        bits.append(secrets.token_hex(3))
    return "-".join(bits)
