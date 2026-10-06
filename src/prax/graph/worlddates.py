"""When a fact holds in the world, read from the sentences that say so
(AL step 5).

Asked of the general extraction, a model gave no world date at all on
five documents that state them: its twenty triples go to what its prompt
ranks first, and a dated fact is rarely among them
(``docs/eval/extraction-standard-names-and-dates-2026-10-05.md``). This
reading asks for nothing else. It reads only the sentences of a
document's text chunks that hold a year and a word of lasting or
happening (``world_time:`` in ``ontology/lexicon.yaml``: "since",
"founded", "joined", "until"), with only the relations whose ``kind`` is
``state`` or ``event``, and asks which facts those sentences date.

A fact is kept only when its quote is in one of the sentences it was
shown and each date's year is in the quote (``extraction.checked_date``),
and the ontology takes it: a date the text does not say is never written.
The door's half (``apply``) ends an undated edge of the same fact and
document and states it again with its dates (``edge_endings`` with
``corrected_by``, so ``restore_run`` undoes a run whole), or links the
fact anew when the document had not stated it.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from prax import models, store
from prax.graph import extraction, lineformat, ontology
from prax.graph.extraction import Triple
from prax.text import dates, quotes

DATED_KINDS = ("state", "event")
MAX_SENTENCES = 12  # a document's worth: the model call stays one short one
SENTENCE_CHARS = 400
MIN_SENTENCE = 30
MAX_FACTS = 8
PRODUCER = "world-dates"

_YEAR = re.compile(r"(?<![\d.,/-])(1[5-9]\d\d|20[0-4]\d)(?![\d/])")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
# a year in a citation is a year of the work cited: "(Smith 2003)",
# "(Smith et al., 2003; Jones 1999)", "Smith (2003)", "[12]"
_CITATION = re.compile(
    r"\([^()]*?[A-Z][^()]*?\b(1[5-9]\d\d|20[0-4]\d)[a-z]?\b[^()]*\)"
    r"|\b[A-Z][\w'-]+(?: et al\.?)?,? \((1[5-9]\d\d|20[0-4]\d)[a-z]?\)"
    r"|\[\d+(?:[,–-]\s*\d+)*\]"
)
_SPACE = re.compile(r"\s+")
# what a model writes for an end the sentence does not name
_STAND_IN = re.compile(
    r"(?i)^(the )?(author|authors|he|she|they|we|company|unknown|none)$"
)
# a name that is a date ("1984", "June 1977", "the mid-1980s"): a model
# that has nowhere else to put the date makes it the fact's other end
_DATE_NAME = re.compile(
    r"(?i)^\W*(the\s+)?((early|mid|late)[-\s]*)?(\d{1,2}\.?\s+)?"
    rf"(({'|'.join(dates.month_names())})\.?\s+)?(\d{{1,2}},?\s+)?"
    r"(1[5-9]\d\d|20\d\d)s?(\s*[-–]\s*\d{2,4})?\W*$"
)


@dataclass(frozen=True)
class Cues:
    stems: tuple[str, ...]
    words: frozenset[str]

    def said(self, sentence: str) -> bool:
        for w in re.findall(r"[^\W\d_]+", sentence.lower()):
            if w in self.words or w.startswith(self.stems):
                return True
        return False


def cues() -> Cues:
    """The lexicon's ``world_time:`` words."""
    section = ontology.lexicon().section("world_time") or {}
    return Cues(
        tuple(str(s).lower() for s in section.get("stems") or []),
        frozenset(str(w).lower() for w in section.get("words") or []),
    )


def sentences(texts: list[str], *, limit: int = MAX_SENTENCES) -> list[str]:
    """The sentences of a document's text chunks that may date a fact: a
    year outside a citation and a word of the ``world_time`` cues, each
    whole and at most ``SENTENCE_CHARS`` long, the first ``limit`` of them
    in the document's order, none twice."""
    said = cues()
    out: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for s in _SENTENCE.split(text):
            s = _SPACE.sub(" ", s).strip()
            if not MIN_SENTENCE <= len(s) <= SENTENCE_CHARS or s in seen:
                continue
            if not _YEAR.search(_CITATION.sub(" ", s)) or not said.said(s):
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= limit:
                return out
    return out


