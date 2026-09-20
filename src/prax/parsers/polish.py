"""An automatic transcript, punctuated: the paragraphs of a talk written
as the speaker meant them.

YouTube's automatic captions come as one lower-case run with every
*uh* in it: "thanks so uh title slide all right yeah i'm andrew kelly i
am the uh president". A local model writes each paragraph again with
its sentences, capitals and punctuation, the fillers dropped, and
nothing else changed — a copy-edit, not a summary. Each paragraph keeps
its time mark, the frames and headings between them are not touched,
and a paragraph the model changed too much (more words lost than fillers
account for, or words added) keeps its raw form: the model is not
trusted with the content, only with the punctuation. The raw captions
stay in the archived original; the polished text is the artifact, which
a better model may write again.
"""

from __future__ import annotations

import re
from typing import Any

SYSTEM = """\
You copy-edit the automatic transcript of a recorded talk. You are given
its paragraphs, one per line, each opening with its time mark in square
brackets. Write each paragraph again as the speaker meant it: proper
sentences with capitals and punctuation, the filler words dropped (uh,
um, er, you know, like, sort of, kind of, I mean, right?, okay so), a
speech-recognition slip of a single word corrected only where the
intended word is plain from the sentence. Change nothing else: every
fact, name, number, term and the order of things stay as spoken; do not
summarise, do not shorten, do not add a word the speaker did not say, do
not drop a sentence. Keep each paragraph on its own line, opening with
the same time mark. Answer with the paragraphs only, nothing before or
after."""

MARK = re.compile(r"^\[(?P<t>(?:\d{1,2}:)?\d{1,2}:\d{2})\]\s+(?P<words>.+)$")
BATCH_CHARS = 2400  # of paragraphs per call: a talk is a few dozen calls
MAX_TOKENS = 1600
# what a paragraph may lose to the fillers and gain to a corrected word;
# beyond it the model rewrote, and the raw paragraph stays
MIN_KEPT = 0.72
MAX_GROWN = 1.08
FILLERS = re.compile(
    r"\b(?:uh|um|er|erm|hmm|you know|i mean|sort of|kind of|like|okay so|right)\b[,.]?",
    re.IGNORECASE,
)


def paragraphs(text: str) -> list[tuple[int, str, str]]:
    """``(line index, mark, words)`` for every transcript paragraph."""
    out = []
    for i, line in enumerate(text.split("\n")):
        m = MARK.match(line.strip())
        if m:
            out.append((i, m.group("t"), m.group("words")))
    return out


def _words(s: str) -> int:
    return len(s.split())


def acceptable(raw: str, new: str) -> bool:
    """Whether the model's paragraph is the raw one punctuated: the words
    kept, but for the fillers; nothing much added."""
    new = new.strip()
    if not new:
        return False
    raw_n, new_n = _words(raw), _words(new)
    if new_n > MAX_GROWN * raw_n + 2:
        return False
    # the words that may go: the fillers; the rest must stay
    kept_floor = max(MIN_KEPT * raw_n, raw_n - len(FILLERS.findall(raw)) - 2)
    return new_n >= min(kept_floor, raw_n)


def _batches(paras: list[tuple[int, str, str]]) -> list[list[tuple[int, str, str]]]:
    out: list[list[tuple[int, str, str]]] = []
    cur: list[tuple[int, str, str]] = []
    size = 0
    for p in paras:
        n = len(p[2]) + 12
        if cur and size + n > BATCH_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += n
    if cur:
        out.append(cur)
    return out


def polish(previous: str, *, runtime: Any | None = None) -> tuple[str, dict[str, int]]:
    """The text with its transcript paragraphs punctuated; a count of
    what was polished and what stayed raw. ``runtime`` stands in for the
    step's model in tests."""
    from prax import models
    from prax.parsers import ExtractionError

    paras = paragraphs(previous)
    if not paras:
        raise ExtractionError("no transcript paragraphs to polish")
    if runtime is None:
        spec = models.resolve("polish")
        if spec is None:
            raise ExtractionError(
                "no polish model: steps.polish in prax.yaml names one"
            )
        runtime = models.runtime(spec)
    lines = previous.split("\n")
    done = 0
    kept = 0
    for batch in _batches(paras):
        user = "\n".join(f"[{t}] {words}" for _, t, words in batch)
        out, _usage = runtime.chat(SYSTEM, user, max_tokens=MAX_TOKENS, temperature=0.0)
        answered: dict[str, str] = {}
        for line in str(out).split("\n"):
            m = MARK.match(line.strip())
            if m and m.group("t") not in answered:
                answered[m.group("t")] = m.group("words").strip()
        for i, t, raw in batch:
            new = answered.get(t)
            if new is not None and acceptable(raw, new):
                lines[i] = f"[{t}] {new}"
                done += 1
            else:
                kept += 1
    if not done:
        raise ExtractionError("no paragraph could be polished")
    return "\n".join(lines), {"polished": done, "kept_raw": kept}
