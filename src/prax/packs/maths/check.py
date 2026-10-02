"""The check of an answer (AD2, step 2): what the answer claims, checked by
the calculator after the model has written it, and marked in the text
whatever the model wrote.

Asked to mark its own unchecked numbers, the local model did so once in
160 answers (docs/symbolic-maths.md). So the door does it:

- every link of a display equation (``$$a = b = c$$``) whose sides are
  both expressions is checked with ``same``; a link with a bare name on
  one side (``I_1 = …``, ``H_n(z) = …``) is a definition and is left
  alone. A link that does not hold is marked after the display.
- every number written with a decimal point and three or more digits is
  compared with what the answer had to go on: the question, the passages
  and the calculator's results (in any of the units k, m, µ, n apart). A
  number near a result but not equal to it is marked with the result; one
  found nowhere is marked "(not checked)".

The marks are in the answer's text and in ``checks``, one entry each.
"""

from __future__ import annotations

import itertools
import math
import re
from typing import Any

from . import tool

DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
# a number with a decimal point or an exponent; a full stop after it ends
# a sentence
NUMBER = re.compile(r"(?<![\w.\\])(\d+\.\d+|\d+\.?\d*[eE][-+]?\d+)(?!\w|\.\d)")
# a number to check: three significant digits or more
SIGNIFICANT = 3
NEAR = 0.05  # a number this close to a result, and not equal, is a misreckoning
SCALES = (1.0, 1e3, 1e-3, 1e6, 1e-6, 1e9, 1e-9, 1e12, 1e-12)
TAG = re.compile(r"(,|\\quad|\\qquad)?\s*\(\s*\d+[a-z]?\s*\)\s*$|\\tag\{[^}]*\}")
# a symbol, with a subscript and an argument list at most: what a
# definition's left side is
WORDS = re.compile(r"\\(?:text|mathrm|operatorname)\{[^{}]*\}")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# a number with a unit written as a word after it
UNIT_WORD = re.compile(r"\d\s*(?:\\[,;! ]\s*)?\\(?:text|mathrm)\{[\s\\A-Za-z]{1,12}\}")
UNITS = re.compile(r"\\(?:Omega|mu|circ)\b|\\,\s*[A-Za-z]")
NAME = re.compile(
    r"\\?[A-Za-z]+'*(?:_\{?[A-Za-z0-9,]+\}?)?(?:\^\{?[^{}]*\}?)?"
    r"(?:\s*\\?left)?(?:\([^()]*\)|\[[^\[\]]*\])?(?:\s*\\?right)?"
)
NOT_CHECKED = "(not checked)"
LINKS_MAX = 12  # links checked in one answer
# a link is judged only with this many free symbols or fewer: arithmetic
# and one-variable identities. On the e1 run's answers, judging every
# link marked 64 "does not hold" and most were relations that hold given
# other facts; at one symbol the marks were the e^{xx} typo and two
# misreckonings (2026-10-02)
FREE_MAX = 1
TIMEOUT_S = 60.0


def sides(display: str) -> list[str]:
    """A display's sides at its top-level equals signs; none for one with
    alignment, several lines, or a relation that is not equality."""
    text = TAG.sub("", display.strip()).strip().rstrip(".,;")
    if UNIT_WORD.search(text):
        return []  # 26 \text{ mV} = 0.026 \text{ V}: units again
    text = WORDS.sub("", text)  # \text{Numerator}: a label, not maths
    text = THOUSANDS.sub("", text)  # 10,000 is ten thousand
    if re.search(r"\\\\|&|\\(?:le|ge|leq|geq|neq|approx|sim|propto)\b|[<>]", text):
        return []
    if UNITS.search(text):
        return []  # 10 kΩ = 10,000 Ω: a conversion of units, not maths
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif ch == "," and depth == 0:
            return []  # a list in one display: B = -1, C = -1/(2V_T)
        elif ch == "=" and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [s.strip() for s in out] if len(out) > 1 else []


def is_name(side: str) -> bool:
    """A side that names a thing rather than computing it: a definition's
    left side (``I_1``, ``H_n(z)``, ``y[n]``, ``\\beta``)."""
    side = side.strip()
    # an argument list whose name was a \text{...} label: tanh_AD1(x)
    return bool(NAME.fullmatch(side)) or bool(re.fullmatch(r"\([^()]*\)", side))


def links(answer: str) -> list[tuple[int, str, str]]:
    """The links to check: (where the display ends, left, right)."""
    out = []
    for m in DISPLAY.finditer(answer):
        parts = sides(m.group(1))
        for left, right in itertools.pairwise(parts):
            if left and right and not is_name(left) and not is_name(right):
                out.append((m.end(), left, right))
    return out[:LINKS_MAX]


def _digits(text: str) -> int:
    return len(re.sub(r"[^0-9]", "", text.split("e")[0].split("E")[0]).lstrip("0"))


