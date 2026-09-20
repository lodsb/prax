"""An automatic transcript, punctuated: the paragraphs of a talk written
as the speaker meant them.

YouTube's automatic captions come as one lower-case run with every
*uh* in it: "thanks so uh title slide all right yeah i'm andrew kelly i
am the uh president". A local model writes each paragraph again with
its sentences, capitals and punctuation, the fillers dropped, and
nothing else changed — a copy-edit, not a summary. One paragraph per
call, with a worked example in front of the model: given six at once, a
small model polishes the first and copies the rest. Each paragraph
keeps its time mark, the frames and headings between them are not
touched, and a paragraph the model changed too much (more words lost
than fillers account for, or words added) keeps its raw form: the model
is trusted with the punctuation, not the content. The raw captions stay
in the archived original; the polished text is the artifact, which a
better model may write again.
"""

from __future__ import annotations

import re
from typing import Any

SYSTEM = """\
You copy-edit one paragraph of the automatic transcript of a recorded
talk. Write it again as the speaker meant it: proper sentences with
capitals and punctuation, the filler words dropped (uh, um, er, you
know, like, sort of, kind of, I mean, right?, okay so), a speech
recognition slip of a single word corrected only where the intended word
is plain from the sentence. Change nothing else: every fact, name,
number, term and the order of things stay as spoken; do not summarise,
do not shorten, do not add a word the speaker did not say, do not drop a
sentence, do not answer or comment. Answer with the paragraph only.

Example. Given:
thanks so uh title slide all right yeah i'm andrew kelly i am the uh \
president and lead software developer of the zig software foundation \
and uh thanks for coming to my talk so uh first thing um where am i pointing
You answer:
Thanks. So, title slide. All right, yeah, I'm Andrew Kelly. I am the \
president and lead software developer of the Zig Software Foundation, \
and thanks for coming to my talk. So, first thing: where am I pointing?"""

MARK = re.compile(r"^\[(?P<t>(?:\d{1,2}:)?\d{1,2}:\d{2})\]\s+(?P<words>.+)$")
MAX_TOKENS = 700  # a paragraph is under 450 characters; the answer about as long
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
    for i, t, raw in paras:
        out, _usage = runtime.chat(SYSTEM, raw, max_tokens=MAX_TOKENS, temperature=0.0)
        new = " ".join(str(out).split())
        m = MARK.match(new)
        if m:  # the mark given back with the words: only the words are wanted
            new = m.group("words")
        if new and new != raw and acceptable(raw, new):
            lines[i] = f"[{t}] {new}"
            done += 1
        else:
            kept += 1
    if not done:
        raise ExtractionError("no paragraph could be polished")
    recaption(lines)
    return "\n".join(lines), {"polished": done, "kept_raw": kept}


FIGURE = re.compile(
    r"^!\[(?P<t>(?:\d{1,2}:)?\d{1,2}:\d{2})\s*[—–-]\s*(?P<words>[^\]]*)\]"
    r"\(figure:(?P<ref>[0-9a-f]{16,64})\)$"
)
CAPTION_WORDS = 12


def recaption(lines: list[str]) -> int:
    """A frame's caption is the opening words of the paragraph at its
    moment, written by the extension from the raw captions: after the
    polish, the same words as the polished paragraph has them (what the
    vision model is told, and what the page shows under the frame)."""
    by_mark = {t: words for _, t, words in paragraphs("\n".join(lines))}
    n = 0
    for i, line in enumerate(lines):
        m = FIGURE.match(line.strip())
        if not m:
            continue
        words = by_mark.get(m.group("t"))
        if not words:
            continue
        head = " ".join(words.split()[:CAPTION_WORDS]).replace("]", ")")
        new = f"![{m.group('t')} — {head}](figure:{m.group('ref')})"
        if new != line.strip():
            lines[i] = new
            n += 1
    return n
