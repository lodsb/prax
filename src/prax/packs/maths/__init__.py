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
            "    the results. When maths says not the same, believe it and look\n"
            "    again: a passage's formula may be extracted wrongly, or a sign\n"
            "    or factor of yours may be off."
        )
    },
    settings="maths",
)
