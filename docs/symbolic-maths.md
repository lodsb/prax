# Symbolic maths: the maths pack (stage AD)

The plan of stage AD in `docs/PLAN.md`, and since 2026-10-01 the design
of the first pack with capability (`docs/packs.md`). It answers three
questions:

- how many documents carry formulas worth it;
- which parser reads marker's LaTeX;
- what "plug things together" can ask of a tool.

Measured read-only on 2026-09-30 with `scripts/eval_latex.py`. The user
chose on 2026-10-01: a small SymPy calculator that a model calls, with
equality as the step that checks the others (below).

## What the library holds

A display equation is a `formula` chunk. Its `data` holds the LaTeX
(`latex`), the number the prose refers to it by (`number`) and the
readings in words (`readings`). Only marker writes them, so only the
documents marker read have any.

| | count |
|---|---|
| `formula` chunks | 17,417 |
| documents with any | 243 |
| documents with 5 or more | 227 |
| documents with 20 or more | 159 |

In a sample of 2,000 formulas, 1,767 have an `=`, 527 a sum, product
or integral, 350 some `\text{}`, and 285 an environment such as
`cases`. Most are relations between named quantities, the kind a
tool could work with.

## Which parser

SymPy 1.14 has two LaTeX parsers (`parse_latex`, backends `antlr` and
`lark`). The ANTLR one needs `antlr4-python3-runtime` 4.11. prax's
environment has another version, so the measurement ran in a scratch
environment. Each formula was cleaned first: the equation number, a
trailing `\quad`, punctuation and alignment marks taken off.

| backend | parsed | plausibly faithful |
|---|---|---|
| `antlr` | 1,619 of 2,000 (81%) | about 710 (36%) |
| `lark` | 165 of 2,000 (8%) | 72 (4%) |

"Parsed" is not "read right". The ANTLR parser misreads the notation
of papers in ways a heuristic can count:

| what went wrong | share | example |
|---|---|---|
| a command read as a symbol | 23% | `\tilde{\zeta}` becomes `tilde*zeta`, `\mathcal{T}` becomes `T*mathcal` |
| no parse | 19% | `\|\sqrt{\phi}\|^2`, `(a_0/b_0)` in `\left(`…`\right)` pairs |
| evaluated to a truth value | 9% | an equation SymPy decided was false |
| an index read as a product | 7% | the signal `x[n]` becomes `n*x` |
| cut short | 6% | `\widetilde{s}_K(x) = …` becomes `s*widetilde` |
| a name split into letters | a few | `IMF_1(t)` becomes `I*M*F_1(t)` |

So 36% is the ceiling of the parser as it stands, and the true share of
faithful readings is lower. A parse that is wrong is worse than none:
a tool that simplifies `n*x` where the paper meant the signal `x[n]`
answers confidently and wrongly.

## What "plug things together" can mean

A model is fluent at algebra and unreliable at it: asked how to get from
A to B, it writes a chain of steps, and a wrong step looks like a right
one. SymPy does the parts a model gets wrong, or says it cannot. So the
tool is a small calculator of whitelisted SymPy operations that a model
calls (the user, 2026-10-01). The model designs, and the tool computes
and checks.

