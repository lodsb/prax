"""What a document is and what it is about, asked of a local model.

Stage Z of `docs/PLAN.md`. The vocabularies are `ontology/genres.yaml`
(what a text is: a paper, a datasheet, an essay) and
`ontology/subjects.yaml` (what it is about: politics, audio, cooking),
each two levels deep (`ontology.Facet`). A document may take several of
each.

**One question a label, one token an answer.** A model asked to list a
document's labels suppresses all but one at each step of the list, and
its first step does not predict the rest (library doc 13331). So each
label is its own yes/no question, and the answer is P(yes) read from the
token's top alternatives (`calibration.yes_probability`), the way the
local adjudicator reads a pair. The document comes first in the prompt
and the question last, so llama-server reuses the document's prefix from
its cache and a further label costs the question's few tokens: the shared
state of the decision models (Jev, Jeff, Kev; docs 10307, 13341, 13342)
on the model prax already runs.

**Top-down.** The levels and groups are asked first (nine and four), and
the labels under a level only where the level is at least ``GATE``
likely, so a recipe is not asked whether it is a thesis.

**What the model reads** is `view`: the title, where it came from, its
language, its summary, the summaries of its sections, and the opening of
its text. A summary normalises a document's length and variety (TnT-LLM,
doc 13322), but a genre is a matter of form as much as of content, which
the opening shows and a summary may not. Whether the opening earns its
tokens is measured (`scripts/eval_genres.py`).

The alternative measured beside it, `listed`, asks for the labels as one
list under a grammar: one call, no probabilities.

**What the step does** (`label`) is both, as measured best and cheapest
(docs/eval/genres-2026-09-29.md): the list proposes, and only the
proposed labels and their levels are asked, calibrated by the Platt maps
fitted on a person's labels (``steps.genres.platt`` in prax.yaml) and
kept at ``KEEP``. About two seconds a document where asking every label
top-down took seven, and the same F1.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from prax.graph import calibration
from prax.graph.ontology import Facet

OPENING = 2000  # characters of the text the model reads
KEEP = 0.3  # a proposed label this likely, calibrated, is kept
SECTIONS = 6  # section summaries at most
GATE = 0.2  # a level this likely has the labels under it asked
TIMEOUT = 120.0

SYSTEM = (
    "You classify the documents of a personal research library. Read the"
    " document, then answer the question about it with one word: yes or no."
)


def view(
    title: str | None,
    meta: Mapping[str, Any],
    text: str,
    *,
    opening: int = OPENING,
    where: str | None = None,
) -> str:
    """The document as the model reads it. ``opening`` 0 leaves the text
    out: the summary alone."""
    lines = [f"Title: {title or '(untitled)'}"]
    source = meta.get("source")
    if source or where:
        lines.append(f"Source: {source or ''}{f' ({where})' if where else ''}".strip())
    if meta.get("lang"):
        lines.append(f"Language: {meta['lang']}")
    if meta.get("summary"):
        lines.append(f"Summary: {meta['summary']}")
    sections = [
        str(s.get("summary") or "")
        for s in (meta.get("sections") or {}).get("items", [])[:SECTIONS]
        if isinstance(s, dict)
    ]
    if any(sections):
        lines.append("Sections: " + " / ".join(s for s in sections if s))
    if opening and text:
        head = " ".join(text[:opening].split())
        lines.append(f"The beginning of the text:\n{head}")
    return "\n".join(lines)


def question(facet: Facet, label: str, description: str, *, about: bool) -> str:
    """The yes/no question for one label. A genre asks what the document
    is, a subject what it is about; a level asks what it does or where it
    belongs."""
    is_level = facet.level_of(label) == label
    if about:
        return f"Is this document about {label} ({description})?"
    if is_level:
        return f"Is this document {label}, that is: it {description}?"
    return f"Is this document a {label}, that is: {description}?"


def _descriptions(facet: Facet) -> dict[str, str]:
    out: dict[str, str] = {}
    for lv in facet.levels:
        out[lv.name] = lv.description
        out.update(dict(lv.genres))
    return out


def ask(base_url: str, model: str, document: str, q: str) -> float | None:
    """P(yes) for one question about one document, or None when the
    server gave no answer to read."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{document}\n\nQuestion: {q}\nAnswer:"},
        ],
        "max_tokens": 1,
        "temperature": 0,
        "logprobs": True,
        "top_logprobs": 10,
        "cache_prompt": True,
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        got = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT).read())
        top = got["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        urllib.error.URLError,
    ):
        return None
    return calibration.yes_probability([(t["token"], t["logprob"]) for t in top])


