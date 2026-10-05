"""What a document says of itself: retired, superseded, invalid (stage AL,
step 5, "what is current").

A project's notes say it in two ways, both read here and nothing else: the
front matter (``status: retired``, ``retired: 2026-10-02``,
``superseded_by: docs/plan-v2.md``) and an explicit status line near the
top (``Status: superseded by [the new plan](plan-v2.md)``, ``> RETIRED
2026-10-02``, ``**Deprecated** since 2026-09``). A line further down, or
one that only mentions the words in passing ("the retired voice board"),
says nothing about the document: the reader looks at the first
``HEAD_LINES`` lines and wants the state word at a line's start. Only a
project's own notes are read this way (``prax.capture.projects``); a paper
saying "superseded by" is about other people's work.

The paths a status line names (the replacement) are found by
``prax.text.paths``; the caller matches them. Nothing here imports prax
but its own text package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from prax.text import paths

HEAD_LINES = 30  # how far down a status line counts
# the states, and the words that say each (the first word of the line)
STATES = {
    "retired": ("retired", "withdrawn", "archived", "abandoned"),
    "superseded": ("superseded", "replaced", "obsolete", "outdated"),
    "invalid": ("invalid", "invalidated", "void", "wrong", "retracted"),
    "deprecated": ("deprecated",),
    "current": ("current", "active", "accepted", "final", "approved", "valid"),
    "draft": ("draft", "proposed", "wip"),
}
STALE = frozenset({"retired", "superseded", "invalid", "deprecated"})
# the edges that make the document they reach stale, and the state each
# puts it in; a stale note naming its replacement is reached by the first
# of them whose state it has, else by ``supersedes``
STALE_RELS = {"supersedes": "superseded", "invalidates": "invalid"}
_REL_OF = {state: rel for rel, state in STALE_RELS.items()}


def stale_rel(state: str) -> str:
    """The relation from a replacement to a note in ``state``."""
    return _REL_OF.get(state, "supersedes")


_WORD = {w: state for state, words in STATES.items() for w in words}
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{4}-\d{2})\b")
# a line's furniture before its first word: a quote mark, a heading, a
# list dash, emphasis, an emoji-free warning sign of text
_LEAD = re.compile(r"^[\s>#*_\-|]*(?:\[!?\w+\]\s*)?[\s*_]*")
_STATUS_KEY = re.compile(r"^status\s*[:=]\s*[*_\s]*(\w+)", re.IGNORECASE)
_FRONT = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_FRONT_KEY = re.compile(r"^([A-Za-z_\-]+)\s*:\s*(.*?)\s*$")


@dataclass
class Status:
    """A document's state as it states it: ``state`` (one of ``STATES``),
    ``since`` (a date as written, or None), ``words`` (the line or the
    front matter that says it: the evidence) and ``refs`` (the paths the
    line names, as ``prax.text.paths.references`` gives them)."""

    state: str
    since: str | None
    words: str
    refs: list[tuple[list[str], str]] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return self.state in STALE


def _front_matter(text: str) -> dict[str, str]:
    m = _FRONT.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        kv = _FRONT_KEY.match(line)
        if kv:
            out[kv.group(1).lower().replace("-", "_")] = kv.group(2).strip("'\"")
    return out


def read(text: str, here: str, prefix: str = "") -> Status | None:
    """The document's own status, or None when it states none. ``here`` is
    its path in the repository and ``prefix`` the project's folder, which
    a replacement's path is resolved against."""
    front = _front_matter(text)
    if front:
        state = (
            _WORD.get(front.get("status", "").split()[0].lower())
            if front.get("status")
            else None
        )
        since = None
        for key in ("retired", "superseded", "deprecated", "invalidated"):
            if front.get(key):
                state = state or _WORD.get(key, key)
                found = _DATE.search(front[key])
                since = since or (found.group(1) if found else None)
        replacement = front.get("superseded_by") or front.get("replaced_by")
        if replacement and not state:
            state = "superseded"
        if state:
            if since is None:
                found = _DATE.search(front.get("status", "")) or _DATE.search(
                    front.get("date", "") if state in STALE else ""
                )
                since = found.group(1) if found else None
            words = "; ".join(
                f"{k}: {v}"
                for k, v in front.items()
                if k
                in (
                    "status",
                    "retired",
                    "superseded",
                    "deprecated",
                    "invalidated",
                    "superseded_by",
                    "replaced_by",
                )
            )
            refs = (
                paths.references(f"`{replacement}`", here, prefix)
                if replacement
                else []
            )
            return Status(state, since, words, refs)
    body = _FRONT.sub("", text, count=1)
    for line in body.splitlines()[:HEAD_LINES]:
        rest = _LEAD.sub("", line).strip()
        if not rest:
            continue
        keyed = _STATUS_KEY.match(rest)
        word = (
            keyed.group(1) if keyed else re.split(r"[\s:.,;!*_]", rest, maxsplit=1)[0]
        )
        state = _WORD.get(word.lower())
        if state is None:
            continue
        if not keyed and state not in STALE:
            continue  # "Current draft of…" begins prose; only "Status: current" says it
        found = _DATE.search(rest)
        return Status(
            state,
            found.group(1) if found else None,
            line.strip(),
            paths.references(rest, here, prefix),
        )
    return None
