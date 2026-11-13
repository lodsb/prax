"""Who is reading (stage U) and what they may see: the viewer, the
modules it is given, the test of a personal document, the guards a read
of a document or a chunk wears, and the connections a request thread
and the door open."""

from __future__ import annotations

import functools
import itertools
import json
import sqlite3
import threading
from collections.abc import Callable, Iterable
from collections.abc import Set as AbstractSet
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from prax import config

from .locks import BUSY_TIMEOUT, CACHE_MB, JOURNAL_MB, MMAP_MB, F

# ---------------------------------------------------------------- the wall
# Who is reading (stage U). The door sets the viewer for a request made
# with a named token; none is set for the administrator's token, for the
# door's own passes, for scripts and for tests, which see everything. A
# restricted viewer does not see a document the owner marked personal (or
# a rule suspects), nor one outside the modules its token is given, and
# every read below that takes a document or a chunk, or returns rows of
# them, answers as if such a document were not there.


@dataclass(frozen=True)
class Viewer:
    name: str
    # the modules it sees, with the ones built on them (``Ontology.within``)
    # and ``unassigned`` for documents without a set; None: every module
    domains: frozenset[str] | None = None
    personal: bool = False  # sees documents marked or suspected personal
    # what it may do (migration 58): a reader reads, a contributor also
    # writes; which routes each may call is the door's (prax.wall.auth)
    role: str = "reader"


VIEWER: ContextVar[Viewer | None] = ContextVar("prax_viewer", default=None)
UNASSIGNED = "unassigned"


# Which documents belong to a domain, said once. Every caller names the
# modules it means (one domain, or it and the modules built on it:
# ``Ontology.within``) and whether a document with no set counts: it is
# read against every module, so a search keeps it, while a module's own
# graph leaves it out or the whole library would come back. An empty
# list is no set.


def decode_domains(raw: str | None) -> list[str] | None:
    """A document's ``meta.domains`` as stored (JSON) to its list, None
    for no set."""
    if not raw:
        return None
    got = json.loads(raw)
    return list(got) if got else None


def domain_sets(con: sqlite3.Connection) -> list[tuple[str, list[str] | None]]:
    """The distinct domain sets the documents hold, as stored and decoded
    (None for an empty list stored, which is no set): a dozen on the
    library, read through ``idx_documents_domains``. The one read under
    ``domain_clause`` and the extraction stamps' version case (structure
    review finding 10)."""
    return [
        (str(raw), decode_domains(raw))
        for (raw,) in con.execute(
            "SELECT DISTINCT json_extract(meta, '$.domains') FROM documents"
            " WHERE json_extract(meta, '$.domains') IS NOT NULL"
        )
    ]


def holds_domain(raw: str | None, names: Iterable[str], *, unset: bool) -> bool:
    """Whether a document whose ``meta.domains`` is ``raw`` belongs to one
    of ``names``; one with no set belongs when ``unset``."""
    held = decode_domains(raw)
    if held is None:
        return unset
    return not set(held).isdisjoint(names)


def domain_clause(
    con: sqlite3.Connection,
    names: Iterable[str],
    *,
    unset: bool,
    alias: str = "d",
) -> tuple[str, list[str]]:
    """The SQL of ``holds_domain`` for a query over ``documents <alias>``:
    `` AND (…)`` and its arguments. No names and not ``unset`` keeps
    nothing.

    The store holds a dozen distinct domain sets. They are read first
    through ``idx_documents_domains``, and the query keeps the documents
    whose set is one of those that hold a name, found through the same
    index once. Reading each document's set with ``json_each`` cost a
    search scoped to a domain 550 ms (docs/research-database-layout.md)."""
    wanted = set(names)
    held: list[str] = []
    none: list[str] = []  # an empty list stored is no set too
    for raw, decoded in domain_sets(con):
        if decoded is None:
            none.append(raw)
        elif wanted and not wanted.isdisjoint(decoded):
            held.append(raw)
    sets = held + (none if unset else [])
    parts: list[str] = []
    if sets:
        parts.append(
            "json_extract(meta, '$.domains') IN (" + ",".join("?" * len(sets)) + ")"
        )
    if unset:
        parts.append("json_extract(meta, '$.domains') IS NULL")
    if not parts:
        return " AND 0", []
    return (
        f" AND {alias}.id IN (SELECT id FROM documents WHERE {' OR '.join(parts)})",
        sets,
    )


K = TypeVar("K")
V = TypeVar("V")


