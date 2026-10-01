"""The maths pack's side in prax (docs/symbolic-maths.md): the calculator
run as a subprocess of its own environment, a formula of the library
resolved to its LaTeX, and the surfer's ``maths:`` action.

The calculator is ``runtime.py`` beside this file, run by the interpreter
``maths.python`` names (SymPy 1.14 and ANTLR 4.11, which prax's own
environment cannot hold beside OCR's ANTLR 4.9). It takes one request as
JSON and answers as JSON; past ``maths.timeout_s`` the process is killed
and the answer says so. Nothing a model wrote is run: the request is
data, and the runtime reads it with its parsers.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from prax import config, store

RUNTIME = Path(__file__).with_name("runtime.py")
TIMEOUT_S = 20.0
OPERATIONS = (
    "read",
    "same",
    "simplify",
    "substitute",
    "solve",
    "diff",
    "integrate",
    "series",
    "limit",
    "evaluate",
    "code",
    "chain",
    "expand",
    "factor",
    "apart",
    "together",
)
WITH_VAR = ("solve", "diff", "integrate", "series", "limit", "apart")
CHUNK_REF = re.compile(r"^chunk:(\d+)$")
PASSAGE = re.compile(r"\[(\d+)\]")
# what a model writes around and between formulas, read as meant
# (the maths eval of 2026-10-01: 15 of 39 calls failed on these)
MATH_DELIMS = re.compile(r"\${1,2}")  # $...$ and $$...$$ around a formula
EQ_SPLIT = re.compile(r"\s*==\s*")
TO_SAME = ("simplify", "expand", "factor", "together")
NAME_VALUE = r"[A-Za-z_]\w*\s*=\s*[^\s,=]+"
TRAILING_VALUES = re.compile(rf"\s+({NAME_VALUE}(?:\s*,\s*{NAME_VALUE})*)\s*$")
# the second round of the eval (2026-10-02): 12 more ways it was written
TRAILING_TALK = re.compile(r"\s+(?:answer|result|gives|then)\b\s*:?.*$", re.IGNORECASE)
AS_CALL = re.compile(r"(?:diff|integrate|simplify|expand|factor)\(")
FOR_VAR = re.compile(r"\s+for\s+([A-Za-z]\w*)\b.*$")
SYMPY_ORDER = re.compile(r"(.+?),\s*([A-Za-z]\w*)\s*,\s*([^,]+?)(?:\s*,\s*(\d+))?\s*")
POINT_AFTER = re.compile(r"(.+?),\s*([^,()]+?)(?:\s*,\s*(\d+))?\s*")
AT_POINT = re.compile(r"(.+?)\s+(?:at\s+)?([A-Za-z]\w*)\s*=\s*(\S+)")
# a model writes x**2 or exp(x) as often as LaTeX: formulas without a
# backslash and with a * or a named function call are plain notation
PLAINLY = re.compile(
    r"\*|\b(exp|log|ln|sqrt|tanh|sinh|cosh|sin|cos|tan|atan|polylog|Li2|Abs|diff|integrate)\("
)


class MathsUnavailable(RuntimeError):
    """This host has no maths environment named (``maths.python``)."""


def python() -> Path:
    raw = config.setting("maths.python", "PRAX_MATHS_PYTHON")
    if not raw:
        raise MathsUnavailable(
            "the maths pack needs maths.python in prax.yaml: the interpreter of an"
            " environment with sympy 1.14 and antlr4-python3-runtime 4.11"
            " (docs/howto.md, 'The maths pack')"
        )
    path = Path(str(raw))
    if not path.exists():
        raise MathsUnavailable(f"maths.python names {path}, which does not exist")
    return path


def run(request: dict[str, Any]) -> dict[str, Any]:
    """One request to the calculator, its answer as a dict; an error is an
    ``error`` key, as the runtime gives it."""
    timeout = config.number("maths.timeout_s", "PRAX_MATHS_TIMEOUT_S", TIMEOUT_S)
    try:
        done = subprocess.run(
            [str(python()), str(RUNTIME)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "error": f"the calculator took longer than {timeout:g} s and was stopped"
        }
    try:
        got: dict[str, Any] = json.loads(done.stdout)
    except json.JSONDecodeError:
        tail = (done.stderr or "").strip().splitlines()[-1:] or ["no answer"]
        return {"error": f"the calculator failed: {tail[0][:200]}"}
    return got


def formula_of(con: sqlite3.Connection, chunk_id: int) -> str:
    """The LaTeX of a formula chunk (the wall applies: a hidden chunk is
    as absent)."""
    chunk = store.get_chunk(con, chunk_id)
    if chunk is None:
        raise KeyError(f"no chunk {chunk_id}")
    data = chunk.get("data") or {}
    if chunk.get("kind") != "formula" or not data.get("latex"):
        raise ValueError(
            f"chunk {chunk_id} is not a display formula; write the formula out"
        )
    return str(data["latex"])


def resolve(con: sqlite3.Connection, formula: str) -> str:
    """``chunk:<id>`` as that formula's LaTeX, anything else as written."""
    m = CHUNK_REF.match(formula.strip())
    return formula_of(con, int(m.group(1))) if m else formula


