# Does the library want a mathematics module? 2026-09-17

Question (`docs/PLAN.md`, since 2026-09-15): once papers are read with
their mathematics — equations as `formula` chunks, each with a reading in
words — does the extraction produce shapes the ontology has no home for,
the way the review queue asked for `written_at` and `mentions` in v6? Or
is the earlier "193 mathematical-looking items" an artefact of texts that
had no equations in them?

## What was done

Twenty-one equation-heavy papers (the eight of the marker evaluation and
thirteen more, picked by the density of numbered-equation references
among paper-sized PDFs: series in Hindu mathematics, eigenvalues, proximal
SVMs, fractional wavelets and the fractional Fourier transform, complex
ICA, equal temperament, nonlinear strings, harmonic balance, modal bowed
strings, Gaussian processes, Markov dice games, waveshaping aliasing,
time-frequency reassignment, GMRES, diode clippers, loopback FM, RMS
compressors, synchrosqueezing) were read with marker (1,222 display
equations), every equation got a reading from the 35B (1,212 readings,
about a second each), and then the extract step read the new texts —
which needed the rule that a text replaced after its extraction is
extracted again (`stale-extractions`, this same night). All 21 are
extracted against `core2+research7`.

## What came out

778 live edges from the 21, by shape (the top of the list):

| edges | shape |
|---|---|
| 320 | `paper cites paper` |
| 117 | `paper about concept` — ambiguity function, Port-Hamiltonian system, adjacency matrix, arithmetic progression, bifurcation, Gaussian kernel, 12-tone equal temperament… |
| 35 + 23 | `paper authored_by author` / `person` |
| 32 | `paper about method` |
| 27 | `paper uses method` — FastICA, Givens transformation matrices, Markov chain, empirical mode decomposition, the reassignment method… |
| 25 | `paper proposes method` — invariant energy quadratisation, complex maximization of non-Gaussianity, the construction of orthogonal wavelets for the FRWT… |
| 20 | `paper proposes claim` — "the valid n-tone divisions satisfying surjective note-name mapping are 5, 7, 12, 19, 26, 31, 43…", "CMN cost function converges to the principal component of the source distribution under noncircularity", "digital realizations of the FRFT lack properties of the continuous FRFT" |
| 9 + 7 + 6 | `method extends method` / `concept`, `concept extends concept` — the fractional Fourier transform extends the Fourier transform, the FRWT the wavelet transform, adaptive FSST the synchrosqueezing transform |
| 6 + 3 | `method contrasts method` (JADE against FOBI, proximal SVM against SVM), `paper contrasts method` |
| 5 + 3 | `method implements concept` / `method` |
| 3 | `paper defines concept` — negentropy, multiresolution analysis, the enharmonicity condition |

The mathematics landed as **concepts, methods, claims and the relations
among them** — `extends`, `contrasts`, `implements`, `defines` — and the
summaries say what the papers do ("reorganizing GMRES … Givens rotations
for orthogonalization"; "an explicit wave-domain model for diode clippers
using the Lambert W function … correction terms for reverse-biased
diodes"). Nothing was left unsaid for want of a type.

The review queue got 48 items from the 21 papers, 25 still open. The
mathematical-looking ones:

| open | shape | what it is |
|---|---|---|
| 8 | `paper located_in place` | the institution's city, read off the first page — an affiliation, not mathematics |
| 5 | `cites` a method as if it were a paper ("finite difference method", "Sturm-Liouville transformation") | a `uses`/`extends` written with the wrong verb |
| 1 | `GMRES iterative method implements solving systems of linear equations` | a method and the problem it solves |
| 1 | `Givens transformation matrices used_in Arnoldi process` | a method inside a method |

Across the whole queue (4,532 open items after tonight), 374 carry a
mathematical word, and the top shapes are `paper located_in place` (56),
`located_in` untyped (22), `about`/`cites`/`uses` untyped (55, 32, 18)
and `paper located_in organization` (18) — none of them mathematics
either. No model invented a mathematical entity type; not one item asks
for an *equation* as a thing in the graph.

## Conclusion

**No mathematics module now.** The extraction's ontology of concepts,
methods and claims with `extends`, `contrasts`, `implements` and
`defines` carries what these papers say; the equations themselves are
best where they are — `formula` chunks with readings in words, which is
what makes "Shockley diode equation" find the formula and lets `ask`
quote it. A named equation the prose treats as a thing ("Shockley's
diode equation", "the wave equation") already arrives as a `concept`
when the model thinks it matters, and `defines` says which paper
introduced it.

Two things the evidence does ask for, neither a module:

1. **A typing rule**: `paper located_in place` (56 open, 8 of them from
   these papers) and `paper located_in organization` (18) are the
   affiliation on the first page; the rule that maps a document's
   `affiliated_with organization` to `written_at` should take
   `located_in organization` the same way and drop `located_in place`
   from a document (a paper is not in a city).
2. **Two relations to watch for in research v8**, when the queue shows
   them in numbers: `solves` (a method and the class of problem: GMRES
   and large sparse linear systems) and `models` (a method or equation
   and the system it stands for: the Lambert W model and the diode
   clipper). One instance each tonight; the v6 changes were made on
   hundreds.

The "193 mathematical items" of 2026-09-15 were what they looked like:
bibliographic misfits carrying a maths-flavoured word. With equations in
the input the queue did not grow a mathematical shape; the graph grew
mathematical concepts.
