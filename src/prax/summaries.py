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

from dataclasses import dataclass, field
from typing import Any

from prax import answers, language

CANONICAL = "en"  # the language the document field is written in

SYSTEM = (
    "You translate one short description of a document into English."
    " Answer with the English text and nothing else: the first word of your"
    " answer is the first word of the translation. No preamble, no quotation"
    " marks, no notes, and never repeat the instruction, the title or a"
    " label from the message. Keep the meaning, the sentence count and the"
    " register. Leave names of people, organizations, places, products and"
    " works exactly as printed, and leave a term the field uses untranslated"
    " where English uses it too."
)
# the preamble, the fence, the quotes and the message's own labels are
# `prax.answers` — every module that calls a model met them separately
MAX_GROWTH = 2.5  # a translation longer than this is the model talking


@dataclass
class Translation:
    """The English summary, and what the call cost."""

    text: str
    usage: dict[str, Any] = field(default_factory=dict)


def user_message(summary: str, *, lang: str | None = None, title: str = "") -> str:
    """What the model is given: an instruction and the text.

    Written as sentences rather than labelled fields, because a model
    handed ``Document title: …\nDescription: …`` fills the form in and
    returns the labels with the answer (2026-09-24, four of the first
    eight). The title is context for the names in the text and is named
    inside the sentence, where there is nothing to copy.
    """
    named = language.name(lang) or "another language"
    parts = []
    if title.strip():
        parts.append(
            f"The document is called \u201c{title.strip()}\u201d. That is context"
            " for the names below, not part of what you translate."
        )
    parts.append(f"Translate this {named} text into English.")
    parts.append("")
    parts.append(summary.strip())
    return "\n".join(parts)


def parse(out: str) -> str | None:
    """The English text out of what the model returned, or None when it
    returned nothing usable.

    The wrapping — a fence, a preamble, a label the message used, quotes —
    is ``prax.answers``; what is left here is the one thing peculiar to a
    summary, which is that the model may put the title on its own line
    above the description.
    """
    text = answers.FENCE.sub("", answers.strip_tokens(out or "").strip()).strip()
    # "Document title: X\nDescription, written in German:\n<the English>":
    # a model handed labelled fields fills the form in, and the answer is
    # under the labels. A label on the *last* line is kept, because what
    # follows it on that line is the answer
    lines = text.splitlines()
    while len(lines) > 1 and answers.is_label_line(lines[0].strip()):
        lines = lines[1:]
    return answers.unwrap("\n".join(lines), sentence=True) or None


def acceptable(text: str, original: str) -> str | None:
    """Why the translation cannot be used, or None when it can.

    Four ways a small model fails this task and two of them are silent:
    it hands the German back, it fills in the form the message looked
    like and returns the labels with the answer, it answers about the
    text instead of translating it, or it writes an essay. Length catches
    the last, the detector the first, and this is where a translation
    that came back wearing the prompt is caught rather than stored.
    """
    if answers.is_label_line(text):
        return "the prompt's labels"
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


def native(held: dict[str, Any]) -> tuple[str, str] | None:
    """The summary as first written, ``(language, text)``, out of
    ``meta.summaries`` — or None when the only one there is English.

    It is what a translation is made from and remade from: translating a
    translation compounds whatever the first one got wrong.
    """
    for code, text in (held or {}).items():
        if code != CANONICAL and isinstance(text, str) and text.strip():
            return str(code), text
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
    if isinstance(held, dict):
        # the one already there goes in first, under its own language. It
        # got here before ``meta.summaries`` existed, so nothing else
        # would file it, and the first batch of translations overwrote
        # eight German summaries that this line would have kept
        # (2026-09-24)
        there, there_lang = meta.get("summary"), meta.get("summary_lang")
        if there and there_lang and there_lang not in held:
            held[str(there_lang)] = there
        if code:
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
