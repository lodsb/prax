"""Line-oriented extraction output for local models.

Small models under a JSON-schema grammar spend more than half their output
tokens on structure and, with nothing bounding the triple array, keep
emitting until the token limit (``docs/eval/local-llm-2026-09-08.md``).
This is the same data as ``extraction.output_schema`` as tab-separated
lines, with a hand-written GBNF grammar that bounds the number of lines
and the length of every field::

    summary<TAB>two or three sentences
    triple<TAB>src=...<TAB>src_type=...<TAB>rel=...<TAB>dst=...<TAB>dst_type=...
      <TAB>confidence=...<TAB>evidence=...
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

from prax import ontology
from prax.extraction import CONFIDENCES, Extraction, Triple

SEP = "\t"
TRIPLE_KEYS = ("src", "src_type", "rel", "dst", "dst_type", "confidence", "evidence")
UNMAPPED_KEYS = ("src", "rel", "dst", "reason")
MAX_TRIPLES = 20  # same cap as the Claude prompt; small models over-generate
MAX_UNMAPPED = 5
NAME_CHARS = 200
TEXT_CHARS = 300


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
            'summary ::= "summary\\t" text "\\n"',
            (
                'triple ::= "triple\\tsrc=" name "\\tsrc_type=" etype "\\trel=" rel'
                ' "\\tdst=" name "\\tdst_type=" etype "\\tconfidence=" conf'
                ' "\\tevidence=" text "\\n"'
            ),
            (
                'unmapped ::= "unmapped\\tsrc=" name "\\trel=" name "\\tdst=" name'
                ' "\\treason=" text "\\n"'
            ),
            f"etype ::= {_alternatives(onto.entity_types)}",
            f"rel ::= {_alternatives(onto.relations)}",
            f"conf ::= {_alternatives(CONFIDENCES)}",
            f"name ::= char{{1,{NAME_CHARS}}}",
            f"text ::= char{{1,{TEXT_CHARS}}}",
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
            f"summary{t}two or three sentences",
            (
                f"triple{t}src=source name{t}src_type=source type{t}rel=relation"
                f"{t}dst=target name{t}dst_type=target type{t}confidence=..."
                f"{t}evidence=verbatim quote"
            ),
            f"unmapped{t}src=source name{t}rel=relation{t}dst=target name{t}reason=...",
            (
                "The document's own name is its exact Title line, never the word"
                " 'paper'. Cite other papers by their title only; a bracketed"
                " reference number is not a name. Do not repeat a triple as"
                " unmapped. Fields never contain tabs or line breaks; names"
                f" stay under {NAME_CHARS} and quotes under {TEXT_CHARS}"
                " characters. Fewer good triples beat many weak ones; stop"
                " after the last line."
            ),
        ]
    )


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
            ex.summary = " ".join(f for f in fields[1:] if f)[:TEXT_CHARS]
        elif kind == "triple" and len(fields) == 8 and all(fields[1:7]):
            key = tuple(f.lower() for f in fields[1:6])
            if key in seen:
                repeats += 1
                continue
            seen.add(key)
            ex.triples.append(Triple(*fields[1:7], evidence=fields[7][:TEXT_CHARS]))
        elif kind == "unmapped" and len(fields) == 5:
            key = tuple(f.lower() for f in fields[:4])
            if key in seen:
                repeats += 1
                continue
            seen.add(key)
            ex.unmapped.append(
                {
                    "src": fields[1],
                    "rel": fields[2],
                    "dst": fields[3],
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


def _unkey(field: str) -> str:
    """``src=Paper X`` -> ``Paper X``; a bare field passes through."""
    return _KEY.sub("", field.strip(), count=1).strip()


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
        lines.append("triple" + SEP + _keyed(TRIPLE_KEYS, values))
    for u in ex.unmapped:
        values = [_clean(u.get(k, "")) for k in UNMAPPED_KEYS]
        lines.append("unmapped" + SEP + _keyed(UNMAPPED_KEYS, values))
    return "\n".join(lines) + "\n"