def dated_relations(onto: ontology.Ontology) -> list[ontology.Relation]:
    """The relations a world date fits: a fact that lasts or happens
    (``state``, ``event``), and one that holds for good but was done at a
    time by someone: who made a thing, which is ``eternal`` and has a
    person or an organization at its end. Not who wrote a document, whose
    date is when it appeared (``meta.published``)."""
    docs = {t for t in onto.entity_types if onto.is_a(t, "document")}
    agents = {"person", "organization"}

    def fits(r: ontology.Relation) -> bool:
        if r.kind in DATED_KINDS:
            return True
        made = bool(r.domain) and not set(r.domain) <= docs
        return r.kind == "eternal" and made and bool(agents & set(r.range))

    return [
        onto.relations[n] for n in sorted(onto.relations) if fits(onto.relations[n])
    ]


INTRO = (
    "You date facts for a research library. You see numbered sentences from"
    " one document. Find the facts these sentences give a date for: when"
    " something began, ended or happened (an affiliation from 2015, a company"
    " founded in 1975, a device made until 2003, a work published in 1999)."
    " Work only from the sentences."
)
RULES = (
    (
        "A fact only where a sentence states its date. The year of a cited"
        " work, a version number or a page number is not a date of a fact."
    ),
    (
        "from and to: YYYY, YYYY-MM or YYYY-MM-DD, as precise as the sentence."
        " Give from when it began or happened, and to only when the sentence"
        " says when it ended; leave to out otherwise. An organization bought"
        " by another is part_of it from that date."
    ),
    (
        "evidence: the words of one sentence, copied exactly, that hold the"
        " fact and its year (under 200 characters)."
    ),
    (
        "Both ends are named things as the sentence prints them, never"
        " translated: a person, an organization, a place, a device, a work. A"
        " date is never a name; it goes in from or to. Never write 'author',"
        " 'he', 'the company' or another stand-in: where the sentence does"
        " not name the thing, leave the fact out."
    ),
    (
        "confidence: EXTRACTED when the sentence states the fact, INFERRED"
        " when it clearly follows."
    ),
    f"At most {MAX_FACTS} facts. If no sentence dates a fact, answer: none",
)
_T = lineformat.SEP
LINE = (
    f"triple{_T}src=<name>{_T}src_type=<type>{_T}rel=<relation>{_T}dst=<name>"
    f"{_T}dst_type=<type>{_T}confidence=<EXTRACTED or INFERRED>"
    f"{_T}evidence=<verbatim words>{_T}from=<YYYY[-MM[-DD]]>"
    f"[{_T}to=<YYYY[-MM[-DD]]>]"
)


def system_prompt(onto: ontology.Ontology) -> str:
    """The entity types, the dated relations and the rules, from the
    ontology, so the prompt and the check agree."""
    ent = "\n".join(
        f"- {name}: {extraction._desc(onto, name)}"
        for name in sorted(onto.entity_types)
    )
    rel = "\n".join(
        f"- {r.name} ({', '.join(sorted(r.domain)) or 'any'} -> "
        f"{', '.join(sorted(r.range)) or 'any'}): {r.description}"
        for r in dated_relations(onto)
    )
    return "\n".join(
        [
            INTRO,
            "",
            "Entity types:",
            ent,
            "",
            "Relations, with the allowed source -> target types:",
            rel,
            "",
            "Rules:",
            *(f"- {r}" for r in RULES),
            "",
            "Answer with one line a fact, the fields separated by one tab:",
            LINE,
            (
                "Angle brackets mark what you replace: never write them. from"
                " or to may be left out, not both. Fields never contain tabs."
            ),
        ]
    )


def grammar(onto: ontology.Ontology) -> str:
    """GBNF: ``none``, or one to ``MAX_FACTS`` triple lines of the dated
    relations, each with a from or a to."""
    names = lineformat._alternatives
    return "\n".join(
        [
            f'root ::= "none\\n" | triple{{1,{MAX_FACTS}}}',
            (
                'triple ::= "triple\\tsrc=" name "\\tsrc_type=" etype "\\trel=" rel'
                ' "\\tdst=" name "\\tdst_type=" etype "\\tconfidence=" conf'
                ' "\\tevidence=" text dates "\\n"'
            ),
            "dates ::= from to? | to",
            'from ::= "\\tfrom=" date',
            'to ::= "\\tto=" date',
            'date ::= [0-9]{4} ("-" [0-9]{2} ("-" [0-9]{2})?)?',
            f"etype ::= {names(onto.entity_types)}",
            f"rel ::= {names(r.name for r in dated_relations(onto))}",
            'conf ::= "EXTRACTED" | "INFERRED"',
            f"name ::= char{{1,{lineformat.NAME_CHARS}}}",
            f"text ::= char{{1,{lineformat.TEXT_CHARS}}}",
            "char ::= [^\\t\\n\\r]",
        ]
    )


