# marker against pymupdf4llm on equation-heavy papers, 2026-09-15

Question: does a parser that transcribes formulas as LaTeX
([marker](https://github.com/datalab-to/marker), datalab-to) read the
library's mathematical papers better than the `pymupdf4llm` chain that
parsed them, and what does it cost to run? Prompted by a reader's
suggestion that "LaTeX might be easier for an LLM to work with than
pymupdf4llm output for anything equation-heavy — I don't have any
quantitative way to substantiate that though".

Setup: marker-pdf 2.0.0 in a venv of its own (1.3 GB with torch 2.14,
transformers 5.17, surya-ocr 0.22; 3½ minutes to install), `--mode fast`
through the llama.cpp inference backend on the CPU, against the text the
store already holds for the same documents. The sample is the eight PDFs
of this library whose prose refers to the most numbered equations, found
by counting `(n)` references in the current text.

## What the library holds today

| | the eight documents |
|---|---|
| text | 427,036 characters |
| numbered equations the prose refers to | 347 |
| characters of maths notation (`$`) | 9 |
| replacement characters (glyphs no font named) | 391 |

Three hundred and forty-seven equations referred to, none of them
present. A display equation in a two-column paper is usually a vector
drawing: `pymupdf4llm` drops it, and prax's figure finder does not
collect vector drawings either, so it is not even a figure a vision
model could read. What does survive of the inline mathematics arrives
like this, from *ADAA for Stateful Systems*:

    **Figure 2.** Trajectory of � 21<sup>_e−jω_+</sup><sup><u>1</u></sup>
    2<sup>_e−_2</sup><sup>_jω_�</sup><sup>_−_1compared to the unit circle</sup>

## What marker reads instead

Two papers converted in full (19 pages), both extraction paths compared
by the same counter:

| | in the library (pymupdf4llm) | marker |
|---|---|---|
| characters | 73,903 | 88,850 |
| inline maths (`$…$`) | 0 | 28 |
| display maths (`$$…$$`) | 0 | 100 |
| LaTeX commands (`\frac`, `\sum`, …) | 0 | 354 |
| replacement characters | 20 | 0 |
| headings | 33 | 32 |
| table rows | 8 | 48 |

The equations come back as the paper's own mathematics — from the diode
clipper paper, the Lambert W model the whole paper exists for:

    $$i = I_s \left( e^{v/V_T} - 1 \right), \quad (1)$$
    $$\frac{a-b}{2R} = I_s \left( e^{\frac{a+b}{2V_T}} - 1 \right). \quad (4)$$
    $$x = \frac{1}{C} \mathcal{W}\left(\frac{CD}{B} e^{AC/B}\right) - \frac{A}{B}. \quad (7)$$

Tables improve too (8 rows against 48): the fast mode still runs layout
detection, and a wave-digital paper's port tables come out as Markdown
rather than as run-together lines.

## What it costs

| | |
|---|---|
| install | 3½ minutes, 1.3 GB of venv, 136 MB of layout weights, plus the Surya GGUF on first use |
| first document | 337 s for 11 pages, including the model download and the server start |
| steady state, CPU | 268 s for 8 pages — **33 s a page**, single process, images off |
| GPU | not measured: see below |

At 33 s a page on the CPU, the 9,447 PDFs of this library (call it
250,000 pages) are out of the question — months. It is a tool to point
at the papers whose mathematics matters, which is how prax treats
`docling` and the vision passes: an explicit extractor, asked for per
document.

## Running it on this machine

- **The backend is chosen automatically and wants Docker.** With CUDA
  visible, surya 0.22 picks vLLM in Docker and fails with `docker binary
  not found`; `SURYA_INFERENCE_BACKEND=llamacpp` and `LLAMA_CPP_BINARY`
  pointing at a `llama-server` binary is the way on a Windows host
  without Docker. `LLAMA_CPP_NGL` (99 by default) decides whether its
  model goes on the card.
- **Memory is the constraint, not the GPU.** With llama-server holding
  the 35B (about 22 GB of commit) and the worker running, this 31 GB
  machine had 8 GB of RAM and 13 GB of commit free; marker's default two
  worker processes plus its own inference server exhausted that and the
  run was killed. Single-process with `--disable_multiprocessing` and
  `--disable_image_extraction` fits beside the rest.
- **It leaves its server behind.** `Failed to stop llamacpp (pid …)` on
  Windows: check for a stray `llama-server` after a batch.
- **Figures are extracted again.** marker writes every figure as a JPEG
  beside the Markdown; prax already keeps figures by hash out of the
  original, so `--disable_image_extraction` avoids the duplicate work.
- **Licence.** The code is Apache 2.0; the model weights are a modified
  AI Pubs Open RAIL-M — free for research, personal use and small
  companies, paid above their threshold. Fine for a personal library,
  worth knowing before it goes anywhere commercial. As an optional
  extra (`pip install marker-pdf`) rather than a dependency, prax stays
  MIT.

## What is not measured yet

- **The GPU number.** Measured the next day, below: 2 s a page through
  marker's server with the card free.
- **Whether the answers improve.** The honest test is downstream: the
  same equation question asked of the library before and after a
  document is re-read this way — does `ask` cite the equation, does the
  extraction produce better claims. That belongs with the ask eval
  (`docs/ask.md`), not with a character count.
- **Regressions.** marker is a different reading, not a strictly better
  one: 20% more characters is partly real text and partly repetition
  from OCR of figure captions, and a re-read replaces a text artifact
  that other passes have already been over. A per-document extractor
  keeps that reversible (`parse_history` holds the earlier text under
  its own hash).

## Conclusion so far

For equation-heavy papers the difference is not a matter of taste: the
mathematics is absent from the library's current text and present in
marker's. The suggestion is right, and on this evidence the way to use
it is as a named extractor over the documents whose formulas matter —
not as a new default chain.

## 2026-09-16: the GPU, marker's server, and the extractor

With `prax up` able to pause llama-server (`prax up --stop llama-server`),
the card was free for the measurement. marker's recognition model goes
through llama.cpp's server on the GPU (`LLAMA_CPP_NGL=99`, about 5 GB of
the card); its layout model stays on the CPU (the venv's torch is the
CPU build, and layout is the smaller part). A different sample from the
15th — eight paper-sized PDFs picked by the density of numbered-equation
references in their prose, 125 pages, two of them the same as before:

| | pages | library chars | refs | marker chars | `$$` | `$…$` | tables |
|---|---|---|---|---|---|---|---|
| 9549 Loopback FM with a time-varying delay | 8 | 35,225 | 98 | 43,294 | 50 | 17 | 38 |
| 9813 Diode clipper model for WDFs | 11 | 38,678 | 82 | 45,151 | 50 | 14 | 27 |
| 2309 Physical modeling of drums | 4 | 18,309 | 66 | 17,489 | 27 | 65 | 0 |
| 266 Recursive time-frequency reassignment | 6 | 22,540 | 60 | 27,422 | 27 | 38 | 6 |
| 2420 Notes on GMRES organization | 10 | 18,815 | 53 | 21,991 | 16 | 30 | 13 |
| 90 A new fractional wavelet transform | 18 | 39,838 | 84 | 69,553 | 93 | 38 | 114 |
| 9812 Attack and release in RMS compressors | 35 | 63,285 | 151 | 66,870 | 107 | 68 | 56 |
| 84 Adaptive STFT synchrosqueezing | 33 | 36,061 | 78 | 94,621 | 186 | 575 | 4 |
| | 125 | 272,751 | 672 | 386,391 | 556 | 845 | 258 |

The library's text for these eight holds 18 `$` characters in total; marker's
holds 556 display equations and 845 inline ones.

**Speed.**

| path | | per page |
|---|---|---|
| batch, 8 processes, GPU recognition (`marker pdf/ --mode fast`) | 125 pages in 485 s wall, model start included | 3.9 s |
| marker's own server (`marker_server`), one request at a time, steady | 8–35 page papers in 15–70 s | **1.6–2.6 s** |
| the first request to a fresh server | starts its llama-server on the way | +30 s |
| the server itself | models loaded, answering `/` | 10 s |
| CPU, single process (the 15th) | | 33 s |

Fifteen times the CPU rate. The whole library (about 250,000 pages) would
still be six days of the card, so it stays a tool to point at documents.

**How it runs in prax now.** marker's server is a role of `prax up`
(`run: marker: {venv: …, on_demand: true}` — declared, started by
`prax up --start marker`), and `marker` is an explicit extractor
(`prax reread --extractor marker --ids …`) that is a client of it: the
PDF goes up, Markdown comes back, marker's page rules become the
chunker's page markers, its image references (files it did not write
here) are dropped and prax's own figure references are placed by hash
as for every PDF. The stamp is `marker/2.0.0` (the version read from
the role's venv; `+balanced` when the vision model lays out too). A
`--stop marker` ends its llama-server with it: roles are ended as a
tree now (a job object on Windows, the session elsewhere), which the
15th's "leaves its server behind" needed.

The eight papers re-read this way through the door, in one pass of the
worker with the server on the card: all eight upgraded, **537 formula
chunks** between them (50, 49, 27, 27, 16, 92, 101, 175 — a display
equation short of a relation, such as `$$\rightarrow K$$`, stays in its
paragraph), the tables as tables, 52 figures placed. The document page
shows each equation with its number; the paragraph after it says
"where i is the current…" as the paper does.

**Quirks seen.** A two-part definition set side by side in the paper —
`K → w: a = v + iR, b = v − iR (2)` — came back as a one-row table with
the maths inline in its cells; marker read the layout as a table, and
prax keeps what marker said. The characters are 40% more than the
library's, partly real (the equations, the tables) and partly OCR of
figure captions repeated as text. Both are reasons for an explicit
extractor rather than a default one.

**Still not measured.** Whether answers improve — the same equation
question before and after — which belongs with the ask evaluation, and
now has its material: eight papers whose formulas are chunks.