def calculate(con: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    """A request from the door or the MCP tool: the formulas resolved from
    the library where they name a chunk, then the calculator."""
    op = str(request.get("op") or "read")
    if op not in OPERATIONS:
        raise ValueError(f"no operation {op!r}; they are {', '.join(OPERATIONS)}")
    out = dict(request)
    for key in ("a", "b"):
        if isinstance(out.get(key), str):
            out[key] = resolve(con, out[key])
    if isinstance(out.get("steps"), list):
        out["steps"] = [resolve(con, str(f)) for f in out["steps"]]
    return run(out)


def right_side(latex: str) -> str:
    """What a displayed equation defines: the part after its last ``=``
    (``y[n] = x[n] - a`` as ``x[n] - a``), for a passage that stands inside
    a formula the model wrote."""
    return latex.rsplit("=", 1)[-1].strip() if "=" in latex else latex.strip()


# ------------------------------------------------------------ the surfer
# maths: <op> [<var>] <formula> [== <formula>] [with x=1, y=2]
# A formula is a passage [n] (a display formula the surf has read) or
# LaTeX written out. Examples:
#   maths: same [3] == [7]
#   maths: integrate x \tanh(x)
#   maths: evaluate [4] with R=1000, C=1e-6
#   maths: code \log(\cosh(x))


def parse_step(arg: str) -> dict[str, Any]:
    """A surf step's argument as a request; ValueError says how to write it."""
    text = " ".join(arg.split())
    op, _, rest = text.partition(" ")
    op = op.lower()
    if op not in OPERATIONS and AS_CALL.match(text):
        # diff(F, x) as the whole step: the call is the formula
        op, rest = "simplify", text
    if op not in OPERATIONS:
        raise ValueError(f"maths takes one of {', '.join(OPERATIONS)} first")
    rest = MATH_DELIMS.sub("", rest)  # a model wraps formulas in $...$
    rest = TRAILING_TALK.sub("", rest)  # "... answer: 0.0132" after the values
    if " given " in rest:
        raise ValueError(
            "write given values as 'with x=1, y=2' at the end; an equation to"
            " use is a formula of its own (substitute, solve)"
        )
    if op in TO_SAME and EQ_SPLIT.search(rest):
        op = "same"  # simplify a == b: what is asked is whether they are equal
    request: dict[str, Any] = {"op": op}
    args: dict[str, Any] = {}
    given = ""
    if " with " in rest:  # given values, for any operation
        rest, _, given = rest.partition(" with ")
    elif op in ("same", "chain", "evaluate", "substitute"):
        # the values written without "with" (same a == b x=1.0)
        m = TRAILING_VALUES.search(rest)
        if m:
            rest, given = rest[: m.start()], m.group(1)
    if given:
        values = {}
        for pair in given.split(","):
            name, eq, value = pair.partition("=")
            if not eq:
                raise ValueError("with takes name=value pairs, comma-separated")
            values[name.strip()] = value.strip()
        args["values"] = values
    if op in WITH_VAR:
        rest = _with_variable(op, rest, args)
    if op == "code" and rest.split(" ", 1)[0] in ("c", "python"):
        lang, _, rest = rest.partition(" ")
        args["language"] = lang
    if op == "same":
        sides = EQ_SPLIT.split(rest, maxsplit=1)
        if len(sides) != 2:
            raise ValueError("same takes two formulas: same <a> == <b>")
        request["a"], request["b"] = sides[0].strip(), sides[1].strip()
    elif op == "chain":
        steps = [f.strip() for f in EQ_SPLIT.split(rest)]
        if len(steps) < 2 or not all(steps):
            raise ValueError("chain takes the steps of a derivation: chain a == b == c")
        request["a"], request["steps"] = steps[0], steps
    else:
        request["a"] = rest.strip()
    if not request["a"]:
        raise ValueError(f"{op} takes a formula: a passage [n] or LaTeX")
    # passages are LaTeX; the formulas the model wrote say their notation
    written = [*request.get("steps", ()), request["a"], request.get("b", "")]
    own = [f for f in written if f and not re.fullmatch(r"\[\d+\]", f)]
    if (
        own
        and all("\\" not in f and not PASSAGE.search(f) for f in own)
        and any(PLAINLY.search(f) for f in own)
    ):
        request["notation"] = "plain"
    elif op not in ("same", "chain"):
        # an equation in LaTeX has one =; a model writes == as in plain
        request["a"] = EQ_SPLIT.sub(" = ", request["a"])
    if args:
        request["args"] = args
    return request


def _with_variable(op: str, rest: str, args: dict[str, Any]) -> str:
    """The variable of solve, diff, integrate, series, limit and apart, the
    ways a model writes it: first (``solve x <f>``), after ``for`` at the
    end (``solve <f> for I1``), SymPy's order (``series tanh(x), x, 0, 5``)
    or as the point (``series tanh(x) x=0``). The formula is what remains."""
    if op in ("series", "limit"):
        point = "at" if op == "series" else "to"
        m = SYMPY_ORDER.fullmatch(rest)
        if m:
            args["var"], args[point] = m.group(2), m.group(3).strip()
            if m.group(4) and op == "series":
                args["order"] = int(m.group(4))
            return m.group(1).strip()
        m = AT_POINT.fullmatch(rest)
        if m:
            args["var"], args[point] = m.group(2), m.group(3)
            return m.group(1).strip()
    m = FOR_VAR.search(rest)
    if m:
        args["var"] = var = m.group(1)
        formula = rest[: m.start()].strip()
        first, _, after = formula.partition(" ")
        # solve x <f> for x: the first x is the variable named twice, unless
        # an operator follows it (solve I1 * (1 + e) == I for I1)
        named_twice = first == var and after[:1].isalnum()
        return after if named_twice else formula
    var, _, formula = rest.partition(" ")
    if not re.fullmatch(r"[A-Za-z]\w*", var):
        raise ValueError(f"{op} takes the variable first: {op} x <formula>")
    args["var"] = var
    m = POINT_AFTER.fullmatch(formula)
    if m and op in ("series", "limit"):  # series x tanh(x), 0, 5
        args["at" if op == "series" else "to"] = m.group(2).strip()
        if m.group(3) and op == "series":
            args["order"] = int(m.group(3))
        return m.group(1).strip()
    return formula


def shown(got: dict[str, Any]) -> str:
    """An answer as one line for the surfer's log: the reading first, so a
    misreading is seen, then the result."""
    if "error" in got:
        return f"maths: {got['error']}"
    reads = got.get("read") or {}
    parts = [f"read {k} as {v.get('text')}" for k, v in reads.items()]
    if "chain" in got:
        if got["chain"]:
            parts.append(f"every link holds ({len(got.get('links') or [])})")
        else:
            n = int(got.get("broken_at") or 0)
            link = (got.get("links") or [{}])[-1]
            parts.append(
                f"link {n} (step {n} to step {n + 1}) does not hold"
                f" ({link.get('how')}); the links before it do"
            )
    if "same" in got:
        verdict = {True: "the same", False: "not the same", None: "undecided"}
        parts.append(f"{verdict[got['same']]} ({got.get('how')})")
    if "result" in got:
        res = got["result"]
        if isinstance(res, list):
            parts.append("solutions: " + "; ".join(r.get("text", "") for r in res))
        else:
            parts.append(f"result: {res.get('text')}")
    if "code" in got:
        parts.append(f"code: {got['code']}")
    if got.get("note"):
        parts.append(str(got["note"]))
    return " | ".join(parts)


def passages_in(con: sqlite3.Connection, s: Any, formula: str) -> str:
    """A formula with the passages it names: ``[n]`` alone is that
    passage's display formula (``chunk:<id>``), ``[n]`` inside a formula
    is the formula's right side in parentheses (``\\frac{d}{dx}[3]``)."""
    whole = re.fullmatch(r"\[(\d+)\]", formula.strip())

    def chunk_of(ref: str) -> int:
        p = s.by_n(ref)
        if p is None or p.chunk_id is None:
            raise ValueError(f"there is no passage {ref}")
        return int(p.chunk_id)

    if whole:
        return f"chunk:{chunk_of(formula.strip())}"
    return PASSAGE.sub(
        lambda m: "(" + right_side(formula_of(con, chunk_of(m.group(0)))) + ")",
        formula,
    )


def surf_maths(con: sqlite3.Connection, s: Any, arg: str) -> str:
    """The surfer's ``maths:`` action: a passage [n] is the formula it
    holds, alone or inside a formula; anything else is what the model
    wrote, LaTeX or plain notation."""
    try:
        request = parse_step(arg)
        for key in ("a", "b"):
            if request.get(key):
                request[key] = passages_in(con, s, request[key])
        if "steps" in request:
            request["steps"] = [passages_in(con, s, f) for f in request["steps"]]
        return shown(calculate(con, request))
    except (ValueError, KeyError, MathsUnavailable) as exc:
        return f"maths: {exc}"