def user_message(title: str, said: list[str]) -> str:
    lines = [f"Document: {title}", "", "Sentences:"]
    lines += [f"{i}. {s}" for i, s in enumerate(said, 1)]
    return "\n".join([*lines, "", "Answer:"])


def _norm(s: str) -> str:
    return _SPACE.sub(" ", s.replace("“", '"').replace("”", '"')).strip().lower()


def checked(
    triples: list[Triple], said: list[str], onto: ontology.Ontology
) -> list[Triple]:
    """The triples the sentences bear out: a dated relation the ontology
    takes for these types, a quote that is in one of the sentences, and a
    date whose year is in that quote, and two ends that are named things
    (not a date, not a stand-in). The dates are rewritten as checked."""
    shown = [_norm(s) for s in said]
    dated = {r.name for r in dated_relations(onto)}
    out = []
    for t in triples:
        quote = _norm(t.evidence)
        if t.rel not in dated or len(quote) < 8 or not any(quote in s for s in shown):
            continue
        # no "ended, date unknown" from this reading: asked for it, the 27B
        # wrote it on every fact, and none of their sentences said so
        begin = extraction.checked_date(t.world_from, t.evidence) or ""
        end = extraction.checked_date(t.world_to, t.evidence) or ""
        if not begin and not end:
            continue
        if extraction._drop_reason(t) or any(
            _DATE_NAME.match(n) or _STAND_IN.match(n) for n in (t.src, t.dst)
        ):
            continue
        try:
            onto.check_edge(t.src_type, t.rel, t.dst_type)
            onto.check_names(t.src, t.src_type, t.rel, t.dst, t.dst_type)
        except ValueError:
            continue
        t.world_from, t.world_to = begin, end
        out.append(t)
    return out


def read(
    runtime: models.Runtime, title: str, said: list[str], onto: ontology.Ontology
) -> tuple[list[Triple], dict[str, Any]]:
    """Ask the model, check what it gives, and ask it again of each fact
    that passed whether its sentence says so (``supported``); the facts
    kept and the usage. A fact the judge gives no probability for is not
    kept: unverified is not good enough to write."""
    line = supported_line()
    text, usage = runtime.chat(
        system_prompt(onto),
        user_message(title, said),
        grammar=grammar(onto),
        max_tokens=1200,
        temperature=0.0,
    )
    got = lineformat.parse(text)
    passed = checked(got.triples, said, onto)
    kept = []
    for t in passed:
        p = supported(runtime, sentence_of(t, said), statement(t, onto))
        if p is not None and p >= line:
            kept.append(t)
    usage = {**usage, "proposed": len(got.triples), "checked": len(passed)}
    return kept, usage


# the judge's P(yes) a fact needs: at 0.9, 18 of 18 kept facts were right
# on two samples, at 0.5 22 of 26 (docs/eval/world-dates-2026-10-06.md)
SUPPORTED = 0.9
JUDGE = (
    "Does the passage state this fact, with this date as the date of this"
    " fact? A date of something else in the passage does not count, nor a"
    " fact the passage does not say. Answer yes or no."
)


def supported_line() -> float:
    """``steps.worlddates.supported`` in prax.yaml, else ``SUPPORTED``."""
    try:
        return float(models.settings("worlddates").get("supported", SUPPORTED))
    except (TypeError, ValueError):
        return SUPPORTED


def sentence_of(t: Triple, said: list[str]) -> str:
    """The sentence the fact's quote is in."""
    quote = _norm(t.evidence)
    return next((s for s in said if quote in _norm(s)), t.evidence)


def statement(t: Triple, onto: ontology.Ontology) -> str:
    """The fact as a plain sentence for the judge: "Live developed by
    Ableton, in 2001"; "… affiliated with Cooper, since 1999" for a
    state; an event's place "in" its date, as a happening is."""
    rel = onto.relations.get(t.rel)
    words = t.rel.replace("_", " ")
    begin, end = _spoken(t.world_from), _spoken(t.world_to)
    happens = rel is None or rel.kind != "state" or onto.is_a(t.src_type, "event")
    if begin and (end == begin or not end) and happens:
        when = f", {'on' if len(t.world_from) == 10 else 'in'} {begin}"
    elif begin and end and end != begin:
        when = f", from {begin} until {end}"
    elif begin:
        when = f", since {begin}"
    else:
        when = f", until {end}"
    return f"{t.src} {words} {t.dst}{when}."


_MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _spoken(date: str) -> str:
    """``2003-10-11`` as "October 11, 2003", ``1998-08`` as "August 1998":
    a sentence spells its dates, and the judge compares words."""
    if len(date) >= 7 and date[5:7].isdigit() and 1 <= int(date[5:7]) <= 12:
        month = _MONTH_NAMES[int(date[5:7]) - 1]
        return (
            f"{month} {int(date[8:10])}, {date[:4]}"
            if len(date) == 10
            else (f"{month} {date[:4]}")
        )
    return date


def supported(runtime: Any, sentence: str, claim: str) -> float | None:
    """The model's P(yes) that the sentence states the claim, read from
    the first token's log probabilities; None where the runtime is not a
    served model that gives them, or gives none."""
    from prax.graph import calibration

    base_url = getattr(runtime, "base_url", None)
    model = getattr(runtime, "model", None)
    if not base_url or not model:
        return None
    prompt = f"{JUDGE}\n\nPassage: {sentence}\n\nFact: {claim}"
    top = models.top_logprobs(base_url, model, [{"role": "user", "content": prompt}])
    return None if top is None else calibration.yes_probability(top)


# ------------------------------------------------------------- the door's half


@dataclass
class Applied:
    restated: int = 0  # an undated edge of the document ended, stated with dates
    linked: int = 0  # a fact the document had not stated
    existing: int = 0  # the document already states it with dates
    refused: int = 0  # what the ontology or the checks refuse here
    edges: list[int] = field(default_factory=list)


def as_triple(r: dict[str, Any]) -> Triple:
    """A fact as the worker posts it."""
    return Triple(
        str(r.get("src") or ""),
        str(r.get("src_type") or ""),
        str(r.get("rel") or ""),
        str(r.get("dst") or ""),
        str(r.get("dst_type") or ""),
        str(r.get("confidence") or "EXTRACTED"),
        str(r.get("evidence") or ""),
        world_from=str(r.get("from") or ""),
        world_to=str(r.get("to") or ""),
    )


def as_result(t: Triple) -> dict[str, Any]:
    """A checked fact as the worker posts it."""
    return {
        "src": t.src,
        "src_type": t.src_type,
        "rel": t.rel,
        "dst": t.dst,
        "dst_type": t.dst_type,
        "confidence": t.confidence,
        "evidence": t.evidence,
        "from": t.world_from,
        "to": t.world_to,
    }


def apply(
    con: sqlite3.Connection,
    doc_id: int,
    facts: list[dict[str, Any]],
    said: list[str],
    *,
    model: str,
    run: str,
) -> Applied:
    """Write a document's dated facts and stamp it (``meta.world_dates``).
    The facts are checked again against the sentences the door handed
    out, since a worker is a client: nothing it posts is believed alone."""
    onto = ontology.current().for_domains(store.document_domains(con, doc_id))
    triples = [as_triple(f) for f in facts]
    good = checked(triples, said, onto)
    report = Applied(refused=len(triples) - len(good))
    read_doc = store.get_document(con, doc_id)
    text, text_hash = (
        (read_doc["text"], read_doc["text_hash"]) if read_doc else ("", None)
    )
    producer = f"{PRODUCER}:{model}"
    for t in good:
        edge = store.Edge(t.src, t.src_type, t.rel, t.dst, t.dst_type)
        at = quotes.place(text, t.evidence) if text_hash else None
        place = (at[0], at[1], str(text_hash)) if at else None
        mine = store.document_edges_of(con, edge, doc_id)
        if any(e["world_from"] or e["world_to_precision"] for e in mine):
            report.existing += 1
            continue
        new = store.link(
            con,
            edge,
            confidence=t.confidence,
            source_doc=doc_id,
            ontology_version=onto.version,
            evidence=t.evidence,
            producer=producer,
            run=run,
            world_from=t.world_from or None,
            world_to=t.world_to or None,
            place=place,
        )
        report.edges.append(new)
        if not mine:
            report.linked += 1
            continue
        for e in mine:  # the same fact, undated: this one replaces it
            store.end_edge(con, int(e["id"]), run=run, corrected_by=new)
        report.restated += 1
    store.stamp_world_dates(
        con,
        doc_id,
        {
            "run": run,
            "model": model,
            "at": store.now(),
            "text_hash": text_hash,
            "sentences": len(said),
            "restated": report.restated,
            "linked": report.linked,
            "existing": report.existing,
            "refused": report.refused,
        },
    )
    return report
