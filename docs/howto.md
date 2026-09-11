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
| `dev` | pytest, ruff, httpx | every dev checkout |
| `embed` | usearch, onnxruntime, tokenizers, huggingface_hub, numpy | the desktop (GPU via onnxruntime-directml) and the serving host |
| `ingest` | pymupdf4llm, trafilatura, magika | the parse queue; the desktop |
| `docling` | docling (about 3 GB with PyTorch) | optional; only for `--extractor docling` |
| `local` | llama-cpp-python | optional; a GGUF model in process, section 3h |

Check the installed FastMCP major version after upgrades; the code targets
the 4.x line:

    python -c "import fastmcp; print(fastmcp.__version__)"

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
      inbox/             drop folder (planned, Stage 1)

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
module per domain (`core.yaml`, `research.yaml`; format in
`src/prax/ontology.py`), each with a
`version`. `store.link` rejects anything else. Add types and bump the
version; edges keep the version they were written under. `PRAX_ONTOLOGY`
points at a different file (tests use it). After a bump, replay the review
queue (`scripts/replay_review.py`, section 3f) before extracting anything
new; the bump also re-selects every document for extraction. The reasoning
behind v2 is in `docs/ontology-v2.md`.

## 3a. Importing the Zotero library

The importer never opens the live `zotero.sqlite`; it copies the file into
`<data dir>/zotero-import/` and opens the copy read-only. Everything goes
through `prax.store`. Details of the mapping: `docs/sources.md` §1.

    # read-only census; nothing is written. --hash adds sha256 dedupe (reads every file)
    python scripts/import_zotero.py R:/Zotero --dry-run
    # trial run, then the whole library. Re-runs skip what is already imported.
    $env:PRAX_DATA_DIR = "C:\prax-data"
    python scripts/import_zotero.py R:/Zotero --commit --limit 500
    python scripts/import_zotero.py R:/Zotero --commit --quiet

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
    # Docling on a hand-picked set
    python scripts/parse_pending.py --ids 12 34 --extractor docling --force

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

Images (schematics, plots, photos, whiteboards) get text through Claude's
vision: `claude-vision` describes the image and transcribes its printed
and handwritten text into Markdown, which becomes the document's text
artifact like any parser's output. Explicit only, a few cents per image
(`PRAX_VISION_MODEL`, default `claude-sonnet-5`; Haiku 4.5 misread a
compressor schematic's identity where Sonnet transcribed the whole
revision table):

    python scripts/parse_pending.py --pending --mime image/ --extractor claude-vision

