"""The maths pack's calculator (docs/symbolic-maths.md): run by the
interpreter ``maths.python`` names, never imported by prax.

It imports SymPy and the standard library only: SymPy's LaTeX parser
needs ANTLR 4.11, and prax's environment holds 4.9 for OCR, so the two
live apart. One request comes in as JSON on standard input, one answer
goes out as JSON on standard output, and the caller kills the process
past its time limit. No code a model wrote runs here: a formula is read
by the LaTeX parser or by SymPy's expression parser without its
``eval`` transformations, and only the operations in ``OPERATIONS`` are
called on it.

    python runtime.py < request.json
"""

from __future__ import annotations

import cmath
import json
import random
import re
import sys
from collections.abc import Callable
from typing import Any

import sympy
from sympy.parsing.latex import parse_latex
from sympy.parsing.sympy_parser import parse_expr, standard_transformations

# ------------------------------------------------------------ the reading

NUMBER = re.compile(r"^\s*\(\s*\d+[a-z]?\s*\)\s*(\\quad|\\qquad)?")
TAIL = re.compile(r"(\\quad|\\qquad|\\,|\\;|\\!|,|\.|;)+\s*$")

# a name the parser would split, rewritten to a placeholder it reads as
# one symbol (or one function, before a parenthesis) and renamed after
PLACEHOLDER = "\\Upsilon_{%d}"
FIRST = 9001  # far from any index a formula writes on Upsilon itself
ACCENTS = {
    "tilde": "tilde",
    "widetilde": "tilde",
    "hat": "hat",
    "widehat": "hat",
    "bar": "bar",
    "overline": "bar",
    "vec": "vec",
    "dot": "dot",
    "ddot": "ddot",
    "check": "check",
    "breve": "breve",
}
FONTS = {
    "mathcal": "cal",
    "mathscr": "scr",
    "mathfrak": "frak",
    "mathbb": "bb",
    "mathbf": "",
    "boldsymbol": "",
    "bm": "",
    "mathit": "",
    "mathrm": "",
    "mathsf": "",
    "operatorname": "",
    "text": "",
    "textrm": "",
    "textit": "",
}
# what a paper writes for a function SymPy knows by another name
FUNCTIONS = {
    "sgn": sympy.sign,
    "sign": sympy.sign,
    "sinc": sympy.sinc,
    "erf": sympy.erf,
    "erfc": sympy.erfc,
    "arctanh": sympy.atanh,
    "artanh": sympy.atanh,
    "atanh": sympy.atanh,
    "arcsinh": sympy.asinh,
    "arsinh": sympy.asinh,
    "arccosh": sympy.acosh,
    "sech": sympy.sech,
    "csch": sympy.csch,
    "Si": sympy.Si,
    "Ci": sympy.Ci,
    "Li": sympy.li,
    "Re": sympy.re,
    "Im": sympy.im,
    "atan2": sympy.atan2,
    "max": sympy.Max,
    "min": sympy.Min,
    "arg": sympy.arg,
}
# a single letter (or a Greek letter's command) with an optional simple
# subscript, then an index in brackets: the signal x[n], h[k-1]
GREEK = (
    "alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa"
    "|lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma|tau|upsilon|phi|varphi"
    "|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega"
)
INDEX = re.compile(
    r"(?<![\\A-Za-z])([A-Za-z]|\\Upsilon_\{\d+\}|\\(?:" + GREEK + r"))"
    r"((?:_\{[^{}]*\}|_[A-Za-z0-9])?)"
    r"\[([^\[\]]+)\]"
)
BRACED = r"\{((?:[^{}]|\{[^{}]*\})*)\}"
# commands the tool does not read: a relation it has no meaning for, a
# matrix, an ellipsis, a bracket of quantum mechanics. A formula holding
# one is refused rather than read with the command as a name
UNREAD = (
    "simeq|approx|propto|ll|gg|sim|equiv|triangleq|doteq|cong|rightarrow|to"
    "|mapsto|Rightarrow|leftrightarrow|Leftrightarrow|longrightarrow|cdots|dots"
    "|ldots|vdots|ddots|langle|rangle|nabla|partial|otimes|oplus|wedge|vee"
    "|subset|subseteq|supset|cup|cap|forall|exists|mid|perp|parallel|star"
)
UNREAD_ENVS = (
    "array",
    "matrix",
    "pmatrix",
    "bmatrix",
    "vmatrix",
    "Bmatrix",
    "smallmatrix",
)
# a command for a function SymPy has, written without \operatorname
COMMAND_FUNCTIONS = {"Re": "Re", "Im": "Im", "arg": "arg"}


