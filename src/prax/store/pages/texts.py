"""A page's text and name: its kinds and authors, its slug and the old
slugs that lead to it, its blocks and links."""

from __future__ import annotations

import sqlite3
from typing import Any, Final

from prax.text import blocks, markup, slugs

from ..base import (
    _reading,
)

# Living Markdown documents (migration 0006): notes on a document, ongoing
# projects, topic pages. A page is a document, so everything that applies
# to documents applies; its identity is the slug, every save is a new text
# artifact with an append-only revision row, and its relationships to other
# documents are edges. An agent never overwrites human text: ``write_page``
# refuses an agent revision over a human one unless told to; ``append_page``
# adds a section instead, ``fill_blocks`` replaces the interiors of the
# page's ask blocks (``prax.text.blocks``) and nothing outside them. A
# ``question`` page is a standing question the door keeps answered
# (``prax.answering.questions``), a ``briefing`` the day's page of what arrived;
# both are the agent's, a person adds sections under them.

PAGE_KINDS = ("addendum", "project", "synthesis", "topic", "question", "briefing")


PAGE_AUTHORS = ("human", "agent")


# a link to a library document in a page's own prose, ``[title](#doc/12)``:
# the page annotates that document, an edge kept while the link stands
_DOC_LINK = markup.DOC_LINK


_LINK_EVIDENCE = "link in page"


def slugify(text: str) -> str:
    return slugs.slug(text)


def _page_original(slug: str, text: str) -> bytes:
    """The archived original of a page: the first revision with an identity
    line, so two pages with the same opening text stay distinct documents."""
    return f"<!-- prax page: {slug} -->\n{text}".encode()


@_reading
def page_titles(con: sqlite3.Connection) -> set[str]:
    """Titles of the documents that are pages: the only names a page or
    project entity may carry."""
    return {
        r[0]
        for r in con.execute(
            "SELECT d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
        )
        if r[0]
    }


def _slug_of(con: sqlite3.Connection, slug: str) -> str:
    """The slug a page has now: ``slug`` itself, or the page's current one
    when ``slug`` is an old name of it (``page_aliases``, migration 67)."""
    row = con.execute(
        "SELECT p.slug FROM page_aliases a JOIN pages p ON p.doc_id = a.doc_id"
        " WHERE a.slug = ?",
        (slug,),
    ).fetchone()
    return str(row[0]) if row else slug


# a page past this many characters is worth splitting: an append or a
# section written to it says so (``large`` in the answer). The nlbank log
# reached 407,101 characters in 183 revisions (2026-11-03)
PAGE_LARGE: Final = 200_000


def page_blocks(text: str, meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The ask blocks of a page's text as data, each with what the page's
    ``meta.asks`` remembers of it (when it was asked, by which model, its
    sources, whether the pass found it edited by hand and left it)."""
    asks = (meta or {}).get("asks") or {}
    out = []
    for b in blocks.blocks(text):
        kept = asks.get(b.id) or {}
        out.append(
            {
                "id": b.id,
                "question": b.question,
                "options": dict(b.options),
                "filled": b.filled,
                "held": blocks.held(b, text),
                "asked": b.asked,
                "run": b.tail_attrs.get("run"),
                "asked_at": kept.get("asked_at"),
                "model": kept.get("model"),
                "sources": kept.get("sources") or [],
                "checked_at": kept.get("checked_at"),
                "history": len(kept.get("history") or []),
                "left": kept.get("held"),  # {at, why} when the pass left it
            }
        )
    return out


def linked_documents(text: str) -> list[int]:
    """The library documents a page's text links to, ``[title](#doc/N)``,
    in order of first mention."""
    return list(dict.fromkeys(int(m.group(1)) for m in _DOC_LINK.finditer(text or "")))