class Memo(Generic[K, V]):
    """The newest ``kept`` answers of a read, by key, under a lock; the
    answer is computed outside it, so two threads may compute one twice
    and the second keeps it. A key that holds ``store_state`` never
    outlives a write. The hidden set, the media members, the People list
    and the domain shares of a search each wrote this until 2026-11-09
    (structure review findings 12 and 30); the hubs (a stamp and an age)
    and the path index (built under its lock, a 150 MB peak) keep rules
    of their own."""

    def __init__(self, kept: int) -> None:
        self.kept = kept
        self._held: dict[K, V] = {}
        self._lock = threading.Lock()

    def get(self, key: K, compute: Callable[[], V]) -> V:
        with self._lock:
            if key in self._held:
                return self._held[key]
        got = compute()
        with self._lock:
            self._held[key] = got
            while len(self._held) > self.kept:
                self._held.pop(next(iter(self._held)))
        return got

    def clear(self) -> None:
        with self._lock:
            self._held.clear()


# (viewer, connection, state) -> hidden set
_hidden_memo: Memo[tuple[Any, ...], frozenset[int]] = Memo(16)


class Connection(sqlite3.Connection):
    """A store connection: an ordinary one with a ``serial``, a number no
    other connection of this process has had. ``id(con)`` is not that: a
    request thread's connection is freed when the thread ends and the next
    one is often made at the same address, starting at the same
    ``data_version`` and ``total_changes``, so a memo keyed by the address
    served the hidden set of before a document was marked personal (the
    safety review of 2026-11-11)."""

    _serials = itertools.count(1)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.serial = next(Connection._serials)
        # SQLite's lower() and LIKE fold ASCII alone: "Über" is not "über"
        self.create_function("prax_fold", 1, _fold, deterministic=True)


def _fold(text: Any) -> str | None:
    """A text in the case it is compared in, any script (``str.casefold``);
    SQL's ``prax_fold``."""
    return None if text is None else str(text).casefold()


def store_state(con: sqlite3.Connection) -> tuple[Any, ...]:
    """What a read's memo is keyed by besides its arguments: the file, this
    connection (its ``serial``; a connection ``connect`` did not make falls
    back to its address, which a later one may reuse), ``PRAGMA
    data_version`` (another connection committed) and this connection's
    own ``total_changes`` (it wrote), so a memo never outlives a write."""
    return (
        str(con.execute("PRAGMA database_list").fetchone()[2]),
        getattr(con, "serial", None) or ("id", id(con)),
        int(con.execute("PRAGMA data_version").fetchone()[0]),
        con.total_changes,
    )


def doc_visible(doc: Any, hidden: AbstractSet[int]) -> bool:
    """Whether what a row says is the viewer's to see by its document: it
    has none (a person's fact, a rule's), or one not ``hidden``. The one
    test of a fact's or a name's ``source_doc``; ``visible_edges`` adds
    the premises of a derived edge (structure review finding 39)."""
    return doc is None or int(doc) not in hidden


def hidden_documents(con: sqlite3.Connection) -> frozenset[int]:
    """The documents the current viewer may not see; empty for none.

    Read once for a viewer and a state of the store: a search asked it
    two or three times and each read scanned every document (18 ms for a
    module-limited token; the quality review of 2026-10-05). The state is
    ``PRAGMA data_version`` (another connection committed) and this
    connection's own ``total_changes`` (it wrote), so a write is never
    missed."""
    viewer = VIEWER.get()
    if viewer is None:
        return frozenset()
    return _hidden_memo.get(
        (viewer, *store_state(con)), lambda: _hidden_read(con, viewer)
    )


def personal_sql(alias: str) -> str:
    """The one test of a document being personal for a reason other than
    its domains, as SQL over the documents row ``alias``: marked
    (``sensitivity``, suspected or personal), or a picture a person named
    a face in, even one they opened, since who a face is (and so who was
    where) is for a token with ``personal`` only (plan item AT's wall,
    decided 2026-10-28). Every reader of personal-ness asks this: the
    wall, the rule pass, the domain rules, the counts. A new reason is one
    more line here."""
    return (
        f"({alias}.sensitivity IS NOT NULL OR EXISTS (SELECT 1 FROM faces pf"
        " JOIN face_names pn ON pn.face_id = pf.id"
        f" WHERE pf.doc_id = {alias}.id AND pn.ended_at IS NULL"
        " AND pn.verdict = 'person'))"
    )


