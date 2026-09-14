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
queue (`scripts/replay_review.py`, section 3f) before extracting anything
new; the bump also re-selects every document for extraction. The reasoning
behind v2 is in `docs/ontology-v2.md`; the studio module (gear, manuals,
datasheets, magazine articles) in `docs/ontology-studio.md`. A document
is extracted against the modules of its domain set (section 3e, "A
document's domains"), so a new module re-selects only documents without
a set.

## 3a. Importing the Zotero library

The importer never opens the live `zotero.sqlite`; it copies the file into
`<data dir>/zotero-import/` and opens the copy read-only. Everything goes
through `prax.store`. Details of the mapping: `docs/sources.md` §1.

    # read-only census; nothing is written. --hash adds sha256 dedupe (reads every file)
    python scripts/import_zotero.py /path/to/Zotero --dry-run
    # trial run, then the whole library. Re-runs skip what is already imported.
    $env:PRAX_DATA_DIR = "C:\prax-data"
    python scripts/import_zotero.py /path/to/Zotero --commit --limit 500
    python scripts/import_zotero.py /path/to/Zotero --commit --quiet

The test fixture in `tests/fixtures/zotero/` is regenerated with
`scripts/make_zotero_fixture.py` (the exact command is in its docstring).

## 3b. Parse queue

Extractors live in `prax.parsers` (see its docstring for the table) and are
picked by MIME type in registry order, falling back to the next one when
one raises. The queue records every attempt in `meta.parse_history` and the
winner in `meta.text_source` as `<name>/<version>`, so any pass can be
redone later with `--upgrade <prefix>`. An upgrade keeps the old text when
the new one is suspiciously short (login walls, scans without OCR) unless
`--force` is given. Runs on the desktop, never on the serving host.

    # documents never indexed (registered by an importer or the inbox)
    python scripts/parse_pending.py --pending
    # re-extract everything Zotero's cache produced, HTML first
    python scripts/parse_pending.py --upgrade zotero-ft-cache --mime text/html
    # scans: OCR is explicit and bounded (PRAX_OCR_MAX_PAGES, default 60)
    python scripts/parse_pending.py --pending --extractor pymupdf4llm-ocr
    # scans in another script: the recognizer is a setting (parse.ocr_language:
    # ch reads Chinese and English; en, latin, arabic, cyrillic, devanagari,
    # japan, korean, el, th...) and part of the stamp, so a book read with the
    # wrong one has not been read with the right one; --title picks them out
    PRAX_OCR_LANGUAGE=arabic python scripts/parse_pending.py --title "In Arabic"         --extractor pymupdf4llm-ocr --force

For a script the OCR font cannot write back into the page (Arabic,
Devanagari, Tamil, Telugu, Thai, Georgian — pymupdf4llm writes what it
recognized with Droid Sans Fallback, which has Latin, Greek, Cyrillic and
CJK), prax runs the recognizer itself and writes the lines as text in
reading order (right to left for Arabic), page markers included; no
layout analysis, which a scanned book rarely has to give.

    # Docling on a hand-picked set
    python scripts/parse_pending.py --ids 12 34 --extractor docling --force

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

    python scripts/parse_pending.py --ids 8605 --extractor vision-pages --force
    # every page, for printed pages with notes in the margin
    PRAX_VISION_PAGES=all python scripts/parse_pending.py --ids 8605 --extractor vision-pages --force

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
    python scripts/parse_pending.py --ids 9706 --extractor figures --force

or "read again… → figures" on the page. A PDF image no caption claims
(`Figure on page N`) is read only with `parse.figures: all`
(`PRAX_FIGURES=all`): many of those are decoration. The reading goes under the
image line as `*Figure, as read by <model>:* …` (a model reads a figure
once; another model's reading joins it), and the figure chunk carries
it — findable, and read by the extraction. The parsers find
figures as they read (`trafilatura` r3, `pymupdf4llm` r2); the
retroactive pass over a library parsed before that is `figure-refs`,
which puts the original's figures into the current text without
re-reading the pages (pymupdf4llm's layout analysis takes 10–30 s a
document; this takes a fraction of a second):

    python scripts/parse_pending.py --upgrade trafilatura/2.2.0-r2 --mime text/html   # 284 pages: 2 min
    python scripts/parse_pending.py --upgrade pymupdf4llm/1.28.2 --extractor figure-refs --force

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

Long passes over thousands of PDFs should run as a loop of short-lived
processes: MuPDF's layout analysis grows the process over time, and one
run over 5,000 documents was killed for memory. Every invocation
re-selects what is left, so batching costs nothing:

    for ($i = 0; $i -lt 40; $i++) {
      python scripts/parse_pending.py --upgrade zotero-ft-cache --limit 250 --quiet
    }

Originals above `PRAX_MAX_LAYOUT_MB` (default 40) skip layout analysis and
get plain text through the fallback.

Code is kept as code. HTML pages come out as Markdown with `<pre>` blocks
fenced (trafilatura's Markdown output). Text attachments that are source
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

    python scripts/parse_pending.py --upgrade trafilatura --mime text/html
    python scripts/parse_pending.py --upgrade plain --mime text/plain
    python scripts/embed_pending.py --compact      # vectors for the new chunks

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

    python scripts/parse_pending.py --pending --mime image/ --extractor vision

The stamp records the model (`vision/1+<model>`); `claude-vision` is the
same extractor pinned to Claude, the name the first descriptions carry.
The document view shows an image inline above its description. An image
that matters gets the expensive reading the way a paper does: promote it
(section 3e), and `extract_graph.py --promoted` describes it again with
the promote step's model (Sonnet here) before extracting from that.
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

    python scripts/rechunk.py --all       # every indexed document
    python scripts/rechunk.py --legacy    # only rows that have no kind yet

Chunks are disposable; nothing else is touched.

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

    python scripts/embed_pending.py --dry-run     # counts
    python scripts/embed_pending.py --batch 64    # everything pending; idempotent
    python scripts/embed_pending.py --compact     # reconcile only, no embedding

Stop the HTTP door before an embedding run on Windows: the door keeps the
index file memory-mapped and the save (an atomic rename) is refused while
it is open (`PermissionError` on the `.tmp` file). Re-parsing documents
(a new extractor revision, Docling on a few) creates new chunks that need
a run afterwards; `--compact` alone drops stale keys without embedding.

The job saves the index every 50,000 chunks and reconciles index and
bookkeeping on start, so an interrupted run is simply started again. Copy
both `.usearch` files together with `prax.db` when moving the store.

Settings: `PRAX_EMBED` (model name, `hash` for tests, `0` off),
`PRAX_EMBED_VARIANT` (`int8` by default everywhere: measured 2026-09-12
on 1,024 real chunks, CPU int8 23 chunks/s, CPU fp32 17, DirectML fp32
45, DirectML int8 80, so int8 is the faster one on both and keeps the
store's vectors from one variant), `PRAX_EMBED_PROVIDERS` (DirectML
first when the runtime offers it), `PRAX_EMBED_THREADS`; `PRAX_VEC_DTYPE` (`f16`
default, `i8` for half the file at recall 0.93) and `PRAX_VEC_EF` (search
expansion, 64) for the index. Changing the model means re-embedding into
a new file: `chunk_embeddings.model` records what each vector came from
and `embed_pending.py` picks up the difference.

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

    python scripts/refresh_document_fields.py
    python scripts/embed_pending.py            # chunks, then document fields

Why: chunk scoring finds documents *about* a term, not the document that
*is* the thing; "schematic" put a CAD manual first and the one schematic
nowhere (`docs/eval/retrieval-field-2026-09-10.md`).

### Acronyms

Papers define their acronyms in the text ("antiderivative antialiasing
(ADAA)"). `scripts/build_acronyms.py` collects those definitions from
every text artifact into the `acronyms` table (migration 0008; 5,887
pairings from the library, 1,826 defined by two or more documents), and
the search expands a query token that is a known acronym to its phrase
on the keyword side as a phrase match (the embedder sees the query as
typed: expanding it measured worse). Re-run the
script after a large import; `--dry-run` shows the top pairings.

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

    # credentials: ANTHROPIC_API_KEY, or `ant auth login`
    python scripts/extract_graph.py --dry-run             # selection and cost estimate
    # --min-chars 500 (default) skips notes and empty scans; --mime narrows the type
    python scripts/extract_graph.py --limit 20            # trial, synchronous
    python scripts/extract_graph.py --submit-batch        # whole selection at half price
    python scripts/extract_graph.py --collect-batch <id>  # apply when the batch has ended

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
written before migration 0007 are tagged once with

    python scripts/backfill_provenance.py

Run it again after any job that was started before the migration ends.

### Entity resolution

    python scripts/resolve_entities.py --dry-run               # both tiers listed
    python scripts/resolve_entities.py --commit --no-embed     # sure merges only
    python scripts/resolve_entities.py --commit --twins --no-embed  # concept+method twins
    python scripts/resolve_entities.py --commit --adjudicate   # Claude decides the rest

Sure merges are equal names after normalization (case, accents,
punctuation, plural, suffixes) and author initials forms that abbreviate
exactly one full name; they need no model. Likely merges are close by
name embedding, for concepts, methods, tools, datasets and venues only,
and merge nothing unless an adjudicator says yes. A merge sets
`entities.canonical_id`; nothing is deleted, `traverse` and the UI follow
the pointer, and undoing one is clearing that column.

### Promoting documents to the expensive model

The local pass reads everything once; the papers you work with deserve
the richer pass (claims, relations between methods). A document is
flagged in `meta.promote` from its page ("promote"), from the Promote
view's candidates (scored by project membership, synthesis sources,
notes on the document and citations from other library documents), by
Claude Code through the `promote` MCP tool, or by the store itself when
the document joins a project or becomes a synthesis source. The flag
queues; nothing is spent until the pass runs:

    python scripts/extract_graph.py --promoted --dry-run
    python scripts/extract_graph.py --promoted                  # the promote step's model
    python scripts/extract_graph.py --promoted --submit-batch   # Claude at half price

The model is the `promote` step in `prax.yaml` (section 3k; default
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

    python scripts/assign_domains.py --dry-run        # counts per rule
    python scripts/assign_domains.py --commit         # documents without a set
    python scripts/assign_domains.py --commit --force # re-assign rule-set ones too

A set written by hand ("domains…" in the document page's action row,
`PUT /doc/{id}/domains`, `POST`/`DELETE /doc/{id}/domains/{name}`, the
`set_domains` MCP tool) is never overwritten by the rules. Adding a
domain keeps the others; removing the last one puts the document back in
every module. A re-run for one domain reads the documents assigned to
it that are not yet stamped with their subset's version:

    python scripts/extract_graph.py --domain family --dry-run
    python scripts/extract_graph.py --domain family
    python scripts/extract_graph.py --promoted --domain research

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
moved onto the device the document describes). `scripts/type_review.py` applies
the rules in `prax.review.apply_typing_rules` to every open typed item:

    python scripts/type_review.py --dry-run    # counts per rule, nothing written
    python scripts/type_review.py --commit

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

    PRAX_TYPING=server-35b python scripts/type_review.py --model --dry-run --limit 240
    PRAX_TYPING=server-35b python scripts/type_review.py --model --commit --workers 3

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

    python scripts/import_citations.py --dry-run
    python scripts/import_citations.py --commit --source crossref
    python scripts/import_citations.py --commit --source crossref --resolve-titles

### Review queue and ontology growth

The review view (`#review`) filters by relation and by unmapped versus
typed items, drops all matching items in bulk, and replays typed items
against the current ontology; the same is available as
`scripts/replay_review.py`. The proposal for ontology v2, built from the
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
    # a GGUF model into the data directory (or anywhere):
    python scripts/fetch_model.py server-35b       # repo and file from prax.yaml
    # the server (Windows; other hosts run llama-server with the same flags):
    scripts/llama_server.ps1 -Model <data dir>/models/<file>.gguf -Slots 3 -NoThinking
    # a vision-language model also describes images when its projector is loaded
    # (the mmproj-*.gguf in the model's repository, ~1 GB; Qwen3.6 has one); on
    # a card that also drives the display, leave it 3-4 GB (the script's header
    # has the measurements: -CpuMoe 2 frees 0.7 GB for a tenth of the speed)
    scripts/llama_server.ps1 -Model <file>.gguf -Mmproj mmproj-F16.gguf -Slots 2 -CpuMoe 2 -UBatch 256 -ImageMaxTokens 1024 -NoThinking

Then in `prax.yaml`:

    models:
      server-35b: {kind: openai, base_url: http://127.0.0.1:8080/v1, model: <name the server reports>}
    steps:
      extract: {model: server-35b}
      titles:  {model: server-35b}
      ask:     {model: server-35b}

The grammar-constrained line format (`prax.lineformat`) needs a server
that honours the `grammar` field: llama-server does, vLLM does not.
`-NoThinking` matters for models that think by default (Qwen3.x, Gemma
4): thinking tokens would break the grammar. Memory on a Windows host:
howto 3l, "Jobs". An 8 GB card fits a 7-8B model at Q4 with an 8 K
context; a board without a usable GPU leaves the steps at `none` or
points them at a server elsewhere on the private network.

**One card, several jobs.** The same loaded model serves extraction,
titles, ask and — with its projector — images, so one server is the
whole local side; two *different* models on one card are sequential:
llama-server's router mode (`--models-dir` or `--models-preset`, with
`--models-max 1`) loads the model a request names and unloads the other,
at the cost of a reload (10–30 s for 22 GB) each time the job changes.
A small second model fits beside the big one: the reranker below (0.6
GB) ran next to the 35B (23.9 of 24.5 GB).

**Context per slot.** `-c` is split evenly over the slots, so the 3 × 8 K
default gives a document 8 K; a text whose script tokenizes densely
(Arabic at about a token per character) overruns it at prax's 16 K-char
budget. For those, a run with `-Slots 1 -CtxPerSlot 24576` and
`extract_graph.py --ids …` is the way (three books, 2026-09-13).

**Load figures.** `--metrics` (on by default in the script) exposes
Prometheus text at `/metrics`; the door's `GET /models/servers` reads it
with `/props` for every `openai` model in `prax.yaml`, and the Jobs page
shows each server: model file, slots, whether it sees images, requests
running and waiting, tokens per second, tokens read since the start and
how many of them came from the prompt cache.

**A reranker in llama-server.** `-Reranker` starts the same binary with
a cross-encoder GGUF (`--reranking`, one slot, its own port) and
`rerank: {model: server, url: http://127.0.0.1:8081}` in `prax.yaml`
(or `PRAX_RERANK=server`) rescores the top hits through it, 92 ms for
ten candidates on the GPU:

    scripts/llama_server.ps1 -Model bge-reranker-v2-m3-Q8_0.gguf -Reranker -Port 8081

Measured 2026-09-13 on the library's 62 queries (`docs/eval/rerank-server-2026-09-13.md`):
bge-reranker-v2-m3 at depth 10 scores hit@1 0.74 / MRR 0.83 against
0.85 / 0.90 for the fused list alone — paraphrase and structure queries
gain a little, keyword queries lose a lot, as with the ONNX rerankers in
2026-09-08. Reranking stays off; the route is there for a
document-aware candidate (title, heading path, chunk) later.

## 3i. Ask: questions answered from the library

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

## 3j. Titles worth the name

Half the imported titles were file names (standalone Zotero
attachments come as `<md5>-slides.pdf`, items without metadata as
`Unknown - 2002 - No Title.pdf`) and conference papers arrive in ALL
CAPS. A title is what a search hit, a citation in an answer and a
`paper` entity are called, so `scripts/repair_titles.py` repairs them:

    python scripts/repair_titles.py --dry-run        # who needs one, and why
    python scripts/repair_titles.py --sample 20      # the model's guesses, nothing applied
    python scripts/repair_titles.py --reason caps    # the recase rule only, no model
    python scripts/repair_titles.py                  # everything, applied
    python scripts/embed_pending.py                  # afterwards, door stopped

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
`embed_pending.py`. The document page shows the former title. A
wrong repair is fixed with `--ids <id>` after editing, or by calling
`store.retitle` with the right title and `source="human"`.

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
`PRAX_VEC_DTYPE=i8 python scripts/embed_pending.py`. The names are in
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
start the server (`scripts/llama_server.ps1 -Model <gguf> -Slots 3
-NoThinking`; the switch matters for Qwen3.x and Gemma 4, which think by
default and would break the grammar), measured choices in
`docs/eval/extractors-local-2026-09-11.md`,
add an `openai` model with its address, point `steps.extract.model`
at it, and `extract_graph.py --workers <slots> --never-extracted` runs the
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
`meta.previous_capture`. What came in before that check existed is
handled by `scripts/dedupe_captures.py --dry-run | --commit`, which
keeps one capture per URL (a snapshot, then an extracted one, then the
oldest) and retires the rest.

Retiring (`store.retire_document`, "retire…" on a document page, `POST
/doc/{id}/retire`) takes a document out of search and the graph: chunks
and retrieval field go, its edges end, open review items close; the
row, the archived bytes and the text artifact stay, `meta.retired` says
why and of which document it was a duplicate. Batch jobs and the browse
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
  options.
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

  Four steps, each a batch the door hands out with a lease: parse (the
  worker fetches the original and posts the text), titles, extract (the
  door sends the prepared prompt input, the worker posts the triples),
  embed (chunk and field texts out, vectors in, into the door's delta
  index). The worker never spends money (a step whose model is the Claude
  API is skipped with a note; the promote pass is the way to that model),
  never opens the database, and never touches the curated imports unless
  `--scope all` says so. It announces itself as a job with heartbeats, so
  the Jobs view shows it wherever it runs. `--no-titles`, `--no-extract`,
  `--no-embed`, `--no-parse` switch steps off.

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
  download folder, a project's PDFs) is read with `--from <folder>
  [--domains research]`: every file under it is registered, nothing is
  moved or removed, and a second run finds them already known by hash.
  An uploaded PDF shows "pending" in the Inbox view until a worker has
  been over it, and the view refreshes itself while something is
  pending. `scripts/inbox.py` (one pass, straight through the store) is
  for a machine without a door and for `--from`; the by-hand passes
  (`repair_titles.py --ids`, `extract_graph.py --ids`,
  `embed_pending.py`) still exist for a store host with the door
  stopped.

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
Side by side on one GPU: the worker's steps and one by-hand pass
(`extract_graph.py`, `repair_titles.py`) share the model server's slots
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

## 3m. Healing what recurs

Extraction at scale leaves the same few kinds of damage behind, and they
come back with every pass, so they have names and a place:
`prax.store.repair`.

    prax heal                              what is wrong (changes nothing)
    prax heal --apply                      repair all of it
    prax heal --check self-edges --apply   one kind
    prax heal --json                       for a script

| ailment | what it is | what repairing does |
|---|---|---|
| `placeholder-entities` | a model copied a word out of its own prompt: "source name", "target name", "unknown", "n/a" | ends every edge they carry; the entity stays as the record of what happened |
| `reference-number-entities` | "[12]", "fig. 3", a bare year — the ontology says a reference number is never a name | ends their edges |
| `mangled-names` | a citation importer left markup or line breaks in a title: `<i>The Origins of Music</i>` | cleans the name, or merges into the entity that already carries the clean one |
| `unnamed-entities` | no name at all, or a whole citation as one (a claim is a sentence and is left alone) | ends their edges |
| `self-edges` | an edge from a thing to itself, left after two names were merged | ends them |
| `edges-of-retired-documents`, `review-of-retired-documents` | written by a pass that was already reading a document when it was retired | ends them; resolves the queue items as dropped |
| `stale-jobs` | a job still marked running whose heartbeat stopped a day ago (the door reaps its own host within minutes) | closes them as failed |
| `documents-without-an-extractor` | something waiting for text of a kind nothing here can read | a report: install what reads it (3b) or retire it |
| `chunks-without-vectors` | the current model has no vector for them | a report: run a worker |

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
| GET | `/search` | `q`, `limit?`, `kind?`, `mode?` | list of `{chunk_id, doc_id, title, snippet, score, kind, heading, page}` |
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

Anything that talked to the door without a token before — a local
script, this session's `curl` — needs the header from then on;
`/health` and the UI's files stay open. Plain HTTP inside the private
network is the design; a VPN address (Tailscale's `100.x.y.z`) is the
same recipe with that address, and TLS through a reverse proxy only if
the door were ever exposed beyond it.

## 4a. The `prax` command

One command for the everyday work, and the same one wherever the door is:

    prax                              where things stand, and what to type
    prax search granular synthesis    find documents
    prax ask --answer how does a feedback delay network work
    prax add ~/Downloads/paper.pdf --domain research
    prax add https://example.org/article
    prax import links bookmarks.html  what a service or an app exported
    prax show 4312                    read one in the terminal
    prax open 4312                    …or in the browser
    prax status                       the store, the graph, this host
    prax inbox                        what came in, what still waits
    prax jobs                         passes running now and lately
    prax heal                         what recurring damage is in the store
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
board's `prax.yaml`, the worker launcher for the desktop — and its README
is the step-by-step; this section is the reasoning.

**On the board.** Copy the data directory over (section 7), then:

    pip install "prax[serve]"        # the core plus vectors; no parsers, no models
    export PRAX_DATA_DIR=/srv/prax   # the SSD, never the SD card
    export PRAX_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    prax serve --host <its address on your private network> --port 8000

as a systemd unit with those two variables in its environment. In the
board's `prax.yaml`:

    vectors:
      dtype: i8        # half the file, recall 0.93: the memory a board has
    steps:             # nothing local answers here
      ask: {model: none}
      titles: {model: none}
      extract: {model: none}

An index written as `f16` stays `f16`: the setting takes effect when the
vectors are written, so re-embed into a fresh file
(`python scripts/embed_pending.py --compact` after removing the old
`vectors-*.usearch`) or copy the desktop's file and accept its precision.

**On the machine with the models**, pointing at the board:

    prax --door http://<board>:8000 --token <the token> status
    prax work --watch --door http://<board>:8000 --token <the token>

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

A nightly copy is one scheduled task running `prax backup`; Litestream
replication is on the later list.

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