def clean(s: str) -> str:
    """The formula without its number, trailing spacing and punctuation,
    alignment marks, ``\\left``/``\\right``, labels and tags."""
    s = NUMBER.sub("", s)
    if (
        "\\begin{cases}" not in s
    ):  # a case's row keeps its & between value and condition
        s = s.replace("&", " ")
    s = re.sub(r"\\(left|right|big|Big|bigg|Bigg)[lr]?\b", "", s)
    s = re.sub(r"\\(displaystyle|textstyle|limits|nolimits)", "", s)
    s = re.sub(r"\\(label|tag)\{[^}]*\}", "", s)
    s = re.sub(r"\(\s*\d+(\.\d+)*[a-z]?\s*\)\s*$", "", s.strip())  # a trailing (22.1.8)
    if "\\begin{cases}" not in s:
        # a multi-line equation is one chain: the environment and its line
        # breaks go (a case distinction keeps them, _cases reads its rows)
        s = re.sub(
            r"\\(begin|end)\{(aligned|align\*?|split|gathered|eqnarray\*?)\}", " ", s
        )
        s = s.replace("\\\\", " ")
    # prose in a formula is not maths: \text{for all}, the unit \text{ s}
    s = re.sub(r"\\(text|textrm|mbox)\{([^{}]*\s[^{}]*|\s*[a-z]{1,3}\s*)\}", " ", s)
    # what follows a \quad is a condition or a comment, not the formula
    head = re.split(r"\\q?quad\b", s, maxsplit=1)[0]
    if head.strip():
        s = head
    return TAIL.sub("", s.strip()).strip()


def _name(inner: str) -> str:
    """A LaTeX name as a symbol's name: ``\\zeta`` is ``zeta``, braces,
    spaces and backslashes go."""
    return re.sub(r"[\\{}\s]", "", inner) or "x"


