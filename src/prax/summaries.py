"""A document's summary, in one language.

``meta.summary`` is not the author's abstract. It is two or three
sentences the extraction model writes, and it is most of the document
field that ``documents_fts`` and ``document_embeddings`` search — what a
document *is*, next to its title and kind. So the language it is written
in decides which queries can reach the document at all.

Nothing chose that language. The extraction prompt asked for "two or
three sentences a reader would use to decide whether to open the
document" and said nothing else, so the model followed the source when
it felt like it: of 9,030 summaries, 8,746 came out English and 273 did
not, 270 of them German. A German article described in German, the
German article beside it described in English, no rule
(`docs/research-multilingual-2026-09-23.md`).

The prompt says it now, and the field is English. This module is what
brings the ones written before that into line: a translation of the
summary itself, which needs no model that reads the document and no
paid call — a local instruct model turns three German sentences into
three English ones in a second.

Nothing is thrown away. The summary as first written stays under
``meta.summaries`` keyed by its language, beside the English one. A
reader who wants the German summary of a German document, or a field
that carries both so a German query reaches it, has the text already;
what to do with it is `docs/PLAN.md`, not this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from prax import language

CANONICAL = "en"  # the language the document field is written in

SYSTEM = (
    "You translate one short description of a document into English."
    " Return the English text only: no preamble, no quotation marks, no"
    " notes. Keep the meaning, the sentence count and the register. Leave"
    " names of people, organizations, places, products and works exactly as"
    " printed, and leave a term the field uses untranslated where English"
    " uses it too."
)
# a model that has been told not to explain itself and explains itself
_PREAMBLE = re.compile(
    r"^\s*(?:here (?:is|'s) (?:the )?(?:english )?(?:translation|version)"
    r"|english(?: translation| version)?|translation)\s*[:\-—]\s*",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.IGNORECASE)
MAX_GROWTH = 2.5  # a translation longer than this is the model talking


@dataclass
class Translation:
    """The English summary, and what the call cost."""

    text: str
    usage: dict[str, Any] = field(default_factory=dict)


def user_message(summary: str, *, lang: str | None = None, title: str = "") -> str:
    """What the model is given: the summary, and the title as context for
    the names in it (an untranslated title is a hint, not an instruction)."""
    named = language.name(lang) or "another language"
    parts = [f"Document title: {title.strip()}"] if title.strip() else []
    parts.append(f"Description, written in {named}:")
    parts.append(summary.strip())
    return "\n".join(parts)


def parse(out: str) -> str | None:
    """The English text out of what the model returned, or None when it
    returned nothing usable."""
    text = _FENCE.sub("", (out or "").strip()).strip()
    text = _PREAMBLE.sub("", text).strip()
    if len(text) >= 2 and text[0] in "\"'“„«" and text[-1] in "\"'”“»":
        text = text[1:-1].strip()
    return text or None


def acceptable(text: str, original: str) -> str | None:
    """Why the translation cannot be used, or None when it can.

    Three ways a small model fails this task and one of them is silent:
    it hands the German back, it answers about the text instead of
    translating it, or it writes an essay. Length catches the third,
    the detector the first.
    """
    if len(text) > max(400, len(original) * MAX_GROWTH):
        return "too long"
    if len(text) < len(original) / 3:
        return "too short"
    if text.strip() == original.strip():
        return "unchanged"
    code = language.detect(text)
    if code is not None and code != CANONICAL:
        return f"still {code}"
    return None


def keep(meta: dict[str, Any], text: str, *, lang: str | None = None) -> str | None:
    """File a summary in a document's ``meta`` and say what language it
    was filed under.

    ``meta.summary`` is the one the document field indexes and is English
    wherever an English one exists; ``meta.summaries`` holds every one we
    have, keyed by language, so the German summary of a German document
    is never lost to the translation that replaced it. A document whose
    only summary is German keeps it as the canonical one: worse for a
    search than English, better than no summary at all.

    A summary too short to place is filed without a language rather than
    as English, and ``meta.summary_lang`` stays absent: the pass that
    hands documents to a model asks for the ones known to be in another
    language, never for the ones nothing could read.
    """
    code = lang or language.detect(text)
    held = meta.setdefault("summaries", {})
    if code and isinstance(held, dict):
        held[code] = text
    if code == CANONICAL or not meta.get("summary"):
        meta["summary"] = text
        if code:
            meta["summary_lang"] = code
        else:
            meta.pop("summary_lang", None)
    return code


def translate(
    runtime: Any,
    summary: str,
    *,
    lang: str | None = None,
    title: str = "",
) -> Translation | None:
    """The summary in English, or None when the model did not manage it.

    The caller keeps the original either way: a summary nobody could
    translate stays as it was written, which is worse for a search and
    better than a paragraph of the model thinking aloud.
    """
    out, usage = runtime.chat(
        SYSTEM,
        user_message(summary, lang=lang, title=title),
        max_tokens=max(200, len(summary) // 2),
        temperature=0.0,
    )
    text = parse(out)
    if text is None or acceptable(text, summary) is not None:
        return None
    return Translation(text=text, usage=usage)
