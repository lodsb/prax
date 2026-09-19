# Does `ask` answer with the equation, 2026-09-17

Question: once a paper's mathematics is in the store — its display
equations as `formula` chunks with their LaTeX, a reading in words under
each — does `ask` find the equation a question is about, cite it, and
quote it right? The honest test the marker note
(`marker-equations-2026-09-15.md`) left open.

Setup: `tests/eval/questions-equations.yaml`, 22 questions whose answer
is an equation in one of the 21 papers read with marker on 2026-09-16
(named: the question names the equation or the thing; meaning: it says
what the equation does, in other words). `scripts/eval_ask.py` puts each
through `POST /ask` (8 passages) twice: with no steps, and with the
host's default of 8 surfing steps. Model: Qwen 3.6 35B-A3B (UD-Q4_K_S)
on the local llama-server, two slots. Scored automatically per question:

    sources   the expected paper is among the passages the model was given
    cited     the answer cites the expected paper
    formula   the answer cites a formula chunk (an equation, not prose about it)
    match     a cited passage of the expected paper matches the question's regex
    quoted    the answer holds maths of its own ($…$)

The store at the time: 10,050 documents, 9,012 formula chunks (1,222
the evening before; 301 PDFs read with marker after the night described
below).

## Results

| steps | sources | cited | formula cited | match | maths quoted | stated (partly) | s/question |
|---|---|---|---|---|---|---|---|
| 0 | 100% (22) | 95% (21) | 91% (20) | 95% (21) | 77% (17) | 50% (11, +1) | 10 |
| 8 | 100% (22) | 95% (21) | 95% (21) | 95% (21) | 91% (20) | 73% (16, +1) | 16 |

*stated* is a judge's word (`--judge server-35b`, the same 35B, shown the
question, the paper's matching formula chunks and the answer: yes,
partly, no — added the same day, over the saved answers; two runs of the
judge differed by one verdict at 0 steps).

| # | question | style | 0 steps | 8 steps |
|---|---|---|---|---|
| 1 | what is the Shockley diode equation as the diode clipper WDF paper writes it | named | ✓ f $ no | ✓ f $ **yes** |
| 2 | how is the diode current written in the wave domain, in terms of the incident and reflected waves | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 3 | how does the diode clipper model solve its implicit equation with the Lambert W function | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 4 | how is the instantaneous phase of a loopback frequency modulated signal defined | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 5 | which PDE models the two-dimensional vibration of a drum membrane, and its boundary conditions | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 6 | how is the STFT computed by a recursive filter, what is the impulse response | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 7 | how are Givens rotations applied to the upper Hessenberg matrix from Arnoldi in GMRES | named | ✓ f $ no | ✓ f $ **yes** |
| 8 | how is the fractional wavelet transform defined, and the signal reconstructed from it | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 9 | the definition of the RMS voltage in the RMS compressor paper | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 10 | how is a multicomponent signal written as a sum of amplitude and phase components before synchrosqueezing | meaning | ✓ f $ no | src $ partly |
| 11 | the Hindu series for the solid content of a pyramid with a triangular base, the citighana | named | ✓ f no | ✓ f no |
| 12 | how are the entries of the kernel matrix defined in the proximal SVM | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 13 | what classification rule assigns a point to a class in the proximal SVM | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 14 | how is negentropy maximization set up for complex ICA, the augmented vectors | named | ✓ f no | ✓ f $ **yes** |
| 15 | which condition on the number of steps approximating a fifth do good equal temperaments satisfy | meaning | ✓ f $ no | ✓ f $ no |
| 16 | the equation of motion of the geometrically exact nonlinear string, and its strain energy density | named | ✓ f $ partly | ✓ f $ **yes** |
| 17 | the Van der Pol oscillator as a first-order system in the harmonic balance paper | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 18 | the friction characteristic of the bowed string as a function of relative velocity | meaning | ✓ f no | ✓ f no |
| 19 | the squared exponential covariance function for Gaussian process regression | named | ✓ f $ **yes** | ✓ f $ no |
| 20 | the transition probability of the Markov chain in the musical dice game, and its entropy | named | ✓ no | ✓ f $ no |
| 21 | the kernel of the fractional Fourier transform | named | src f $ no | ✓ f $ **yes** |
| 22 | how continuous-time convolution reduces aliasing in waveshaping, the interpolated input | meaning | ✓ no | ✓ f $ **yes** |

