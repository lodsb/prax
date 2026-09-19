# How-to

Practical steps for developing, running, testing, and deploying prax. What
is described here works today unless a section is marked *planned*.

## 1. Development environment

The repo expects a virtualenv at `.venv` in the project root. `.mcp.json`
points at it.

Windows (PowerShell). Bare `python` is usually the Microsoft Store stub;
use the `py` launcher:

    py -3.13 -m venv .venv
    .venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    pip install -e ".[dev]"

Linux, macOS, Raspberry Pi:

    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install --upgrade pip
    pip install -e ".[dev]"

Extras, install only where they run (see `rationale.md` R8):

| Extra | Contents | Where |
|---|---|---|
| `serve` | the core plus `embed` | the door on the board: `pip install "prax[serve]"` and nothing else |
| `work` | `embed` plus `ingest` | the worker on the machine with the models |
| `embed` | usearch, onnxruntime, tokenizers, numpy | vectors for hybrid search (Windows GPU: onnxruntime-directml instead, never both) |
| `ingest` | pymupdf4llm, trafilatura, magika | parsing PDFs and pages, code detection |
| `docling` | docling (about 3 GB with PyTorch) | optional; only for `--extractor docling` |
| `dev` | pytest, ruff | every dev checkout |

The core (what `pip install prax` brings) is the door and the MCP proxy:
fastapi, uvicorn, pydantic, python-multipart, pyyaml, httpx, anthropic
(the Claude-kind steps), mcp. Fresh venvs on 2026-09-12, from the
declared extras alone:

| install | packages | on disk | the big ones |
|---|---|---|---|
| `prax[serve]` (the board) | 54 | 228 MB | onnxruntime 46, numpy 53, then cryptography (mcp), hf_xet (tokenizers) |
| `prax[work,dev]` (the desktop) | 92 | 648 MB | OpenCV 118 (RapidOCR's), pymupdf 109, onnxruntime 46, rapidocr 33, numpy 53 |

A venv that has lived through removals carries their leftovers (the
desktop's was 771 MB and 168 packages before a rebuild); `pip install`
into a fresh one is the honest number. Measured running: the door's
working set is about 70 MB with the vector index mapped and the embedder
loaded, its private memory 0.8 GB (the ONNX runtime and the usearch
views; under the 1 GB of invariant 7); the worker sits at 0.8 GB between
passes and 1.7 GB after an embedding batch.

The MCP server uses the official `mcp` package (2.x); check after
upgrades:

    python -c "import importlib.metadata as m; print(m.version('mcp'))"

## 2. Tests and lint

    python -m pytest
    ruff check src tests

Tests never touch `data/`. Every fixture uses `tmp_path` and points
`PRAX_DATA_DIR` at it before importing the API or MCP modules.

The UI's JavaScript is checked when `node` is on the path: both scripts
must parse, and `tests/ui/lib.test.js` (node's own test runner, no npm)
covers the pure helpers in `src/prax/ui/lib.js`. Without node those
tests are skipped.

What a stranger's machine would find, `.github/workflows/ci.yml` runs
on every push: the suite on Linux (3.12, 3.13) and Windows, `ruff`, and
the README's quick start in a fresh venv — `scripts/smoke.sh`, which
installs `prax[serve,work,dev]` into a throwaway venv, starts a door on
an empty store, adds a note, searches for it (keywords: a fresh store
has no vectors, so the embedder is never loaded and nothing is fetched
but the packages), runs `prax status`, `show`, `models` and `doctor`,
and stops the door. Run it yourself after
touching the install path (`bash scripts/smoke.sh`, three minutes,
mostly pip; on Windows from Git Bash). Two bugs it found on its first
run, for the record: a closed pipe (`prax show 12 | head`) was reported
as "no door", and non-Latin output crashed under a Windows pipe's
codepage — the command now writes UTF-8 whatever the console.

## 3. Data directory

Layout, all under one directory:

    data/
      prax.db            SQLite (WAL); the canonical store
      prax.db-wal, -shm  WAL sidecar files; copy them with the db
      archive/<xx>/<sha256>   originals and parsed-text artifacts
      inbox/             drop folder (section 3l); inbox/failed/ what the store refused

The location defaults to `<repo>/data` and is overridden with the
`PRAX_DATA_DIR` environment variable. On the Pi point it at the SSD.

### Schema migrations

The schema lives in `src/prax/migrations/NNNN_name.sql`. `store.init_db`
(called by the API, the MCP server and every script) applies the files
whose number is above the database's `PRAGMA user_version`, each in its own
transaction, and stamps the version. To change the schema, add the next
numbered file; never edit one that has been applied. A database created
before migrations existed (version 0) is upgraded in place.

### Ontology

`ontology/` holds the entity and relation types the graph accepts, one
module per domain (`core.yaml`, `research.yaml`, `studio.yaml`,
`craft.yaml`, `kitchen.yaml`, `workshop.yaml`; format in
`src/prax/ontology.py`), each with a
`version`. `store.link` rejects anything else. Add types and bump the
version; edges keep the version they were written under. `PRAX_ONTOLOGY`
points at a different file (tests use it). After a bump, replay the review
queue (`prax maintain --only review`, section 3f) before extracting anything
new; the bump also re-selects every document for extraction. The reasoning
behind v2 is in `docs/ontology-v2.md`; the studio module (gear, manuals,
datasheets, magazine articles) in `docs/ontology-studio.md`. A document
is extracted against the modules of its domain set (section 3e, "A
document's domains"), so a new module re-selects only documents without
a set.

## 3a. Importing the Zotero library

The importer never opens the live `zotero.sqlite`; it copies the file
into a work directory and opens the copy read-only. The plan is made on
the machine with the library, each document travels to the door as one
request (the record, the file, Zotero's cached text), and the door
writes it through `prax.store` (`POST /import/zotero/item`) — so the
library may live on another machine than the store. Details of the
mapping: `docs/sources.md` §1.

    prax import zotero /path/to/Zotero --dry-run     # the census; nothing is sent
    prax import zotero /path/to/Zotero -n 500        # a trial run, then
    prax import zotero /path/to/Zotero               # the whole library

Re-runs skip what the door already has and refresh a record that
changed, so an interrupted run is simply started again. The worker
parses what came without cached text (`prax work --scope all`).

The test fixture in `tests/fixtures/zotero/` is regenerated with
`scripts/make_zotero_fixture.py` (the exact command is in its docstring).

## 3b. Parse queue

Extractors live in `prax.parsers` (see its docstring for the table) and are
picked by MIME type in registry order, falling back to the next one when
one raises. The queue records every attempt in `meta.parse_history` and the
winner in `meta.text_source` as `<name>/<version>`, so any pass can be
redone later with `--upgrade <prefix>` — or left to the nightly pass,
which finds the documents whose extractor prax has revised since (3l¾).
An upgrade keeps the old text when the new one is suspiciously short
(login walls, scans without OCR) unless `--force` is given; every
history entry carries the hash of the text it produced, so the earlier
artifact stays in the archive. Runs on the desktop, never on the serving
host.

    # documents never indexed (registered by an importer): the backlog pass
    prax work --scope all --steps parse
    # re-extract everything Zotero's cache produced, HTML first: a reading
    # request on each, drained by the worker (3l¾)
    prax reread --extractor trafilatura --text-source zotero-ft-cache --mime text/html
    # scans: OCR is explicit and bounded (PRAX_OCR_MAX_PAGES, default 60)
    prax reread --extractor pymupdf4llm-ocr --unreadable
    # scans in another script: the recognizer is a setting (parse.ocr_language:
    # ch reads Chinese and English; en, latin, arabic, cyrillic, devanagari,
    # japan, korean, el, th...) and part of the stamp, so a book read with the
    # wrong one has not been read with the right one; the mode names it for
    # the request, --title picks the books out
    prax reread --extractor pymupdf4llm-ocr --mode arabic --title "In Arabic"

For a script the OCR font cannot write back into the page (Arabic,
Devanagari, Tamil, Telugu, Thai, Georgian — pymupdf4llm writes what it
recognized with Droid Sans Fallback, which has Latin, Greek, Cyrillic and
CJK), prax runs the recognizer itself and writes the lines as text in
reading order (right to left for Arabic), page markers included; no
layout analysis, which a scanned book rarely has to give.

    # Docling on a hand-picked set
    prax reread --extractor docling --ids 12 34

**Pages OCR cannot read** — handwriting, scores, photographed notebooks —
go through the vision model page by page: `vision-pages` keeps a page
that has a text layer and renders every other page (150 dpi,
`parse.vision_dpi`) for the `vision` step's model with a transcription
prompt (verbatim text, `(handwritten)` marked, a figure or a score as one
`[Figure: …]` line, `[illegible]` rather than a guess). Page markers as
the OCR extractor writes them, so chunks keep their pages. The stamp
carries the model (`vision-pages/1.28+<model>`); explicit only, and
bounded (`parse.vision_max_pages`, default 200), since a page takes
4–10 s locally (2–3 s to read the picture, the rest writing; the first
page after the server starts took two minutes once) and a cent or two
with Sonnet.

    prax reread --extractor vision-pages --ids 8605
    # every page, for printed pages with notes in the margin
    prax reread --extractor vision-pages --mode all --ids 8605

Or from the document's page in the web UI: "read again…" asks for the
extractor (and the pages) and a running worker (`scripts/work.py
--watch`) does it next — requests go out before the pending captures —
with the outcome shown on the page and the queue on Jobs. A worker
refuses a reading whose model would cost money (the vision step set to
Claude) and says so; those stay a command you run yourself.

**Figures.** The pictures that belong to a document's content — a
photograph with its caption, a plot, a schematic, a panel — are found
at parse time (`prax.parsers.figures`) and written into the Markdown
where they sit, as `![caption](figure:<sha256 of the image bytes>)`:
for a captured page, the images inside `<figure>` (with their
`<figcaption>`), with a real `alt` text, or big enough to be a
photograph, among those the snapshot carries inline — the site's
chrome has none of that; for a PDF, the placed raster images at least
90 pt on each side that are not on many pages (a logo), with the
nearest "Figure N" block below as caption, put right above that
caption. Nothing new is stored: the door serves a figure out of the
original by its hash (`GET /doc/{id}/figure/{sha}`), and the document
view shows it with its caption. The chunker makes the image line, its
caption and any reading one `figure` chunk (`data`: ref, caption,
readings), so a figure is a search hit of its own. Then the second
half, the reading:

    # what the vision model makes of every figure, written under each
    prax reread --extractor figures --ids 9706

or "read again… → figures" on the page. A PDF image no caption claims
(`Figure on page N`) is read only with `parse.figures: all`
(`PRAX_FIGURES=all`, or "every image" in the page's form): many of those
are decoration. The reading goes under the
image line as `*Figure, as read by <model>:* …` (a model reads a figure
once; another model's reading joins it), and the figure chunk carries
it — findable, read by the extraction, and read by a surfing ask like
any paragraph. The model is shown what the document says around the
figure — its title, the caption, the text on either side of the image
line — because what a plot is *of* is written there and not in the
picture: without it, "six stacked curves that likely illustrate spectral
characteristics"; with it, "the frequency responses of the five learned
CNN kernels against the ground truth filter". It is told to name things
in the document's terms but to state only what is visible, so the
surrounding text names the subject without being described in the
figure's place (extractor revision r2; readings written before it stand
until a figure is read again).

**The life of a figure**, then, is four steps, and only the third costs
anything:

1. *Found.* The parser writes `![caption](figure:<sha>)` where the image
   sits (`trafilatura` r3, `pymupdf4llm` r2; `figure-refs` for a library
   parsed before that). The bytes stay in the original.
2. *Asked for.* A reading request, from "read again… → figures", from
   `prax reread`, or placed by the door itself when a capture is parsed
   and the vision model is local — the door never asks for a reading
   that costs money.
3. *Read.* A worker takes the request, the vision model describes the
   figure with the text around it, and the description is written under
   the image line. About four seconds a figure locally.
4. *Used.* The chunker makes the image line, its caption and the
   description one `figure` chunk, which is embedded and searched like
   any other, read by the extraction, and read by a surfing ask.

Step 2 is the one that does not happen by itself for everything: the
door asks only for captures (the extension, an upload, the drop folder),
only at the moment their parse finds figures, and only if the document
has never had a reading request. A Zotero library never gets one that
way. What is left over has a name on the Jobs page — the
`unread-figures` ailment counts the documents holding a figure nobody
has read, and its offers ask the vision model for them (the captioned
ones, or every image) — and the same selection is on the command line:

    prax reread --extractor figures --unread-figures --dry-run

A model reads a figure once, so a better prompt — or a better model of
the same name — reaches the library only on request:

    # the figures this model has read, read again under the current prompt
    prax reread --extractor figures --mode again --read-figures --dry-run

`--mode again` (`all-again` for the uncaptioned images too) reads the
figures this model has read before and replaces its own earlier reading,
leaving another model's where it is; `--read-figures` selects the
documents that hold such a figure. Drop `--dry-run` to place the
requests: one per document, the worker drains them before the pending
captures, and a figure costs about four seconds on a local server — a
library-wide pass is an overnight job, so `--limit` it or name `--ids`
when only some documents matter. The parsers find
figures as they read (`trafilatura` r3, `pymupdf4llm` r2); the
retroactive pass over a library parsed before that is `figure-refs`,
which puts the original's figures into the current text without
re-reading the pages (pymupdf4llm's layout analysis takes 10–30 s a
document; this takes a fraction of a second):

    prax reread --extractor trafilatura --text-source trafilatura/2.2.0-r2   # 284 pages: 2 min
    prax reread --extractor figure-refs --text-source pymupdf4llm/1.28.2

A document whose text comes out the same is `same` — the stamp moves,
nothing is rebuilt — and one that gained a figure keeps every chunk
whose text did not change, with its vector; only the changed chunks
are re-embedded. Vector drawings in a PDF are not found this way (they
are not images); `vision-pages` over the page covers those.

Measured on an orchestral score (Cowell, doc 8605, 2026-09-14): where
OCR produced table-shaped garbage, the local model gave "a page of
orchestral sheet music showing staves for Percussion, Trumpet, Horns
I–II and III–IV, Trombones, and Tuba … *mf*, *cresc.*, *senza sord.*" —
findable, not a transcription of the notes; and on some pages only the
bar numbers, so look at a few pages of a document before running the
whole of it. The pass replaces the document's text (the OCR reading
stays in the archive and in `parse_history`), as every PDF extractor
does; the additive reading is the image extractor's, section 3b above.

Office documents need nothing installed for `.docx` and `.odt`: both
are a zip of XML and `prax.parsers` reads them here (headings by outline
level or style name in any language, numbered and bulleted paragraphs
as list items, tables as Markdown tables), and `.rtf` is read by
`striprtf` (an 8 KB package in the `ingest` extra) as plain text. Only
the old binary `.doc` goes through LibreOffice, which converts it to
`.docx` in a scratch directory with a profile of its own: install it
([libreoffice.org](https://www.libreoffice.org), or `soffice` on PATH)
and the `office` extractor appears; without it those documents stay
pending and say why. Python's MIME table misses `.docx` on some machines,
so `prax.parsers.guess_mime` names the office types itself.

Before switching the default extractor for a document class, run
`scripts/compare_extractors.py` over a sample and read the texts, not only
the metrics table it writes.

Long passes over thousands of PDFs are better as a loop of short-lived
processes than as one: MuPDF's layout analysis grows the process over
time, and one run over 5,000 documents was killed for memory. A worker
re-selects what is left on every pass, so batching costs nothing:

    for ($i = 0; $i -lt 40; $i++) { prax work --scope all --steps parse -n 250 --quiet }

(the watching worker is the same process for as long as it runs, as
a service from logon on, and the nightly pass takes a hundred at a
time — 4b).

Layout analysis reads a long PDF a window of pages at a time
(`parse.layout_window` [`PRAX_LAYOUT_WINDOW`], 100: a 532-page book in
two minutes, the process flat at ~570 MB; a heading's level is ranked
within its window, so a window without a chapter title may rank its
sections one up). Originals above `PRAX_MAX_LAYOUT_MB` (default 200)
skip layout analysis and get plain text through the fallback: one line
per line of print, no headings — what 98 documents had before the
window, when the cap was 400 pages and 40 MB (`prax reread --extractor
pymupdf4llm --text-source pymupdf/ --mime application/pdf` reads them
again). A scan is refused for OCR by a probe of the first five pages and
ten more spread over the document — a journal issue with a scanned
cover has its text from page seven on.

Code is kept as code. HTML pages come out as Markdown with `<pre>` blocks
fenced (trafilatura's Markdown output), and a page's comment section —
when the snapshot has one: a thread under a blog post, a forum page —
follows the article under its own `## Comments` heading (trafilatura
r4), so every comment is a chunk under that heading, searchable, and
the article's own text stays what a search hit or an extraction reads
first; `parse.comments: false` (`PRAX_COMMENTS`) leaves them out, with
`+nocomments` in the stamp. Text attachments that are source
files become one fenced block with a language: the filename's extension
decides when it is telling (`.m`, `.py`, `.scd`, `.h`, ...), otherwise
Magika, a small content-type model in the `ingest` extra, classifies the
bytes and only a confident programming-language verdict counts (a
bibliographic note full of "Key: value" lines scores as YAML and stays
prose). A note that mixes prose and code, a forum thread or a chat log
with a function pasted in, gets its code regions fenced by a line scorer
(code signals per line, runs of at least four lines, block comments
always code), so each function is one chunk and the prose around it stays
prose. Extractors carry a `revision` in their stamp (`plain/1-r3`) so
such changes re-select what they wrote:

    prax reread --extractor trafilatura --text-source trafilatura --mime text/html
    prax reread --extractor plain --text-source plain --mime text/plain
    # the vectors for the new chunks follow: the worker's embed step

(the backlog pass finds these by itself once the revision is bumped — 3l¾;
the request is for doing it now).

For PDFs, pymupdf4llm fences monospace runs, which misses code set in a
proportional font; Docling's layout model has an explicit code label and
is the better extractor for a hand-picked set of code-heavy papers
(`--ids ... --extractor docling --force`).

Images (schematics, plots, photos, whiteboards) get text through a
vision model: the `vision` extractor describes the image and transcribes
its printed and handwritten text into Markdown, which becomes the
document's text artifact like any parser's output. Explicit only. The
`vision` step of `prax.yaml` names the model:

* a Claude model — a few cents per image (`PRAX_VISION_MODEL`, default
  `claude-sonnet-5`; Haiku 4.5 misread a compressor schematic's identity
  where Sonnet transcribed the whole revision table);
* the local llama-server, when its model is a vision-language model and
  the server was started with the model's projector (section 3h,
  `-Mmproj`): free, about 15 s per image on the 24 GB card with
  Qwen3.6-35B-A3B, which transcribed the 1176 schematic's component
  values more completely than Sonnet and named the device slightly less
  well (measured 2026-09-13, one image; Claude's descriptions carry
  more interpretation, the local ones more verbatim text).

    prax reread --extractor vision --mime image/     # every image again; the door
                                                     # asks for new ones by itself (3l½)

The stamp records the model (`vision/1+<model>`); `claude-vision` is the
same extractor pinned to Claude, the name the first descriptions carry.
The document view shows an image inline above its description. An image
that matters gets the expensive reading the way a paper does: promote it
(section 3e), and the promote step (`prax work --steps promote --spend`)
describes it again with its model (Sonnet here) before extracting from
that.
Readings add up rather than replace each other: the second model's
reading goes first in the artifact, the earlier ones follow, every
section headed with its model (`## Text in the image (qwen…)`), so the
verbatim list one model is good at and the interpretation the other is
good at are both searchable and both feed the extraction; the same
model read again replaces only its own reading (`vision.merge_readings`).

## 3c. Chunks

`prax.chunking` turns each text artifact into structure-aware chunks
(rationale R13): sections of paragraphs, whole tables with their caption
and a parsed grid, figure captions, code blocks, each with a heading path
and a locator (character range and page). Search hits carry `kind`,
`heading` and `page`; `kind="table"` narrows to tables. After changing the
chunker, or after a migration that added chunk columns:

    prax maintain --rechunk               # every indexed document, a job on the door

Chunks are disposable; nothing else is touched, and a chunk whose text
did not change keeps its id and its vector.

## 3d. Embeddings and hybrid search

`prax.embeddings` runs bge-small-en-v1.5 (384-d) through onnxruntime;
the model files are fetched once into `<data dir>/models/` on first use
(`prax.fetch`, plain HTTPS from the Hugging Face hub, resumable; a copy
in an old Hugging Face cache is taken from there; `PRAX_OFFLINE=1`
refuses to download; `python scripts/fetch_model.py --embed` fetches
ahead of time). Vectors live
in `<data dir>/vectors-<model>.usearch` (a memory-mapped HNSW index,
`prax.vectors`), bookkeeping in `chunk_embeddings`. Search is hybrid by
default and falls back to FTS when there is no index file, no usearch or
`PRAX_EMBED=0`.

    pip install -e ".[embed]"
    # Windows desktop with a GPU: DirectML instead of the CPU runtime.
    # Never both: the two packages share one module and the last one
    # installed wins, silently (the desktop ran on the CPU for days so).
    pip uninstall -y onnxruntime onnxruntime-directml; pip install onnxruntime-directml

    prax work --steps embed --scope all       # everything pending; idempotent
    prax status                                # the counts

The worker embeds what has no vector yet and posts the vectors; the door
writes them into the small delta index beside the main file and folds
the delta in when it grows (`POST /vectors/merge` does it now). Nothing
is stopped for it. Re-parsing documents (a new extractor revision,
Docling on a few) creates new chunks that a following pass embeds; the
`chunks-without-vectors` ailment (3m) counts what waits. Copy every
`.usearch` file together with `prax.db` when moving the store, or let
`prax backup` do it (7).

Settings: `PRAX_EMBED` (model name, `hash` for tests, `0` off),
`PRAX_EMBED_VARIANT` (`int8` by default everywhere: measured 2026-09-12
on 1,024 real chunks, CPU int8 23 chunks/s, CPU fp32 17, DirectML fp32
45, DirectML int8 80, so int8 is the faster one on both and keeps the
store's vectors from one variant), `PRAX_EMBED_PROVIDERS` (DirectML
first when the runtime offers it), `PRAX_EMBED_THREADS`; `PRAX_VEC_DTYPE` (`f16`
default, `i8` for half the file at recall 0.93) and `PRAX_VEC_EF` (search
expansion, 64) for the index. Changing the model means re-embedding into
a new file: `chunk_embeddings.model` records what each vector came from
and the worker's embed step picks up the difference.

Query: `search(q, mode="hybrid"|"fts"|"vec", kind=..., doctype=...)` in
the store, the API (`/search?mode=&doctype=`) and the MCP tool. Hybrid
fuses four rank lists at document level: chunk BM25, chunk KNN, and BM25
and KNN over the document field (migration 0005: title, kind words,
creators, venue, extraction summary, an image description's opening
paragraph). Hits carry `score` (RRF) and `fts_rank`, `vec_rank`,
`field_rank`, `dvec_rank`; a hit found only through the field opens at
the document's best-matching chunk. `doctype` keeps one type: `pdf`,
`web`, `image`, `text`, `note`. The field follows a document's text and
metadata on its own; after migration 0005 or a change to
`store.document_field` rebuild it and embed:

    prax maintain --only fields        # the field of every document (3n)
    prax work --steps embed --scope all  # then the vectors, chunks and fields

Why: chunk scoring finds documents *about* a term, not the document that
*is* the thing; "schematic" put a CAD manual first and the one schematic
nowhere (`docs/eval/retrieval-field-2026-09-10.md`).

### Acronyms

Papers define their acronyms in the text ("antiderivative antialiasing
(ADAA)"). The `acronyms` pass of `prax maintain` (3n; nightly) collects
those definitions from every text artifact into the `acronyms` table
(migration 0008; 5,887 pairings from the library, 1,826 defined by two
or more documents), and the search expands a query token that is a
known acronym to its phrase on the keyword side as a phrase match (the
embedder sees the query as typed: expanding it measured worse).

One more rank list in the same change: chunks that contain the query's
rare acronym-shaped terms (at most six letters, digits, or a known
acronym, in fewer than 50 chunks), weight 3, so "adaa iir" is decided by
the five documents that say ADAA and not by the thousands that say IIR;
a query that is only such terms weights the keyword list instead.
Measured on the library set in `docs/eval/retrieval-acronyms-2026-09-12.md`
(MRR 0.89 to 0.905); feeding the expansions to the embedder and an
all-terms tier were tried and left off.

## 3e. Graph extraction (Stage 3)

`prax.extraction` sends each document's metadata header and the first
12,000 characters of its text to Claude with a JSON schema generated from
the composed ontology, and writes the returned triples through `store.link` with
a confidence and a quoted evidence string. Triples that do not fit the
ontology go to `review_queue`; the summary goes to `meta.summary`; the
document is stamped with the ontology version so reruns are incremental.

    prax work --steps extract --scope all -n 20       # a trial, then the rest
    prax work --steps extract --scope all --workers 3   # the backlog, a served model's slots

The extract step never spends money: a Claude model in `steps.extract` is
refused by the worker (the promote step, below, is the paid pass). Notes
and empty scans are skipped (`pipeline.MIN_CHARS`, 500 characters).

A prompt the server's slot cannot hold (a schematic's symbols, a dense
script: more tokens per character than the 12 K-character budget
assumes) is cut to the share the server states and asked once more,
`cut: 1` in the stamp; a document that still fails is remembered
(`meta.extraction_error`, the `extraction-failed` ailment) and not
tried again every pass — until the ontology moves or a bigger slot is
up. The summary a local model writes is bounded at 700 characters and
cut at a sentence; before 2026-09-15 the bound was 300 and the grammar
stopped it mid-word, which is what the summaries extracted earlier look
like until a pass reads those documents again.

Settings: `PRAX_EXTRACT_MODEL` (default `claude-opus-5`),
`PRAX_EXTRACT_EFFORT` (default `medium`), `PRAX_EXTRACT=stub` for tests,
`PRAX_EXTRACT=<name>` for an `openai` model of `prax.yaml` (section
3h: llama-server on this or another machine). The local path asks for
tab-separated lines instead of JSON under a grammar that bounds the
output to 20 triples (`prax.lineformat`); the extractor name stamped on
documents is `<model>@<host>` and its cost is zero.
Bumping the ontology version re-selects every document. Review the queue
in the UI's Review tab (section 3g) or with `store.list_review` and
`resolve_review`.

### Provenance and upgrading a producer's work

Every edge records its `producer` (a model name, `zotero`, `crossref`,
`page`, `replay`, `manual`, `agent`) and `run` (a batch id, a script run,
a page revision). `store.provenance_summary` lists live and retired edges
per producer and run; `store.retire_run(producer=..., run=...)` ends a
run's edges when a better pass has replaced them (history stays). Edges
written before migration 0007 were tagged once (`store.backfill_provenance`,
2026-09-11); every edge since carries both.

### Entity resolution

    prax resolve                        # the plan: sure, twins, likely — nothing merged
    prax resolve --apply                # the sure merges, a job on the door
    prax resolve --apply --twins        # concept+method twins as well
    prax resolve --type author          # one entity type

The likely tier is listed for you; an adjudicator is the only thing
that merges it — the `adjudicate` step of the worker, whose model is
`steps.adjudicate` in `prax.yaml` (a Claude model: paid, so the step
runs only with `--spend`, or `spend: true` under `run.worker`, and only
when named in `--steps`):

    prax work --steps adjudicate --spend     # the likely pairs to the adjudicate model, once

Each pair is one line of a forty-line question; a yes is a merge, a no
is recorded on the pair (`entity_candidates.decided`) so the next
week's computation of its type does not ask again. The worker says
what a pass cost. Measured 2026-09-17: 8,242 pairs, 3,683 merged, 4,559
kept apart, $2.59 with Opus 5 — a tenth of a cent a decision.
`scripts/resolve_entities.py --commit --adjudicate` does the same from a
process that opens the database file, for a host without a worker.

Sure merges are equal names after normalization (case, accents,
punctuation, plural, suffixes) and author initials forms that abbreviate
exactly one full name; they need no model. Likely merges are close by
name embedding, for concepts, methods, tools, datasets and venues only,
and merge nothing unless an adjudicator says yes. The embedding is a
worker's, never the door's: the `resolve` step of `prax work` takes one
type's names from the door, embeds them, finds the pairs at or above
the threshold (0.92, a block of rows at a time — 30,000 names cost a
few hundred megabytes, not the square) and posts them; the door keeps
them (`entity_candidates`, replaced whole per type) and the plan reads
them from there, skipping any pair an entity of which has since been
merged. A type is computed again after a week; `prax resolve` says when
each was. Embedding 138,000 names inside the door, and the square of
similarities after, crashed it once (2026-09-17) — it does not embed
anything now. A merge sets `entities.canonical_id`; nothing is deleted,
`traverse` and the UI follow the pointer, and undoing one is clearing
that column.

### Promoting documents to the expensive model

The local pass reads everything once; the papers you work with deserve
the richer pass (claims, relations between methods). A document is
flagged in `meta.promote` from its page ("promote"), from the Promote
view's candidates (scored by project membership, synthesis sources,
notes on the document and citations from other library documents), by
Claude Code through the `promote` MCP tool, or by the store itself when
the document joins a project or becomes a synthesis source. The flag
queues; nothing is spent until the pass runs — a work step the worker
takes only when named, and only with `--spend`, which is the asking:

    prax work --steps promote --spend          # the flagged documents, once each
    prax work --steps promote --spend -n 5     # five of them

Without `--spend` the worker says the step is paid and touches nothing;
a promoted image is first described again by the promote model, and the
extraction reads that. The model is the `promote` step in `prax.yaml` (section 3k; default
Sonnet 5, `max_triples: 30`). A flagged document counts as done once
that producer's stamp is in its extraction history, so the pass is
idempotent and a later local pass does not undo it; both producers'
edges sit side by side. `GET /promote` returns the flagged list with
status and the candidates; `POST /doc/{id}/promote` and `DELETE` set
and clear the flag.

### A document's domains

A document is read against the ontology modules it belongs to, its
domain set in `meta.domains`: the papers against `core` plus `research`,
the family photos against `core` plus a `family` module, a document that
is both (a relative's thesis, a photo from a conference) against both.
No domain set means every module, which is what the library had before
modules existed. The extraction prompt, the grammar and the JSON schema
are built for that subset, the document itself is a `paper` where the
research module is loaded and a `document` otherwise, and the stamp
carries the subset's version (`core1+family1`), so a document is due
again when one of *its* modules grows, not when any module does.

Sets come from rules in `prax.yaml` (first match wins; a rule without
`match` is the default; `match` keys `source`, `mime`, `path`,
`collection`, `tag`):

    domains:
      - match: {collection: Family}
        domains: [family]
      - match: {source: zotero}
        domains: [research]
      - domains: [research]

    prax maintain --only domains        # documents without a set (3n; nightly)

A capture gets its set from the rules as it arrives; the pass is for what
came before the rules, or after a rule changed (`store.assign_domains(...,
force=True)` re-assigns rule-set documents too).

A set written by hand ("domains…" in the document page's action row,
`PUT /doc/{id}/domains`, `POST`/`DELETE /doc/{id}/domains/{name}`, the
`set_domains` MCP tool) is never overwritten by the rules. Adding a
domain keeps the others; removing the last one puts the document back in
every module. A re-run for one domain reads the documents assigned to
it that are not yet stamped with their subset's version:

    prax work --steps extract --scope all      # the backlog pass takes them: a
                                               # document is re-selected when its
                                               # subset's version moved

`search(..., domain="family")` (API and MCP `domain=`) keeps the hits
from that domain; documents without a set are in every domain. When a
document is read again under another subset (its domains changed, or
one of its modules grew), the same producer's earlier reading is
retired as the new one is applied (`store.retire_reading`, history
kept); other producers' edges stay.

### Typing rules over the queue

A model's misfits are systematic: the document typed as what it is about
("this manual" as a tool), `authored_by` written backwards or with authors
on both ends, `cites` for a tool or method the paper uses, `about` for a
claim it makes or a paper it discusses, and in the studio domain the
document put where its device belongs ("the manual has this feature":
moved onto the device the document describes). The door applies the
rules in `prax.review.apply_typing_rules` to a document's items right
after its extraction; over the whole queue they are the `review` pass of
`prax maintain` (3n) — a replay against the current ontology first,
then the rules — which the nightly task runs:

    prax maintain --only review

A rule retypes, flips, renames the relation, or drops what no relation can
hold; it never invents. What it links is written as INFERRED edges with the
producer `typing-rules` and one run id per pass, with the item's evidence
and source document, so a pass can be retired like any other producer's.
Unmapped items are covered too when the relation the model named and the
shape of the names decide (affiliation between a person and an institution,
supervision between two people, authorship between a title and a person,
funding, who built a tool, a mention whose reason names what kind of thing
it is). Items the rules do not cover stay open as evidence for the next
ontology version; v5 (`docs/ontology-v5.md`) came out of that evidence. After the
local backlog of 2026-09-12 the first pass closed 2,898 of the 4,300 typed
items as linked (2,468 new edges, 430 already in the graph) and dropped 769.
A second batch of rules after the v5 re-read (placeholder names, a cited
"document" that is a paper, a listed "person" who is the author, venues,
cited titles) linked 3,091 and dropped 3,390 of 25,827; research v6 and a
replay took 6,680 more.

What no rule can decide — an item with no types at all, "X about Y" with
nothing but the names — goes to a model (`prax.typing_pass`): a batch of
two dozen items with the document's title, its own type and the ontology
subset it is read against, answered as `<n>: <type> -> <type>` or `none`.
An answer that fits the ontology (after the same remaps the rules use: a
"cited" tool is used, a "cited" person is mentioned) becomes an INFERRED
edge, producer `typing:<model>`; `none` drops; a misfit stays open. The
model is the `typing` step (`prax.yaml` or `PRAX_TYPING=…` for one run):

    prax work --steps typing -n 10           # ten requests of up to 40 items
    prax work --steps typing --watch          # until the queue is typed

A work step (3l): the door hands out batches of untyped items with their
documents' titles and domains, the worker asks the typing model, the
door applies the answers — and a misfit keeps the types the model gave
it, so it is not asked again and the rules or a later ontology can take
it. The step runs only when named; a paid typing model is refused.

On 480 items of the live queue the local 35B linked 263, dropped 21 and
left 48 misfits, at about 30 s a request of 24 items on the 4090.

## 3f. Citation network

`prax.importers.citations` asks OpenAlex or Crossref for each document's
reference list and citation count (by DOI, or by exact title with
`--resolve-titles`) and writes `paper --cites--> paper` edges with the
source and work ids as evidence; references that are library documents
are named by their document title. `meta.citations` holds the citation
count and makes re-runs skip. No key needed; `PRAX_CITATIONS_MAILTO`
joins the polite pools. Crossref answers in half a second per request;
OpenAlex needs fewer requests but was unreachable for hours on
2026-09-10, hence two sources behind one flag.

    prax import citations --dry-run                        # the selection's size
    prax import citations --source crossref                # a job on the door
    prax import citations --source crossref --resolve-titles   # DOI-less ones too, by title
    prax import citations --refresh -n 50                  # fetched ones again

The door fetches (it has the DOIs, and `citations.mailto` in prax.yaml
for Crossref's polite pool); the job's note counts resolved documents,
edges and requests as it goes.

### Review queue and ontology growth

The review view (`#review`) filters by relation and by unmapped versus
typed items, drops all matching items in bulk, and replays typed items
against the current ontology; the same is the `review` pass of
`prax maintain` (3n), nightly. The proposal for ontology v2, built from the
queue's numbers, is `docs/ontology-v2.md`.

## 3g. Pages: notes, projects, topics

Pages are Markdown documents in the store (rationale R15). From the UI:
"add a note" on any document creates an addendum page linked to it
(`annotates`), the Pages tab creates topic and project pages and lists
them, "edit page" opens the editor, every save is a revision with an
author, and the context column offers "add to project" (`part_of`).
From code or the MCP door: `write_page`, `append_page`, `get_page`.

    PUT  /page/{slug}        {text, title?, kind?, author?, note?, annotates?, part_of?}
    POST /page/{slug}/append {section, heading?}         # the agent's way in
    GET  /page/{slug}, GET /page/{slug}/revision/{n}, GET /pages?kind=
    POST /project/{slug}/members {doc_id}

An agent revision over a human one is refused (HTTP 409, MCP error);
`append_page` adds a section instead. A `synthesis` page (ontology v4,
`docs/ontology-v4.md`) draws on several sources: its `annotates` list
becomes `synthesizes` edges, and extraction may give it claims the
papers support or contradict. Pages are extracted and embedded
like any document, so a topic page's concepts enter the graph; the
extractor's header carries `Kind: page` or `Kind: project`.

## 3h. Local models (optional)

A local model runs in **llama-server** (llama.cpp's HTTP server), on the
machine with the GPU, and prax talks to it as an `openai` model in
`prax.yaml` (section 3k). Nothing of the model lives in prax's own
process: the door stays lean (invariant 7), the worker stays small, and
the same server serves extraction, titles and ask at once through its
slots. Measured choices: `docs/eval/extractors-local-2026-09-11.md`
(Qwen3.6-35B-A3B on a 24 GB card, 4-5 s per document with 3 slots;
Qwen2.5-7B on an 8 GB card, `docs/eval/local-llm-2026-09-08.md`).

    # llama.cpp release binaries (CUDA, Vulkan, Metal or CPU builds):
    #   https://github.com/ggml-org/llama.cpp/releases
    # unpacked to %LOCALAPPDATA%\prax\llama.cpp on Windows, ~/.local/share/prax/llama.cpp
    # or the PATH elsewhere (Homebrew's llama.cpp puts llama-server there);
    # paths.llama_server in prax.yaml names any other place
    prax models fetch server-35b       # repo and file from prax.yaml, once

Then the server is a `serve:` block on the model's entry in `prax.yaml`
and a line under `run:`, and `prax up` starts it (4b) — the port from
`base_url`, the context per slot from `n_ctx`, the file from `repo` and
`file` (or `serve.path`):

    models:
      server-35b:
        kind: openai
        base_url: http://127.0.0.1:8080/v1
        model: <name the server reports>    # the producer name on edges
        repo: unsloth/Qwen3.6-35B-A3B-GGUF
        file: Qwen3.6-35B-A3B-UD-Q4_K_S.gguf
        n_ctx: 16384
        serve:
          slots: 2
          mmproj: mmproj-F16.gguf   # the projector beside it: the same server describes images
          cpu_moe: 2                # a card that also drives the display: see below
          ubatch: 256
          image_max_tokens: 1024
    steps:
      extract: {model: server-35b}
      titles:  {model: server-35b}
      ask:     {model: server-35b}
    run:
      llama-server: {model: server-35b}

`prax up --status` shows it *starting* until the model is loaded (three
minutes for the 35B, cold) and *up* once its `/health` says so; its
output is in `<data dir>/logs/llama-server.log`. The command line that
comes out (`--flash-attn on`, the KV cache at `q8_0`, `--n-gpu-layers
999`, `--load-mode mmap`, thinking off, `--metrics`, no web UI) is
`prax.up.llama_argv`; `serve.extra` appends arguments of your own.

The grammar-constrained line format (`prax.lineformat`) needs a server
that honours the `grammar` field: llama-server does, vLLM does not.
Thinking is off unless `serve.thinking: true`; it matters for models
that think by default (Qwen3.x, Gemma 4): thinking tokens would break
the grammar. Memory on a Windows host: howto 3l, "Jobs". An 8 GB card
fits a 7-8B model at Q4 with an 8 K context; a board without a usable
GPU leaves the steps at `none` or points them at a server elsewhere on
the private network.

**A card that also drives the display.** The 35B-A3B at Q4 with three
8 K slots took 22.1 GB of a 4090's 24.5, and the projector with its
compute buffers pushed that to 23.7 — the desktop's own programs were
starved and the display froze (2026-09-14). Measured by the server's own
dedicated memory (`Get-Counter '\GPU Process Memory'`):

| `serve:` | dedicated | speed |
|---|---|---|
| 3 slots, projector, `ubatch: 256, image_max_tokens: 1024` | 21.1 GB | 167 tok/s |
| 2 slots, the same | 20.8 GB | |
| 2 slots, the same, `cpu_moe: 2` (in use) | 20.1 GB | 148 tok/s |
| `cpu_moe: 8` | | 23 tok/s |
| `projector_on_cpu: true` | an image took minutes to encode; do not | |

The slot count changes little (a `q8_0` 8 K slot is ~0.3 GB) and
`cpu_moe` is the dial that matters: it keeps the expert weights of the
first N layers in RAM, a MoE model's bulk — 2 costs a tenth of the
speed, 8 most of it. The desktop wants 3-4 GB. Memory on Windows
(2026-09-12, a 22 GB model on a 32 GB machine): the driver backs every
VRAM allocation with system commit, so the server charges 20-30 GB of
commit whatever the load mode; `mmap` keeps that at about 22 GB (the
model's pages are file-backed and evictable). With IDEs and a browser
open the commit limit is what runs out first, so a page file of at least
twice the RAM is the setting that matters. Extraction prompts are about
3,500 tokens in and 1,100 out, so 8 K per slot is the floor; what a
slot costs at 16 K and beyond: `deploy/README.md`, "The card on the
desktop".

**The mathematics, as LaTeX: marker.** `pymupdf4llm` drops a display
equation — in a two-column paper it is usually a vector drawing — and
the library's text for its most equation-heavy papers held 672
references to numbered equations and 18 `$` characters
(`docs/eval/marker-equations-2026-09-15.md`).
[marker](https://github.com/datalab-to/marker) reads them back as the
paper's own LaTeX, `$$…$$` on a line of its own, which the chunker makes
a `formula` chunk of (3l), and its tables come out as tables. It is a
heavy install (1.3 GB of torch and surya; the weights are RAIL-M, the
code Apache 2.0) and never one of prax's dependencies: it lives in a
venv of its own, and its server is a role of `prax up`:

    python -m venv %LOCALAPPDATA%\prax\marker-venv        # ~/.local/share/prax/marker-venv elsewhere
    %LOCALAPPDATA%\prax\marker-venv\Scripts\pip install marker-pdf fastapi "uvicorn[standard]" python-multipart

    run:
      marker: {venv: C:/Users/you/AppData/Local/prax/marker-venv, on_demand: true}
    # parse:
    #   marker_url: http://127.0.0.1:8765   # [PRAX_MARKER_URL] the server the extractor sends PDFs to
    #   marker_mode: fast                   # [PRAX_MARKER_MODE] balanced: the vision model lays out too

The server reads through llama.cpp's server with `ngl` layers on the
card (99, all, by default; `ngl: 0` for the CPU) and wants about 5 GB of
it, which beside a 20 GB model does not fit a 24 GB card — hence
`on_demand`: declared, started when wanted. Measured 2026-09-16 on the
4090: **2 s a page** through the server against 33 on the CPU, so the
library's 250,000 pages are still days, but the papers whose formulas
matter are an evening:

    prax reread --extractor marker --maths 6 --mime application/pdf --dry-run   # the mathematical papers: how many
    prax up --stop llama-server          # the card free
    prax up --start marker               # ten seconds, then a minute for its first request
    prax reread --extractor marker --maths 6 --mime application/pdf --wait --timeout 420   # or --ids 9549 9813 …
    prax up --stop marker                # its llama-server ends with it
    prax up --start llama-server         # the formula readings the door asked for run once it is up
    prax readings --wait                 # until those are read too; then the extraction follows

Six lines, the same in bash, zsh and PowerShell (`;` between them for one
line), and nothing else: `reread --wait` prints the count as it moves and
returns when no marker request waits (2 on the timeout, minutes), `prax
readings` shows the queue per extractor whenever you look, and `prax up`
is the same swap on every platform. The night of 2026-09-16 did this with
two hand-written watcher scripts that guessed at "stuck" and "nothing
moved in an hour", and both guessed wrong; the queue itself is the
signal. `--maths` is the density of references to numbered equations in
the prose — "(4)" between words — per 10,000 characters, with at least
15 of them: 10 is a mathematical paper, 6 a paper with equations, 3
anything that numbers a few; this library has 111, 281 and 606 PDFs at
those marks, 40 minutes, two hours and four and a half of the card. A
marker read that produced display equations asks the door for their
readings itself (the `formulas` step named, a local model), so an
evening is the two swaps and the one request; the extraction follows,
since the text changed under it. A reading whose server is the one
paused waits, deferred, and comes round once the card is back (7).

`marker` is an explicit extractor, never a default or a fallback: asked
for per document, stamped `marker/2.0.0` (the version read from the
role's venv, `+balanced` for the other mode), reversible through
`parse_history`. What comes back: the mathematics, the tables, headings;
marker's own image references are dropped (they name files it did not
write here) and prax's figure references are placed by hash as for
every PDF, so the figures keep their readings. A two-part definition set
side by side may come back as a small table — marker's reading of the
layout, kept as it said it.

**A reading for a formula.** A display equation is a `formula` chunk
with its LaTeX and its number (3l), and the LaTeX embeds to noise: a
person searching says "Shockley's diode equation", not
`\frac{a-b}{2R}`. So, as a figure gets a reading from the vision model,
a formula gets one from a text model — one to three sentences under the
equation, in the document's own terms, with the name the equation goes
by when it has one. `steps.formulas` names the model (`none` by default);
the `formulas` extractor writes the readings into the text, asked for on
a document's page ("read again… → formulas") or over every document
holding an unread equation, which the `unread-formulas` ailment counts:

    steps:
      formulas: {model: server-35b}

    prax reread --extractor formulas --unread-formulas
    prax reread --extractor formulas --read-formulas --mode again   # a better prompt or model: this model's earlier readings replaced, another's kept

The 35B reads about one equation a second; the eight marker papers'
537 equations took ten minutes, and "Shockley diode equation" then
brings the formula itself. The web UI typesets the LaTeX with KaTeX
(vendored, nothing fetched): every formula chunk, with its number and
the source a click away, the inline maths of a document that carries
any, and an answer's — a `$` before a digit is left as the price it is. The stamp is `formulas/1+<model>`; the
`parse.formula_readings` setting [`PRAX_FORMULA_READINGS`] is the mode
(`new` or `again`) for one run. A model is only ever shown the equation
and the prose around it, never another equation or an earlier reading.

**One card, several jobs.** The same loaded model serves extraction,
titles, ask and — with its projector — images, so one server is the
whole local side; two *different* models on one card are sequential:
llama-server's router mode (`--models-dir` or `--models-preset`, with
`--models-max 1`) loads the model a request names and unloads the other,
at the cost of a reload (10–30 s for 22 GB) each time the job changes.
A small second model fits beside the big one: the reranker below (0.6
GB) ran next to the 35B (23.9 of 24.5 GB).

**Context per slot.** Every slot gets `n_ctx` tokens (the server's `-c`
is `slots × n_ctx`, split evenly), so 2 × 16 K gives a document 16 K; a
text whose script tokenizes densely (Arabic at about a token per
character) can overrun prax's 16 K-char budget. For those, `n_ctx: 24576`
with `slots: 1` for a while, `prax up --restart llama-server`, and `prax
work --steps extract --scope all` while it is up is the way (three
books, 2026-09-13).

**Load figures.** `--metrics` (on by default in the script) exposes
Prometheus text at `/metrics`; the door's `GET /models/servers` reads it
with `/props` for every `openai` model in `prax.yaml`, and the Jobs page
shows each server: model file, slots, whether it sees images, requests
running and waiting, tokens per second, tokens read since the start and
how many of them came from the prompt cache.

**A reranker in llama-server.** A second server role starts the same
binary with a cross-encoder GGUF (`--reranking`, one slot, its own port) and
`rerank: {model: server, url: http://127.0.0.1:8081}` in `prax.yaml`
(or `PRAX_RERANK=server`) rescores the top hits through it, 92 ms for
ten candidates on the GPU:

    models:
      ranker: {kind: openai, base_url: http://127.0.0.1:8081/v1, model: bge-reranker-v2-m3,
               repo: …, file: bge-reranker-v2-m3-Q8_0.gguf, serve: {reranker: true}}
    run:
      reranker: {model: ranker}

Measured 2026-09-13 on the library's 62 queries (`docs/eval/rerank-server-2026-09-13.md`):
bge-reranker-v2-m3 at depth 10 scores hit@1 0.74 / MRR 0.83 against
0.85 / 0.90 for the fused list alone — paraphrase and structure queries
gain a little, keyword queries lose a lot, as with the ONNX rerankers in
2026-09-08. Reranking stays off; the route is there for a
document-aware candidate (title, heading path, chunk) later.

## 3i. Ask: questions answered from the library

What the asking model may do, what it may not, what each move costs and
how to steer it: [`docs/ask.md`](ask.md). This section is how to set it
up.

`prax.ask` turns a question into a bundle (one passage per document from
the hybrid search, plus what the graph records about those documents)
and hands it to a model that answers with `[n]` citations. Which model
is the host's choice:

| `PRAX_ASK` | who answers |
|---|---|
| an `openai` model | a llama-server or vLLM on this or another machine answers; the door loads nothing (section 3h; a 7B answers in about 20 s on an 8 GB card, a 35B-A3B in a few seconds on a 24 GB one) |
| a `claude` model | the API (effort low; about a cent per question) |
| `none` | nobody: the bundle comes back for the caller's own model |

The step is `ask` in `prax.yaml` (section 3k), `PRAX_ASK=<name|none>`
for one run. Without a file it is `none`, so the serving board answers
with the bundle. On the batch host:

    $env:PRAX_DATA_DIR = "C:\prax-data"
    uvicorn prax.api:app --port 8000     # steps.ask.model in prax.yaml (3k)

Then the Ask tab in the UI, or:

    POST /ask {"question": "...", "limit": 8, "doctype": null, "backend": null}
    GET  /ask/config
    POST /ask/save {"slug": "reverb", "heading": "...", "result": <the /ask response>}

`backend` overrides the host setting for one question. The response
carries the passages (chunk and document ids, text), the facts, the
answer, the model, the citations it made (invented numbers are
dropped), token usage, seconds and cost. `save` appends the answer to a
page as the agent under the question as heading, with a source list
linking the cited documents and `annotates` edges to them; a human's
text on the page is never touched. Over MCP the `ask` tool returns the
bundle by default (Claude Code answers itself) and runs the host's
model with `answer=True`.

**Surfing.** A model that answers does not have to take the first
search's eight passages as they come. With `steps` (the composer's
"steps", `prax ask --steps`, the request's `steps`; the host's default
is `steps.ask.steps` in `prax.yaml`, 8 out of the box, 0 is the
one-shot answer) the model works the library first (`prax.surf`): each
step it writes a note and one action — `search` again with better
words, `read` on where a passage stopped (`read: [3]`), a document a
result named (`read: doc 4080`) or, with words after either, the part
of that document which holds them (`read: doc 4080 delay-free loops`:
the only way into a long paper the graph pointed at, whose start is a
title page), `facts` of a passage's document, `walk` the graph from an
entity (its relations, the documents behind them), `similar` documents,
`drop` passages that are beside the point — or `answer` when the
passages kept say enough; a grammar holds a local model to the two
lines and to passage numbers and document ids it has actually seen. The
door's own search of the question is step 0; the answer is then
written from the passages kept, under their loop numbers, with the ask
prompt above. Two budgets bound it: the steps, and `tokens` of reading
(the composer's "reading", `--tokens`; `steps.ask.tokens`; the default
is 4,000 for a local model and the ceiling is what its context holds
beyond the prompt's overhead — 14,184 for a 16 K slot — 16,000 and
60,000 for Claude). The ceiling comes from `n_ctx` under the model in
`prax.yaml`, which is also the slot `prax up` gives the server, so the
two cannot disagree (a server started by hand with a larger slot buys
nothing until `n_ctx` says so, and `n_ctx` above the true slot only
means the server refuses the answer, which is then cut to fit and asked
again). What a card can hold
is arithmetic: the cache costs
`full_attention_layers × kv_heads × (key_length + value_length)` values
a token, which for Qwen3.6-35B-A3B (10 of its 40 layers, the rest being
SSM layers with a fixed state) is 10.6 KiB at `q8_0`, so two 16 K slots
are 0.33 GB of the 4090's 24 — measured, with the rest of that card's
budget, in `deploy/README.md`. The prompt grows by appending, so a llama-server's
prefix cache makes a step cost its own tokens only: on the 4090 the
35B-A3B takes two to four seconds a step and a whole surf twenty to
forty seconds. The result carries the `trail` (each step's note,
action, what it brought, seconds), `steps`, `dropped` and
`reading_left`; `save` keeps the trail on the page under "How it was
found". With `stream: true` the door answers one JSON object per line
as it goes — `step` events, `answering`, then `answer` with the result
(or `error`) — which is what the UI and the CLI show while the model
works; a client that goes away stops the surf at its next step.

    prax ask --answer --steps 12 --tokens 6000 what does ADAA do to a stateful nonlinearity
    prax ask --answer --steps 0 quick question        # no surfing: the first search alone

## 3j. Titles worth the name

Half the imported titles were file names (standalone Zotero
attachments come as `<md5>-slides.pdf`, items without metadata as
`Unknown - 2002 - No Title.pdf`) and conference papers arrive in ALL
CAPS. A title is what a search hit, a citation in an answer and a
`paper` entity are called, so the titles step repairs them — for a
capture as it arrives, for the library with

    prax work --steps titles --scope all       # then embed, for the document field

ALL CAPS titles are recased by rule (`titles.recase`: stopwords,
known acronyms). File names go to the titles step's model (`prax.yaml`
or `--model`; a local server keeps everything on the machine, about a
second per document) with the first 1,500 characters of text, the file name, the
first Markdown heading and the PDF metadata title as hints; it answers
with the printed title or, for a course sheet or a manual, a short
descriptive name in the document's language. `meta.title_confidence`
is `high` when the title's words occur in the text and `low` when the
model described the document. Documents without text keep their file
name.

Every change goes through `store.retitle`: the old title stays in
`meta.title_history` with its source, `meta.title_source` names who
wrote the current one (the Zotero importer leaves such a title alone
on refresh), the `paper` entity carrying the old title is renamed or
merged into the entity of the new one so its edges follow, and the
document field is refreshed, which queues the document vector for
the embed step. The document page shows the former title. A wrong
repair is fixed by calling `store.retitle` with the right title and
`source="human"`.

## 3k. What this host does: `prax.yaml`

One file in the data directory (`PRAX_CONFIG` points elsewhere;
`prax.example.yaml` in the repo is the template) holds what a host
chooses, in sections: `models` and `steps` (which model does which step),
`domains` (which ontology modules a document is read against),
`embeddings`, `vectors`, `rerank`, `parse`, `citations`, `door`,
`ontology` and `paths`. Everything has a default, so the file may hold
only what differs.

Each setting can still be given as an environment variable for one run,
and the variable wins: `PRAX_EMBED=hash pytest`,
`PRAX_VEC_DTYPE=i8 prax serve`. The names are in
`prax.example.yaml` beside each setting, and `prax.config` is where they
are read. What lives in the environment and nowhere else: `PRAX_DATA_DIR`
(it is what finds the file), `PRAX_CONFIG`, `PRAX_TOKEN` (a secret),
`PRAX_DOOR` (which door a client talks to), and the per-run switches
`PRAX_<STEP>`, `PRAX_OFFLINE` and `PRAX_DEBUG`. A section the code does
not know is an error, not a silent typo.

Every AI-assisted step (`extract`, `promote`, `ask`, `titles`, `vision`,
`adjudicate`) takes its model from the same file. `models` names
backends, `steps` assigns them:

    models:
      sonnet:     {kind: claude, model: claude-sonnet-5, effort: medium}
      local-server: {kind: openai, base_url: http://127.0.0.1:8080/v1, model: <name the server reports>}
      server-32b: {kind: openai, base_url: http://gpu-box:8080/v1, model: qwen2.5-32b}
    steps:
      extract:    {model: sonnet, max_triples: 20}
      ask:        {model: local-server}
      titles:     {model: local-server}
      vision:     {model: sonnet}
      adjudicate: {model: opus}

Kinds: `claude` (the API, key in `ANTHROPIC_API_KEY`), `openai` (any
OpenAI-compatible server: `llama-server` on this or another machine,
section 3h, vLLM, or a hosted API with `api_key_env` naming the variable
that holds its key and `price` as USD per million input and output
tokens for the cost lines; `n_ctx` says what context a slot has), `stub`
(tests). Names that need no file: any `claude-*` id, `stub`, `none`.

Precedence per step: `PRAX_<STEP>` in the environment (a model name or
`none`), then the file, then the default (`claude-opus-5` for
extraction, `none` for ask and titles, Sonnet 5 for vision, `none` for
adjudication).
`PRAX_<STEP>_MODEL` swaps the Claude model id for a step that resolves
to Claude, as before. A runtime is built once per process however many
steps name it; the model itself lives in its server.

The extraction step is the one to move when a GPU box is around:
add an `openai` model with its address and a `serve:` block, name it
under `run:` so `prax up` starts the server (3h; thinking is off there,
which matters for Qwen3.x and Gemma 4: they think by default and would
break the grammar), measured choices in
`docs/eval/extractors-local-2026-09-11.md`, point `steps.extract.model`
at it, and `prax work --steps extract --scope all --workers <slots>` runs the
same prompt and grammar against the server (llama-server honours the `grammar` field; vLLM does not,
so use a Claude-kind model or llama-server for extraction). The door
stays lean: the model lives in the server's process, not the door's
(invariant 7). `GET /ask/config` shows what the door resolved.

## 3l. Captures: uploads, sent pages, fetched URLs, the drop folder

Everything that is not a curated import comes in as a capture
(`prax.inbox`), with `meta.source` saying how (`upload`, `capture`,
`inbox`), `meta.capture` saying when and in which send, and a domain set
from the request, the folder, or the `domains:` rules in prax.yaml. Text
and HTML are searchable at once (trafilatura is light enough for the
door); PDFs and images are archived and wait for the parse queue on the
batch host. A page captured twice with the same bytes is one document;
a changed page is a new document whose `meta.previous_capture` points at
the last one with the same canonical URL (fragment and tracking
parameters stripped).

A page sent again is one document: the markup of a page differs
between two visits, so its bytes' hash does, but the extracted text does
not, and the door compares the chunk fingerprints (`store.similarity`,
Jaccard over hashed chunks, 0.9 or above) of a new capture with the
earlier live captures of the same canonical URL before it registers
anything. The same page is noted on the earlier document
(`meta.recaptured`); a snapshot arriving for a page held only as a bare
DOM replaces it. A page that changed in between is a new document with
`meta.previous_capture`. What that check missed (until 2026-09-15 a
page with figures compared short of the threshold against itself, so
a page sent twice became two documents) is handled by the `dedupe`
pass of `prax maintain` (3n; nightly), which keeps one capture per URL
(a snapshot, then an extracted one, then the oldest) and retires the
rest as duplicates of the keeper.

Retiring (`store.retire_document`, "retire…" on a document page, `POST
/doc/{id}/retire`) takes a document out of search and the graph: chunks
and retrieval field go, its edges end, open review items close; the
row, the archived bytes and the text artifact stay, `meta.retired` says
why and of which document it was a duplicate. **Retiring as a duplicate
is a union**: what the duplicate holds and the keeper lacks moves to the
keeper first — edges with their evidence and producer, open review
items, tags, domains, the summary, the extraction stamp — and only what
both hold is ended on the duplicate; two extractions of one page become
one document with every fact either found. Batch jobs and the browse
list pass retired documents by (`GET /documents?retired=1` lists them);
"un-retire" re-chunks the text and brings it back.

Four ways in:

- **The Inbox view** (`#inbox`): drop files or pick them, choose domains
  and tags, or paste a URL for the door to fetch. The list below shows
  the latest captures with their state (pending, indexed, extracted).
- **The door**: `POST /ingest/file` (multipart: `file`, `title`,
  `domains` and `tags` comma-separated, `session`), `POST /ingest/html
  {url, html, title, domains, tags, session}` for a page as a browser
  rendered it (the extension's path, `docs/extension.md`), `POST
  /ingest/url {url, …}` to fetch server-side. `POST /ingest` (text) takes
  `domains` too. `GET /inbox` lists recent captures.
- **The browser extension** (`clients/browser-extension/`, `docs/extension.md`): "send
  this tab" posts a self-contained snapshot (SingleFile: images, fonts
  and styles inlined, scripts removed), "send all tabs in window" does the
  same under one session id; a PDF tab is fetched again inside the
  browser, with its session, and uploaded, so paywalled PDFs arrive too. Load the folder unpacked (Waterfox and Firefox: about:debugging;
  Chrome: chrome://extensions) and set the server and token in its
  options. `node scripts/extension_bed.mjs --browser both` exercises it
  end to end in headless Chrome and Firefox against a throwaway door.
- **Claude Code**: the `capture_url` MCP tool.
- **The drop folder** `data/inbox/`: the door consumes it by itself
  while it runs (every `PRAX_INBOX_SCAN` seconds, 20 by default, 0 to
  turn it off): any file put there is registered through the store
  without another process.

- **The worker** takes every capture the rest of the way: it asks the
  door for work, does it with the models of its own prax.yaml, and posts
  the results (`prax.work` on the door, `prax.worker` here; howto 4
  for the door):

      python scripts/work.py                       # one pass against the local door
      python scripts/work.py --watch               # keep going (the usual way)
      python scripts/work.py --door http://board:8000 --watch   # from another machine
      python scripts/work.py --scope all --steps extract --limit 20   # a backlog pass

  Five steps, each a batch the door hands out with a lease: parse (the
  worker fetches the original and posts the text), titles, extract (the
  door sends the prepared prompt input, the worker posts the triples),
  embed (chunk and field texts out, vectors in, into the door's delta
  index), resolve (one entity type's names out, the pairs close by name
  embedding in, a type a week — the likely tier of `prax resolve`). The
  worker never spends money (a step whose model is the Claude
  API is skipped with a note; the promote pass is the way to that model),
  never opens the database, and never touches the curated imports unless
  `--scope all` says so. It announces itself as a job with heartbeats, so
  the Jobs view shows it wherever it runs. `--no-titles`, `--no-extract`,
  `--no-embed`, `--no-parse`, `--no-resolve` switch steps off; `promote`,
  `typing` and `adjudicate` run only when named. A reading or extraction
  whose server is loading or paused (a 503, a refused connection, marker's
  server not up) is not the document's fault: the worker reports it as
  "not yet", the door keeps it leased ten minutes and hands out the rest
  of the queue meanwhile, and it comes round again once that lease runs
  out — so a queue of formula readings waiting for llama-server does not
  hold back the marker readings behind it while the card is marker's.

  The worker also uploads this machine's `Downloads/prax-inbox/` when
  that folder exists (`--also` names others), sidecars included: the
  browser extension saves a file there when a site hands the file to a
  navigation only (`docs/extension.md`).

  A file in `inbox/<module>/` (say `inbox/family/`) lands in that domain;
  `<file>.json` next to a file is a sidecar (`title`, `source_url`,
  `domains`, `tags`). Files still being written (younger than two
  seconds, or `.part`/`.crdownload`) wait for the next scan. Consumed
  files are removed, the archive holds their bytes; what the store
  refused goes to `inbox/failed/`. A folder that is not prax's own (a
  download folder, a project's PDFs) is `prax add <folder> -r --domain
  research`: every file under it is uploaded, nothing is moved or
  removed, and a second run finds them already known by hash. An
  uploaded PDF shows "pending" in the Inbox view until a worker has
  been over it ("no text found" when every extractor tried and found
  none), and the view refreshes itself while something is pending.

### Jobs, and a UI that follows

Every batch pass announces itself in the `jobs` table (`store.Job`,
migration 0009): name, host and pid, a heartbeat, done and total, a
note. `GET /jobs` and the Jobs view show what runs and what ran; a job
without a heartbeat for ten minutes is marked stale, and the door closes
one whose process is gone or whose heartbeat stopped half an hour ago.
The rows are bookkeeping, nothing reads them to decide what to do.

The same view shows what the door's host has left: free RAM and commit
headroom (`prax.hostinfo`, no dependency; the worker's heartbeat carries
its own footprint, under a gigabyte between passes). Commit is the
number to watch on a Windows batch host: a GPU model server charges
system commit for the VRAM it fills (a 22 GB model is 22 GB of commit
with `--load-mode mmap`, 30 GB without), so with IDEs and a browser open
a 32 GB machine reaches its commit limit before its RAM runs out, and
processes then fail to start. Give such a machine a fixed page file of
at least twice its RAM, or close the big programs during a long pass.
Side by side on one GPU: a watching worker and a second one-off worker
(`prax work --steps extract --scope all`) share the model server's slots
(three on the desktop), which is the limit; a second GPU model or a
second model server does not fit next to a 22 GB one on a 24 GB card.

The UI polls `GET /changes` every ten seconds while its tab is visible:
a stamp made of SQLite's `data_version` (another process committed) and
the door's own write count, plus the number of running jobs for the
badge in the navigation. When the stamp moved and a listing is open
(inbox, browse, a document, review, promote, jobs, pages), the view is
rendered again in place, keeping the scroll position and never while
something is being typed. One small query per ten seconds per open tab
is the whole cost.

## 3l½. What runs on its own, and what you run

With the door up and a worker watching (`prax work --watch`, on the
machine with the models), a capture — an upload, a page from the
extension, a file in the drop folder — goes all the way on its own:

| on its own, for every new document | where |
|---|---|
| parse, with the figures found and referenced, ligatures and Symbol-font glyphs turned into letters, page markers, the text cleaned | the parsers, `store.index_text` |
| a title where the file name was one; the graph extraction under the current ontology; the typing rules over what it queued; the vectors | the worker's steps (titles, extract, embed) |
| an image described, and a parsed document's figures read by the vision model | reading requests the door places itself, when the vision step is a local server — nothing is spent unasked; with Claude as the vision model these stay yours to ask for |
| a request placed on a document's page ("read again…") | the worker, before the pending captures |
| a capture nothing here could read — a scan without a text layer: the first extractor refuses it, the fallback finds nothing — is tried once and then left; the inbox says "no text found" and the Health panel lists it (`unreadable-documents`); OCR or the vision model over its pages is yours to ask for on its page | the door, which does not hand a run chain out again |
| jobs whose process is gone closed; the drop folder consumed | the door |

What stays a command or a click, because it costs money, time or a
decision: the promote pass (`prax work --steps promote --spend`, Claude
over the flagged documents), the
model typing pass over the review queue, OCR of scans and the vision
model over whole pages (`vision-pages`), a re-read of the whole library
at once after a parser changes (the `--upgrade` runs above; the nightly
pass below does it a few at a time instead), the ontology migrations,
and the repairs — `prax heal`, or the Health panel at the foot of the
Jobs page, which shows what every ailment finds right now and repairs
the repairable ones with one button (a job; nothing is deleted). The
curated imports' own backlog (Zotero, GitHub, chats) is a worker with
`--scope all` — the nightly one, or `prax work --scope all` now.

## 3l¾. Keeping the library current: what is versioned, and the nightly pass

The code moves and the data has to follow, so everything a pass
produces carries the version of what produced it, and every earlier
result stays addressable:

| what | versioned by | where the history is |
|---|---|---|
| the original | its sha256; it never changes | the archive |
| the text | the extractor's stamp `name/version[-rN][+variant]` in `meta.text_source`; `-rN` is prax's own revision of that extractor, bumped whenever its output changes (the figures it finds, a cleaner reading, page markers) | `meta.parse_history`: every attempt with its extractor, outcome, size, seconds — and the artifact's `text_hash`, so an earlier text is still in the archive under its own hash, never overwritten |
| the readings (an image, a page's figures, whole pages) | the reading model's name in the stamp (`vision/…+server-35b`); readings are additive, a second model's is kept beside the first | the reading itself carries each model's name |
| chunks and vectors | disposable, derived from the text (`chunk_embeddings` says which chunk has a vector from which model); chunks whose text did not change keep their ids and vectors across a re-index | none needed |
| the graph | `ontology_version`, `producer` and `run` on every edge; bi-temporal (`valid_from`, `valid_to`, `ingested_at`) — a better pass ends the old edges and writes new ones, nothing is deleted | the `edges` table is its own history |
| the extraction | `meta.extraction` (extractor, ontology version, run, counts) and `meta.extraction_history` | the same |
| a page | numbered revisions with their author | `page_revisions` |

A document is **stale** when its stamp names an extractor whose revision
prax has moved on since (`parsers.behind`): a re-read would produce
something new, or say `same` and cost only the parse. An annotating
extractor whose addition *is* what a revision added says so
(`Extractor.covers`: `figure-refs` covers `pymupdf4llm` r2 and
`trafilatura` r3), and after it the stamp moves to that revision — the
document is not read again for what it already has. The Health panel
counts the stale ones (`stale-parses`). Four ways to bring a stale
text up to date, from cheapest to dearest:

| way | when | what it costs |
|---|---|---|
| the ailment's repair — "repair" next to `stale-parses` on the Jobs page, or `prax heal --check stale-parses --apply` (a run takes up to 5,000; press again for the rest) | an annotation in the history already made the revision's change (the figure-refs pass over the library ran before stamps moved) | moves the stamp, writes a `stamped` history entry; nothing is read |
| the backlog pass, below | the revision changed what the extractor produces | a re-read per document, a batch a night; `same` when nothing came of it |
| `prax reread --extractor <name> --text-source <old stamp>` — a reading request on every document the old stamp matches, drained by the worker | you want the whole library at the new revision now, not over nights | every document, forced, as the worker gets to them |
| "read again…" on a document's page | one document, now, with the extractor of your choice | that one read |

The backlog pass:

    prax work --scope all --limit 100      # captures first, then a hundred stale ones

In scope `all` the parse step hands out, after the pending captures, the
stale documents oldest first, a batch at a time; the worker re-reads
each with the current extractor and the door keeps or upgrades the text
by the usual rule (a suspiciously short new text keeps the old). A
re-read that comes out the same moves the stamp and touches nothing
else. As the worker's nightly pass on the machine with the models —
`nightly: "03:00"` under `run.worker` (4b), or `prax work --watch
--nightly 03:00` by hand: when that hour comes round each day the
watching worker does one pass over everything, `nightly_limit`
documents a step, then goes back to watching (a worker started after
the hour waits for the next night's; it does not run a pass over
everything at noon). A worker not under `prax up` can use cron:

    0 3 * * * PRAX_TOKEN=<token> /srv/prax/.venv/bin/prax work --scope all --limit 100 --door http://127.0.0.1:8000 >> /srv/prax-data/logs/nightly.log 2>&1

The token is better read from the environment or a file than written
into the task; `--limit` is how much of the night it may take (a
hundred PDFs is a few minutes; readings by the vision model, when the
door asks for them after a re-read found figures, follow in the next
watch cycle). A worker already running `--watch --scope all` needs no
task: the same pass is what it does when the captures are done.

The other steps have their own notion of stale, and the same pass
applies it: `titles` reads the documents whose title is still a file
name; `extract` the documents not yet extracted under the current
version of their ontology subset (never extracted, or extracted before
a module grew) — in scope `all` that is the library's whole extraction
backlog, oldest first, a hundred a night with the local model; `embed`
the chunks without a vector from the current model. `--steps parse,embed`
keeps only the texts and their vectors current and leaves the graph for
a pass you name.

What the pass does not do: it does not re-run extractors that are
explicit only (OCR, `vision-pages`, Docling) — what was asked for once
is not asked for again by itself — and it does not spend money: a paid
model in any step is refused by the worker, whatever the scope.

## 3m. Healing what recurs

Extraction at scale leaves the same few kinds of damage behind, and they
come back with every pass, so they have names and a place:
`prax.store.repair`.

    prax heal                              what is wrong (changes nothing)
    prax heal --apply                      repair all of it
    prax reread --extractor pymupdf4llm-ocr --unreadable     what an ailment offers, from the shell
    prax heal --check self-edges --apply   one kind
    prax heal --json                       for a script

The Health panel at the foot of the Jobs page is the same thing with
buttons: "repair" next to each ailment that can be repaired (one kind,
like `--check`), and one for all of them together. One kind at a time
is the usual way: what you meant to leave alone (a few unnamed entities
you still want to look at) stays alone.

| ailment | what it is | what repairing does |
|---|---|---|
| `placeholder-entities` | a model copied a word out of its own prompt: "source name", "target name", "unknown", "n/a" | ends every edge they carry; the entity stays as the record of what happened |
| `reference-number-entities` | "[12]", "fig. 3", a bare year — the ontology says a reference number is never a name | ends their edges |
| `mangled-names` | a citation importer left markup or line breaks in a title: `<i>The Origins of Music</i>` | cleans the name, or merges into the entity that already carries the clean one |
| `wire-names` | an extractor's own wire syntax glued to a name — `chord dst_type=concept(confidence=EXTRACTED evidence=…`, `x(dst=y)`: a line the model wrote in the triple format, taken whole (1,612 in the library on 2026-09-17, two of them at the top of the likely tier) | cuts the name at the syntax and cleans it, or merges into the entity that already carries it; a name that was nothing but syntax has its edges ended |
| `twin-documents` | two live documents with one title and the same text — a PDF downloaded twice, a book kept in two prints; different bytes, so the hash did not fold them (236 pairs in the library on 2026-09-18) | retires the twin into the keeper (more live edges, then the older one): what it holds and the keeper lacks moves over first (`retire_document` with `duplicate_of`) |
| `unnamed-entities` | no name at all, or a whole citation as one (a claim is a sentence and is left alone) | ends their edges |
| `self-edges` | an edge from a thing to itself, left after two names were merged | ends them |
| `edges-of-retired-documents`, `review-of-retired-documents` | written by a pass that was already reading a document when it was retired | ends them; resolves the queue items as dropped |
| `stale-extractions` | documents whose extraction was made from a text a later read has replaced (marker over a pymupdf4llm text, OCR over a scan) — the graph speaks of a text that is gone | moves the stamp aside so the extract step selects them again; the old reading's edges are retired when the new one is applied. A replacing read does this on the way in now; these are from before |
| `stale-jobs` | a job still marked running whose heartbeat stopped a day ago (the door reaps its own host within minutes) | closes them as failed |
| `unmapped-glyphs` | a text still holding ligature glyphs (ﬁ, ﬂ) or Symbol-font code points (=, ∈, α as private-use characters) from before every text was cleaned on the way in (`prax.glyphs`): boxes on screen, words search cannot match | re-indexes each from its own artifact, cleaned; chunks with unchanged text keep their vectors |
| `extraction-failed` | documents the extract step could not read under the current ontology — a prompt the model's slot cannot hold even after the cut, a server error; the error is kept in `meta.extraction_error` and the passes leave them out until the ontology moves or a reading succeeds | forgets the errors that were the model server's (loading, down, refused) so those are selected again; a prompt no slot holds stays |
| `stale-parses` | documents read by an extractor prax has revised since: a re-read would produce something new, or say `same` | moves the stamp where an annotation in the history already made the revision's change (figure references placed); the rest the backlog pass reads a few at a time, or `--upgrade` at once (3l¾) |
| `documents-without-an-extractor` | something waiting for text of a kind nothing here can read | a report: install what reads it (3b) or retire it |
| `unreadable-documents` | documents every extractor here has tried and found no text in (scans without a text layer); they wait and are not tried again | a report, with two offers on the panel: OCR over all of them, or the vision model over their scanned pages (`prax reread --unreadable --extractor …`); or retire them |
| `chunks-without-vectors` | the current model has no vector for them | a report: run a worker |

What the cleaning does not do: a glyph the PDF's font gave no name at
all comes out of MuPDF as U+FFFD, and a symbol font other than Adobe's
(the F1xx, F2xx private-use ranges) as a code point nobody can read
back — both are lost at extraction, and the UI shows the first as a
small box rather than a question mark. Only OCR or Docling can recover
those, and only sometimes. What the fonts do: the UI's Literata covers
Latin; Greek, Cyrillic, maths and CJK fall through to the reading
stack's next faces (STIX Two Text, Georgia, Cambria) and the system's
own, so a symbol that is a real character renders; a box that survives
the heal is one of the two lost kinds above.

Nothing is deleted. An edge is invalidated, so it keeps its provenance
and its place in history (invariant 8) and a later pass can write the
right one; a review item is resolved as dropped; a job row is closed. The
pass announces itself as a job, and `GET /heal` is the looking half of
`POST /heal`, so the UI and a cron line see the same thing the command
does.

Adding an ailment is a `find` (what is wrong, as rows a person can read),
optionally a `repair`, and an entry in `AILMENTS`. The rule this module
lives by: **look before repairing**. The first draft of
`unnamed-entities` flagged every name over 200 characters and would have
thrown away 27 real claims and 94 real citations; the dry run against the
library is what caught it, which is why the dry run is the default.


## 3n. Maintenance: what the store does to itself

A few tables are derived from the rest and drift unless they are
rebuilt; none of that needs a model or a decision, so it is one pass,
a job on the door, run by the nightly task after the worker's pass and
by `prax maintain` on request:

    prax maintain                      # every pass
    prax maintain --only acronyms      # one

| pass | what it rebuilds |
|---|---|
| `acronyms` | the acronyms table from every text's "phrase (ACRONYM)" definitions (3d): what a query token expands to; minutes over a large library |
| `fields` | the document retrieval field (title, kind, summary) of every document — after titles were fixed or summaries written |
| `domains` | the domain set of every document nobody assigned by hand, from the `domains:` rules in `prax.yaml` (3e); nothing without rules |
| `dedupe` | the duplicate captures of one page retired as duplicates of the keeper — a union: their facts, tags, domains and stamps join the keeper's first (3l); row and file kept |
| `review` | the review queue: a replay against the current ontology (a typed item it accepts now becomes an edge), then the typing rules over every open item (3e) — what the door does for one document after its extraction, for the whole queue |
| `rechunk` (only with `--rechunk`) | every chunk rebuilt from its text artifact, after a change to the chunker (3c); the nightly has no reason to |

What stays out on purpose: the repairs (`prax heal`, 3m — a person picks
the ailment), the readings and extractions (the worker, with a model),
entity resolution (the likely merges are a decision). `POST /maintain
{only}` starts the job; the Jobs view shows which pass it is on.

## 4. Running the HTTP door

    uvicorn prax.api:app --reload --port 8000

### Access

The door checks one shared secret, `PRAX_TOKEN`, on every request except
the UI's static files and `/health`. Generate one and set it in the
service's environment:

    python -c "import secrets; print(secrets.token_urlsafe(32))"
    $env:PRAX_TOKEN = "<the token>"          # PowerShell
    export PRAX_TOKEN=<the token>             # shell

Scripts and the extension send `Authorization: Bearer <token>`; an
extension also needs its origin in `PRAX_CORS_ORIGINS` (comma-separated,
`docs/extension.md`), unset otherwise. The UI
asks for the token once and exchanges it for an HttpOnly session cookie
(`POST /session`, 30 days, `DELETE /session` to end it), so links to
originals work in new tabs. Without `PRAX_TOKEN` the door admits
loopback clients only, which is what a development server needs and what
keeps a misconfigured deployment closed. On the serving host bind the
service to the private network's interface (`--host <that address>`:
the LAN, a VPN address; Tailscale's `100.x.y.z` is one example) and
never to a public one; plain HTTP inside the private network is fine,
TLS through a reverse proxy only if the door were ever exposed. The MCP
server talks to the door with the same token.

Endpoints:

| Method | Path | Body / params | Returns |
|---|---|---|---|
| POST | `/ingest` | JSON `{text, title?, source_url?}` | `{doc_id, hash, created}` |
| POST | `/ingest/file` | multipart `file`, form `title?`, `source_url?` | `{doc_id, hash, created}` |
| GET | `/get/{doc_id}` | `offset?`, `max_chars?` | document row plus text |
| GET | `/search` | `q`, `limit?`, `kind?`, `mode?` | list of `{chunk_id, doc_id, title, snippet, score, kind, heading, page, figure}` (`figure`: a figure chunk's image reference, served by `/doc/{id}/figure/{ref}`) |
| GET | `/chunk/{chunk_id}` | | one chunk: text, kind, heading, locator, table `data` |
| POST | `/link` | JSON `{src, src_type, rel, dst, dst_type, confidence?, source_doc?}` | `{edge_id}` |
| GET | `/traverse` | `entity`, `hops?` (max 2) | list of edges with types and hop distance |

The web UI is served by the same process at `http://127.0.0.1:8000/ui/`
(`/` redirects there): search, document and browse views; the graph view
follows Stage 3. It needs no build step; the files live in `src/prax/ui/`.

A first smoke run from PowerShell:

    Invoke-RestMethod -Method Post http://127.0.0.1:8000/ingest `
      -ContentType application/json `
      -Body '{"text": "Granular synthesis smears transients.", "title": "note"}'
    Invoke-RestMethod "http://127.0.0.1:8000/search?q=granular"

### From another machine on the private network

A door started with the defaults is invisible from the next room, by
design twice over: `prax serve` binds `127.0.0.1`, so the LAN gets no
listener at all (a browser on another machine waits and times out), and
without `PRAX_TOKEN` the door admits loopback clients only, so it would
answer 401 even if it heard them. Opening it takes three things, on the
machine that runs the door:

1. A token in the door's environment, and the same one in the worker's,
   since the worker is a client too:

        $env:PRAX_TOKEN = "<the token>"        # PowerShell; generate one as above
        prax serve --host 0.0.0.0 --port 8000  # or --host <this machine's private address>
        prax work --watch --interval 20        # in another shell, same variable

   `0.0.0.0` means every interface of the machine; a home LAN behind
   the router is fine, and the token is what gates it. On a machine
   with a public interface, bind the private address instead.

2. A firewall rule for the port. Windows asks on the first bind when
   the door runs in a console; started hidden it does not, and the rule
   needs an administrator's PowerShell:

        New-NetFirewallRule -DisplayName "prax door" -Direction Inbound `
          -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private

   (`-Profile Private` keeps it to networks marked private; check the
   current network's profile with `Get-NetConnectionProfile`.)

3. The token on the other machine: the web UI at
   `http://<address>:8000/ui/` asks for it once and keeps a session
   cookie; the `prax` command takes `--door http://<address>:8000` and
   `PRAX_TOKEN`; the MCP server and the Claude Code plugin read
   `PRAX_DOOR` and `PRAX_TOKEN` (`docs/claude-workflow.md`); the browser
   extension has both in its options (`docs/extension.md`).

A check from the door's own machine that does not go through loopback —
its LAN address instead — proves the bind and the token together:

    curl -s -o /dev/null -w "%{http_code}\n" http://<address>:8000/search?q=x                       # 401
    curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer <token>" http://<address>:8000/search?q=x   # 200

What the door's log says (`logs/door.err.log` on the batch host): a
refused request is one line, `refused GET /search from 192.168.178.23:
missing or invalid token` — the client and the reason; and uvicorn's
`Invalid HTTP request received` is a client speaking TLS to the plain
HTTP port — a URL typed with `https://`, a browser's HTTPS-first
attempt before it falls back (harmless), or an extension whose
manifest lets Firefox's default MV3 policy upgrade its fetches
(`docs/extension.md`; prax's manifest sets its own policy without
`upgrade-insecure-requests` for that reason).

Anything that talked to the door without a token before — a local
script, this session's `curl` — needs the header from then on;
`/health` and the UI's files stay open.

**HTTP or HTTPS.** Plain HTTP inside the private network is the design:
what travels is the token and the documents, and the private network
is what keeps them private — a home LAN behind the router, or a VPN
(Tailscale encrypts the wire itself, so plain HTTP over a `100.x.y.z`
address is already private). HTTPS on the door is a certificate
problem, not a code one: the door serves it with

    prax serve --host 0.0.0.0 --ssl-certfile cert.pem --ssl-keyfile key.pem

(the session cookie becomes `Secure`), but a certificate for a bare
LAN address has to be one every client trusts — a self-signed one
means a warning in the browser and, worse, a silent failure in the
extension, which cannot click through. The two ways that work: a
private CA whose root is installed on every client (mkcert, or Caddy
in front of the door with `tls internal`; Firefox needs
`security.enterprise_roots.enabled` or the root in its own store), or
a real certificate for a real name — `tailscale cert <name>.ts.net`
issues one for the machine's Tailscale name, which is the clean
answer when the board is reached that way. Neither is worth doing for
a LAN with only your own devices on it; either is, the day the door is
reachable from a network you do not run.

## 4a. The `prax` command

One command for the everyday work, and the same one wherever the door is:

    prax                              where things stand, and what to type
    prax search granular synthesis    find documents
    prax ask --answer how does a feedback delay network work
    prax ask --answer --steps 12 which methods extend ADAA, and who proposed them
    prax add ~/Downloads/paper.pdf --domain research
    prax add https://example.org/article
    prax import links bookmarks.html  what a service or an app exported
    prax show 4312                    read one in the terminal
    prax open 4312                    …or in the browser
    prax status                       the store, the graph, this host
    prax inbox                        what came in, what still waits
    prax jobs                         passes running now and lately
    prax heal                         what recurring damage is in the store
    prax maintain                     what the store does to itself: acronyms, fields, domains, duplicates
    prax reread --extractor X ...     a reading on a selection: --unreadable, --mime, --text-source, --ids; --wait
    prax readings                     the reading queue per extractor; --wait blocks until it drains
    prax backup D:/prax-backup        copy the store (only what is new)
    prax work --watch                 be the worker for a door
    prax serve                        run the door here
    prax doctor                       when something feels wrong
    prax models                       which model does which step
    prax graph "wave digital filter"  what the graph knows around a name
    prax pages                        the notes kept in the library

It is a client (`clients/cli/`, `prax.client`): every command is one HTTP
call, nothing opens the database, and `--door` (or `PRAX_DOOR`) points it
at another machine — `prax --door http://board:8000 status` from the
desktop, `prax work --watch --door http://board:8000` to drain that
board's queue with this machine's models. `PRAX_TOKEN` or `--token`
carries the bearer token when the door asks for one. Every read command
takes `--json` for a script to parse, and colour goes away when the
output is piped.

`prax work` is `scripts/work.py` under a shorter name, and `prax serve`
is the uvicorn line. The one-off maintenance scripts (import, backfill,
resolution, typing rules, rechunk, replay) stay scripts: they open the
database directly and are the known deviation of invariant 4.

## 4b. Always on: `prax up`

Until the store moves to a board (6), the desktop is the server, and a
server survives a reboot — and, as it turned out, a closed terminal.
prax owns its process model: `run:` in `prax.yaml` names which of prax's
roles this host runs and with what, and `prax up` keeps them running.
The operating system's only job is to start `prax up` when you log in
and start it again if it vanishes; the same code does the same thing on
Windows, Linux and macOS, and the same tests cover it there.

    run:
      llama-server: {model: server-35b}      # a models: entry with a serve: block (3h)
      door:         {host: 0.0.0.0, port: 8000}
      worker:       {interval: 20, nightly: "03:00", nightly_limit: 100}
    schedule:
      maintain: "03:30"
      backup: {at: "04:30", archive: false}  # to paths.backup

    prax up                  # here, in this terminal; ctrl-c stops everything in order
    prax up -d               # detached: survives this terminal
    prax up --status
    prax up --restart door   # after a code change; --restart all
    prax up --stop llama-server   # that one stays stopped: the card free for a while
    prax up --start marker        # a role declared on_demand starts only when asked
    prax up --start llama-server  # and back (a --restart of a stopped role starts it too)
    prax up --stop           # everything, in order
    prax up --install        # start at login; --uninstall removes the entry

| role | what | waits for |
|---|---|---|
| `llama-server` | llama-server for the model named, from its `serve:` block (3h) | — |
| `reranker` | a second llama-server with a cross-encoder (`serve: {reranker: true}`) | — |
| `marker` | marker's server from its own venv, the PDF-to-LaTeX reading (3h); `on_demand: true` declares it without starting it | — |
| `door` | `prax serve --host … --port …` (`ssl_certfile`, `ssl_keyfile` for HTTPS) | — |
| `worker` | `prax work --watch`, with `--nightly` for one bounded pass over everything a day | the door, unless `door:` names one elsewhere |

What `prax up` does: starts the roles in that order behind real health
gates — the worker once the door answers `/health`; a model server shows
*starting* until its own `/health` says loaded, three minutes cold for
the 35B — restarts what dies after 1, 2, 4 … 60 seconds (a run of five
minutes starts the count over), stops them in reverse order (`SIGTERM`
elsewhere; on Windows a process without a console can only be ended,
which is safe for prax: the database is in WAL mode, the archive is
content-addressed, every job is re-selectable), and writes each role's
output to `<data dir>/logs/<name>.log` — rotated on every start, ten
kept — and its own lines to `up.log`. It has no port and no state: a pid
file and a status file under `<data dir>/run/`, and a command file that
`--stop` and `--restart` write, which is what works the same everywhere.
Children get no console on Windows (and sit in a job object that ends
them if the supervisor itself is killed) and a session of their own
elsewhere, so nothing a terminal does reaches them.

The timed passes are not the supervisor's. `maintain` and `backup` run
on the door's own clock — `schedule:` — as the jobs their endpoints
start, visible on the Jobs page (`door.clock_seconds` says how often the
clock looks, 30 by default); the jobs table is the memory, so a door
restarted at noon does not run the night again, one that was down at the
hour catches up once, and a pass still running is left alone. The
worker's nightly backlog pass (3l¾) is its own: `nightly:` under
`run.worker`, or `prax work --watch --nightly 03:00` by hand. Give the
worker's pass a head start on the maintenance.

The login entry: `prax up --install` writes one — a Task Scheduler task
on Windows (`\prax\prax up`, run by `pythonw.exe`, which never has a
console: a console program started by Task Scheduler gets a window for a
moment, Windows 11 hands a windowed console to Windows Terminal, and
closing that window is how three services once ended together with
`0xC000013A`), a systemd user unit on Linux
(`~/.config/systemd/user/prax.service`, `Restart=always`; `loginctl
enable-linger $USER` keeps it running without a session), a launchd agent
on macOS (`~/Library/LaunchAgents/io.github.lodsb.prax.plist`, `KeepAlive`).
Under your own account, nothing system-wide, no password stored: the
services run while you are logged on. Secrets never go into the entry:

| | where it comes from |
|---|---|
| the token | `PRAX_TOKEN` in the environment (on Windows the *user* variable), or one line in `<data dir>/door.token`; without one the door answers this machine only |
| the Anthropic key | `ANTHROPIC_API_KEY` in the environment; where the login entry has no user environment (systemd, launchd), `KEY=value` lines in `<data dir>/up.env`, which every child gets |
| the card's power cap | `nvidia-smi -pl` needs an administrator: a task of your own |

Before 2026-09-16 two shell scripts did this per platform, five services
each; if they installed anything, remove it first — Windows:
`Get-ScheduledTask -TaskPath \prax\ | Unregister-ScheduledTask`, Linux:
`systemctl --user disable --now prax-{door,worker,llama-server}.service
prax-{nightly,backup}.timer`, macOS: `launchctl bootout
gui/$UID/io.github.lodsb.prax.<name>` for each — then `prax up --install`.
Found on the way and worth knowing: Task Scheduler's "restart on failure"
is about a task it could not launch, not one that ended (a task exiting 1
with three restarts a minute apart was never run again), so the old
"comes back after a crash" was never true on Windows; `prax up` is where
restarts live now, and they are tested.

## 5. MCP server in Claude Code

`.mcp.json` in the repo root registers the server. Claude Code runs the
command with the project root as the working directory, so the interpreter
path is relative:

    "command": "${PRAX_PYTHON:-.venv/Scripts/python.exe}"

On Linux or macOS set `PRAX_PYTHON=.venv/bin/python` in the shell that
launches Claude Code. Claude Code reads `.mcp.json` at startup only, so
restart it after editing the file.

The server is a proxy: every tool is one HTTP call to the door
(`prax.client`), the process imports no store module and opens no
database (CLAUDE.md invariants 4 and 5). So the door has to be running,
here or on the board: `PRAX_DOOR` names it (default
`http://127.0.0.1:8000`) and `PRAX_TOKEN` is sent when the door asks for
one; `.mcp.json` passes both through from the shell that launches Claude
Code. A tool called while the door is down answers `{"error": "the door
is not reachable ..."}` rather than failing. Tools: `search`, `get`,
`get_chunk`, `context`, `documents`, `traverse`, `link`, `ask`,
`set_domains`, `promote`, `get_page`, `write_page`, `append_page`,
`ingest`, `capture_url`, `ingest_file` (a file on the machine running
Claude Code, uploaded to the door). What the agent writes is stamped
`agent` (edges' producer, pages' author, domain sets, promotions).

For every other project, the plugin (`clients/claude-plugin/`,
`docs/claude-workflow.md`) registers the server at user scope and adds
the skill that says when to use it, the commands `/prax:scope`,
`/prax:research`, `/prax:remember`, `/prax:sync`, `/prax:archive`, and
a session-end hook that syncs a project's docs (and, when asked, its
sessions and memory files):

    export PRAX_PYTHON=/path/to/prax/.venv/bin/python
    claude plugin marketplace add lodsb/prax
    claude plugin install prax@prax

## 6. Deployment on the board (*the code is ready; the move is not made*)

The board holds the store and runs the door; the machine with the GPU
does the model work through it (howto 3l). Nothing else has to move.
`deploy/` holds the pieces — an install script, the systemd unit, the
board's `prax.yaml` — and its README is the step-by-step; this section
is the reasoning. The desktop's side is `run:` and `prax up` (4b).

**On the board.** Copy the data directory over (section 7), then:

    pip install "prax[serve]"        # the core plus vectors; no parsers, no models
    export PRAX_DATA_DIR=/srv/prax   # the SSD, never the SD card
    export PRAX_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    prax serve --host <its address on your private network> --port 8000

as a systemd unit with those two variables in its environment (the
board runs one process, so systemd keeps `prax serve` itself alive;
`prax up` is for a host with more than one). In the board's `prax.yaml`:

    vectors:
      dtype: i8        # half the file, recall 0.93: the memory a board has
    steps:             # nothing local answers here
      ask: {model: none}
      titles: {model: none}
      extract: {model: none}
    schedule:          # the door's own clock: maintenance nightly, a backup when there is a disk
      maintain: "03:30"

An index written as `f16` stays `f16`: the setting takes effect when the
vectors are written, so re-embed into a fresh file (remove the old
`vectors-*.usearch` with the door stopped, start it with the setting, and
let a worker embed) or copy the desktop's file and accept its precision.

**On the machine with the models**, pointing at the board:

    prax --door http://<board>:8000 --token <the token> status
    prax work --watch --door http://<board>:8000 --token <the token>

or, kept running: `run: {llama-server: {model: …}, worker: {door:
http://<board>:8000}}` in the desktop's `prax.yaml` and `prax up
--install` (4b).

**In Claude Code**, `PRAX_DOOR=http://<board>:8000` and `PRAX_TOKEN` in
the shell that launches it; the MCP server is a proxy and needs nothing
else (section 5).

**The browser extension** points at the same address, and the board's
`door.cors_origins` lists the extension's origin.

What is still to do on the board itself: put the service file in place,
measure the door's memory there (invariant 7's gigabyte), and decide
whether the vector index is `i8` or a copy of the desktop's `f16`.

## 7. Backup and moving the store

Everything is three things: one SQLite file, a few index files, and one
directory of content-addressed files. `prax backup` copies them:

    prax backup D:/prax-backup       # a directory on the door's machine
    prax backup                      # paths.backup in prax.yaml [PRAX_BACKUP]
    prax backup I:/prax-db --no-archive   # the database, indexes and config only

The door does the copying (`POST /backup`, a job you can watch in `prax
jobs` and the Jobs view): the database through SQLite's online backup —
one consistent snapshot, the WAL folded in, writers not blocked — then
the `vectors-*.usearch` files and `prax.yaml`, then every archive file
the copy does not have yet. Archive files are immutable and named by
their hash, so the second run costs what the day added, and an
interrupted copy is simply resumed by the next one. A `backup.json`
manifest records what was copied and when.

The copy is a store: `PRAX_DATA_DIR` pointed at it opens it, on this
machine or another. The delta vector index is copied as it stands, so a
vector written between the snapshot and the file copy may be missing
there; `prax heal` finds those (`chunks-without-vectors`) and a worker
re-embeds them. Model files are not copied (`prax models fetch`).

`--no-archive` copies what cannot be rebuilt — the database, the
indexes, the config, a few gigabytes — and leaves the originals out:
for a disk too small for them. The manifest says so, and a full run to
the same directory later adds the archive. (The originals of a Zotero
library are also in Zotero; captured pages and uploads are not
anywhere else, so the full copy is the real backup once there is a disk
for it.)

A nightly copy is `backup:` under `schedule:` in `prax.yaml` — the
door's own clock runs it as a job (4b); Litestream replication is on
the later list.

## 8. Adding a source

Most sources are a client of the door (`sources.md` 6): what a service
or an app exported, sent through `POST /ingest` or `POST /ingest/url`.

    prax import github octocat --domain workshop        # someone's stars
    PRAX_GITHUB_TOKEN=… prax import github              # your own, 5,000 requests an hour
    prax import chat "Telegram Desktop/result.json" --links
    prax import chat signal/*.json                      # sigtop export-messages -f json
    prax import links bookmarks.html pocket.csv medium-export.zip
    prax import links reading.txt --dry-run             # what would be added
    prax import project ~/work/synth --domain workshop   # a project's docs, keyed by path
    prax import claude ~/work/synth --since 2026-09-01   # its Claude Code sessions, words only

Every run skips what the library already holds (by key, or by URL) and
`--refresh` re-reads what changed at the source. A new source of this
kind is a reader in `prax.importers` that yields `feed.Item`s — text of
its own with a key and a version, or a link — and a line in
`clients/cli/prax_cli/importing.py`; `feed.run` does the rest.

A source that must open something on the door's host (the Zotero
importer, the backfill) is a client of `prax.store` instead:

1. Obtain original bytes and whatever metadata the source has.
2. `register(...)`: archive, insert the row. Dedupe is automatic by hash.
3. If text is already available, call `index_text(...)`. Otherwise leave
   `parsed_at` NULL and let the parse queue pick it up.
4. Seed graph edges from metadata with `link(...)`, `confidence="EXTRACTED"`
   and `source_doc` set.
