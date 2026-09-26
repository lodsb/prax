"""Line-oriented extraction output for local models.

Small models under a JSON-schema grammar spend more than half their output
tokens on structure and, with nothing bounding the triple array, keep
emitting until the token limit (``docs/eval/local-llm-2026-09-08.md``).
This is the same data as ``extraction.output_schema`` as tab-separated
lines, with a hand-written GBNF grammar that bounds the number of lines
and the length of every field::

    summary<TAB>two or three sentences
    triple<TAB>src=...<TAB>src_type=...<TAB>rel=...<TAB>dst=...<TAB>dst_type=...
      <TAB>confidence=...<TAB>evidence=...[<TAB>src_as=...][<TAB>dst_as=...]
    unmapped<TAB>src=...<TAB>rel=...<TAB>dst=...<TAB>reason=...

Fields carry their key: without it a 7B model swaps sources and targets
and writes types into the relation slot (three runs in the eval note);
with it the line reads like the JSON object it replaces, at eight more
tokens per triple.

Tab is the separator because every tokenizer has it as one token, models
write TSV fluently, and no entity name or quoted sentence contains one; the
grammar excludes tabs and line breaks from fields, so nothing is escaped.
The wire format is the model's concern only: ``parse`` returns the same
``Extraction`` the Claude path produces from JSON, and ``render`` is its
inverse (used by tests and to show a model what is wanted).
"""

from __future__ import annotations

import re
from typing import Any

from prax import answers, ontology
from prax.extraction import CONFIDENCES, Extraction, Triple

SEP = "\t"
TRIPLE_KEYS = ("src", "src_type", "rel", "dst", "dst_type", "confidence", "evidence")
UNMAPPED_KEYS = ("src", "rel", "dst", "reason")
MAX_TRIPLES = 20  # same cap as the Claude prompt; small models over-generate
MAX_UNMAPPED = 3  # a 32B model fills five with guesses; three keeps the real misfits
NAME_CHARS = 200
TEXT_CHARS = 300  # an evidence quote, a reason
SUMMARY_CHARS = 700  # the summary: two or three sentences, whole ones


def _alternatives(names: Any) -> str:
    return " | ".join(f'"{n}"' for n in sorted(names))


def grammar(
    onto: ontology.Ontology,
    *,
    max_triples: int = MAX_TRIPLES,
    max_unmapped: int = MAX_UNMAPPED,
) -> str:
    """GBNF for llama.cpp: one summary, 1..max_triples triples, 0..max_unmapped
    unmapped lines; types, relations and confidences as literal alternatives
    from the ontology; fields bounded in length and free of tabs and breaks."""
    return "\n".join(
        [
            f"root ::= summary triple{{1,{max_triples}}} unmapped{{0,{max_unmapped}}}",
            'summary ::= "summary\\t" stext "\\n"',
            (
                'triple ::= "triple\\tsrc=" name "\\tsrc_type=" etype "\\trel=" rel'
                ' "\\tdst=" name "\\tdst_type=" etype "\\tconfidence=" conf'
                ' "\\tevidence=" text srcas? dstas? "\\n"'
            ),
            # the names as printed, where the name written is another
            'srcas ::= "\\tsrc_as=" name',
            'dstas ::= "\\tdst_as=" name',
            (
                'unmapped ::= "unmapped\\tsrc=" name "\\trel=" name "\\tdst=" name'
                ' "\\treason=" text "\\n"'
            ),
            f"etype ::= {_alternatives(onto.entity_types)}",
            f"rel ::= {_alternatives(onto.relations)}",
            f"conf ::= {_alternatives(CONFIDENCES)}",
            f"name ::= char{{1,{NAME_CHARS}}}",
            f"text ::= char{{1,{TEXT_CHARS}}}",
            f"stext ::= char{{1,{SUMMARY_CHARS}}}",
            "char ::= [^\\t\\n\\r]",
        ]
    )


def prompt_section(*, max_triples: int = MAX_TRIPLES) -> str:
    """The format description appended to the system prompt for local models."""
    t = SEP
    return "\n".join(
        [
            (
                "Output format: plain lines with fields separated by one tab"
                f" character, nothing else. First one summary line, then 1 to"
                f" {max_triples} triple lines, then 0 to {MAX_UNMAPPED} unmapped"
                " lines:"
            ),
            f"summary{t}two or three sentences, under {SUMMARY_CHARS} characters",
            (
                f"triple{t}src=<name>{t}src_type=<type>{t}rel=<relation>"
                f"{t}dst=<name>{t}dst_type=<type>{t}confidence=<EXTRACTED or"
                f" INFERRED or AMBIGUOUS>{t}evidence=<verbatim quote>"
                f"[{t}src_as=<name as printed>][{t}dst_as=<name as printed>]"
            ),
            f"unmapped{t}src=<name>{t}rel=<relation>{t}dst=<name>{t}reason=<why>",
            (
                "Angle brackets above mark what you replace: never write a"
                " line that still contains them, and never use the words"
                " inside them as a name."
                " The document's own name is its exact Title line, never the word"
                " 'paper'. Cite other papers by their title only; a bracketed"
                " reference number is not a name. Do not repeat a triple as"
                " unmapped, and do not list citations by number or by author and"
                " year as unmapped: a citation without a title is simply left out."
                " Unmapped is only for a relationship the text states that fits"
                " no relation; never add one to say what the text does not"
                " mention."
                " Names are written as printed, with spaces, never as"
                " identifiers. Where a name is written in another language than"
                " the document prints it, add src_as or dst_as with the printed"
                " word; leave them out otherwise."
                " Fields never contain tabs or line breaks; names"
                f" stay under {NAME_CHARS} and quotes under {TEXT_CHARS}"
                " characters. Fewer good triples beat many weak ones; stop"
                " after the last line."
            ),
        ]
    )


