"""The model typing pass over the review queue.

The local extractor sometimes names two things and a relation but not
what kind of things they are ("Cuckoo Hashing for Undergraduates
published_in European Symposium on Algorithms", no types). The typing
rules take the shapes that are safe to decide by name; what they leave
is put to a model, a batch at a time, with the document's title, its own
type and the ontology subset the document is read against: for each
item, the type of each end, or ``none`` when the end is not a nameable
thing. An answer that fits the ontology becomes an INFERRED edge with
the item's evidence and source document, producer ``typing:<model>``,
one run id per pass; ``none`` drops the item; anything else leaves it
open. Which model is the ``typing`` step in ``prax.yaml``
(``PRAX_TYPING=server-35b`` for one run); ``none`` means the pass is
off. Nothing here writes to SQLite except through ``prax.store``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prax import models, ontology, store
from prax.review import REMAP, RETYPE, _doc_titles, _placeholder

STEP = "typing"
PRODUCER_PREFIX = "typing"
BATCH = 24  # items per request
NONE = "none"
_ANSWER = re.compile(
    r"^\s*(\d+)\s*[:.)]\s*([a-z_]+)\s*(?:->|→|,|\|)\s*([a-z_]+)\s*$", re.IGNORECASE
)


@dataclass
class Batch:
    items: list[dict[str, Any]]
    docs: dict[int, tuple[str, str]]  # doc id -> (title, own type)
    onto: ontology.Ontology


@dataclass
class ModelTypingReport:
    checked: int = 0
    linked: int = 0
    existing: int = 0
    dropped: int = 0
    misfit: int = 0  # the model's types do not fit the relation
    unanswered: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    run: str = ""
    model: str = ""
    by_shape: dict[str, int] = field(default_factory=dict)
    samples: list[tuple[str, str]] = field(default_factory=list)  # (outcome, line)

    def hit(self, shape: str) -> None:
        self.by_shape[shape] = self.by_shape.get(shape, 0) + 1

    def __str__(self) -> str:
        return (
            f"{self.checked} untyped items, {self.requests} requests to {self.model}:"
            f" link {self.linked} ({self.existing} already in the graph),"
            f" drop {self.dropped}, misfit {self.misfit}, unanswered {self.unanswered}"
        )


# ------------------------------------------------------------------ prompt


def system_prompt(onto: ontology.Ontology) -> str:
    types = "\n".join(
        f"- {name}: {' '.join(onto.describe(name).split())[:160]}"
        for name in sorted(onto.entity_types)
    )
    rels = "\n".join(
        f"- {r.name}: {', '.join(sorted(r.domain)) or 'anything'}"
        f" -> {', '.join(sorted(r.range)) or 'anything'}"
        for r in sorted(onto.relations.values(), key=lambda r: r.name)
    )
    return (
        "You assign types to the two ends of relations that a model extracted"
        " from documents in a personal research library but left untyped. For"
        " each numbered item, answer with one line:\n\n"
        "    <number>: <type of the first thing> -> <type of the second thing>\n\n"
        "using only these type names, or `none` for an end that is not a"
        " nameable thing (a placeholder such as 'source name', a whole"
        " sentence, a URL, a reference number, an empty pattern). Choose the"
        " pair that the relation accepts. The first thing is often the"
        " document itself, whose own type is given; a person's name is an"
        " author, a journal or conference is a venue, an institution or"
        " company is an organization, a named algorithm is a method, a"
        " program or library is a tool, an idea is a concept, a cited title"
        " is a paper. Answer every item, nothing else.\n\n"
        f"Types:\n{types}\n\n"
        f"Relations, with what they accept (first -> second):\n{rels}\n"
    )


def user_prompt(batch: Batch) -> str:
    lines = []
    last_doc = None
    for n, it in enumerate(batch.items, 1):
        doc = it["source_doc"]
        if doc != last_doc:
            title, own = batch.docs.get(doc, ("", "paper"))
            lines.append(f"\nDocument: {title or '(untitled)'} (its own type: {own})")
            last_doc = doc
        ev = " ".join(str(it.get("evidence") or "").split())[:140]
        lines.append(
            f"{n}. {it['src']} --{it['rel']}--> {it['dst']}"
            + (f"   (evidence: {ev})" if ev else "")
        )
    return "\n".join(lines).strip() + "\n"


def parse_answer(text: str, count: int) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    for line in text.splitlines():
        m = _ANSWER.match(line)
        if not m:
            continue
        n = int(m.group(1))
        if 1 <= n <= count and n not in out:
            out[n] = (m.group(2).lower(), m.group(3).lower())
    return out


# ------------------------------------------------------------------ batches


def batches(
    con: Any, *, limit: int | None = None, size: int = BATCH
) -> Iterable[Batch]:
    """Open untyped items grouped by document, packed ``size`` to a
    request, each batch with the ontology subset its documents share
    (documents of different subsets never share a batch)."""
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list_review(con, limit=1000, offset=offset)
        if not page:
            break
        offset += len(page)
        items.extend(it for it in page if not (it["src_type"] and it["dst_type"]))
        if limit and len(items) >= limit:
            break
    if limit:
        items = items[:limit]
    # placeholders are the rules' business; a relation the ontology does not
    # know cannot be typed into it, and stays in the queue as evidence
    whole = ontology.current()
    items = [
        it
        for it in items
        if not (_placeholder(it["src"]) or _placeholder(it["dst"]))
        and whole.canonical_relation((it["rel"] or "").lower()) in whole.relations
    ]
    docs = {
        k: (v[0], v[1])
        for k, v in _doc_titles(
            con, {it["source_doc"] for it in items if it["source_doc"]}
        ).items()
    }
    subset_of: dict[int, tuple[str, ...]] = {}
    for doc_id in docs:
        domains = store.document_domains(con, doc_id)
        subset_of[doc_id] = tuple(sorted(domains)) if domains else ()
    by_subset: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for it in sorted(items, key=lambda x: (x["source_doc"] or 0, x["id"])):
        by_subset.setdefault(subset_of.get(it["source_doc"], ()), []).append(it)
    for key, group in by_subset.items():
        onto = whole.for_domains(list(key)) if key else whole
        for i in range(0, len(group), size):
            chunk = group[i : i + size]
            yield Batch(
                chunk,
                {d: docs[d] for d in {it["source_doc"] for it in chunk} if d in docs},
                onto,
            )


# ---------------------------------------------------------- through the door


def hand_out(con: Any, *, limit: int) -> list[dict[str, Any]]:
    """Up to ``limit`` batches for a worker: the items, their documents'
    titles and own types, and the domains the batch's ontology subset is
    made of (the worker rebuilds the prompt from those)."""
    out = []
    for b in batches(con):
        if len(out) >= limit:
            break
        domains = sorted(store.document_domains(con, d) or [] for d in b.docs)
        out.append(
            {
                "items": [
                    {
                        "id": it["id"],
                        "src": it["src"],
                        "rel": it["rel"],
                        "dst": it["dst"],
                        "source_doc": it["source_doc"],
                        "evidence": it.get("evidence"),
                    }
                    for it in b.items
                ],
                "docs": {str(d): list(v) for d, v in b.docs.items()},
                "domains": domains[0] if domains else [],
            }
        )
    return out


def batch_of(item: dict[str, Any]) -> Batch:
    """A worker's or the door's Batch from a handed-out item."""
    whole = ontology.current()
    domains = list(item.get("domains") or [])
    onto = whole.for_domains(domains) if domains else whole
    docs = {int(k): (v[0], v[1]) for k, v in (item.get("docs") or {}).items()}
    return Batch(list(item["items"]), docs, onto)


def take_in(
    con: Any,
    results: list[dict[str, Any]],
    *,
    model: str,
    run: str,
) -> ModelTypingReport:
    """Apply a worker's answers: each result is a handed-out item with the
    model's ``text`` (or an ``error``), the items re-read from the queue
    so a resolved one is left alone."""
    rep = ModelTypingReport(run=run, model=model)
    producer = f"{PRODUCER_PREFIX}:{model}"
    for r in results:
        if r.get("error") or not r.get("text"):
            rep.unanswered += len(r.get("items") or [])
            continue
        b = batch_of(r)
        rep.requests += 1
        usage = r.get("usage") or {}
        rep.input_tokens += usage.get("input_tokens", 0)
        rep.output_tokens += usage.get("output_tokens", 0)
        answers = parse_answer(r["text"], len(b.items))
        for n, it in enumerate(b.items, 1):
            live = store.get_review(con, it["id"])
            if live is None or live.get("resolved_at"):
                continue
            rep.checked += 1
            _apply(con, rep, b, live, answers.get(n), producer, True, 0)
    return rep


# ------------------------------------------------------------------ the pass


def run(
    con: Any,
    *,
    commit: bool,
    limit: int | None = None,
    workers: int = 2,
    runtime: Any | None = None,
    log: Callable[[str], None] | None = None,
    keep_samples: int = 40,
) -> ModelTypingReport:
    say = log or (lambda _: None)
    spec = None
    if runtime is None:
        spec = models.resolve(STEP)
        if spec is None:
            raise RuntimeError(
                "the typing step is off: name a model in prax.yaml (steps: typing:"
                " model: …) or PRAX_TYPING=<model> for one run"
            )
        runtime = models.runtime(spec)
    rep = ModelTypingReport(
        run="typing-model-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S"),
        model=getattr(runtime, "name", spec.name if spec else "model"),
    )
    producer = f"{PRODUCER_PREFIX}:{rep.model}"
    todo = list(batches(con, limit=limit))
    rep.checked = sum(len(b.items) for b in todo)
    say(f"{rep.checked} untyped items in {len(todo)} requests to {rep.model}")

    def ask(b: Batch) -> tuple[Batch, str, dict[str, int]]:
        text, usage = runtime.chat(
            system_prompt(b.onto), user_prompt(b), max_tokens=40 * len(b.items) + 50
        )
        return b, text, usage

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for b, text, usage in pool.map(ask, todo):
            rep.requests += 1
            rep.input_tokens += usage.get("input_tokens", 0)
            rep.output_tokens += usage.get("output_tokens", 0)
            answers = parse_answer(text, len(b.items))
            for n, it in enumerate(b.items, 1):
                _apply(con, rep, b, it, answers.get(n), producer, commit, keep_samples)
            if rep.requests % 20 == 0:
                say(f"  {rep.requests}/{len(todo)} requests: {rep}")
    return rep


def _apply(
    con: Any,
    rep: ModelTypingReport,
    b: Batch,
    it: dict[str, Any],
    answer: tuple[str, str] | None,
    producer: str,
    commit: bool,
    keep_samples: int,
) -> None:
    line = f"{it['src'][:40]!r} -{it['rel']}-> {it['dst'][:40]!r}"
    if answer is None:
        rep.unanswered += 1
        return
    st, dt = (b.onto.canonical_type(a) if a != NONE else NONE for a in answer)
    if NONE in (st, dt):
        rep.dropped += 1
        rep.hit(f"drop ({st} -> {dt})")
        if len(rep.samples) < keep_samples:
            rep.samples.append(("drop", line))
        if commit:
            store.resolve_review(con, it["id"], "dropped")
        return
    rel = b.onto.canonical_relation(it["rel"])
    # the rules' near-miss tables: a venue typed as an organization is a
    # venue, a "cited" tool is used, a "cited" person is mentioned
    st, dt = RETYPE.get((rel, st, dt), (st, dt))
    rel = REMAP.get((rel, st, dt), rel)
    try:
        b.onto.check_edge(st, rel, dt)
    except ValueError:
        rep.misfit += 1
        rep.hit(f"misfit {st} -{rel}-> {dt}")
        if commit:
            # the item keeps the types the model gave it: a typed misfit
            # for the rules and a later ontology, not asked again
            store.retype_review(con, it["id"], st, dt)
        return
    edge = store.Edge(it["src"], st, rel, it["dst"], dt)
    rep.hit(f"{st} -{rel}-> {dt}")
    if len(rep.samples) < keep_samples:
        rep.samples.append(
            ("link", f"[{st}] {it['src'][:36]!r} -{rel}-> [{dt}] {it['dst'][:36]!r}")
        )
    if store.find_edges(con, edge):
        rep.existing += 1
    else:
        rep.linked += 1
        if commit:
            store.link(
                con,
                edge,
                confidence="INFERRED",
                source_doc=it["source_doc"],
                evidence=it["evidence"],
                producer=producer,
                run=rep.run,
            )
    if commit:
        store.resolve_review(con, it["id"], "linked")