The document view shows an image inline above its description.

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
model files download from the Hugging Face hub on first use. Vectors live
in `<data dir>/vectors-<model>.usearch` (a memory-mapped HNSW index,
`prax.vectors`), bookkeeping in `chunk_embeddings`. Search is hybrid by
default and falls back to FTS when there is no index file, no usearch or
`PRAX_EMBED=0`.

    pip install -e ".[embed]"
    # Windows desktop with a GPU: DirectML instead of the CPU runtime
    pip uninstall -y onnxruntime; pip install onnxruntime-directml

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
`PRAX_EMBED_VARIANT` (`fp32` on a GPU, `int8` on CPU by default),
`PRAX_EMBED_PROVIDERS`, `PRAX_EMBED_THREADS`; `PRAX_VEC_DTYPE` (`f16`
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
`PRAX_EXTRACT=local` with `PRAX_LOCAL_MODEL=<model.gguf>` for a model on
this machine (section 3h; `PRAX_LOCAL_CTX`, default 8192). The local path
asks for tab-separated lines instead of JSON under a grammar that bounds
the output to 20 triples (`prax.lineformat`); the extractor name stamped
on documents is `local:<model file>` and its cost is zero.
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

### Typing rules over the queue

A model's misfits are systematic: the document typed as what it is about
("this manual" as a tool), `authored_by` written backwards or with authors
on both ends, `cites` for a tool or method the paper uses, `about` for a
claim it makes or a paper it discusses. `scripts/type_review.py` applies
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

A GGUF model can run in-process through `llama-cpp-python` on the batch
host, for extraction of new documents without the API, private material,
and the planned "ask" feature. Measured on the desktop's GTX 1070:
`docs/eval/local-llm-2026-09-08.md` (Qwen2.5-7B-Instruct Q4_K_M, 5.2 GB
VRAM, valid schema-constrained extractions at 80 s per document).

    # CPU build (any host)
    pip install -e ".[local]"
    # CUDA 12 wheel on Windows or Linux with an NVIDIA driver >= 525
    pip install -e ".[local]" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
    pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12   # Windows: the DLLs the wheel needs
    python -c "from prax import local_llm; print(local_llm.llama_class())"

Models are GGUF files, e.g. from the Hugging Face cache:

    python -c "from huggingface_hub import hf_hub_download as d; print(d('bartowski/Qwen2.5-7B-Instruct-GGUF', 'Qwen2.5-7B-Instruct-Q4_K_M.gguf'))"
    python scripts/bench_local_llm.py <path.gguf> --docs 3

Always go through `prax.local_llm.llama_class()` rather than importing
`llama_cpp` directly: on Windows the wheel's loader only searches `PATH`,
and that function puts the venv's `nvidia/*/bin` and `llama_cpp/lib`
folders there first. A stale `CUDA_PATH` (this desktop has a 10.2 toolkit)
does no harm. An 8 GB card fits a 7–8B model at Q4 with an 8 K context;
the Q6A has no usable GPU and would run a 3B model at a few tokens per
second, which is why the API stays the default there.

## 3i. Ask: questions answered from the library

`prax.ask` turns a question into a bundle (one passage per document from
the hybrid search, plus what the graph records about those documents)
and hands it to a model that answers with `[n]` citations. Which model
is the host's choice:

| `PRAX_ASK` | who answers |
|---|---|
| a `gguf` model | loaded once into the door's process on the first question (section 3h; Qwen2.5-7B answers in about 20 s on the GTX 1070, 5 GB of VRAM) |
| an `openai` model | a llama-server or vLLM elsewhere answers; the door loads nothing |
| a `claude` model | the API (effort low; about a cent per question) |
| `none` | nobody: the bundle comes back for the caller's own model |

The step is `ask` in `prax.yaml` (section 3k), `PRAX_ASK=<name|none>`
for one run. Without a file it is `local` when `PRAX_LOCAL_MODEL` is set
and `none` otherwise, so the serving board answers with the bundle. On
the batch host:

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
known acronyms). File names go to the local model (`PRAX_LOCAL_MODEL`
or `--model`; nothing leaves the machine, about 1.3 s per document on
the 1070) with the first 1,500 characters of text, the file name, the
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

## 3k. Which model does which step: `prax.yaml`

