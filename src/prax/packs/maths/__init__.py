"""The maths pack: a SymPy calculator a model calls (docs/symbolic-maths.md).
Capability only so far; its knowledge (a module for theorems and
definitions) waits until an extraction measures a need for one."""

from __future__ import annotations

from prax.packs.base import Pack

# The surfer's maths step is one JSON object (AD2, 2026-10-02): under a
# local model's grammar it can only be written so that it parses, which
# 21 to 30 of every 85 free-text calls were not. Formulas are plain
# notation; a passage is [n], alone or inside one. No backslash: a JSON
# string would read LaTeX's \frac as a form feed. The operations are
# tool.OPERATIONS (tests/test_maths_tool.py holds the two together).
GRAMMAR = r"""
maths ::= "maths: {" m-op ", " m-formula m-more "}\n"
m-more ::= m-other? m-then? m-var? m-at? m-values? m-lang?
m-op ::= "\"op\": \"" m-name-op "\""
m-name-op ::= m-check | m-algebra | m-calculus
m-check ::= "read" | "same" | "chain" | "evaluate" | "code"
m-algebra ::= "simplify" | "expand" | "factor" | "together" | "apart" | "substitute"
m-calculus ::= "solve" | "diff" | "integrate" | "series" | "limit"
m-formula ::= "\"formula\": " m-str
m-other ::= ", \"other\": " m-str
m-then ::= ", \"then\": [" m-str (", " m-str){0,5} "]"
m-var ::= ", \"var\": \"" m-name "\""
m-at ::= ", \"at\": " m-str
m-values ::= ", \"values\": {" m-pair (", " m-pair){0,7} "}"
m-pair ::= "\"" m-name "\": " m-str
m-lang ::= ", \"language\": \"" ("c" | "python") "\""
m-name ::= [A-Za-z] [A-Za-z0-9_]{0,15}
m-str ::= "\"" m-ch{1,240} "\""
m-ch ::= [^"\\\n\r\t]
"""

HELP = """\
maths: {"op": "same", "formula": "[3]", "other": "1/(1 + exp(-x))"}
                    whether two formulas are equal
maths: {"op": "chain", "formula": "a", "then": ["b", "c"]}
                    a derivation a = b = c, checked link by link
maths: {"op": "evaluate", "formula": "1/(2*pi*R*C)", "values": {"R": "10k", "C": "1u"}}
                    a number; values may carry k M m u n p
maths: {"op": "solve", "formula": "I = I1 + I1*exp(u)", "var": "I1"}
                    also diff, integrate, series and limit (with "at"),
                    apart (with "var"), simplify, expand, factor, together,
                    read, code (with "language": "c" or "python")
    One JSON object on the line. A formula is plain notation (x**2*exp(-x),
    tanh(x)/2, Li2(z), W(z)) or a passage [n], alone or inside one
    ("[3]*2"); write exp(x) for e to the x, and no LaTeX. "values" puts
    given values or definitions in, for any operation. Do the maths here,
    not in your head: check a formula you derive, a step you take and a
    number you give before you answer; the answer is given the results.
    Check with same against what a result must equal: an antiderivative
    F of f is {"op": "same", "formula": "diff(F, x)", "other": "f"}.
    Every number the answer will work out comes from evaluate, with the
    formula it comes from and the given values. When maths says not the
    same, believe it and look again: a passage's formula may be extracted
    wrongly, or a sign or factor of yours may be off."""

MANIFEST = Pack(
    name="maths",
    tools={"maths": "prax.packs.maths.tool:surf_maths"},
    tool_help={"maths": HELP},
    tool_grammar={"maths": GRAMMAR.strip()},
    answer_check="prax.packs.maths.check:check_answer",
    settings="maths",
)