class Reading:
    """One formula read: the LaTeX after the rules, the placeholder of
    each rewritten name, and which rules fired."""

    def __init__(self, latex: str) -> None:
        self.names: dict[str, str] = {}
        self.rules: list[str] = []
        self._next = FIRST
        self.latex = self._rewrite(clean(latex))

    def _hold(self, name: str) -> str:
        for ph, held in self.names.items():
            if held == name:
                return ph
        ph = PLACEHOLDER % self._next
        self._next += 1
        self.names[ph] = name
        return ph

    def _rewrite(self, s: str) -> str:
        if "\\begin{cases}" in s:
            self.rules.append("cases")
        # what the tool does not read is refused, not guessed: a relation
        # it has no meaning for, a matrix, an ellipsis. \partial in a
        # derivative and an arrow under \lim are read, so they are taken
        # out before the look
        look = re.sub(r"\\frac\{\\partial[^{}]*\}\{\\partial[^{}]*\}", " ", s)
        look = re.sub(r"\\lim_\{[^{}]*\}", " ", look)
        found = re.search(r"\\(" + UNREAD + r")(?![A-Za-z])", look)
        if found:
            raise ValueError(f"the tool does not read \\{found.group(1)}")
        env = re.search(r"\\begin\{(" + "|".join(UNREAD_ENVS) + r")\}", s)
        if env:
            raise ValueError(f"the tool does not read a {env.group(1)}")
        if re.search(r"(?<!\^)\*", s):
            raise ValueError(
                "a bare * is a convolution or a product: written as \\cdot it is read"
            )
        # \Re, \Im, \arg are SymPy's functions; \{ \} group like parentheses
        s = re.sub(
            r"\\(" + "|".join(COMMAND_FUNCTIONS) + r")(?![A-Za-z])",
            lambda m: "\\operatorname{" + COMMAND_FUNCTIONS[m.group(1)] + "}",
            s,
        )
        s = s.replace("\\{", "(").replace("\\}", ")")

        # Leibniz's notation: \frac{d^2x}{dt^2} is the second derivative of x
        # (the parser knows the first derivative only, so the n-th is nested)
        def leibniz(m: re.Match[str]) -> str:
            n = int(m.group(2) or 1)
            inner = m.group(3)
            for _ in range(n):
                inner = f"\\frac{{d}}{{d{m.group(4)}}} ({inner})"
            return inner

        s = re.sub(
            r"\\frac\{d(\^\{?(\d)\}?)?\s*([A-Za-z])\}\{d\s*([A-Za-z])(\^\{?\d\}?)?\}",
            leibniz,
            s,
        )
        # a superscript in parentheses is a label, not a power: g^{(m)}, d^{(1)}
        s = re.sub(
            r"(\\[A-Za-z]+|[A-Za-z])(_\{[^{}]*\}|_[A-Za-z0-9])?\^\{\(([^(){}]+)\)\}",
            lambda m: self._hold(
                _name(m.group(1))
                + ("_" + _name(m.group(2)[1:]) if m.group(2) else "")
                + f"^({_name(m.group(3))})"
            ),
            s,
        )
        # \mathrm{d}x in an integral is the differential, not a name
        s = re.sub(r"\\mathrm\{d\}", "d", s)
        for _ in range(4):  # innermost first: \hat{\mathbf{x}}
            before = s

            def accent(m: re.Match[str]) -> str:
                self.rules.append("accent")
                return self._hold(f"{_name(m.group(2))}_{ACCENTS[m.group(1)]}")

            s = re.sub(r"\\(" + "|".join(ACCENTS) + r")\s*" + BRACED, accent, s)
            s = re.sub(r"\\(" + "|".join(ACCENTS) + r")\s+(\\?[A-Za-z]+)", accent, s)

            def font(m: re.Match[str]) -> str:
                inner = _name(m.group(2))
                if (
                    m.group(1) in ("mathrm", "text", "textrm", "mathit")
                    and len(inner) == 1
                ):
                    return inner  # a single upright letter is the letter
                self.rules.append("font")
                suffix = FONTS[m.group(1)]
                return self._hold(f"{inner}_{suffix}" if suffix else inner)

            s = re.sub(r"\\(" + "|".join(FONTS) + r")\s*" + BRACED, font, s)
            if s == before:
                break

        def folded(m: re.Match[str]) -> str:
            # a rewritten name with a subscript after it (\tilde{\zeta}_k):
            # two subscripts in a row make the parser stop short
            held = self.names[m.group(1)]
            return self._hold(f"{held}_{_name(m.group(2) or m.group(3))}")

        s = re.sub(r"(\\Upsilon_\{\d+\})_(?:\{([^{}]*)\}|([A-Za-z0-9]))", folded, s)

        def sub(m: re.Match[str]) -> str:
            self.rules.append("subscript")
            base = m.group(1)
            return self._hold(f"{_name(base)}_{_name(m.group(2))}")

        # a subscript of Greek letters is a multi-index name: D_{\alpha\beta\gamma}
        s = re.sub(
            r"(\\[A-Za-z]+|[A-Za-z])_\{((?:\s*\\(?:" + GREEK + r")\s*){2,})\}",
            sub,
            s,
        )
        # a subscript of two letters or more is a name, not a product, and
        # so is one of letters and commas (c_{in,p,i})
        s = re.sub(
            r"(\\[A-Za-z]+|[A-Za-z])_\{((?=[^{}]*[A-Za-z][A-Za-z0-9])[A-Za-z0-9,]+"
            r"|[A-Za-z0-9]+,[A-Za-z0-9,]+)\}",
            sub,
            s,
        )

        def index(m: re.Match[str]) -> str:
            self.rules.append("index")
            return f"{m.group(1)}{m.group(2)}({m.group(3)})"

        return INDEX.sub(index, s)

    def renamed(self, e: Any) -> Any:
        """The parsed expression with each placeholder given its name, and
        a known function's name read as SymPy's function."""
        swap: dict[Any, Any] = {}
        for sym in e.atoms(sympy.Symbol):
            name = self.names.get(_latex_key(sym.name))
            if name is None and "Upsilon_{" in sym.name:
                # a placeholder inside another name's subscript: Y_{Upsilon_{9001}}
                name = re.sub(
                    r"Upsilon_\{\d+\}",
                    lambda m: self.names.get("\\" + m.group(0), m.group(0)),
                    sym.name,
                )
            if name is not None:
                swap[sym] = sympy.Symbol(name)
        for f in e.atoms(sympy.core.function.AppliedUndef):
            fname = f.func.__name__
            name = self.names.get(_latex_key(fname), fname)
            known = FUNCTIONS.get(name)
            target = known if known is not None else sympy.Function(name)
            swap[f] = target(*f.args)
        # replace, not xreplace: the parser's unevaluated products keep a
        # second occurrence (in an exponent) out of xreplace's reach
        if swap:
            e = e.replace(lambda x: x in swap, lambda x: swap[x])
        # e as the base of a power is Euler's number; an e elsewhere (a
        # charge) stays a symbol
        euler = sympy.Symbol("e")
        return e.replace(
            lambda x: isinstance(x, sympy.Pow) and x.base == euler,
            lambda x: sympy.exp(x.exp),
        )


