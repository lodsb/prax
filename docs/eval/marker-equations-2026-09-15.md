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

- **The GPU number.** marker's card-bound path needs either Docker with
  the NVIDIA toolkit or `LLAMA_CPP_NGL > 0`, and the 4090 here is held
  by llama-server (20.3 of 24 GB) for the ask and vision steps. The
  measurement wants the server stopped for a few minutes.
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