def whole_sentences(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters at the last sentence end past
    the halfway mark, so a summary that ran long loses a sentence rather
    than its last word; a text within the limit is returned as it is."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for mark in (". ", "! ", "? "):
        cut = head.rfind(mark)
        if cut >= limit // 2:
            return head[: cut + 1]
    return head.rstrip()


def parse(text: str) -> Extraction:
    """Lines to an ``Extraction``. Malformed lines are dropped and counted in
    ``usage['dropped_lines']``; repeats of a triple or an unmapped item
    (small models loop) are dropped and counted in ``usage['repeats']``."""
    ex = Extraction()
    dropped = repeats = 0
    seen: set[tuple[str, ...]] = set()
    for raw in text.splitlines():
        fields = [_unkey(f) for f in raw.split(SEP)]
        kind = fields[0]
        if kind == "summary" and len(fields) >= 2 and not ex.summary:
            ex.summary = whole_sentences(
                " ".join(f for f in fields[1:] if f), SUMMARY_CHARS
            )
        elif kind == "triple" and 8 <= len(fields) <= 10 and all(fields[1:7]):
            key = tuple(f.lower() for f in fields[1:6])
            if key in seen:
                repeats += 1
                continue
            seen.add(key)
            src, src_type, rel, dst, dst_type, conf = fields[1:7]
            # the optional fields are read by their key, not their place:
            # either may be there without the other
            printed = {
                m.group(1): m.group(2).strip()
                for f in raw.split(SEP)[8:]
                if (m := _PRINTED.match(_TOKENS.sub("", f).strip()))
            }
            ex.triples.append(
                Triple(
                    _name(src),
                    src_type,
                    rel,
                    _name(dst),
                    dst_type,
                    conf,
                    evidence=fields[7][:TEXT_CHARS],
                    src_as=printed.get("src_as", "")[:NAME_CHARS],
                    dst_as=printed.get("dst_as", "")[:NAME_CHARS],
                )
            )
        elif kind == "unmapped" and len(fields) == 5:
            key = tuple(f.lower() for f in fields[:4])
            if key in seen:
                repeats += 1
                continue
            seen.add(key)
            ex.unmapped.append(
                {
                    "src": _name(fields[1]),
                    "rel": fields[2],
                    "dst": _name(fields[3]),
                    "reason": fields[4],
                }
            )
        elif raw.strip():
            dropped += 1
    if dropped:
        ex.usage["dropped_lines"] = dropped
    if repeats:
        ex.usage["repeats"] = repeats
    return ex


_KEY = re.compile(r"^[a-z_]+=")
_PRINTED = re.compile(r"^(src_as|dst_as)=(.*)$")
_SNAKE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)+$")


_TOKENS = answers.TOKENS  # the chat template leaking into the text


def _unkey(field: str) -> str:
    """``src=Paper X`` -> ``Paper X``; a bare field passes through; a chat
    model's control tokens that leak past the grammar are dropped."""
    return _KEY.sub("", _TOKENS.sub("", field).strip(), count=1).strip()


def _name(value: str) -> str:
    """A 32B model sometimes writes entity names as identifiers
    (``rwc_pop_dataset``); the graph wants them as printed."""
    return value.replace("_", " ") if _SNAKE.match(value) else value


def _clean(s: str) -> str:
    return " ".join(str(s).split())


def _keyed(keys: tuple[str, ...], values: list[str]) -> str:
    return SEP.join(f"{k}={v}" for k, v in zip(keys, values, strict=True))


def render(ex: Extraction) -> str:
    """The inverse of ``parse``."""
    lines = [f"summary{SEP}{_clean(ex.summary)}"]
    for t in ex.triples:
        values = [
            _clean(t.src),
            t.src_type,
            t.rel,
            _clean(t.dst),
            t.dst_type,
            t.confidence,
            _clean(t.evidence),
        ]
        line = "triple" + SEP + _keyed(TRIPLE_KEYS, values)
        for key, said in (("src_as", t.src_as), ("dst_as", t.dst_as)):
            if said:
                line += f"{SEP}{key}={_clean(said)}"
        lines.append(line)
    for u in ex.unmapped:
        values = [_clean(u.get(k, "")) for k in UNMAPPED_KEYS]
        lines.append("unmapped" + SEP + _keyed(UNMAPPED_KEYS, values))
    return "\n".join(lines) + "\n"