def _latex_key(name: str) -> str:
    """A placeholder symbol's name back to the LaTeX it was written as:
    SymPy names ``\\Upsilon_{9001}`` ``Upsilon_{9001}``."""
    return "\\" + name if name.startswith("Upsilon_{") else name


def _cases(r: Reading) -> Any:
    """``\\begin{cases} a & c \\\\ b & d \\end{cases}`` as a Piecewise, with
    whatever stands before the environment (``y =``) kept."""
    m = re.search(r"\\begin\{cases\}(.*)\\end\{cases\}", r.latex, re.DOTALL)
    assert m
    before = r.latex[: m.start()].strip()
    pieces = []
    for row in re.split(r"\\\\", m.group(1)):
        row = row.strip()
        if not row:
            continue
        value, _, cond = row.partition(" ") if "&" not in row else row.partition("&")
        raw = re.sub(r"\\text\{[^}]*\}|,", " ", cond).strip()
        if not raw or raw.lower() in ("otherwise", "else", "sonst"):
            pieces.append((r.renamed(parse_latex(value.strip())), True))
        else:
            pieces.append(
                (r.renamed(parse_latex(value.strip())), r.renamed(parse_latex(raw)))
            )
    pw = sympy.Piecewise(*pieces)
    if before.endswith("="):
        return sympy.Eq(r.renamed(parse_latex(before[:-1])), pw)
    return pw


# plain notation: names, numbers, operators, parentheses, commas and an
# equals sign, and nothing else. SymPy's expression parser is Python's
# eval, so this gate is what keeps a model's text from being code: no
# attribute access (a dot only inside a number), no dunder, no quotes, no
# brackets, and the evaluation sees no builtins (2026-10-01: without the
# gate a test's "__import__('os').system(...)" ran)
PLAIN = re.compile(r"^[A-Za-z0-9_ +\-*/^(),=]*$")
SAFE_GLOBALS: dict[str, Any] = {"__builtins__": {}}
for _n in dir(sympy):
    if not _n.startswith("_"):
        SAFE_GLOBALS[_n] = getattr(sympy, _n)


