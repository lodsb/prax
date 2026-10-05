"""The check of an answer (AD2): what the answer claims, checked by the
calculator after the model has written it. The answer's text is left as
the model wrote it; the checks come back as annotations with the span
they are about, for the reader to see beside it (the review of
2026-10-02: marks written into the text landed inside LaTeX and code,
and had to be stripped again before an answer could be scored).

Asked to mark its own unchecked numbers, the local model did so once in
160 answers (docs/symbolic-maths.md). So the door does it:

- every link of a display equation (``$$a = b = c$$``) goes to the
  calculator's ``judge``, which decides on the parsed expressions what
  the link claims: a definition, a condition, a relation among
  quantities, or arithmetic and identities it can decide
  (``runtime.judge``). This module only splits a display into links.
- in an answer about maths (one with a display, or with the tools'
  results), every number written with three significant digits or more
  outside the maths, code and links is compared with what the answer had
  to go on: the question, the passages and the calculator's results, in
  units a thousand apart. A number near a result but not equal to it is
  "differs"; one found nowhere is "not checked".
"""

from __future__ import annotations

import itertools
import math
import re
from typing import Any

from prax.text import markup

from . import tool

DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
# what a number check leaves alone: maths, code, links and their ids
MASKED = re.compile(
    r"\$\$.+?\$\$|\$[^$\n]+\$|\\\(.+?\\\)|\\\[.+?\\\]"
    r"|```.*?```|`[^`\n]+`"
    r"|https?://\S+|\b10\.\d{4,9}/\S+|\barXiv:\s*\S+|\b\d{4}\.\d{4,5}(?:v\d+)?\b"
    r"|\b[A-Z][A-Za-z+#]*\s+v?\d+\.\d+(?:\.\d+)*",  # a version: Python 3.11
    re.DOTALL,
)
# a number with a decimal point or an exponent; a full stop after it ends a
# sentence, and a digit group before it (1,234.5) is part of it
NUMBER = re.compile(r"(?<![\w.,\\])(\d+\.\d+|\d+\.?\d*[eE][-+]?\d+)(?!\w|\.\d)")
SIGNIFICANT = 3  # a number to check: three significant digits or more
NEAR = 0.05  # a number this close to a result, and not equal, is a misreckoning
SCALES = (1.0, 1e3, 1e-3, 1e6, 1e-6, 1e9, 1e-9, 1e12, 1e-12)
# a display's equation number is the chunker's (``markup.eq_number``)
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# what is not one chain of equalities: alignment, several lines, another
# relation, two statements side by side
NOT_A_CHAIN = re.compile(
    r"\\\\|&|\\(?:le|ge|leq|geq|neq|approx|sim|propto)(?![A-Za-z])|[<>]|\\q?quad"
    r"|:"  # a ratio a : b = c : d, an assignment n := n + 1
)
LINKS_MAX = 12  # links judged in one answer
TIMEOUT_S = 60.0
RESULT = re.compile(r"result: ([-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)")


def sides(display: str) -> list[str]:
    """A display's sides at its top-level equals signs; none for one that
    is not a single chain of equalities (alignment, several lines, an
    inequality, two statements side by side, a list of definitions)."""
    text = markup.eq_number(display.strip())[0].strip().rstrip(".,;")
    text = THOUSANDS.sub("", text)  # 10,000 is ten thousand
    if NOT_A_CHAIN.search(text):
        return []
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


def links(answer: str) -> list[tuple[int, int, str, str]]:
    """The links of the answer's displays: (start, end of the display,
    left, right)."""
    out = []
    for m in DISPLAY.finditer(answer):
        for left, right in itertools.pairwise(sides(m.group(1))):
            if left and right:
                out.append((m.start(), m.end(), left, right))
    return out[:LINKS_MAX]


def check_links(answer: str) -> list[dict[str, Any]]:
    """Each link judged by the calculator, in one process."""
    found = links(answer)
    if not found:
        return []
    batch = [{"op": "judge", "a": left, "b": right} for _, _, left, right in found]
    got = tool.run({"batch": batch}, timeout=TIMEOUT_S).get("batch") or []
    checks = []
    for (start, end, left, right), res in zip(found, got, strict=False):
        checks.append(
            {
                "kind": "link",
                "start": start,
                "end": end,
                "link": f"{left} = {right}",
                "verdict": str(res.get("verdict") or "not judged"),
                "why": str(res.get("why") or res.get("error") or ""),
            }
        )
    return checks


def _digits(text: str) -> int:
    return len(re.sub(r"[^0-9]", "", text.split("e")[0].split("E")[0]).lstrip("0"))


def _masked(text: str) -> str:
    """The text with maths, code and links blanked to spaces, so positions
    stay where they were."""
    return MASKED.sub(lambda m: " " * len(m.group(0)), text)


def numbers(text: str) -> list[tuple[int, int, float, str]]:
    """The numbers worth checking outside maths, code and links: (start,
    end, value, as written)."""
    out = []
    for m in NUMBER.finditer(_masked(text)):
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


def check_numbers(
    answer: str, sources: str, results: list[float]
) -> list[dict[str, Any]]:
    """One check per number: computed (a result gives it), quoted (the
    question or a passage holds it), differs (near a result, not equal)
    or not checked."""
    known = [v for _, _, v, _ in numbers(sources)]
    known += [float(m.group(1)) for m in NUMBER.finditer(sources)]
    checks: list[dict[str, Any]] = []
    for start, end, value, written in numbers(answer):
        check: dict[str, Any] = {
            "kind": "number",
            "start": start,
            "end": end,
            "number": written,
        }
        if any(_rounds_to(value, written, r) for r in results):
            check["verdict"] = "computed"
        elif any(_rounds_to(value, written, k) for k in known) or written in sources:
            check["verdict"] = "quoted"
        else:
            near = next(
                (n for r in results if (n := _near(value, r)) is not None), None
            )
            if near is not None:
                check["verdict"] = "differs"
                check["result"] = f"{near:.{max(_decimals(written), 2)}f}"
            else:
                check["verdict"] = "not checked"
        checks.append(check)
    return checks


def check_answer(
    answer: str, *, question: str, passages: list[str], worked: list[str]
) -> tuple[str, list[dict[str, Any]]]:
    """The answer, unchanged, and its checks. ``worked`` is the surf's tool
    lines (``surf.Surf.worked``), whose results the numbers are held to.
    The numbers are checked only in an answer about maths: one with a
    display, or with results."""
    results = [float(x) for w in worked for x in RESULT.findall(w)]
    try:
        found = check_links(answer)
    except tool.MathsUnavailable:
        found = []
    if results or DISPLAY.search(answer):
        sources = "\n".join([question, *passages])
        found += check_numbers(answer, sources, results)
    return answer, found
