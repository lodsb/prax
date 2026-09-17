# prax build plan

The staged checklist the project was built against, kept as the record
of what was done when; ticks carry dates and measurements. Work one
stage per Claude Code session. Each stage ends green: tests pass,
`ruff` clean, and the stage's checklist fully ticked before moving on.
Decisions behind the stages: `docs/rationale.md`. Source details:
`docs/sources.md`.

## Stage 0 — Prove the core

Goal: text in, search and graph out, reachable from Claude Code. No parsers,
no embeddings, no real sources yet; those come in Stage 1 and 2 once this
path is proven.

- [x] `prax.store`: schema init from numbered migrations, WAL on, one process-wide
      lock, connection usable from worker threads (FastAPI and FastMCP both
      run sync handlers off the main thread)
- [x] Two-step ingest: `register` archives the original bytes and inserts
      the document row (hash = sha256 of the original); `index_text` stores
      the parsed text as its own content-addressed artifact, chunks it
      (~1000-char windows, overlap 150) into `chunks` + FTS5, and stamps
      `parsed_at`. `ingest_text` composes both for plain text.
- [x] `get` reads the text artifact (never re-joins overlapping chunks) and
      accepts `offset` / `max_chars`
- [x] `search` (FTS5-only): the store builds the MATCH expression from the
      user string; punctuation and operators in a query never raise
- [x] `link`, `traverse` (recursive CTE, max 2 hops, both edge endpoints
      within the hop limit, valid edges only, result rows carry entity
      types and hop distance)
- [x] FastAPI: `POST /ingest` (text), `POST /ingest/file` (multipart),
      `GET /get/{id}`, `GET /search`, `POST /link`, `GET /traverse`
- [x] FastMCP over stdio: `search`, `get`, `traverse`, `link`, `ingest`,
      `ingest_file` (server-local path); no connection opened at import time
- [x] pytest, all on `tmp_path`: ingest → search, link → traverse, duplicate
      ingest is a no-op, register-without-text then index, plus regression
      tests for the four skeleton bugs (thread affinity, FTS syntax crash,
      traverse off-by-one, chunk reassembly), API through TestClient, MCP
      through the in-process client
- [x] `.mcp.json` points at the venv interpreter (relative path,
      `PRAX_PYTHON` override); all six tools verified over stdio using that
      exact command. Confirm once more from a fresh Claude Code session
      (it reads `.mcp.json` only at startup).

## Stage 1 — Real sources and parsing

Goal: the existing Zotero library and live browser tabs flow into prax.
Parsing is a batch job; the serving path never parses.

- [x] Schema migrations (`src/prax/migrations/`, `PRAGMA user_version`)
      and a loaded, validated, versioned ontology (`prax.ontology`), so the
      store can grow past papers without re-ingesting (rationale R12)
- [x] Zotero test fixture: the items listed in `docs/sources.md`, copied
      from `R:\Zotero` into `tests/fixtures/zotero/` with a reduced
      `zotero.sqlite`; 5.5 MB, built by `scripts/make_zotero_fixture.py`
- [x] Zotero importer, `src/prax/importers/zotero.py` behind
      `scripts/import_zotero.py`: works on a copy of
      `zotero.sqlite` opened read-only; `--dry-run` prints an inventory
      (items by type, attachments by link mode, missing files, duplicate
      hashes) before anything is written. Maps items → documents with
      Zotero key, item type, creators, date, DOI, URL, abstract, tags and
      collection paths in `meta`; archives attachments by hash; indexes
      text straight from `.zotero-ft-cache` where present (98% of PDFs),
      tagged `meta.text_source`; imports notes as text documents;
      `--limit N` for trial runs. Idempotent on re-run.
- [x] Scratch run on C: (`PRAX_DATA_DIR=C:\prax-data`, about 27 GB):
      fixture, then `--limit 500`, then the full library. Review the
      dry-run report before each. (Result recorded in `docs/sources.md`.)
- [x] Parse queue, `scripts/parse_pending.py` over `prax.parsers`: every
      document with `parsed_at IS NULL` (`--pending`), and every document
      whose `meta.text_source` matches a prefix (`--upgrade`), is parsed by
      MIME type through a pluggable extractor registry (pymupdf4llm then
      pymupdf for PDF with fallback, explicit-only pymupdf4llm-ocr and
      docling, trafilatura for HTML, plain for text) and handed to
      `index_text`. Runs on the desktop, not the serving host.
- [x] Extractor decision: Docling versus pymupdf4llm on a table-heavy
      sample (`scripts/compare_extractors.py`, `docs/eval/`); verdict in
      rationale R8: pymupdf4llm stays the default, no Docling upgrade pass
- [x] Structure-aware chunks (migration 0002, `prax.chunking`, R13):
      kind, locator, heading path, table data; `search` filters by kind,
      `get_chunk` returns one chunk; `scripts/rechunk.py`
- [x] Upgrade pass over the cache-derived PDFs and HTML snapshots with the
      default extractors (`--upgrade zotero-ft-cache`): every text artifact
      now carries an extractor stamp and Markdown structure; 163 documents
      kept their cache text because the new extraction was shorter
      (`docs/sources.md`)
- [x] OCR pass over the scanned PDFs the bulk pass left empty
      (`--extractor pymupdf4llm-ocr`): 283 documents gained text, the rest
      is artwork or unreadable (`docs/sources.md`)
