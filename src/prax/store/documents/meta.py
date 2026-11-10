"""A document's meta and its field, what a search reads a document as
(title, kind, summary, what its chapters are about), and the writes that
change them."""

from __future__ import annotations

import copy
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from prax.graph import ontology
from prax.text import dates, language, mimes

from ..base import (
    ASIDE_KINDS,
    LIVE,
    DocumentMeta,
    MetaLike,
    NotFound,
    _committing,
    _guards,
    _reading,
    _serialized,
    check_meta,
    meta_of,
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


def bounded_histories(meta: MetaLike, keep: int) -> dict[str, int]:
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


def _document_meta(
    con: sqlite3.Connection, doc_id: int, *columns: str
) -> tuple[sqlite3.Row, dict[str, Any]]:
    """A document's row (its meta and ``columns``) and its meta decoded;
    NotFound when there is no such document."""
    row = con.execute(
        f"SELECT {', '.join(('meta', *columns))} FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"no such document: {doc_id}")
    return row, meta_of(row["meta"])


@_reading
def get_meta(con: sqlite3.Connection, doc_id: int) -> DocumentMeta:
    """The document's ``meta`` JSON without touching the text artifact."""
    return cast(DocumentMeta, _document_meta(con, doc_id)[1])


# the columns a meta write may change with it: the wall's mark
# (``sensitivity``, decided with the cues in ``meta.private``) and the
# title (with ``meta.title_history``)
_META_COLUMNS = ("sensitivity", "title")


def _put_meta(
    con: sqlite3.Connection, doc_id: int, meta: MetaLike, **columns: Any
) -> None:
    """Write a document's meta back, inside the caller's transaction: the
    read-modify-write of every store function that changes a key of it,
    with any of ``_META_COLUMNS`` that change with it. It refreshes
    nothing; a caller that changes what the document field indexes
    (title, kind, summary) refreshes it itself."""
    unknown = set(columns) - set(_META_COLUMNS)
    if unknown:
        raise ValueError(f"not a column a meta write sets: {sorted(unknown)}")
    check_meta(meta)
    bounded_histories(meta, HISTORY_KEEP)
    sets = "".join(f", {c} = ?" for c in columns)
    con.execute(
        f"UPDATE documents SET meta = ?{sets} WHERE id = ?",
        (json.dumps(meta), *columns.values(), doc_id),
    )


def _set_meta(
    con: sqlite3.Connection,
    doc_id: int,
    meta: MetaLike,
    *,
    title: str | None = None,
    source_url: str | None = None,
) -> None:
    """Replace ``meta`` (and optionally title / source_url) of a document.

    Importers use this to merge provenance when a known hash turns up again
    under another source record.
    """
    check_meta(meta)
    bounded_histories(meta, HISTORY_KEEP)
    cur = con.execute(
        "UPDATE documents SET meta = ?, title = COALESCE(?, title),"
        " source_url = COALESCE(?, source_url) WHERE id = ?",
        (json.dumps(meta), title, source_url, doc_id),
    )
    if cur.rowcount == 0:
        raise NotFound(f"no such document: {doc_id}")
    _refresh_document_field(con, doc_id)


set_meta = _serialized(_committing(_set_meta))


def _update_meta(
    con: sqlite3.Connection,
    doc_id: int,
    change: Callable[[DocumentMeta], MetaLike | None] | None = None,
    *,
    keys: MetaLike | None = None,
    title: str | None = None,
    source_url: str | None = None,
) -> DocumentMeta:
    """Change a document's meta in one write: read it, hand it to
    ``change`` (which changes it in place, or returns the meta to keep),
    set ``keys`` over it, and write it back when it changed, the document
    field with it. A write between a separate ``get_meta`` and
    ``set_meta`` is lost; one in here waits. ``change`` must not call the
    store. Returns the meta."""
    meta = cast(DocumentMeta, _document_meta(con, doc_id)[1])
    before = copy.deepcopy(meta)
    got = change(meta) if change is not None else None
    if got is not None:
        meta = cast(DocumentMeta, got)
    meta.update(cast(DocumentMeta, keys or {}))
    if meta != before or title is not None or source_url is not None:
        _set_meta(con, doc_id, meta, title=title, source_url=source_url)
    return meta


update_meta = _serialized(_committing(_update_meta))


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
            f" AND {_due('published')}"
            f" AND {LIVE}"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    ]


@_reading
def world_dates_needed(con: sqlite3.Connection, limit: int = 200) -> list[int]:
    """Documents with a text the ``worlddates`` step has not read, or read
    when the text was another: newest first, like the ``dates`` step."""
    return [
        r[0]
        for r in con.execute(
            "SELECT id FROM documents WHERE text_hash IS NOT NULL"
            f" AND {_due('worlddates')}"
            f" AND {LIVE}"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    ]


@_serialized
def stamp_world_dates(
    con: sqlite3.Connection, doc_id: int, stamp: dict[str, Any]
) -> None:
    """What the ``worlddates`` step read of a document and wrote
    (``meta.world_dates``), with the text's hash: a new text is read again."""
    meta = get_meta(con, doc_id)
    meta["world_dates"] = stamp
    _put_meta(con, doc_id, meta)
    con.commit()


def keep_summary(meta: MetaLike, text: str, *, lang: str | None = None) -> str | None:
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
    _, meta = _document_meta(con, doc_id)
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


# The steps that mark a document whose text they read and kept nothing
# from (``meta.<step>_tried``), so it is not handed out to the same model
# for ever: dates found no date, genres no label, summaries no translation,
# titles no better title. The step's own result clears its mark, and so
# does a new text (``index_text``, ``TRIED_ON_TEXT``): a scan that failed
# on its OCR layer is asked again once marker has read it.
TRIED_STEPS = ("genres", "published", "summary", "titles")


def _tried_key(step: str) -> str:
    if step not in TRIED_STEPS:
        raise ValueError(f"not a step that marks a document tried: {step}")
    return f"{step}_tried"


@dataclass(frozen=True)
class Stamp:
    """Where a step records that it read a document, and so when it reads
    the document again (structure review finding 1). ``key`` is the record
    in the meta. A step that keeps what it read keeps it there with the
    hash of the text it read (``on_text``: a new text is read again), the
    version of its reading (``version``, the record's key for it: an older
    one is read again), or both. A step that read a text and kept nothing
    leaves a mark instead (``tried``, ``step_tried``), which a new text
    clears (``TRIED_ON_TEXT``) and its own result replaces."""

    key: str
    on_text: bool = False
    version: str | None = None
    tried: bool = False


# Every step whose record says whether a document waits for it: the one
# place a due query or a count of the waiting is built from (``_due``). A
# new step names its record here rather than writing the test again in a
# shape of its own.
STAMPS: dict[str, Stamp] = {
    "photos": Stamp("photo", version="v"),
    "faces": Stamp("faces", version="v"),
    "geocode": Stamp("place", version="v"),
    "worlddates": Stamp("world_dates", on_text=True),
    "sections": Stamp("sections", on_text=True),
    "quality": Stamp("quality", on_text=True, version="version"),
    **{step: Stamp(f"{step}_tried", tried=True) for step in TRIED_STEPS},
}


def _due(step: str, alias: str | None = None) -> str:
    """The SQL that keeps the documents ``step`` reads (``STAMPS``): no
    record, a record of another text, or one of an older version, whose
    test takes the step's current version as one ``?``; for a step that
    marks a document tried, no mark. ``NOT`` it is what the step has read."""
    stamp = STAMPS[step]
    meta = f"{alias}.meta" if alias else "meta"
    text = f"{alias}.text_hash" if alias else "text_hash"
    if stamp.tried:
        return f"json_extract({meta}, '$.{stamp.key}') IS NULL"
    tests = []
    if stamp.on_text:
        tests.append(f"json_extract({meta}, '$.{stamp.key}.text_hash') IS NOT {text}")
    if stamp.version:
        tests.append(
            f"coalesce(json_extract({meta}, '$.{stamp.key}.{stamp.version}'), 0) < ?"
        )
    return "(" + " OR ".join(tests) + ")"


def _of_this_text(step: str, alias: str | None = None) -> str:
    """The SQL that keeps the documents whose record of ``step`` was read
    from the text they hold now (an ``on_text`` step of ``STAMPS``): what
    it found of an earlier text says nothing of this one."""
    stamp = STAMPS[step]
    if not stamp.on_text:
        raise ValueError(f"{step} does not record the text it read")
    meta = f"{alias}.meta" if alias else "meta"
    text = f"{alias}.text_hash" if alias else "text_hash"
    return f"json_extract({meta}, '$.{stamp.key}.text_hash') = {text}"


def step_tried(
    con: sqlite3.Connection,
    doc_id: int,
    step: str,
    run: str | None,
    why: str | None = None,
) -> None:
    """``step`` read the document and kept nothing: mark it, ``{run, why,
    at}`` (``why`` cut to 200 characters), so the step passes it by until
    its text changes (``TRIED_STEPS``)."""
    entry: dict[str, Any] = {"run": run, "at": now()}
    if why:
        entry["why"] = why[:200]
    update_meta(con, doc_id, keys={_tried_key(step): entry})


def _retitle_document(
    con: sqlite3.Connection,
    doc_id: int,
    title: str,
    *,
    source: str,
    run: str | None,
    confidence: str | None,
) -> str | None:
    """The document's half of ``store.retitle``: the title replaced, the old
    one kept in ``meta.title_history`` with its source, ``meta.title_source``
    naming who wrote the new one (an importer keeps its hands off a title
    it did not write). Returns the old title, or None when it was the same.
    Does not commit: the graph's half follows in the same write."""
    if not title:
        raise ValueError("a title cannot be empty")
    row, meta = _document_meta(con, doc_id, "title")
    old = row["title"] or ""
    if old == title:
        return None
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
    _put_meta(con, doc_id, meta, title=title)
    return str(old)


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
# How sure a label must be to count: a person's labels are 1.0 (or carry
# no p), a model's their calibrated probability (stage Z,
# docs/eval/genres-2026-09-29.md). One line for every reader of a label: a
# recalibration moves it once
LABEL_P = 0.5


def sure_labels(given: Any, key: str, *, at_least: float = LABEL_P) -> list[str]:
    """The names of the labels in ``given`` (``meta.genres``, ``meta.subjects``:
    ``[{key: name, "p": …}]``) with ``p`` of ``at_least`` or more; one
    without a ``p``, or with ``p`` null, is a person's and counts. The
    sample of the Review page, the domain rules, a document's field and
    marker's choice each read it with a constant of their own until
    2026-11-09 (structure review finding 36)."""
    out = []
    for x in given if isinstance(given, list) else []:
        if not isinstance(x, dict) or not x.get(key):
            continue
        p = x.get("p")
        if p is None or float(p) >= at_least:
            out.append(str(x[key]))
    return out


# what a document is, in the words a query would use (stage Z): the
# genre labels a person or the labeller gave, sure enough (``sure_labels``).
# The levels ("informational") say too little to help a search
_GENRE_WORDS = {"qa": "question and answer", "source": "source code"}


def _genre_words(meta: dict[str, Any]) -> list[str]:
    levels = {lv.name for lv in ontology.genres().levels}
    return [
        _GENRE_WORDS.get(g, g)
        for g in sure_labels(meta.get("genres"), "genre")
        if g not in levels
    ]


_VISION_SOURCES = ("claude-vision", "vision/")
_PAGE_KIND_WORDS = {
    "addendum": "note page",
    "project": "project page",
    "synthesis": "synthesis page",
}


def _kind_words(
    meta: dict[str, Any], mime: str, first_kind: str | None, source: str
) -> list[str]:
    """What kind of document it is, in the words a person searches by:
    its medium, a note, a wiki page, source code, an image description,
    its genres."""
    words: list[str] = []
    if mimes.is_picture(mime):
        words.append("image")
    elif mime in mimes.DOCUMENT_IMAGES:
        words.append("scanned document")
    elif mime in _KIND_WORDS:
        words.append(_KIND_WORDS[mime])
    elif mime.startswith("audio/"):
        words.append("audio recording")
    elif mime.startswith("video/"):
        words.append("video")
    if (meta.get("zotero") or {}).get("kind") == "note":
        words.append("note")
    page = meta.get("page") or {}
    if page.get("kind"):
        words.append(_PAGE_KIND_WORDS.get(page["kind"], "wiki page"))
    if first_kind == "code":
        words.append("source code")
    if source.startswith(_VISION_SOURCES):
        words.append("image description")
    return words + [w for w in _genre_words(meta) if w not in words]


def _folder_words(con: sqlite3.Connection, sha256: str) -> str:
    """A referenced file has no text until something reads it: the folders
    it lies in are what a person would search for it by."""
    place = con.execute(
        "SELECT path FROM places WHERE sha256 = ? AND gone IS NULL"
        " ORDER BY last_seen DESC LIMIT 1",
        (sha256,),
    ).fetchone()
    if place and "/" in place[0]:
        return str(place[0].rsplit("/", 1)[0].replace("/", " "))
    return ""


def _record_words(meta: dict[str, Any]) -> list[str]:
    """Its record's creators, and its venue with the year."""
    out = []
    creators = [c.get("name") for c in meta.get("creators", []) if c.get("name")]
    if creators:
        out.append("by " + ", ".join(creators[:6]))
    fields = meta.get("fields") or {}
    venue = fields.get("publicationTitle") or fields.get("proceedingsTitle")
    bits = [b for b in (venue, str(meta.get("date") or "")[:4]) if b]
    if bits:
        out.append(" ".join(bits))
    return out


def _what_it_shows(con: sqlite3.Connection, doc_id: int) -> str:
    """A vision reading's "What it shows" section, capped."""
    shows = con.execute(
        "SELECT text FROM chunks WHERE doc_id = ?"
        " AND heading LIKE '%What it shows%' ORDER BY seq LIMIT 1",
        (doc_id,),
    ).fetchone()
    if not shows:
        return ""
    lines = [ln for ln in shows["text"].splitlines() if not ln.startswith("#")]
    return " ".join(lines).strip()[:1200]


@_guards("doc", lambda: None)
def document_field(con: sqlite3.Connection, doc_id: int) -> str | None:
    """The retrieval field of a document, or None when it does not exist."""
    row = con.execute(
        """
        SELECT d.title, d.mime, d.meta, d.hash, d.held,
               (SELECT kind FROM chunks c WHERE c.doc_id = d.id ORDER BY seq LIMIT 1)
                   AS first_kind
        FROM documents d WHERE d.id = ?
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    meta = meta_of(row["meta"])
    if meta.get("retired"):
        return None  # a retired document has no retrieval field
    source = str(meta.get("text_source") or "")
    words = _kind_words(meta, row["mime"] or "", row["first_kind"], source)
    parts = [row["title"] or "", " ".join(words)]
    if not row["held"]:
        parts.append(_folder_words(con, row["hash"]))
    parts.append(_photo_words(meta))
    parts += _record_words(meta)
    if meta.get("summary"):
        parts.append(str(meta["summary"]))
    if source.startswith(_VISION_SOURCES):
        parts.append(_what_it_shows(con, doc_id))
    parts.extend(_section_lines(meta))
    return "\n".join(p for p in parts if p.strip())


SECTION_FIELD_CHARS = 3_000  # of section summaries in one document's field


def _photo_words(meta: dict[str, Any]) -> str:
    """A photo's EXIF for its field: the month it was taken in as words a
    person types ("2019 05 may 2019-05-03"), the town, region and country
    its position lies in (``meta.place``) and the camera; empty for a
    document that is not a read photo."""
    photo = meta.get("photo") or {}
    taken = str((meta.get("taken") or {}).get("date") or "")
    bits: list[str] = []
    if len(taken) >= 7 and taken[:4].isdigit() and taken[5:7].isdigit():
        month = int(taken[5:7])
        if 1 <= month <= 12:
            # the month as a person of the library's language types it
            spelled = dates.spelled_month(month, language.canonical()).lower()
            bits += [taken[:4], taken[5:7], spelled]
            bits += [taken[:10]] if len(taken) >= 10 else []
    place = meta.get("place") or {}
    where = [str(place[k]) for k in ("name", "region", "country") if place.get(k)]
    if where:
        bits += ["in"] + list(dict.fromkeys(where))
    if photo.get("camera"):
        bits.append(str(photo["camera"]))
    return "taken " + " ".join(bits) if bits else ""


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
def set_quality(
    con: sqlite3.Connection,
    doc_id: int,
    findings: list[dict[str, Any]],
    *,
    version: int,
    text_hash: str,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """What the checks of a text found (``parsers.quality``, stage AO),
    kept in ``meta.quality`` with the text artifact they read
    (``text_hash``), so a new text makes them stale rather than wrong.
    ``profile``, when the original was read for them, is kept as
    ``meta.profile`` (``quality.assess``). None, and nothing kept, when
    the document holds another text by now."""
    row, meta = _document_meta(con, doc_id, "text_hash")
    if row["text_hash"] != text_hash:
        return None  # a text that landed since: its own checks will run
    meta["quality"] = held = quality_record(findings, text_hash, version)
    if profile is not None:
        meta["profile"] = profile
    _put_meta(con, doc_id, meta)
    con.commit()
    return held


def quality_record(
    findings: list[dict[str, Any]], text_hash: str | None, version: int
) -> dict[str, Any]:
    """``meta.quality``: the findings, the checks' version, when, and the
    text they read (``set_quality`` and the ``quality`` pass)."""
    return {
        "version": version,
        "at": now(),
        "text_hash": text_hash,
        "findings": findings,
    }


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
    row, meta = _document_meta(con, doc_id, "text_hash")
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
def sections_needed(con: sqlite3.Connection, *, limit: int = 200) -> list[int]:
    """Long documents whose sections nobody has read, or has read from a
    text that has since been replaced. Longest first: a book gains most."""
    from prax.writing import sections as sec

    rows = con.execute(
        "SELECT id FROM documents WHERE text_len >= ?"
        f" AND {LIVE} AND text_hash IS NOT NULL"
        f" AND {_due('sections')}"
        " ORDER BY text_len DESC LIMIT ?",
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
        raise NotFound(f"no such document: {doc_id}")
    con.commit()
