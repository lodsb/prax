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

| steps | sources | cited | formula cited | match | maths quoted | s/question |
|---|---|---|---|---|---|---|
| 0 | 100% (22) | 95% (21) | 91% (20) | 95% (21) | 77% (17) | 10 |
| 8 | 100% (22) | 95% (21) | 95% (21) | 95% (21) | 91% (20) | 16 |

| # | question | style | 0 steps | 8 steps |
|---|---|---|---|---|
| 1 | what is the Shockley diode equation as the diode clipper WDF paper writes it | named | ✓ f $ | ✓ f $ |
| 2 | how is the diode current written in the wave domain, in terms of the incident and reflected waves | meaning | ✓ f $ | ✓ f $ |
| 3 | how does the diode clipper model solve its implicit equation with the Lambert W function | named | ✓ f $ | ✓ f $ |
| 4 | how is the instantaneous phase of a loopback frequency modulated signal defined | named | ✓ f $ | ✓ f $ |
| 5 | which PDE models the two-dimensional vibration of a drum membrane, and its boundary conditions | named | ✓ f $ | ✓ f $ |
| 6 | how is the STFT computed by a recursive filter, what is the impulse response | meaning | ✓ f $ | ✓ f $ |
| 7 | how are Givens rotations applied to the upper Hessenberg matrix from Arnoldi in GMRES | named | ✓ f $ | ✓ f $ |
| 8 | how is the fractional wavelet transform defined, and the signal reconstructed from it | named | ✓ f $ | ✓ f $ |
| 9 | the definition of the RMS voltage in the RMS compressor paper | named | ✓ f $ | ✓ f $ |
| 10 | how is a multicomponent signal written as a sum of amplitude and phase components before synchrosqueezing | meaning | ✓ f $ | src $ |
| 11 | the Hindu series for the solid content of a pyramid with a triangular base, the citighana | named | ✓ f | ✓ f |
| 12 | how are the entries of the kernel matrix defined in the proximal SVM | named | ✓ f $ | ✓ f $ |
| 13 | what classification rule assigns a point to a class in the proximal SVM | meaning | ✓ f $ | ✓ f $ |
| 14 | how is negentropy maximization set up for complex ICA, the augmented vectors | named | ✓ f | ✓ f $ |
| 15 | which condition on the number of steps approximating a fifth do good equal temperaments satisfy | meaning | ✓ f $ | ✓ f $ |
| 16 | the equation of motion of the geometrically exact nonlinear string, and its strain energy density | named | ✓ f $ | ✓ f $ |
| 17 | the Van der Pol oscillator as a first-order system in the harmonic balance paper | named | ✓ f $ | ✓ f $ |
| 18 | the friction characteristic of the bowed string as a function of relative velocity | meaning | ✓ f | ✓ f |
| 19 | the squared exponential covariance function for Gaussian process regression | named | ✓ f $ | ✓ f $ |
| 20 | the transition probability of the Markov chain in the musical dice game, and its entropy | named | ✓ | ✓ f $ |
| 21 | the kernel of the fractional Fourier transform | named | src f $ | ✓ f $ |
| 22 | how continuous-time convolution reduces aliasing in waveshaping, the interpolated input | meaning | ✓ | ✓ f $ |

✓ a cited passage of the expected paper matches; src: the paper was
among the sources but not cited so; f: a formula chunk cited; $: the
answer quotes maths. The full answers: `C:\prax-data\logs\ask-equations.json`
(local; the questions name documents by id, so the set is bound to this
store).

## Reading

- **Retrieval finds the paper every time**, named or described (22 of
  22 at both settings), and the formula chunk is what gets cited (20 and
  21 of 22): a display equation with its LaTeX and a reading in words is
  a passage the fusion ranks well for a question about it. The
  "meaning" questions — in other words than the paper's — do as well as
  the named ones.
- **The numbers saturate; the answers differ.** The automatic scores
  are nearly the same with and without steps, and they flatter the
  no-steps run. Question 1 at 0 steps cites the formula chunk that
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
  an invented equation.
- **Before**: the same papers as `pymupdf4llm` read them had no formula
  chunks at all (the 09-15 note: 347 equations referred to, none
  present, the inline maths a glyph soup), so `formula cited` was 0 by
  construction and `ask` could only paraphrase prose around an
  equation it did not have. The evaluation was not run on the old
  texts before they were replaced; the 09-15 note is the "before".

## How the material came to be (the night of 2026-09-16/17)

The mathematical part of the library through marker in one evening:
`prax reread --extractor marker --maths 6 --mime application/pdf
--text-source pymupdf4llm` (282 PDFs at ≥ 6 numbered-equation
references per 10,000 characters), the card swapped to marker with
`prax up --stop llama-server; prax up --start marker`, a detached
watcher to swap it back, a second to run extraction and this evaluation
after. Marker read 280 of them (2 s a page on the card; two 530-page
books over the layout cap were refused, rightly); the door's follow-up
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

- The evaluation needs a judge for what the scores cannot see — did the
  answer *state* the equation the question asked for — before the
  no-steps and the steps settings can be compared on it honestly; the
  regex `match` is a floor, not the measure.
- `prax resolve --apply --twins` after the extraction pass over the 280
  re-read papers: Lambert W function was five entity rows before it.
- The set is bound to this store's ids; a portable one would name
  papers by title as the retrieval sets do.