- [x] The 71 scanned books (17 K pages): one deliberate run with
      `PRAX_OCR_MAX_PAGES=1000` (2026-09-12, about a second a page on the
      desktop's CPU). English and Latin came out clean; the Arabic books
      came out as letter salad because RapidOCR's default recognizer reads
      Chinese and English, so the recognizer is now a setting
      (`parse.ocr_language`, in the text-source stamp) and those books get
      a second run with `arabic`
- [x] Capture door (2026-09-12, `prax.inbox`): `POST /ingest/html` (URL,
      title, rendered DOM; trafilatura at once), `POST /ingest/url`
      (server-side fetch), `POST /ingest/file` with domains and tags, a
      capture-session id, canonical URLs with `meta.previous_capture`,
      `GET /inbox`, the Inbox view (drop zone, URL form, recent captures),
      the `capture_url` MCP tool, `PRAX_CORS_ORIGINS` for an extension
- [x] Jobs and the capture pipeline (2026-09-12): `jobs` table (migration
      0009) with `store.Job` around every batch pass, `GET /jobs` and the
      Jobs view; `prax.pipeline` with the passes as functions and
      `process_captures` (parse, titles, extract, embed) that the inbox
      watcher runs over new captures, never spending money; the door
      releases its index views on request so embedding can save; the UI
      polls `GET /changes` and re-renders listings in place
- [x] Retiring and duplicate captures (2026-09-12): `store.retire_document`
      (chunks, field and edges go; row, bytes and text stay), the same page
      sent again is one document by chunk fingerprint, a snapshot replaces
      a bare-DOM capture, `scripts/dedupe_captures.py` for what came before
- [x] Browser extension (2026-09-12, `clients/browser-extension/`, `docs/extension.md`):
      Manifest V3 for Firefox, Waterfox and Chrome from one folder; "send
      this tab" (rendered DOM to `/ingest/html`, PDFs as URL to
      `/ingest/url`) and "send all tabs in window" under one session id,
      optional closing; options for server, token, default domains;
      progress and results in the popup; `scripts/build_extension.py`
      packs an .xpi; node tests for the helpers. Pages are saved as
      self-contained snapshots through vendored SingleFile (AGPL, so
      `clients/browser-extension/` carries its own licence); a PDF tab is fetched with
      the browser's session and uploaded; HTML originals are served
      with a sandboxing header
- [x] Inbox folder (2026-09-12): `scripts/inbox.py [--watch] [--parse]`
      registers what lands in `data/inbox/` (subfolder = domain, JSON
      sidecar, settle time, `failed/`), consumed files removed
