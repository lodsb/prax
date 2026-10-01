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
            "maths: same [n] == <latex>   check that two formulas are equal;\n"
            "                    also read, simplify, solve x, diff x,\n"
            "                    integrate x, series x, evaluate [n] with R=1000,\n"
            "                    code; a formula is [n] or LaTeX"
        )
    },
    settings="maths",
)
