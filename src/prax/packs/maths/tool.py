"""The maths pack's side in prax (docs/symbolic-maths.md): the calculator
run as a subprocess of its own environment, a formula of the library
passed to it as LaTeX, and the surfer's ``maths:`` action.

The calculator is ``runtime.py`` beside this file, run by the interpreter
``maths.python`` names (SymPy 1.14 and ANTLR 4.11, which prax's own
environment cannot hold beside OCR's ANTLR 4.9). It takes one request as
JSON and answers as JSON; past ``maths.timeout_s`` the process is killed
and the answer says so. Nothing a model wrote is run: the request is
data, and the runtime reads it with its parsers.

A formula of the library inside one a model wrote (``chunk:<id>`` from
the door or the MCP tool, ``[n]`` in the surf) is a placeholder symbol in
the formula and its LaTeX in ``passages``: the runtime reads the LaTeX
with its LaTeX reader and puts the expression in, the whole of it when
the placeholder is the formula, its right side when it stands inside one.
It is never turned into plain notation and read back (the review of
2026-10-02: ``y[n]``, ``H(z)`` and ``V_{T,1}`` did not survive that).
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
CHUNK_REF = re.compile(r"\bchunk:(\d+)\b")
PASSAGE = re.compile(r"\[(\d+)\]")
# the JSON step's keys and what each holds (the pack's grammar writes this
# shape for a local model; a model without the grammar is held to it here)
STEP_KEYS = {
    "op": str,
    "formula": str,
    "other": str,
    "then": list,
    "var": str,
    "at": str,
    "values": dict,
    "language": str,
}


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


def run(request: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    """One request to the calculator, its answer as a dict; an error is an
    ``error`` key, as the runtime gives it. ``timeout`` for a batch, which
    is several requests' time."""
    if timeout is None:
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


def placeholder(chunk_id: int) -> str:
    """The symbol a library formula stands as in a formula, until the
    runtime puts its reading in."""
    return f"PASSAGEc{chunk_id}"


def _formulas(request: dict[str, Any]) -> list[tuple[str, Any]]:
    """Where a request holds formulas: (key, value) for a, b, steps and
    the values."""
    out: list[tuple[str, Any]] = [(k, request.get(k)) for k in ("a", "b", "steps")]
    out.append(("values", (request.get("args") or {}).get("values")))
    return [(k, v) for k, v in out if v]


def calculate(con: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    """A request from the door, the MCP tool or the surf: each formula of
    the library it names (``chunk:<id>``) goes to the runtime as LaTeX.
    In LaTeX notation a formula that is only a reference is that LaTeX; in
    plain notation each reference is a placeholder with its LaTeX in
    ``passages``."""
    op = str(request.get("op") or "read")
    if op not in OPERATIONS:
        raise ValueError(f"no operation {op!r}; they are {', '.join(OPERATIONS)}")
    out = json.loads(json.dumps(request))  # a copy the references are put into
    passages: dict[str, str] = dict(out.get("passages") or {})
    plain = out.get("notation") == "plain"

    def put(text: str) -> str:
        whole = CHUNK_REF.fullmatch(text.strip())
        if whole and not plain:
            return formula_of(con, int(whole.group(1)))

        def one(m: re.Match[str]) -> str:
            chunk_id = int(m.group(1))
            passages[placeholder(chunk_id)] = formula_of(con, chunk_id)
            return placeholder(chunk_id)

        return CHUNK_REF.sub(one, text)

    for key in ("a", "b"):
        if isinstance(out.get(key), str):
            out[key] = put(out[key])
    if isinstance(out.get("steps"), list):
        out["steps"] = [put(str(f)) for f in out["steps"]]
    values = (out.get("args") or {}).get("values")
    if isinstance(values, dict):
        out["args"]["values"] = {str(k): put(str(v)) for k, v in values.items()}
    if passages:
        out["passages"] = passages
    return run(out)


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


# ------------------------------------------------ the surfer's maths step
# One JSON object (the pack's GRAMMAR and HELP): {"op", "formula", "other",
# "then", "var", "at", "values", "language"}. Formulas are plain notation;
# a passage [n] stands for the display formula it holds.


def request_of(step: dict[str, Any]) -> dict[str, Any]:
    """The JSON step as a calculator request, held to the step's shape;
    ValueError says what is wrong."""
    for key, value in step.items():
        want = STEP_KEYS.get(key)
        if want is None:
            raise ValueError(
                f"the step has no {key!r}; it takes {', '.join(STEP_KEYS)}"
            )
        if not isinstance(value, want):
            # a ValueError: the step's caller reads it as what to write
            raise ValueError(f'"{key}" is a {want.__name__}')  # noqa: TRY004
    if any(not isinstance(f, str) for f in step.get("then") or []):
        raise ValueError('"then" is a list of formulas')
    if any(
        not isinstance(v, str | int | float)
        for v in (step.get("values") or {}).values()
    ):
        raise ValueError('"values" maps a name to a value or a formula')
    op = str(step.get("op") or "")
    if op not in OPERATIONS:
        raise ValueError(f"op is one of {', '.join(OPERATIONS)}")
    formula = str(step.get("formula") or "").strip()
    if not formula:
        raise ValueError(f'{op} takes a "formula"')
    request: dict[str, Any] = {"op": op, "a": formula, "notation": "plain"}
    if op == "same":
        if not step.get("other"):
            raise ValueError('same takes "formula" and "other", the two to compare')
        request["b"] = str(step["other"])
    if op == "chain":
        then = list(step.get("then") or [])
        if not then:
            raise ValueError('chain takes "formula" and "then", the steps after it')
        request["steps"] = [formula, *then]
    args: dict[str, Any] = {}
    if op in WITH_VAR:
        if not step.get("var"):
            raise ValueError(f'{op} takes "var", the variable')
        args["var"] = str(step["var"])
    if step.get("at") is not None and op in ("series", "limit"):
        args["at" if op == "series" else "to"] = str(step["at"])
    if step.get("values"):
        args["values"] = {str(k): str(v) for k, v in step["values"].items()}
    if step.get("language"):
        args["language"] = str(step["language"])
    if args:
        request["args"] = args
    return request


def with_chunks(s: Any, text: str) -> str:
    """A formula with each passage [n] as ``chunk:<id>`` of the formula it
    holds; a passage that is not one says so."""

    def one(m: re.Match[str]) -> str:
        p = s.by_n(m.group(0))
        if p is None or p.chunk_id is None:
            raise ValueError(f"there is no passage {m.group(0)}")
        return f"chunk:{p.chunk_id}"

    return PASSAGE.sub(one, text)


def surf_maths(con: sqlite3.Connection, s: Any, arg: str) -> str:
    """The surfer's ``maths:`` action: one JSON object, whose passages [n]
    are the formulas they hold."""
    try:
        try:
            step = json.loads(arg)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"write the step as one JSON object ({exc.msg}), e.g."
                ' {"op": "evaluate", "formula": "2*pi*f", "values": {"f": "1k"}}'
            ) from exc
        if not isinstance(step, dict):
            raise ValueError("write the step as one JSON object")  # noqa: TRY004
        request = request_of(step)
        for key in ("a", "b"):
            if request.get(key):
                request[key] = with_chunks(s, request[key])
        if "steps" in request:
            request["steps"] = [with_chunks(s, f) for f in request["steps"]]
        values = (request.get("args") or {}).get("values")
        if values:
            request["args"]["values"] = {
                k: with_chunks(s, v) for k, v in values.items()
            }
        return shown(calculate(con, request))
    except (ValueError, KeyError, TypeError, MathsUnavailable) as exc:
        return f"maths: {exc}"
