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

The words that say a state, in each language, are the lexicon's
(``status:`` in ``ontology/lexicon.yaml``), passed in as ``Words`` or
read from the lexicon on disk when none are. A state word at a line's
start counts only on a line of a status line's shape: after a key, or
alone, emphasised, in capitals, or before a colon, a dash, a date or
one of the words that follow a status ("Superseded by …"). The paths
a status line names (the replacement) are found by
``prax.text.paths``; the caller matches them. Nothing here imports prax
at the top but its own text package.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from typing import Any

from prax.text import paths

HEAD_LINES = 30  # how far down a status line counts
# the states a document may say it is in; the words for each are data
STATES = ("retired", "superseded", "invalid", "deprecated", "current", "draft")
STALE = frozenset({"retired", "superseded", "invalid", "deprecated"})
# the edges that make the document they reach stale, and the state each
# puts it in; a stale note naming its replacement is reached by the first
# of them whose state it has, else by ``supersedes``
STALE_RELS = {"supersedes": "superseded", "invalidates": "invalid"}
_REL_OF = {state: rel for rel, state in STALE_RELS.items()}


def stale_rel(state: str) -> str:
    """The relation from a replacement to a note in ``state``."""
    return _REL_OF.get(state, "supersedes")


_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{4}-\d{2})\b")
# a line's furniture before its first word: a quote mark, a heading, a
# list dash, emphasis, an emoji-free warning sign of text
_LEAD = re.compile(r"^[\s>#*_\-|]*(?:\[!?\w+\]\s*)?[\s*_]*")
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


@dataclass(frozen=True)
class Words:
    """The words of one lexicon's ``status:`` section."""

    by_word: tuple[tuple[str, str], ...] = ()  # (word, state), lower case
    keys: tuple[str, ...] = ()  # "status", "Zustand"
    after: tuple[str, ...] = ()  # "by", "since", "seit"

    def state(self, word: str) -> str | None:
        return dict(self.by_word).get(word.lower())

    @property
    def key(self) -> re.Pattern[str]:
        return _key_pattern(self.keys)

    @property
    def follows(self) -> re.Pattern[str]:
        return _after_pattern(self.after)


def words_of(section: dict[str, Any] | None) -> Words:
    """``Words`` from a lexicon's ``status:`` section; empty for none."""
    section = section or {}
    states = section.get("states") or {}
    return Words(
        by_word=tuple(
            (str(w).lower(), str(state))
            for state, ws in states.items()
            if state in STATES
            for w in ws or []
        ),
        keys=tuple(str(k) for k in section.get("keys") or []),
        after=tuple(str(a) for a in section.get("after") or []),
    )


def lexicon_words() -> Words:
    """The status words of the lexicon on disk."""
    from prax.graph import ontology  # the lexicon is data beside the ontology

    return words_of(ontology.lexicon().section("status"))


@functools.lru_cache(maxsize=8)
def _key_pattern(keys: tuple[str, ...]) -> re.Pattern[str]:
    alt = "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))
    if not alt:
        return re.compile(r"(?!x)x")
    return re.compile(rf"^(?:{alt})\s*[:=]\s*[*_\s]*(\w+)", re.IGNORECASE)


@functools.lru_cache(maxsize=8)
def _after_pattern(after: tuple[str, ...]) -> re.Pattern[str]:
    alt = "|".join(
        re.escape(a).replace(r"\ ", r"\s+")
        for a in sorted(after, key=len, reverse=True)
    )
    # a colon or a bang, a dash, a bracket, a date, or a word of ``after``
    marks = r"[:!]|\s*[—–-]\s|\s*\(|\s+\d{4}"
    return re.compile(
        rf"^(?:{marks}" + (rf"|\s+(?:{alt})(?!\w)" if alt else "") + ")",
        re.IGNORECASE,
    )


def _marks_a_status(line: str, rest: str, word: str, w: Words) -> bool:
    """Whether a line opening with a state word, with no key before it,
    is a status line rather than a sentence that starts with the word."""
    after = rest[len(word) :].lstrip("*_")
    if not after.strip(" .") or (word.isupper() and len(word) > 1):
        return True  # "Deprecated" alone, "RETIRED"
    if re.search(r"(\*\*|__|\*|_)" + re.escape(word) + r"\1", line, re.IGNORECASE):
        return True  # **Deprecated** since 2026-09
    return bool(w.follows.match(after))


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


def read(
    text: str, here: str, prefix: str = "", words: Words | None = None
) -> Status | None:
    """The document's own status, or None when it states none. ``here`` is
    its path in the repository and ``prefix`` the project's folder, which
    a replacement's path is resolved against; ``words`` the lexicon's
    (the one on disk when None)."""
    w = words if words is not None else lexicon_words()
    front = _front_matter(text)
    if front:
        state = (
            w.state(front.get("status", "").split()[0]) if front.get("status") else None
        )
        since = None
        for key in ("retired", "superseded", "deprecated", "invalidated"):
            if front.get(key):
                state = state or w.state(key) or key
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
            said = "; ".join(
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
            return Status(state, since, said, refs)
    body = _FRONT.sub("", text, count=1)
    for line in body.splitlines()[:HEAD_LINES]:
        rest = _LEAD.sub("", line).strip()
        if not rest:
            continue
        keyed = w.key.match(rest)
        word = (
            keyed.group(1) if keyed else re.split(r"[\s:.,;!*_]", rest, maxsplit=1)[0]
        )
        state = w.state(word)
        if state is None:
            continue
        if not keyed and state not in STALE:
            continue  # "Current draft of…" begins prose; only "Status: current" says it
        if not keyed and not _marks_a_status(line, rest, word, w):
            continue  # "Archived copies of the data live on the NAS"
        found = _DATE.search(rest)
        return Status(
            state,
            found.group(1) if found else None,
            line.strip(),
            paths.references(rest, here, prefix),
        )
    return None