Every AI-assisted step (`extract`, `ask`, `titles`, `vision`,
`adjudicate`) takes its model from `prax.yaml` in the data directory
(`PRAX_CONFIG` points elsewhere; `prax.example.yaml` in the repo is the
template). `models` names backends, `steps` assigns them:

    models:
      sonnet:     {kind: claude, model: claude-sonnet-5, effort: medium}
      local-7b:   {kind: gguf, path: <file>.gguf, n_ctx: 8192}
      server-32b: {kind: openai, base_url: http://127.0.0.1:8080/v1, model: qwen2.5-32b}
    steps:
      extract:    {model: sonnet, max_triples: 20}
      ask:        {model: local-7b}
      titles:     {model: local-7b}
      vision:     {model: sonnet}
      adjudicate: {model: opus}

Kinds: `claude` (the API, key in `ANTHROPIC_API_KEY`), `gguf` (llama.cpp
in the process that runs the step, section 3h), `openai` (any
OpenAI-compatible server: `llama-server`, vLLM, or a hosted API with
`api_key_env` naming the variable that holds its key and `price` as
USD per million input and output tokens for the cost lines), `stub`
(tests). Names that need no file: any `claude-*` id, `local` (the GGUF
in `PRAX_LOCAL_MODEL`), `stub`, `none`.

Precedence per step: `PRAX_<STEP>` in the environment (a model name or
`none`), then the file, then the default (`claude-opus-5` for
extraction, `local` for ask and titles when a local model exists, else
`none`, Sonnet 5 for vision, `none` for adjudication).
`PRAX_<STEP>_MODEL` swaps the Claude model id for a step that resolves
to Claude, as before. A model is loaded once per process however many
steps name it, so ask and titles share one 7B in the door.

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

## 4. Running the HTTP door

    uvicorn prax.api:app --reload --port 8000

### Access

The door checks one shared secret, `PRAX_TOKEN`, on every request except
the UI's static files and `/health`. Generate one and set it in the
service's environment:

    python -c "import secrets; print(secrets.token_urlsafe(32))"
    $env:PRAX_TOKEN = "<the token>"          # PowerShell
    export PRAX_TOKEN=<the token>             # shell

Scripts and the extension send `Authorization: Bearer <token>`. The UI
asks for the token once and exchanges it for an HttpOnly session cookie
(`POST /session`, 30 days, `DELETE /session` to end it), so links to
originals work in new tabs. Without `PRAX_TOKEN` the door admits
loopback clients only, which is what a development server needs and what
keeps a misconfigured deployment closed. Bind the service to the
Tailscale address (`--host 100.x.y.z`) on the serving host; the MCP
server over stdio needs no token.

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

## 5. MCP server in Claude Code

`.mcp.json` in the repo root registers the server. Claude Code runs the
command with the project root as the working directory, so the interpreter
path is relative:

    "command": "${PRAX_PYTHON:-.venv/Scripts/python.exe}"

On Linux or macOS set `PRAX_PYTHON=.venv/bin/python` in the shell that
launches Claude Code. Claude Code reads `.mcp.json` at startup only, so
restart the session after editing it. The first time it sees the server it
asks whether to trust it; if that prompt was dismissed, run
`claude mcp reset-project-choices`.

Inside a session, `/mcp` shows connection state. The tools are `search`,
`get`, `get_chunk`, `traverse`, `link`, `ask`, `get_page`, `write_page`,
`append_page`, `ingest`, and `ingest_file`. The server can also
be run by hand to check it starts:

    python -m prax.mcp_server

It speaks MCP over stdio, so it will sit waiting for input; Ctrl+C ends it.

## 6. Deployment on the Pi / N100 (*planned*)

The shape, to be finalized in Stage 1:

- `data/` on the external SSD, `PRAX_DATA_DIR` set in the service
  environment.
- `uvicorn prax.api:app --host 100.x.y.z --port 8000` bound to the
  Tailscale address only, run from a systemd unit.
- Parsing and embedding jobs scheduled with systemd timers on the batch
  host; they share the same `data/` over the network or the file is copied
  back after each run.
- The MCP server on the Pi will use the streamable-HTTP transport and proxy
  the HTTP door (`rationale.md` R5).

## 7. Backup and moving the store

Everything is two things: one SQLite file and one directory of
content-addressed files.

- Database: use SQLite's online backup so the WAL is folded in, then copy
  the result.

      python -c "import sqlite3; s=sqlite3.connect('data/prax.db'); d=sqlite3.connect('backup.db'); s.backup(d)"

- Archive: `rsync -a data/archive/ backup/archive/`. Files are immutable
  and named by hash, so an interrupted copy can simply be resumed.

Litestream replication is on the later list.

## 8. Adding a source

Every source is a client of `prax.store`. The pattern (see `sources.md`):

1. Obtain original bytes and whatever metadata the source has.
2. `register(...)`: archive, insert the row. Dedupe is automatic by hash.
3. If text is already available, call `index_text(...)`. Otherwise leave
   `parsed_at` NULL and let the parse queue pick it up.
4. Seed graph edges from metadata with `link(...)`, `confidence="EXTRACTED"`
   and `source_doc` set.
