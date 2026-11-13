"""A register of open questions (plan item AU, step 4).

A project keeps the questions it has not settled in one Markdown file, read
at the start of a session: a heading a question, then its fields as a list,
``- key: value``, a value running on over the indented lines under it::

    ## Open

    ### What is the drive's inertia?
    - status: open
    - asked: 2026-11-10, after the new platter went on
    - test: a driven acceleration against the inertia from its geometry
    - why not a coast: the gear's lash carries torque only when loaded

``status`` says the question's state with its first word (``open``,
``answered``, ``retired`` or ``retracted``) and the date in it, the day it
came to that; a section whose heading is a state word alone (``## Answered``)
says it for a question without one. Every other key is kept as written
(``test``, ``answer``, ``why it matters``, ``why not …``); a bullet with no
key belongs to the field before it. A question's text runs from its heading
to the next heading of its level or above, its own sub-headings included.

``split`` cuts such a file into its head (what is not a question, in order:
the preamble and the sections' headings and words) and its questions, which
the sync keeps as documents of their own (``prax.capture.projects``). A
question's ``key`` is its heading as written: the status changing, a field
added, the question moving to another section leave it the same question.
A key that comes twice is numbered from its second (``~2``).

A file is a register when ``REGISTER_MIN`` questions or more are in one of
``STATES``: a design note with one ``- status:`` line under a heading stays
a note. Nothing here imports prax but its text modules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from prax.text import markup

REGISTER_MIN = 2  # questions in a state that make a file a register
# a question's states, in the order a read about a subject shows them: what
# is open, what was believed and found false, what is settled, what was
# dropped; "retracted" is stale, and so what was built on it is suspect
STATES = ("open", "retracted", "answered", "retired")

# ``- key: value``: a key of a few words, no path or sentence, and emphasis
# around it allowed (``- **answer**: …``)
FIELD = re.compile(
    r"^[-*+]\s+[*_]{0,2}(?P<key>[A-Za-z][\w '’/-]{0,47}?)[*_]{0,2}\s*:"
    r"(?:\s+(?P<value>.*))?$"
)
_BULLET = re.compile(r"^[-*+]\s+(?P<value>.*)$")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_FIRST_WORD = re.compile(r"^[\s*_`~]*([A-Za-z]+)")


@dataclass(frozen=True)
class Question:
    key: str  # the heading as written, numbered when it comes again
    heading: str
    state: str | None  # the status's first word, else the section's; lower case
    since: str | None  # the date the status line gives
    section: str | None  # the state the section it stands in names
    fields: tuple[tuple[str, str], ...]  # (key in lower case, value), in order
    start: int  # offsets into the file's text; text == file[start:end]
    end: int
    text: str

    def field(self, key: str) -> str | None:
        """The first value under ``key`` (lower case), or None."""
        return next((v for k, v in self.fields if k == key), None)

    @property
    def why_not(self) -> list[tuple[str, str]]:
        """The ``why not …`` fields: what was tried or ruled out, and why."""
        return [(k, v) for k, v in self.fields if k.startswith("why not")]

    @property
    def asked(self) -> str | None:
        """The date the ``asked`` field gives, or None."""
        m = _DATE.search(self.field("asked") or "")
        return m.group(1) if m else None


@dataclass(frozen=True)
class Register:
    head: str  # everything that is not a question, in order
    questions: list[Question]


def state_of(words: str) -> str | None:
    """The state a section's heading names, when its words are a state word
    alone (``Answered``, ``Open (3)``), else None."""
    bare = re.sub(r"\([^()]*\)", " ", words)
    bare = re.sub(r"[^\w\s]", " ", bare).strip().lower()
    return bare if bare in STATES else None


def fields(body: str) -> tuple[tuple[str, str], ...]:
    """The ``- key: value`` fields of a question's body, up to its first
    sub-heading: keys in lower case, a value's lines joined by spaces."""
    out: list[list[str]] = []
    fenced = False
    for line in body.splitlines():
        bare = line.rstrip()
        if bare.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if fenced:
            if out:
                out[-1][1] += " " + bare.strip()
            continue
        if markup.HEADING.match(bare):
            break
        if not bare.strip():
            continue
        if not line[:1].isspace():
            m = FIELD.match(bare)
            if m:
                out.append(
                    [" ".join(m.group("key").lower().split()), m.group("value") or ""]
                )
                continue
            b = _BULLET.match(bare)
            if b and out:
                out[-1][1] += " " + b.group("value").strip()
                continue
            if not out:
                continue  # words before the fields
        if out:
            out[-1][1] += " " + bare.strip()
    return tuple((k, v.strip()) for k, v in out)


def _status(fs: tuple[tuple[str, str], ...]) -> tuple[str | None, str | None]:
    said = next((v for k, v in fs if k == "status"), None)
    if not said:
        return None, None
    word = _FIRST_WORD.match(said)
    date = _DATE.search(said)
    return (
        word.group(1).lower() if word else None,
        date.group(1) if date else None,
    )


def _question(
    text: str, start: int, end: int, words: str, section: str | None
) -> Question:
    line_end = text.find("\n", start)
    fs = fields(text[line_end + 1 : end] if line_end != -1 else "")
    state, since = _status(fs)
    return Question(
        key=words.strip(),
        heading=words.strip(),
        state=state or section,
        since=since,
        section=section,
        fields=fs,
        start=start,
        end=end,
        text=text[start:end],
    )


def one(text: str, section: str | None = None) -> Question | None:
    """A question's own text read again (a synced question's document):
    its first heading and the fields under it, ``section`` the state its
    register's section gave it; None when the text has no heading."""
    heads = markup.headings(text)
    if not heads:
        return None
    return _question(text, heads[0][0], len(text), heads[0][2], section)


def split(text: str, *, least: int = REGISTER_MIN) -> Register | None:
    """A Markdown text cut into its head and its questions, or None when
    fewer than ``least`` of them are in one of ``STATES``."""
    heads = markup.headings(text)
    found: list[Question] = []
    inside_until = -1  # a question's sub-headings are its own
    for i, (offset, level, words) in enumerate(heads):
        if offset < inside_until:
            continue
        end = next((o for o, lv, _ in heads[i + 1 :] if lv <= level), len(text))
        parent = next((w for o, lv, w in reversed(heads[:i]) if lv < level), None)
        section = state_of(parent) if parent is not None else None
        q = _question(text, offset, end, words, section)
        if q.state not in STATES and section is None:
            continue  # a note's own "- status: draft" makes it no question
        found.append(q)
        inside_until = end
    if sum(1 for q in found if q.state in STATES) < least:
        return None
    seen: dict[str, int] = {}
    questions = []
    for q in found:
        seen[q.key] = seen.get(q.key, 0) + 1
        if seen[q.key] > 1:
            q = replace(q, key=f"{q.key}~{seen[q.key]}")
        questions.append(q)
    head, at = [], 0
    for q in questions:
        head.append(text[at : q.start])
        at = q.end
    head.append(text[at:])
    return Register("".join(head), questions)