- [x] Door-side importers (2026-09-13, `prax import`): GitHub stars,
      chat exports (Telegram Desktop, Signal via sigtop, WhatsApp) by
      conversation and month, lists of links (browser bookmarks, Pocket
      and Raindrop CSV, text, Medium's export); a reader yields items,
      `feed.run` sends them through the door and skips what is held
      (`docs/sources.md` 6, with the candidates that would fit next)
- [x] Claude Code plugin (2026-09-13, `clients/claude-plugin/`,
      `docs/claude-workflow.md`): the MCP server at user scope, a skill
      for when to use the library, `/prax:scope`, `/prax:research`,
      `/prax:remember`, `/prax:sync`, a session-end hook; underneath,
      `prax import project` (docs keyed by path, versioned by content)
      and the `context` and `documents` tools; `/prax:archive` and
      `prax import claude` keep the sessions themselves (words, not
      tool calls) as the raw record beside the page's distillation.
      Open: whether a project module (decision, requirement, component)
      earns ontology types
- [x] Research v6 (2026-09-13, docs/ontology-v6.md): `written_at`,
      `mentions` and `about` widened, `authored_by` towards an
      organization, from the 18,848 items the v5 re-read left; a second
      batch of typing rules before it (placeholders, near misses,
      venues, cited titles: 3,091 linked, 3,390 dropped). Open: a model
      typing pass for the untyped remainder; the v6 re-read decision
- [x] Ontology v7 (2026-09-13, docs/ontology-v7.md): `authored_by` and
      `part_of` moved to core (part_of holds between organizations),
      `located_in` and `published_by` added, studio's `written_by` folded
      in (migration 0012). A first stable shape across the domains; the
      review queue is evidence again (~3.5k items) rather than a backlog
- [ ] Backfill of the old zoetrope disk: the hash inventory in
      `scripts/backfill.py` gains a `--commit` mode that registers files
      through the store; review the dedupe report first
- [x] Zotero-derived graph seeds: `authored_by` edges from creators, with
      `confidence = EXTRACTED` and `source_doc` set (part of the importer;
      one edge per paper title and author, notes excluded)

## Stage 2 — Hybrid retrieval

- [x] Embedding batch job: bge-small-en-v1.5 ONNX (384-dim) → `chunks_vec`
      (sqlite-vec 0.1.9, cosine, `kind` metadata column) through
      `prax.embeddings` and `scripts/embed_pending.py`; bookkeeping in
      `chunk_embeddings` (migration 0003); GPU via DirectML on the desktop,
      int8 on CPU. Full library: about 4.5 h at 53 chunks/s.
- [x] `search` is hybrid: FTS5 + vec in parallel, RRF fusion (k = 60),
      `mode=fts|vec` to force one side, degrades to FTS when vectors are
      absent; hits carry `fts_rank` and `vec_rank`
- [x] Eval harness: 20 hand-written queries against the Zotero fixture
      with expected docs (`tests/eval/queries.yaml`, `prax.evaluation`,
      `scripts/eval_retrieval.py`); first run in `docs/eval/`: hit@1 0.95
      fts, 0.90 vec and hybrid on the 10-document fixture
- [x] A larger eval set against the full store (expectations by title):
      62 queries, `tests/eval/queries-library.yaml`; FTS 0.82 MRR, vec
      0.81, hybrid 0.83 after document-level fusion (`docs/eval/`)
- [x] Vector index moved to a usearch HNSW file next to `prax.db`
      (`prax.vectors`, invariant 1 and R6 updated): 46 ms per query at
      recall 0.98 instead of sqlite-vec's 4 s; `chunk_embeddings` stays the
      bookkeeping; the batch job appends, saves and compacts
- [x] Optional cross-encoder rerank behind a flag (`prax.rerank`,
      `search(rerank=True)`, `PRAX_RERANK`): benchmarked MiniLM-L6 and
      bge-reranker-base at depths 10 and 30; none beats the fused list
      beyond noise and depth 30 hurts (`docs/eval/`). Off by default.
- [ ] Document-aware rerank input (title + heading path + chunk) as a
      follow-up experiment; the harness and flag are in place

## Stage 2b — Web UI

Goal: inspect and use the store without an agent. A client of the HTTP
door, nothing more: static files served by the same FastAPI process, a few
read endpoints for browsing, no framework and no build step (rationale
R14). Design in `docs/ui.md`.

- [x] Browsing endpoints on the door: `GET /documents` (paged, filtered
      by title, source, MIME), `GET /doc/{id}/original` (archived bytes
      with their MIME type, so a PDF opens in the browser at a page),
      `GET /doc/{id}/text` (the Markdown artifact), `GET /doc/{id}/chunks`
      (the document as its chunks with kind, heading, page, locator),
      `GET /entities?q=` (graph entry points)
- [x] Static UI mounted at `/ui/`: `src/prax/ui/` with one page, plain JS
      and CSS, a vendored Markdown renderer; hash routes `#search`,
      `#doc/<id>`, `#browse`, `#graph` (lookup and one-hop table for now)
- [x] Document view: metadata, the document rendered chunk by chunk with
      kind badges, heading path and page, the searched chunk highlighted
      and scrolled to, tables from their grids, an outline, "open
      original" at the chunk's page in a new tab
- [x] Search view: query, mode and kind, results with snippet, kind,
      heading, page and which side found them, each opening the document
      at that chunk
- [x] Browse view: recent and filtered document lists
- [x] Graph view: entity search, neighbourhood as a canvas force layout,
      expand by click, edges labelled with relation and confidence, source
      documents one click away
- [x] Review view: the queue paged, each item dropped, marked as an
      ontology gap, or linked as an edge after fixing types or relation
      (`GET /review`, `POST /review/{id}`, `GET /ontology`)
- [x] Bearer token on the door (`PRAX_TOKEN`, header or session cookie,
      loopback-only when unset; `prax.auth`) and a token prompt in the UI;
      Tailscale binding is a deployment step (`docs/howto.md`)

## Stage 3 — Graph enrichment

- [x] `ontology.yaml` v1: eight entity types, ten relations, descriptions
      written as extractor instructions (the prompt is generated from the
      file)
- [x] Extraction job (`prax.extraction`, `scripts/extract_graph.py`):
      structured output against the ontology's JSON schema, confidence and
      a quoted `evidence` per edge, `source_doc` + `ontology_version`
      stamped, misfits to `review_queue` (migration 0004), summary in
      `meta.summary`, incremental by `meta.extraction`; synchronous with a
      budget or via the Message Batches API. Dry-run estimate for the 8,448
      indexed documents: Opus 5 about $250 ($125 batch), Sonnet 5 $100
      ($50), Haiku 4.5 $50 ($25)
- [x] Trial run: 21 documents with Sonnet 5 (2026-09-09), 181 edges, 38
      review items, $0.71; Opus/Sonnet/Haiku compared on three papers
      (`docs/eval/local-llm-2026-09-08.md`); Sonnet 5 with a 20-triple cap
      chosen. Selection skips documents under 500 characters of text.
- [x] Full run over the never-extracted documents (2026-09-11/12): not the
      batch API but Qwen3.6-35B-A3B on an RTX 4090 through
      llama-server (`docs/eval/extractors-local-2026-09-11.md`), 6,938
      documents in 8.3 h, 57,808 edges, 20,110 review items; a Sonnet
      second pass on chosen papers remains an option
- [x] Citation network (2026-09-10): `prax.importers.citations`, OpenAlex
      or Crossref by DOI or exact title, `cites` edges with evidence,
      citation counts in `meta.citations`; Crossref pass over the 1,069
      DOI documents
- [x] Graph overview by default (hubs, the edges among them and
      co-occurrence links); double click opens a neighbourhood
- [x] Pages (2026-09-10, migration 0006, rationale R15): notes on
      documents, project threads with reading lists, topic pages; revisions
      with author; agent appends, never overwrites; MCP tools; ontology v3
      (page, project, annotates, part_of)
- [x] Document-level retrieval field (2026-09-10, migration 0005):
      title, kind, summary and image description fused into search as two
      more rank lists; `doctype` filter; the one schematic now answers
      "schematic"
- [x] Images as documents (2026-09-10): shown inline; `claude-vision`
      extractor describes and transcribes them (Sonnet 5 read the UREI
      1176LN schematic's revision table and handwritten notes; Haiku did
      not)
- [x] Document context column (2026-09-10): summary, entities, similar by
      vector centroid, shared entities, citations in and out, same
      authors, Zotero parent and siblings (`GET /doc/{id}/context`)
- [x] Extractor input widened: closing sections appended after the head
- [x] Review view: filters, bulk drop, replay against the ontology
      (`scripts/replay_review.py`)
- [x] Ontology v2 (2026-09-10, `docs/ontology-v2.md`): widened rules,
      `defines`/`contrasts`/`advised_by`; replay linked 377 queued
      triples, 170 typed items and 1,401 unmapped ones remain open
- [ ] Decide full re-run versus delta pass for the 1,021 documents
      extracted under v1 before the next batch
- [x] Code kept as code (2026-09-10): HTML `<pre>` blocks fenced, source
      attachments fenced by extension or Magika; extractor revisions in
      the stamp; 105 pages and 100 attachments re-parsed (48 and 11 code
      chunks where there was 1)
- [x] Local model option measured (`docs/eval/local-llm-2026-09-08.md`):
      llama-cpp-python CUDA wheel runs on the GTX 1070 (`prax.local_llm`,
      `local` extra, `scripts/bench_local_llm.py`); Qwen2.5-7B Q4 gives
      valid schema-constrained extractions at 80 s per document, a week
      of GPU time for the backlog against a $25-125 batch job, so the API
      stays the default for the bulk run
- [x] `LocalExtractor` (`PRAX_EXTRACT=local`, `PRAX_LOCAL_MODEL`): the
      same prompt and document input, answered as tab-separated lines
      under a bounded GBNF grammar (`prax.lineformat`, 20 triples, field
      lengths capped) so small models terminate; parsed into the same
      `Extraction`; runtime behind `prax.local_llm.LlamaRuntime`
- [x] Ask (2026-09-11, `prax.ask`, rationale R16): hybrid search to a
      bundle of one passage per document plus the graph's facts about
      them; answered by a local GGUF model (`PRAX_ASK=local`, Qwen2.5-7B
      in about 20 s on the 1070), Claude, or nobody (the bundle for an
      MCP client); `[n]` citations resolved to chunk and document ids;
      `POST /ask/save` appends an answer to a page with sources and
      `annotates` edges; Ask tab in the UI; MCP tool `ask`
- [x] Titles worth the name (2026-09-11, `prax.titles`,
      `scripts/repair_titles.py`, `store.retitle`): file names and
      Zotero's "No Title" names replaced by the local model's reading of
      the first page (printed title or a descriptive name), ALL CAPS
      recased by rule; old titles kept in `meta.title_history`, paper
      entities follow; hybrid search without vectors now fuses the
      field's BM25 too
- [x] Acronym expansion and the rare-terms list (2026-09-12, migration
      0008, `prax.acronyms`, `scripts/build_acronyms.py`): a query token the
      library defines as an acronym is expanded on the keyword side; the
      rare acronym-shaped terms get a rank list of their own; library MRR
      0.89 to 0.905 (`docs/eval/retrieval-acronyms-2026-09-12.md`).
      "adaa iir algorithms" now finds the ADAA papers. Graph panel and
      review items link an edge's evidence to its chunk (`?find=`)
- [x] Modular ontology (2026-09-12): `ontology/` with `core.yaml` (person,
      organization, document, place, event, work, concept, tool;
      affiliated_with, developed_by) and `research.yaml` (requires core;
      author, paper, venue, page, project as subtypes); composed version
      `core1+research5`; subtypes pass where the parent is allowed; type and
      relation aliases; `for_domains` narrows to a document's modules
- [x] Per-document domain set (2026-09-12): `meta.domains` (none = every
      module) from `domains:` rules in prax.yaml (`assign_domains.py`),
      by hand on the document page / API / `set_domains` MCP tool (never
      overwritten by rules); extraction builds prompt, grammar and schema
      for the document's subset, names the document `paper` or `document`
      accordingly and stamps the subset's version; `extract_graph.py
      --domain` re-runs one domain; `search(domain=)`
- [x] Promote queue (2026-09-12): `meta.promote` set from the document
      page, the Promote view's scored candidates, the `promote` MCP tool,
      or by the store when a document joins a project or a synthesis;
      `prax work --steps promote --spend` runs the `promote` step's model
      (Sonnet 5, 30 triples) over flagged documents that producer has
      not read; done-ness from the extraction history
- [x] Ontology v5 (2026-09-12, `docs/ontology-v5.md`): `organization` with
      `affiliated_with`, `funded_by`, `developed_by`; `mentions` as the weakest
      relation; wider `contrasts`, `extends`, `about`, `part_of`. Replay linked
      195 typed leftovers; rules for unmapped items (affiliation, supervision,
      authorship, funding, development, mentions) linked 1,261 more without a
      model; 232 organizations in the graph; 18,372 items stay open, most of
      them untyped `about`, `cites`, `part_of` and `published_in` the rules
      cannot type
- [x] Studio module (2026-09-12, `ontology/studio.yaml`,
      `docs/ontology-studio.md`): device, component, manufacturer,
      publication, manual, datasheet, schematic, article, feature,
      standard, spec; describes, covers, has_part, has_feature,
      conforms_to, has_spec, compatible_with, succeeds, appeared_in,
      written_by, names. Modules declare `self_types`; aliases never
      shadow a declared name across modules. Composed version
      `core1+research5+studio1`; 19 gear documents re-extracted under
      `core1+studio1` (191 edges, 58 more by the `self-as-device` rule;
      the earlier reading as papers retired: a producer's re-read under
      another subset supersedes its own earlier reading,
      `store.retire_reading` in `apply()`)
- [x] Typing rules over the review queue (2026-09-12,
      `prax.review.apply_typing_rules`, `scripts/type_review.py`): the
      local model's systematic misfits retyped, flipped, renamed or
      dropped as INFERRED edges with producer `typing-rules`; 2,898 linked
      (2,468 new edges), 769 dropped, 656 typed items left for a person or ontology v5
- [x] Ontology v4 (2026-09-11, `docs/ontology-v4.md`): page kind
      `synthesis`, relation `synthesizes`, pages may support, contradict
      and propose claims; Ask can start a synthesis page from an answer
- [x] `prax.yaml` (2026-09-11, `prax.models`): named models and the step
      each serves; kinds claude, gguf, openai (llama-server, vLLM, hosted
      APIs), stub; environment variables stay as per-run overrides; one
      loaded runtime per process; ready for a 4090 running
      llama-server for the extraction backlog
- [x] Entity resolution (`prax.resolution`, `scripts/resolve_entities.py`):
      sure merges (normalized names, author initials forms) apply on their
      own, likely merges (name embeddings, concept/method/tool/dataset/venue
      only) go to an adjudicator (none, stub, or Claude); merges recorded
      via `entities.canonical_id` with chain flattening; `traverse` walks
      canonical ids. 264 author and title variants merged in the scratch
      store.
- [x] Resolution run after the v3 re-run (2026-09-11): 1,528 sure merges
      (title case, accents, initials), 471 concept/method twins merged into
      their methods (`--twins`), likely tier with the Opus adjudicator:
      636 merged, 746 declined
- [x] Edge invalidation: `store.invalidate_edge` sets `valid_to` and can
      insert the successor edge (history kept); the contradiction pass
      that calls it comes with the second extraction round
- [x] `traverse` surfaces confidence, evidence, ontology version and
      validity on every edge (store, API and MCP)

## Next modules (planned, after the v5 backlog finishes)

Agreed 2026-09-12. Three small modules, written the way v5 and studio
were: small first, twenty documents read with the local model, the
review queue says what is missing.

- [x] `craft.yaml` v1 (2026-09-12, docs/ontology-craft.md): `technique`
      and `material`, with `applies`, `made_of` and `needs` (`needs`
      moved down from kitchen: a build needs a bandsaw as a recipe needs
      a mixer).
- [x] `kitchen.yaml` v1 (2026-09-12): `recipe` as the self type, `dish`
      (a work), `ingredient` (a material), `cuisine` (a concept);
      `makes`, `calls_for`, `variant_of`, `belongs_to`. Quantities stay
      in the text.
- [x] `workshop.yaml` v1 (2026-09-12): `build` as the self type and
      `design`, studio's `device` and `component` reused; `made_with`,
      `follows`, `derived_from`. A relation for repairs and mods was
      left out until the queue asks for it.
- [x] Domain rules and the drop subfolders for them (2026-09-12):
      `prax.example.yaml` shows `path: kitchen/`, `path: workshop/` and
      a tag rule; the extension's domain list already grows on its own
      from `GET /inbox`.
- [x] Grown from the library's own documents (2026-09-12): a sweep found
      two recipes and twelve builds, read with the local model; the queue
      grew studio to v3, workshop and kitchen to v2 (docs/ontology-craft.md
      "The first fourteen"). Twenty each would need documents the library
      does not hold yet: drop them into `data/inbox/kitchen/` and
      `…/workshop/`. Surfaced for later: "who wrote this" belongs in core
      (a core v2, with the next full re-read).

## The heal pass (done 2026-09-12)

Asked for with the three modules: a place for the damage that recurs, so
that "source name" and its kind are a named ailment rather than a
one-off clean-up.

- [x] `prax.store.repair`: nine ailments, each a `find` and (when it is
      safe) a `repair` that goes through the store's own functions —
      edges invalidated, never deleted; review items resolved; jobs
      closed. `GET /heal` looks, `POST /heal` repairs, `prax heal` is
      both with a dry run by default (howto 3m).
- [x] Found in the library on the first run: 42 placeholder entities
      (968 edges under "source name" as a paper alone), 291 names with
      markup a citation importer left in, 71 self-edges from merged
      aliases, 5 reference numbers as names, 5 whole citations as names.
- [x] The dry run earned its place immediately: the first
      `unnamed-entities` rule would have thrown away 27 real claims and
      94 real citations, and the repair for mangled names became "mend
      the name" instead of "end the edges".
- [ ] Ailments worth adding when they show up: entities that differ only
      by case or punctuation, edges whose evidence quote is no longer in
      the document, documents whose archive file is missing.

## Housekeeping pass (planned, after the v5 backlog finishes)

Measured 2026-09-12 against the live store (9,500 documents, 1.7 GB).
The rule for this pass: quality first; a dependency is dropped only when
what replaces it is at least as good, and where a library is the right
tool but a heavy install, vendoring its built files or extracting the
part in use (as done with SingleFile) beats losing the capability.

First, the process model (the cause of every failure on 2026-09-12:
three processes writing one SQLite file, which invariant 4 forbids):
- [x] The door as the only writer (2026-09-12): the work protocol
      (`prax.work`: `GET/POST /work/{step}` with leases, session jobs),
      the worker (`prax.worker`, `scripts/work.py`: parse, titles,
      extract, embed with this machine's models, local drop folders
      uploaded, never a paid model unasked, never the database), the
      door consuming its own drop folder, vectors into a delta index
      the door holds (merged into the main file on a schedule, so no
      other process replaces a mapped file), a connection per request
      thread for reads. Left as known deviations: the MCP server and the
      one-off maintenance scripts, which still open the file.

Memory on the batch host (found 2026-09-12 at 04:30, with the machine
within a gigabyte of its commit limit):
- [x] The watcher's resident footprint (2026-09-12): with the door as
      the only writer the writable index copies left the worker; it
      sits at about 850 MB of private memory between passes (the
      embedding runtime, loaded on first use), down from 4.8 GB.
- [x] Commit on Windows (2026-09-12): `--load-mode mmap` in the launcher;
      the page-file requirement and the side-by-side rule are in howto
      3l ("Jobs"); `GET /jobs` carries the host's free RAM and commit
      headroom (`prax.hostinfo`, ctypes on Windows, /proc on Linux) and
      the Jobs view shows them, red when under 4 GB or 10 %; the
      worker's heartbeat carries its own footprint.
- [x] Which passes run side by side: howto 3l ("Jobs").

Performance, cheap first:
- [x] Expression indexes (2026-09-12, migration 0010): `meta.source`
      (live documents), `meta.retired`, `meta.domains`,
      `meta.extraction.ontology_version`, `meta.promote.at`,
      `meta.zotero.parent`, and the review queue's open items by
      document. Measured on a copy of the live store: the capture
      listings, the retired and promote lists and a document's open
      review items went from 12-37 ms scans to under a millisecond; the
      extraction selection stays a scan (it returns most rows).
- [x] Embedding on the GPU (2026-09-12): the cause was two onnxruntime
      packages in one venv (the CPU one installed last shadowed the
      DirectML one). Measured on 1,024 real chunks: CPU int8 23
      chunks/s, CPU fp32 17, DirectML fp32 45, DirectML int8 80. The
      default variant is int8 on every provider now (faster on both,
      one variant for the whole store); the desktop venv keeps only
      onnxruntime-directml; howto 3d says never both.
- [x] Batch selections folded into SQL (2026-09-12):
      `select_for_extraction` compares each document against the
      version of its own domain subset with one CASE over the domain
      sets in use (it used to read every candidate's meta in Python:
      8,700 reads per worker pass), takes `sources` and
      `skip_mime_prefix` so `captures_ready` and the work hand-out are
      one query; `assign_domains` selects only the documents without a
      set. One intended change: a document with a domain set whose
      reading carries the whole ontology's version (read before its set
      was assigned) is due again under its subset; the old Python filter
      let those pass. 66 captures on the desktop, re-read by the worker.
- [x] `traverse` on hubs (2026-09-12, migration 0011): the old query
      joined a canon CTE (no index) into the recursion and scanned every
      live edge per frontier row; at two hops from the biggest hub it
      ran for over ten minutes on the live graph (114k entities, 125k
      live edges). The walk now runs over raw ids with the edge indexes
      and expands alias groups through an expression index on the
      canonical id: 10-25 ms at one hop, 30-190 ms at two hops on the
      four biggest hubs, same results.

Dependencies (188 packages, 3.5 GB in the venv; the serving path needs a
fraction):
- [x] The in-process llama.cpp binding and the `local` extra dropped
      (2026-09-12, `prax.local_llm`, the `gguf` kind, `PRAX_LOCAL_*`):
      an `openai` model at llama-server does the same from its own
      process; howto 3h is about llama-server now.
- [x] Docling (2026-09-12): kept as the documented explicit extractor
      in its own extra, uninstalled from the desktop venv with its
      stack (4.0 GB to 0.8 GB for the whole venv).
- [x] Magika measured (2026-09-12) and kept optional: it labels the
      language of 39 of 6,855 fenced code blocks (the blocks themselves
      come from the line scorer) and decides "code" for 2 of 13
      whole-file code attachments that have no extension. Small either
      way; it stays in the `ingest` extra and degrades to nothing when
      absent, as before.
- [x] The Hugging Face client replaced (2026-09-12) by `prax.fetch`:
      one resumable HTTPS GET per file into `<data dir>/models/`, the
      old cache reused; `scripts/fetch_model.py` fetches the embedder
      and the GGUFs `prax.yaml` names.
- [x] FastMCP replaced (2026-09-12): `prax.mcp_server` is a proxy of
      the door on the official `mcp` package (2.x, `MCPServer`), each
      tool one HTTP call through `prax.client` (shared with the
      worker); the process imports no store module, which a test
      checks in a subprocess. The door's request bodies carry who acts
      (`producer`, `by`, `author`: "agent" from the proxy). The
      second-writer deviation of the MCP server is over; only the
      one-off scripts remain.
- [x] A `serve` extra (2026-09-12): `prax[serve]` is the core plus
      `embed`, `prax[work]` is `embed` plus `ingest`; the core itself is
      fastapi, uvicorn, pydantic, python-multipart, pyyaml, httpx,
      anthropic and mcp (howto 1). The door on the desktop: 70 MB working
      set, 0.8 GB private with the embedder loaded and the index mapped
      (invariant 7's 1 GB holds; the board's own number is still to be
      taken when it runs there). Fresh venvs from the declared extras
      (2026-09-12, evening): `prax[serve]` is 54 packages and 228 MB,
      `prax[work,dev]` 92 packages and 648 MB, of which OpenCV (118 MB)
      is RapidOCR's price for reading scans. The desktop's own venv had
      accumulated 771 MB and 168 packages through the removals; rebuild
      it when nothing runs on it. Nothing further worth cutting without
      losing a capability: cryptography comes with mcp, hf_xet with
      tokenizers, babel with trafilatura.
- [x] Docs (2026-09-12): "reachable over your private network" instead
      of Tailscale as the assumption, Tailscale kept as one example
      (CLAUDE.md, howto 4 and 6, architecture, extension.md, sources.md,
      ui.md, rationale R11, the options page). The note as written: Nothing in prax needs it; any VPN or the LAN
      does, the door listens on that interface and checks a token, plain
      HTTP inside the private network is fine, TLS through a reverse
      proxy only if the door were ever exposed. Tailscale stays as one
      example (it is the least setup). About twenty mentions across
      CLAUDE.md, README, howto, architecture, extension.md and sources.md.
- [x] Word documents (2026-09-12): `docx` reads the zip of XML here
      (headings by outline level or style name, lists, tables as
      Markdown), `office` converts `.doc`, `.rtf` and `.odt` through
      LibreOffice when a machine has it and is simply not offered when
      it does not (`Extractor.check`). `prax.parsers.guess_mime` names
      the office types Python's table misses. The one `.doc` capture in
      the store parses now. 2026-09-13: `.odt` read here like `.docx`,
      `.rtf` through striprtf; LibreOffice only for the binary `.doc`,
      its output to a file rather than a pipe (the pipe outlived the
      conversion and hung the test)
- [x] Models fetched on demand (2026-09-12, `scripts/fetch_model.py`,
      `repo` and `file` on a models entry, the example config shows
      it). The original note: a `models` entry may name `repo` and
      `file` instead of `path`; `prax models fetch <name>` (or the first
      use of the step) downloads into `<data dir>/models/` and records
      the file's hash; the same for the embedding model, so a fresh
      install needs no manual download and the config stays
      declarative. The llama-server binary the same way (howto 3k
      already documents its download).
- [x] `prax.store` split into a package (2026-09-12): `base` (connection,
      lock, archive, index files), `documents` (ingest, read, meta,
      domains, promotion, retiring, the document field), `retrieval`
      (query expansion, FTS, vectors and their delta, fusion, rerank),
      `graph` (entities, edges, traversal, review, selection), `pages`,
      `jobs`, `summary` (`stats`). The `__init__` re-exports all 203
      names, so `store.<name>` is unchanged everywhere and no caller
      imports a submodule; inside, a module may import only from the
      ones before it in that order, which is checked by the split
      itself. 4,099 lines became seven files of 120 to 1,230.
- [x] The `prax` command (2026-09-12, `clients/cli/`): a client like
      the extension beside it, one HTTP call per command, no database.
      Everyday: search, ask, add (files, folders, URLs, a pipe), show,
      open, graph, pages. Running it: status (the README's state table
      from the live store, through the new `GET /stats`), jobs, inbox,
      work, serve, doctor, models (and `models fetch <name>`). Colour
      and separators go away when the console cannot take them; `--json`
      on every read command; `--door`/`PRAX_DOOR` points it at the
      board. Left as scripts: the one-off maintenance passes (import,
      backfill, resolution, typing rules, rechunk, replay, dedupe,
      domains assign), which open the database and are invariant 4's
      known deviation. `prax import`, `prax review type` and
      `prax domains assign` are worth adding once those move behind the
      door.
- [x] Settings moved into prax.yaml (2026-09-12): sections
      `embeddings`, `vectors`, `rerank`, `parse`, `citations`, `door`,
      `ontology`, `paths` beside `models`, `steps` and `domains`, read
      through `prax.config` (`setting`, `number`, `whole`, `words` by
      dotted path). The matching `PRAX_*` variable still wins for one
      run, so nothing that worked stopped working; an unknown section is
      an error rather than a silent typo. Environment-only: the data
      directory, the config path, the token, `PRAX_DOOR`, `PRAX_OFFLINE`,
      `PRAX_DEBUG` and `PRAX_<STEP>`.

## Deployment shape: the board holds the store, the desktop does the model work (planned)

Agreed 2026-09-12. The queue already exists implicitly: every document
carries its state (parsed or not, the ontology version it was read
under, vectors or not, a title guess tried or not), so "what needs a
model" is a selection over stamps at any moment, idempotent and
restartable. What is missing is doing that work from another machine,
since a batch pass opens the SQLite file directly today.

- [x] A work protocol on the door (2026-09-12, `prax.work`): `GET
      /work/{step}?limit&scope` hands out a leased batch, `POST
      /work/{step}` applies the results; the door stays the only writer,
      the index-release dance is gone (delta indexes), leases expire
      back into the pool.
- [x] The worker (2026-09-12, `prax.worker`, `scripts/work.py --door
      http://<board>:8000 --watch`): drains the queue whenever this
      machine is on, with its own prax.yaml and the never-spend rule;
      the door itself keeps what needs no model (the drop folder, HTML
      captures, dedupe).
- [~] On the board: everything the code needs is in place (2026-09-12) —
      the `serve` extra, `vectors: {dtype: i8}` in prax.yaml, the token,
      `prax serve --host <private address>`, the worker and the MCP proxy
      pointed at it with `--door`/`PRAX_DOOR`; and `deploy/` (later the
      same day) holds the install script, the systemd unit with a 1.5 GB
      cap, the board's `prax.yaml`, the env file for the address and the
      token, and `worker.ps1` for the desktop. What is left is the move
      itself, which needs the board: copy the store, run the install
      script, measure the door's memory there, decide `i8` or the
      desktop's `f16`.

## Reading the mathematics, and what prax runs (planned, 2026-09-16)

Two threads that met on 2026-09-15. The measurement first
(`docs/eval/marker-equations-2026-09-15.md`): the eight most
equation-heavy PDFs here refer to 347 numbered equations and hold nine
characters of maths between them, because a display equation in a
two-column paper is a vector drawing that `pymupdf4llm` drops and the
figure finder does not collect. marker reads them as LaTeX. Separately,
closing a terminal that evening took the door, the worker and
llama-server with it, which is a wrapper handing its children a shared
console rather than anything about Windows.

- [x] **The console fix** (2026-09-16). The cause was one layer up from
      `-NoNewWindow`: Task Scheduler starts `powershell.exe` with a
      visible console (`-WindowStyle Hidden` is parsed after the console
      exists), Windows 11 hands a visible console to Windows Terminal,
      so every logon opened a Windows Terminal window with three blank
      tabs, and closing it ended the three services with `0xC000013A`.
      The task's process is now `conhost.exe --headless` around
      PowerShell: no window, nothing to close (probed: the child of a
      headless host has no console window at all). Found on the way:
      Task Scheduler's restart-on-failure never restarted a task that
      exited non-zero (a probe with three restarts a minute apart was
      not run again), so "comes back after a crash" was never true on
      Windows — one more reason for `prax up`. The host also swallows
      the exit code; `-Status` reads it from `<name>.runs.log` instead.
- [x] **`prax up`** (2026-09-16, `prax.up`, `prax.autostart`,
      `prax.schedule`). prax owns its process model: `run:` in
      `prax.yaml` names which roles this host keeps alive, `prax up`
      starts them in order behind real health gates, restarts with a
      capped backoff, stops in reverse, rotates a log each; a pid, a
      status and a command file under `<data dir>/run/` are the whole
      interface, so `--stop` and `--restart` work the same everywhere.
      Children get no console on Windows (and a job object ends them
      with the supervisor) and their own session elsewhere. One login
      entry per platform (`--install`: a Task Scheduler task under
      `pythonw.exe`, a systemd user unit, a launchd agent). The
      llama-server command line comes from a `serve:` block on the
      model's own entry, the context per slot from its `n_ctx`. Against
      the plan as written: the nightly pass and the backup did *not*
      stay in the OS scheduler — `maintain` and `backup` are jobs on the
      door already, so they run on the door's own clock (`schedule:`)
      with the jobs table as memory, and the worker's backlog pass is
      its `nightly` hour; nothing outside prax schedules anything.
      `deploy/desktop.ps1`, `deploy/desktop.sh`, `deploy/worker.ps1`,
      `scripts/llama_server.ps1` and `.sh` are gone (their measurements
      moved to howto 3h and `deploy/README.md`). This machine runs under
      it since 19:28. Later, if wanted: a graceful stop for the door on
      Windows (`POST /shutdown` on loopback, since a process without a
      console cannot receive Ctrl-Break).
- [x] **marker as a named extractor** (2026-09-16). The GPU number:
      2 s a page through marker's own server (`marker_server`, models
      loaded) against 33 on the CPU; eight equation-heavy papers, 125
      pages, 556 display equations where the library held 18 `$`
      characters. Built as prax builds such things: marker's server is
      a role of `prax up` from a venv of its own (`run: marker: {venv,
      on_demand: true}` — it wants 5 GB of a card the 35B fills, so it
      is started for an evening with `--stop llama-server`, `--start
      marker`), and `marker` is an explicit extractor that is its
      client: page rules become the chunker's page markers, marker's
      image references are dropped and prax's figures placed by hash,
      stamped `marker/2.0.0` from the venv. Roles are ended as a tree
      now (a job object per role on Windows, the session elsewhere),
      which marker's stray llama-server needed. The eight papers were
      re-read through the door: 537 formula chunks, the figures kept.
      Not a dependency: marker-pdf is never installed into prax's venv.
      `docs/eval/marker-equations-2026-09-15.md` has the numbers.