def _plain(formula: str) -> Any:
    text = formula.replace("^", "**")
    if (
        not PLAIN.match(formula)
        or "__" in formula
        or re.search(r"\.(?!\d)|(?<!\d)\.", formula)
    ):
        raise ValueError(
            "plain notation takes names, numbers, + - * / ** ^ ( ) , = only"
        )
    if "=" in text:
        left, _, right = text.partition("=")
        return sympy.Eq(_plain(left), _plain(right))
    return parse_expr(
        text,
        local_dict={},
        global_dict=dict(SAFE_GLOBALS),
        transformations=standard_transformations,
        evaluate=False,
    )


def read(formula: str, notation: str = "latex") -> tuple[Any, Reading | None]:
    """A formula as SymPy: LaTeX through the rules and the parser, or plain
    notation (``x**2 + 1``) through the gate of ``PLAIN`` and the
    expression parser with no builtins."""
    if notation == "plain":
        return _plain(formula), None
    r = Reading(formula)
    if "cases" in r.rules:
        return _cases(r), r
    e = _factors(r.renamed(parse_latex(r.latex)))
    _whole(r, e)
    _no_command_names(r, formula, e)
    return e, r


def _factors(e: Any) -> Any:
    """A name read as a function that the formula also uses as a quantity
    (``G(1 - G/N)``), or ``i`` and ``j``, the imaginary units, is a factor
    before parentheses, not a function: G times (1 - G/N)."""
    bare = {s.name for s in e.free_symbols}
    constants = {"pi": sympy.pi, "e": sympy.E}  # 2\pi(1000) is 2 pi times 1000
    swap = {}
    for f in e.atoms(sympy.core.function.AppliedUndef):
        name = f.func.__name__
        if len(f.args) != 1:
            continue
        arg = f.args[0]
        if name in constants:
            swap[f] = constants[name] * arg
        elif name in bare or name in ("i", "j"):
            swap[f] = sympy.Symbol(name) * arg
        elif (
            "_" in name
            and isinstance(arg, sympy.Add)
            and any(t.is_Number for t in arg.args)
        ):
            # a subscripted quantity before a sum with a plain number is a
            # factor: I_s(e^{v/V_T} - 1). x(n - 1) (no subscript), h_r(t - nT)
            # (no number) and X_c(j\Omega) (no sum) stay functions
            swap[f] = sympy.Symbol(name) * arg
    return e.xreplace(swap) if swap else e


def _no_command_names(r: Reading, formula: str, e: Any) -> None:
    """Refuse a reading in which a command of the formula survived as a
    name (``\\simeq`` as the symbol ``simeq``): a misreading the parser
    gives without a word. A Greek letter's command is a name by right."""
    written = set(re.findall(r"\\([A-Za-z]+)", formula)) - set(GREEK.split("|"))
    for s in e.atoms(sympy.Symbol):
        base = re.split(r"[_^{(]", s.name, maxsplit=1)[0]
        if base in written and base not in r.names.values():
            raise ValueError(f"the parser read the command \\{base} as a name")


