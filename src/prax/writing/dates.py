"""When a document was published, read from its first page by a model.

The sources ``prax.text.dates`` reads without a model (a record, a page's
tags, an arXiv id) date a sixth of the library; most of the rest are PDFs
whose date is printed near their start: a conference and its year, a
journal issue, "Received … accepted …", a copyright line. A heuristic
(the latest year on the first page) was right for 63% of 1,836 documents
Zotero had dated (2026-10-03), too often to be trusted and too seldom to
be enough.

The model sees the title and the beginning of the text and answers on one
line with the date and the words that state it, or ``none``. Its answer is
believed only when those words are on the page and hold the year: a date
the text does not say is never written (``checked``). No grammar, as for
titles: a plain instruction and a stop at the line's end give a clean
line from a small model too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prax import models
from prax.text import dates
from prax.writing.titles import head

HEAD_CHARS = 3000  # the first page, about

SYSTEM = """\
You date documents for a research library. You see a document's title and
the beginning of its text. Answer with when the document itself was
published, and the words in the text that say so, on one line:

DATE | WORDS

DATE is YYYY, YYYY-MM or YYYY-MM-DD, as precise as the text states it.
WORDS are copied exactly from the text, a few words that contain the date:
a conference with its year, a journal issue, a "published" or "received"
line, a copyright line. Not the year of a cited work, not a date in the
document's subject matter, not a software version. If the text does not
state when this document was published, answer: none"""

_LINE = re.compile(r"^\s*([0-9]{4}(?:-[0-9]{2}){0,2})\s*\|\s*(.+?)\s*$")
_EVIDENT = re.compile(
    r"proceedings|conference|symposium|workshop|journal|vol\.|volume|issue|"
    r"received|accepted|published|copyright|©|\(c\)|arxiv|preprint|"
    r"submitted|revised|tagung|konferenz",
    re.IGNORECASE,
)


@dataclass
class Dated:
    date: str
    precision: str
    words: str
    confidence: str  # high: the words name a publication event; medium: a bare year


def user_message(text: str, title: str) -> str:
    return "\n".join(
        [
            f"Title: {title}",
            "",
            "Beginning of the document:",
            "",
            head(text, HEAD_CHARS),
            "",
            "Answer:",
        ]
    )


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def checked(answer: str, text: str) -> Dated | None:
    """The model's line, believed only when its words are in the text and
    hold the date's year (and parse as a date). None for ``none``, for a
    line that is not ``DATE | WORDS``, and for anything the text does not
    bear out."""
    m = _LINE.match(answer.splitlines()[0] if answer else "")
    if not m:
        return None
    got = dates.parse(m.group(1))
    words = m.group(2).strip().strip("\"'“”")
    if got is None or len(words) < 4:
        return None
    if _norm(words) not in _norm(head(text, HEAD_CHARS)):
        return None  # not copied from the page
    year = got[0][:4]
    if (
        year not in words
        and f"'{year[2:]}" not in words
        and f"-{year[2:]}" not in words
    ):
        return None  # the words do not say this year
    confidence = "high" if _EVIDENT.search(words) else "medium"
    return Dated(got[0], got[1], words[:200], confidence)


def read_date(
    runtime: models.Runtime, text: str, title: str
) -> tuple[Dated | None, dict[str, int]]:
    """Ask the model; the checked answer and the usage."""
    out, usage = runtime.chat(
        SYSTEM, user_message(text, title), max_tokens=80, temperature=0.0, stop=["\n"]
    )
    return checked(out, text), usage
