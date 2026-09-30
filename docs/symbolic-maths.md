# Symbolic maths: the plan (stage AD)

A plan, not a design that is built. It answers the three questions of
stage AD in `docs/PLAN.md`:

- how many documents carry formulas worth it;
- which parser reads marker's LaTeX;
- what "plug things together" can ask of a tool.

The build waits for the user's reading of it. Measured read-only on
2026-09-30 with `scripts/eval_latex.py`.

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

Five operations, from the most to the least useful as far as the
library goes. Each takes formulas by their passage number.

1. **Same or not.** Two papers write one relation in their own
   notation. The tool maps one's symbols to the other's (the model
   proposes the mapping) and asks SymPy whether the difference
   simplifies to zero, or checks it numerically at random points.
2. **Substitute.** A definition from one formula into another: the
   transfer function of a filter into the system equation.
3. **Solve and rearrange.** Solve for a quantity the question asks
   about.
4. **Evaluate.** Numbers from the text or the question, with units kept
   apart (SymPy's `units`), for "what cutoff do these values give".
5. **Derive.** Differentiate, expand in a series, take a limit.

The tool shows the formula back as it read it (`sympy.latex` of the
expression) with every answer. The model, and a person reading the
trail, then see the reading and can catch a misreading.

## The pipeline

Three steps, each measured before the next:

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
2. **The local model for the rest** (about a day, then a batch). A new
   reading of the `formulas` step. The model writes SymPy for a formula
   the rules could not read. It is given the formula, the lines around
   it and the symbol table so far. The result is kept only when a check
   passes: its LaTeX, rendered back, parses to the same expression tree
   as the model's code. At about a second a formula on the 35B model,
   the roughly 11,000 left after the rules are three to four hours.
3. **The tool** (about two days). A `math` action of the surfer
   (`prax.answering.surf`), with a grammar line for local models, and
   an operation of open ask mode. It runs in a subprocess with a
   timeout and SymPy alone, never code the model wrote.

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

Thirty questions over the 243 documents, written with the user. Ten
ask whether two formulas are the same, ten ask to solve or substitute,
and ten ask what given values produce. Each is scored by hand, with and
without the tool, in grounded and in open mode (`scripts/eval_ask.py`).

## What the user decides

- Which of the five operations matter. The pipeline is the same for
  all of them, but the tool's first version need not do all five.
- Whether step 2 (the model's reading) is worth its batch, after step 1
  has been measured.
- Whether the SymPy runtime and ANTLR 4.11 join prax's dependencies (an
  optional extra, `maths`). They are not in the serving path.