def _whole(r: Reading, e: Any) -> None:
    """Refuse a reading that stopped short: the ANTLR parser can return the
    first part of a formula and drop the rest without a word (6% of a
    sample, docs/symbolic-maths.md). A name the rules rewrote that is not
    in the result, or an equals sign that did not become an equation, is
    such a reading, and an answer about a fragment is worse than none."""
    got = {str(x) for x in e.free_symbols} | {
        f.func.__name__ for f in e.atoms(sympy.core.function.AppliedUndef)
    }
    got |= {type(f).__name__ for f in e.atoms(sympy.Function)}
    used = {n for ph, n in r.names.items() if ph in r.latex}
    lost = sorted(n for n in used if n not in got and n not in FUNCTIONS)
    if lost:
        raise ValueError(f"the parser read only part of the formula; it dropped {lost}")
    # and every letter the formula writes: somewhere in the result's names
    # (a symbol, bound ones too, a subscript's letter, a function), so a
    # tail the parser dropped is seen by the letters it took with it
    names = {str(x) for x in e.atoms(sympy.Symbol)} | got
    held = {c for n in names for c in re.findall(r"[A-Za-z]", n)}
    bare_letters = re.sub(r"\\Upsilon_\{\d+\}|\\[A-Za-z]+", " ", r.latex)
    letters = set(re.findall(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", bare_letters))
    absorbed = {"d", "e", "i", "j"}  # a differential, Euler's number, imaginary units
    missing = sorted(letters - absorbed - held)
    if missing:
        raise ValueError(
            f"the parser read only part of the formula; it dropped {missing}"
        )
    bare = re.sub(r"\\(leq?|geq?|neq?|approx|equiv)\b", "", r.latex)
    if "=" in bare and not isinstance(e, sympy.core.relational.Relational):
        raise ValueError("the parser read only part of the formula: its = was lost")


# ------------------------------------------------------------ the operations


def _shown(e: Any) -> dict[str, Any]:
    return {"latex": sympy.latex(e), "text": str(e)}


def _symbols(e: Any) -> list[str]:
    return sorted(str(s) for s in e.free_symbols)


def _sides(e: Any) -> Any:
    """An equation as its two sides' difference, anything else as itself."""
    return e.lhs - e.rhs if isinstance(e, sympy.Equality) else e


def op_same(a: Any, b: Any, **_: Any) -> dict[str, Any]:
    """Whether two expressions are equal: their difference simplified to
    zero, else a numeric check at random points; it says which."""
    if isinstance(a, sympy.Equality) and isinstance(b, sympy.Equality):
        a, b = _sides(a), _sides(b)
        # two equations are the same relation when one side difference is a
        # constant multiple of the other's
        if sympy.simplify(a) == 0 and sympy.simplify(b) == 0:
            return {"same": True, "how": "both identities"}
        ratio = sympy.simplify(a / b)
        if ratio.free_symbols == set() and ratio != 0:
            return {"same": True, "how": "symbolic", "factor": str(ratio)}
        d = a - b
    else:
        d = _sides(a) - _sides(b)
    if sympy.simplify(d) == 0:
        return {"same": True, "how": "symbolic"}
    syms = sorted(d.free_symbols, key=str)
    if not syms:
        value = complex(sympy.N(d))
        return {"same": abs(value) < 1e-9, "how": "numeric", "difference": str(value)}
    rng = random.Random(7)
    worst = 0.0
    tried = 0
    for _point in range(40):
        point = {s: rng.uniform(0.1, 2.0) for s in syms}
        try:
            value = complex(sympy.N(d.subs(point)))
        except (TypeError, ValueError):
            continue
        if cmath.isnan(value):
            continue
        tried += 1
        worst = max(worst, abs(value))
    if tried < 5:
        return {
            "same": None,
            "how": "neither: the difference did not simplify and few points evaluated",
        }
    return {
        "same": worst < 1e-8,
        "how": f"numeric at {tried} random points in (0.1, 2)",
        "largest_difference": worst,
    }


def op_simplify(a: Any, **_: Any) -> dict[str, Any]:
    return {"result": _shown(sympy.simplify(a))}


def op_substitute(
    a: Any, *, values: dict[str, Any] | None = None, **_: Any
) -> dict[str, Any]:
    """``values`` maps a symbol's name to an expression (as parsed)."""
    swap = {sympy.Symbol(k): v for k, v in (values or {}).items()}
    return {"result": _shown(a.subs(swap))}


def op_solve(a: Any, *, var: str, **_: Any) -> dict[str, Any]:
    eq = a if isinstance(a, sympy.Equality) else sympy.Eq(a, 0)
    sols = sympy.solve(eq, sympy.Symbol(var), dict=False)
    return {"result": [_shown(s) for s in sols]}


def op_diff(a: Any, *, var: str, n: int = 1, **_: Any) -> dict[str, Any]:
    return {"result": _shown(sympy.diff(a, sympy.Symbol(var), n))}


def op_integrate(
    a: Any, *, var: str, lower: Any = None, upper: Any = None, **_: Any
) -> dict[str, Any]:
    x = sympy.Symbol(var)
    got = (
        sympy.integrate(a, (x, lower, upper))
        if lower is not None
        else sympy.integrate(a, x)
    )
    out: dict[str, Any] = {"result": _shown(got)}
    if got.has(sympy.Integral):
        out["note"] = "SymPy left an integral it could not do in closed form"
    return out


def op_series(
    a: Any, *, var: str, at: Any = 0, order: int = 6, **_: Any
) -> dict[str, Any]:
    return {"result": _shown(sympy.series(a, sympy.Symbol(var), at, order))}


def op_limit(a: Any, *, var: str, to: Any = 0, **_: Any) -> dict[str, Any]:
    return {"result": _shown(sympy.limit(a, sympy.Symbol(var), to))}


def op_evaluate(
    a: Any, *, values: dict[str, Any] | None = None, digits: int = 12, **_: Any
) -> dict[str, Any]:
    swap = {sympy.Symbol(k): v for k, v in (values or {}).items()}
    got = sympy.N(a.subs(swap), digits)
    return {"result": _shown(got)}


def op_code(a: Any, *, language: str = "c", **_: Any) -> dict[str, Any]:
    from sympy.printing.codeprinter import PrintMethodNotImplementedError

    printers = {"c": sympy.ccode, "python": sympy.pycode}
    if language not in printers:
        raise ValueError("language is c or python")
    try:
        return {"code": printers[language](a)}
    except PrintMethodNotImplementedError as exc:
        # C has no polylog: the program needs an implementation of it
        missing = str(exc).rsplit(":", 1)[-1].split()[0]
        raise ValueError(
            f"{language} has no {missing}: the expression needs an implementation"
            f" of it beside the code"
        ) from exc


OPERATIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "same": op_same,
    "simplify": op_simplify,
    "substitute": op_substitute,
    "solve": op_solve,
    "diff": op_diff,
    "integrate": op_integrate,
    "series": op_series,
    "limit": op_limit,
    "evaluate": op_evaluate,
    "code": op_code,
}
# the arguments that are formulas themselves, read like the main one
FORMULA_ARGS = ("lower", "upper", "at", "to")


def answer(request: dict[str, Any]) -> dict[str, Any]:
    """One request: ``op``, the formula(s) as ``a`` (and ``b`` for
    ``same``), ``notation`` (latex or plain), and the operation's own
    arguments. Every answer says how each formula was read."""
    op = str(request.get("op") or "read")
    notation = str(request.get("notation") or "latex")
    if op != "read" and op not in OPERATIONS:
        raise ValueError(f"no operation {op!r}; they are read, {', '.join(OPERATIONS)}")
    a, ra = read(str(request["a"]), notation)
    out: dict[str, Any] = {
        "op": op,
        "read": {
            "a": {
                **_shown(a),
                "symbols": _symbols(a),
                "rules": sorted(set(ra.rules)) if ra else [],
            }
        },
    }
    if op == "read":
        return out
    a = a.doit()  # what the formula writes (a derivative, an integral) done
    kwargs = dict(request.get("args") or {})
    for k in FORMULA_ARGS:
        if k in kwargs and isinstance(kwargs[k], str):
            kwargs[k] = read(kwargs[k], notation)[0]
    if "values" in kwargs:
        kwargs["values"] = {
            str(k): read(str(v), notation)[0] if isinstance(v, str) else v
            for k, v in kwargs["values"].items()
        }
    if op == "same":
        b, rb = read(str(request["b"]), notation)
        out["read"]["b"] = {
            **_shown(b),
            "symbols": _symbols(b),
            "rules": sorted(set(rb.rules)) if rb else [],
        }
        mapping = request.get("mapping") or {}
        if mapping:  # one paper's symbols as another's: a name, or plain notation
            b = b.xreplace(
                {sympy.Symbol(k): _plain(str(v)) for k, v in mapping.items()}
            )
        out.update(op_same(a, b.doit()))
        return out
    out.update(OPERATIONS[op](a, **kwargs))
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    try:
        request = json.loads(sys.stdin.read())
        result = answer(request)
    except Exception as exc:  # noqa: BLE001 - the caller reads the error as the answer
        result = {"error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
