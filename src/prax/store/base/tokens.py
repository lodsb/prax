"""Named tokens (stage U): only a secret's sha256 is kept, with the
modules, the role and whether it sees what is personal; the viewer a
request with one reads as, and when it was last used."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from typing import Any

from prax.graph import ontology

from .common import _NOW
from .locks import _reading, _serialized
from .wall import UNASSIGNED, Viewer

TOKEN_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
# a reader reads; a contributor also adds to the library and changes it
TOKEN_ROLES = ("reader", "contributor")
_tokens: dict[str, dict[str, Any]] | None = None


def _token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@_serialized
def add_token(
    con: sqlite3.Connection,
    name: str,
    *,
    domains: list[str] | None = None,
    personal: bool = False,
    role: str = "reader",
) -> str:
    """A new named token; returns its secret, which is kept nowhere."""
    global _tokens
    if not TOKEN_NAME.fullmatch(name):
        raise ValueError("a token name is lowercase letters, digits, - and _")
    _check_role(role)
    if con.execute("SELECT 1 FROM tokens WHERE name = ?", (name,)).fetchone():
        raise ValueError(f"a token named {name!r} exists; remove it first")
    secret = "prax_" + secrets.token_urlsafe(32)
    con.execute(
        "INSERT INTO tokens (name, hash, domains, personal, role)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            name,
            _token_hash(secret),
            json.dumps(sorted(set(domains))) if domains else None,
            int(bool(personal)),
            role,
        ),
    )
    con.commit()
    _tokens = None
    return secret


def _check_role(role: str) -> None:
    if role not in TOKEN_ROLES:
        raise ValueError(f"a token's role is {' or '.join(TOKEN_ROLES)}")


@_serialized
def set_token_role(con: sqlite3.Connection, name: str, role: str) -> bool:
    """Change what a named token may do; False when there is no such token.
    Its secret stays the same."""
    global _tokens
    _check_role(role)
    cur = con.execute("UPDATE tokens SET role = ? WHERE name = ?", (role, name))
    con.commit()
    _tokens = None
    return cur.rowcount > 0


@_reading
def list_tokens(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """The named tokens, without their secrets."""
    return [
        {
            "name": r["name"],
            "domains": json.loads(r["domains"]) if r["domains"] else None,
            "personal": bool(r["personal"]),
            "role": r["role"],
            "created_at": r["created_at"],
            "last_used": r["last_used"],
        }
        for r in con.execute(
            "SELECT name, domains, personal, role, created_at, last_used FROM tokens"
            " ORDER BY name"
        )
    ]


@_serialized
def remove_token(con: sqlite3.Connection, name: str) -> bool:
    global _tokens
    cur = con.execute("DELETE FROM tokens WHERE name = ?", (name,))
    con.commit()
    _tokens = None
    return cur.rowcount > 0


# a token's last use is written at most this often: the lookup is on every
# request, and a write on every read would put the store's lock on it
TOKEN_USE_EVERY = 3600.0
_token_used: dict[str, float] = {}


def note_token_use(con: sqlite3.Connection, name: str) -> bool:
    """Record that a named token was used, once an hour at most: what the
    admin page shows to say which tokens can go."""
    now_m = time.monotonic()
    if now_m - _token_used.get(name, float("-inf")) < TOKEN_USE_EVERY:
        return False
    _token_used[name] = now_m
    _write_token_use(con, name)
    return True


@_serialized
def _write_token_use(con: sqlite3.Connection, name: str) -> None:
    con.execute(f"UPDATE tokens SET last_used = {_NOW} WHERE name = ?", (name,))
    con.commit()


@_reading
def token_viewer(con: sqlite3.Connection, secret: str) -> Viewer | None:
    """The viewer a named token's secret stands for, or None."""
    global _tokens
    if _tokens is None:
        _tokens = {
            str(r["hash"]): dict(r)
            for r in con.execute(
                "SELECT name, hash, domains, personal, role FROM tokens"
            )
        }
    row = _tokens.get(_token_hash(secret))
    return None if row is None else _viewer_of(row)


@_reading
def named_viewer(con: sqlite3.Connection, name: str) -> Viewer | None:
    """The viewer of the named token ``name``, or None: what an export made
    for that token's holder may hold (``prax export --as``)."""
    row = con.execute(
        "SELECT name, domains, personal, role FROM tokens WHERE name = ?", (name,)
    ).fetchone()
    return None if row is None else _viewer_of(dict(row))


def _viewer_of(row: dict[str, Any]) -> Viewer:
    domains = None
    if row["domains"]:
        onto = ontology.current()
        seen: set[str] = set()
        for d in json.loads(row["domains"]):
            seen |= set(onto.within(d)) if d != UNASSIGNED else {UNASSIGNED}
        domains = frozenset(seen)
    return Viewer(str(row["name"]), domains, bool(row["personal"]), str(row["role"]))
