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
)
WITH_VAR = ("solve", "diff", "integrate", "series", "limit")
CHUNK_REF = re.compile(r"^chunk:(\d+)$")


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
    return run(out)


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
    if op not in OPERATIONS:
        raise ValueError(f"maths takes one of {', '.join(OPERATIONS)} first")
    request: dict[str, Any] = {"op": op}
    args: dict[str, Any] = {}
    if " with " in rest and op in ("evaluate", "substitute"):
        rest, _, given = rest.partition(" with ")
        values = {}
        for pair in given.split(","):
            name, eq, value = pair.partition("=")
            if not eq:
                raise ValueError("with takes name=value pairs, comma-separated")
            values[name.strip()] = value.strip()
        args["values"] = values
    if op in WITH_VAR:
        var, _, rest = rest.partition(" ")
        if not re.fullmatch(r"[A-Za-z]\w*", var):
            raise ValueError(f"{op} takes the variable first: {op} x <formula>")
        args["var"] = var
    if op == "code" and rest.split(" ", 1)[0] in ("c", "python"):
        lang, _, rest = rest.partition(" ")
        args["language"] = lang
    if op == "same":
        a, sep, b = rest.partition(" == ")
        if not sep:
            raise ValueError("same takes two formulas: same <a> == <b>")
        request["a"], request["b"] = a.strip(), b.strip()
    else:
        request["a"] = rest.strip()
    if not request["a"]:
        raise ValueError(f"{op} takes a formula: a passage [n] or LaTeX")
    # a model writes x**2 or exp(x) as often as LaTeX: formulas without a
    # backslash and with a * are plain notation (passages are LaTeX)
    written = [request[k] for k in ("a", "b") if k in request]
    out_written = [f for f in written if not f.startswith("[")]
    plainly = re.compile(
        r"\*|\b(exp|log|sqrt|tanh|sinh|cosh|sin|cos|tan|atan|polylog)\("
    )
    if (
        out_written
        and all("\\" not in f for f in out_written)
        and any(plainly.search(f) for f in out_written)
    ):
        request["notation"] = "plain"
    if args:
        request["args"] = args
    return request


def shown(got: dict[str, Any]) -> str:
    """An answer as one line for the surfer's log: the reading first, so a
    misreading is seen, then the result."""
    if "error" in got:
        return f"maths: {got['error']}"
    reads = got.get("read") or {}
    parts = [f"read {k} as {v.get('text')}" for k, v in reads.items()]
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


def surf_maths(con: sqlite3.Connection, s: Any, arg: str) -> str:
    """The surfer's ``maths:`` action: a passage [n] is the formula it
    holds, anything else is LaTeX the model wrote."""
    try:
        request = parse_step(arg)
        for key in ("a", "b"):
            ref = request.get(key)
            if ref and re.fullmatch(r"\[\d+\]", ref):
                p = s.by_n(ref)
                if p is None or p.chunk_id is None:
                    return f"maths: there is no passage {ref}"
                request[key] = f"chunk:{p.chunk_id}"
        return shown(calculate(con, request))
    except (ValueError, KeyError, MathsUnavailable) as exc:
        return f"maths: {exc}"