| operation | what it does | for |
|---|---|---|
| `read` | the formula as parsed, with its symbols | catching a misreading before anything else |
| `same` | the difference simplifies to zero, else a numeric check at random points; it says which | a model's step checked, two papers' formulas compared |
| `simplify`, `substitute`, `solve` | the algebra | rearranging, a definition put into another formula |
| `diff`, `integrate`, `series`, `limit` | the calculus | an antiderivative, a Taylor expansion |
| `evaluate` | numbers, with units when given | what given values produce |
| `code` | the expression as C or Python (SymPy's printers) | the line that goes into a program |

A formula is LaTeX, a passage of the library by its number, or plain
maths notation. Every answer shows the formula back as the tool read
it (`sympy.latex`), so the model, and a person reading the trail, can
catch a misreading. Comparing formulas of two papers needs a mapping of
their symbols. The model proposes it, and the tool takes it as an
argument.

**An example the user gave.** "Make an antiderivative-antialiased
version of this shaper." First-order ADAA needs the antiderivative F of
the shaper f. The output is (F(xₙ) − F(xₙ₋₁)) / (xₙ − xₙ₋₁), with f at
the midpoint when the two inputs are close. Claude finds the shaper's
passage and asks `read` how it was parsed. Then `integrate` gives F
(and, again, the second antiderivative for second order), `same` checks
that F's derivative is f, and `code` gives the line in C. Claude writes
the ADAA around it. For `tanh`, F is `log(cosh(x))`. The second
antiderivative needs a polylogarithm, which SymPy writes and a model
rarely gets right.

## Where it runs

**Its own Python.** OCR's RapidOCR needs `omegaconf`, which pins ANTLR
4.9, and SymPy's LaTeX parser needs ANTLR 4.11, so the two cannot share
prax's environment. The tool runs in a subprocess with an environment of
its own (SymPy 1.14 and `antlr4-python3-runtime` 4.11), named by the
setting `maths.python`, as marker has its own. A host that does not name
the pack in `packs:` needs neither.

**A sandbox.** The subprocess takes one request as JSON on its input and
answers as JSON, under a time limit (`maths.timeout_s`). It imports
nothing of prax. No code a model wrote runs. SymPy's `sympify` is
Python's `eval`, so a formula is read by the LaTeX parser or by a
restricted parser of plain notation, and only the whitelisted operations
are called on it.

**Its doors.** A route of the door (`POST /maths`), an MCP tool of the
same name for Claude, and a `maths:` action of the surfer for the local
model, which cannot run code.

## The order of the work

Each step measured before the next:

1. **Rules before the parser** (about a day). Each normalizes one thing
   the parser misreads:
   - `x[n]` as an indexed function;
   - an accent as part of the name (`\tilde{\zeta}` as `zeta_tilde`);
   - a `\mathcal`, `\mathbf` or `\operatorname` name as one symbol;
   - `cases` as `Piecewise`, and `\left`/`\right` pairs;
   - a multi-letter name that the text defines.

   They are measured on the same 2,000 formulas and hand-checked on
   100. A rule stays only if it raises the faithful share without new
   misreadings.
2. **The tool** (about two days): the subprocess, its operations, the
   route, the MCP tool and the surf action, as the maths pack.
3. **The questions** (below), with and without the tool.
4. **The local model for the rest**, only after step 1 is measured: a
   reading of the `formulas` step that writes SymPy for a formula the
   rules could not read, kept only when its LaTeX parses back to the
   same expression. At about a second a formula on the 35B model, the
   roughly 11,000 left after the rules are three to four hours.

## Where the parse lives

In the formula chunk's `data`, beside the LaTeX and the readings:

- `sympy`: the expression as `srepr`;
- `symbols`: each symbol to the LaTeX it came from, and what the text
  says it is when the readings say so;
- `parsed_by`: `rules` or the model's name, with its revision.

Chunks are disposable, and a re-index keeps the chunks whose text did
not change. A parse is therefore redone only where the formula
changed, and a reading of the `formulas` step is how it is asked for
again. No new table.

## How it is measured

About twenty questions over the 243 documents, shown to the user before
they are used. Some ask whether two formulas are the same, some how to
get from one formula to another (each step of the answer checked), some
what given values produce, and one asks for an ADAA version of a shaper.
Each is scored by hand, with and without the tool, in grounded and in
open mode (`scripts/eval_ask.py`).

## What the user decided (2026-10-01)

- The operations: the calculator above, with `same` as the check of the
  others, rather than one tool per operation.
- SymPy and ANTLR 4.11 in an environment of their own, on the hosts that
  name the pack; never in the door's serving path.
- Step 4 waits for step 1's measurement.