def probabilities(
    base_url: str,
    model: str,
    document: str,
    facet: Facet,
    *,
    about: bool,
    gate: float = GATE,
    slots: int = 2,
) -> dict[str, float]:
    """P(yes) for the levels of a facet, then for the labels under every
    level at least ``gate`` likely. A label not asked is absent."""
    said = _descriptions(facet)

    def run(labels: Iterable[str]) -> dict[str, float]:
        labels = list(labels)
        with ThreadPoolExecutor(max_workers=max(1, slots)) as pool:
            got = pool.map(
                lambda x: ask(
                    base_url, model, document, question(facet, x, said[x], about=about)
                ),
                labels,
            )
            return {x: p for x, p in zip(labels, got, strict=True) if p is not None}

    out = run(lv.name for lv in facet.levels)
    under = [
        g for lv in facet.levels if out.get(lv.name, 0.0) >= gate for g, _ in lv.genres
    ]
    out.update(run(under))
    return out


def calibrated(
    p: float, label: str, facet: Facet, name: str, platt: Mapping[str, Any] | None
) -> float:
    """A raw P(yes) through the facet's Platt map for its kind (``"genres
    levels"``, ``"subjects labels"``, …); raw where the file has none."""
    kind = "levels" if facet.level_of(label) == label else "labels"
    m = (platt or {}).get(f"{name} {kind}")
    if not m:
        return p
    a, b = (
        (float(m[0]), float(m[1]))
        if isinstance(m, list | tuple)
        else (
            float(m["a"]),
            float(m["b"]),
        )
    )
    return calibration.Platt(a, b)(p)


def label(
    runtime: Any,
    base_url: str,
    model: str,
    document: str,
    G: Facet,
    S: Facet,
    *,
    platt: Mapping[str, Any] | None = None,
    keep: float = KEEP,
    slots: int = 2,
) -> dict[str, dict[str, float]]:
    """A document's genres (``g``) and subjects (``s``) with their
    calibrated probabilities: the labels the list proposes, and their
    levels, each asked and kept at ``keep``."""
    out: dict[str, dict[str, float]] = {}
    for key, facet, name, about in (
        ("g", G, "genres", False),
        ("s", S, "subjects", True),
    ):
        proposed = facet.implied(listed(runtime, document, facet, about=about))
        said = _descriptions(facet)
        with ThreadPoolExecutor(max_workers=max(1, slots)) as pool:
            raw = list(
                pool.map(
                    lambda x, f=facet, a=about, d=said: ask(
                        base_url, model, document, question(f, x, d[x], about=a)
                    ),
                    proposed,
                )
            )
        out[key] = {
            x: round(q, 3)
            for x, p in zip(proposed, raw, strict=True)
            if p is not None and (q := calibrated(p, x, facet, name, platt)) >= keep
        }
    return out


def list_grammar(facet: Facet) -> str:
    """A GBNF grammar for a comma-separated list of the facet's labels."""
    names = " | ".join(json.dumps(x) for x in facet.labels())
    return f'root ::= label (", " label)*\nlabel ::= {names}\n'


def listed(runtime: Any, document: str, facet: Facet, *, about: bool) -> list[str]:
    """The labels as one list the model writes under a grammar: the
    alternative to asking label by label. No probabilities."""
    said = _descriptions(facet)
    vocab = "\n".join(f"- {x}: {said[x]}" for x in facet.labels())
    ask_for = "what it is about" if about else "what kind of document it is"
    system = (
        "You classify the documents of a personal research library. Name"
        f" {ask_for}, choosing every label that fits from this list, most"
        f" fitting first, separated by commas:\n{vocab}"
    )
    text, _ = runtime.chat(system, document, grammar=list_grammar(facet), max_tokens=60)
    return facet.check(x.strip() for x in text.split(",") if x.strip())
