"""A project's manifest (migration 38): where its working copy is, what is
read from it, and its documents."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Final

from ..base import (
    _reading,
    _serialized,
)

# ------------------------------------------------------------- projects
#
# A project's manifest (migration 38): where its working copy is, what is
# read from it, and whether the session-end hook syncs it on its own. The
# plan of a sync and its documents are ``prax.capture.projects``.

PROJECT_SOURCE: Final = "project"  # ``meta.source`` of a synced document


def _project_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    out["settings"] = json.loads(out.get("settings") or "{}")
    out["last"] = json.loads(out["last"]) if out.get("last") else None
    out["auto_sync"] = bool(out.get("auto_sync"))
    return out


@_reading
def project_named(con: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """A project's manifest by name, or None."""
    return _project_row(
        con.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
    )


@_reading
def project_at(
    con: sqlite3.Connection, remote: str | None, prefix: str
) -> dict[str, Any] | None:
    """The project of a working copy: its canonical remote and its folder
    within the repository. None without a remote, which keys nothing."""
    if not remote:
        return None
    return _project_row(
        con.execute(
            "SELECT * FROM projects WHERE remote = ? AND prefix = ?", (remote, prefix)
        ).fetchone()
    )


@_reading
def list_projects(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every project's manifest, by name."""
    rows = con.execute("SELECT * FROM projects ORDER BY name").fetchall()
    return [p for p in (_project_row(r) for r in rows) if p is not None]


@_serialized
def save_project(
    con: sqlite3.Connection,
    name: str,
    *,
    remote: str | None,
    prefix: str,
    settings: dict[str, Any],
    auto_sync: bool | None = None,
) -> dict[str, Any]:
    """Write a project's manifest; ``auto_sync`` None keeps what it was. A
    working copy belongs to one project: a second name for the same remote
    and folder is refused (ValueError)."""
    if not name or not name.strip():
        raise ValueError("a project needs a name")
    if remote:
        taken = con.execute(
            "SELECT name FROM projects WHERE remote = ? AND prefix = ? AND name != ?",
            (remote, prefix, name),
        ).fetchone()
        if taken:
            raise ValueError(
                f"{remote} {prefix or '(the whole repository)'} is project"
                f" {taken[0]!r} already"
            )
    con.execute(
        "INSERT INTO projects (name, remote, prefix, settings, auto_sync)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET remote = excluded.remote,"
        " prefix = excluded.prefix, settings = excluded.settings,"
        " auto_sync = CASE WHEN ? IS NULL THEN projects.auto_sync"
        " ELSE excluded.auto_sync END",
        (
            name,
            remote,
            prefix,
            json.dumps(settings, sort_keys=True),
            int(bool(auto_sync)),
            None if auto_sync is None else int(auto_sync),
        ),
    )
    con.commit()
    got = _project_row(
        con.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
    )
    assert got is not None
    return got


@_serialized
def note_project_sync(con: sqlite3.Connection, name: str, last: dict[str, Any]) -> None:
    """When a project was last synced, and what that sync did."""
    con.execute(
        "UPDATE projects SET synced_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),"
        " last = ? WHERE name = ?",
        (json.dumps(last, sort_keys=True), name),
    )
    con.commit()


@_reading
def project_documents(
    con: sqlite3.Connection, name: str, keys: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """A project's live synced documents by key: ``{key: {doc_id, version,
    path}}``, those its name carries and those any of ``keys`` names (a
    document synced before the manifest, under an older key)."""
    keys = keys or []
    marks = ",".join("?" * len(keys)) or "NULL"
    rows = con.execute(
        "SELECT id, json_extract(meta, '$.project.key') AS key,"
        " json_extract(meta, '$.project.version') AS version,"
        " json_extract(meta, '$.project.path') AS path FROM documents"
        " WHERE json_extract(meta, '$.source') = ?"
        " AND json_extract(meta, '$.retired') IS NULL"
        " AND (json_extract(meta, '$.project.name') = ?"
        f" OR json_extract(meta, '$.project.key') IN ({marks}))"
        " ORDER BY id",
        (PROJECT_SOURCE, name, *keys),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["key"] and r["key"] not in out:
            out[str(r["key"])] = {
                "doc_id": int(r["id"]),
                "version": r["version"],
                "path": r["path"],
            }
    return out
