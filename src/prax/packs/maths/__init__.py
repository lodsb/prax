"""The maths pack: a SymPy calculator a model calls (docs/symbolic-maths.md).
Capability only so far; its knowledge (a module for theorems and
definitions) waits until an extraction measures a need for one."""

from __future__ import annotations

from prax.packs.base import Pack

MANIFEST = Pack(
    name="maths",
    tools={"maths": "prax.packs.maths.tool:surf_maths"},
    tool_help={
        "maths": (
            "maths: same [n] == <formula>   whether two formulas are equal\n"
            "maths: chain <a> == <b> == <c>   check a derivation link by link\n"
            "maths: evaluate [n] with R=10k, C=1u   a number (k M m u n p allowed)\n"
            "maths: solve x <formula>   also diff x, integrate x, series x,\n"
            "                    limit x, apart s, simplify, expand, factor,\n"
            "                    together, read, code c <formula>\n"
            "    A formula is a passage [n], LaTeX, or plain notation\n"
            "    (x**2*exp(-x), tanh(x)/2); [n] may stand inside one\n"
            "    (diff x [3], same [2] == 1/(1 + exp(-x))). Any step may end\n"
            "    with x=..., y=... to put given values or definitions in.\n"
            "    Write exp(x) for e to the x. Do the maths here,\n"
            "    not in your head: check a formula you derive, a step you take\n"
            "    and a number you give before you answer. The answer is given\n"
            "    the results. Check with same, against what it must equal: an\n"
            "    antiderivative F of f is same diff(F, x) == f, never diff alone.\n"
            "    Every number the answer will work out comes from evaluate:\n"
            "    write the formula it comes from, with the given values\n"
            "    (evaluate 2*pi*f/fs with f=1k, fs=48k). The answer marks a\n"
            "    number worked out without one as not checked.\n"
            "    When maths says not the same, believe it and look again: a\n"
            "    passage's formula may be extracted wrongly, or a sign or\n"
            "    factor of yours may be off."
        )
    },
    settings="maths",
)
