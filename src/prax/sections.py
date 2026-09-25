"""What a long document's parts are about.

`meta.summary` is two or three sentences for a whole document, which is
right for a paper and almost nothing for a book. *The Princeton
Companion to Mathematics* is 5.2 million characters and its summary is
307 of them; the document field — what `documents_fts` and the document
vector search — therefore says nothing about the middle of it. 1,229
documents in the library are over 100,000 characters.

So a summary per section as well. The unit is the top-level heading
region, because that is a chapter in a book and a major section in a
manual, and only where there is enough of it to be worth a sentence: a
document whose headings carry a paragraph each is a paper, and its own
summary already covers it.

**Where this lives, and why not a chunk.** A chunk is a region of the
artifact and its text *is* that region (invariant: `chunk.text ==
artifact[start:end]`), so a generated sentence cannot be one. The
readings of a figure and a formula solve that by being written into the
artifact under the thing they read; a section summary has no such line
to sit under, and inventing one would mean rewriting every long
document's text. `documents.meta` is the extension point the conventions
name for exactly this, so the summaries live there and reach search
through the document field.

That does mean a section summary is not independently searchable — the
field is one row for the whole document. Making each one its own
searchable thing is the fuller design, and it wants the artifact to
carry them, which is a bigger change than this (`docs/PLAN.md`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from prax import answers

MIN_DOCUMENT = 60_000  # characters: shorter than this and one summary does
MIN_SECTION = 4_000  # a section shorter than this is not a chapter
MAX_SECTIONS = 40  # per document, longest first
READ_CHARS = 12_000  # of a section given to the model, in Latin script
# What a character costs in tokens depends on the script, and the cap
# above is the only budget this pass had until 2026-09-25 — when three
# documents failed 71 times because 12,000 characters of Arabic and of
# Tibetan are 23,834 and 26,255 tokens against a 16,384-token slot.
# UTF-8 length is the cheap proxy that tracks it: a Latin character is
# one byte and about a quarter of a token, an Arabic one is two bytes and
# roughly a token, a Tibetan one is three. Budgeting on bytes therefore
# costs a Latin document nothing and holds the others inside the slot.
READ_BYTES = 12_000  # …and of its UTF-8, which is what a tokenizer sees
READ_TOKENS = 11_000  # …and what it really costs, where the server counts
MAX_SUMMARY = 400  # characters of answer kept


def system() -> str:
    """What the model is told. The answer is in the language the library
    is written in, whatever the section is in."""
    from prax import language

    into = language.name(language.canonical()) or "English"
    return (
        "You are given one section of a longer document and you say what"
        f" it is about, in one or two sentences of {into}, so a reader"
        " searching a library can tell whether this is the part they want."
        " Answer with those sentences and nothing else: no preamble, no"
        " heading, no bullet list, no quotation marks."
        " Name what the section actually covers — the ideas, methods,"
        " people or things in it — rather than describing it as a section"
        ' of a document. Never write "this section" or "the text".'
    )


@dataclass
class Section:
    """A part of a document, and what it is about."""

    heading: str
    chars: int
    summary: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    def as_meta(self) -> dict[str, Any]:
        return {"heading": self.heading, "chars": self.chars, "summary": self.summary}


# "This section explores the modelling of adaptation…" — the model says
# it whatever the prompt asks, and the words after it are the answer. So
# the opener comes off rather than the summary being thrown away: the
# first version of this guard refused every real chapter of the Princeton
# Companion for its first two words (2026-09-24).
_OPENER = re.compile(
    r"^(this|the)\s+(section|chapter|part|document|text|article)\s+", re.IGNORECASE
)
# what is left has to say something about a subject, not about being one
_CONTENTLESS = re.compile(
    r"^(?:(?:is|are|was|were)\s+)?(?:about\s+)?(?:a|an|the)?\s*"
    r"(?:section|chapter|part|contents?|text|document)\b"
    r"|^(?:describes?|explains?|covers?|presents?|discusses|outlines?)\s+"
    r"(?:the\s+)?(?:contents?|structure|sections?|chapters?|material)\b",
    re.IGNORECASE,
)


_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*[^>]*>")
_EMPHASIS = re.compile(r"[*_`]{1,3}")


def clean_heading(heading: str) -> str:
    """A heading as a reader would write it.

    A PDF extractor leaves its own markup in one — "<mark>12</mark>
    KNOWLEDGE REPRESENTATION", "24<sup>PERCEPTION</sup>" — and the
    document field would carry it into the index.
    """
    text = _EMPHASIS.sub("", _TAG.sub(" ", heading or ""))
    return " ".join(text.split())


def parse(out: str) -> str | None:
    """The sentences out of what the model returned, or None.

    The wrapping is ``prax.answers``; what is peculiar here is the
    opener, which is the model naming the thing it was given rather than
    what is in it.
    """
    text = answers.unwrap(out, sentence=True)
    without = _OPENER.sub("", text).strip()
    if without and without != text:
        text = without[0].upper() + without[1:]
    return text[:MAX_SUMMARY].strip() or None


def acceptable(text: str, heading: str) -> str | None:
    """Why the summary cannot be used, or None when it can.

    A small model asked what a section is about hands the heading back,
    says nothing but that it is a section, or writes a page.
    """
    if _CONTENTLESS.match(text):
        return "about itself, not about its subject"
    if text.strip().lower() == heading.strip().lower():
        return "the heading again"
    if len(text) < 30:
        return "too short"
    return None


def user_message(
    heading: str, text: str, *, title: str = "", runtime: Any | None = None
) -> str:
    """The section, named and given. Sentences rather than labelled
    fields, so the model has no form to fill in (``prax.answers``)."""
    parts = []
    if title.strip():
        parts.append(f"The document is called “{title.strip()}”.")
    parts.append(f"This is its section “{heading}”. What is it about?")
    parts.append("")
    parts.append(read_for(text, runtime=runtime))
    return "\n".join(parts)


def read_for(
    text: str,
    *,
    chars: int = READ_CHARS,
    byts: int = READ_BYTES,
    runtime: Any | None = None,
) -> str:
    """As much of a section as the model can be given.

    Measured against the server where it can count (``models.fits_for``),
    guessed from the UTF-8 length where it cannot.
    """
    from prax import models

    if runtime is None:
        return models.fits(text, chars=chars, byts=byts)
    return models.fits_for(runtime, text, tokens=READ_TOKENS, chars=chars, byts=byts)


def summarize(
    runtime: Any, heading: str, text: str, *, title: str = ""
) -> Section | None:
    """One section read, or None when the model did not manage it."""
    out, usage = runtime.chat(
        system(),
        user_message(heading, text, title=title, runtime=runtime),
        max_tokens=160,
        temperature=0.0,
    )
    got = parse(out)
    if got is None or acceptable(got, heading) is not None:
        return None
    return Section(heading=heading, chars=len(text), summary=got, usage=usage)
