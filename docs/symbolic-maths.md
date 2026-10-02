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
| `chain` | `same` on each link of `a == b == c`, stopping at the first that fails | a derivation checked, and the step that went wrong named |
| `simplify`, `substitute`, `solve` | the algebra | rearranging, a definition put into another formula |
| `expand`, `factor`, `together`, `apart` | the forms of an expression; `apart` is partial fractions in a variable | a transfer function as its poles |
| `diff`, `integrate`, `series`, `limit` | the calculus | an antiderivative, a Taylor expansion |
| `evaluate` | numbers; a value may carry an SI prefix and a unit (`10k`, `1u`, `26mV`) | what given values produce |
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
passage and asks `read` how it was parsed. Then `integrate` gives F,
`same` checks that F's derivative is f, and `code` gives the line in C.
Claude writes the ADAA around it. For `tanh`, SymPy gives F as
`x - log(tanh(x) + 1)`, which is `log(cosh(x))`.

Second order needs the second antiderivative, which needs the
dilogarithm Li₂, and SymPy does not find it: `integrate` answers with the
integral unevaluated and says so. Here the model proposes and the tool
checks. Claude's first proposal on 2026-10-01 had a wrong sign, and `same`
said "not the same". The corrected x²/2 − x·log 2 + Li₂(−e^(−2x))/2 was
"the same", numerically at 40 points. `code` then says that C has no
polylogarithm, so the program needs its own Li₂.

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

**Its doors** (built 2026-10-01, step 2):

- `POST /maths` on the door: `op`, the formula `a` (and `b` for
  `same`), `notation`, `args` (`var`, `values`, `lower`, `upper`, `at`,
  `to`, `language`) and `mapping`. A formula is LaTeX, plain notation, or
  `chunk:<id>` for a display formula of the library, read through the
  wall: a hidden document's chunk answers as an absent one. A host that
  does not name the pack answers 404; one without `maths.python`, 503.
