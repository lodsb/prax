"""One name per thing, whatever language the document was in.

The graph holds `Olivenöl` with ten documents behind it and `olive oil`
with one, and a walk from either reaches half the evidence. The same
happens to `Speicherverwaltung` beside `memory management` and
`Gauß-Elimination` beside `Gaussian elimination`. It is the graph's half
of what `prax.summaries` fixes for the document field.

Only some names may be folded this way, and the ontology says which:
a type whose `naming:` is `common` names a *kind* of thing, which every
language has its own word for, and a `proper` one names a particular
thing whose name is the same string everywhere. `Niklas Klügel` is not
translated and neither is `Einführung in die Softwaretechnik`; `Olivenöl`
is.

**Which names are not English** is answered by the library rather than by
a list of German endings. A name English documents use at least as often,
document for document, as the rest of the library does is an English
name; one they use less is a candidate. FTS5 already indexes every chunk,
so the test is one MATCH. It needs no rule per language, and it is wrong
in a way that costs nothing: a rare English term nobody else wrote down
(`extendible hashing`) becomes a candidate, and the model hands it back
unchanged.

It was "occurs in any English document" until 2026-09-26, and that let
out the commonest words of all: `Mehl` is in eight English documents (a
programming textbook, an operating-systems tutorial sheet) and in
eighteen German ones out of a quarter as many, so it was ruled English
and never folded into `flour`. The rate is what says whose word it is.

**The model decides**, and its answer is recorded as evidence, not as
truth. The name it gives is a label with its language, producer and run
(`entity_labels`, migration 20), the name the document used stays a label
too, so a German search still reaches the entity, and where the English
name is already an entity the two are merged under that run — which
`store.unmerge_run` undoes whole.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from prax import answers, language

MAX_WORDS = 6  # a longer "name" is a sentence, and not this pass's business
LOOK_AT = 200  # chunks of a name's occurrences to look through, at most

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
# what a model says when it has nothing to change, in the words it uses
_UNCHANGED = re.compile(
    r"^(same|unchanged|already english|n/?a|none|-)\W*$", re.IGNORECASE
)


def system() -> str:
    """What the model is told. The language is the library's."""
    into = language.name(language.canonical()) or "English"
    return (
        "You are given the name of a concept, method, technique, material,"
        " ingredient, dish or cuisine as one document called it, and you"
        f" give the name {into} uses for the same thing."
        " Answer with that name alone: no preamble, no quotation marks, no"
        " explanation, no full stop."
        f" A name that is already {into} you repeat exactly as given."
        " Keep a person's name, a place, a product or a standard inside the"
        " name exactly as printed and translate only the words around it,"
        " so Gauß-Elimination is Gaussian elimination and Büchi-Automat is"
        " Büchi automaton."
        " Give the established term, lowercase unless it is a proper noun,"
        f" singular, and never a description: if you do not know the {into}"
        " term, repeat the name you were given."
    )


@dataclass
class Naming:
    """What the model called the thing, and what the call cost."""

    name: str
    changed: bool
    usage: dict[str, Any] = field(default_factory=dict)


# not prax's own pages: a briefing that says "Olivenöl sits beside olive
# oil" is an English document containing the German word, and it took
# Olivenöl out of the net that would have folded it. The library must not
# be its own evidence
_NOT_A_PAGE = "NOT EXISTS (SELECT 1 FROM pages p WHERE p.doc_id = d.id)"


