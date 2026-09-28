"""What a model wrapped its answer in, taken off.

A model told to answer with a title and nothing else writes ``Title: The
Sound of…``. Told to answer with a translation and nothing else, it hands
back the labels of the message it was given. Told to answer with a term,
it adds a full stop, or quotation marks, or both in the wrong order, or a
sentence explaining itself, or — where the chat template leaks — a
``<tool_call>`` tag.

Seven modules met those failures separately and each kept its own
patterns: `titles` since it was written, `lineformat` for the local
extractor, `typing_pass`, `surf`, `ask`, and this week `summaries` and
`vocabulary`. A failure caught in one was caught in none of the others,
which is how four summaries came back wearing ``Document title:`` a
fortnight after `titles` had learned to strip a label
(`docs/stratification.md`).

So this module holds the wrapping and nothing else. What a *good* answer
looks like stays with the caller: whether a title is plausible, whether a
translation is still German, whether a name is a description — those are
about the answer, and this is about the packaging.
"""

from __future__ import annotations

import re

# a chat template leaking into the text
TOKENS = re.compile(r"</?tool_call>|<\|im_end\|>|<\|endoftext\|>|</?think>")
# a fenced block the model put its answer in
FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.IGNORECASE)
# "Title:", "Answer:", "Description, written in German:" — a label, whether
# the message used it or the model invented it
LABEL = re.compile(
    r"^\s*(?:the\s+)?(?:document\s+|english\s+)?"
    r"(?:title|titel|dokument|document|answer|antwort|description|summary|text"
    r"|translation|name|term|output|result|response)"
    # "The English name **is**: olive oil"
    r"(?:\s+(?:is|ist|would be))?"
    r"(?:\s*,[^:\n]{0,40})?"
    # the colon, or — where the model left it out — the quote that opens
    # the answer: 'The English name is "olive oil".'
    r"(?:\s*[:=]\s*|\s+(?=[\"'“„«‘]))",
    re.IGNORECASE,
)
# "Here is the translation:", "Sure! Here's the title —". The apostrophe
# form has no space before the 's
PREAMBLE = re.compile(
    r"^\s*(?:sure[!,.]?\s*)?(?:here\s*(?:is|'s)|this\s+is)\s+(?:the\s+)?"
    r"[^:\n]{0,40}?\s*[:\-—]\s*",
    re.IGNORECASE,
)
QUOTES_OPEN = "\"'“„«‘"
QUOTES_CLOSE = "\"'”“»’"


def strip_tokens(text: str) -> str:
    """Chat-template tokens and anything after the first of them."""
    return TOKENS.split(text or "", 1)[0]


def unwrap(text: str, *, sentence: bool = False) -> str:
    """One answer with its packaging removed.

    Fences, template tokens, a preamble, a label, quotation marks and a
    trailing full stop, taken off in whichever order the model put them
    on — a full stop outside the quotes and a quote outside the full stop
    are both things a model does, so it loops until neither is there.

    ``sentence`` keeps a final full stop, for an answer that is prose (a
    summary) rather than a name.
    """
    out = FENCE.sub("", strip_tokens(text).strip()).strip()
    for _ in range(4):
        was = out
        out = PREAMBLE.sub("", out).strip()
        out = LABEL.sub("", out).strip()
        if not sentence:
            out = out.rstrip(".").strip()
        if len(out) >= 2 and out[0] in QUOTES_OPEN and out[-1] in QUOTES_CLOSE:
            out = out[1:-1]
        out = out.strip()
        if out == was:
            break
    return out


def first_line(text: str, *, sentence: bool = False) -> str | None:
    """The first line of an answer that carries anything, unwrapped, or
    None when none does. For an answer that is one name or one title and
    may be followed by the model explaining itself."""
    for line in (text or "").splitlines():
        got = unwrap(line, sentence=sentence)
        if got:
            return got
    return None


def is_label_line(text: str) -> bool:
    """Whether a line is the message's own label handed back — the failure
    that put ``Document title: …`` at the head of four summaries."""
    return bool(LABEL.match(text or ""))