- [x] **A display equation is a chunk** (2026-09-15, `formula` in
      `chunking.KINDS`): the LaTeX, the number the prose refers to it by
      and any readings in `data`, on the figure's pattern. What counts is
      structural — a relation, or an expression long enough to stand
      alone — which keeps 99 of 100 of marker's display equations and
      leaves the diagram fragment in the prose. Inline maths stays in its
      sentence, because a chunk is a region of the artifact.
- [x] **A reading for a formula** (2026-09-17, `prax.parsers.formulas`),
      as a figure has one: the `formulas` step names a text model, the
      `formulas` extractor (explicit, annotating) writes one to three
      sentences under each display equation — the equation and the prose
      around it are all the model sees — with `parse.formula_readings:
      again` to replace this model's earlier readings and keep another's,
      `--read-formulas`/`--unread-formulas` selections and the
      `unread-formulas` ailment. The eight marker papers' 537 equations
      were read by the 35B in ten minutes ("Shockley's diode equation…",
      "the Shockley diode model in the wave domain, relating the incident
      and reflected waves…"), and "Shockley diode equation" brings the
      formula itself. Found on the way: the read/unread selection query
      had an `OR` outside its `kind` clause, so `--unread-formulas`
      selected every document with an unread figure (3,210 requests,
      withdrawn); fixed and pinned with a test.
- [x] **Show the figure where it is cited** (2026-09-16). A search hit
      and an ask passage (the surf's too) carry `figure`, the reference
      of a figure chunk's image, and the UI shows the picture in the hit
      list and the sources column beside the reading that found it.
      Checked on the library: "bar chart comparing methods" brings
      eight figures in twenty hits, each with its picture; an answer
      about which figures compare methods shows them in its sources.

- [x] **Then look at the review queue again** (2026-09-17,
      `docs/eval/maths-ontology-2026-09-17.md`). Twenty-one equation-heavy
      papers read with marker (1,222 display equations, each with a
      reading), then extracted against the current ontology — which took
      the rule that a text replaced after its extraction is extracted
      again (`meta.extraction_stale`, the `stale-extractions` ailment for
      what came before; a re-read document goes first in the extract
      order). The answer: **no mathematics module.** The mathematics
      landed as concepts (ambiguity function, Port-Hamiltonian system,
      negentropy…), methods, claims ("the valid n-tone divisions … are 5,
      7, 12, 19, …") and `extends`/`contrasts`/`implements`/`defines`
      between them; the queue's 25 open items from the 21 are affiliations
      read as `located_in` and a method cited as if a paper — not one
      asks for an equation as a thing in the graph. The equations are
      best where they are, formula chunks with readings, which is what
      finds them. Two follow-ups the evidence does ask for: a typing rule
      that takes a document's `located_in organization` to `written_at`
      and drops `located_in place` (74 open items); and `solves` and
      `models` as candidate relations for research v8 when the queue
      shows them in numbers (one instance each tonight).

## After the mathematics (noted 2026-09-17, night)

What the marker evenings taught, kept for the day:

- [ ] **The follow-up edge.** prax's process graph is implicit and mostly
      there — parse → figure readings (when the text has image refs, once
      the vision model is free) → extract → embed, and since tonight a
      replacing re-read → extract first. One edge is missing: after a
      marker read that produced `formula` chunks, the door should request
      the `formulas` reading itself, as it requests `figures` after a
      parse; then `prax reread --extractor marker --ids …` is the whole
      evening and the rest follows. The same door-side follow-up pattern
      (`work._ask_reading`), a test, a line in howto 3h.
- [ ] **A typing rule for `located_in` from a document** (74 open items,
      `docs/eval/maths-ontology-2026-09-17.md`): a document's
      `located_in organization` is `written_at`; `located_in place` from a
      document is dropped (a paper is not in a city). `prax.review` rules,
      replayed by `prax maintain --only review`.
- [ ] **The mathematical part of the library through marker, in one
      evening.** By the density of numbered-equation references the
      candidates are few: 111 PDFs at ≥ 10 references per 10,000
      characters and ≥ 20 references (~1,300 pages, 40 minutes of the
      card), 281 at ≥ 6/10k (2 h), 606 at ≥ 3/10k (4½ h). One `--stop
      llama-server`, `--start marker`, a `prax reread --extractor marker`
      over a selection (a `--maths <density>` selector, or ids from the
      same count), and back — not weeks of nights. So the card-swapping
      policy below is not needed for this; it stays a note.
- [ ] **Resource-aware swapping in `prax up`** (only if marker re-reads
      become routine): the card holds the 35B or marker, not both, and
      nothing in the system knows it. The honest expression is a window
      on the clock we have — `run: marker: {…, window: "01:00-06:00",
      yields: llama-server}`: at the hour, if marker readings wait, pause
      llama-server and start marker; when the queue is empty or the
      window closes, swap back. Hysteresis matters (a swap is 3 minutes
      of reload each way). Not a DAG scheduler: five steps and one
      exclusive resource do not want Airflow, and the process model was
      just made smaller.
- [ ] **The ask evaluation with equations** — the honest test the marker
      note left open: the same equation questions asked before and after
      a document is read with its mathematics (does `ask` cite the
      formula, does the answer quote it right). The material exists now:
      21 papers, 1,222 formula chunks with readings. Belongs with
      `docs/ask.md` and `docs/eval/`.
- [ ] **A procedural graph for the surfer** (later; the reference is doc
      9985, "Procedural Graphs: Self-Evolving Execution Structures for LLM
      Agents", arXiv 2609.09153): procedural knowledge as (procedure,
      relation, procedure) triples that bias an agent's next move, refined
      from failed against successful trajectories. prax's surf has the
      moves and keeps every trail with its answer, which is exactly the
      material such a graph learns from — once there are enough failed
      trails to learn from. Not the batch passes: those have a fixed graph.

- [ ] **`prax resolve`'s likely tier out of the door.** The plan's third
      tier embeds the names of ~130,000 entities inside the door process
      (onnxruntime through the embedder) and on 2026-09-17 that crashed the
      door with an access violation after ten minutes — `prax up` had it
      back in a second, its first real crash, but invariant 7 says the
      serving process does no such work. The sure and twins tiers are
      cheap and stay; the likely tier belongs on the worker through the
      work protocol (hand out names, take in pairs), or behind a bound.
      Until then: `prax resolve --apply --twins` after an extraction pass
      folds the twins (Lambert W function is five entity rows tonight,
      concept and method, hyphen and case) without touching the likely
      tier.

## Later / maybe

- Streamable-HTTP MCP transport for remote access over Tailscale, and the
  MCP server proxying the HTTP door instead of importing the store
- Litestream replication of `data/prax.db`
- Kùzu migration script (only if the entity threshold is crossed)
- Complement / "blast-radius" SQL tools exposed via MCP
- Karakeep or Linkwarden as an additional capture front-end feeding the inbox