- The MCP tool `maths`, one call to that route.
- The surfer's action as one JSON object (AD2, 2026-10-02):
  `maths: {"op": "evaluate", "formula": "I_s*(exp(v/V_T) - 1)",
  "values": {"I_s": "2.52 nA", "v": "0.4 V", "V_T": "25.85 mV"}}`. The
  keys are `op`, `formula`, `other` (for `same`), `then` (for `chain`),
  `var`, `at` (for `series` and `limit`), `values` and `language`.
  Under a local model's grammar (the pack's `tool_grammar`, GBNF) the
  step can only be written in that shape. Formulas are plain notation
  and hold no backslash. A passage [n] is the calculator's plain reading
  of its display formula (the `read` answer's `plain`): the whole of it,
  or its right side inside a formula. `tool.surf_json` builds the
  request; a line that is not JSON still goes to `parse_step`.
- The check after the answer (AD2, step 2, `packs/maths/check.py`, the
  pack's `answer_check`). With tools on, the surf hands the answer to it
  before returning, and the result carries the checks (`checks`).
  - Each display equation is split at its top-level `=`, and each link
    is checked with `same`, in one calculator process (`{"batch": …}`).
    A link is judged only when its two sides together have at most one
    free symbol (`FREE_MAX`): arithmetic, or a one-variable identity.
    A relation among quantities (`ω_c/(2Q) = Δω/2`, which holds given
    `Q = ω_c/Δω`) is "not judged". Left out: a side that is a name
    (`I_1`, `H_n(z)`, `u'`), a unit conversion, a list of definitions in
    one display, and a display with alignment or an inequality.
  - A link that does not hold gets a line after its display: "The
    calculator finds that this does not hold".
  - Each number with three or more significant digits is held to the
    question, the passages and the tool's results, in units a thousand
    apart. A number within 5% of a result, and not equal to it, gets
    "(the calculator gives 13.23)". One found nowhere gets "(not
    checked)".
  - Measured on the 160 answers of the e1/e2 double run (2026-10-02):
    judging every link marked 64 "does not hold", most of them relations
    true given other facts. With the rule above: 17 marks, every one
    real. Fifteen are the Moog paper's `e^{xx}` typo quoted, and two are
    the model's own arithmetic in a diode answer (`0.4/0.02585 =
    15.478`, true 15.474). 44 links hold, 120 are not judged, 85 do not
    read. The numbers half is not measured on these answers: the eval
    files keep no passages.
- The library's formulas checked (AD2, step 4): the maths pack's
  watched step `equations` (`packs/maths/formulas.py`). The door hands
  out formula chunks not checked at `CHECK_VERSION`
  (`store.formulas_to_check`). The worker reads each formula and judges
  its chain's links in one calculator process. The door keeps the
  result in `data.check` (`store.set_formula_checks`): `reads`, `links`,
  `judged`, `holds`, and `broken`, the links that do not hold. A
  formula with a broken link carries "‹its = does not hold, by the
  calculator›" in the equations-nearby line its passage shows. A worker
  without `maths.python` asks for nothing. A re-chunk drops the checks
  and the step makes them again.
  - Judged, by one rule for the library and for answers
    (`check.verdict`): arithmetic, compared to the decimal places of the
    side that states the result (`31/53 × 1200 = 701.887` holds), and an
    identity in one variable, with the same symbol on both sides. Not
    judged: an equation in one unknown (`1 - y = 1/9`), a relation among
    several quantities, a display with a limit, sum, product, integral,
    ceiling, `\div`, a mixed number, a ratio or `:=`, two statements
    side by side (`\quad`), a function with no definition.
  - Measured on 2,100 of the 17,431 formulas (244 documents, read-only,
    2026-10-02): 35% read, 11 links judged, one does not hold. That one
    is real: `9/8 · 256/243 × 80/81 = 20480/19683 × 80/81` in a tuning
    paper, where the left side's first product is 32/27. The rules above
    came from the first two samples, which marked a true integral
    identity, a rounded division, mixed numbers and equations in `y` as
    broken. Coverage is small on purpose: a wrong "does not hold" next
    to a passage is worse than none.
  - Not built: checking a table's rows against each other (the RNN
    paper's antiderivatives). A table does not say which column is the
    derivative of which; it waits for a measured need.
- An equation by its number (AD2, step 5, `surf.named_equations`): a
  question that writes "equation (5)", "eq. (14)" or "equations (3) and
  (4)" gets those formula chunks after the door's first search and
  before the model's first step. One document gives them all: of the
  search's first eight documents (`NAMED_DOCS`), the earliest with the
  most of the numbers named, because every paper has an equation (1).
  On the live store (read-only, the 8 questions that name equations):
  the right paper in all 7 that got one, the eighth already had its
  equation among the search's passages. Taking each number from the
  first document that had it brought Helmholtz's (1) and Strogatz's (4)
  into Dattorro and RMS questions. The resonator question, wrong in all
  16 answers of two double runs, now starts with Dattorro's (5).
- The surfer's action as a line of text, as it was before AD2 and as a
  model without the grammar may still write it:
  `maths: same [3] == [7]`, `maths: integrate x \tanh(x)`,
  `maths: evaluate [4] with R=1000, C=1e-6`, `maths: code python <latex>`.
  A passage [n] alone is the display formula it holds. Inside a formula
  it is that formula's right side in parentheses (`diff x [3]`). The
  answer goes into the log as one line, its reading first. The prompt
  shows the action only on a host that runs the pack (the manifest's
  `tool_help`). It tells the model to check what it derives, each step
  and each number, and to believe a "not the same".
- The answer is written by a call that sees none of the steps. So the
  last six tool requests and their answers go into the bundle's note
  (`surf.answer_note`, `WORKED_KEPT`). Before 2026-10-01 they did not, and
  no check the surf made reached an answer.
- Plain notation knows only the names in `runtime.PLAIN_NAMES`, the
  functions and constants. Every other name is a symbol, so `beta` and
  `N` are quantities, not SymPy's beta function and `N()`.

The prax side is `src/prax/packs/maths/tool.py`. It runs the calculator,
resolves a chunk to its LaTeX, and parses the surfer's syntax.

## Step 1, measured (2026-10-01)

The rules live in the maths pack's runtime
(`src/prax/packs/maths/runtime.py`, `Reading`). Each rewrites a name the
parser would split into a placeholder it reads as one symbol, and
renames it after the parse:

- an accent (`\tilde{\zeta}_k` is `zeta_tilde_k`), a font
  (`\mathcal{T}` is `T_cal`), a multi-letter or comma subscript
  (`G_{max}`, `c_{in,p,i}`), a Greek multi-index
  (`D_{\alpha\beta\gamma}`), a parenthesized superscript (`g^{(m)}`, a
  label and not a power).
- `x[n]` as the signal `x(n)`, after an accent too.
- `\frac{d^2u}{dt^2}` as the second derivative (the parser knows only
  the first, so it is nested).
- `\operatorname{sgn}`, `\Re` and `\arg` as SymPy's functions.
- a factor before parentheses, not a function: a name the formula also
  uses alone, and `i`, `j`, `\pi`, `e` (`G(1 - G/N)`, `2\pi(1000)`).
- `e` as the base of a power as Euler's number.
- `cases` as a Piecewise, and a multi-line environment as one chain.
- prose (`\text{for all}`, a unit) dropped, and what follows a `\quad`.

Two checks refuse a reading instead of answering about it:

- **stopped short**: the ANTLR parser returns the first part of a
  formula and drops the rest without a word. A letter of the formula
  missing from the result, or an `=` that did not become an equation, is
  such a reading.
- **a command it does not read**: a relation such as `\simeq`, `\propto`
  or `\ll`, a matrix, an ellipsis or `\nabla`. A bare `*` is refused too,
  because in signal processing it is a convolution. So is a command of
  the formula that survived as a name.

On the same sample of 2,000 formulas (seed 4; formulas of documents
marked personal left out), with `scripts/eval_latex.py --modes
antlr,rules`:

| | the parser alone | with the rules |
|---|---|---|
| accepted | 710 (36%) "plausible" | 761 (38%) |
| refused as stopped short or unread | none: wrong readings came back as answers | 391 (20%) |
| no parse | 381 (19%) | 840 (42%) |
| a sign of a misreading among the answers | 909 (45%) | 8 (0.4%) |

The accepted readings were checked by hand against their LaTeX, in three
sets of 50 drawn apart. Each set came after the fixes the one before had
shown. Faithful: 21 of 50, then 36, then 43 (86%). Most wrong ones of the
last set are notation the formula leaves open: `V_3(R_9\beta - …)` may be
a function or a product. The others are physics notation the tool is not
for, such as a functional derivative or an expectation `E[…]`.

So about a third of the library's display formulas are read faithfully,
and nearly all of the rest are said to be unread rather than answered
wrongly. Every answer shows its reading back, which is how a model
catches the remaining ambiguities.

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
4. **The local model for the rest**, only after step 1 is measured. A
   reading of the `formulas` step writes SymPy for a formula the rules
   could not read. It is kept only when its LaTeX parses back to the same
   expression. At about a second a formula on the 35B model, the
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
they are used. They ask:

- whether two formulas are the same;
- how to get from one formula to another, each step of the answer
  checked;
- what given values produce;
- and, once, for an ADAA version of a shaper.


Each is scored by hand, with and without the tool, in grounded and in
open mode (`scripts/eval_maths.py`, questions in
`tests/eval/questions-maths.yaml`).

Since AD2 step 3 (2026-10-02), 10 of the 20 are also scored by machine
(`scripts/score_maths.py`): the six yes-or-no questions by the opening
sentence's verdict, and the four values by the closest number in the
answer within a tolerance, in any unit a thousand apart. A check may ask
for a pattern besides the verdict (`also`). An opening sentence that
declines ("the passages do not contain") scores wrong, as the hand
scores do. The hand scores of four runs are
`tests/eval/maths-hand-scores.yaml`; with `--hand` the scorer compares
itself with them. On those 160 answers it agrees on 141 (88%); most of
the rest are answers the hand called partly right for thin reasoning.
The derivation and ADAA questions stay with the hand.

The first run (2026-10-01, the local ask model, 8 steps) scored right,
partly right and wrong as follows:

| tools, mode | right | partly | wrong |
|---|---|---|---|
| on, grounded | 10 | 3 | 7 |
| off, grounded | 11 | 3 | 6 |
| on, open | 17 | 2 | 1 |
| off, open | 17 | 1 | 2 |

The tool was called in 7 of the 40 asks with tools on, and made no
difference. Four faults explained it:

- the answer never saw the tool's results;
- plain notation refused a decimal point;
- plain notation knew every SymPy name, so `beta` was a function;
- a passage could not stand inside a formula.

All four are fixed, the prompt now asks for checks, and `chain`,
`expand`, `factor`, `together` and `apart` were added. The rerun is the
measurement of that.

The second run (2026-10-01, the same model and steps, after the fixes
and two more: `with` after any operation, `e**x` as Euler's number):

| tools, mode | right | partly | wrong |
|---|---|---|---|
| on, grounded | 11 | 5 | 4 |
| off, grounded | 7 | 5 | 8 |
| on, open | 14 | 2 | 4 |
| off, open | 15 | 3 | 2 |

The answers are in `docs/eval/maths-answers-2026-10-01-c.json`, kept
out of the repository like the first run's. The tool was called in 11
of the 40 asks with tools on (39 calls), against 7 before.

Read with care. The "off" rows ran the same code both times, and
"off, grounded" went from 11 right to 7. So the spread between two runs
of the same thing is about four answers, and the second scoring may be
stricter than the first. Compare rows within one run, not across runs.
Within this run, grounded mode gains with the tool: 4 more right and 4
fewer wrong. Open mode does not.

What the tool did, case by case:
- It made answers right. With the RC values given, grounded mode could
  answer "10 ms" from `evaluate` where the library says nothing. On the
  Moog tanh forms, open mode checked the identity and said so.
- It made an answer wrong. For Dattorro's beta the model wrote the
  formula with ω_c = π f/F_s (a factor 2 short) and gave the tool's
  0.9678 instead of 0.9366. The tool computed what it was asked.
- It was misread. On the second antiderivative of tanh, `diff` gave a
  derivative that is not log cosh x. The model then said the tool had
  confirmed it. It used `diff` and `simplify` where `same` would have
  said "not the same". All four ways got this question wrong.
- 15 of the 39 calls failed. The causes, most frequent first:
  - a formula wrapped in `$…$`;
  - values without `with` (`x=1.0` at the end);
  - `solve` or `simplify` given an equation `a == b` (an IndexError);
  - `Li2` for `polylog(2, …)`;
  - an SI prefix inside the formula (`10k * 1u`), which only works as a
    value;
  - "given … and …" for definitions;
  - a parenthesis the model left open.

The fixes that followed (2026-10-01, the third round):
- `tool.parse_step` drops `$` and `$$`, takes trailing `x=…` values
  without `with` (for `same`, `chain`, `evaluate`, `substitute`), splits
  on `==` with or without spaces, and turns `simplify a == b` (also
  `expand`, `factor`, `together`) into `same`. "given … and …" is refused
  with how to write it.
- Plain notation reads an equation written with `=` or `==`, a prefixed
  number inside a formula (`10k * 1u`), and `Li2(z)` as
  `polylog(2, z)`. A formula it cannot parse says to check the
  parentheses, not Python's "invalid syntax".
- `I` and `E` are no longer SymPy's imaginary unit and Euler's number in
  plain notation. In these papers `I` is a current: the solve of
  `I == I1 + I1*exp(u)` gave the imaginary unit before. Euler's number
  is `exp(1)` or `e**x`.
- The prompt says to check with `same` against what a result must equal
  (`same diff(F, x) == f` for an antiderivative), and to write the
  formula a number comes from before evaluating it.

The double run (2026-10-02, after the third round): every way twice,
scored by the same rules in one sitting
(`docs/eval/maths-answers-2026-10-01-d1.json` and `-d2.json`, kept out
of the repository):

| tools, mode | run 1 R / P / W | run 2 R / P / W | same score in both |
|---|---|---|---|
| on, grounded | 11 / 5 / 4 | 11 / 6 / 3 | 13 of 20 |
| on, open | 12 / 5 / 3 | 13 / 6 / 1 | 17 of 20 |
| off, grounded | 7 / 4 / 9 | 9 / 3 / 8 | 13 of 20 |
| off, open | 11 / 8 / 1 | 13 / 4 / 3 | 13 of 20 |

- Two runs of the same way disagree on 3 to 7 of the 20 questions, and
  the right count moves by up to 2.
- In grounded mode the tool is worth 2 to 4 more right answers and about
  5 fewer wrong ones, in both runs. That is past the spread.
- In open mode it makes no difference: the model knows these identities
  without the library or the tool.
- Questions no way gets right yet: the resonator of Dattorro's (5) (it
  equals (1 − A(z))/2; every answer says no or cannot find it), the
  second antiderivative of tanh (every answer repeats the table's wrong
  form), and the diode current (13.27 to 13.34 mA against 13.23; the
  model computes e^15.47 by hand and is off).
- The tool was called in 16 and 13 of the 40 asks with it, 87 calls;
  30 failed. The ways they failed are the fourth round below.

The fourth round (2026-10-02):
- `tool.parse_step` takes the variable after `for` (`solve <f> for
  I1`), SymPy's order (`series tanh(x), x, 0, 5`), the point as
  `x=0` or `at x=0`, a call as the whole step (`diff(F, x)` is
  `simplify` of it), and drops words after the values (`answer: …`).
- Plain notation reads `W` as the Lambert W, and a function it does not
  know is named in the error with the list of those it does.

The rule for numbers (2026-10-02, 4aa6602): a number a passage prints is
cited, one the answer works out is a result from the note, and any other
carries "(not checked)". Both answer prompts hold it, and the surf line
asks for every computed number through `evaluate`. The second double run
(`docs/eval/maths-answers-2026-10-02-e1.json` and `-e2.json`):

| tools, mode | run 1 R / P / W | run 2 R / P / W | same score in both |
|---|---|---|---|
| on, grounded | 12 / 3 / 5 | 12 / 3 / 5 | 16 of 20 |
| on, open | 15 / 3 / 2 | 13 / 5 / 2 | 16 of 20 |
| off, grounded | 9 / 6 / 5 | 8 / 7 / 5 | 13 of 20 |
| off, open | 12 / 7 / 1 | 13 / 6 / 1 | 16 of 20 |

- Against the first double run every row moved by 0 to 2 right
  answers, inside the spread. The rule changed one question, not the
  totals.
- That question is the diode current. In the first run both tool-on
  ways called `evaluate` and gave 13.23 mA, the first right answers it
  ever had. The second run computed it by hand again (13.17, 13.28).
- "(not checked)" appeared in 1 of 160 answers. The marking half of the
  rule is mostly ignored by this model.
- A conflict it showed: for the RC time constant, grounded mode had the
  tool's 0.01 s and still said the passages do not hold it, in all four
  tool-on grounded answers of both double runs. The grounded prompt now
  says a result in the note was computed from the question's values, is
  used and said to be computed, and answers a question that is a
  calculation.
- Failed calls: 21 of 83 (30 of 87 before). `solve I1 for I = …` took
  `I` as the variable: "for" names a variable only when one stands alone
  after it.

## What the user decided (2026-10-01)

- The operations: the calculator above, with `same` as the check of the
  others, rather than one tool per operation.
- SymPy and ANTLR 4.11 in an environment of their own, on the hosts that
  name the pack; never in the door's serving path.
- Step 4 waits for step 1's measurement.
