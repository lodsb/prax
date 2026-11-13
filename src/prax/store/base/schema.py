"""The numbered migrations and ``init_db``, which applies them each in
its own transaction with its Python half (``AFTER_MIGRATION``), and the
content-addressed archive (invariant 2)."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prax import config

from .locks import _serialized
from .nodes import _migration_69_nodes


def migrations() -> list[tuple[int, Path]]:
    """Numbered ``NNNN_name.sql`` files in ``prax/migrations``, ascending."""
    found = []
    for path in config.MIGRATIONS_DIR.glob("*.sql"):
        head = path.name.split("_", 1)[0]
        if not head.isdigit():
            raise ValueError(f"migration file without a number prefix: {path.name}")
        found.append((int(head), path))
    found.sort()
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(
            f"migrations must be numbered 1..N without gaps, got {numbers}"
        )
    return found


def schema_version(con: sqlite3.Connection) -> int:
    """The last migration applied (``PRAGMA user_version``); 0 = empty DB."""
    return int(con.execute("PRAGMA user_version").fetchone()[0])


@_serialized
def init_db(con: sqlite3.Connection) -> int:
    """Bring the database up to the latest migration; return its version.

    Each pending migration runs in its own transaction and stamps
    ``user_version`` on success, so an interrupted upgrade resumes cleanly.
    Applied migrations are never re-run; extend the schema by adding a new
    numbered file, never by editing an old one.
    """
    current = schema_version(con)
    known = migrations()
    latest = known[-1][0] if known else 0
    if current > latest:
        raise RuntimeError(
            f"the store is at schema version {current}, this code knows {latest}:"
            " update prax before opening it"
        )
    for number, path in known:
        if number <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        after = AFTER_MIGRATION.get(number)
        try:
            if after is None:
                con.executescript(
                    "BEGIN;\n" + sql + f"\nPRAGMA user_version = {number};\nCOMMIT;"
                )
            else:
                # its Python half in the same transaction: committed apart, a
                # stop between the two left a store at the version whose
                # half never ran (the safety review of 2026-11-11)
                con.executescript("BEGIN;\n" + sql)
                after(con)
                con.execute(f"PRAGMA user_version = {number}")
                con.commit()
        except BaseException:  # a stop too: nothing of the migration stays
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        current = number
    _drop_legacy_vec_table(con)
    return current


# the migration that gave an entity its document: the nodes written before
# it are linked by ``_migration_69_nodes`` as the store passes it
DOCUMENT_NODES = 69
# a migration's Python half, run in the migration's own transaction and
# frozen like its SQL: a half calls no rule that lives on, so a store that
# crosses the migration in a year is upgraded as one that crossed it on
# its day, and the heal brings both to the living rule (the quality review
# of 2026-11-11, C2)
AFTER_MIGRATION: dict[int, Callable[[sqlite3.Connection], Any]] = {
    DOCUMENT_NODES: _migration_69_nodes,
}


def _drop_legacy_vec_table(con: sqlite3.Connection) -> None:
    """Stage 2 first kept vectors in a sqlite-vec table; the index file
    replaced it. Drop the table when the extension is still around to do
    so (it cannot be dropped without its module); run VACUUM afterwards to
    reclaim the space (docs/howto.md)."""
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chunks_vec'"
    ).fetchone()
    if row is None:
        return
    try:
        import sqlite_vec

        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        con.execute("DROP TABLE chunks_vec")
        con.commit()
    except (ImportError, AttributeError, sqlite3.OperationalError):
        pass


# what an archive name is: a sha256, or a figure's shorter reference
_DIGEST = re.compile(r"[0-9a-f]{16,64}")


def is_digest(value: object) -> bool:
    """Whether ``value`` can name a file of the archive."""
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def archive_path(digest: str) -> Path:
    """Where an original or a text artifact lives: the one place that says
    so, for the day the archive gains a second level (CLAUDE.md). Anything
    but a digest is refused: a path in its place would name a file outside
    the archive (the review of 2026-10-28)."""
    if not is_digest(digest):
        raise ValueError(f"not an archive digest: {str(digest)[:80]!r}")
    return config.archive_dir() / digest[:2] / digest


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """sha256 of a file read in chunks (for inventories; no archiving)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _archive_bytes(data: bytes) -> str:
    """Content-address ``data`` into the archive; return its sha256 hex."""
    digest = hashlib.sha256(data).hexdigest()
    dest = archive_path(digest)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(data)
        part.replace(dest)
    return digest


def _read_archive(digest: str) -> bytes:
    return archive_path(digest).read_bytes()


def _like_prefix(prefix: str) -> str:
    """A LIKE pattern matching strings that start with ``prefix`` literally."""
    escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped + "%"


# ---------------------------------------------------------------- tokens
# Named API tokens beside the administrator's PRAX_TOKEN (stage U, the
# wall). A token is a name, the modules it sees, whether it sees personal
# documents and its role (``TOKEN_ROLES``, migration 58); only the sha256
# of its secret is kept. The door is
# the only writer, so the lookup the middleware makes on every request is
# served from memory and forgotten on every change.
