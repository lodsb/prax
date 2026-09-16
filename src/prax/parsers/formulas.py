"""Formula readings: a model says in words what a display equation is.

A display equation is a ``formula`` chunk (``prax.chunking``): its LaTeX
and the number the prose refers to it by. The LaTeX itself embeds to
noise and a person searching says "the diode equation", not
``\\frac{a-b}{2R}``, so, as a figure gets a reading from the vision model,
a formula gets one from a text model — one to three sentences under the
equation, on the figure's pattern::

    $$i = I_s \\left( e^{v/V_T} - 1 \\right), \\quad (1)$$
    *Formula, as read by qwen3.6-35b@127.0.0.1:8080:* Shockley's diode
    equation: the current through the diode as an exponential of the
    voltage across it over the thermal voltage …

The ``formulas`` step of ``prax.yaml`` names the model (``none`` by
default: nobody reads formulas unless a host says so); the ``formulas``
extractor (explicit, annotating) writes the readings into the current
text, ``parse.formula_readings: again`` replaces this model's earlier ones,
and
the ``unread-formulas`` ailment says which documents hold formulas
nobody has read. Formulas exist in the text only where a parser wrote
them as LaTeX (``marker``, howto 3h).
"""

from __future__ import annotations

import re
from typing import Any

from prax import chunking

SYSTEM = """\
You read one display equation from a document in a personal research
library and say in one to three sentences what it is, so that someone
searching the library in words would find it and someone reading past it
would understand it. Say what it expresses, name the quantities in it in
the document's own terms, and give the name the equation goes by when it
has one (Shockley's diode equation, the wave equation, a Householder
reflection). Plain prose: no LaTeX, no symbols the text does not use, no
preamble, no "this equation". Everything you say must follow from the
equation and the text around it; do not guess at what it "likely" means
and do not describe the text itself."""

AROUND_CHARS = 700  # of the text on each side of the equation
MAX_TOKENS = 160
READ_BY = re.compile(r"^\*Formula, as read by (?P<model>.+?):\*", re.MULTILINE)
WHICH = ("new", "again")


def model_name() -> str:
    """What the ``formulas`` step resolves to, for the extractor's stamp."""
    from prax import models

    spec = models.resolve("formulas")
    return spec.runtime_name if spec is not None else "none"


def refs(text: str) -> list[dict[str, Any]]:
    """Every display equation in the text: its line index, LaTeX, number
    and the models that have read it."""
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if chunking._is_formula(stripped):
            j = i + 1
            while j < len(lines) and chunking._READ_BY.match(lines[j].strip()):
                j += 1
            parsed = chunking.parse_formula("\n".join(lines[i:j]))
            if parsed:
                out.append(
                    {
                        "at": i,
                        "latex": parsed["latex"],
                        "number": parsed["number"],
                        "read_by": [r["model"] for r in parsed["readings"]],
                        "end": j,
                    }
                )
            i = j
            continue
        i += 1
    return out


def around(lines: list[str], at: int) -> str:
    """The text on either side of the equation line, without any reading
    (a model must not be shown its own earlier work or another's) and
    without the other equations' lines."""
    keep = [
        ln
        for ln in lines[: max(0, at)][-30:] + ["…"] + lines[at + 1 :][:30]
        if not chunking._READ_BY.match(ln.strip())
        and not chunking._is_formula(ln.strip())
    ]
    text = " ".join(" ".join(keep).split())
    if len(text) > 2 * AROUND_CHARS:
        text = text[:AROUND_CHARS] + " … " + text[-AROUND_CHARS:]
    return text


def user_message(title: str, latex: str, number: str | None, near: str) -> str:
    parts = []
    if title:
        parts.append(f'From "{title}".')
    label = f"The equation ({number}):" if number else "The equation:"
    parts.append(f"{label}\n$${latex}$$")
    if near:
        parts.append(f"The text around it: {near}")
    return "\n\n".join(parts)


def _read_by(line: str, who: str) -> bool:
    m = READ_BY.match(line)
    return bool(m and m.group("model").strip() == who)


def describe(previous: str, *, runtime: Any | None = None) -> str:
    """The text with a reading under every display equation this model
    has not read (``parse.formula_readings: again``: every one, its earlier
    reading replaced, another model's kept). ``runtime`` stands in for the
    step's model in tests."""
    from prax import config, models
    from prax.parsers import ExtractionError, figures

    which = str(
        config.setting("parse.formula_readings", "PRAX_FORMULA_READINGS", "new")
    )
    if which not in WHICH:
        raise ExtractionError(
            f"parse.formula_readings must be one of {WHICH}, not {which!r}"
        )
    again = which == "again"
    if runtime is None:
        spec = models.resolve("formulas")
        if spec is None:
            raise ExtractionError(
                "no formulas model: steps.formulas in prax.yaml names one"
            )
        runtime = models.runtime(spec)
    who = runtime.name
    wanted = [r for r in refs(previous) if again or who not in r["read_by"]]
    if not wanted:
        return previous
    lines = previous.split("\n")
    title = figures.document_title(previous)
    done = 0
    # from the last equation up, so earlier line indexes stay right
    for r in reversed(wanted):
        near = around(lines, r["at"])
        out, _usage = runtime.chat(
            SYSTEM,
            user_message(title, r["latex"], r["number"], near),
            max_tokens=MAX_TOKENS,
            temperature=0.0,
        )
        text = " ".join(str(out).split())
        if not text:
            continue
        at = r["at"] + 1
        if again:
            while at < len(lines) and chunking._READ_BY.match(lines[at].strip()):
                if _read_by(lines[at].strip(), who):
                    del lines[at]
                else:
                    at += 1
        lines.insert(at, f"*Formula, as read by {who}:* {text}")
        done += 1
    if not done:
        raise ExtractionError("no formula could be read")
    return "\n".join(lines)