✓ a cited passage of the expected paper matches; src: the paper was
among the sources but not cited so; f: a formula chunk cited; $: the
answer quotes maths; **yes**/partly/no: the judge. The full answers: `C:\prax-data\logs\ask-equations.json`
(local; the questions name documents by id, so the set is bound to this
store).

## Reading

- **Retrieval finds the paper every time**, named or described (22 of
  22 at both settings), and the formula chunk is what gets cited (20 and
  21 of 22): a display equation with its LaTeX and a reading in words is
  a passage the fusion ranks well for a question about it. The
  "meaning" questions — in other words than the paper's — do as well as
  the named ones.
- **The regex numbers saturate; the judge does not.** The automatic
  scores are nearly the same with and without steps, and they flatter
  the no-steps run: the judge finds the equation *stated* in 11 of 22
  one-shot answers and 16 of 22 surfed ones. Question 1 at 0 steps cites the formula chunk that
  matches (`I_s`) and still begins "the provided passages do not
  explicitly state the Shockley diode equation in its standard form";
  with 8 steps the surfer opened the paper, read on to its equations
  and answered with both forms it gives, `$$i = I_s(e^{v/V_T} - 1)$$`
  and the generalised one with the ideality factors `M` and `N`,
  citing the formula chunks. Question 21 the same: at 0 steps "the
  actual equation is missing or illegible", at 8 steps the kernel as a
  series over the eigenfunctions, equation (9) of the overview paper.
  The steps cost 6 s a question and buy the equation itself.
- **What the model quotes is the paper's.** Where an answer sets a
  display equation of its own (7 answers at 0 steps, 9 at 8), it is the
  chunk's LaTeX re-set, not a recollection: the loopback-FM phase
  `$$\theta_i(t) = \omega_c t - I\sin(\omega_m t) + \phi_c$$` with the
  carrier phase the paper carries, the Lambert W coefficients `A = a +
  2RI_s`, `C = -1/(2V_T)` as the diode clipper paper defines them.
- **The misses are the model's, not retrieval's.** Question 10 at 8
  steps had the paper among its sources, surfed seven steps and settled
  for a single-mode form from another paper; question 21 at 0 steps
  had two passages naming the kernel and none defining it, and said so.
  Both are the honest failure — "the passages do not define it" — not
  an invented equation. The judge's "no"s are of the same kind: the
  citighana answer names the series and Narayana's formula without
  writing it (11), the Givens answer describes the rotations' effect
  without the rotation (7 at 0 steps), the covariance answer names the
  signal variance and the length scale without the exponential (19 at
  8 steps) — descriptions where an equation was asked for, with the
  equation cited beside them.
- **Before**: the same papers as `pymupdf4llm` read them had no formula
  chunks at all (the 09-15 note: 347 equations referred to, none
  present, the inline maths a glyph soup), so `formula cited` was 0 by
  construction and `ask` could only paraphrase prose around an
  equation it did not have. The evaluation was not run on the old
  texts before they were replaced; the 09-15 note is the "before".

## The prompt line (the same afternoon)

The judge's "no"s were descriptions with the equation cited beside
them, and the answer prompt asked for citations, never for the
equation. One sentence added to `ask.SYSTEM` — *when the question asks
for an equation, a formula or a definition and a passage shows it,
write it out as that passage has it (LaTeX between `$$` on a line of
its own) before you explain it; a passage that shows the equation
answers the question even when it does not name it* — and the same 22
questions again, same model, same judge:

| steps | sources | cited | formula cited | match | maths quoted | stated (partly) | s/question |
|---|---|---|---|---|---|---|---|
| 0, before | 100% (22) | 95% (21) | 91% (20) | 95% (21) | 77% (17) | 50% (11, +1) | 10 |
| 0, after | 100% (22) | 91% (20) | 95% (21) | 91% (20) | 91% (20) | **73% (16, +2)** | 10 |
| 8, before | 100% (22) | 95% (21) | 95% (21) | 95% (21) | 91% (20) | 73% (16, +1) | 16 |
| 8, after | 100% (22) | 95% (21) | 95% (21) | 95% (21) | 100% (22) | **86% (19, +1)** | 17 |

The one-shot answer now states the equation as often as the surf did
before, and the surf reaches 19 of 22, at no cost in time. What is left:
the citighana series (11) and the complex-ICA set-up (14) at 0 steps —
the formula chunk is cited but the answer keeps to prose; the Markov
chain's transition probability (20), where the answer says the
passages do not define it, at both settings; and the FrFT kernel (21),
where the answer writes the transform's integral and says the kernel's
own formula is cut off in the passage it has — which it is: the
formula chunk holds the integral, the kernel is the next equation.
The questions where the regex columns dipped (cited 21→20 at 0 steps)
are the answer citing the formula chunk alone and not the prose
passage the regex expected; the judge is unmoved by that.