def library_sizes(con: sqlite3.Connection) -> tuple[int, int]:
    """How many documents are in the library's language, and how many in
    another one it knows. What a count of occurrences is divided by."""
    row = con.execute(
        "SELECT count(*) FILTER (WHERE lang = ?), count(*) FILTER (WHERE lang <> ?)"
        " FROM (SELECT json_extract(d.meta, '$.lang') AS lang FROM documents d"
        f" WHERE {_NOT_A_PAGE})",
        (language.canonical(), language.canonical()),
    ).fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def in_english_text(
    con: sqlite3.Connection, name: str, *, sizes: tuple[int, int] | None = None
) -> bool:
    """Is this name the library language's word, by the library's own use?

    The library as its own dictionary: the share of English documents
    that use the name, against the share of the others. A name English
    documents never use is a candidate; one they use at least as often as
    the rest is already the word English uses, whatever it looks like.
    ``sizes`` is ``library_sizes``, for a caller asking about many names.
    """
    words = _WORD.findall(name)
    if not words or len(words) > MAX_WORDS:
        return True  # nothing to look up, or not a name
    canonical = language.canonical()
    match = " ".join(f'"{w}"' for w in words)  # the words in order
    try:
        row = con.execute(
            "SELECT count(DISTINCT d.id) FILTER (WHERE lang = ?),"
            " count(DISTINCT d.id) FILTER (WHERE lang <> ?)"
            " FROM (SELECT d.id, json_extract(d.meta, '$.lang') AS lang"
            " FROM chunks_fts f JOIN chunks c ON c.id = f.rowid"
            " JOIN documents d ON d.id = c.doc_id"
            f" WHERE chunks_fts MATCH ? AND {_NOT_A_PAGE}) d",
            (canonical, canonical, match),
        ).fetchone()
    except sqlite3.OperationalError:
        return True  # a name FTS cannot parse is not this pass's business
    ours, theirs = int(row[0] or 0), int(row[1] or 0)
    if not ours:
        return False
    if not theirs:
        return True
    n_ours, n_theirs = sizes or library_sizes(con)
    # ours / n_ours >= theirs / n_theirs, without the division
    return ours * max(n_theirs, 1) >= theirs * max(n_ours, 1)


def parse(out: str) -> str | None:
    """The name out of what the model returned, or None when it returned
    nothing usable.

    The wrapping is ``prax.answers``; what is peculiar here is that a
    model with nothing to change says so in words rather than repeating
    the name, and "same" is not a name.
    """
    text = answers.first_line(out)
    if not text or _UNCHANGED.match(text):
        return None
    return text


def acceptable(name: str, given: str) -> str | None:
    """Why the name cannot be used, or None when it can.

    A small model asked for a term writes a definition instead, or
    answers in the language it was given, or invents something with
    nothing of the original in it. The first two are catchable here; the
    third is what the review queue and ``unmerge_run`` are for.
    """
    if len(_WORD.findall(name)) > MAX_WORDS:
        return "a description, not a name"
    if len(name) > max(60, len(given) * 2):
        return "too long"
    if name.strip().lower() == given.strip().lower():
        return "unchanged"
    # a name that is the given one with words taken out is not a
    # translation of it: "outdoor travel health insurance" came back as
    # "travel health insurance", which is the model editing an English
    # name it was asked to repeat (2026-09-24)
    words = [w.lower() for w in _WORD.findall(name)]
    had = [w.lower() for w in _WORD.findall(given)]
    if words and all(w in had for w in words):
        return "only words taken out"
    return None


def user_message(name: str, kind: str, *, context: str = "") -> str:
    """What the model is given: the name, what kind of thing it is, and
    where it was said, as sentences — a model handed labelled fields
    fills the form in and returns the labels (``prax.summaries``).

    The title says which of two things a bare word is, and it can also
    be taken for the answer: `Apfel-auflauf`, a variant named in passing
    in an elderflower-lemon bake, came back as "elderflower lemon bake".
    Saying that the document may be about something else fixed it and
    changed none of the other 80 names of that day for the worse; the
    sentence the name was said in, tried instead, named the dish around
    it four times out of 81 (2026-09-26)."""
    parts = [f"A {kind} is called “{name}”."]
    if context.strip():
        parts.append(
            f"It was named in a document called “{context.strip()}”, which may"
            " be about something else: name this thing, not the document."
        )
    parts.append("What does English call it?")
    return " ".join(parts)


def rename(runtime: Any, name: str, kind: str, *, context: str = "") -> Naming | None:
    """The English name of the thing, or None when the model did not
    manage one. ``changed`` is False when the name was already English,
    which is the commonest answer and costs only the call."""
    out, usage = runtime.chat(
        system(),
        user_message(name, kind, context=context),
        max_tokens=40,
        temperature=0.0,
        stop=["\n"],
    )
    got = parse(out)
    if got is None:
        return Naming(name=name, changed=False, usage=usage)
    why = acceptable(got, name)
    if why == "unchanged":
        return Naming(name=name, changed=False, usage=usage)
    if why is not None:
        return None
    return Naming(name=got, changed=True, usage=usage)
