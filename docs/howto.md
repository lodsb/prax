# How-to

Practical steps for developing, running, testing and deploying prax.
Everything here works today unless a section is marked *planned*.

## 1. Development environment

The repo expects a virtualenv at `.venv` in the project root.
`.mcp.json` points at it.

Windows (PowerShell). Bare `python` is usually the Microsoft Store
stub. Use the `py` launcher:

    py -3.13 -m venv .venv
    .venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    pip install -e ".[dev]"

Linux, macOS, Raspberry Pi:

    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install --upgrade pip
    pip install -e ".[dev]"

Install an extra only where it runs (`rationale.md` R8):

| Extra | Contents | Where |
|---|---|---|
| `serve` | the core plus `embed` | the door on the board: `pip install "prax[serve]"` and nothing else |
| `work` | `embed` plus `ingest` | the worker on the machine with the models |
| `embed` | usearch, onnxruntime, tokenizers, numpy | vectors for hybrid search. On a Windows GPU use onnxruntime-directml instead, never both |
| `ingest` | pymupdf4llm, trafilatura, magika | parsing PDFs and pages, code detection |
| `docling` | docling (about 3 GB with PyTorch) | optional; only for `--extractor docling` |
| `dev` | pytest, ruff | every dev checkout |

The core, what `pip install prax` brings, is the door and the MCP proxy:
fastapi, uvicorn, pydantic, python-multipart, pyyaml, httpx, anthropic
(for the Claude-kind steps) and mcp. Fresh venvs on 2026-09-12, from
the declared extras alone:

| install | packages | on disk | the big ones |
|---|---|---|---|
| `prax[serve]` (the board) | 54 | 228 MB | onnxruntime 46, numpy 53, then cryptography (mcp), hf_xet (tokenizers) |
| `prax[work,dev]` (the desktop) | 92 | 648 MB | OpenCV 118 (RapidOCR's), pymupdf 109, onnxruntime 46, rapidocr 33, numpy 53 |

A venv that has lived through removals keeps their leftovers. The
desktop's was 771 MB and 168 packages before a rebuild. `pip install`
into a fresh venv gives the honest number. Measured while running: the
door's working set is about 70 MB with the vector index mapped and the
embedder loaded. Its private memory is 0.8 GB, for the ONNX runtime and
the usearch views, under the 1 GB of invariant 7. The worker sits at
0.8 GB between passes and 1.7 GB after an embedding batch.

The MCP server uses the official `mcp` package (2.x). Check the version
after upgrades:

    python -c "import importlib.metadata as m; print(m.version('mcp'))"

## 2. Tests and lint

    python -m pytest
    ruff check src tests

Tests never touch `data/`. Every fixture uses `tmp_path` and points
`PRAX_DATA_DIR` at it before importing the API or MCP modules.

The UI's JavaScript is checked when `node` is on the path. Both scripts
must parse, and `tests/ui/lib.test.js` (node's own test runner, no npm)
covers the pure helpers in `src/prax/ui/lib.js`. Without node those
tests are skipped.

`.github/workflows/ci.yml` runs on every push what a stranger's machine
would run: the suite on Linux (3.12, 3.13) and Windows, `ruff`, and the
README's quick start in a fresh venv. The quick start is
`scripts/smoke.sh`. It installs `prax[serve,work,dev]` into a throwaway
venv, starts a door on an empty store, adds a note, searches for it,
runs `prax status`, `show`, `models` and `doctor`, and stops the door.
The search uses keywords only: a fresh store has no vectors, so the
embedder is never loaded and nothing is fetched but the packages. Run
it yourself after touching the install path: `bash scripts/smoke.sh`,
three minutes, mostly pip, on Windows from Git Bash. It found two bugs
on its first run. A closed pipe (`prax show 12 | head`) was reported as
"no door", and non-Latin output crashed under a Windows pipe's codepage.
The command now writes UTF-8 whatever the console.

## 3. Data directory

Everything lives under one directory:

    data/
      prax.db            SQLite (WAL); the canonical store
      prax.db-wal, -shm  WAL sidecar files; copy them with the db
      archive/<xx>/<sha256>   originals and parsed-text artifacts
      inbox/             drop folder (section 3l); inbox/failed/ what the store refused

The location defaults to `<repo>/data`. `PRAX_DATA_DIR` overrides it.
On the Pi point it at the SSD.

### Schema migrations

The schema lives in `src/prax/migrations/NNNN_name.sql`. `store.init_db`
is called by the API, the MCP server and every script. It applies the
files whose number is above the database's `PRAGMA user_version`, each
in its own transaction, and stamps the version. To change the schema,
add the next numbered file. Never edit a file that has been applied. A
database created before migrations existed (version 0) is upgraded in
place.

### Ontology

`ontology/` holds the entity and relation types the graph accepts, one
module per domain: `core.yaml`, `research.yaml`, `studio.yaml`,
`craft.yaml`, `kitchen.yaml`, `workshop.yaml`. The format is in
`src/prax/ontology.py`. Each module has a `version`. `store.link`
rejects any type the modules do not declare. To grow a module, add
types and bump its version; edges keep the version they were written
under. `PRAX_ONTOLOGY` points at a different file (the tests use it).

After a bump, replay the review queue with `prax maintain --only
review` (section 3f) before extracting anything new. The bump also
re-selects every document for extraction. A document is extracted
against the modules of its domain set (section 3e, "A document's
domains"), so a new module re-selects only the documents without a set.
The reasoning behind v2 is in `docs/ontology-v2.md`. The studio module
(gear, manuals, datasheets, magazine articles) is in
`docs/ontology-studio.md`.

## 3a. Importing the Zotero library

The importer never opens the live `zotero.sqlite`. It copies the file
into a work directory and opens the copy read-only. The plan is made on
the machine with the library. Each document travels to the door as one
request (`POST /import/zotero/item`) with the record, the file and
Zotero's cached text, and the door writes it through `prax.store`. The
library may therefore live on another machine than the store. The
mapping is in `docs/sources.md` §1.

    prax import zotero /path/to/Zotero --dry-run     # the census; nothing is sent
    prax import zotero /path/to/Zotero -n 500        # a trial run, then
    prax import zotero /path/to/Zotero               # the whole library

A re-run skips what the door already has and refreshes a record that
changed. An interrupted run is started again the same way. The worker
parses what came without cached text (`prax work --scope all`).

The test fixture in `tests/fixtures/zotero/` is regenerated with
`scripts/make_zotero_fixture.py`. The exact command is in its docstring.

## 3b. Parse queue

Extractors live in `prax.parsers`; its docstring has the table. They
are picked by MIME type in registry order. When one raises, the next
one is tried. The queue records every attempt in `meta.parse_history`
and the winner in `meta.text_source` as `<name>/<version>`. Any pass
can therefore be redone later with `--upgrade <prefix>`, or left to the
nightly pass, which finds the documents whose extractor prax has
revised since (3l¾). An upgrade keeps the old text when the new one is
suspiciously short (login walls, scans without OCR), unless `--force`
is given. Every history entry carries the hash of the text it
produced, so the earlier artifact stays in the archive. Parsing runs on
the desktop, never on the serving host.

    # documents never indexed (registered by an importer): the backlog pass
    prax work --scope all --steps parse
    # re-extract everything Zotero's cache produced, HTML first: a reading
    # request on each, drained by the worker (3l¾)
    prax reread --extractor trafilatura --text-source zotero-ft-cache --mime text/html
    # scans: OCR is explicit and bounded (PRAX_OCR_MAX_PAGES, default 60)
    prax reread --extractor pymupdf4llm-ocr --unreadable
    # scans read as their cover: a text layer on the front matter only, under
    # 100 bytes a page over five pages or more (the `thin-texts` ailment; the
    # worker records a PDF's page count as meta.pages, and `prax heal --only
    # uncounted-pages` counts the older ones once); --thin 40 for a stricter bar
    prax reread --extractor pymupdf4llm-ocr --thin
    # scans in another script: the recognizer is a setting (parse.ocr_language:
    # ch reads Chinese and English; en, latin, arabic, cyrillic, devanagari,
    # japan, korean, el, th...) and part of the stamp, so a book read with the
    # wrong one has not been read with the right one; the mode names it for
    # the request, --title picks the books out
    prax reread --extractor pymupdf4llm-ocr --mode arabic --title "In Arabic"

pymupdf4llm writes what the OCR recognized back into the page with
Droid Sans Fallback, which has Latin, Greek, Cyrillic and CJK. For a
script that font cannot write (Arabic, Devanagari, Tamil, Telugu, Thai,
Georgian) prax runs the recognizer itself and writes the lines as text
in reading order, right to left for Arabic, with page markers. There
is no layout analysis in that path. A scanned book rarely has layout
to give.

    # Docling on a hand-picked set
    prax reread --extractor docling --ids 12 34

**Pages OCR cannot read**, such as handwriting, scores and photographed
notebooks, go through the vision model page by page. The `vision-pages`
extractor keeps a page that has a text layer and renders every other
page at 150 dpi (`parse.vision_dpi`) for the `vision` step's model. The
prompt asks for a transcription: verbatim text, `(handwritten)` marked,
a figure or a score as one `[Figure: …]` line, `[illegible]` instead of
a guess. Page markers are written as the OCR extractor writes them, so
chunks keep their pages. The stamp carries the model
(`vision-pages/1.28+<model>`). The pass is explicit only and bounded by
`parse.vision_max_pages` (default 200). A page takes 4–10 s locally:
2–3 s to read the picture, the rest writing. The first page after the
server starts took two minutes once. With Sonnet a page costs a cent or
two.

    prax reread --extractor vision-pages --ids 8605
    # every page, for printed pages with notes in the margin
    prax reread --extractor vision-pages --mode all --ids 8605

The same request can come from the document's page in the web UI.
"process…" lists every route from the document with its state beside
each: OCR, the vision model over the scanned pages or every page,
marker, the extractor again, Docling, the figures, the equations, the
graph. A button asks for one. A running worker
(`scripts/work.py --watch`) does it next; requests go out before the
pending captures. The outcome is shown on the page and the queue on
Jobs. A worker refuses a reading whose model would cost money (the
vision step set to Claude) and says so. Those readings stay a command
you run yourself.

**Figures.** The pictures that belong to a document's content, such as
a photograph with its caption, a plot, a schematic or a panel, are
found at parse time (`prax.parsers.figures`). They are written into the
Markdown where they sit, as `![caption](figure:<sha256 of the image
bytes>)`. For a captured page the candidates are the images the
snapshot carries inline that sit inside `<figure>` (with their
`<figcaption>`), or have a real `alt` text, or are big enough to be a
photograph. The site's chrome has none of that. For a PDF the
candidates are the placed raster images at least 90 pt on each side
that do not repeat on many pages (a logo would). The nearest "Figure N"
block below becomes the caption, and the image line is put right above
it. Nothing new is stored: the door serves a figure out of the original
by its hash (`GET /doc/{id}/figure/{sha}`), and the document view shows
it with its caption. The chunker makes the image line, its caption and
any reading one `figure` chunk (`data`: ref, caption, readings), so a
figure is a search hit of its own.

The second half is the reading:

    # what the vision model makes of every figure, written under each
    prax reread --extractor figures --ids 9706

The same request is "process… → Read the figures nobody has read" on
the page. A PDF image no caption claims (`Figure on page N`) is read
only with `parse.figures: all` (`PRAX_FIGURES=all`, or "Read every
image, the uncaptioned ones too" in the dialog), because many of those
are decoration. "Read every figure again" (`--mode again`) reads them
all under the current prompt, this model's earlier readings replaced;
that is how a library takes up a better prompt or a better model of
the same name. The reading goes under the image line as
`*Figure, as read by <model>:* …`. A model reads a figure once; another
model's reading joins it. The figure chunk carries the reading, so it
is findable, read by the extraction, and read by a surfing ask like any
paragraph.

The model is shown what the document says around the figure: its
title, the caption, the text on either side of the image line. What a
plot is *of* is written there, not in the picture. Without that
context the reading was "six stacked curves that likely illustrate
spectral characteristics"; with it, "the frequency responses of the
five learned CNN kernels against the ground truth filter". The prompt
tells the model to name things in the document's terms but to state
only what is visible, so the surrounding text supplies the subject
without being described in the figure's place. This is extractor
revision r2. Readings written before r2 stand until a figure is read
again.

**The life of a figure** has four steps, and only the third costs
anything:

1. *Found.* The parser writes `![caption](figure:<sha>)` where the
   image sits (`trafilatura` r3, `pymupdf4llm` r2; `figure-refs` for a
   library parsed before that). The bytes stay in the original.
2. *Asked for.* A reading request comes from the page's "process…"
   dialog, from `prax reread`, or from the door itself when a capture is parsed
   and the vision model is local. The door never asks for a reading
   that costs money.
3. *Read.* A worker takes the request. The vision model describes the
   figure with the text around it, and the description is written
   under the image line. About four seconds a figure locally.
4. *Used.* The chunker makes the image line, its caption and the
   description one `figure` chunk. It is embedded and searched like any
   other, read by the extraction, and read by a surfing ask.

Step 2 does not happen by itself for everything. The door asks only
for captures (the extension, an upload, the drop folder), only at the
moment their parse finds figures, and only if the document has never
had a reading request. A Zotero library never gets one that way. What
is left over has a name on the Jobs page: the `unread-figures` ailment
counts the documents that hold a figure nobody has read, and its offers
ask the vision model for them, either the captioned ones or every
image. The same selection is on the command line:

    prax reread --extractor figures --unread-figures --dry-run

A model reads a figure once. A better prompt, or a better model of the
same name, reaches the library only on request:

    # the figures this model has read, read again under the current prompt
    prax reread --extractor figures --mode again --read-figures --dry-run

`--mode again` reads the figures this model has read before and
replaces its own earlier reading; another model's reading stays.
`--mode all-again` does the same for the uncaptioned images too.
`--read-figures` selects the documents that hold such a figure. Drop
`--dry-run` to place the requests, one per document. The worker drains
them before the pending captures. A figure costs about four seconds on
a local server, so a library-wide pass is an overnight job: `--limit`
it, or name `--ids` when only some documents matter.

The parsers find figures as they read (`trafilatura` r3, `pymupdf4llm`
r2). For a library parsed before that, the `figure-refs` pass puts the
original's figures into the current text without re-reading the pages.
pymupdf4llm's layout analysis takes 10–30 s a document; `figure-refs`
takes a fraction of a second.

    prax reread --extractor trafilatura --text-source trafilatura/2.2.0-r2   # 284 pages: 2 min
    prax reread --extractor figure-refs --text-source pymupdf4llm/1.28.2

A document whose text comes out the same is reported as `same`: the
stamp moves and nothing is rebuilt. A document that gained a figure
keeps every chunk whose text did not change, with its vector; only the
changed chunks are re-embedded. Vector drawings in a PDF are not images
and are not found this way. `vision-pages` over the page covers those.

Measured on an orchestral score (Cowell, doc 8605, 2026-09-14). Where
OCR produced table-shaped garbage, the local model gave "a page of
orchestral sheet music showing staves for Percussion, Trumpet, Horns
I–II and III–IV, Trombones, and Tuba … *mf*, *cresc.*, *senza sord.*".
That is findable, but not a transcription of the notes. On some pages
it gave only the bar numbers. Look at a few pages of a document before
running the whole of it. The pass replaces the document's text, as
every PDF extractor does; the OCR reading stays in the archive and in
`parse_history`. The additive reading is the figure extractor's, above.

Office documents need nothing installed for `.docx` and `.odt`. Both
are a zip of XML, and `prax.parsers` reads them: headings by outline
level or style name in any language, numbered and bulleted paragraphs
as list items, tables as Markdown tables. `.rtf` is read by `striprtf`
(an 8 KB package in the `ingest` extra) as plain text. Only the old
binary `.doc` goes through LibreOffice, which converts it to `.docx` in
a scratch directory with a profile of its own. Install it
([libreoffice.org](https://www.libreoffice.org), or `soffice` on PATH)
and the `office` extractor appears. Without it those documents stay
pending and say why. Python's MIME table misses `.docx` on some
machines, so `prax.parsers.guess_mime` names the office types itself.

Before switching the default extractor for a document class, run
`scripts/compare_extractors.py` over a sample. Read the texts, not only
the metrics table it writes.

Long passes over thousands of PDFs are better as a loop of short-lived
processes than as one process. MuPDF's layout analysis grows the
process over time, and one run over 5,000 documents was killed for
memory. A worker re-selects what is left on every pass, so batching
costs nothing:

    for ($i = 0; $i -lt 40; $i++) { prax work --scope all --steps parse -n 250 --quiet }

The watching worker is one process for as long as it runs, as a service
from logon on. The nightly pass takes a hundred documents at a time
(4b).

Layout analysis reads a long PDF a window of pages at a time
(`parse.layout_window`, `PRAX_LAYOUT_WINDOW`, default 100). A 532-page
book takes two minutes, with the process flat at about 570 MB. A
heading's level is ranked within its window, so a window without a
chapter title may rank its sections one level up. Originals above
`PRAX_MAX_LAYOUT_MB` (default 200) skip layout analysis and get plain
text through the fallback: one line per line of print, no headings. 98
documents had that before the window existed, when the cap was 400
pages and 40 MB. `prax reread --extractor pymupdf4llm --text-source
pymupdf/ --mime application/pdf` reads them again. A scan is refused
for OCR by a probe of the first five pages and ten more spread over the
document, so a journal issue with a scanned cover still gets its text
from page seven on.

Code is kept as code. HTML pages come out as Markdown with `<pre>`
blocks fenced (trafilatura's Markdown output). When the snapshot has a
comment section, such as a thread under a blog post or a forum page,
it follows the article under its own `## Comments` heading (trafilatura
r4). Every comment is then a chunk under that heading and searchable,
while the article's own text stays what a search hit or an extraction
reads first. `parse.comments: false` (`PRAX_COMMENTS`) leaves comments
out, with `+nocomments` in the stamp.

A text attachment that is a source file becomes one fenced block with
a language. The filename's extension decides when it is telling (`.m`,
`.py`, `.scd`, `.h`, ...). Otherwise Magika, a small content-type model
in the `ingest` extra, classifies the bytes, and only a confident
programming-language verdict counts. A bibliographic note full of "Key:
value" lines scores as YAML and stays prose. A note that mixes prose
and code, such as a forum thread or a chat log with a function pasted
in, gets its code regions fenced by a line scorer. The scorer counts
code signals per line, takes runs of at least four lines, and treats
block comments as code always. Each function is then one chunk and the prose around it stays prose.
Extractors carry a `revision` in their stamp (`plain/1-r3`), so such
changes re-select what they wrote:

    prax reread --extractor trafilatura --text-source trafilatura --mime text/html
    prax reread --extractor plain --text-source plain --mime text/plain
    # the vectors for the new chunks follow: the worker's embed step

The backlog pass finds these by itself once the revision is bumped
(3l¾). The request is for doing it now.

For PDFs, pymupdf4llm fences monospace runs, which misses code set in
a proportional font. Docling's layout model has an explicit code label
and is the better extractor for a hand-picked set of code-heavy papers
(`--ids ... --extractor docling --force`).

Images (schematics, plots, photos, whiteboards) get text through a
vision model. The `vision` extractor describes the image and
transcribes its printed and handwritten text into Markdown, which
becomes the document's text artifact like any parser's output. It runs
only when asked. The `vision` step of `prax.yaml` names the model:

* a Claude model, a few cents per image (`PRAX_VISION_MODEL`, default
  `claude-sonnet-5`). Haiku 4.5 misread a compressor schematic's
  identity where Sonnet transcribed the whole revision table;
* the local llama-server, when its model is a vision-language model
  and the server was started with the model's projector (section 3h,
  `-Mmproj`). Free, about 15 s per image on the 24 GB card with
  Qwen3.6-35B-A3B. On the 1176 schematic it transcribed the component
  values more completely than Sonnet and named the device slightly
  less well (measured 2026-09-13, one image). Claude's descriptions
  carry more interpretation, the local ones more verbatim text.

    prax reread --extractor vision --mime image/     # every image again; the door
                                                     # asks for new ones by itself (3l½)

The stamp records the model (`vision/1+<model>`). `claude-vision` is the
same extractor pinned to Claude, the name the first descriptions carry.
The document view shows an image inline above its description. An
image that matters gets the expensive reading the way a paper does:
promote it (section 3e), and the promote step (`prax work --steps
promote --spend`) describes it again with its model (Sonnet here)
before extracting from that. Readings add up; they do not replace each
other. The second model's reading goes first in the artifact and the
earlier ones follow, each section headed with its model (`## Text in
the image (qwen…)`). The verbatim list one model is good at and the
interpretation the other is good at are both searchable and both feed
the extraction. The same model read again replaces only its own
reading (`vision.merge_readings`).

## 3c. Chunks

`prax.chunking` turns each text artifact into structure-aware chunks
(rationale R13): sections of paragraphs, whole tables with their
caption and a parsed grid, figure captions, code blocks. Each chunk
has a heading path and a locator (character range and page). Search
hits carry `kind`, `heading` and `page`; `kind="table"` narrows a
search to tables. After changing the chunker, or after a migration
that added chunk columns:

    prax maintain --rechunk               # every indexed document, a job on the door

Chunks are disposable. Nothing else is touched, and a chunk whose text
did not change keeps its id and its vector.

## 3d. Embeddings and hybrid search

`prax.embeddings` runs bge-small-en-v1.5 (384-d) through onnxruntime.
The model files are fetched once into `<data dir>/models/` on first
use (`prax.fetch`: plain HTTPS from the Hugging Face hub, resumable).
A copy in an old Hugging Face cache is taken from there.
`PRAX_OFFLINE=1` refuses to download. `python scripts/fetch_model.py
--embed` fetches ahead of time. Vectors live in `<data
dir>/vectors-<model>.usearch`, a memory-mapped HNSW index
(`prax.vectors`), with the bookkeeping in `chunk_embeddings`. Search is
hybrid by default. It falls back to FTS when there is no index file,
no usearch, or `PRAX_EMBED=0`.

    pip install -e ".[embed]"
    # Windows desktop with a GPU: DirectML instead of the CPU runtime.
    # Never both: the two packages share one module and the last one
    # installed wins, silently (the desktop ran on the CPU for days so).
    pip uninstall -y onnxruntime onnxruntime-directml; pip install onnxruntime-directml

    prax work --steps embed --scope all       # everything pending; idempotent
    prax status                                # the counts

The worker embeds what has no vector yet and posts the vectors. The
door writes them into the small delta index beside the main file and
folds the delta in when it grows (`POST /vectors/merge` does it now).
Nothing is stopped for it. Re-parsing documents (a new extractor
revision, Docling on a few) creates new chunks, which a following pass
embeds. The `chunks-without-vectors` ailment (3m) counts what waits.
When moving the store, copy every `.usearch` file together with
`prax.db`, or let `prax backup` do it (7).

Settings:

- `PRAX_EMBED`: the model name; `hash` for tests; `0` off.
- `PRAX_EMBED_VARIANT`: `int8` by default everywhere. Measured
  2026-09-12 on 1,024 real chunks: CPU int8 23 chunks/s, CPU fp32 17,
  DirectML fp32 45, DirectML int8 80. int8 is the faster one on both
  and keeps the store's vectors from one variant.
- `PRAX_EMBED_PROVIDERS`: DirectML first when the runtime offers it.
- `PRAX_EMBED_THREADS`.
- `PRAX_VEC_DTYPE`: `f16` by default; `i8` halves the file at recall
  0.93.
- `PRAX_VEC_EF`: the search expansion, default 64.

Changing the model means re-embedding into a new file.
`chunk_embeddings.model` records what each vector came from, and the
worker's embed step picks up the difference.

The query is `search(q, mode="hybrid"|"fts"|"vec", kind=...,
doctype=...)` in the store, `/search?mode=&doctype=` in the API, and
the MCP tool. Hybrid fuses four rank lists at document level: chunk
BM25, chunk KNN, and BM25 and KNN over the document field. The field
(migration 0005) holds the title, kind words, creators, venue, the
extraction summary and the opening paragraph of an image description.
Hits carry `score` (RRF) and `fts_rank`, `vec_rank`, `field_rank`,
`dvec_rank`. A hit found only through the field opens at the
document's best-matching chunk. `doctype` keeps one type: `pdf`, `web`,
`image`, `text`, `note`. The field follows a document's text and
metadata on its own. After migration 0005, or a change to
`store.document_field`, rebuild it and embed:

    prax maintain --only fields        # the field of every document (3n)
    prax work --steps embed --scope all  # then the vectors, chunks and fields

The reason for the field: chunk scoring finds documents *about* a
term, not the document that *is* the thing. "schematic" put a CAD
manual first and the one schematic nowhere
(`docs/eval/retrieval-field-2026-09-10.md`).

### Acronyms

Papers define their acronyms in the text ("antiderivative antialiasing
(ADAA)"). The `acronyms` pass of `prax maintain` (3n; nightly)
collects those definitions from every text artifact into the
`acronyms` table (migration 0008). The library gave 5,887 pairings,
1,826 of them defined by two or more documents. The search expands a
query token that is a known acronym to its phrase on the keyword side,
as a phrase match. The embedder sees the query as typed; expanding it
measured worse.

The same change added one more rank list: chunks that contain the
query's rare acronym-shaped terms, with weight 3. A term counts when it
has at most six letters or digits, or is a known acronym, and appears
in fewer than 50 chunks. "adaa iir" is then decided by the five
documents that say ADAA and not by the thousands that say IIR. A query
made only of such terms weights the keyword list instead. Measured on
the library set in `docs/eval/retrieval-acronyms-2026-09-12.md`: MRR
0.89 to 0.905. Feeding the expansions to the embedder and an all-terms
tier were tried and left off.

## 3e. Graph extraction (Stage 3)

`prax.extraction` sends each document's metadata header and the first
12,000 characters of its text to the model, with a JSON schema
generated from the composed ontology. It writes the returned triples
through `store.link`, each with a confidence and a quoted evidence
string. Triples that do not fit the ontology go to `review_queue`. The
summary goes to `meta.summary`. The document is stamped with the
ontology version, so reruns are incremental.

    prax work --steps extract --scope all -n 20       # a trial, then the rest
    prax work --steps extract --scope all --workers 3   # the backlog, a served model's slots

The extract step never spends money. The worker refuses a Claude model
in `steps.extract`; the promote step, below, is the paid pass. Notes
and empty scans are skipped (`pipeline.MIN_CHARS`, 500 characters).

A prompt the server's slot cannot hold is cut to the share the server
states and asked once more, with `cut: 1` in the stamp. That happens
with a schematic's symbols or a dense script, which have more tokens
per character than the 12 K-character budget assumes. A document that
still fails is remembered (`meta.extraction_error`, the
`extraction-failed` ailment) and not tried again every pass, until the
ontology moves or a bigger slot is up. The summary a local model writes
is bounded at 700 characters and cut at a sentence. Before 2026-09-15
the bound was 300 and the grammar stopped it mid-word; summaries
extracted before then look like that until a pass reads those
documents again.

Settings: `PRAX_EXTRACT_MODEL` (default `claude-opus-5`),
`PRAX_EXTRACT_EFFORT` (default `medium`), `PRAX_EXTRACT=stub` for
tests, `PRAX_EXTRACT=<name>` for an `openai` model of `prax.yaml`
(section 3h: llama-server on this or another machine). The local path
asks for tab-separated lines instead of JSON, under a grammar that
bounds the output to 20 triples (`prax.lineformat`). The extractor name
stamped on documents is then `<model>@<host>`, and its cost is zero.
Bumping the ontology version re-selects every document. Review the
queue in the UI's Review tab (section 3g) or with `store.list_review`
and `resolve_review`.

### Provenance and upgrading a producer's work

Every edge records its `producer` (a model name, `zotero`, `crossref`,
`page`, `replay`, `manual`, `agent`) and its `run` (a batch id, a
script run, a page revision). `store.provenance_summary` lists live and
retired edges per producer and run. `store.retire_run(producer=...,
run=...)` ends a run's edges when a better pass has replaced them; the
history stays. Edges written before migration 0007 were tagged once
(`store.backfill_provenance`, 2026-09-11). Every edge since carries
both.

### Entity resolution

    prax resolve                        # the plan: sure, twins, likely — nothing merged
    prax resolve --apply                # the sure merges, a job on the door
    prax resolve --apply --twins        # concept+method twins as well
    prax resolve --type author          # one entity type

The likely tier is listed for you. Only an adjudicator merges it: the
`adjudicate` step of the worker, whose model is `steps.adjudicate` in
`prax.yaml`. That is a Claude model and paid, so the step runs only
with `--spend` (or `spend: true` under `run.worker`), and only when
named in `--steps`:

    prax work --steps adjudicate --spend     # the likely pairs to the adjudicate model, once

Each pair is one line of a forty-line question. A yes is a merge. A no
is recorded on the pair (`entity_candidates.decided`), so the next
week's computation of its type does not ask again. The worker says
what a pass cost. Measured 2026-09-17: 8,242 pairs, 3,683 merged, 4,559
kept apart, $2.59 with Opus 5, a tenth of a cent a decision.
`scripts/resolve_entities.py --commit --adjudicate` does the same from
a process that opens the database file, for a host without a worker.

Sure merges need no model. They are equal names after normalization
(case, accents, punctuation, plural, suffixes), and author initials
that abbreviate exactly one full name. Likely merges are close by name
embedding, for concepts, methods, tools, datasets and venues only, and
merge nothing unless an adjudicator says yes. The embedding is a
worker's, never the door's. The `resolve` step of `prax work` takes one
type's names from the door, embeds them, finds the pairs at or above
the threshold (0.92) and posts them. It works a block of rows at a
time, so 30,000 names cost a few hundred megabytes, not the square.
The door keeps the pairs (`entity_candidates`, replaced whole per
type). The plan reads them from there and skips any pair whose entity
has since been merged. A type is computed again after a week; `prax
resolve` says when each was. Embedding 138,000 names inside the door,
and the square of similarities after, crashed it once (2026-09-17). It
embeds nothing now. A merge sets `entities.canonical_id`. Nothing is
deleted; `traverse` and the UI follow the pointer. Undoing a merge is
clearing that column.

### Promoting documents to the expensive model

The local pass reads everything once. The papers you work with deserve
the richer pass, which finds claims and relations between methods. A
document is flagged in `meta.promote` from its page ("process… →
Promote to the expensive model") or from the Promote view's
candidates. Claude Code flags one through the `promote` MCP tool. The
store itself flags a document when it joins a project or becomes a
synthesis source. The candidates are scored by project
membership, synthesis sources, notes on the document and citations
from other library documents. The flag queues; nothing is spent until
the pass runs. The pass is a work step the worker takes only when
named, and only with `--spend`, which is the asking:

    prax work --steps promote --spend          # the flagged documents, once each
    prax work --steps promote --spend -n 5     # five of them

Without `--spend` the worker says the step is paid and touches nothing.
A promoted image is first described again by the promote model, and
the extraction reads that. The model is the `promote` step in
`prax.yaml` (section 3k; default Sonnet 5, `max_triples: 30`). A
flagged document counts as done once that producer's stamp is in its
extraction history, so the pass is idempotent and a later local pass
does not undo it. Both producers' edges sit side by side. `GET
/promote` returns the flagged list with status and the candidates.
`POST /doc/{id}/promote` sets the flag and `DELETE` clears it.

### Reading the graph again

The extract pass reads a document once per ontology version and
domain set. To have the local model read one again now, without
changing anything else: "process… → Extract the graph again" on its
page, or `POST /doc/{id}/extract`. The stamp goes to the history,
`meta.extraction_stale.requested` says who asked, and the worker's next
extract pass takes the document before its backlog, in the captures
scope too. The new reading retires the producer's earlier edges, which
stay as history (invariant 8). The dialog reads `requested` until the
pass has run.

### A document's domains

A document is read against the ontology modules it belongs to, its
domain set in `meta.domains`. The papers are read against `core` plus
`research`, the family photos against `core` plus a `family` module. A
document that is both, such as a relative's thesis or a photo from a
conference, is read against both. No domain set means every module,
which is what the library had before modules existed. The extraction
prompt, the grammar and the JSON schema are built for that subset. The
document itself is a `paper` where the research module is loaded and a
`document` otherwise. The stamp carries the subset's version
(`core1+family1`), so a document is due again when one of *its* modules
grows, not when any module does.

Sets come from rules in `prax.yaml`. The first match wins. A rule
without `match` is the default. `match` keys are `source`, `mime`,
`path`, `collection` and `tag`:

    domains:
      - match: {collection: Family}
        domains: [family]
      - match: {source: zotero}
        domains: [research]
      - domains: [research]

    prax maintain --only domains        # documents without a set (3n; nightly)

A capture gets its set from the rules as it arrives. The pass is for
what came before the rules, or after a rule changed;
`store.assign_domains(..., force=True)` re-assigns rule-set documents
too.

A set written by hand is never overwritten by the rules. Hand-set means
"domains…" in the document page's action row (a box to tick per
module), `PUT /doc/{id}/domains`, `POST` or `DELETE
/doc/{id}/domains/{name}`, or the `set_domains` MCP tool. Adding a
domain keeps the others. Removing the last one puts the document back
in every module. A change under an extraction made against the old set
leaves that reading stale (`meta.extraction_stale.domains_changed`; the
door answers `reread: true`). The worker's next extract pass takes the
document before the backlog, and the new reading retires the old one
with the history kept. A re-run for one domain reads the documents
assigned to it that are not yet stamped with their subset's version:

    prax work --steps extract --scope all      # the backlog pass takes them: a
                                               # document is re-selected when its
                                               # subset's version moved

`search(..., domain="family")` (API and MCP `domain=`) keeps the hits
from that domain. Documents without a set are in every domain. When a
document is read again under another subset, because its domains
changed or one of its modules grew, the same producer's earlier reading
is retired as the new one is applied (`store.retire_reading`, history
kept). Other producers' edges stay.

### Typing rules over the queue

A model's misfits are systematic:

- the document typed as what it is about ("this manual" as a tool);
- `authored_by` written backwards, or with authors on both ends;
- `cites` for a tool or method the paper uses;
- `about` for a claim it makes or a paper it discusses;
- in the studio domain, the document put where its device belongs
  ("the manual has this feature", moved onto the device the document
  describes).

The door applies the rules in `prax.review.apply_typing_rules` to a document's
items right after its extraction. Over the whole queue they are the
`review` pass of `prax maintain` (3n), which the nightly task runs: a
replay against the current ontology first, then the rules.

    prax maintain --only review

A rule retypes, flips, renames the relation, or drops what no relation
can hold. It never invents. What it links is written as INFERRED edges
with the producer `typing-rules` and one run id per pass, with the
item's evidence and source document, so a pass can be retired like any
other producer's. Unmapped items are covered too, when the relation the
model named and the shape of the names decide the case. Those cases
are affiliation between a person and an institution, supervision
between two people, authorship between a title and a person, funding,
who built a tool, and a mention whose reason names what kind of thing
it is. Items the rules do not cover stay open as evidence for the next ontology version. v5
(`docs/ontology-v5.md`) came out of that evidence.

Numbers: after the local backlog of 2026-09-12 the first pass closed
2,898 of the 4,300 typed items as linked (2,468 new edges, 430 already
in the graph) and dropped 769. A second batch of rules after the v5
re-read (placeholder names, a cited "document" that is a paper, a
listed "person" who is the author, venues, cited titles) linked 3,091
and dropped 3,390 of 25,827. Research v6 and a replay took 6,680 more.

What no rule can decide goes to a model (`prax.typing_pass`): an item
with no types at all, or "X about Y" with nothing but the names. A
batch of two dozen items is sent with the document's title, its own
type and the ontology subset it is read against, and answered as `<n>:
<type> -> <type>` or `none`. An answer that fits the ontology becomes
an INFERRED edge with producer `typing:<model>`, after the same remaps
the rules use (a "cited" tool is used, a "cited" person is mentioned).
`none` drops the item. A misfit stays open. The model is the `typing`
step (`prax.yaml`, or `PRAX_TYPING=…` for one run):

    prax work --steps typing -n 10           # ten requests of up to 40 items
    prax work --steps typing --watch          # until the queue is typed

This is a work step (3l). The door hands out batches of untyped items
with their documents' titles and domains, the worker asks the typing
model, and the door applies the answers. A misfit keeps the types the
model gave it, so it is not asked again, and the rules or a later
ontology can take it. The step runs only when named. A paid typing
model is refused.

On 480 items of the live queue the local 35B linked 263, dropped 21 and
left 48 misfits, at about 30 s per request of 24 items on the 4090.

## 3f. Citation network

`prax.importers.citations` asks OpenAlex or Crossref for each
document's reference list and citation count, by DOI or by exact title
with `--resolve-titles`. It writes `paper --cites--> paper` edges with
the source and work ids as evidence. A reference that is a library
document is named by that document's title. `meta.citations` holds the
citation count and makes re-runs skip. No key is needed;
`PRAX_CITATIONS_MAILTO` joins the polite pools. Crossref answers in
half a second per request. OpenAlex needs fewer requests but was
unreachable for hours on 2026-09-10, hence two sources behind one flag.

    prax import citations --dry-run                        # the selection's size
    prax import citations --source crossref                # a job on the door
    prax import citations --source crossref --resolve-titles   # DOI-less ones too, by title
    prax import citations --refresh -n 50                  # fetched ones again

The door fetches: it has the DOIs, and `citations.mailto` in prax.yaml
for Crossref's polite pool. The job's note counts resolved documents,
edges and requests as it goes.

Four documents in five have no DOI for Crossref to answer, but their
reference lists are in the parsed text. The `references` pass of `prax
maintain` (3n) reads them by rules: the entries under a
References/Bibliography heading, each into surnames, year, title and a
printed id. It matches each entry against the library's document field
by title, creators and year, with a score. A printed DOI or arXiv id
gives an `EXTRACTED` edge. A title match over the threshold gives
`INFERRED`, with the score in the evidence ("references: [12] 'Title
as printed' (2018), score 0.93"). Several candidates within a margin of
each other, the twins of one paper, each get `AMBIGUOUS`. Nothing
outside the library is named; the Crossref edges do that. Measured on
the library (`docs/eval/references-2026-09-20.md`): 6,551 sure links
from 1,612 documents, three quarters of what Crossref found, and 5,629
links from documents Crossref could not resolve.

Each entry is a `reference` chunk. The chunker cuts them under the
heading; run `prax maintain --rechunk` once for a library indexed
before that. The chunk's `data` holds what the entry names and, after
the pass, the document it cites. The document page shows the entry
with a link to that document ("likely 0.93" for a title match, "?"
between twins). An in-text marker like "[12]" in the prose links to
what entry 12 cites, or to the entry itself when nothing in the library
matched. Reference chunks are never embedded and stay out of a search
unless asked for with `kind=reference`: an entry matches every author,
venue and year in the library and says nothing the cited paper does
not say better.

    prax maintain --only references     # the documents whose text changed since

### Review queue and ontology growth

The review view (`#review`) filters by relation and by unmapped versus
typed items, drops all matching items in bulk, and replays typed items
against the current ontology. The same replay is the `review` pass of
`prax maintain` (3n), run nightly. The proposal for ontology v2, built
from the queue's numbers, is `docs/ontology-v2.md`.

## 3g. Pages: notes, projects, topics

Pages are Markdown documents in the store (rationale R15). In the UI,
"add a note" on any document creates an addendum page linked to it
(`annotates`). The Pages tab creates topic and project pages and lists
them. "edit page" opens the editor, and every save is a revision with
an author. The context column offers "add to project" (`part_of`).
From code or the MCP door the calls are `write_page`, `append_page`
and `get_page`.

    PUT  /page/{slug}        {text, title?, kind?, author?, note?, annotates?, part_of?}
    POST /page/{slug}/append {section, heading?}         # the agent's way in
    GET  /page/{slug}, GET /page/{slug}/revision/{n}, GET /pages?kind=
    POST /project/{slug}/members {doc_id}

An agent revision over a human one is refused (HTTP 409, MCP error).
`append_page` adds a section instead. A `synthesis` page (ontology v4,
`docs/ontology-v4.md`) draws on several sources: its `annotates` list
becomes `synthesizes` edges, and extraction may give it claims the
papers support or contradict. Pages are extracted and embedded like any
document, so a topic page's concepts enter the graph. The extractor's
header carries `Kind: page` or `Kind: project`.

Two kinds of page keep themselves (`prax.questions`, `docs/ask.md`
"Standing questions"). A `question` page holds an answer the door asks
again when the library learns something about it: a document that
arrived since ranks for the question, or shares two of the answer's
entities, or a source was read again. The new answer is an agent
revision whose note names what changed; the sections you appended
under the answer are kept. A `briefing` page is the day's "What
arrived". Neither is ever a search hit or an ask's evidence, because
both are the model's own words.

    prax ask --answer --stand how does ADAA handle a stateful nonlinearity
    prax questions                      # each question, and what is new for it
    prax questions --ask [SLUG] [--force]   # ask again what is due, as a job
    prax questions --briefing           # the day's page too
    schedule:
      questions: "06:30"                # in prax.yaml: the check daily, the briefing after

A standing question can also live inside a page of your own, between
your notes, as an ask block (`docs/ask.md` "Ask blocks"). It is two
HTML comments, which the editor's "+ standing question" writes for you:

    <!-- prax:ask id=q1 "how does ADAA handle a stateful nonlinearity" -->
    <!-- /prax:ask id=q1 -->

Saving the page answers it: the interior is filled, and the tail is
closed with the interior's hash and the day. From then on it is checked
and re-asked like a question page. The interior is replaced; nothing
outside the markers is touched. If you edit inside the block, the pass
leaves it ("edited by hand"). "answer anew" in the UI, or `prax
questions --ask SLUG#q1 --release`, replaces it. A `<!-- prax:keep
-->…<!-- /prax:keep -->` region inside the block survives every answer.
`prax questions` lists blocks as `slug#id`. A page's `[title](#doc/N)`
links are its `annotates` edges.

## 3h. Local models (optional)

A local model runs in **llama-server**, llama.cpp's HTTP server, on the
machine with the GPU. prax talks to it as an `openai` model in
`prax.yaml` (section 3k). Nothing of the model lives in prax's own
process: the door stays lean (invariant 7), the worker stays small, and
one server serves extraction, titles and ask at once through its slots.
The measured choices are in `docs/eval/extractors-local-2026-09-11.md`
(Qwen3.6-35B-A3B on a 24 GB card, 4–5 s per document with 3 slots) and
`docs/eval/local-llm-2026-09-08.md` (Qwen2.5-7B on an 8 GB card).

    # llama.cpp release binaries (CUDA, Vulkan, Metal or CPU builds):
    #   https://github.com/ggml-org/llama.cpp/releases
    # unpacked to %LOCALAPPDATA%\prax\llama.cpp on Windows, ~/.local/share/prax/llama.cpp
    # or the PATH elsewhere (Homebrew's llama.cpp puts llama-server there);
    # paths.llama_server in prax.yaml names any other place
    prax models fetch server-35b       # repo and file from prax.yaml, once

The server is then a `serve:` block on the model's entry in `prax.yaml`
and a line under `run:`. `prax up` starts it (4b): the port from
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

`prax up --status` shows the role as *starting* until the model is
loaded, which takes three minutes for the 35B when cold, and *up* once
its `/health` says so. Its output is in `<data
dir>/logs/llama-server.log`. The command line prax builds is
`prax.up.llama_argv`: `--flash-attn on`, the KV cache at `q8_0`,
`--n-gpu-layers 999`, `--load-mode mmap`, thinking off, `--metrics`, no
web UI. `serve.extra` appends arguments of your own.

The grammar-constrained line format (`prax.lineformat`) needs a server
that honours the `grammar` field. llama-server does; vLLM does not.
Thinking is off unless `serve.thinking: true`. That matters for models
that think by default (Qwen3.x, Gemma 4), because thinking tokens
would break the grammar. Memory on a Windows host is covered in howto
3l, "Jobs". An 8 GB card fits a 7–8B model at Q4 with an 8 K context.
A board without a usable GPU leaves the steps at `none` or points them
at a server elsewhere on the private network.

**A card that also drives the display.** The 35B-A3B at Q4 with three
8 K slots took 22.1 GB of a 4090's 24.5 GB. The projector with its
compute buffers pushed that to 23.7 GB, the desktop's own programs were
starved, and the display froze (2026-09-14). Measured by the server's
own dedicated memory (`Get-Counter '\GPU Process Memory'`):

| `serve:` | dedicated | speed |
|---|---|---|
| 3 slots, projector, `ubatch: 256, image_max_tokens: 1024` | 21.1 GB | 167 tok/s |
| 2 slots, the same | 20.8 GB | |
| 2 slots, the same, `cpu_moe: 2` (in use) | 20.1 GB | 148 tok/s |
| `cpu_moe: 8` | | 23 tok/s |
| `projector_on_cpu: true` | an image took minutes to encode; do not | |

The slot count changes little: a `q8_0` 8 K slot is about 0.3 GB.
`cpu_moe` is the dial that matters. It keeps the expert weights of the
first N layers in RAM, which is a MoE model's bulk. 2 costs a tenth of
the speed, 8 most of it. The desktop wants 3–4 GB for itself.

Memory on Windows (2026-09-12, a 22 GB model on a 32 GB machine): the
driver backs every VRAM allocation with system commit, so the server
charges 20–30 GB of commit whatever the load mode. `mmap` keeps that at
about 22 GB, because the model's pages are file-backed and evictable.
With IDEs and a browser open, the commit limit runs out first, so a
page file of at least twice the RAM is the setting that matters.
Extraction prompts are about 3,500 tokens in and 1,100 out, so 8 K per
slot is the floor. What a slot costs at 16 K and beyond is in
`deploy/README.md`, "The card on the desktop".

**The mathematics, as LaTeX: marker.** `pymupdf4llm` drops a display
equation; in a two-column paper it is usually a vector drawing. The
library's text for its most equation-heavy papers held 672 references
to numbered equations and 18 `$` characters
(`docs/eval/marker-equations-2026-09-15.md`).
[marker](https://github.com/datalab-to/marker) reads them back as the
paper's own LaTeX, `$$…$$` on a line of its own. The chunker makes a
`formula` chunk of each (3l). marker's tables come out as tables. marker
is a heavy install (1.3 GB of torch and surya; the weights are RAIL-M,
the code Apache 2.0) and never one of prax's dependencies. It lives in
a venv of its own, and its server is a role of `prax up`:

    python -m venv %LOCALAPPDATA%\prax\marker-venv        # ~/.local/share/prax/marker-venv elsewhere
    %LOCALAPPDATA%\prax\marker-venv\Scripts\pip install marker-pdf fastapi "uvicorn[standard]" python-multipart

    run:
      marker: {venv: C:/Users/you/AppData/Local/prax/marker-venv, on_demand: true}
    # parse:
    #   marker_url: http://127.0.0.1:8765   # [PRAX_MARKER_URL] the server the extractor sends PDFs to
    #   marker_mode: fast                   # [PRAX_MARKER_MODE] balanced: the vision model lays out too

The server reads through llama.cpp's server with `ngl` layers on the
card (99, all, by default; `ngl: 0` for the CPU). It wants about 5 GB
of the card, which does not fit beside a 20 GB model on a 24 GB card.
Hence `on_demand`: declared, started when wanted. Measured 2026-09-16
on the 4090: **2 s a page** through the server against 33 s on the
CPU. The library's 250,000 pages are still days, but the papers whose
formulas matter are an evening:

    prax reread --extractor marker --maths 6 --mime application/pdf --dry-run   # the mathematical papers: how many
    prax up --stop llama-server          # the card free
    prax up --start marker               # ten seconds, then a minute for its first request
    prax reread --extractor marker --maths 6 --mime application/pdf --wait --timeout 420   # or --ids 9549 9813 …
    prax up --stop marker                # its llama-server ends with it
    prax up --start llama-server         # the formula readings the door asked for run once it is up
    prax readings --wait                 # until those are read too; then the extraction follows

Six lines, the same in bash, zsh and PowerShell (`;` between them for
one line), and nothing else. `reread --wait` prints the count as it
moves and returns when no marker request waits (exit 2 on the timeout,
minutes). `prax readings` shows the queue per extractor whenever you
look. `prax up` is the same swap on every platform. The night of
2026-09-16 did this with two hand-written watcher scripts that guessed
at "stuck" and "nothing moved in an hour", and both guessed wrong. The
queue itself is the signal.

`--maths` is the density of references to numbered equations in the
prose: a "(4)" between words, counted per 10,000 characters, with at
least 15 of them in the document. 10 is a mathematical paper, 6 a paper with equations, 3 anything
that numbers a few. This library has 111, 281 and 606 PDFs at those
marks: 40 minutes, two hours and four and a half hours of the card. A
marker read that produced display equations asks the door for their
readings itself (the `formulas` step named, a local model), so an
evening is the two swaps and the one request. The extraction follows,
since the text changed under it. A reading whose server is the paused
one waits, deferred, and comes round once the card is back (7).

**The pictures of a scanned book.** A scanned page is one image, so
the figure finder has nothing to point at. marker's layout finds the
figures inside the page and crops them, and prax keeps those crops. The
parse inlines each as a data URL captioned `Picture on page N`, with
the "Figure N" line under it when there is one. The door files it in
the archive as a content-addressed artifact before indexing, and
references it like any figure. This is the one kind of figure not
served out of the original, since the original holds only the page.
The vision pass reads them with `prax reread --extractor figures --mode
all --ids …` (`all`, because their captions do not claim a figure), a
worker fetching the picture from the door. A figure-refs pass keeps
them. A New Kind of Science, read again with marker, gets its pictures
this way.

`marker` is an explicit extractor, never a default or a fallback. It is
asked for per document, stamped `marker/2.0.0` (the version read from
the role's venv, `+balanced` for the other mode), and reversible
through `parse_history`. What comes back: the mathematics, the tables,
headings. marker's own image references are dropped, because they name
files it did not write here. prax's figure references are placed by
hash as for every PDF, so the figures keep their readings. A two-part
definition set side by side may come back as a small table; that is
marker's reading of the layout, kept as it said it.

**A reading for a formula.** A display equation is a `formula` chunk
with its LaTeX and its number (3l). The LaTeX embeds to noise: a
person searching says "Shockley's diode equation", not
`\frac{a-b}{2R}`. So a formula gets a reading from a text model, the
way a figure gets one from the vision model. The reading is one to
three sentences under the equation, in the document's own terms, with
the name the equation goes by when it has one. `steps.formulas` names the model
(`none` by default). The `formulas` extractor writes the readings into
the text. It is asked for on a document's page ("read again… →
formulas"), or over every document holding an unread equation, which
the `unread-formulas` ailment counts:

    steps:
      formulas: {model: server-35b}

    prax reread --extractor formulas --unread-formulas
    prax reread --extractor formulas --read-formulas --mode again   # a better prompt or model: this model's earlier readings replaced, another's kept

The 35B reads about one equation a second. The eight marker papers'
537 equations took ten minutes, and "Shockley diode equation" then
brings the formula itself. The web UI typesets the LaTeX with KaTeX,
vendored, nothing fetched: every formula chunk with its number and the
source a click away, the inline maths of a document that carries any,
and an answer's maths. A `$` before a digit is left as the price it is.
The stamp is `formulas/1+<model>`. The `parse.formula_readings` setting
(`PRAX_FORMULA_READINGS`) is the mode, `new` or `again`, for one run. A
model is only ever shown the equation and the prose around it, never
another equation or an earlier reading.

**One card, several jobs.** The same loaded model serves extraction,
titles, ask and, with its projector, images, so one server is the
whole local side. Two *different* models on one card are sequential.
llama-server's router mode (`--models-dir` or `--models-preset`, with
`--models-max 1`) loads the model a request names and unloads the
other. Each change of job then costs a reload, 10–30 s for 22 GB. A small second model fits beside the big one: the reranker
below (0.6 GB) ran next to the 35B, at 23.9 of 24.5 GB.

**Context per slot.** Every slot gets `n_ctx` tokens. The server's `-c`
is `slots × n_ctx`, split evenly, so 2 × 16 K gives a document 16 K. A
text whose script tokenizes densely (Arabic, at about a token per
character) can overrun prax's 16 K-character budget. For those, set
`n_ctx: 24576` with `slots: 1` for a while, run `prax up --restart
llama-server`, and run `prax work --steps extract --scope all` while it
is up. That was the way for three books on 2026-09-13.

**Load figures.** `--metrics` (on by default in the script) exposes
Prometheus text at `/metrics`. The door's `GET /models/servers` reads
it, with `/props`, for every `openai` model in `prax.yaml`. The Jobs
page shows each server: model file, slots, whether it sees images,
requests running and waiting, tokens per second, tokens read since the
start and how many of them came from the prompt cache.

**A reranker in llama-server.** A second server role starts the same
binary with a cross-encoder GGUF (`--reranking`, one slot, its own
port). `rerank: {model: server, url: http://127.0.0.1:8081}` in
`prax.yaml` (or `PRAX_RERANK=server`) rescores the top hits through it,
92 ms for ten candidates on the GPU:

    models:
      ranker: {kind: openai, base_url: http://127.0.0.1:8081/v1, model: bge-reranker-v2-m3,
               repo: …, file: bge-reranker-v2-m3-Q8_0.gguf, serve: {reranker: true}}
    run:
      reranker: {model: ranker}

Measured 2026-09-13 on the library's 62 queries
(`docs/eval/rerank-server-2026-09-13.md`): bge-reranker-v2-m3 at depth
10 scores hit@1 0.74 and MRR 0.83, against 0.85 and 0.90 for the fused
list alone. Paraphrase and structure queries gain a little, keyword
queries lose a lot, as with the ONNX rerankers on 2026-09-08. Reranking
stays off. The route is there for a document-aware candidate (title,
heading path, chunk) later.

## 3i. Ask: questions answered from the library

What the asking model may do, what it may not, what each move costs
and how to steer it is in [`docs/ask.md`](ask.md). This section is how
to set it up.

`prax.ask` turns a question into a bundle: one passage per document
from the hybrid search, plus what the graph records about those
documents. It hands the bundle to a model that answers with `[n]`
citations. Which model is the host's choice:

| `PRAX_ASK` | who answers |
|---|---|
| an `openai` model | a llama-server or vLLM on this or another machine; the door loads nothing (section 3h). A 7B answers in about 20 s on an 8 GB card, a 35B-A3B in a few seconds on a 24 GB one |
| a `claude` model | the API, effort low, about a cent per question |
| `none` | nobody: the bundle comes back for the caller's own model |

The step is `ask` in `prax.yaml` (section 3k); `PRAX_ASK=<name|none>`
sets it for one run. Without a file it is `none`, so the serving board
answers with the bundle. On the batch host:

    $env:PRAX_DATA_DIR = "C:\prax-data"
    uvicorn prax.api:app --port 8000     # steps.ask.model in prax.yaml (3k)

Then use the Ask tab in the UI, or:

    POST /ask {"question": "...", "limit": 8, "doctype": null, "backend": null}
    GET  /ask/config
    POST /ask/save {"slug": "reverb", "heading": "...", "result": <the /ask response>}

`backend` overrides the host setting for one question. The response
carries the passages (chunk and document ids, text), the facts, the
answer, the model, the citations it made (invented numbers are
dropped), token usage, seconds and cost. `save` appends the answer to
a page as the agent, under the question as heading, with a source list
linking the cited documents and `annotates` edges to them. A human's
text on the page is never touched. Over MCP the `ask` tool returns the
bundle by default, so Claude Code answers itself; with `answer=True`
it runs the host's model.

**Surfing.** A model that answers does not have to take the first
search's eight passages as they come. With `steps` the model works the
library first (`prax.surf`). `steps` comes from the composer's
"steps", `prax ask --steps`, or the request's `steps`; the host's
default is `steps.ask.steps` in `prax.yaml`, 8 out of the box, and 0
is the one-shot answer. Each step the model writes a note and one
action:

- `search` again with better words;
- `read` on where a passage stopped (`read: [3]`), or a document a
  result named (`read: doc 4080`). With words after either, it reads
  the part of that document which holds them (`read: doc 4080
  delay-free loops`). That is the only way into a long paper the graph
  pointed at, whose start is a title page;
- `facts` of a passage's document;
- `walk` the graph from an entity: its relations and the documents
  behind them;
- `similar` documents;
- `drop` passages that are beside the point;
- `answer`, when the passages kept say enough.

A grammar holds a local model to the two lines and to the passage
numbers and document ids it has seen. The door's own search of the
question is step 0. The answer is then written from the passages kept,
under their loop numbers, with the ask prompt above.

Two budgets bound the surf: the steps, and `tokens` of reading. The
reading budget comes from the composer's "reading", `--tokens`, or
`steps.ask.tokens`. The default is 4,000 for a local model. The ceiling
is what the model's context holds beyond the prompt's overhead: 14,184
tokens for a 16 K slot, 16,000 default and 60,000 ceiling for Claude.
The ceiling comes from `n_ctx` under the model in `prax.yaml`, which is
also the slot `prax up` gives the server, so the two cannot disagree. A
server started by hand with a larger slot buys nothing until `n_ctx`
says so. `n_ctx` above the true slot only means the server refuses the
answer, which is then cut to fit and asked again.

What a card can hold is arithmetic. The cache costs
`full_attention_layers × kv_heads × (key_length + value_length)` values
per token. For Qwen3.6-35B-A3B that is 10.6 KiB at `q8_0`, because 10
of its 40 layers are full attention and the rest are SSM layers with a
fixed state. Two 16 K slots are therefore 0.33 GB of the 4090's 24 GB.
The rest of that card's budget is measured in `deploy/README.md`. The
prompt grows by appending, so a llama-server's prefix cache makes a
step cost its own tokens only. On the 4090 the 35B-A3B takes two to
four seconds a step and a whole surf twenty to forty seconds.

The result carries the `trail` (each step's note, action, what it
brought, seconds), `steps`, `dropped` and `reading_left`. `save` keeps
the trail on the page under "How it was found". With `stream: true`
the door answers one JSON object per line as it goes: `step` events,
`answering`, then `answer` with the result, or `error`. That is what
the UI and the CLI show while the model works. A client that
disconnects stops the surf at its next step.

    prax ask --answer --steps 12 --tokens 6000 what does ADAA do to a stateful nonlinearity
    prax ask --answer --steps 0 quick question        # no surfing: the first search alone

## 3j. Titles worth the name

Half the imported titles were file names. Standalone Zotero attachments
come as `<md5>-slides.pdf`, items without metadata as `Unknown - 2002 -
No Title.pdf`, and conference papers arrive in ALL CAPS. A title is
what a search hit, a citation in an answer and a `paper` entity are
called, so the titles step repairs them. A capture is repaired as it
arrives; the library with:

    prax work --steps titles --scope all       # then embed, for the document field

ALL CAPS titles are recased by rule (`titles.recase`: stopwords, known
acronyms). File names go to the titles step's model (`prax.yaml` or
`--model`). A local server keeps everything on the machine, at about a
second per document. The model gets the first 1,500 characters of
text, the file name, the first Markdown heading and the PDF metadata
title as hints. It answers with the printed title or, for a course
sheet or a manual, a short descriptive name in the document's
language. `meta.title_confidence` is `high` when the title's words
occur in the text and `low` when the model described the document.
Documents without text keep their file name.

Every change goes through `store.retitle`. The old title stays in
`meta.title_history` with its source. `meta.title_source` names who
wrote the current one; the Zotero importer leaves such a title alone on
refresh. The `paper` entity carrying the old title is renamed, or
merged into the entity of the new one, so its edges follow. The
document field is refreshed, which queues the document vector for the
embed step. The document page shows the former title. A wrong repair
is fixed by calling `store.retitle` with the right title and
`source="human"`.

## 3k. What this host does: `prax.yaml`

One file in the data directory holds what a host chooses. `PRAX_CONFIG`
points elsewhere; `prax.example.yaml` in the repo is the template. The
sections are `models` and `steps` (which model does which step),
`domains` (which ontology modules a document is read against),
`embeddings`, `vectors`, `rerank`, `parse`, `citations`, `door`,
`ontology` and `paths`. Everything has a default, so the file may hold
only what differs.

Each setting can still be given as an environment variable for one
run, and the variable wins: `PRAX_EMBED=hash pytest`, `PRAX_VEC_DTYPE=i8
prax serve`. The names are in `prax.example.yaml` beside each setting,
and `prax.config` is where they are read. Some things live in the
environment and nowhere else: `PRAX_DATA_DIR` (it is what finds the
file), `PRAX_CONFIG`, `PRAX_TOKEN` (a secret), `PRAX_DOOR` (which door
a client talks to), and the per-run switches `PRAX_<STEP>`,
`PRAX_OFFLINE` and `PRAX_DEBUG`. A section the code does not know is an
error, not a silent typo.

Every model-assisted step (`extract`, `promote`, `ask`, `titles`,
`vision`, `adjudicate`) takes its model from the same file. `models`
names backends; `steps` assigns them:

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

The kinds are `claude` (the API, key in `ANTHROPIC_API_KEY`), `openai`
and `stub` (tests). An `openai` model is any OpenAI-compatible server:
`llama-server` on this or another machine (section 3h), vLLM, or a
hosted API. For a hosted API, `api_key_env` names the variable that
holds its key and `price` gives USD per million input and output
tokens for the cost lines. `n_ctx` says what context a slot has. Names
that need no file: any `claude-*` id, `stub`, `none`.

Precedence per step: `PRAX_<STEP>` in the environment (a model name or
`none`), then the file, then the default. The defaults are
`claude-opus-5` for extraction, `none` for ask and titles, Sonnet 5 for
vision, and `none` for adjudication. `PRAX_<STEP>_MODEL` swaps the
Claude model id for a step that resolves to Claude, as before. A
runtime is built once per process however many steps name it. The
model itself lives in its server.

The extraction step is the one to move when a GPU box is around. Add
an `openai` model with its address and a `serve:` block, and name it
under `run:` so `prax up` starts the server (3h). Thinking is off
there, which matters for Qwen3.x and Gemma 4: they think by default and
would break the grammar. The measured choices are in
`docs/eval/extractors-local-2026-09-11.md`. Point `steps.extract.model`
at the model, and `prax work --steps extract --scope all --workers
<slots>` runs the same prompt and grammar against the server.
llama-server honours the `grammar` field; vLLM does not, so use a
Claude-kind model or llama-server for extraction. The door stays lean:
the model lives in the server's process, not the door's (invariant 7).
`GET /ask/config` shows what the door resolved.

## 3l. Captures: uploads, sent pages, fetched URLs, the drop folder

Everything that is not a curated import comes in as a capture
(`prax.inbox`). `meta.source` says how (`upload`, `capture`, `inbox`).
`meta.capture` says when and in which send. The domain set comes from
the request, the folder, or the `domains:` rules in prax.yaml. Text and
HTML are searchable at once, because trafilatura is light enough for
the door. PDFs and images are archived and wait for the parse queue on
the batch host. A page captured twice with the same bytes is one
document. A changed page is a new document whose
`meta.previous_capture` points at the last one with the same canonical
URL (fragment and tracking parameters stripped).

A page sent again is one document even when its bytes differ. The
markup of a page differs between two visits, so its hash does, but the
extracted text does not. Before the door registers a new capture, it
compares its chunk fingerprints (`store.similarity`, Jaccard over
hashed chunks, 0.9 or above) with the earlier live captures of the same
canonical URL. A match is noted on the earlier document
(`meta.recaptured`). A snapshot arriving for a page held only as a bare
DOM replaces it. A page that changed in between is a new document with
`meta.previous_capture`. Until 2026-09-15 a page with figures compared
short of the threshold against itself, so a page sent twice became two
documents. The `dedupe` pass of `prax maintain` (3n; nightly) handles
what the check missed: it keeps one capture per URL (a snapshot, then
an extracted one, then the oldest) and retires the rest as duplicates
of the keeper.

Retiring takes a document out of search and the graph
(`store.retire_document`; "retire…" on a document page; `POST
/doc/{id}/retire`). Its chunks and retrieval field go, its edges end,
and its open review items close. The row, the archived bytes and the
text artifact stay. `meta.retired` says why, and of which document it
was a duplicate. **Retiring as a duplicate is a union.** What the
duplicate holds and the keeper lacks moves to the keeper first: edges
with their evidence and producer, open review items, tags, domains,
the summary, the extraction stamp. Only what both hold is ended on the
duplicate. Two extractions of one page therefore become one document
with every fact either found. Batch jobs and the browse list pass
retired documents by; `GET /documents?retired=1` lists them.
"un-retire" re-chunks the text and brings the document back.

The ways in:

- **The Inbox view** (`#inbox`). Drop files or pick them, choose
  domains and tags, or paste a URL for the door to fetch. The list
  below shows the latest captures with their state (pending, indexed,
  extracted).
- **The door.** `POST /ingest/file` (multipart: `file`, `title`,
  `domains` and `tags` comma-separated, `session`). `POST /ingest/html
  {url, html, title, domains, tags, session}` for a page as a browser
  rendered it; this is the extension's path (`docs/extension.md`).
  `POST /ingest/url {url, …}` fetches server-side. `POST /ingest`
  (text) takes `domains` too. `GET /inbox` lists recent captures.
- **The browser extension** (`clients/browser-extension/`,
  `docs/extension.md`). "send this tab" posts a self-contained snapshot
  (SingleFile: images, fonts and styles inlined, scripts removed).
  "send all tabs in window" does the same under one session id. A PDF
  tab is fetched again inside the browser, with its session, and
  uploaded, so paywalled PDFs arrive too. Load the folder unpacked
  (Waterfox and Firefox: about:debugging; Chrome: chrome://extensions)
  and set the server and token in its options. `node
  scripts/extension_bed.mjs --browser both` exercises it end to end in
  headless Chrome and Firefox against a throwaway door.
- **A paper from its abstract page** (the extension,
  `docs/extension.md`). The citation tags name the PDF and the ids.
  The PDF is uploaded with `meta.doi`, `meta.arxiv` and
  `meta.creators`, which `prax import citations` joins on. A PDF
  already held gets the ids filled in.
- **A video** (the extension on a YouTube watch page,
  `docs/extension.md`). It arrives as the transcript with a frame every
  so often, as one HTML document of prax's own shape: `POST
  /ingest/html` with `mode: video`, and what the extension knows of the
  recording as `video`. The door parses it with the parser the
  document names (`meta.parser`). The passages carry their moment
  (`locator.time`). The frames are figures, which the vision pass reads
  with a prompt of their own: the moment, the words spoken around it,
  and "transcribe the slide's text as written", so a talk's slides
  become searchable text. An automatic transcript is punctuated first
  by the polish step (`steps.polish`, a local model): sentences,
  capitals, the fillers dropped, nothing else changed. A paragraph the
  model rewrote keeps its raw form. The `unpolished-transcripts`
  ailment lists the ones still raw; `prax reread --extractor polish
  --unpolished` does them all. `doctype=video` finds them. The
  document's page shows the player: the first frame as a poster until
  you press play (nothing is fetched from the provider before that),
  then the provider's embed. Every passage's moment is a link that
  seeks it. A search hit or an ask's source says "at 12:34" where a
  paper's would say "p. 7".
- **Claude Code.** The `capture_url` MCP tool.
- **The drop folder** `data/inbox/`. The door consumes it by itself
  while it runs, every `PRAX_INBOX_SCAN` seconds (20 by default; 0
  turns it off). Any file put there is registered through the store
  without another process.

**The worker** takes every capture the rest of the way. It asks the
door for work, does it with the models of its own prax.yaml, and posts
the results (`prax.work` on the door, `prax.worker` here; howto 4 for
the door):

    python scripts/work.py                       # one pass against the local door
    python scripts/work.py --watch               # keep going (the usual way)
    python scripts/work.py --door http://board:8000 --watch   # from another machine
    python scripts/work.py --scope all --steps extract --limit 20   # a backlog pass

There are five steps, each a batch the door hands out with a lease:

- parse: the worker fetches the original and posts the text;
- titles;
- extract: the door sends the prepared prompt input, the worker posts
  the triples;
- embed: chunk and field texts out, vectors in, into the door's delta
  index;
- resolve: one entity type's names out, the pairs close by name
  embedding in, a type a week. This is the likely tier of `prax
  resolve`.

The worker never spends money: a step whose model is the Claude API is
skipped with a note, and the promote pass is the way to that model. It
never opens the database. It never touches the curated imports unless
`--scope all` says so. It announces itself as a job with heartbeats, so
the Jobs view shows it wherever it runs. `--no-titles`, `--no-extract`,
`--no-embed`, `--no-parse` and `--no-resolve` switch steps off.
`promote`, `typing` and `adjudicate` run only when named.

A reading or extraction whose server is loading or paused (a 503, a
refused connection, marker's server not up) is not the document's
fault. The worker reports it as "not yet". The door keeps the item
leased for ten minutes and hands out the rest of the queue meanwhile.
The item comes round again once that lease runs out. A queue of
formula readings waiting for llama-server therefore does not hold back
the marker readings behind it while the card is marker's.

A parse batch lands one document at a time: each text is posted the
moment its extractor is done, not when the last of the batch is. The
worker beats the session every five minutes while it reads, renewing
the leases of what it still holds. A book that takes marker an hour is
neither handed out again at the lease's fifteen minutes nor has its
session reaped for silence at thirty.

The worker also uploads this machine's `Downloads/prax-inbox/` when
that folder exists; `--also` names others. Sidecars are included. The
browser extension saves a file there when a site hands the file to a
navigation only (`docs/extension.md`).

A file in `inbox/<module>/` (say `inbox/family/`) lands in that domain.
`<file>.json` next to a file is a sidecar with `title`, `source_url`,
`domains` and `tags`. Files still being written (younger than two
seconds, or `.part`/`.crdownload`) wait for the next scan. Consumed
files are removed; the archive holds their bytes. What the store
refused goes to `inbox/failed/`. A folder that is not prax's own, such
as a download folder or a project's PDFs, is uploaded with `prax add
<folder> -r --domain research`. Every file under it is uploaded.
Nothing is moved or removed, and a second run finds the files already
known by hash. An uploaded PDF shows "pending" in the Inbox view until a
worker has been over it, or "no text found" when every extractor tried
and found none. The view refreshes itself while something is pending.

### Jobs, and a UI that follows

Every batch pass announces itself in the `jobs` table (`store.Job`,
migration 0009): name, host and pid, a heartbeat, done and total, a
note. `GET /jobs` and the Jobs view show what runs and what ran. A job
without a heartbeat for ten minutes is marked stale. The door closes a
job whose process is gone or whose heartbeat stopped half an hour ago.
The rows are bookkeeping; nothing reads them to decide what to do.

The same view shows what the door's host has left: free RAM and commit
headroom (`prax.hostinfo`, no dependency). The worker's heartbeat
carries its own footprint, under a gigabyte between passes. Commit is
the number to watch on a Windows batch host. A GPU model server charges
system commit for the VRAM it fills: a 22 GB model is 22 GB of commit
with `--load-mode mmap`, 30 GB without. With IDEs and a browser open, a
32 GB machine reaches its commit limit before its RAM runs out, and
processes then fail to start. Give such a machine a fixed page file of
at least twice its RAM, or close the big programs during a long pass.
A watching worker and a second one-off worker (`prax work --steps
extract --scope all`) share the model server's slots, three on the
desktop, and that is the limit. A second GPU model or a second model
server does not fit next to a 22 GB one on a 24 GB card.

**When the UI feels slow.** The door logs every request over two
seconds to `logs/door.log`, for example `slow: GET /search took 12.1 s
(0 other requests in flight; jobs: worker; fts 0.3 s, embed 11.2 s,
…)`. The line says what else was in flight, which jobs ran, and for a
search which side took the time. Read that line before guessing. What
it has said so far, and what was done about each:

- Reads waited on the store's write lock. They take none now.
- The embed hand-out scanned a million chunks under the lock. It counts
  first now.
- The index merge rewrote a gigabyte under the index lock. It is built
  beside the index now.
- The door embedded each query on the card, behind the worker's model.
  `embeddings.door_providers` is the CPU by default, three milliseconds
  a query.
- The keyword side scored two thirds of a million chunks for every "a",
  "in" and "and" in a natural-language query, on a 2 MB page cache
  (18 s cold). The stopwords are left out of the match expression now;
  the embedder still sees them. A connection has 64 MB of cache and the
  file mapped (`door.sqlite_cache_mb`, `door.sqlite_mmap_mb`).
- The vector side took 4–6 s while a heal opened ten thousand PDFs. The
  mapped index had been given up to those reads, and a search
  page-faulted its way through the graph. `vectors.serve: memory` loads
  the index instead: 2.7 GB resident for 1.4 M vectors on a host with
  the RAM, and 11–50 ms a query after. The door loads it at startup,
  and a merge loads the new file outside the lock, so no search waits
  on a load. The first search after a restart once waited 100 s for it
  while llama-server read its model from the same disk.
- The keyword index gets the same treatment: it is read through once
  at startup (0.7 GB, a second). On a host where llama-server's model
  file owns the operating system's cache, the first searches after a
  start read their posting lists from disk. One query took 25 s: it
  held a lone "2", which is in two thirds of the chunks. A lone
  character is left out of the keyword side now, as a stopword is.

Python is not on that list. The stalls were locks, shared devices and
a cold cache. The heavy lifting (SQLite, usearch, ONNX, the model) is
native already.

The UI polls `GET /changes` every ten seconds while its tab is visible.
The answer is a stamp made of SQLite's `data_version` (another process
committed) and the door's own write count, plus the number of running
jobs for the badge in the navigation. When the stamp moved and a
listing is open (inbox, browse, a document, review, promote, jobs,
pages), the view is rendered again in place, keeping the scroll
position, and never while something is being typed. One small query
per ten seconds per open tab is the whole cost.

## 3l½. What runs on its own, and what you run

With the door up and a worker watching (`prax work --watch`, on the
machine with the models), a capture goes all the way on its own. That
holds for an upload, a page from the extension and a file in the drop
folder alike:

| on its own, for every new document | where |
|---|---|
| parse: the figures found and referenced, ligatures and Symbol-font glyphs turned into letters, page markers, the text cleaned | the parsers, `store.index_text` |
| a title where the file name was one; the graph extraction under the current ontology; the typing rules over what it queued; the vectors | the worker's steps (titles, extract, embed) |
| an image described; a parsed document's figures read by the vision model; a video's automatic transcript punctuated (the polish step) and then its frames read; a marker read's display equations read (the formulas step) | reading requests the door places itself after a text lands (`pipeline.follow_ups`), for a capture parsed at ingest and a worker's parse alike, when the step's model is a local server. Nothing is spent unasked. With Claude as the model these stay yours to ask for |
| a request placed on a document's page ("read again…") | the worker, before the pending captures |
| a capture nothing here could read, such as a scan without a text layer: the first extractor refuses it, the fallback finds nothing. It is tried once and then left. The inbox says "no text found" and the Health panel lists it (`unreadable-documents`). OCR or the vision model over its pages is yours to ask for on its page | the door, which does not hand a run chain out again |
| jobs whose process is gone closed; the drop folder consumed | the door |

Some things stay a command or a click, because they cost money, time
or a decision:

- the promote pass (`prax work --steps promote --spend`, Claude over
  the flagged documents);
- the model typing pass over the review queue;
- OCR of scans, and the vision model over whole pages
  (`vision-pages`);
- a re-read of the whole library at once after a parser changes (the
  `--upgrade` runs above); the nightly pass below does it a few at a
  time instead;
- the ontology migrations;
- the repairs: `prax heal`, or the Health panel at the foot of the
  Jobs page, which shows what every ailment finds right now and
  repairs the repairable ones with one button. That is a job; nothing
  is deleted.

The curated imports' own backlog (Zotero, GitHub, chats) is a worker
with `--scope all`: the nightly one, or `prax work --scope all` now.

## 3l¾. Keeping the library current: what is versioned, and the nightly pass

The code moves and the data has to follow. Everything a pass produces
carries the version of what produced it, and every earlier result
stays addressable:

| what | versioned by | where the history is |
|---|---|---|
| the original | its sha256; it never changes | the archive |
| the text | the extractor's stamp `name/version[-rN][+variant]` in `meta.text_source`. `-rN` is prax's own revision of that extractor, bumped whenever its output changes (the figures it finds, a cleaner reading, page markers) | `meta.parse_history`: every attempt with its extractor, outcome, size, seconds, and the artifact's `text_hash`. An earlier text is still in the archive under its own hash, never overwritten |
| the readings (an image, a page's figures, whole pages) | the reading model's name in the stamp (`vision/…+server-35b`). Readings are additive: a second model's is kept beside the first | the reading itself carries each model's name |
| chunks and vectors | disposable, derived from the text. `chunk_embeddings` says which chunk has a vector from which model. Chunks whose text did not change keep their ids and vectors across a re-index | none needed |
| the graph | `ontology_version`, `producer` and `run` on every edge, and the bi-temporal columns `valid_from`, `valid_to`, `ingested_at`. A better pass ends the old edges and writes new ones; nothing is deleted | the `edges` table is its own history |
| the extraction | `meta.extraction` (extractor, ontology version, run, counts) and `meta.extraction_history` | the same |
| a page | numbered revisions with their author | `page_revisions` |

A document is **stale** when its stamp names an extractor whose
revision prax has moved on since (`parsers.behind`). A re-read would
produce something new, or say `same` and cost only the parse. An
annotating extractor whose addition *is* what a revision added says so
(`Extractor.covers`: `figure-refs` covers `pymupdf4llm` r2 and
`trafilatura` r3). After it runs, the stamp moves to that revision, and
the document is not read again for what it already has. The Health
panel counts the stale ones (`stale-parses`). There are four ways to
bring a stale text up to date, from cheapest to dearest:

| way | when | what it costs |
|---|---|---|
| the ailment's repair: "repair" next to `stale-parses` on the Jobs page, or `prax heal --check stale-parses --apply` (a run takes up to 5,000; press again for the rest) | an annotation in the history already made the revision's change (the figure-refs pass over the library ran before stamps moved) | moves the stamp and writes a `stamped` history entry; nothing is read |
| the backlog pass, below | the revision changed what the extractor produces | a re-read per document, a batch a night; `same` when nothing came of it |
| `prax reread --extractor <name> --text-source <old stamp>`: a reading request on every document the old stamp matches, drained by the worker | you want the whole library at the new revision now, not over nights | every document, forced, as the worker gets to them |
| "read again…" on a document's page | one document, now, with the extractor of your choice | that one read |

The backlog pass:

    prax work --scope all --limit 100      # captures first, then a hundred stale ones

In scope `all` the parse step hands out the stale documents after the
pending captures, oldest first, a batch at a time. The worker re-reads
each with the current extractor. The door keeps or upgrades the text
by the usual rule: a suspiciously short new text keeps the old. A
re-read that comes out the same moves the stamp and touches nothing
else.

The pass runs as the worker's nightly pass on the machine with the
models: `nightly: "03:00"` under `run.worker` (4b), or `prax work
--watch --nightly 03:00` by hand. When that hour comes round each day,
the watching worker does one pass over everything, `nightly_limit`
documents a step, then goes back to watching. A worker started after
the hour waits for the next night's pass; it does not run a pass over
everything at noon. A worker not under `prax up` can use cron:

    0 3 * * * PRAX_TOKEN=<token> /srv/prax/.venv/bin/prax work --scope all --limit 100 --door http://127.0.0.1:8000 >> /srv/prax-data/logs/nightly.log 2>&1

Read the token from the environment or a file instead of writing it
into the task. `--limit` is how much of the night the pass may take. A
hundred PDFs is a few minutes. Readings by the vision model, when the
door asks for them after a re-read found figures, follow in the next
watch cycle. A worker already running `--watch --scope all` needs no
task: the same pass is what it does when the captures are done.

The other steps have their own notion of stale, and the same pass
applies it. `titles` reads the documents whose title is still a file
name. `extract` reads the documents not yet extracted under the current
version of their ontology subset, either never extracted or extracted
before a module grew. In scope `all` that is the library's whole
extraction backlog, oldest first, a hundred a night with the local
model. `embed` reads the chunks without a vector from the current
model. `--steps parse,embed` keeps only the texts and their vectors
current and leaves the graph for a pass you name.

What the pass does not do: it does not re-run extractors that are
explicit only (OCR, `vision-pages`, Docling). What was asked for once
is not asked for again by itself. It does not spend money: the worker
refuses a paid model in any step, whatever the scope.

## 3m. Healing what recurs

Extraction at scale leaves the same few kinds of damage behind, and
they come back with every pass. So they have names and a place:
`prax.store.repair`.

    prax heal                              what is wrong (changes nothing)
    prax heal --apply                      repair all of it
    prax reread --extractor pymupdf4llm-ocr --unreadable     what an ailment offers, from the shell
    prax heal --check self-edges --apply   one kind
    prax heal --json                       for a script

The Health panel at the foot of the Jobs page is the same thing with
buttons: "repair" next to each ailment that can be repaired (one kind,
like `--check`), and one for all of them together. One kind at a time
is the usual way. What you meant to leave alone, such as a few unnamed
entities you still want to look at, stays alone.

| ailment | what it is | what repairing does |
|---|---|---|
| `placeholder-entities` | a model copied a word out of its own prompt: "source name", "target name", "unknown", "n/a" | ends every edge they carry; the entity stays as the record of what happened |
| `reference-number-entities` | "[12]", "fig. 3", a bare year. The ontology says a reference number is never a name | ends their edges |
| `mangled-names` | a citation importer left markup or line breaks in a title: `<i>The Origins of Music</i>` | cleans the name, or merges into the entity that already carries the clean one |
| `wire-names` | an extractor's own wire syntax glued to a name: `chord dst_type=concept(confidence=EXTRACTED evidence=…`, `x(dst=y)`. A line the model wrote in the triple format, taken whole (1,612 in the library on 2026-09-17, two of them at the top of the likely tier) | cuts the name at the syntax and cleans it, or merges into the entity that already carries it. A name that was nothing but syntax has its edges ended |
| `twin-documents` | two live documents with one title and the same text: a PDF downloaded twice, a book kept in two prints. Different bytes, so the hash did not fold them (236 pairs in the library on 2026-09-18) | retires the twin into the keeper (more live edges, then the older one). What it holds and the keeper lacks moves over first (`retire_document` with `duplicate_of`) |
| `unnamed-entities` | no name at all, or a whole citation as one. A claim is a sentence and is left alone | ends their edges |
| `self-edges` | an edge from a thing to itself, left after two names were merged | ends them |
| `edges-of-retired-documents`, `review-of-retired-documents` | written by a pass that was already reading a document when it was retired | ends them; resolves the queue items as dropped |
| `unpolished-transcripts` | videos whose transcript is the automatic one as it came, the polish not written yet (captured before the step existed, or while its model was away) | a report with an offer: polish all of them (`prax reread --extractor polish --unpolished`) |
| `stale-extractions` | documents whose extraction was made from a text a later read has replaced (marker over a pymupdf4llm text, OCR over a scan). The graph speaks of a text that is gone | moves the stamp aside so the extract step selects them again. The old reading's edges are retired when the new one is applied. A replacing read does this on the way in now; these are from before |
| `stale-jobs` | a job still marked running whose heartbeat stopped a day ago (the door reaps its own host within minutes) | closes them as failed |
| `unmapped-glyphs` | a text still holding ligature glyphs (ﬁ, ﬂ) or Symbol-font code points (=, ∈, α as private-use characters) from before every text was cleaned on the way in (`prax.glyphs`). Boxes on screen, words search cannot match | re-indexes each from its own artifact, cleaned. Chunks with unchanged text keep their vectors |
| `extraction-failed` | documents the extract step could not read under the current ontology: a prompt the model's slot cannot hold even after the cut, or a server error. The error is kept in `meta.extraction_error`, and the passes leave them out until the ontology moves or a reading succeeds | forgets the errors that were the model server's (loading, down, refused), so those are selected again. A prompt no slot holds stays |
| `stale-parses` | documents read by an extractor prax has revised since. A re-read would produce something new, or say `same` | moves the stamp where an annotation in the history already made the revision's change (figure references placed). The rest the backlog pass reads a few at a time, or `--upgrade` at once (3l¾) |
| `documents-without-an-extractor` | something waiting for text of a kind nothing here can read | a report: install what reads it (3b) or retire it |
| `not-documents` | originals that cannot be what their type says: a macOS resource fork (`._file`), a Windows shortcut, a program, an empty file, a PDF without its header. Registered from a folder that held them beside the real files | retires them (row and bytes stay). The unreadable list is the scans again |
| `uncounted-pages` | PDFs with text whose page count no parse recorded (read before the worker kept `meta.pages`). `thin-texts` cannot weigh them | opens each PDF once and writes its count. Needs pymupdf on the door. A check never opens a file; ten thousand of them took 200 s |
| `thin-texts` | PDFs of five pages or more with under 100 bytes of text a page: scans whose text layer is the cover's, read as if it were the book (a Google Books scan whose only text is its usage page) | a report, with an offer on the panel: OCR over all of them (`prax reread --extractor pymupdf4llm-ocr --thin`; `parse.ocr_max_pages` must cover the longest) |
| `unreadable-documents` | documents every extractor here has tried and found no text in (scans without a text layer). They wait and are not tried again | a report, with two offers on the panel: OCR over all of them, or the vision model over their scanned pages (`prax reread --unreadable --extractor …`). Or retire them |
| `chunks-without-vectors` | the current model has no vector for them | a report: run a worker |

Two kinds of damage the cleaning cannot undo. A glyph the PDF's font
gave no name at all comes out of MuPDF as U+FFFD. A symbol font other
than Adobe's (the F1xx, F2xx private-use ranges) comes out as a code
point nobody can read back. Both are lost at extraction. The UI shows
the first as a small box, not a question mark. Only OCR or Docling can
recover those, and only sometimes. The UI's Literata covers Latin.
Greek, Cyrillic, maths and CJK fall through to the reading stack's
next faces (STIX Two Text, Georgia, Cambria) and the system's own, so
a symbol that is a real character renders. A box that survives the
heal is one of the two lost kinds.

Nothing is deleted. An edge is invalidated, so it keeps its provenance
and its place in history (invariant 8), and a later pass can write the
right one. A review item is resolved as dropped. A job row is closed.
The pass announces itself as a job. `GET /heal` is the looking half of
`POST /heal`, so the UI and a cron line see the same thing the command
does.

Adding an ailment is a `find` (what is wrong, as rows a person can
read), optionally a `repair`, and an entry in `AILMENTS`. The rule this
module lives by: **look before repairing**. The first draft of
`unnamed-entities` flagged every name over 200 characters and would
have thrown away 27 real claims and 94 real citations. The dry run
against the library caught it, which is why the dry run is the default.

## 3n. Maintenance: what the store does to itself

A few tables are derived from the rest and drift unless they are
rebuilt. None of that needs a model or a decision, so it is one pass:
a job on the door, run by the nightly task after the worker's pass, and
by `prax maintain` on request:

    prax maintain                      # every pass
    prax maintain --only acronyms      # one

| pass | what it rebuilds |
|---|---|
| `acronyms` | the acronyms table from every text's "phrase (ACRONYM)" definitions (3d): what a query token expands to. Minutes over a large library |
| `fields` | the document retrieval field (title, kind, summary) of every document. Run it after titles were fixed or summaries written |
| `domains` | the domain set of every document nobody assigned by hand, from the `domains:` rules in `prax.yaml` (3e). Nothing without rules |
| `dedupe` | the duplicate captures of one page, retired as duplicates of the keeper. A union: their facts, tags, domains and stamps join the keeper's first (3l). Row and file kept |
| `review` | the review queue: a replay against the current ontology (a typed item it accepts now becomes an edge), then the typing rules over every open item (3e). What the door does for one document after its extraction, for the whole queue |
| `references` | the citations a document's own reference list makes to documents in the library (3f). The entries under a References/Bibliography heading are read by rules (`prax.references`) and matched against the document field by title, creators and year. The `cites` edges are `EXTRACTED` by a printed DOI or arXiv id, `INFERRED` by a title match with the score in the evidence, `AMBIGUOUS` for each of several candidates within the margin (twins in the library). A document is read once per text (`meta.references`); a re-read retires the earlier edges. Measured in `docs/eval/references-2026-09-20.md` |
| `fts` | the keyword index's segments merged a little (FTS5's `merge`, up to a minute). Every batch of chunks leaves a segment behind, and a term spread over two dozen of them is read from two dozen places when the cache is cold |
| `rechunk` (only with `--rechunk`) | every chunk rebuilt from its text artifact, after a change to the chunker (3c). The nightly has no reason to |

What stays out on purpose: the repairs (`prax heal`, 3m; a person picks
the ailment), the readings and extractions (the worker, with a model),
and entity resolution (the likely merges are a decision). `POST
/maintain {only}` starts the job. The Jobs view shows which pass it is
on.

## 4. Running the HTTP door

    uvicorn prax.api:app --reload --port 8000

### Access

The door checks one shared secret, `PRAX_TOKEN`, on every request
except the UI's static files and `/health`. Generate one and set it in
the service's environment:

    python -c "import secrets; print(secrets.token_urlsafe(32))"
    $env:PRAX_TOKEN = "<the token>"          # PowerShell
    export PRAX_TOKEN=<the token>             # shell

Scripts and the extension send `Authorization: Bearer <token>`. An
extension also needs its origin in `PRAX_CORS_ORIGINS`
(comma-separated, `docs/extension.md`); leave it unset otherwise. The
UI asks for the token once and exchanges it for an HttpOnly session
cookie (`POST /session`, 30 days; `DELETE /session` ends it), so links
to originals work in new tabs. Without `PRAX_TOKEN` the door admits
loopback clients only. That is what a development server needs, and it
keeps a misconfigured deployment closed. On the serving host, bind the
service to the private network's interface with `--host <that
address>`: the LAN, or a VPN address such as Tailscale's `100.x.y.z`.
Never bind a public one. Plain HTTP inside the private network is
fine. Use TLS through a reverse proxy only if the door were ever
exposed. The MCP server talks to the door with the same token.

Endpoints:

| Method | Path | Body / params | Returns |
|---|---|---|---|
| POST | `/ingest` | JSON `{text, title?, source_url?}` | `{doc_id, hash, created}` |
| POST | `/ingest/file` | multipart `file`, form `title?`, `source_url?` | `{doc_id, hash, created}` |
| GET | `/get/{doc_id}` | `offset?`, `max_chars?` | document row plus text |
| GET | `/search` | `q`, `limit?`, `kind?`, `mode?` | list of `{chunk_id, doc_id, title, snippet, score, kind, heading, page, figure}`. `figure` is a figure chunk's image reference, served by `/doc/{id}/figure/{ref}` |
| GET | `/chunk/{chunk_id}` | | one chunk: text, kind, heading, locator, table `data` |
| POST | `/link` | JSON `{src, src_type, rel, dst, dst_type, confidence?, source_doc?}` | `{edge_id}` |
| GET | `/traverse` | `entity`, `hops?` (max 2) | list of edges with types and hop distance |

The same process serves the web UI at `http://127.0.0.1:8000/ui/`;
`/` redirects there. It has search, document and browse views, and the
graph view from Stage 3. It needs no build step. The files live in
`src/prax/ui/`.

A first smoke run from PowerShell:

    Invoke-RestMethod -Method Post http://127.0.0.1:8000/ingest `
      -ContentType application/json `
      -Body '{"text": "Granular synthesis smears transients.", "title": "note"}'
    Invoke-RestMethod "http://127.0.0.1:8000/search?q=granular"

### From another machine on the private network

A door started with the defaults is invisible from the next room, by
design twice over. `prax serve` binds `127.0.0.1`, so the LAN gets no
listener at all: a browser on another machine waits and times out. And
without `PRAX_TOKEN` the door admits loopback clients only, so it would
answer 401 even if it heard them. Opening it takes three things, on the
machine that runs the door:

1. A token in the door's environment, and the same one in the worker's,
   since the worker is a client too:

        $env:PRAX_TOKEN = "<the token>"        # PowerShell; generate one as above
        prax serve --host 0.0.0.0 --port 8000  # or --host <this machine's private address>
        prax work --watch --interval 20        # in another shell, same variable

   `0.0.0.0` means every interface of the machine. A home LAN behind
   the router is fine; the token is what gates it. On a machine with a
   public interface, bind the private address instead.

2. A firewall rule for the port. Windows asks on the first bind when
   the door runs in a console. Started hidden it does not ask, and the
   rule needs an administrator's PowerShell:

        New-NetFirewallRule -DisplayName "prax door" -Direction Inbound `
          -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private

   `-Profile Private` keeps it to networks marked private. Check the
   current network's profile with `Get-NetConnectionProfile`.

3. The token on the other machine. The web UI at
   `http://<address>:8000/ui/` asks for it once and keeps a session
   cookie. The `prax` command takes `--door http://<address>:8000` and
   `PRAX_TOKEN`. The MCP server and the Claude Code plugin read
   `PRAX_DOOR` and `PRAX_TOKEN` (`docs/claude-workflow.md`). The
   browser extension has both in its options (`docs/extension.md`).

A check from the door's own machine through its LAN address, not
loopback, proves the bind and the token together:

    curl -s -o /dev/null -w "%{http_code}\n" http://<address>:8000/search?q=x                       # 401
    curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer <token>" http://<address>:8000/search?q=x   # 200

The door's log (`logs/door.err.log` on the batch host) records a
refused request as one line with the client and the reason. It reads
`refused GET /search from 192.168.178.23: missing or invalid token`.
A line from uvicorn saying `Invalid HTTP request received` is a client
speaking TLS to the plain HTTP port. That is a URL typed with
`https://`, or a browser's HTTPS-first attempt before it falls back,
which is harmless. It can also be an extension whose manifest lets
Firefox's default MV3 policy upgrade its fetches. prax's manifest sets its own policy without
`upgrade-insecure-requests` for that reason (`docs/extension.md`).

Anything that talked to the door without a token before, such as a
local script or this session's `curl`, needs the header from then on.
`/health` and the UI's files stay open.

**HTTP or HTTPS.** Plain HTTP inside the private network is the design.
What travels is the token and the documents, and the private network
is what keeps them private: a home LAN behind the router, or a VPN.
Tailscale encrypts the wire itself, so plain HTTP over a `100.x.y.z`
address is already private. HTTPS on the door is a certificate
problem, not a code one. The door serves it with

    prax serve --host 0.0.0.0 --ssl-certfile cert.pem --ssl-keyfile key.pem

and the session cookie becomes `Secure`. But a certificate for a bare
LAN address has to be one every client trusts. A self-signed one means
a warning in the browser and, worse, a silent failure in the
extension, which cannot click through. Two ways work. One is a private
CA whose root is installed on every client: mkcert, or Caddy in front
of the door with `tls internal`; Firefox needs
`security.enterprise_roots.enabled` or the root in its own store. The
other is a real certificate for a real name: `tailscale cert
<name>.ts.net` issues one for the machine's Tailscale name, which is
the clean answer when the board is reached that way. Neither is worth
doing for a LAN with only your own devices on it. Either is, the day
the door is reachable from a network you do not run.

## 4a. The `prax` command

One command for the everyday work, the same one wherever the door is:

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
    prax reread --extractor X ...     a reading on a selection: --unreadable, --thin, --mime, --text-source, --ids; --wait
    prax readings                     the reading queue per extractor; --wait blocks until it drains
    prax backup D:/prax-backup        copy the store (only what is new)
    prax work --watch                 be the worker for a door
    prax serve                        run the door here
    prax doctor                       when something feels wrong
    prax models                       which model does which step
    prax graph "wave digital filter"  what the graph knows around a name
    prax pages                        the notes kept in the library

It is a client (`clients/cli/`, `prax.client`). Every command is one
HTTP call, and nothing opens the database. `--door` (or `PRAX_DOOR`)
points it at another machine: `prax --door http://board:8000 status`
from the desktop, or `prax work --watch --door http://board:8000` to
drain that board's queue with this machine's models. `PRAX_TOKEN` or
`--token` carries the bearer token when the door asks for one. Every
read command takes `--json` for a script to parse, and colour goes away
when the output is piped.

`prax work` is `scripts/work.py` under a shorter name, and `prax serve`
is the uvicorn line. The one-off maintenance scripts (import, backfill,
resolution, typing rules, rechunk, replay) stay scripts. They open the
database directly and are the known deviation of invariant 4.

## 4b. Always on: `prax up`

Until the store moves to a board (6), the desktop is the server. A
server survives a reboot, and, as it turned out, a closed terminal.
prax owns its process model. `run:` in `prax.yaml` names which of
prax's roles this host runs and with what, and `prax up` keeps them
running. The operating system's only job is to start `prax up` when
you log in and start it again if it vanishes. The same code does the
same thing on Windows, Linux and macOS, and the same tests cover it
there.

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
    prax up --tray           # the same, with a tray icon as its face (see below)
    prax tray                # an icon beside the prax up already running

| role | what | waits for |
|---|---|---|
| `llama-server` | llama-server for the model named, from its `serve:` block (3h) | — |
| `reranker` | a second llama-server with a cross-encoder (`serve: {reranker: true}`) | — |
| `marker` | marker's server from its own venv, the PDF-to-LaTeX reading (3h). `on_demand: true` declares it without starting it | — |
| `door` | `prax serve --host … --port …` (`ssl_certfile`, `ssl_keyfile` for HTTPS) | — |
| `worker` | `prax work --watch`, with `--nightly` for one bounded pass over everything a day | the door, unless `door:` names one elsewhere |

`prax up` starts the roles in that order behind real health gates. The
worker starts once the door answers `/health`. A model server shows
*starting* until its own `/health` says loaded, three minutes cold for
the 35B. What dies is restarted after 1, 2, 4 … 60 seconds; a run of
five minutes starts the count over. The roles are stopped in reverse
order, with `SIGTERM` on Linux and macOS. On Windows a process without
a console can only be ended, which is safe for prax: the database is
in WAL mode, the archive is content-addressed, and every job is
re-selectable. Each role's output goes to `<data dir>/logs/<name>.log`,
rotated on every start with ten kept, and the supervisor's own lines
to `up.log`. It has no port and no state beyond a pid file and a
status file under `<data dir>/run/`, and a command queue
(`run/commands/`, one file per command, renamed into place whole) that
`--stop` and `--restart` write. That is what works the same
everywhere. Children get no console on Windows, and sit in a job
object that ends them if the supervisor itself is killed. Elsewhere
they get a session of their own. Nothing a terminal does reaches them.

The timed passes are not the supervisor's. `maintain` and `backup` run
on the door's own clock, `schedule:`, as the jobs their endpoints
start, visible on the Jobs page. `door.clock_seconds` says how often
the clock looks, 30 by default. The jobs table is the memory: a door
restarted at noon does not run the night again, one that was down at
the hour catches up once, and a pass still running is left alone. The
worker's nightly backlog pass (3l¾) is its own: `nightly:` under
`run.worker`, or `prax work --watch --nightly 03:00` by hand. Give the
worker's pass a head start on the maintenance.

`prax up --install` writes the login entry. On Windows it is a Task
Scheduler task, `\prax\prax up`, run by `pythonw.exe`, which never has
a console. A console program started by Task Scheduler gets a window
for a moment, Windows 11 hands a windowed console to Windows Terminal,
and closing that window is how three services once ended together with
`0xC000013A`. On Linux it is a systemd user unit
(`~/.config/systemd/user/prax.service`, `Restart=always`; `loginctl
enable-linger $USER` keeps it running without a session). On macOS it
is a launchd agent (`~/Library/LaunchAgents/io.github.lodsb.prax.plist`,
`KeepAlive`). All of it is under your own account: nothing system-wide,
no password stored. The services run while you are logged on.

On a desktop the entry runs `prax up --tray` when the tray library is
installed (`pip install prax[tray]`: pystray and Pillow). The icon is
the mark in the tray, with a red dot when a role is down or nothing
runs. Its tooltip names each role's state. Its menu opens prax in the
browser, restarts or stops a role, starts a paused one, opens the logs
folder, and quits, which stops everything in order. `prax tray` alone puts the
icon beside a supervisor already running, or offers to start one; its
menu has "Stop prax" for everything and "Quit the tray" for the icon
alone. A Linux user unit has no display, so the icon is not part of it
there; `prax tray` from a session does the same. The tray is a client
of the supervisor like `prax up --status`: it reads the status file and
writes the command queue, nothing more. Secrets never go into the
entry:

| | where it comes from |
|---|---|
| the token | `PRAX_TOKEN` in the environment (on Windows the *user* variable), or one line in `<data dir>/door.token`. Without one the door answers this machine only |
| the Anthropic key | `ANTHROPIC_API_KEY` in the environment. Where the login entry has no user environment (systemd, launchd), `KEY=value` lines in `<data dir>/up.env`, which every child gets |
| the card's power cap | `nvidia-smi -pl` needs an administrator: a task of your own |

Before 2026-09-16 two shell scripts did this per platform, five
services each. If they installed anything, remove it first, then run
`prax up --install`. Windows: `Get-ScheduledTask -TaskPath \prax\ |
Unregister-ScheduledTask`. Linux: `systemctl --user disable --now
prax-{door,worker,llama-server}.service prax-{nightly,backup}.timer`.
macOS: `launchctl bootout gui/$UID/io.github.lodsb.prax.<name>` for
each. Found on the way and worth knowing: Task Scheduler's "restart on
failure" is about a task it could not launch, not one that ended. A
task exiting 1 with three restarts a minute apart was never run again,
so the old "comes back after a crash" was never true on Windows. `prax
up` is where restarts live now, and they are tested.

## 5. MCP server in Claude Code

`.mcp.json` in the repo root registers the server. Claude Code runs the
command with the project root as the working directory, so the
interpreter path is relative:

    "command": "${PRAX_PYTHON:-.venv/Scripts/python.exe}"

On Linux or macOS set `PRAX_PYTHON=.venv/bin/python` in the shell that
launches Claude Code. Claude Code reads `.mcp.json` at startup only, so
restart it after editing the file.

The server is a proxy. Every tool is one HTTP call to the door
(`prax.client`). The process imports no store module and opens no
database (CLAUDE.md invariants 4 and 5). So the door has to be running,
here or on the board. `PRAX_DOOR` names it (default
`http://127.0.0.1:8000`), and `PRAX_TOKEN` is sent when the door asks
for one; `.mcp.json` passes both through from the shell that launches
Claude Code. A tool called while the door is down answers `{"error":
"the door is not reachable ..."}` instead of failing. The tools are
`search`, `get`, `get_chunk`, `context`, `documents`, `traverse`,
`link`, `ask`, `set_domains`, `promote`, `get_page`, `write_page`,
`append_page`, `ingest`, `capture_url` and `ingest_file` (a file on
the machine running Claude Code, uploaded to the door). What the agent
writes is stamped `agent`: edges' producer, pages' author, domain sets,
promotions.

For every other project, the plugin (`clients/claude-plugin/`,
`docs/claude-workflow.md`) registers the server at user scope. It adds
the skill that says when to use it, the commands `/prax:scope`,
`/prax:research`, `/prax:remember`, `/prax:sync` and `/prax:archive`,
and a session-end hook that syncs a project's docs and, when asked, its
sessions and memory files:

    export PRAX_PYTHON=/path/to/prax/.venv/bin/python
    claude plugin marketplace add lodsb/prax
    claude plugin install prax@prax

## 6. Deployment on the board (*the code is ready; the move is not made*)

The board holds the store and runs the door. The machine with the GPU
does the model work through it (howto 3l). Nothing else has to move.
`deploy/` holds the pieces: an install script, the systemd unit, the
board's `prax.yaml`. Its README is the step-by-step; this section is
the reasoning. The desktop's side is `run:` and `prax up` (4b).

**On the board.** Copy the data directory over (section 7), then:

    pip install "prax[serve]"        # the core plus vectors; no parsers, no models
    export PRAX_DATA_DIR=/srv/prax   # the SSD, never the SD card
    export PRAX_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    prax serve --host <its address on your private network> --port 8000

Run that as a systemd unit with those two variables in its
environment. The board runs one process, so systemd keeps `prax serve`
itself alive; `prax up` is for a host with more than one. In the
board's `prax.yaml`:

    vectors:
      dtype: i8        # half the file, recall 0.93: the memory a board has
    steps:             # nothing local answers here
      ask: {model: none}
      titles: {model: none}
      extract: {model: none}
    schedule:          # the door's own clock: maintenance nightly, a backup when there is a disk
      maintain: "03:30"

An index written as `f16` stays `f16`. The setting takes effect when
the vectors are written. Either re-embed into a fresh file (remove the
old `vectors-*.usearch` with the door stopped, start it with the
setting, and let a worker embed), or copy the desktop's file and accept
its precision.

**On the machine with the models**, pointing at the board:

    prax --door http://<board>:8000 --token <the token> status
    prax work --watch --door http://<board>:8000 --token <the token>

Or, kept running: `run: {llama-server: {model: …}, worker: {door:
http://<board>:8000}}` in the desktop's `prax.yaml`, then `prax up
--install` (4b).

**In Claude Code**, set `PRAX_DOOR=http://<board>:8000` and
`PRAX_TOKEN` in the shell that launches it. The MCP server is a proxy
and needs nothing else (section 5).

**The browser extension** points at the same address, and the board's
`door.cors_origins` lists the extension's origin.

What is still to do on the board itself: put the service file in
place, measure the door's memory there (invariant 7's gigabyte), and
decide whether the vector index is `i8` or a copy of the desktop's
`f16`.

## 7. Backup and moving the store

The store is three things: one SQLite file, a few index files, and one
directory of content-addressed files. `prax backup` copies them:

    prax backup D:/prax-backup       # a directory on the door's machine
    prax backup                      # paths.backup in prax.yaml [PRAX_BACKUP]
    prax backup I:/prax-db --no-archive   # the database, indexes and config only

The door does the copying (`POST /backup`, a job you can watch in `prax
jobs` and the Jobs view). The database goes through SQLite's online
backup: one consistent snapshot, the WAL folded in, writers not
blocked. Then come the `vectors-*.usearch` files and `prax.yaml`, then
every archive file the copy does not have yet. Archive files are
immutable and named by their hash, so the second run costs what the
day added, and an interrupted copy is resumed by the next one. A
`backup.json` manifest records what was copied and when.

The copy is a store. `PRAX_DATA_DIR` pointed at it opens it, on this
machine or another. The delta vector index is copied as it stands, so
a vector written between the snapshot and the file copy may be missing
there. `prax heal` finds those (`chunks-without-vectors`), and a worker
re-embeds them. Model files are not copied; `prax models fetch` gets
them again.

`--no-archive` copies what cannot be rebuilt (the database, the
indexes, the config, a few gigabytes) and leaves the originals out.
Use it for a disk too small for them. The manifest says so, and a full
run to the same directory later adds the archive. The originals of a
Zotero library are also in Zotero. Captured pages and uploads are not
anywhere else, so the full copy is the real backup once there is a
disk for it.

A nightly copy is `backup:` under `schedule:` in `prax.yaml`. The
door's own clock runs it as a job (4b). Litestream replication is on
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

Every run skips what the library already holds, by key or by URL.
`--refresh` re-reads what changed at the source. A new source of this
kind is a reader in `prax.importers` that yields `feed.Item`s, each a
text of its own with a key and a version, or a link, plus a line in
`clients/cli/prax_cli/importing.py`. `feed.run` does the rest.

A source that must open something on the door's host (the Zotero
importer, the backfill) is a client of `prax.store` instead:

1. Obtain the original bytes and whatever metadata the source has.
2. `register(...)`: archive the bytes, insert the row. Dedupe is
   automatic by hash.
3. If text is already available, call `index_text(...)`. Otherwise
   leave `parsed_at` NULL and let the parse queue pick it up.
4. Seed graph edges from metadata with `link(...)`,
   `confidence="EXTRACTED"` and `source_doc` set.