## The neighbourhood (the same afternoon)

The FrFT kernel (21) was the case the prompt line could not reach: the
passage held equation (1), the transform as an integral with a kernel,
and the kernel is (2), the next formula chunk; the surfer read on
blind and did not find it. So a formula passage now names the
equations around it by number — `equations nearby: (1) Defines the
fractional Fourier transform… ‹this one›; (2) The kernel K_α of the
transform…; (3) …` (two before, three after, the reading's first
sentence for each; `store.equations_near`) — and the surfer has `read:
[n] (2)`, the equation by the number the paper calls it
(`store.formula_by_number`). The same 22 questions, model and judge:

| steps | sources | cited | formula cited | match | maths quoted | stated (partly) | s/question |
|---|---|---|---|---|---|---|---|
| 0, prompt line | 100% (22) | 91% (20) | 95% (21) | 91% (20) | 91% (20) | 73% (16, +2) | 10 |
| 0, + neighbourhood | 100% (22) | 95% (21) | 95% (21) | 95% (21) | 95% (21) | 68% (15, +2) | 9 |
| 8, prompt line | 100% (22) | 95% (21) | 95% (21) | 95% (21) | 100% (22) | 86% (19, +1) | 17 |
| 8, + neighbourhood | 100% (22) | 91% (20) | 100% (22) | 91% (20) | 100% (22) | 77% (17, +2) | 16 |

What it was built for, it does: the kernel question went from four
steps and "the formula is cut off" to two steps and the kernel written
out exactly, `K_α(t,u) = √((1 − j cot α)/2π) e^{…}` with its δ cases;
the Markov chain's transition probability (20) went from "the passages
do not define it" to stated. The aggregate did not move: three
questions went the other way (7 yes → partly, 15 and 22 yes → no), and
reading them, that is the run's noise, not the line's doing — 22 asks
of the same question at the same setting differ by one or two verdicts
between runs (the 0-step row, which cannot read the neighbourhood, lost
one too), and the "no"s are answers that state a *different* equation
of the right paper (15: the enharmonicity condition (6) instead of the
GCD condition (23); the question admits both). One thing to watch: with
the equations named, the model answered 15 after two steps instead of
five, satisfied with the first equation it held. The set is too small
to see a one-question effect through that noise; a change here wants
either a larger set or each question asked three times.

Kept: it costs a line per formula passage and makes the next equation
a thing the model can see and open, which is right whether or not
this set can measure it.

**The baseline with its spread** (`--repeat 3`, the same afternoon: each
question asked three times per setting, the counts means over the runs
and the range the lowest and highest run):

| steps | sources | cited | formula cited | match | maths quoted | stated (partly) | s/question |
|---|---|---|---|---|---|---|---|
| 0 | 100% (22.0) | 94% (20.7) 20–21 | 95% (21.0) | 94% (20.7) 20–21 | 94% (20.7) 20–21 | 68% (15.0) 14–16, +1.7 | 8 |
| 8 | 100% (22.0) | 89% (19.7) 19–20 | 95% (21.0) 20–22 | 89% (19.7) 19–20 | 98% (21.7) 21–22 | 85% (18.7) 18–20, +0.3 | 14 |

So the spread of one ask is ±1 verdict at either setting, the single
runs above all fall inside it, and a change to `ask` from here is
measured against 15.0 and 18.7 with the three-run mean, not against
one ask.

## The larger set (2026-09-19)

Fifty-two questions instead of twenty-two: the original set (with 9813
renamed to its keeper 9272, the twin-documents heal having retired it)
and thirty more over the three books read with marker on the 17th
(Strogatz, Benson, Bishop) and twenty more papers — the Moog ladder's
tanh, the MS20's OTA transfer function, the Buchla lowpass gate, the
Lambert W identity, antiderivative antialiasing, WSOLA, Griffin–Lim's
least-squares estimate, the Krylov space, matching pursuit's residual,
the wavelet refinement relation, FastICA's negentropy, CTC's sum over
alignments, Newtonian shear stress, Zee's path integral, the Christoffel
symbols. Sixteen of the fifty-two are "meaning" questions. Every
`expect` was checked to match a formula chunk of a live expected
document before the run. Three runs per setting, the same model and
judge, the ask of the day (the prompt line, the neighbourhood, the
embed hand-out no longer holding the store's lock for seventeen seconds
a cycle — the first attempt at this run crawled at 40–70 s an ask on
that, which is how the bug was found):