def numbers(text: str) -> list[tuple[int, int, float, str]]:
    """The numbers worth checking: (start, end, value, as written)."""
    out = []
    for m in NUMBER.finditer(text):
        if _digits(m.group(1)) >= SIGNIFICANT:
            out.append((m.start(), m.end(), float(m.group(1)), m.group(1)))
    return out


def _decimals(written: str) -> int:
    mantissa = written.split("e")[0].split("E")[0]
    return len(mantissa.split(".")[1]) if "." in mantissa else 0


def _rounds_to(value: float, written: str, source: float) -> bool:
    """Whether a source value, in some unit, is the written number to the
    precision it is written with."""
    if "e" in written.lower():
        return math.isclose(value, source, rel_tol=10 ** -(_digits(written) - 1))
    places = _decimals(written)
    return any(round(source * k, places) == round(value, places) for k in SCALES)


def _near(value: float, source: float) -> float | None:
    """The source in the unit that brings it near the value, or None."""
    for k in SCALES:
        s = source * k
        if s and abs(value - s) / abs(s) < NEAR:
            return s
    return None


def _shown_like(value: float, written: str) -> str:
    places = max(_decimals(written), 2)
    return f"{value:.{places}f}"


def check_numbers(
    answer: str, sources: str, results: list[float]
) -> tuple[str, list[dict[str, Any]]]:
    """The answer with its numbers marked, and one check each."""
    known = [v for _, _, v, _ in numbers(sources)]
    checks: list[dict[str, Any]] = []
    marks: list[tuple[int, str]] = []
    for start, end, value, written in numbers(answer):
        after = answer[end : end + 40].lstrip()
        if after.startswith((NOT_CHECKED, "(the calculator gives")):
            continue  # marked already
        if answer[max(0, start - 22) : start].endswith("the calculator gives "):
            continue  # a mark's own number

        if any(_rounds_to(value, written, r) for r in results):
            checks.append({"number": written, "verdict": "computed"})
            continue
        if any(_rounds_to(value, written, k) for k in known) or written in sources:
            checks.append({"number": written, "verdict": "quoted"})
            continue
        near = next((n for r in results if (n := _near(value, r)) is not None), None)
        if near is not None:
            said = _shown_like(near, written)
            checks.append({"number": written, "verdict": "differs", "result": said})
            marks.append((end, f" (the calculator gives {said})"))
        else:
            checks.append({"number": written, "verdict": "not checked"})
            marks.append((end, f" {NOT_CHECKED}"))
    for at, mark in sorted(marks, reverse=True):
        answer = answer[:at] + mark + answer[at:]
    return answer, checks


def check_links(answer: str) -> tuple[str, list[dict[str, Any]]]:
    """The answer with each display equation's failing links marked."""
    found = links(answer)
    if not found:
        return answer, []
    batch = [{"op": "same", "a": left, "b": right} for _, left, right in found]
    got = tool.run({"batch": batch}, timeout=TIMEOUT_S).get("batch") or []
    checks: list[dict[str, Any]] = []
    marks: dict[int, list[str]] = {}
    for (end, left, right), res in zip(found, got, strict=False):
        reads = res.get("read") or {}
        names = {n for r in reads.values() for n in r.get("symbols") or []}
        if "error" in res or res.get("same") is None:
            checks.append({"link": f"{left} = {right}", "verdict": "unread"})
        elif len(names) > FREE_MAX:
            # a relation among quantities holds given other facts (Q =
            # ω_c/Δω, the diode's waves): not an identity to judge alone
            checks.append({"link": f"{left} = {right}", "verdict": "not judged"})
        elif res["same"]:
            checks.append({"link": f"{left} = {right}", "verdict": "holds"})
        else:
            checks.append({"link": f"{left} = {right}", "verdict": "does not hold"})
            marks.setdefault(end, []).append(f"{left} = {right}")
    for end in sorted(marks, reverse=True):
        said = "; ".join(f"${x}$" for x in marks[end])
        note = f"\n\n*The calculator finds that this does not hold: {said}.*"
        answer = answer[:end] + note + answer[end:]
    return answer, checks


RESULT = re.compile(r"result: ([-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)")


def check_answer(
    answer: str, *, question: str, passages: list[str], worked: list[str]
) -> tuple[str, list[dict[str, Any]]]:
    """The checked answer and its checks. ``worked`` is the surf's tool
    lines (``surf.Surf.worked``), whose results the numbers are held to."""
    results = [float(x) for w in worked for x in RESULT.findall(w)]
    sources = "\n".join([question, *passages])
    try:
        answer, eq = check_links(answer)
    except tool.MathsUnavailable:
        eq = []
    answer, nums = check_numbers(answer, sources, results)
    return answer, eq + nums