def _hidden_read(con: sqlite3.Connection, viewer: Viewer) -> frozenset[int]:
    out: set[int] = set()
    if not viewer.personal:
        out |= {
            int(r[0])
            for r in con.execute(
                f"SELECT id FROM documents d WHERE {personal_sql('d')}"
            )
        }
    if viewer.domains is not None:
        unset = UNASSIGNED in viewer.domains
        for doc_id, raw in con.execute(
            "SELECT id, json_extract(meta, '$.domains') FROM documents"
        ):
            if not holds_domain(raw, viewer.domains, unset=unset):
                out.add(int(doc_id))
    return frozenset(out)


def document_hidden(con: sqlite3.Connection, doc_id: int) -> bool:
    """Whether the current viewer may not see this document."""
    viewer = VIEWER.get()
    if viewer is None:
        return False
    row = con.execute(
        f"SELECT {personal_sql('d')}, json_extract(d.meta, '$.domains')"
        " FROM documents d WHERE d.id = ?",
        (int(doc_id),),
    ).fetchone()
    if row is None:
        return False  # not there at all: the read says so itself
    if row[0] and not viewer.personal:
        return True
    if viewer.domains is not None:
        unset = UNASSIGNED in viewer.domains
        return not holds_domain(row[1], viewer.domains, unset=unset)
    return False


def chunk_hidden(con: sqlite3.Connection, chunk_id: int) -> bool:
    if VIEWER.get() is None:
        return False
    row = con.execute("SELECT doc_id FROM chunks WHERE id = ?", (int(chunk_id),))
    got = row.fetchone()
    return got is not None and document_hidden(con, int(got[0]))


def _guards(kind: str, empty: Callable[[], Any]) -> Callable[[F], F]:
    """A read that takes one document (``kind`` "doc") or chunk ("chunk")
    as its first argument after the connection answers ``empty()`` when
    the current viewer may not see it: as if it were not there."""
    hidden = document_hidden if kind == "doc" else chunk_hidden

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(con: sqlite3.Connection, key: int, *a: Any, **k: Any) -> Any:
            if VIEWER.get() is not None and hidden(con, key):
                return empty()
            return fn(con, key, *a, **k)

        return wrapper  # type: ignore[return-value]

    return deco


DOC_KEYS = ("doc_id", "source_doc")


def scrub(value: Any, hidden: frozenset[int]) -> Any:
    """``value`` with every dict in a list that names a hidden document
    (by ``doc_id`` or ``source_doc``) taken out, at any depth; a dict keyed
    by a hidden document id loses that key."""
    if not hidden:
        return value
    if isinstance(value, list):
        return [
            scrub(v, hidden)
            for v in value
            if not (
                isinstance(v, dict)
                and any(v.get(k) in hidden for k in DOC_KEYS if v.get(k) is not None)
            )
        ]
    if isinstance(value, dict):
        return {
            k: scrub(v, hidden)
            for k, v in value.items()
            if not (isinstance(k, int) and k in hidden)
        }
    return value


def _scrubbed(fn: F) -> F:
    """A read whose rows name documents (by ``doc_id`` or ``source_doc``)
    loses the rows of documents the current viewer may not see."""

    @functools.wraps(fn)
    def wrapper(con: sqlite3.Connection, *a: Any, **k: Any) -> Any:
        out = fn(con, *a, **k)
        if VIEWER.get() is None:
            return out
        return scrub(out, hidden_documents(con))

    return wrapper  # type: ignore[return-value]


_local = threading.local()


def thread_connection() -> sqlite3.Connection:
    """This thread's own connection to the store (opened on first use,
    kept for the thread's life). The door's request threads read on their
    own connections; writes are still one at a time behind ``_LOCK``, and
    the schema is applied once by the process's main connection."""
    con = getattr(_local, "con", None)
    if con is None:
        con = connect()
        _local.con = con
    return con


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(
        path, check_same_thread=False, timeout=BUSY_TIMEOUT, factory=Connection
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    cache = config.whole("door.sqlite_cache_mb", "PRAX_SQLITE_CACHE_MB", CACHE_MB)
    mmap = config.whole("door.sqlite_mmap_mb", "PRAX_SQLITE_MMAP_MB", MMAP_MB)
    con.execute(f"PRAGMA cache_size = -{max(cache, 0) * 1024}")
    con.execute(f"PRAGMA mmap_size = {max(mmap, 0) * 1024 * 1024}")
    journal = config.whole(
        "door.sqlite_journal_mb", "PRAX_SQLITE_JOURNAL_MB", JOURNAL_MB
    )
    con.execute(f"PRAGMA journal_size_limit = {max(journal, 0) * 1024 * 1024}")
    return con