| steps | sources | cited | formula cited | match | maths quoted | stated (partly) | s/question |
|---|---|---|---|---|---|---|---|
| 0 | 98% (51.0) | 90% (47.0) 46–48 | 93% (48.3) 47–49 | 90% (47.0) 46–48 | 97% (50.7) 50–51 | **62% (32.0) 31–33**, +3.0 | 10 |
| 8 | 98% (51.0) | 88% (46.0) 44–47 | 91% (47.3) 46–48 | 87% (45.3) 44–46 | 99% (51.3) 51–52 | **87% (45.0) 44–47**, +1.7 | 15 |

What the larger set shows that the small one could not:

- **The steps are worth thirteen questions of fifty-two**, six times
  the spread (±2). On the small set the difference was three or four,
  inside its noise. The surf reads on to what the first search brought
  only the neighbour of: the pitchfork normal form, the Lorenz
  equations, the Bessel integral, the Lambert W identity, the piano
  string's PDE, the FrFT kernel — 0/3 one-shot, 3/3 surfed, every run.
- **The verdicts are stable.** Forty of the fifty-two questions score
  3/3 or 0/3 at a setting: the model and the judge are consistent on
  the same question; the spread is in the dozen that sit on an edge.
- **Retrieval is not the limit.** The expected paper is among the
  sources 51 of 52 times (the one miss, the string's wave equation in
  Benson, is answered from the piano paper's string equation instead —
  the same equation — and the judge says yes). The regex columns are
  flat between settings; the judge column is not.
- **What stays wrong** is consistent too: the citighana series (11)
  and the Markov chain's transition probability (20) are 0/3 at both
  settings — the passage names the thing and the model describes it;
  the tape head's field (39) and WSOLA's synthesis (43) come back
  "partly" — the equation is long and the answer gives part of it.
  Those four are the set's hard cases, and a change to `ask` is
  measured on them first.

The baseline from here: **32.0 (31–33) of 52 one-shot, 45.0 (44–47)
with eight steps.**

### The hard cases, looked at (the same day)

Two of the four were the set's fault, not the model's. The citighana
answer (11) *had* written the equation — Nārāyaṇa's formula for the
sum of figurate numbers, which the paper calls citighana — and the
regex had handed the judge the sum-of-cubes identity from elsewhere in
the paper; the WSOLA answer (43) gave equation (8), the paper's "basic
synthesis equation", and the judge had been shown (4)–(6). Both
expectations now name the paper's own forms, and the judge sees up to
five matching chunks instead of three. The saved answers re-judged
that way: **33.0 (32–34) one-shot, 44.3 (44–45) surfed** — the
corrected baseline, and 11 and 43 at 3/3 both ways.

Of the two real ones, the Markov chain's transition probability (20)
is a surfer's miss: the passages the model held were prose of the
right paper, the formula chunk was never opened, and a prose passage
carries no line about the equations next door (only a formula passage
does). The tape head's field (39) is the model citing the passage that
holds the equation and not reproducing it.

### "In full" (the same day)

For 39, one more clause in the answer prompt: *write it out as that
passage has it — in full, every term and condition, the whole display,
not a part of it or a paraphrase.* The 52 three times, against the
corrected baseline:

| steps | cited | formula cited | match | stated (partly) | s/question |
|---|---|---|---|---|---|
| 0, before | 90% (47.0) 46–48 | 93% (48.3) | 90% (47.0) | 63% (33.0) 32–34, +3.0 | 10 |
| 0, in full | 86% (44.7) 44–46 | 90% (46.7) | 86% (44.7) | 67% (34.7) 32–38, +2.3 | 12 |
| 8, before | 88% (46.0) 44–47 | 91% (47.3) | 87% (45.3) | 85% (44.3) 44–45, +2.3 | 15 |
| 8, in full | 85% (44.3) 44–45 | 92% (47.7) | 84% (43.7) | 87% (45.0) 43–48, +1.7 | 14 |

It does what it was for — the tape head's field goes from 0/3 to 3/3
one-shot and 1/3 to 3/3 surfed, written out from the prose passage
that holds it — and the aggregate moves +1.7 and +0.7, inside the
spread. Eighteen questions changed a verdict at a setting; reading
them, the moves are the judge's: the path integral (51) went 3/3 → 0/3
surfed on two answers that set the same equation word for word, one
calling it "(19)" and the other "(unnumbered)"; the Shockley question
(1) lost a run by answering from the *other* WDF paper's Shockley
equation — the same equation, the wrong paper. Kept, for the class of
answer it fixes; the honest result is "no harm, one fix", and the
instrument's floor is a verdict or two per question, not per set.

### The neighbourhood on prose passages (the night of 2026-09-18/19, reverted)

For 20, the surfer's miss: a text chunk of a paper with equations
carried the neighbourhood line too (the two equations each way, as a
formula chunk does), so that a paragraph brought by the first search
could say "equation (1) is next door" and the surfer open it. The 52
three times, against "in full" (the run shared llama-server with the
nightly extraction pass for its first hour, hence the seconds):

| steps | cited | formula cited | match | stated (partly) | s/question |
|---|---|---|---|---|---|
| 0, in full | 86% (44.7) 44–46 | 90% (46.7) | 86% (44.7) | 67% (34.7) 32–38, +2.3 | 12 |
| 0, prose too | 86% (44.7) 43–46 | 88% (46.0) | 86% (44.7) | 65% (34.0), +2.3 | 20 |
| 8, in full | 85% (44.3) 44–45 | 92% (47.7) | 84% (43.7) | 87% (45.0) 43–48, +1.7 | 14 |
| 8, prose too | 88% (45.7) 45–46 | 93% (48.3) | 87% (45.3) | 81% (42.0) 39–45, +1.0 | 29 |

It fixed its target — the Markov chain went 1/3 → 3/3 surfed, and 18,
21 and 48 gained a run or two — and cost more than it fixed: −3.0
surfed, the ranges barely touching (43–48 against 39–45), sixteen runs
lost against seven gained; one-shot unmoved. The answers say why. A
prose passage is often a table of contents or a preface, and the
equations next to *that* are whatever the chapter opens with: the
Lorenz question (24, 3/3 → 1/3) has the model report that "the text
provided only includes the TOC and nearby unrelated equations (damped
harmonic oscillator, heat equation)" — it opened the neighbours the
line offered, found them unrelated, and never made the second search
that had found the formula chunk before. The tape head's field (39,
3/3 → 1/3) spent seven steps the same way and ended with the passage
that "cuts off before the formula" instead of reading on. On a formula
passage the line names the equation's own siblings; on a prose passage
it names whatever is near, and the surfer takes an offer over a
search. Reverted (`d324541`); the formula-passage line stays. If the
case of 20 is worth another try, the honest variant is narrower: only
the equations the prose itself refers to by number.

## How the material came to be (the night of 2026-09-16/17)

The mathematical part of the library through marker in one evening:
`prax reread --extractor marker --maths 6 --mime application/pdf
--text-source pymupdf4llm` (282 PDFs at ≥ 6 numbered-equation
references per 10,000 characters), the card swapped to marker with
`prax up --stop llama-server; prax up --start marker`, a detached
watcher to swap it back, a second to run extraction and this evaluation
after. Marker read 280 of them (2 s a page on the card; two 530-page
books over marker's page cap of the day were refused — since then a
long document goes to its server a window of pages at a time); the door's follow-up
edge asked for the formula readings itself, 1 s an equation on the 35B
once the card was back; the stale-extraction rule sent the re-read
papers to the extract step first.

The evening ran twice, because its first run found two holes in the
work protocol that only a long queue behind a paused server could show:
a worker's "not yet" for a reading whose server is down left the item
where it was, so the follow-ups of the first papers read filled every
batch; and the hand-out took its requested readings from the status
view's newest-fifty window, so the older requests behind it were never
seen at all. Both fixed that night (a "not yet" defers the item ten
minutes; the hand-out reads the whole queue oldest first), and two
more the morning showed (a connection lost under a request is the
server's, not the document's; a restarted worker waits for the next
night's hour rather than running the nightly pass at once). Timings and
counts: `docs/PLAN.md`, "After the mathematics".

## What follows

- The judge is the measure now (`--judge`, `--rejudge` over saved
  answers, `--repeat N` for the mean and the spread); the regex columns
  are its floor. The set is 52 questions since the 19th; its spread is
  ±2, and the four hard cases above are where a change to `ask` shows
  first.
- `prax resolve --apply --twins` after the extraction pass over the 280
  re-read papers: Lambert W function was five entity rows before it.
- The set is bound to this store's ids; a portable one would name
  papers by title as the retrieval sets do.
