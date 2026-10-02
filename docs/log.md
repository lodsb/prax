# prax: what was done, and why

The record the build plan grew around, moved here on 2026-09-25 so that
`docs/PLAN.md` could go back to being a plan. Chronological. Ticks carry
their dates and measurements, and the reasoning for a night's work is
under that night.

**This file is a record, not a list.** Its boxes are as they stood on
the day. What is still open lives in `docs/PLAN.md`, which links back to
the section that explains each one. Decisions behind the stages are
`docs/rationale.md`; measurements are `docs/eval/`.

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
      with a sandboxing header. Test bed (2026-09-19,
      `scripts/extension_bed.mjs`): headless Chrome (DevTools,
      `Extensions.loadUnpacked`) and Firefox (geckodriver, the add-on
      installed temporarily) against a throwaway door, every surface
      of the extension exercised, `--browser both`
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

## Next modules (done 2026-09-13 — craft, kitchen, workshop)

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

## Housekeeping pass (done 2026-09-12–16)

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

## Reading the mathematics, and what prax runs (done 2026-09-17)

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

- [x] **The follow-up edge.** (2026-09-17) prax's process graph is implicit and mostly
      there — parse → figure readings (when the text has image refs, once
      the vision model is free) → extract → embed, and since tonight a
      replacing re-read → extract first. One edge is missing: after a
      marker read that produced `formula` chunks, the door should request
      the `formulas` reading itself, as it requests `figures` after a
      parse; then `prax reread --extractor marker --ids …` is the whole
      evening and the rest follows. The same door-side follow-up pattern
      (`work._ask_reading`), a test, a line in howto 3h. *Done as
      `work._follow_up`; and the edge's first night found a hole in the
      work protocol: the follow-ups (low ids, waiting for a paused
      llama-server) filled every hand-out of ten with "not yet" and
      starved the 228 marker requests behind them — twice over: the
      hand-out also took its requests from the status view's newest-fifty
      window, so the older ones behind it were never seen at all. A "not
      yet" now defers the item (leased ten minutes, `work.DEFER_SECONDS`)
      and the hand-out reads the whole queue oldest first.*
- [x] **A typing rule for `located_in` from a document** (2026-09-17; 74 open items,
      `docs/eval/maths-ontology-2026-09-17.md`): a document's
      `located_in organization` is `written_at`; `located_in place` from a
      document is dropped (a paper is not in a city). `prax.review` rules,
      replayed by `prax maintain --only review`.
- [x] **The mathematical part of the library through marker, in one
      evening.** (`--maths` 2026-09-17; the evening itself ran twice, see the edge above.) By the density of numbered-equation references the
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
- [x] **The ask evaluation with equations** (2026-09-17,
      `docs/eval/ask-equations-2026-09-17.md`: the paper found 22 of 22,
      the formula chunk cited 20–21 of 22, the equation itself quoted
      with steps; the scores saturate, a judge is the next step) — the honest test the marker
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

- [x] **`prax resolve`'s likely tier out of the door.** (2026-09-17 evening:
      the `resolve` work step — a type's names out, the close pairs in,
      kept in `entity_candidates`, a type a week; the door embeds nothing.)
      The plan's third
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

## The night of 2026-09-20: what the slow log said, and the niggles

- [x] **The keyword side without stopwords, a page cache worth the
      name** (0fb2d3d). `/search` 12–24 s, `fts 22.9 s` cold: "a", "in",
      "and" each matched two thirds of a million chunks and the OR
      expression scored them all on SQLite's 2 MB default cache.
      `STOPWORDS` (English and German) out of the match expressions and
      the rare-term probe, never out of the embedder's query;
      `door.sqlite_cache_mb` (64) and `door.sqlite_mmap_mb` (1024) on
      every connection. 0.08 s cold, 0.03 s warm for the same query.
- [x] **The health check never opens a file** (febbf3d): `thin-texts`
      opened 9,347 PDFs to count pages (/heal 200 s); `uncounted-pages`
      names the PDFs without `meta.pages` and its repair counts them once.
      *To run once on the live store:* `prax heal --check uncounted-pages
      --apply` (a few minutes; needs pymupdf on the door — the desktop
      has it).
- [x] **An ad in the player** (ee44c4b): skipped or waited out, never a
      missed moment; the bed's watch page plays one.
- [x] **The figure strip and the domain boxes** (66365d6): "N figures…"
      / `?figures=1`; domains as boxes to tick, a change by hand or by the
      agent under an extraction marks it stale (first in the extract
      queue, `reread: true`).
- [x] **A `references` pass** (7389bf4, e9db156; `prax.references`,
      `scripts/eval_references.py`, `docs/eval/references-2026-09-20.md`):
      the bibliography chunks split into entries by rules, matched
      against the library's document field with a score, `cites` edges
      with the confidence by score; the eval against Crossref's links
      drove the rules (recall 0.75 of Crossref's, 6,551 sure + 2,735
      ambiguous links over the library, 5,629 from documents Crossref
      never resolved). *Run on the live store* the same night, and every
      night since: the pass is in `maintain.PASSES`, so the nightly job
      took it as soon as it existed. Measured 2026-09-24: 9,463 `cites`
      edges from 1,332 citing documents (8,037 INFERRED by title, 1,289
      AMBIGUOUS between twins, 137 EXTRACTED from a printed id), 3,436
      documents stamped `meta.references`, the score in every edge's
      evidence. A later run costs six seconds, because only a document
      whose text changed is read again.
- [x] **Standing questions and the briefing** (6d58058, 2026-09-21;
      the user's note in `niggles.txt`: a "self-aware" wiki — an answer
      page that updates itself as information arrives; also atomic's
      scheduled briefing): `prax.questions`, a `question` page asked
      again when the library learned something (a cheap check, no
      model: the search's top, shared entities, a re-read source), a
      person's sections kept, the `briefing` page daily; `schedule:
      questions: "06:30"` on the live host. Later: the contradiction
      scan (a new source that `argues` against the answer's claims),
      a paragraph on top of the briefing from the model.
- [x] **A tray icon** (195d058, cb5305e, 2026-09-21): `prax up --tray`
      and `prax tray`, the login entry carrying `--tray` on a desktop;
      the mark with a red dot when a role is down, the menu to open
      prax, restart or stop a role, stop everything, the logs.
- [ ] **The embed hand-out after a rechunk** (seen 2026-09-21: `GET
      /work/embed` 15–63 s a cycle while the 11,600 re-chunked text
      chunks were embedded): `pending_embeddings` walks the chunks from
      the highest id down and the rechunk had put 148,000 reference
      chunks — never embedded, filtered out one by one — above the
      pending ones, so every hand-out read past them again. Steady state
      is 0.1 s (the two counts). Worth a pending-set that does not scan
      (the ids a rechunk or an index inserts, drained as they are
      embedded) before the next library-wide rechunk.
- [x] **Ask blocks in a person's page** (2026-09-21; decided in
      rationale R17 after the survey in `research.md` "Living answers
      and mixed pages"): `<!-- prax:ask id=q1 "question" -->` … `<!--
      /prax:ask id=q1 sha= asked= run= -->` inside any page
      (`prax.blocks`: the grammar, `fill` by id, the tail's hash of the
      door's own text), the door filling the interior with the answer
      and its sources and keeping it as a standing question (the same
      check as a question page, per block in `meta.asks`;
      `questions.refresh_blocks`, one agent revision per page through
      `store.fill_blocks`, which touches nothing outside the markers and
      refuses a page whose markers are gone); a hand-edited interior is
      held — left, noted, shown — until "answer anew" / `--release`;
      `<!-- prax:keep -->` regions are the person's, neither hold the
      block nor go with the next answer; the block is one `ask` chunk
      set aside from search like `reference` (`ASIDE_KINDS`); a page
      saved with an unanswered block starts the pass for it at once;
      `[title](#doc/N)` links in any page are `annotates` edges made when
      the link appears and retired when it goes (an edge given as
      `annotates` is never retired by an edit); the UI frames a block on
      a plate with its state, the editor writes the markers ("+ standing
      question"), the Pages view and `prax questions` list blocks as
      `slug#id`, the briefing names the blocks that moved. Not done: the
      check's dated status is written to `meta.asks[id].checked_at` on
      every pass (no revision), as planned; "evidence newest last" in the
      bundle is not ordered yet (the passages come ranked). Deferred as
      before: `mode=propose` with a diff, claim-level KEEP/STALE
      adjudication, the contradiction scan over `argues`.
- [ ] **Sub-graph export / import**, the answer to "what if a repo's
      `docs/` were a piece of the library instead". A sub-graph is what
      is reachable from a seed (a project page's members, a domain, a
      tag, an entity and its hops): the entities and live edges with
      every provenance column, the pages as Markdown with their
      revisions, and for documents only the identity (hash, title, ids,
      meta) unless the originals are asked for. One file, JSON lines,
      deterministic order so it diffs in git, plus the ontology modules
      it was written against: `.prax/graph.jsonl` beside
      `.prax-project`, refreshed by the session-end sync the skill
      already does for notes.
      Import is where the invariants earn their keep. It is a producer
      (`import:<repo>@<commit>`) with a run per file, so it never
      overwrites: edges go through `store.link` with their original
      provenance kept in evidence, an entity name that exists is merged
      by the same resolution the extractor's output goes through, and
      edges the export marks `valid_to` are ended here too.
      Re-importing a newer export is `retire_run(old)` plus `link(new)`,
      so the graph converges on the file; conflicts (one triple with two
      confidences, a page changed on both sides) go to the review queue
      rather than silently one way. A page changed on both sides becomes
      two revisions with a note, never a merge by machine. Not exported:
      chunks, vectors, the review queue. `prax export --project X` and
      `prax import graph FILE`; a day with the tests, and an export
      written against research7 imported into research8 goes through the
      replay the review queue already uses.
- [x] **`vectors.serve: memory`** (20deffa, b4d8d5e, 6f64c37): a heal
      over ten thousand PDFs evicted the mapped index and the next search
      paid 4–6 s of page faults; the desktop loads it (2.7 GB resident for
      1.4 M vectors), opened at startup and preloaded at a merge so no
      search waits on the load (the first search after a restart once
      waited 100 s while llama-server read its model from the same disk).
- [x] **The morning after (2026-09-20):** the eight books are marker-read
      with 2,983 scanned-page pictures filed and read by the vision
      model (08:00–13:30; the videos' frames too), the two displaced
      formula readings after (a figures request displaces a waiting
      formulas one: a document holds one request at a time — worth a
      queue some day). Found on the way: a filed picture was searched
      for in the original before the door was asked (066dcc1: 23 s a
      picture on Hindemith), and the first searches after a restart read
      the keyword index from disk (0bf3eb1: read through at startup, the
      `fts` merge pass, lone characters no keywords). Door and worker run
      0bf3eb1 since 13:45. The extension needs a reload in the browsers
      for the ad fix.
- [ ] **taco and tacos are different searches** (`niggles.txt`). The
      cause: `chunks_fts` is FTS5 with no `tokenize=` at all, so it uses
      `unicode61`, which folds case and accents and stems nothing.
      "taco" reaches the recipe called "Tacos …" only through the vector
      side, which is why the two queries look unrelated. Porter is the
      one-line fix and English-only, so it belongs with the multilingual
      work rather than before it (German wants Snowball's `german2`, and
      the FTS index is rebuilt either way). Until then the query
      expansion is where a stem could be added, beside the acronyms.
- [ ] **A document's own neighbourhood** (`niggles.txt`): a link from a
      document page into the graph view, seeded with its entities —
      "what this document is connected to" as a view rather than a list
      of chips.
- [ ] **One workflow for maintenance and healing** (`niggles.txt`):
      `prax maintain`, `prax heal`, `prax resolve`, `prax backup` and
      the rechunk are five commands with five shapes; the UI cannot
      start any of them, and says nothing while one runs. Wanted: the
      passes as one list in the Jobs view with what each would do, a
      button where it is safe (the read-only checks and the idempotent
      passes), and a banner while the door is busy with one —
      "maintenance: rechunk 3,400 of 9,962". The door already has the
      job records for the banner.
- [ ] **If the UI still feels slow from the MacBook**: the next suspect
      is `GET /doc/<id>/chunks` for a book (5 MB) on the single uvicorn
      worker while a search waits; page it. Read `logs/door.log`'s slow
      lines first.

## The night of 2026-09-23: queues that explain themselves, what a capture is not, and the languages

- [x] **Who would do this work** (4573df1). A flagged document sat at
      "pending" with nothing broken and nothing said: no worker asks for
      the `promote` step unless the run names it (`--steps` defaults to
      the free passes), so the flag waited for a pass this host never
      runs, and re-promoting could not help. `work.who_runs` answers it
      for any step — the model, whether it is paid, whether a run names
      it by default, when a worker last asked this door, why nothing is
      doing it, and the command that would. `GET /promote` carries it as
      `waiting`; the Promote view and a requested route in the process
      dialog print it. The steps themselves are named once now
      (`prax.steps`) instead of three times.
- [x] **The typing rules round** (e8ec76f). 2,655 open items, 628
      resolved in two passes, about 2,000 left. The unlock: the
      self-name rule gave the document the type the graph had for it,
      which is not always the type the relation wants — `calls_for`
      takes a recipe, and a captured recipe is a `document`. It asks the
      relation now, prefers `paper` where several kinds fit, and leaves
      a page of my own alone. With it: a manual "part of" the thing it
      documents is `about` it, the firm behind a manual `published_by`
      it, a manual that "covers" a device `describes` it, `authored_by`
      flips for a `person` as well as an `author`.
      `docs/ontology-v8.md` records the round.
- [x] **What a capture carries that is not the document** (cebc9e4,
      `prax.furniture`, `prax.ingredients`). The comment section and the
      advertising are their own chunks (`comment`, `ad`), set aside like
      a reference and folded to one line in the document view; a
      recipe's ingredient list is one `ingredients` chunk with the
      servings and every line's amount, unit and note in `data`, and is
      not set aside. Measured over the whole store: 14 ad chunks in 7
      captures, 7 in 9,633 PDFs (two magazine adverts, a voucher, an
      offer in a manual), 67 comment sections. One document can be
      chunked again from its own page (`POST /doc/{id}/rechunk`), which
      is how a chunker change is tried now.
- [x] **The multilingual question, measured and researched** (96f2da1,
      `docs/research-multilingual-2026-09-23.md`).

### What the multilingual study found, and the order it proposes

The library is 72 % English and 20.8 % German, no document records
which, and the embedder is `bge-small-en-v1.5` — English only. On the
live door: "noise reduction in audio signals" finds the four right
papers, "Rauschunterdrückung in Audiosignalen" finds an ethnomusicology
journal and packaging tips. In the graph one thing arrives four times
(`Olivenöl`, `olivenöl`, `olive oil` as ingredient, `olive oil` as
concept). The study's order, each step to be measured against the
cross-language eval before the next:

- [x] **1. Record the language** (2026-09-24). `meta.lang`, written by
      `prax.language` when the text is indexed and filled in for older
      documents by the `languages` pass of `prax maintain`: 9,249 of
      10,243 parsed documents carry it (7,196 en, 1,988 de, 39 fr, 12
      es, 10 it). The detector is `py3langid` narrowed to the languages
      the host expects (`parse.languages`), not `lingua-py` or
      `fastText` — smaller, and it says nothing rather than guess, so a
      missing `lang` is always allowed. Per-chunk language was not
      needed once the document carried one.
- [ ] **2. A multilingual embedder of the same 384 dimensions.**
      `multilingual-e5-small` is already in `prax.embeddings.MODELS`, so
      it is `embeddings.model` in `prax.yaml` plus a re-embed into a new
      `vectors-<model>.usearch`; the old file keeps serving until the
      new one is complete, which makes it reversible.
      `granite-embedding-97m-multilingual-r2` is nine points better on
      multilingual retrieval (60.3 against 50.9) at 97 M parameters and
      wants a `ModelSpec` — verify its ONNX export first. Not `bge-m3`
      or `jina-v3`: four times the parameters, 1024 dimensions, a new
      `VEC_DIM`. *The cost is the re-embed: 1,115,976 chunk vectors at
      three to four times bge-small's compute. Measure it on the desktop
      before starting; the user's call.*
- [x] **3. The query in both languages** (2026-09-24), by a different
      route. Not the model translating the query, but the graph's own
      labels: an entity carries its name in every language it was met
      in, so a German query term finds the entity and the English
      documents about it. Measured in
      `docs/eval/query-dictionary-2026-09-24.md` — 32 of 80 German
      queries reach documents they could not reach before, 64% of them
      English. Cheaper than a translation per query and it improves as
      the library grows, because the labels are a by-product of
      extraction rather than a dictionary someone maintains.
- [x] **4. The resolution pass, cross-lingual** (2026-09-24), and it
      did not need the embedder after all. The `vocabulary` pass asks
      the ontology which types may be folded across languages
      (`naming: common`), asks the library whether a name occurs in any
      English document, and asks the local model for the English name of
      what is left: 1,435 entities decided, 556 renamed, 309 merged, 509
      already the word English uses, 89 type clashes queued rather than
      guessed. The alias does carry a language — `entity_labels` holds
      one preferred name per language and any number of alternatives
      (migration 22), so the canonical name is chosen per language
      rather than by whichever spelling arrived first.
- [~] **5. A dictionary only where the domain is closed** —
      superseded, 2026-09-24. The library is its own dictionary: a name
      that occurs in an English document is already the word English
      uses, which is an FTS lookup rather than a list to maintain, and
      it caught `psycho-acoustique` with no French rule anywhere in it.
      Rationale R19, "ask the corpus, not the author". A Wikidata import
      would still be a fair fallback for the names the corpus is silent
      about; nothing needs it yet.
- [x] **The eval for it** (2026-09-24):
      `docs/eval/retrieval-multilingual-2026-09-24.md` and
      `docs/eval/query-dictionary-2026-09-24.md`. What is left of this
      study is step 2 alone, and it is in `docs/PLAN.md`.

### Waiting on a decision

- [ ] **`prax maintain --rechunk`** — the `ad`, `comment` and
      `ingredients` regions only arrive with a re-chunk (10,261
      documents; a chunk whose text did not change keeps its id and its
      vector). An overnight job; the user's call.
- [ ] **The seven pending promotions** — `prax work --steps promote
      --spend -n 7` (roughly $1–3 on Sonnet 5), or the standing shape
      now that a budget can bound it: `steps` and `spend: true` on the
      worker role with `budget: {daily_usd: N}`.
- [ ] **`twin-documents`** (6) — the repair retires documents, so it
      waits for the user's word.
- [ ] **The unread figures** — 55,607 left of 93,158 as the vision pass
      runs; the captioned ones first, sliced so a request does not
      pre-empt the parse queue for days.
- [ ] **Ontology v9, the question the rules round left open**: the
      relations a document takes name `paper` where they could name
      `document`, so a captured page has to be read as a paper for an
      edge to fit. A version bump and a restamp, not a rules change.

## 2026-09-24: the four that were waiting, and how normalization should work

- [x] **The twin documents** (6): folded into their keepers by
      `prax heal --apply --check twin-documents`. Two were `[blank
      page]` scans; the rest a PDF downloaded twice.
- [x] **The seven promotions**: `prax work --steps promote --spend -n 7
      --scope all`. 94 edges linked, 94 of the local model's retired
      (history kept), 11 already there, 9 to the review queue, 0
      rejected. **$0.25** on claude-sonnet-5, all seven calls in the
      ledger — the first paid work the budget and the spend table have
      seen.
- [x] **The language of every document** (2a4e41c): `meta.lang`,
      written at index time and backfilled by the new `languages` pass
      of `prax maintain` (9,957 documents in 68 s). The library says it
      now: 70.9 % English, 20.9 % German, 7.6 % too short to tell, then
      French, Spanish, Italian, Dutch. Step 1 of the multilingual
      study, and the precondition for the rest.
- [x] **The subtype fold** (fcbbf5d, `docs/normalization.md`): the
      resolver grouped by name and type and never asked the ontology,
      which already says an author is a person and a paper a document.
      3,219 folds applied (2,049 person→author, 739 document→paper, 255
      organization→venue, and a tail), plus the 439 sure merges that had
      arrived since the last pass. The type clashes fell from 6,018
      names to 3,723, and what is left is polysemy rather than
      duplication: a paper named after the method it proposes is two
      things, not one.
- [ ] **The figures backlog, and why it was not moving.** 3,692
      documents were waiting for a figure reading with llama-server up
      and a worker running. The worker's `--scope` is `captures` by
      default, so it never takes work for a Zotero PDF; only the
      nightly pass does, 100 documents a night. One bounded pass
      (`prax work --steps parse --scope all -n 40`) drains it at the
      GPU's pace. *To decide:* whether the worker role should carry
      `scope: all` in `prax.yaml`, and whether the door should say this
      the way it now says why a promotion is pending — the demand
      endpoint knows the readings are waiting, and nothing joins that
      to the worker's scope. The same product gap, one queue over.

### What `docs/normalization.md` says, in short

Five kinds of duplicate, five mechanisms, and keeping them apart is the
design. Surface (a deterministic key, done), subtype (the ontology's
hierarchy, done today), morphological (a stemmer, half done — and the
search side has none at all, which is the taco/tacos niggle), language
(a shared vector space, or a dictionary; open), and polysemy, which is
not a merge and wants an edge instead. The pipeline is key, block,
score, decide, record. What is missing structurally: a merge carries no
producer and no run, so a bad pass cannot be retired the way a bad
extraction is, and an alias carries no language, which is what the
`entity_labels` table in that note would fix.

## 2026-09-24, the four steps: the ledger, the queue, the embedder, the labels

- [x] **A reading's tokens reach the ledger** (aea99d8). The morning's
      hole had two halves. The first was that a paid reading ran unasked
      (afae553: the guard keyed on the extractor's name, so `figures`,
      `vision-pages`, `formulas` and `polish` walked past it; it asks
      the step now, and a worker prints where its models come from). The
      second was that when a paid reading does run, nothing recorded it:
      `prax.usage` is a thread-local the client records into, the worker
      posts with its result, and the door turns into a `spend` row under
      the step that ran and the model the worker says it used — not the
      model this host would have chosen, because a worker pointed
      elsewhere uses another.
- [x] **A reading with nothing to do is not handed out** (763b4f7).
      First, a correction: reading requests are handed out whatever the
      worker's scope, so yesterday's diagnosis of the figures backlog
      was wrong — it was never blocked, it was moving at 250 an hour.
      What it was doing was half wasted: 1,818 of 3,477 requests were
      for documents whose every figure the vision model had already
      read, each costing a fetch, a parse and a round trip to come back
      "same". The door checks before handing one out and cancels it
      instead. And `GET /work/demand` now carries the rate a queue is
      moving at and the hours that leaves, because a count alone cannot
      tell a long queue from a stopped one — which is exactly how I
      misread it.
- [x] **A merge says who made it, and can be taken back** (643e5f6,
      migration 20). `entity_labels` holds the names an entity is known
      by, each with its language and the producer, run and entity it
      came from. `unmerge_run` undoes a round of merging whole, the
      counterpart of `retire_run` for edges; `entities_by_label` takes a
      German name to the English entity it belongs to. The `pref` kind
      waits for labels in languages worth choosing between.
- [ ] **The embedder** — the step that was not taken, and why. Two
      prerequisites came out of trying: the ONNX fetch asked for the
      built-in default rather than this host's model, and `embed()`
      applied no passage instruction, which multilingual-e5 is
      measurably worse without. Both fixed. On terms the multilingual
      model is plainly better (a translation pair is its nearest
      neighbour 10 times in 20, against 3 for bge-small), and a chunk
      experiment I ran over the live store was not worth trusting: the
      haystack came out all English and bge "won" rows it cannot win.
      So the decision waits on the eval instead of a hunch. The set now
      carries the same twenty questions in German against the same
      expected documents (`tests/eval/queries.yaml`, `evaluate(lang=)`),
      which runs on the fixture and needs no re-embedding of the
      library to answer. *Next:* run it for both models, then decide —
      and only then pay for 1.1 M vectors.

## The night of 2026-09-24: the figures nothing could extract, and a real queue

- [x] **"55,607 unread figures" was wrong** (52b681c). A figure chunk is
      a caption plus, usually, a reference to the picture it claims. Of
      the store's 108,781, only 53,174 held a picture; the other 55,607
      were captions of figures no extractor could pull out, counted as a
      backlog, handed to the vision model and returned as "same" —
      1,478 of one hour's 2,847 readings. The ailment and the hand-out
      ask for a `ref` now, and the real backlog was 25,837.
- [x] **figure-crops** (cd66c10, 4b042a4): the cause was that
      pymupdf4llm places a reference for an embedded *image object*, and
      a plot drawn with vector paths is not one (a page of the first
      paper opened: 65 drawings, no images). So render the region
      instead — find the caption on its page, gather the drawings and
      images above it that overlap its column, stop at a gap of white
      space, render at 150 dpi and inline it for the door to file.
      Everything in the rectangle comes out, which is why a crop beats
      an extraction for a plot with raster panels and vector axes.
      Two things only measuring caught: a caption whose figure is
      already placed must be left alone (the extractor writes the
      picture *and* leaves the caption in the prose), and "Figure 2
      shows a recorded performance" is a sentence, not a caption.
- [x] **A reading queue** (377b3ad, migration 21). A request was one
      field on the document, so asking for one replaced whatever was
      waiting — the note from 2026-09-20 ("worth a queue some day") came
      due the moment every PDF wanted crops beside its other readings.
      Now a `readings` row per request, several per document, oldest
      first, and the hand-out offers one reading of a document per batch
      (two annotating readings are computed from the same text, so the
      second would undo the first). `meta.reading` stays as the last
      reading that finished.
- [x] **What the queue made possible**: the door asks for the crops
      itself after any PDF parse whose captions have no pictures
      (`pipeline.follow_ups`), queued beside whatever waits rather than
      over it; a reading that runs **no model** is served first
      (da59e98), because it costs seconds and makes the pictures the
      expensive readings then read; and `--bare-captions` (837e30b)
      selects the documents the pass has something to do in — without
      it the command asked all 9,323 PDFs, 6,000 of them for nothing.

### What is left of the figures

The crop pass over the backlog ran on 2026-09-24 night: 3,066
documents, about a hundred a minute, the cost being the fetch of each
original rather than the geometry. The pictures it makes arrive unread,
so they join the vision queue behind what is already there — reading
them is a separate decision, and the sensible shape is the documents
you actually reach for rather than the whole library.

A known limit: a caption line needs a delimiter after its number
("Figure 2:", "Fig 1 --"), which is what keeps prose about a figure from
being taken for one. A book whose captions read "Figure 2.34 A simple
circuit" is therefore mostly skipped — *Practical Electronics for
Inventors* has 1,005 bare captions and yielded one picture. Widening
that rule wants its own measurement, because the failure it prevents
(cropping from a sentence, sweeping the real caption into the picture)
is worse than the one it causes.

## 2026-09-24: the language nobody chose

The reader's complaint was that the abstracts of German papers were
sometimes English and sometimes German, "slightly random". The sources
are not: over a balanced 600-document sample `meta.lang` disagrees with
the body in 1%, and front matter in another language than the body turns
up in about 2%. What is random is ours.

Of 9,030 summaries, 8,746 are English and 273 are not, 270 of them
German — but only 12% of German documents are summarised in German. An
*Arbeitsbescheinigung* described in English, the brand-eins article
beside it likewise, no rule. The cause is one silence: the extraction
prompt asked for "two or three sentences a reader would use to decide
whether to open the document" and said nothing about which language, so
the model chose, mostly English. The same silence governs entity names
three rules above it, which is where `celeriac` came from and why
`Olivenöl` (10 edges) sits beside `olive oil` (8).

It is not only untidy. `meta.summary` *is* most of the document field
that `documents_fts` and `document_embeddings` search, so a German query
meets an English description of a German document — part of the German
keyword MRR of 0.39 against English's 0.91
(`docs/eval/retrieval-multilingual-2026-09-24.md`).

- [x] **The ontology says which names translate.** A `naming:` key per
      entity type, `proper` (a person, a publisher, a product, a title —
      the same string in every language) or `common` (a kind of thing,
      which every language has its own word for). The nearest
      declaration wins, so a subtype may differ from its parent in either
      direction: a `dish` is a `work` whose name translates, a `standard`
      is a `concept` whose name does not. A type that says nothing and
      inherits nothing is proper, because translating a name that should
      not be translated is the worse mistake. Ten types are common:
      claim, concept, cuisine, dish, feature, ingredient, material,
      method, spec, technique.

      It bumps no module version. It says how a type's *names* behave,
      not what types exist; no triple that validated before stops
      validating, so nothing re-extracts. What changed is the prompt,
      and the prompt is a producer — which edges already carry a column
      for (invariant 8).
- [x] **The prompt says it.** One rule, built from `onto.common_types`
      rather than a list kept beside it, so the extraction and the
      vocabulary pass cannot drift apart. New documents stop drifting;
      the ones already read are the two passes below.
- [x] **The `summaries` step.** A summary in another language than the
      field, translated by the local model: 273 documents, no source
      document read, no paid call. `meta.summaries` keeps every summary
      we have keyed by language, so the German one is not lost to the
      English one that replaced it; `meta.summary` is the canonical
      English text the field indexes; `meta.summary_lang` says which
      language that is, and the `languages` pass of `prax maintain`
      fills it in for the summaries written before it existed. A summary
      too short to place claims no language and is never handed out.

### What is left

- **The native summary in the field.** `meta.summaries` holds the German
  text; the field does not index it yet. Adding it is the cheapest lever
  on the German keyword number, and it is one line of `document_field` —
  but it widens every German document's field, so it wants the eval run
  on either side of it before it goes in.
- **Section summaries for a long document.** Two or three sentences for
  a book is two or three sentences for its first chapter and nothing for
  the rest. A summary per chapter or per large section would give the
  document field something to say about the middle of a 400-page book,
  and would give `ask` a cheaper way in than the chunks. The shape is
  probably a summary chunk per heading region, written by the same local
  model, indexed like the document field rather than like text.
### The vocabulary pass: the graph half

Measured against the ontology's own split rather than a list written
beside it. Of 42,068 live entities of a common type, **372 are named in
German** — concept 238, method 67, ingredient 37, dish 19, cuisine 6,
technique 5 — carrying **533 live edges**. That is 0.9%: the extractor
already writes English most of the time, which is why this is a pass over
a few hundred and not a re-reading of the library.

An entity counts when its name carries a mark no English word carries
*and* its evidence is a German document. Both, because "functions" ends
in the letters of a German ending and is not German.

The twins are mostly already there. Of ten checked by hand, eight have an
English entity in the graph: `Olivenöl` (10 documents) beside `olive
oil`, `Speicherverwaltung` beside `memory management` (38 edges),
`Gauß-Elimination` beside `Gaussian elimination`. So the pass is mostly a
merge, not a renaming.

### It ran on 2026-09-24

1,435 entities decided, about half an hour of the local model, nothing
spent: **556 renamed, 309 merged, 509 already the word English uses, 89
type clashes queued for review**. The candidate net ended at zero.

What a sample of thirty renames looks like: twenty-two are plain
translations (`levinson-rekursion` → Levinson recursion, `durchlaufzeit`
→ throughput time, `betriebsrentengesetz` → pension scheme act), four
repair a garbled or mixed name (`multipiste editing` → multitrack
editing, `bplus tree` → b plus tree), and four replace an English name
with another English term — of which two are the field's own
(`budgeted cost of work performed` → earned value, `BFPRT algorithm` →
median of medians algorithm), one is defensible (`microphone wind
protection` → windscreen) and one is lossy (`card game 17 und 4` → 17
and 4). Call it one clear loss in thirty, all of it undoable by run.

The clash queue earned its place immediately: it caught `aktie` against
an entity named `stock` **typed as an author**, and `compilerbau` and
`sizetest` against entities typed as `paper`. Folding those would have
been a disaster, and the 89 are as much a report on the graph's existing
typing as on this pass.

Three bugs, two of them silent, are in the commits of that night: the
fold landed on the twin rather than on the twin's survivor, so 62
entities were refused by `merge_entities` and asked again for ever; an
outcome that wrote nothing (`already`) left the entity a candidate; and
`unmerge_run` had no route and no CLI, so the undo this pass advertised
could only be reached by a script that opens the database.

- [x] **Name them.** The local model gives the English name of each,
      under the rule the extraction prompt now carries: the common noun
      translates, a proper noun inside it does not
      (`Kolmogorov-Komplexität` → `Kolmogorov complexity`,
      `Büchi-Automat` → `Büchi automaton`). A name that comes back
      unchanged is already English and means nothing to do, which is how
      `Gödel's incompleteness theorems` — caught by the umlaut in a
      person's name — takes itself out.
- [x] **Record it as a label, not a rename.** `entity_labels` (migration
      20) holds the German name with its language, producer and run, so a
      German search still reaches the entity and `unmerge_run` can undo a
      round.
- [x] **Merge where the twin exists**, through the tiers that already
      exist, signed with a producer and run.
- [x] **The type clashes come with it.** `Olivenöl` is an `ingredient`
      and `olive oil` a `concept`; `mengenlehre` a `concept` and `set
      theory` a `method`. The pass will meet the polysemy question head
      on rather than beside it, and should hand a clash to the review
      queue rather than guess.

## 2026-09-25: the second hop stops being returned as the territory

`traverse` at two hops on `Fourier transform` was 3.4 MB. The measurement
of why — including a scale-free fit of the whole graph, and two
plausible explanations that died on the way — is
`docs/eval/traverse-neighbourhood-2026-09-25.md`.

- [x] **The two hops stop sharing a shape.** `edges` is the entity's own
      facts with their evidence, untouched, which is what the UI draws
      and what the surfer reads; `neighbours` is the map of what the
      documents around it are also about. `left_out` counts what did not
      fit, because a map that drops the rest silently is worse than a
      large one.
- [x] **Never land on a document**, tested against the ontology's own
      hierarchy so a new module's document type needs no list in the
      code. A document node is a record; expanding one returns its
      catalogue card, which was 22% of the payload.
- [x] **Rank by independent evidence** — how many documents separately
      say so. Invariant 8 read as a ranking. It beat inverse-degree
      weighting and Zhou's resource allocation on the same
      neighbourhood, and needs no degree lookup, which matters because
      degree by canonical id undercounts.
- [x] **A quota per type** (`graph.neighbours`, `graph.per_type`,
      `graph.min_documents` in prax.yaml): ranking alone gave 44 papers
      in 50.

On the live store: **3,439 KB → 76 KB**, and the map reads as one — 12
methods, 12 tools, 5 concepts, 3 authors. 818 passed.

What it did not fix is in `docs/PLAN.md`: a hub's own first hop is still
325 KB.

## 2026-09-25: the sections pass, and three A/Bs of one question

The pass finished: **2,050 long documents**, about 10,400 summaries,
sixteen hours of the local model over two days, nothing spent.

- [x] **What they are worth to the document field**, measured on a copy:
      hit@10 **0.087 to 0.130**, MRR 0.048 to 0.077. Half again as many
      documents found, from a low base, on questions about the middle of
      a book — which the field could say nothing about before, since a
      book's own summary describes its first chapter.
- [x] **Two wrong designs before the right one**, which is the part
      worth keeping: heading queries gave +0.263 and are circular, since
      the field holds the heading as well as the summary; removing the
      summary's words gave +0.007 and excluded exactly what could match.
      A factor of forty between two plausible A/Bs.
      `docs/eval/sections-2026-09-25.md`.
- [x] **The read budget was in the wrong unit** — three documents could
      not be read at all until it was. 12,000 characters of Arabic is
      23,834 tokens; `models.fits` now bounds by UTF-8 length too, and
      extraction had the same cap with the same number.
- [x] **The give-up rule from the day before did not fire**, because it
      wanted byte-identical messages and these differed in a token count:
      71 failures. It counts kinds now.

## 2026-09-25: traverse gets a bounded worst case

- [x] **The second hop became a map** rather than every edge in it:
      3,439 KB to 76 on `Fourier transform`, ranked by how many documents
      separately say so and capped per type, never landing on a document.
- [x] **The first hop got a cap of its own**, spent round-robin over the
      relations so the rare one survives: 319 KB to 71 on `nonnegative
      matrix factorization`. `traverse` now answers in 70-78 KB whatever
      it is asked about, at either hop.
- [x] **The container citations went** — 851 edges that pointed at a
      proceedings volume rather than at the work inside it — and the
      extraction prompt now asks for the volume as a venue, so a library
      started today grows none.
- [x] **The hub that remains is earned.** 1,947 edges, every one its
      own: a real PDF someone imported. The first design would have
      retyped it and destroyed them; the dry run is what caught that.

`docs/eval/traverse-neighbourhood-2026-09-25.md` has the measurements,
including the two explanations that died on the way.

## 2026-09-25: the multilingual embedder, and what the card was costing

- [x] **`embeddings.model: multilingual-e5-small`** in the live
      prax.yaml, the last step of the multilingual study. Same 384
      dimensions, so `VEC_DIM` is unchanged and the only cost is the
      re-embed of 1,119,641 chunk vectors and 9,982 document fields.
- [x] **The plan's cost estimate was wrong twice over.** It assumed
      three to four times bge-small's compute; measured with the card
      free, e5-small is 331 chunks/s against bge-small's 418 — 20%
      slower, not 350%. And the 9 chunks/s measured earlier was not the
      model at all: llama-server held 20.8 GB of a 24 GB card, the
      DirectML embedder could not get a device (174 "GPU device instance
      has been suspended"), and it fell back. Stopping llama-server is
      the whole difference.
- [x] **"The old file keeps serving" was wrong too.** The door refuses
      vectors from a model that is not its own (`work.py`, "the door
      embeds with X, not Y"), which is right, so door and worker share
      the setting and the new index starts empty. Search degrades
      gracefully rather than breaking: FTS is untouched, vector recall
      is whatever has been re-embedded so far, and hybrid returns hits
      throughout.
- [x] **"Reverting is one line" was wrong as well**, found mid-run.
      `chunk_embeddings.chunk_id` is the primary key and the insert is
      `ON CONFLICT(chunk_id) DO UPDATE SET model = excluded.model`, so
      the table remembers **one model per chunk** and the record that a
      chunk has a bge-small vector is overwritten as the e5 one lands.
      The old 1,380 MB index is physically untouched — 1,579,095 vectors,
      keys readable — so going back does not mean recomputing; it means a
      script that reads those keys and puts the rows back. Cheap, and not
      written. The design holds one model at a time on purpose, which is
      defensible; what was wrong was calling it reversible without
      checking.
- [x] **The protocol, not the model, is the limit** — 331 chunks/s of
      capacity delivering 98. In `docs/PLAN.md`.


## 2026-09-26: one German question, and what it was standing on

The walk (stage A) and the invariant kinds (stage B) are recorded in
`docs/PLAN.md` and `CLAUDE.md`. Stage C started from one question,
"Apfelkuchen", which found no recipe and three reasons why
(`docs/eval/apfelkuchen-2026-09-26.md`).

- [x] German compounds split where the library knows both halves
      (`prax.compounds`), MRR 4.33 → 4.83 on the 19 German questions.
- [x] An ingredient list without a heading, found by what its lines say:
      kitchen documents with a list 36 → 56 of 68.
- [x] A name is written as printed and translated once, by the watched
      `vocabulary` step, which keeps the printed word. A local model asked
      to write both names on one line wrote neither (0 of 145). The 74
      kitchen documents were re-extracted: 1,029 `calls_for` edges.
- [x] The vocabulary step's two faults on the day's 94 renames: the
      title taken for the answer (fixed by one clause, 1 → 0 wrong of 81),
      and "any English document uses it" letting out `Mehl` (now a rate).
- [ ] 979 entities the old rule ruled out wait for a re-judgement; the
      brand collision is next.

No German document in this library names an apple, so `Apfelkuchen`
still does not reach the recipe by the graph. Every piece of the route is
now in place and tested, and the library holds no word for it to cross
on.

## 2026-09-26: stage D, the embed hand-out and the delta

- [x] **The hand-out walks on from where it was.** It used to scan from
      the top every time, past every finished chunk. Now the door
      remembers the lowest id it handed out and asks below it. It looks
      first at the range above the highest id there was when the walk
      began, so a new capture is not starved. At the bottom it looks once
      more at the stretch it passed (what a lease let go) and starts a
      new walk. The two counts that answer "nothing pending" are asked
      only when a walk begins. On a snapshot copy with half the embedding
      rows removed (the state it was measured in): **755–800 ms per
      hand-out before; 770 ms once and then 1 ms after**, the same 1,600
      distinct chunks in both arms.
- [x] **The delta is written every 30 s** (`door.vector_save_seconds`),
      after 20,000 vectors, and when the queue drains, not after every
      batch (31 MB rewritten per 200-vector POST, the 10–17 s slow POSTs
      of 2026-09-25).
- [x] **What that costs at a kill is given back at start.** `prax up`
      ends the door by terminating its job object, so no shutdown hook
      runs. The warm-up thread asks the index about the embedding rows
      newer than the index files (`store.reconcile_unsaved`, by
      `contains`) and forgets the ones without a vector, so those chunks
      are embedded again.

## 2026-09-27: stage E, the Step object

- [x] **A step is one object.** The work protocol was three `if step ==`
      chains: `work.hand_out` (363 lines), `work.take_in` (295) and
      `worker.run_once` (267). Adding a step meant three edits in two
      modules (the audit's finding 1). `prax.steps` is now a package. Its
      `__init__` keeps the import-free names and adds `get(name)`, and one
      module per family holds the objects: `parse`, `writing` (titles,
      summaries, sections), `vocabulary`, `extract` (extract, promote),
      `graph` (typing, resolve, adjudicate), `embed`. `prax.steps.base`
      holds the three shapes that repeated: handing out documents,
      taking results in one at a time, and a worker's pass over a local
      model. `work.py` went from 1,144 lines to 360, `worker.py` from
      1,159 to 918.
- [x] **Tests first.** No test drove the worker passes of summaries,
      sections or vocabulary through `run_once`; they were pinned against
      the old code before anything moved. A test now fails if the door's
      hand-out or take-in, or the worker's pass, names a step again.
- [x] **Two bugs fell out of the move.** Titles, summaries, sections,
      vocabulary and extract checked for a paid model only *after*
      fetching, so a refused batch sat leased for fifteen minutes; every
      step now refuses before it asks. A vocabulary result deferred
      because the model server was not ready made the door read a
      `doc_id` its entities do not have.

## 2026-09-27: stage E, the store's module split

- [x] **`documents` (2,739 lines) and `graph` (2,261) are packages of
      parts.** The split follows each module's own call graph, read with
      `ast`: a part uses only the parts before it, which the splitter
      checked before writing anything. `documents`: meta, text, library,
      domains, readings, reads. `graph`: edges, decisions, labels,
      languages, context, traversal. Each package's `__init__` re-exports
      every name, private ones included, so `from .documents import
      _write_chunks` elsewhere in the store is unchanged, and declares an
      `ORDER`. The largest file is now 681 lines.
- [x] **The invariant test reads the parts.** It used to look only at
      `name.py`, so a subpackage's files would have escaped it. Now every
      file of a package must be in its `ORDER`, a part may import only
      the parts before it, and no part may import a store module at or
      after its package.
- [x] **What reached inside had to move.** `store.repair` read
      `_read_archive` through the documents module although it is base's.
      Three tests reached through `store.documents` to names it had only
      imported, and one patched `page_counts` on the package, where
      `count_pages` no longer looks for it.
- `retrieval` (1,837) and `repair` (1,460) stay whole. The line the api
  was split at is two thousand, and neither has reached it.

## 2026-09-27: stage E, the reads outside the store

- [x] **41 reads of prax's tables outside the store, now none.** It was
      26 on 2026-09-22, 38 at the audit, 41 today. Nine more `execute`
      calls are the Zotero importer reading its own read-only copy of
      `zotero.sqlite`, whose tables are not prax's, so they stay. Each
      of the rest became a store read for its question (30 in all: a
      document's source and state, the capture lists, the summaries and
      titles to check, the DOIs, the arrivals for a briefing, an
      entity's name and degree, the entities a set of documents shares,
      a term's documents, a phrase's languages). The caller keeps its
      own judgement: whether a summary is acceptable, what a title
      needs. Each read went in the store module the order allows.
- [x] **A test holds the line.** It fails on SQL outside the store that
      reads one of prax's seventeen tables, or any `MATCH ?`, since a
      keyword query names its table in an f-string. Run against the
      last commit's files it catches 34 of the old lines, and the
      `MATCH` clause catches the compound splitter's. Being allowed and
      counted is what let the number grow.

## 2026-09-27: stage E, the three names

- [x] `models.fits` → `models.trim_guessed`, `models.fits_for` →
      `models.trim_measured`, `sections.read_for` → `sections.budget`.
      The first two are a pair: cutting a text by a character and byte
      budget, and cutting it by the server's own token count where it can
      count. The third is the sections step's defaults over them. The old
      names said neither which was the guess nor whose defaults they
      were. Stage E is done.

## 2026-09-27: a name is not an identity

- [x] **Measured first** (`docs/eval/fractured-names-2026-09-27.md`).
      3,739 names are held by more than one entity. About 2,000 are a
      document beside its topic, which are two things. About 1,800 are
      one thing in pieces: 75 missed same-type merges, 34 subtype pairs
      and ~1,700 unrelated-type splits, mostly tool/method. The repair
      belongs to resolution and waits.
- [x] **Traverse walks one thing.** It seeded the walk with every entity
      a name reached, whatever its type, and merged their edges.
      `store.senses` now lists what a name reaches: canonical entity,
      type, edges, documents, domains. Senses whose types are the same or
      one a subtype of the other are gathered as one thing, which covers
      the missed merges and the subtype pairs. When unrelated things
      remain, one is walked (the one of `type`, else the most connected)
      and `senses` names them all with the walked one marked: never
      merged, and never one-sided without saying so. The door, the MCP
      tool (`type`), the CLI (`--type`), the UI (a node's type) and the
      surfer's walk (`walk: apple (ingredient)`, which its answer
      suggests) all go through it. `senses` costs 34-61 ms after reading
      each sense's edges through its own index; the first version, an OR
      of the two ends, cost 200-400 ms.

## 2026-09-27: stage F, what waited on a word

- [x] **Twin documents:** 2 left of the 6 the plan named, both recent
      re-downloads (ids 10334, 10335) of papers held since the first
      import, 96% the same text. Retired into their keepers; the check
      finds 0.
- [x] **The seven promotions** had already been read: nothing waited,
      nothing spent.
- [x] **The unread figures are smaller than the plan said and now have
      a clock.** The 55,607 counted every unread figure chunk. 46,930 of
      those are captions with no picture behind them, which no reading
      changes. What the vision model would actually read is 13,070
      captioned pictures in 1,984 documents, plus 2,497 uncaptioned
      ones, which the captioned pass leaves alone. At about 55 documents
      an hour that is a day and a half of the card, and a reading that
      was asked for goes before a capture's parse. So the door's clock
      has a fourth entry, `schedule: figures`. At its hour it asks for
      the next slice (150 documents here, about three hours) of
      documents with a picture the current model has not read, and
      nothing while the last slice waits or the model is paid. It is a
      job like the others, so a restart does not run the night twice.

## 2026-09-27: stage G, one thing in pieces

- [x] **The safe merges were a plan away and never applied.** Entity
      resolution already proposes the sure merges (equal after
      normalisation), the subtype folds (a person who is an author) and
      the concept/method twins. They pile up because `prax resolve
      --apply` is run by hand. A sample read sound, with one known
      blind spot: a person mistyped as concept and method folds into a
      method (`W.E.B. Du Bois`). Applied: 205 sure, 59 subtypes, 130
      twins, run `resolve-20260926T234815`.
- [x] **On the door's clock now** (`schedule: resolve`, 03:00, before
      maintain): the same round nightly, the endpoint's own job. The
      likely pairs (741, by name embedding) stay a person's.
- [x] **The rest is listed, not merged.** `heal` has a report-only
      ailment, `split-names`: one name held by things of unrelated types,
      a document beside its topic not counted, the biggest part first.
      `POST /graph/merge` is a person's answer to it (across types only
      when asked, filed under a run). A traverse from such a name already
      walks one thing and names the others.
- [x] **A merge could not always be taken back.** `unmerge_run` found a
      round's merges through the label each wrote under its run, written
      `INSERT OR IGNORE`. When the survivor already answered to that
      name, nothing was written. The round above left 363 labels for 394
      merges. Migration 25 stamps the merge on the entity (`merged_by`,
      `merged_run`), carries over what the labels knew (873 of 23,494
      merges ever made had a run), and `unmerge_run` reads both. The 31
      unrecorded merges of that round stay unrecorded; they were
      same-name merges, correct as made. The migration took 0.4 s on a
      copy.

## 2026-09-27: stage H stopped, the local model is gone

- The biggest hub is not a container. The entity *Proceedings of the
  International Conference on New Interfaces for Musical Expression*
  (degree 1,947, every edge outward) is 118 NIME papers whose title, in
  each PDF's metadata and in Zotero, is the volume's name, merged into
  one by that name. 1,820 live documents share a title with another.
  The fix is to retitle them, with the shared title named to the model as
  not this one (`not_title`, done) and a re-extraction after, so the facts
  leave the shared entity (to do).
- **The titles probe found the local model broken.** llama-server
  answered `3333…` to every prompt (an empty string under the titles
  step's newline stop). Restarted, it could not open its weights:
  `~/.cache/huggingface/hub` is gone, the 35B GGUF and its `mmproj` with
  it. `~/.cache` was last modified 2026-09-26 22:12 local time. Nothing
  garbled reached the store: the model steps' own checks refused it, and
  the last good output was in the 21:00 UTC hour. The embedder is in
  `C:/prax-data/models` and was not touched, so search and embedding run.
  llama-server is paused (`prax up --start llama-server` once the files
  are back); every model step waits until then.

## 2026-09-27: stage I, taco and tacos

- [x] **A rare word also matches its other forms**, where the library
      uses them: a plural or inflection ending (`s`, `es`, `e`, `n`,
      `en`, `er`) added or taken off, kept where the form is a term of
      three documents or more (`compounds.forms`). The library is the
      word list, as for a compound; no stemmer per language, and no
      rebuild of the index. Measured on the eval set, read-only, three
      shapes:
      | rule | English found / MRR | German found / MRR | top tens moved |
      |---|---|---|---|
      | off | 13 / 0.392 | 7 / 0.122 | — |
      | every word | 12 / 0.371 | 6 / 0.137 | 38 of 39 |
      | a word under 50 documents | 13 / 0.392 | 6 / 0.144 | 14 |
      | …and a form in its range (under 10× the word's, or 50) | 13 / 0.392 | 6 / 0.141 | 12 |
      English does not move at all under the last rule. The German side
      trades one paper lost just past rank 10 (it was 9th: "robuste"
      gains "robusten" and "robustes") for a higher MRR. The niggle
      itself: "taco" and "tacos" now open with the same two documents,
      the tacos recipe first; "news" gets no forms, being common. The
      switch is `retrieval.INFLECT`.

## 2026-09-27: stage J, one workflow for maintenance and healing

- [x] **One list.** `GET /maintenance` gives every pass the door runs on
      itself in one shape: what it does (its own docstring's first
      sentence, `store.pass_notes`), when the clock runs it, how it went
      last, and the request that starts it where starting it from a page
      is safe. The idempotent passes, the resolve round, the figures
      slice and the questions are; backup (its destination) and the
      rechunk (its cost) are left to the command line, and the ailments
      stay in the health panel, which already had its buttons. The Jobs
      view shows it as the Passes table, with a run button per pass.
      `POST /figures` starts the slice by hand too.
- [x] **The banner.** `/changes`, which the UI polls every ten seconds,
      names the running maintenance job (`store.running_of`), and the
      header says it on every view, "maintenance: maintain 3,400 of
      9,962", linking to Jobs. A page is slower while one runs, and the
      reason is now on it.
- One run of the full suite during stage I failed one test that seven
  runs since have passed. Its name was not captured, and CI now prints
  failures by name.

## 2026-09-27: stage H applied, once the model was back

- The local model was fetched back (20.9 GB and the 0.9 GB projector,
  the revision prax.yaml names, so its path did not change) and
  llama-server started by a waiter once the download was whole.
- **The probe first**, read-only, through the real selection and the
  real `do_titles`: two documents from each of the ten biggest shared
  titles. The NIME papers got their own titles, lecture and exercise
  sheets a title saying which, and where the model found nothing usable
  (exams, one survey held several times) the document is marked tried
  and left. The step takes 585 documents in 78 titles, not all 1,820:
  the rest have no text, were tried before, or carry a title a person
  or a pass wrote, which the step does not touch.
- **Applied.** The standing worker's scope is captures and these are
  Zotero imports, so a one-off drain ran the titles step over
  everything, 50 a pass: 642 retitled by the time of writing, each
  retitle away from a shared title asking for a re-extraction, which
  the standing worker takes (a requested extraction goes first,
  whatever the scope). No document is titled like the volume any more.
- **What it looks like halfway.** When the last document leaves a title,
  `retitle` renames the entity after that document's new title, so the
  volume's 1,696 edges from 114 documents sit for now on *SoundGrasp*,
  the last paper retitled. Each re-extraction retires that document's
  old reading, so they drain as the queue does (262 waiting at the time
  of writing).
- The first boot of the fetched model mapped the whole file into RAM
  (16.6 GB working set, 1.7 GB left free), and Claude Code reaped a
  background wait under the pressure. The pages are file-backed and
  reclaimable; the same server stood at 15 GB free before the deletion.

## 2026-09-27: stage K, browsing by module

- [x] **A module holds the documents of the modules built on it.** The
      browse list's module filter (and a search's) took only the
      documents listed under that exact module, so "craft" showed its own
      27 and none of the 68 kitchen or 35 workshop documents, which are
      read against craft as well. `Ontology.within(domain)` names the
      module and those built on it, and `list_documents` and a search's
      `_filter_domain` both read it.
- [x] **The unassigned documents alone:** `domain=unassigned` in
      `GET /documents`, and a choice of that name in the browse list.
      The documents no module was set for stay in every module, as
      before; this shows them by themselves.
- [x] **A module chosen filters at once**, without a second click on
      Filter.

## 2026-09-27: stage L, the graph by module and by document

- [x] **One module's graph.** `store.hub_graph(domain=)` draws the
      overview from what that module's documents (and those of the
      modules built on it, `Ontology.within`) say, and adds the module's
      own kinds of thing to the hub types, so the kitchen opens on olive
      oil, salt, onions and Dal Makhani rather than on concepts. The
      documents no module was set for are left out here, unlike in a
      browse or a search: they are in every module and would draw the
      whole library back in. The graph view has a module select; on the
      live store the kitchen overview takes 1.2 s, studio 0.4 s, the
      unfiltered one 2.6 s as before.
- [x] **A document's own graph.** `GET /graph/document/{id}`
      (`store.document_edges`, at most `DOCUMENT_EDGES` = 300) returns
      the document's live edges in traverse's shape, the facts about its
      own entity first. The document page has a `graph` link to
      `#graph?doc=N`, whose nodes expand across the library as any.
      Document 10070: 21 edges.

## 2026-09-27: stage M, ontology v9

- [x] **A document where a paper was named** (`docs/ontology-v9.md`).
      Research 8 to 9: `cites`, `defines`, `extends`, `contrasts`,
      `supports`, `uses`, `proposes`, `funded_by`, and the far end of
      `annotates` and `synthesizes` take any document; `advised_by` any
      person. Only widened. On a snapshot the replay links 85 of the 895
      typed items in the queue; the nightly `maintain` does it live.
- [x] **Migration 0026** restamps the 2,292 `research8` readings. Its
      pattern matches the version string alone: 1,033 of those metas were
      written by `json_set`, which leaves no space after a colon, and a
      pattern like 0018's would have left them due for a re-read.
- [ ] **The older readings.** The "1,021 documents" of 2026-09-10 are
      6,412 at `core1+research5` today. None is a capture, so the
      standing worker (scope captures) never draws them. At the 230 an
      hour measured this morning a full re-read is about 28 hours of the
      local model. The user's call.

## 2026-09-27: stage M, the electronics module

- [x] **Electronics 1, on top of studio** (`docs/ontology-electronics.md`).
      It adds `part_kind` and `package`, and three relations: `shows_part`
      (a schematic's or datasheet's bill of materials), `serves_as` (what
      kind of part) and `in_package`. It is grown from the queue of the
      library's ten electronics documents: a schematic's parts were
      refused 15 times, and a part's kind was tried three ways.
      `package` is declared ahead of its evidence, since no text here
      prints one. Pins are not entities: "VCC" names a different pin on
      every chip.
- [x] **Studio 5:** `has_part` from a component too.
- [x] **Migration 0027** moves the `studio4` stamps. The 61 documents read
      against every module stay due, because they were never read against
      electronics.
- [ ] **The electronics documents' domains** (the user's call). Four are
      tagged `studio` and so are read without electronics, and two
      schematics are tagged `research`.
- The user's order: the full re-read of the 6,412 `core1+research5`
  documents follows once stage H's re-extractions are done.
- [x] **The six electronics documents tagged** (the user's word): `electronics`
      on all six, `studio` on the two research schematics, and each asked to
      be read again. On the way: a person's change of an upload's domains
      marked its reading stale but not asked for, so a worker scoped to the
      captures never took it. `_lens_changed` now records it as `requested`.

## 2026-09-27: stage N, the card and the memory when nothing runs

- [x] **The mapped weights.** The 16.6 GB of the plan was llama-server's
      working set after a fresh load: `--load-mode mmap` (kept for the
      commit charge) leaves every page of the model file there although
      the card holds the weights. One `SetProcessWorkingSetSizeEx(-1, -1)`
      took it from 11.9 GB to 1.8 GB, and it stayed there: the experts of
      the two layers on the CPU, and the buffers. Available memory went
      from 5.2 to 8.8 GB. Before the trim the server's gauges said 1,859
      prompt tokens/s and 81 generated; after it, 1,685 and 87.5 over two
      minutes of extraction, the same. `hostinfo.trim_working_set`, and
      `prax up` does it once a server answers its first health check
      (`trim`, on by default on Windows).
- [x] **The card back when nothing uses it.** `idle_minutes` on a model
      server: the supervisor reads its `/metrics` every thirty seconds,
      stops it after that long with no decode and no request in flight,
      and shows it `idle`. It loads again when the door reports demand:
      a worker's "not yet" (already a deferral, never an error, so a
      worker does not give up over a sleeping server) now counts for the
      role serving that step's model (`work.want`, `work.role_of_step`),
      and so does an `ask` that found the server gone, which answers that
      it is loading. The quiet time is the hysteresis the plan asked for:
      the 35B takes minutes to load. 30 minutes on this host.
- [x] **Who holds the card.** `holders()` was built on 2026-09-25 and
      served by `/up`, but nothing showed it. The Jobs view now says
      "On the card: llama-server 20.3 GB · waterfox 4.4 GB · dwm 2.5 GB".
      prax's own share is llama-server and a 291 MB worker: unloading the
      server is what frees the card.

## 2026-09-27: stage O, a sender for other machines

- [x] **`clients/send/prax_send.py`.** One file, the standard library only,
      written for Python 2.7 and 3 (no Python 2 on this host to run it
      under; the tests run it under 3.13). It walks a tree without
      following links and leaves out hidden files and a NAS's own folders.
      It puts PDFs and text first and hashes each file, remembering the
      hash by path, size and mtime, so a second run hashes only what
      changed. It asks the door which hashes it holds, 500 at a time, and
      uploads the rest with where they came from. Tested end to end
      against a door served by uvicorn with a token: a dry run, a run,
      a second run that sends nothing, and a wrong token that stops the
      run.
- [x] **The door's side.** `POST /known` (`store.known_hashes`, at most
      1,000 hashes a question, a retired document's too) and an `origin`
      field on `/ingest/file`, kept as `meta.origin` (host and path, nothing
      else).
- [x] **Audio and video, written down, not built**
      (`docs/media-by-reference.md`). The hash stays the identity, where a
      file lives is a list of locations checked by re-hashing, and only
      the derived artifacts are copied. The recommendation is to name the
      location first and to proxy through the door later.

## 2026-09-27: stage P, the regions of the library

- [x] **The hub decision, made structurally.** The partition's nodes are
      the topical kinds of thing; documents, people, organizations,
      claims, specs, places and events are left out. The biggest hubs
      were a university as a venue (725), the library's own author (513)
      and papers. Left out, they do not need deciding one by one.
- [x] **The partition** (`prax.communities`). Direct edges plus shared
      documents, each document weighing the same (1/(m − 1) a pair), among
      the 8,488 entities named in two documents or more. Louvain, seeded:
      24 regions (modularity 0.61), and 153 parts from splitting each
      region of 200 or more again. 3.3 s. The regions read right, from
      synthesis and MIDI to the kitchen, 3D printing and the transistors.
      networkx is now a declared dependency (it came transitively).
- [x] **Kept across nights** (migration 0028, the `communities` pass of
      `prax maintain`). A new community takes the id and summary of the
      old one it overlaps by half; under 0.8 overlap the summary is
      marked stale for the step to write again.
- [x] **Named and described**: the `communities` step, on the local
      model. It is given the forty heaviest members and the eight documents
      naming most of them, and answers with a name and what the region
      covers. Regions go first, and a part waits for its region's name.
- [x] **The ways in**: `traverse`'s `community` key, `GET /communities`
      and `/communities/{id}`, the regions listed under the graph view's
      overview and `#graph?community=N`. The entity view now passes a
      member's type on, so a link from a region walks the right sense.
- [ ] Search and ask reading the regions: once the summaries exist, and
      measured.

## 2026-09-27: stage Q, a confidence that was measured (begun)

- [x] **`prax.calibration`**: P(yes) from a token's top alternatives,
      Brier, the reliability table, ECE, Platt's map and isotonic
      regression, with no dependency. The tests showed two bugs before
      they shipped. Newton's method ran away on inputs that take few values
      (a = 10^10), so the step now halves until the loss falls. The
      isotonic fit pooled a tie half gathered, putting one of 0.95's
      decisions into 0.05's block, so ties are now grouped first.
- [x] **`scripts/eval_confidence.py`** (build, ask, score; read-only on
      the store, resumable). The labelled pairs are fewer than the plan
      assumed. Weekly recomputation replaced 1,855 of the 4,559 recorded
      declines, and the 3,683 merges predate migration 25's stamp. So the
      set is the 2,704 declines left, plus the 3,422 merges of the likely
      types that differ after normalization and pass the 0.92 name
      cosine: 6,126 pairs.
- [x] **The pilot, 200 pairs** (fitted on 100, measured on 100, so
      rough). Raw: Brier 0.220, ECE 0.197, agreement 0.72. Overconfident:
      said 0.986, agreed 86%; said 0.015, disagreed 24%. Platt: ECE 0.098.
      Isotonic: Brier 0.191, agreement 0.76. A call takes about 6 s while
      the re-read holds the server's slots, hence the full run waits for
      it.
- **What the pilot found instead.** Many of the strongest disagreements
  are the label's error, not the local model's. "Different" by Opus:
  LDR/LDRs, Gauss-Seidel/Gauß-Seidel iteration (local 0.999). "Same" in
  the labels: preorder/postorder traversal, fractional Fourier transform
  and its short-time variant, hidden semi-Markov and its autoregressive
  form (local 0.000). Some of those may be reconstruction noise, but
  either way they are wrong merges in the graph. So the verdict needs a
  gold sample a person labels, and the "same" positives are worth a look
  of their own: a merge folded two things into one.

## 2026-09-27: stage R, the lists a person decides

- [x] **Three tabs on the Review page**, beside the queue of facts:
      *same thing?* (the 740 undecided likely pairs), *one name, several
      things* (2,243 split names, grown with the re-read from 1,632), and
      *merges to check*. Each side shows its type, the live edges of it
      and its aliases, and a document naming it. Every button is one
      decision.
- [x] **Every decision is a signed pair** (`store.decide_pair`,
      `entity_candidates` with `decided_by = 'human'`): the likely tier
      never asks again, and the decisions are Q's gold sample. A merge
      runs under a run of its own. A confirmed old merge is only
      recorded. A split name merges across types only when told to.
      `unmerge_entity` takes one merge back, which `unmerge_run` could
      not do for the unsigned round of 2026-09-17 without taking the
      whole round. `undecide_pair` takes a click back, merge and record
      both, so a slip is not left as a label.
- [x] **Which merges to check.** Ordering by least word overlap put the
      right merges first: spelling variants, and the vocabulary pass's
      translations, which share no word by design. The list is now two
      shapes from Q's pilot. *One word apart* (911): the same length, one
      word different, the two words not one spelled twice, and at least
      one word shared, so a one-word translation is not listed. About a
      third of a sample of twenty was wrong: ongoing/exit costs,
      preorder/postorder traversal, MIDI standard/file, archive read
      finish/close. *Narrower* (2,520): one name's words a strict part of
      the other's; mixed, with discrete Fourier transform folded into
      Fourier transform among the wrong ones. One word apart goes first.
      The vocabulary pass's merges are left out.
- The page reads are indexed per edge end (a join on `id IN (src, dst)`
  took ten seconds a page): 2–3 s for a page of thirty.

## 2026-09-27: stage S, the door's native exits

- **What `logs/up.log` holds.** Four native exits of the door, none
  since 2026-09-20: two access violations (0xC0000005) on 2026-09-17 at
  03:20 and 03:30, and two `STATUS_BAD_STACK` (0xC0000028) on 2026-09-19
  23:38 and 2026-09-20 01:58. The first pair is already explained
  (this log, "`prax resolve`'s likely tier out of the door"). The door
  embedded 130,000 names in-process with onnxruntime, and that work moved
  to the worker the same evening. The second pair came during embed
  merges with searches running. The merge stopped building under the
  lock after it.
- [x] **The reproduction** (`scripts/stress_vectors.py`). A child process
      on a temporary store, so a native crash ends only it. One thread
      adds to the delta the way an embed post does, readers take single
      vectors the way the similar-documents column does, and a merge
      runs every second. **No native crash**, over 3.6 million reads and
      183 merges. **A Python race reproduced twice in two runs.** An add
      looked up the delta before taking `_INDEX_LOCK`. When a merge swapped
      the delta in between, the add hit the closed index (`TypeError`). In
      the door that is an embed post failing with a 500.
- [x] **Every use of an index now holds the lock.** `_add_to_delta` looks
      the delta up and adds under one hold, and both `add_vectors` and
      `add_document_vectors` call it. `_get_vector` reads under the lock;
      it had read the delta while adds wrote it in place.
      `compact_vectors` builds on a private copy and swaps under the lock,
      like `_merge`. It had dropped the views and then saved over the
      main file unlocked, so a search in between could map the file
      before it was replaced. It also left its writable copy in the shared
      table. Two runs of the stress test after the fix: no error, and the
      writer ran the full two minutes (467 and 477 adds, against 77 and
      132 before it died).
- The cost: under the stress test's worst case, reads now wait behind
  the merge gathering the delta. In the door a merge comes once per
  50,000 vectors, and a document page reads about twenty.
- Whether the native exits were these races cannot be shown: the reads
  that raced only in Python need usearch to release the GIL to race
  natively. What can be said is that no index is now touched outside
  the lock, and `tests/test_vector_locks.py` keeps it so (the lock
  counted, an add across a merge, four seconds of the stress test).

## 2026-09-27: stage T, graph files

- [x] **`prax.graphio`** (`docs/graph-files.md`), as designed on
      2026-09-20. An export is what a project, a domain, a tag or an
      entity reaches: its entities with their labels, the live edges with
      every provenance column, the pages with all their revisions, and
      the documents by identity (hash, ids, title, summary), with the
      ontology's modules in the header. JSON lines with sorted keys in a
      stable order, so a second export of an unchanged library differs
      only in its timestamp. An import is `import:<name>` with a run per
      file. It retires its last import first, links through `store.link`
      with the origin in the evidence, queues what the ontology refuses,
      and gives a page with other text a new revision with a note. It
      rewrites a page's `#doc/N` links to this library's ids. A dry run
      writes nothing.
- [x] `GET /graph/export`, `POST /graph/import`, `prax export`,
      `prax import graph FILE --name --dry-run`.
- [x] **Found on the way: a project page could not link a document.**
      A page's links become edges from the page's entity, and a project
      page's entity is a `project`, which `annotates` does not accept, so
      `write_page` failed. The ontology already had the relation:
      `synthesizes` takes a project. A project page's links now use it,
      like a synthesis page's. No version bump, since the ontology did not
      change.
- [x] **And: `prax import citations` refused to run** ("which files?"),
      its own help's example included: the command-line check that
      wants files did not know citations take none.
- A page's own edges are not exported: importing the page makes them
  again from its links, and exported as well they would be there twice.
- [ ] The Claude Code plugin's session-end sync writing
      `.prax/graph.jsonl`, the last part of the design.

## 2026-09-27: the type hints, checked

- [x] **mypy in CI, at zero** (niggles.txt, "type annotations"). The
      hints were written but nothing read them: mypy's default mode found
      70 errors in 32 files. None was a live bug. One was dead code: a
      readiness check for a model `kind: gguf` that `models.KINDS` has
      never allowed, reading a `ModelSpec.path` that does not exist.
      `contextlib.suppress` would have hidden it had it ever run; it is
      gone. Two were a race worth a guard: the parse hand-out and the
      parse queue read `get_document(...)["text"]` of a document that
      may have been retired in between. The rest were names reused for a
      second type, the Protocols declaring `name` as a settable variable
      where every implementation has a property, and annotations. Strict
      mode (976) is later, file by file; the JavaScript's `@ts-check`
      wants `typescript` from npm, a download, so it waits for a yes.
- [x] **The archive's folders** (niggles.txt, "file cache"): 56,976 files
      in 256 folders, 177–263 each. No change; a threshold in CLAUDE.md
      (10,000 in one folder) says when a second level is due.

## 2026-09-28: stage U, tokens and the wall

- [x] **Named tokens** (`prax token add NAME [--domain M] [--personal]`,
      `list`, `remove`; `POST/GET/DELETE /tokens`, the administrator's
      only). Migration 0029: a `tokens` table keeping the sha256 of each
      secret (`prax_` and 32 random bytes, printed once), the modules it
      sees and whether it sees personal documents; and a `sensitivity`
      column on documents (`suspected`, `personal`), set by
      `PUT /doc/{id}/sensitivity`.
- [x] **The filter is in the store**, keyed by the request's viewer
      (`store.VIEWER`), so `ask`, the surfer and every read inherit it:
      eleven reads that take a document or a chunk answer as if it were
      absent; `list_documents` excludes and does not count; a search sets
      hidden documents aside before the lists are fused, beside the
      pages that are the model's own answers; the walk drops their edges,
      so an entity only they name is not reached; `senses` and
      `find_entities` do not name it; a write to one (a page over it, its
      domains, a promotion, a link from it) answers "no such document".
      The streamed `ask` ran on a thread of its own, which would have read
      unfiltered: it now runs in a copy of the request's context.
- [x] **A named token may call only the MCP tools' routes**
      (`auth.RESTRICTED_ROUTES`); every other route answers 403.
      `tests/test_wall.py` walks each allowed route with a personal
      document and a personal page and checks that none shows them, and
      fails when a route is allowed without being walked.
- The owner's name and the rules for what is personal are stage V, in
  `prax.yaml` only.

## 2026-09-28: DjVu

- [x] **A parser for DjVu** (`parsers._djvu`, DjVuLibre's `djvutxt`,
      `djvused`, `ddjvu`): the text layer page by page with the page
      marks, pages without one rendered and read by the OCR engine the
      scanned PDFs use, within the OCR budget. The page count is
      `djvused`'s: a scanned last page and the end of `djvutxt`'s output
      look the same, which a test caught.
- [x] **A type that is a document** (`prax.mimes`): DjVu's registered
      type is `image/vnd.djvu`, and six places read `image/*` as a
      picture (no graph extraction, a vision reading, "image" in the
      document field). They now ask `mimes.is_picture`.
- [x] **The six DjVu files already in the library** were registered as
      `application/octet-stream`: no MIME table has the type.
      `untyped-documents`, a repairable heal ailment, gives a document so
      taken in the type its name says, when a parser here reads it.
- [x] **The NAS sender** sends `.djvu` and `.djv`.
- DjVuLibre is not installed on the desktop yet: `winget install
  DjVuLibre.DjView` asks for an administrator's confirmation, which the
  session cannot give.

## 2026-09-28: the sender on a NAS of five terabytes

- [x] **A walk that ends.** A dry run over the whole NAS walked 798,470
      folders and never finished: a copied Windows profile's
      `Application Data` junction, followed by the backup into itself.
      The walk now leaves out a profile's junk folders, a name repeated
      three times in a row at the end of a path, a folder reached twice
      (device and inode), anything below `--max-depth` (40), and names
      given with `--skip`. The last line counts what was left out.
- [x] **Sending as it walks.** Files go a batch of about 500 at a time,
      PDFs first within the batch, so a large tree starts at once.
- [x] **A run that can be stopped.** The state file is a journal of JSON
      lines, one per file hashed and per folder finished, appended as it
      happens (a million files would otherwise rewrite a hundred
      megabytes a batch). A real run passes over a folder it finished
      while the folder's mtime stays; `--again` looks anyway. A dry run
      records no folder. A state file of the older shape is converted.

## 2026-09-28: Q, the adjudicator's labels and a rule for "the same thing"

- [x] **Answers by pair number.** The adjudicator answered forty pairs as
      a list by position and padded a short list with False. It now names
      each pair; a pair it does not answer is neither merged nor declined.
      Asked again, Opus agreed with its recorded answers on 82%.
- [x] **One rule** (`resolution.SAME_RULE`, the Review guide's rules of
      thumb): a version, edition, narrower kind, part or tool is a
      different thing. With it the local model settles 60% of the pairs
      at 97% agreement with Opus (`docs/eval/confidence-2026-09-28.md`).
- [x] **77 wrong merges split** through the door on the user's word:
      68 conference editions folded into one another (seven ICASSPs into
      2019, about thirty DAFx years), seven tools, x86-64 into x86. Found
      as the merges whose names carry different numbers, judged by Opus
      under the rule ($0.09), seven tidy-ups left alone.
- Next: the gold sample, a person's decisions under the same rule.
- [x] **The rule revised with the user:** a product's versions and a
      conference's editions are one thing (the library keeps the series);
      a word that changes the kind of thing named makes two; synonyms are
      one only within a field. 69 of the 77 splits merged again. Against
      the user's 218 decisions the local model agrees 80%, Opus 78%: the
      paid tier is no closer to the person than the local model.
- [x] **The NAS dump:** 2,921 documents, 1,789 of them RTF help and
      credit files from copied programs. The sender now leaves out a
      system drive's folders and a Mac program's bundle.

## 2026-09-28: stage Q settled, Q2 the rule as data

- [x] **The local model adjudicates** (`resolution.LocalAdjudicator`):
      one token a pair, Platt's fit on the user's 221 decisions, a pair
      settled at 0.9 either way and the rest left on the Review page with
      its number (`entity_candidates.p_same`, migration 30). At 0.9 it
      settled 40% and agreed with 87 of 89; Opus agreed with 78% overall,
      so the paid tier is off. A dry run over the 479 open pairs: 53
      merges, 82 kept apart, 344 left.
- [x] **Q2:** the rule is `ontology/sameness.yaml`, beside the lexicon
      and out of the version string, with a module's own cases for its
      types (a dish and its variant, a device and its manual, a part and
      its package suffix). The models are asked with it, and the Review
      page shows it above the pairs along with the local model's number.
      Measured again with the file's wording: 38% settled, 82 of 84.

## 2026-09-28: stage V, what is personal

- [x] **Rules that suspect** (`prax.private`): strong words alone, weak
      ones (grouped by meaning, so "Rechnung" and "invoice" are one) in
      pairs, paths alone. The owner's names and private paths go in
      `private:` in prax.yaml only. Measured read-only over 12,974
      documents: 274 at first. Then an IBAN alone (a donation box on a
      web page), "Personalausweis" alone (an exam leaflet), password and
      serial-number words (manuals and exercise sheets) were weakened or
      dropped: 238, of which a sample of 30 held about 25 plainly
      personal ones.
- [x] **Run when a text is indexed and nightly** (`store.suspect`, the
      `private` maintain pass); never over a person's decision, the cues
      kept in `meta.private`, a document with no cue left unmarked.
- [x] **The "personal?" tab** of the Review page, `GET /documents/suspected`.
- [x] **The owner's name was in the repository** as an example of a
      proper name in three docs, two modules and a test, since
      2026-09-24 and pushed. Replaced with a public name (Clara Schumann)
      and an invented one. The history still holds it.
- [x] **A section of prax.yaml the running door did not know** made it
      answer 500 for some minutes: `private:` was added to the file before
      the code knew the section. The order is code first, then the file.

## 2026-09-28: the embedder off the card

- [x] **A driver fault lost the worker's embedder.** At 11:11:15, four
      minutes after llama-server came up beside it, the NVIDIA driver
      logged a GPU error (`nvlddmkm`, event 153), and every embed pass
      failed with "the GPU device instance has been suspended" until the
      worker restarted. The card sharing of `prax up` decides between
      llama-server and marker; the worker's DirectML session is counted by
      nothing. The embedder now builds its session again once when
      DirectML loses the card (`ml.embeddings._lost_device`).
- [x] **The CPU is faster anyway.** 1,000 chunks of the library (668
      characters each), with llama-server and the worker busy: 21.1 a
      second on the CPU, 1.5 on DirectML. The int8 model is built for the
      CPU. `embeddings.providers: [CPUExecutionProvider]` in this host's
      prax.yaml; the card is llama-server's alone, as the allocation
      assumes.

## 2026-09-28: stage X, cleaning up in bulk

- [x] **Counted before built**, read-only over 12,977 documents. A copied
      system or program folder, or a licence/EULA/readme/credits file
      name: 238 documents, 1,154 facts. The same text under two originals:
      55. A program's help files: 896, 3,695 facts, mostly SuperCollider's.
      The user keeps these: "not really junk, more like noisy data". Under
      300 characters of text: 969, but these are scans without a text
      layer, which need a reading rather than a clean-up. The name rule
      first matched titles ("a (very) brief history"); it reads the file
      name only now.
- [x] **A set, shown, retired, restored** (`store.cleanup_preview`,
      `retire_set`, `restore_set`; `GET/POST /cleanup/{rule}`,
      `POST /cleanup-restore`): a run name on every document it retired,
      a copy retired as a duplicate so what it knew moves to the first
      copy, and the ids of the facts each ended kept, so a restore reopens
      exactly those. The "clean up" tab of the Review page.
- [x] **A test that tested nothing.** Stage V's check that a restricted
      token cannot reach `/documents/suspected` compared a path with a
      tuple of (method, pattern) pairs and was always true. It asks the
      patterns now, as the clean-up's test does.

## 2026-09-28: the engineering follow-ups

- [x] **The store's layering is a rule, not a move.** Its twelve calls up
      are passes that must touch the tables (invariant 3) and need a step's
      logic. At the top of a module it imports only config, `text`, `ml`
      and `graph.ontology`, and a test holds that.
- [x] **Each step's worker half is in its step module.** `worker.py` went
      from 933 lines to 274: the loop, the drop folder, the give-up
      counter. The reading chain and its heartbeat are `steps/parse.py`.
      Two copies of `_paid` became `ModelSpec.paid`.
- [x] **The parsers are parts.** `parsers/__init__.py` went from 1,706
      lines to 437. The cut moved one decorator across a section boundary:
      `@functools.cache` left `djvu_tool` and landed on `soffice_path`,
      which lint and types both passed. Caught by reading the boundary,
      then every boundary checked. Tests that replaced `parsers.djvu_tool`
      or `parsers._marker_up` replace them on the part now; on the package
      they would have stopped testing without failing.

## 2026-09-28: stage W, the administrative side

- [x] **An Admin page** beside Jobs, three tabs. *Tokens*: the named ones
      with their modules, whether they see personal documents, when made
      and when last used; a form that makes one and shows its secret once;
      revoke in two clicks. *Personal?*: the rules in force (defaults and
      what prax.yaml adds, the owner's names counted and never spelled
      out) folded above the suspected documents, moved from Review.
      *Clean up*: moved from Review too. An old `#review?list=personal`
      link lands there.
- [x] **A token's last use** was a column nothing wrote. It is written
      when a named token reads, at most once an hour per token, because
      the lookup is on every request and a write there would take the
      store's lock on every read.
- [x] **The rules are shown, not edited.** The door does not write
      prax.yaml; the file stays the one place they change.
- Screenshots of the three tabs from a throwaway door on an empty store:
  the token table, the suspected statement under its folded rules, the
  clean-up rules.

## 2026-09-28: stage Y, OCR in windows

- [x] **A long scan is read a window a pass.** Past `parse.ocr_max_pages`
      (60) a scan was refused: 71 PDFs were read by one-off runs with a
      higher budget, and four DjVu books of 190 to 746 pages had no text.
      Now both OCR readers (`djvu`, `pymupdf4llm-ocr`) take the current
      text, keep the pages it has (by their page marks), OCR the next 60
      without, and return a `parsers.Partial` saying how many pages wait.
- [x] **The door asks for the next window** (`queue.continue_windows`,
      from both parse paths): the text so far stored and indexed, so the
      book is searchable from the first window; `meta.ocr` says how far it
      is; a reading of the same extractor queued while pages wait. The
      unique index of the readings is on pending requests only, so the
      next window queues once the one before has finished.

## 2026-09-28: what "unreadable" was

- [x] **The 312 unreadable documents were mostly not scans.** 306 came
      from the NAS. 157 were RTF files of a few hundred bytes (a Mac
      program's `Credits.rtf`), read correctly and too short to count; 86
      PDFs are real scans (OCR candidates, about 210 pages); 12 PDFs are
      broken and 2 encrypted; 8 Word, 13 OpenDocument and 5 "RTF" files
      are empty, not what they say, or Cygwin links copied as files (the
      `not-documents` ailment names these now as what they are).
- [x] **24 Mac RTF files failed whole** on "unknown encoding:
      mac_japanese": a Mac names its Japanese, Chinese, Korean, Hebrew or
      Thai font's character set by its own name, and Python has no codec
      of that name. Each is mapped to its nearest kin (Shift-JIS, Big5,
      EUC-KR, …); the RTF extractor is revision 2, so the parse queue reads
      them again, and the rest of the RTFs come back "same".

## 2026-09-28: a way in, search

- [x] **A search says where its hits live** (`GET /search?regions=true`,
      `store.regions_of`): the region most of its first five hits'
      entities live in opens the list as an item of its own, with its part
      when the part is as clear. The search page shows it above the hits;
      the MCP tool asks for it.
- [x] **Measured before shown** (`scripts/eval_regions.py`,
      `docs/eval/regions-2026-09-28.md`). The query's words against the
      regions' names and summaries: right 50%, no threshold that helps.
      Where the hits live, each entity counted once: right 81%, and 92% of
      the 82% of queries shown at half of them. A part, among its region's
      parts, only at nine tenths (87% of 24%). Weighing entities by how
      central they are let one hub outvote a recipe ("Apfelkuchen"
      showed the audio software region), and was worse throughout.
- [x] **The personal review is done** by the user: 402 personal, 3
      released, none left suspected.
- [x] **Ask with the region's summary, measured and left off**
      (`scripts/eval_ask_regions.py`): over the 21 named regions an
      answer named 0.25 of the region's heaviest members with it, 0.24
      without; 8 broader, 5 narrower. The flag (`regions`) stays a choice.

## 2026-09-28: the document page, from the niggles

- [x] **Every personal document in one list**: the Admin page's "marked
      personal" tab (`GET /documents/suspected?state=personal`), the last
      marked first, each with "not personal".
- [x] **properties…** on a document's page, beside process…: where it came
      from (source, who sent it, the NAS path, the address), what it is
      (type, pages, language, the text's source, the graph's reading),
      where it belongs (domains and by whom, tags, collections), and who
      may see it (the state, who decided and when, the rules' cues) with
      personal and not personal. Built from what `/get` already carries.
- [x] **Back to the top**: a button once a document is a screen down.
- Screenshots from a throwaway door: the dialog, a click on "personal"
  (the state written, the button gone), and the button after scrolling.

## 2026-09-29: what the MCP client stumbled on

Found while searching the library for the classification research
(`docs/PLAN.md`) and capturing 23 papers through `capture_url`.

- [x] **A bot check is not a document.** MDPI answered the door with
      Akamai's bot check. Trafilatura found no main content and `plain`
      indexed the page's markup as document 13338 (retired). Now
      `inbox.ingest_url` refuses a page of under 32 KB that carries a
      known bot check's mark (`BOT_CHECK_MARKS`: Akamai, Cloudflare,
      Imperva, HUMAN, DataDome) with `BotCheck`, a failed fetch (502
      with the extension hint), and `plain` no longer takes `text/html`
      (`Extractor.excludes`): a page nothing reads is a failed parse.
      Four older documents hold the same kind of markup, SingleFile
      snapshots of a PDF viewer's tab (PLAN, "The UI and the agent").
- [x] **A capture is named by what its URL says.** An arXiv PDF was
      titled `0110053` or `2102.07396`, which `needs_title` did not
      flag, so the titles pass would have kept it. Now the capture keeps
      `meta.arxiv` (or `meta.doi` from a `doi.org` link), stands in
      `arXiv:<id>` as the title, decodes an escaped file name
      (`Annif%20DIY…`), and `needs_title` answers `identifier` for a
      title that is only an arXiv id or a number of five digits or more.
- [x] **The agent reads the brief form.** `GET /get?brief=true` keeps
      `BRIEF_META` (what the document is and where it belongs) and drops
      the histories and hashes; `GET /search?brief=true` drops the ranks
      and empty fields. The MCP `get` and `search` ask for it; the UI
      reads the full form. Measured on live documents: `get` with no text
      3,840 -> 880 bytes (doc 5463), 2,297 -> 787 (a new capture); ten
      search hits 4,963 -> 3,703.
- [x] **An MCP server keeps the code it started with.** Howto 5 now says
      to reconnect it after a change. The previous session's server
      predated the region line by hours.
- Seen, not changed: a query about a machine-learning method gets the
      region "Music Emotion and Feature Analysis", because that is where
      the library's machine-learning papers are. Right for this library,
      and a reminder that the region line says where hits live, not what
      the query is about.
- [x] **A region is named by enough of the hits.** A search whose first
      hits were new papers with no facts yet was named by the one old hit
      among them, at a share of 1.0 ("Annif" -> "Music Theory and
      Composition"). `regions_of` now names a region only when at least
      half of the documents asked about, and at least two, have a fact in
      the partition (`REGION_PLACED`, `REGION_PLACED_MIN`). Measured again
      with `scripts/eval_regions.py`: shown 84%, right 92% at 0.5, as
      before (82%, 92%), so the queries of the eval lose nothing.
- [x] **Every rank is left out of a brief hit**, `fts_rare_rank` too,
      which a query's rare terms add.
- [x] **The research is read and the design written**: PLAN, "What a
      document is, and what it is about", stage Z.

## 2026-09-29: stage Z, step 1: the genres and the tab to label them

- [x] **`ontology/genres.yaml`**, beside the lexicon and the sameness
      rule and out of the version string: eight levels (informational,
      instructional, opinion, persuasion, narrative, interactive,
      personal, code) and 27 genres under them, each with one line of
      description. A level is a label of its own, for a document no genre
      of it fits. `ontology.genres()` loads it; `parse_genres` refuses a
      label named twice.
- [x] **A person's genres are the gold sample.** `store.set_genres`
      (`meta.genres: [{genre, p: 1.0}]`, `genres_by: human`,
      `genres_at`), a skip for a document that cannot be placed
      (`genres_skip`), and `store.genre_sample`, a new part of the
      documents package (`documents/genres.py`). The open sample takes
      each source in turn (NAS, Zotero, the extension, the rest) in an
      order fixed by the id's hash, so the NAS dump does not fill it; each
      item says its source, so a library-wide estimate can weigh them
      back.
- [x] **The Review page's "genre" tab**: ten documents a page, each with
      its summary and the opening of its text, the vocabulary as
      checkboxes, save and can't tell, and "labelled" for a correction.
      Screenshotted on a throwaway door with four invented documents: a
      save and a skip, each row saying what was done.
- The labelling itself is the user's: about 150.
- [x] **Subjects beside the genres** (the user, on the tab: "I am
      missing philosophic/political/sociological genres/tags"). They are
      topics, not forms, so a second vocabulary, `ontology/subjects.yaml`:
      four fields (society, arts, technology, everyday) and 24 subjects,
      drafted from the regions the library has and the fields the user
      named. Labelled in the same row as the genres, as `meta.subjects`;
      may be empty, a genre may not. `ontology.Facet` loads both files.
      A test holds that no label is in both, which found `history` (a
      subject; the genre is now `chronicle`).

## 2026-09-29: the night the sends stopped

- **What the user saw**: documents sent at night did not arrive. Every
  write to the store waited, and a sender gave up.
- **The cause**: the nightly `languages` pass (03:30) ended in a loop over
  every entity label without a language, 78,931 of them, one query each.
  `src = ? OR dst = ?` in one condition kept SQLite off both edge indexes
  and scanned the table, 58 ms a label, and the loop committed only at the
  end: 76 minutes of an open write transaction. A write in the door took
  the store's lock and waited on SQLite behind it, so everything queued.
  In the loop since 2026-09-24. It never finished in time before a
  restart, so its labels stayed unplaced and it began again every night.
  `py-spy dump` of the door found it (`_named_by_language` from
  `_languages`).
- [x] **The fix**: `languages_by_entity`, every entity's single language in
      one pass over the edges (0.7 s on the live store, agreeing with the
      per-entity answer on 300 sampled labels; it places 45,212 of the
      78,931), a commit every thousand labels, and `_named_by_language` as
      a union of the two index ranges.
- **And one of my own**: a door started before `genres.yaml`, and later
  `subjects.yaml`, existed read each as an ontology module with no types,
  and stamped what it wrote with `+genres1` or `+subjects1` (883 edges,
  183 review items; twelve hand-outs failed on the minute `subjects.yaml`
  did not parse). The 848 extraction edges among them were retired when
  the version came back and the documents were read again; 35 typing-rule
  edges and the 183 review items still carry the wrong string.
- [x] **Its fix**: `load_dir` composes only a file that says it is a module
      (`module:`, `entity_types` or `relation_types`), so a file the
      running code has no name for is never read as one.

## 2026-09-29: the gold sample, labelled by a model and checked by a person

- [x] **A model labels, a person checks.** The user: "don't you think you
      are capable of tagging the genre properly and only report the
      inconclusive ones to me?" `set_genres` takes `by`, `p` per label and
      a `note`; a model never writes over a person, and a person's save
      keeps the model's labels as `meta.genres_model`. The tab's "to
      check" list shows a model's labels, the least sure first.
- [x] **141 documents labelled by Claude** from the open sample (title,
      source, summary, the opening of the text), 11 set aside: two bot-check
      pages captured as articles (9532, 9529, retired), seven with no text
      to label, two by the user.
- **Measured against the user's 19, labelled blind:** with levels set
      aside, the same genre set on 9, at least one genre in common on 16.
      Six of the ten differences were one habit: "blog" beside "essay" for
      a Medium post, where the user reads it as an essay. Every subject
      Claude gave, the user gave too, but only half of the user's
      subjects were Claude's: the user labels broadly.
- [x] **What it changed.** A label brings its level (`Facet.implied`), as
      the user ticked them. The vocabularies grew by what the 160 found
      missing: genres `article`, `notes`, `coursework`, and a `lyrical`
      level (`lyrics`, `score`); the subject `religion` (genres 2,
      subjects 2). Claude's labels were revised to match: no "blog" beside
      "essay", broader subjects, the new genres where they fit.
- **Blind agreement, measured on 30** (the user's first 19 and 11 more,
      each labelled by Claude without seeing theirs): genres F1 0.59,
      subjects F1 0.64, levels F1 0.69. The 39 of 40 on the "to check"
      list was the pre-ticked boxes speaking; those checks stay training
      data, not a measure. The sharp forms agree (paper 9 of 10, recipe,
      book, lecture, letter, news); the disagreement is web writing, where
      "blog" was in one set only 8 times.
- [x] **Genres 3: "blog" dropped** (a venue, not a form), and essay (argues
      a view), article (explains or reports without arguing) and news
      (current events) described by what the text does. Claude's labels
      carrying "blog" became essay or article by content. Domain rules
      are to key on the sharp genres and the levels, where agreement is
      good.

## 2026-09-29: stage Z, step 2: the local model says what a document is

- [x] **`prax.writing.genres`**: the view a model reads (title, source,
      language, summary, section summaries, the first 2,000 characters),
      one yes/no question a label with P(yes) from the answer token (the
      local adjudicator's way), the document before the question so
      llama-server reuses its prefix, and a list of labels under a grammar.
- [x] **Measured** (`scripts/eval_genres.py`,
      `docs/eval/genres-2026-09-29.md`) against the user's 78 labels, the 30
      blind ones as the measure. Best and cheapest: the list proposes, the
      proposed labels and their levels are asked and calibrated, kept at
      0.3. Genres F1 0.60 blind (Claude, as a second reader, 0.64), subjects
      0.58 (0.64), 0.69 and 0.78 on the checked 48. Calibration takes the
      expected calibration error from 0.07 to 0.01. About 2.5 s a document,
      nine hours for the open 12,400.
- [x] **The step**, `steps.writing.Genres`: named only, and off until
      `steps.genres.model` names a model; the Platt maps and `keep` in
      prax.yaml. It labels the documents nobody has labelled, the newest
      first, stamps the run, and never writes over a person.
- **Side-quest**: Jeff 0.8B (a Jev-format decision model, library doc
      13341) served on the CPU from a scratch environment, run overnight on
      the same 78 through `--decision`. 75 s a document on the summary,
      204 s with the opening, on the CPU.
- [x] **Step 3, the reading side**: the properties dialog shows a
      document's genres and subjects (the labels under a level, a model's
      with its probability, and who gave them; the level names come from
      `GET /genres`, not a copy in the UI), and Browse filters by genre and
      subject (`GET /documents?genre=&subject=`; a level names every
      document labelled under it). The document field waits for a
      retrieval measurement.


## 2026-09-30: what an idle door holds

- **The question** (the user): why does 32 GB run low when nothing else
  is happening? Measured idle: 23.2 GB committed; the door 3.97 GB
  private of which 2.80 GB paged out, the worker 2.48 GB and 1.65 GB.
  Not Python and not SQLite: ONNX Runtime's CPU arena, which grows to the
  largest batch the embedder has run and never gives it back.
- [x] **Measured** on 2,000 chunks of the library, a process a condition,
      three runs each (`scratchpad/bench_arena.py`):

      | arena | chunks/s | query | peak | kept |
      |---|---|---|---|---|
      | keep (ONNX's default, prax's until today) | 24.0 | 3.0 ms | +2.43 GB | +2.29 GB |
      | shrink after every batch | 22.0 | 3.0 ms | +1.39 GB | +0.03 GB |
      | off | 17.6 | 3.0 ms | +1.08 GB | +0.05 GB |

- [x] **`embeddings.arena`** (`PRAX_EMBED_ARENA`), default `shrink`:
      8% of the worker's embedding speed for the 2.3 GB each of the door
      and the worker held. prax's embedder as built, measured again: 22.2
      chunks/s, 3 ms a query, 30 MB kept.
- The low-memory stops of 2026-09-29 were the side-quest on top of this
      (Jeff on the CPU, Kev's training spilling from the card into RAM,
      llama-server's 19 GB mapped model), not prax at rest.

## 2026-09-30: the small labeller, built in

- [x] **`prax.ml.labeller`**: bge-small fine-tuned with one output a genre
      or subject, run with what prax already has (onnxruntime, tokenizers,
      numpy), the arena setting of the embedder. A run is a directory
      under `models/labeller/`; `CURRENT` names the one in use.
- [x] **`scripts/train_labeller.py`** (the `train` extra: PyTorch,
      transformers, onnx): fetches every labelled document from
      `GET /genres/training`, trains on all but the person's blind labels
      (the person's others three times), exports the encoder to ONNX, and
      activates the run only when the export gives what PyTorch gave.
- [x] **`steps.genres.method: small`** labels with it; `llm` stays the
      teacher. Off: prax.yaml does not choose it yet.
- **The first run** (`labeller-20260930T020624`), on the teacher's 1,429
      documents, Claude's 93 and the person's 48: 42 s on the 4090 with
      llama-server paused for it (on the CPU an epoch took over 15
      minutes). Blind F1: genres 0.585, levels 0.742, subjects 0.619,
      groups 0.769, the same to four decimals after the export. Through
      prax on the CPU: 7.1 documents a second, the open 12,400 in about 29
      minutes; 47 MB kept, 128 MB of model.

## 2026-09-30: the library labelled, and domains by what a document is

- **The small labeller over the library**: 12,340 documents labelled,
      34 with no genre over the threshold. A worker started without the
      host's prax.yaml marked 174 documents "no genres model" and so kept
      them out of every later pass; a worker without a model now defers
      its batch (b5f49c9), and the 174 were cleared and labelled.
- **Domain rules** (stage Z, step 5), chosen after the dry run and put in
      the host's prax.yaml: recipe or cooking → kitchen; datasheet,
      schematic, or electronics at p 0.8 → studio and electronics; a
      paper about audio or music → research and studio; audio or music →
      studio; any other paper → research; facts mostly studio → studio.
      The maintain pass gave 1,466 documents a domain set. Left out, as
      the samples showed: `thesis` (it caught grading forms and
      applications), `making` (3 documents, all wrong), facts mostly
      research (the daily briefings). 1,292 open documents stay without
      one. They are mostly computing (manuals, reference, source code) and
      paperwork, for which no module exists yet: the society-module
      decision, widened to computing.
- **The extension's popup** names the way a tab goes before it is sent
      (`lib.route`, 3631912).
- **SuperCollider's documentation** in the NAS backups (535 help files and
      class-library pages, which the labeller calls computing) → studio,
      by a new rule key: `origin`, a piece of the path the document had
      where it came from (35f0daf). The first run of that rule went to a
      door that had not yet restarted onto the new code. It ignored the
      key it did not know, so the rule matched everything, and 998
      documents (the open ones and retired ones) got studio for a few
      minutes, and the worker re-read a few of them against it. The sets
      were put back through the door (`PUT /doc/{id}/domains` with
      `by: rule`), and an unknown key is now refused, not ignored.
- **Assigning a set re-reads**: a document's extraction is held to its
      subset's version, so the 1,466 new sets put 1,429 documents in the
      extract backlog (the local model, about six a minute). That is the
      design, and it was not said before the rules ran.
- **Left without a domain: 757**. 390 computing (manuals, reference,
      programming books, Linux), 178 paperwork (invoices, contracts,
      forms, applications; 112 already suspected personal), 117 with no
      labels (tiny files, briefings), about 70 society and economics.

## 2026-09-30: computing and society, and the domains in the UI

- **`computing` v1** on top of studio: a program is a kind of device and a
      library a kind of component, so a manual `describes` a program and
      studio's features, standards and versions hold for software; its
      own words are the language code is written in, what it depends on
      and runs on, and the functions and commands it provides. Rules in
      the host's prax.yaml (`subject: computing`, `subject: ai`, a
      computing paper to research and computing) gave 406 documents the
      domain. Left out after the dry run: `information` and `interaction`
      (forms, glossaries, an accounting file).
- **`society` v1**, for later: theories, polities, laws and essays, and
      who argues for or against what. No rule assigns it yet.
- **Without a domain: 350**, the paperwork, the unlabelled small files
      and the argued texts society will take.
- **The domains in the UI**: a search hit, a Browse row and the document
      page show a document's domains as links that filter by them; the
      graph's entity view has the module choice the overview had, and it
      holds for every expansion (`GET /traverse?domain=`, the MCP
      `traverse` too). Both new modules changed the whole ontology's
      version, so the documents without a set are read again, like the
      406.

## 2026-09-30: the engineering pass (the night after stage Z)

Four read-only surveys first: modularity, duplication, complexity, and
practice with the documents. Each claim that led to a change was
checked in the code before the change. The pass went in stages. Each
stage was committed with the full CI green: ruff, ruff format, mypy for
Windows and for Linux, and the 1,043 tests. The door and the worker kept the code
they started with while the re-extraction ran.

**Defects the surveys found.**

- `genres.ask` and the resolution judge called llama-server themselves
      and caught every error. A server that was down read as "no
      answer", so the genres step's defer path could not run.
      `models.top_logprobs` asks through `post_json`, which raises
      `ServerNotReady`. The judge keeps "no answer" on purpose: the pair
      is left and asked again.
- Which documents belong to a domain was written ten times in three
      shapes. `store.holds_domain` and `store.domain_clause` say it once.
      Each caller names its modules and whether an unset document counts,
      so each kept what it did. An empty set is now no set everywhere.
- A reading's mode was an environment variable set for the whole
      process, and the figure fetch hook a module global. Two readings in
      two threads saw each other's. Both are context variables now
      (`config.overriding`, `figures.fetching`).
- A worker without a step's model took the step's batch and marked every
      document tried ("no titles model"), which kept them out of later
      passes. `ModelStep.available` stops it asking. The genres step is
      available when `genres_on()`.
- The review rule for "references" was unreachable.
- The archive path was built by hand in four places. `store.archive_path`
      is the one place now.
- A broken prax.yaml silently turned the figures queue off.
- Five UI deletes ignored failure. They go through `send()` in core.js.
- The invariant test had missed three tables. It reads the migrations
      now.

**Dead code.** `capture/pipeline.py` held an in-process runner,
`process_captures` and its passes, about 500 lines. Nothing but its
tests called it. Nine unused names went too. `scripts/work.py` is `prax work`
now; it had drifted. `scripts/backfill.py`, a stage 1 stub, is retired.

**Structure.**

- `api/jobs.py` (994 lines, eight routers) is `work`, `curation`,
      `importing`, `admin`, `wall` and `passes`. The five job starters
      share `passes.run_as_job`.
- `host/up.py` (1,439 lines) is `roles`, `process` and `up`. A caller
      still writes `up.<name>`.
- The lease table is `steps.leases`. `steps.base` imported `work` at its
      top and `work` imported the steps, a cycle.
- 36 store imports inside functions reached down, not up; they are at
      the top now, and a test holds that line. The store's own arithmetic
      moved down to it (`in_english_text`, `jaccard`), and a name's
      matching key is `prax.text.names`.

**Reuse.** One function now stands for several copies:

- `_put_meta` for twenty copies of the meta write.
- `models.ClaudeRuntime` for ask's own Claude call.
- `Door.from_env` for the MCP proxy and three scripts.
- `prax.evaluation.prf` for two F1 scorers.
- One `client` fixture in conftest for fifteen test files.
- `showError` for sixteen views of the UI.

**Complexity.** Seven functions over 25 are split. Each was checked
against a golden run of its old self:

| function | before | how it was compared |
|---|---|---|
| `text.chunking.chunk` | 35 | 2,836 real texts of the library, every chunk's kind, range, page, heading, data and times |
| `graph.review.decide_unmapped` | 31 | 105,300 generated items |
| `graph.resolution.plan` | 31 | a generated library of 122 entities and 300 pairs, all settings |
| `host.roles.roles` | 27 | 29 door and worker settings, and the refusals |
| `graph.ontology.compose` | 26 | all 511 combinations of the nine modules |
| `capture.routes.routes_for` | 26 → 19 | 1,200 real documents on a read-only connection |
| `graph.review.decide` | 25 | 162,672 generated typed items |

The golden run for `decide` first differed on 1,056 items. The cause was
the generator: `DROP` is a set, so its order moved with the hash seed.
Sorted and seeded, the two runs matched.

**Tooling.** Ruff selects `UP`, `B`, `SIM`, `C4` and a complexity
ceiling of 21, the package's worst after the pass (`video.parse`). The
14 new findings are fixed. Strict mypy covers a first batch by its
flags: `prax.text`, `writing`, `wall`, `ml`, `config`, `worker`,
`host.schedule` and `steps.base`.

**Documents.** The module map of `docs/architecture.md` had drifted by a
pass. It is rewritten, and `tests/test_docs_names.py` resolves every
`prax.…` and `store.…` name in it.

Left for later, in `docs/PLAN.md`:

- Splitting `store.retrieval`. Its tests set module flags, and a package
      would cut them off from the code that reads them.
- The typed shapes for hits, meta and jobs.
- The next strict batch.

## 2026-09-30: an open mode for ask (stage AB)

`ask` answered from the passages alone: its prompt pins the model to
them, under 250 words. The open mode keeps the search and the surfing
and answers with a second prompt, `ask.OPEN_SYSTEM`. The model cites
the passages where it uses them. Beyond them it may explain, derive,
compare and write code, and it says where the library is silent. The
budget is 3,000 tokens (`ask.OPEN_TOKENS`). An open answer comes even
when the search finds nothing.

- `mode: grounded | open` on `POST /ask` (400 for anything else), on
      `ask.ask` and `surf.run`, in the result, and in the MCP `ask` tool
      with `answer=True`. The Ask view has an "open" box, and an open
      answer is labelled so.
- A page an open answer is kept on starts that section with "An open
      answer" and carries `meta.page.open` (`store.mark_open_answer`).
      `ask.evidence` drops such pages from what the one-shot gather and
      the surfer's search take as passages. A plain search still finds
      them.
- The reference is `docs/ask.md`, "Grounded and open".

## 2026-09-30: two extension fixes (stage AC)

**A tab that shows a PDF.** The extension recognised a PDF by its
address (`.pdf`) or by the viewer's markup in the first 20,000
characters. An arXiv `/pdf/…` address has no `.pdf`, and pdf.js puts its
markup further down, so such tabs were snapshotted as the viewer: four
documents held only its frame. `probeShown` asks the page: its
`document.contentType`, and a frame, embed or object over half the page
whose address or type says PDF. `lib.route` gives `pdf` (the tab is
fetched as a PDF, never snapshotted) and `pdf-frame` (the frame's PDF is
fetched, IEEE's `stamp.jsp`).

**A GitHub repository.** A repository's page went as a snapshot. It now
goes to `POST /import/github`, where the door asks GitHub for the
repository and its README (`github.one`, with `sources.github.token`
when there is one). The document has the starred import's shape
(`feed.text_body`, `meta.github.key`), so a later star of the same
repository is already held. A newer push retires the older document
into the new one (`store.documents_of_key`). The page is snapshotted
only when the door cannot reach GitHub.

## 2026-09-30: closing Z (stage AA), the first part

**What a narrower subset reads.** The domain rules of the morning put
2,027 documents under a domain set, and their re-extraction ran through
the day. For the documents with an earlier reading, facts a document,
the earlier reading (retired since 01:00) against the new one:

| domain set | documents | facts a document | kinds of target |
|---|---|---|---|
| studio | 1,440 | 5.5 → 6.7 | 2.7 → 2.4 |
| computing | 370 | 9.4 → 11.7 | 3.5 → 4.0 |
| studio, electronics | 109 | 8.9 → 13.1 | 4.0 → 4.7 |
| research, studio | 63 | 11.8 → 14.4 | 4.4 → 5.4 |
| research | 45 | 13.0 → 15.0 | 4.6 → 5.2 |

Every set reads more. The retired side counts whatever was retired since
01:00, a few merges among it, and the computing side includes the 420
facts the review pass settled (below).

**computing v2.** The first reads of 389 documents queued 1,853 items.
A programming language is now a `standard` (a document names it, a
program conforms to it), and a symbol a `component` (a reference page
describes it). Two review rules came with it: `covers` a program,
library or symbol is `describes` (the rule asked for the type names
`device` and `component` and missed their subtypes), and a symbol that
"provides" its library is flipped. The review pass then settled 451 of
the computing documents' open items (1,826 to 1,375) and linked 420
facts. The 406 documents are read again against v2.

**The store's write lock.** The vocabulary pass failed on one entity
every cycle (179263, its name both as the English preferred label and
without a language). The failed statement's transaction stayed open, so
the door's other writers waited 200 s each from 10:08. Both are fixed:
`label_in_language` does not move a label onto one it already has, and
a store call that fails takes back its own writes and lets the lock go.

**Left of AA:** the labeller's corrections (the Review page's genre tab,
then `scripts/train_labeller.py`), and two decisions of the user: rules
for `society`, and step 4 (`meta.regions`).

## 2026-09-30: research, three questions (stage AF)

Three documents, measured on a copy of the store. Nothing was built.

- `docs/research-condensed-knowledge.md`. The 52,220 tables are parsed
      but untyped; about 180 spec tables and at least 619 result tables
      are ready to become values with units. About 24,000 definition
      sentences, 456 documents with formal definitions, 645 instructional
      documents with numbered steps. First: values in table `data` and a
      units-aware compare tool.
- `docs/research-database-layout.md`. The schema fits. The slow queries
      are indexes that miss (a NOCASE label lookup, `canonical_id`) and
      `meta` read as JSON row by row (a search scoped to a domain spends
      550 ms in `json_each`). Five small migrations and two code changes
      are proposed. Found on the way: a DjVu refusal loop of 2026-09-28
      recorded 897 parse attempts on three books, and nothing caps the
      history.
- `docs/research-code-music-sound.md`. Code first (13,869 code chunks
      without a language, 830 SuperCollider files chunked as text), a
      music module second, sound only if the NAS holds sample libraries.

## 2026-09-30: the infrastructure (stage AG)

Every change was measured on a copy of the store before and after, warm,
the best of three, with the answers compared. The copy's timings ran
without the vector files, so the search answers compared are the keyword
and document-field legs.

| call | before | after | how |
|---|---|---|---|
| `traverse_map("reverb")` | 39.7 ms | 1.6 ms | migration 0031: `entity_labels(label COLLATE NOCASE)`, `entities(canonical_id)` |
| `senses("apple")` | 29.5 ms | 0.2 ms | the same |
| `search("feedback delay network")` | 645 ms | 183 ms | `domain_clause` reads the distinct domain sets through `idx_documents_domains` |
| `list_documents(domain="kitchen")` | 239 ms | 9.6 ms | the same |
| `list_documents()` | 20 ms | 1.0 ms | migration 0032: `idx_documents_live` on `(added_at, id)` |
| `unlabelled_names("de")` | 705 ms | 295 ms | 0032's `idx_documents_lang`; the documents first |
| `foreign_names` | 955 ms | 159 ms | a document's language read once, the entities of other languages first |
| `select_for_extraction`, after a bump | 1,586 ms | 83 ms | `text_len` first, the chunk sum lazily until the limit |
| `pending_embeddings`, nothing pending | 164 ms | 35 ms | migration 0033; a mark of the highest chunk id and the model's rows |
| the reconcile at start | 158 ms | 0.3 ms | 0033: `chunk_embeddings(model, embedded_at)` |

- `document_domains` was not built. The distinct sets through the
  existing index give 183 ms against the table's measured 107, with no
  second copy to keep in step.
- `idx_documents_live` keyed by id made the default listing worse (138
  ms): the planner took it and sorted every live row. Keyed by the
  listing's order it answers in 1 ms.
- `select_for_extraction` keeps its exact bar. On `text_len` alone, 66
  documents with 300 characters of text but less in their chunks would
  have been handed out.
- A refusal that may pass next time (the OCR budget, a failed fetch)
  counts as an attempt after five in a row under one stamp
  (`queue.REFUSALS`). The loops the histories hold: three DjVu books on
  2026-09-28 (the refusal it repeated no longer exists) and `pymupdf`
  empties on 14 documents from 09-14 to 09-24.
- `store.bounded_histories` is written and tested, not wired. It keeps
  the last N entries, the newest of each kind, every text hash and each
  extractor's last five. A dry run on the copy: 14 documents, about
  4,100 entries, 0.6 MB of 53 MB, the same for N from 10 to 50.
- A briefing page keeps its part's length and hash instead of its text
  (309 KB in `meta` on 2026-09-28).
- The document index's delta never reached `DELTA_MERGE_AT` in a library
  of 13,000 documents. A merge is also due at the main file's size.
- The chunk index held 484,000 removed slots beside 1.28 M vectors:
  usearch keeps a removed key's slot. `compact_vectors` rebuilds the
  graph past a tenth; on the copy 1.61 GB became 1.17 GB in 231 s, recall
  at ten against exact search 0.963 -> 0.968. Nothing called it before;
  `prax maintain vectors` does, on request. It no longer holds the
  store's lock for minutes, and a build lock stops a merge from being
  undone by a compaction's swap.
- `journal_size_limit` is 16 MB (`door.sqlite_journal_mb`). `PRAGMA
  optimize` was measured and left out: its statistics moved three plans
  off their indexes (`list_documents` 1 -> 75 ms, `pending_embeddings`
  32 -> 89, `select_for_extraction` 75 -> 89).

The same evening, on the live store, after a backup to `I:\prax-backup`
(3,004 MB, `quick_check` ok):

- The histories held to 20 (`HISTORY_KEEP`, the user's cap): the
  `histories` pass changed 14 documents; the looping books went from
  about 900 parse entries to 25.
- `prax maintain vectors`: the chunk index 1.58 GB -> 1.17 GB in 255 s,
  the document index merged (8.8 MB and an 11.5 MB delta -> 12.2 MB).
  The same five hits for "feedback delay network" before and after.
- `VACUUM` with the door stopped, run by the user at 4 KB pages: 3.00 GB
  -> 2.80 GB, no free pages. The write-ahead log stood at 16 MB before it,
  the new limit.
- Deleted by the user's word: the `multilingual-e5-small` index files
  (1.04 GB; they still covered 1.10 M of 1.28 M chunks, so a switch back
  or the chunk-side rematch now means a full re-embed, about an hour with
  the card free) and `prax-before-heal.db` of 2026-09-12 (1.7 GB).
- The graph's threshold in CLAUDE.md is a walk time, not a count of
  entities: 50 ms for one hop, 500 for two, from the ten most connected
  entities, measured by `prax heal` (`slow-graph-walks`). The live store,
  read-only: one hop 3–41 ms, two hops 34–276 ms (a two-hop walk from a
  paper of 236 edges, the slowest). The 1.6–7 ms reported earlier the
  same day were one-hop walks from `reverb` and `SuperCollider`.

## 2026-10-01: the labeller again, `society` by rule, packs

- **The labeller retrained** (`labeller-20261001T015042`) on the
  person's 194 labels, 41 of them checks of the labeller's own on the
  documents a domain rule placed (8 corrected), Claude's 93 and the
  teacher's 1,429. A person's label counts as blind when nothing was
  pre-ticked, so the test set grew from 30 to 68. On those 68: genres F1
  0.395 -> 0.439, levels 0.564 -> 0.636, subjects 0.618 -> 0.633, groups
  0.784 -> 0.748. It is the current run. The teacher's labels moved from
  the session's scratch folder to `models/labeller/teacher-20260930.jsonl`.
- **The genre tab's "decided a domain"**: the model's labels on the
  documents a domain rule placed by them (about 2,800), least sure first.
- **`society` by rule** (the user's options B and C): an argued subject
  (philosophy, politics, sociology, economics, law) in an opinion genre
  goes to `society`, in a paper to `research` and `society`, after the
  Zotero rule. The subject group `society` was not used: it holds
  education and psychology, 5,000 documents. A rule's `genre` and
  `subject` take a list; `POST /domains/assign` applies the rules again
  to named documents. 264 documents moved in the dry run; five in
  `studio` (music theory, a Goethe reading, a press page) were left
  there, and 259 moved: 241 to `society`, 18 to `research` and
  `society`.
- **A mistake on the way.** The edit to `prax.yaml` failed and the door
  restarted with the old rules, which were then applied to the 259: 47
  moved from `research` to other sets their newer labels matched. Applied
  again with the right rules minutes later, they all ended where the dry
  run said. None was extracted in between. The rule for next time: read
  the rules the door loaded back before applying them.
- **Step 4 dropped** (`meta.regions`): nothing reads it, its ids move
  with each partition, and `store.regions_of` answers on read.
- **Packs** (`docs/packs.md`, stage AD0): every domain is a pack, its
  knowledge turned on by the library and its capability by the host.

## 2026-10-01, the night: the relabel, the guard, packs (stage AD0, step 1)

- **The library relabelled** by the new labeller run: 12,295 documents
  (four kept their old labels, tried without a genre kept). A newer run
  now makes an older run's labels stale, and the step relabels them; a
  person's labels and Claude's are never taken. `genres` is a watched
  step: until tonight the supervised worker never ran it, so a new
  document got no genre and the genre rules never placed it.
- **What the rules do after it**, read-only under the current rules: 24
  documents without a set would get one tomorrow night, five of them
  personal. The rules now never place a document marked personal or
  suspected. 25 documents in `computing` would move to a `studio` set,
  5 of them audio (applied by id: sample-rate conversion, the élastique
  SDK, Vorbis, Kontakt, a guide to audio and video material); the other
  20 were drivers and network books the labeller calls electronics, left
  where they are.
- **The backup keeps the trained labeller** (`models/labeller`, 260 MB
  once): trained here, it cannot be fetched again.
- **Private data and git.** Every commit was searched for what the 402
  personal documents hold: none of their 42 IBANs, 126 street addresses,
  350 postcodes or 251 phone numbers is in git. One private folder path
  from prax.yaml had been copied into a UI test on 2026-09-28; it is an
  invented one now. `scripts/check_private.py` is a pre-commit hook that
  refuses a commit holding one of those values, naming file, line and
  kind but never the value; its first run caught a placeholder address
  that also stands in the owner's forms. The history stays as it is (the
  user).
- **Packs, step 1** (`docs/packs.md`). `prax.packs` holds the manifests;
  research, craft (kitchen, workshop), studio (electronics), computing and
  society are packs, each with its module files, its sameness cases and
  the rules it suggests. The registries read the packs: the steps and
  their homes, the readings, the chunk kinds and the aside kinds, the
  extractors and the surfer's tools of the packs a host names in
  `packs:`, and the config's sections. A golden run with the hash seed
  fixed found the composed ontology, the version string, all 36 subset
  versions, the sameness rule and the lexicon identical before and after.
  The lexicon stays whole in the core: its `by_type` is ordered, and
  splitting it by pack would type 361 of 201,055 names differently.

## 2026-10-01: the maths pack, step 1 (stage AD)

- **The design** (`docs/symbolic-maths.md`, the user's choice): a small
  SymPy calculator a model calls (read, same, simplify, substitute,
  solve, diff, integrate, series, limit, evaluate, code), with `same` as
  the check of a model's steps. The user's example is an ADAA version of
  a shaper: `integrate` gives the antiderivative, `same` checks it, `code`
  gives the line in C.
- **Its own environment.** RapidOCR's `omegaconf` pins ANTLR 4.9, and
  SymPy's LaTeX parser needs 4.11, so the calculator is
  `src/prax/packs/maths/runtime.py`, run by the interpreter of
  `%LOCALAPPDATA%\prax\maths-venv` (SymPy 1.14, ANTLR 4.11) as a
  subprocess: JSON in, JSON out, a time limit, nothing of prax imported.
- **A hole the tests found.** SymPy's expression parser is `eval` even
  with only its standard transformations: a test's injected
  `__import__('os').system(...)` ran. Plain notation now passes a gate
  first (names, numbers, operators, parentheses; no attribute access, no
  dunder, no quotes) and is evaluated with no builtins.
- **The rules, measured** on the 2,000-formula sample: 38% accepted, 20%
  refused as stopped short or unread, 42% no parse. Signs of a
  misreading among the answers went from 45% to 0.4%. Hand-checked
  against the LaTeX in three sets of 50: 21, then 36, then 43 faithful
  (86%). About a third of the library's display formulas are read
  faithfully, and nearly all of the rest are said to be unread.
- **Step 2, the tool** (same day). The maths pack's manifest names its
  tool and the line the surfer's prompt shows for it (`tool_help`, a new
  manifest field). `POST /maths` resolves `chunk:<id>` through the wall
  (a hidden document's chunk answers as an absent one, `tests/test_wall.py`),
  the MCP tool `maths` makes one call to it, and the surfer's `maths:`
  action takes a passage [n] as the formula it holds. `config.host_packs()`
  (`packs:`, `PRAX_PACKS`) is the one place a host's packs are read.
- **The tool caught my own mistake.** The second antiderivative of tanh
  needs the dilogarithm, which SymPy does not find. My first proposal had
  a wrong sign; `same` said "not the same", and the corrected one was the
  same at 40 points. `code` says C has no polylogarithm.
- **A reading fixed on the way:** `I_s(e^{v/V_T} - 1)` was a function
  applied to a sum. A subscripted name before a sum holding a plain number
  is now a factor; `x(n - 1)`, `h_r(t - nT)` and `X_c(j\Omega)` stay
  functions.

## 2026-10-01, the evening: the maths tool measured, its faults, stuck readings (stage AD, step 3)

- **The first run** of the 20 maths questions, four ways each
  (`scripts/eval_maths.py`): right, partly, wrong were 10/3/7 with the
  tool and 11/3/6 without in grounded mode, 17/2/1 and 17/1/2 in open
  mode. The tool was called in 7 of 40 asks and made no difference.
- **Four faults behind it**, fixed in d3f00d9: the answering call never
  saw the tool's results (now the last six go into the bundle's note,
  `surf.answer_note`); plain notation refused a decimal point; it knew
  every SymPy name, so `beta` was a function (now `PLAIN_NAMES` only);
  a passage could not stand inside a formula (now `[n]` inside one is
  its right side). Added `chain`, `expand`, `factor`, `together`,
  `apart`, SI-prefixed values, and a prompt that asks for checks. The
  partial rerun showed two more, fixed in dee47a1: `with` after any
  operation, and `e**x` as Euler's number.
- **The second run**: 11/5/4 with the tool and 7/5/8 without in grounded
  mode, 14/2/4 and 15/3/2 in open mode; 11 of 40 asks called the tool.
  The "off" rows ran unchanged code and moved by four, so only rows of
  one run compare. The case-by-case reading, and the next fixes from
  15 failed calls, are in `docs/symbolic-maths.md` ("How it is
  measured").
- **Readings stuck for two days.** A marker reading whose server was not
  running leased its whole document, and a reading without a model goes
  first, so two vision-pages readings of the same paper never left the
  queue. Now only the reading waits (1c7fec1, `leases.defer_reading`).
  Marker itself still waits for a hand: the planner of stage AI is what
  starts it (`docs/PLAN.md`), in place of `swap: auto`, which would have
  taken the card in the middle of an eval.

## 2026-10-02: the maths tool measured twice (stage AD, step 3)

- **The third round** (6bba583): `$…$`, values without `with`, `==` in
  `solve`, `simplify a == b` as `same`, `Li2`, SI prefixes inside a
  formula, a readable parse error, and `I`/`E` as symbols in plain
  notation (a solve for a current came back imaginary).
- **The double run**, every way twice and scored in one sitting: with the
  tool, grounded mode had 11 right in both runs, without it 7 and 9;
  wrong answers 4 and 3 against 9 and 8. Open mode was 12 and 13 with,
  11 and 13 without. Two runs of one way disagree on 3 to 7 of 20
  questions, so the grounded gain is past the spread and the open one is
  not there. Table and reading: `docs/symbolic-maths.md`.
- **The fourth round** from the 30 failed calls of 87: the variable after
  `for`, SymPy's argument order for `series`, the point as `x=0`, a call
  as the whole step, words after the values, `W` as Lambert W, and an
  unknown function named with the list of known ones.
- **The eval waits for a load.** Both first attempts at the double run
  were stopped for memory while llama-server loaded after its idle time;
  `scripts/eval_maths.py` now waits for the model instead of recording a
  failure for every ask (d56d69a).
- **The rule for numbers, measured** (the same day, 4aa6602 and the
  double run e1/e2): rows moved by 0 to 2 right answers against the first
  double run, inside the spread. The diode current was right for the
  first time (13.23 mA from `evaluate`, in one run's two tool-on ways);
  "(not checked)" appeared once in 160 answers. Grounded mode refused
  the RC time constant while holding the tool's result, in every run:
  the grounded prompt now says a computed result in the note answers a
  question that is a calculation. "for" names a variable only when one
  stands alone after it. Details: `docs/symbolic-maths.md`.

## 2026-10-02: AD2, step 1 — the maths step as a JSON object

- **Why.** Over four rounds every fix to the free-text `maths:` line was
  followed by a new way of writing it: 21 to 30 of every 85 calls failed
  on syntax in the double runs.
- **What.** A pack may give a tool its own GBNF (`Pack.tool_grammar`,
  `packs.tool_grammar`), and the surf's step grammar uses it in place of
  a line of text. The maths step is one JSON object (`op`, `formula`,
  `other`, `then`, `var`, `at`, `values`, `language`), in plain notation
  and without a backslash, so a JSON string cannot turn `\frac` into a
  form feed. A passage [n] is the calculator's plain reading of its
  formula (`read` answers `plain` now; `V_{T}` reads back as `V_T`).
  `tool.surf_json` builds the request; text still goes to `parse_step`.
- **Checked live.** llama-server took the grammar. Given the help and the
  diode task, the 35B wrote `{"op": "evaluate", "formula":
  "I_s*(exp(v/V_T)-1)", "values": {"I_s": "2.52 nA", ...}}`, and the
  tool answered 0.0132319712753 A — 13.23 mA, the right value.
- **A clash caught on the way.** The runtime already had a `BRACED`
  pattern for its LaTeX rules; the new one is `SUBSCRIPT_BRACED`.

## 2026-10-02: AD2, step 2 — the check after the answer

- **What.** A pack may name an `answer_check`, which the surf runs on an
  answer written with tools on. The maths pack's checks every display
  equation's links with `same` (one calculator process for all of them,
  the runtime's new `{"batch": …}`) and every number of three significant
  digits against the question, the passages and the tool's results. It
  marks what fails in the answer's text and returns `checks`.
- **The first try was wrong, and the answers said so.** Run over the 160
  answers of the e1/e2 double run, judging every link marked 64 links
  "does not hold". Most were relations true given other facts:
  `ω_c/(2Q) = Δω/2` needs `Q = ω_c/Δω`, the diode equations relate `a`
  and `b`, `10 kΩ = 10,000 Ω` is a unit. A link is now judged only with
  one free symbol at most, and units, word labels, lists of definitions
  and primes are left out. Result: 17 marks, all real. Fifteen are the
  quoted `e^{xx}` typo; two are a diode answer's own arithmetic
  (`0.4/0.02585 = 15.478`, true 15.474, and the current from it).
- **Not yet measured:** the numbers half, which needs the passages the
  eval files do not keep; machine scoring (step 3) will keep them.

## 2026-10-02: AD2, step 3 — scoring by machine

- **What.** `check:` on 10 of the 20 maths questions (six yes or no, four
  values with a tolerance and a `near` for partly right) and
  `scripts/score_maths.py`, which scores an answers file and, with
  `--hand`, compares itself with `tests/eval/maths-hand-scores.yaml`
  (the hand scores of the d1, d2, e1 and e2 runs, now kept).
- **Agreement, measured.** 130 of 160 at first. Two causes were
  systematic: a grounded answer that declines in its opening sentence but
  quotes the tool's number (hand: wrong, machine: right), and the
  equation (3) question, which the hand marks right only when the answer
  names the `V_T` slip. With a refusal pattern and `also: "V_T"`: 141 of
  160 (88%). The rest are partial credit for thin reasoning.
- `scripts/eval_maths.py` keeps the door's answer checks (`checks`).

## 2026-10-02: AD2, step 4 — the library's formulas checked

- **What.** The maths pack's watched step `equations`: formula chunks
  not checked at `CHECK_VERSION` go to a worker with the maths
  environment, which reads each and judges its chain's links in one
  calculator process; the door keeps `data.check`, and a broken link
  shows beside the passage in the equations-nearby line. Store:
  `formulas_to_check`, `set_formula_checks`.
- **Measured on the live store, read-only.** 17,431 formulas in 244
  documents; about 20 minutes of a worker for all of them. The first
  sample of 600 marked 4 broken, the second of 1,500 marked 14, and
  nearly all were the judge's errors: a true integral identity (22/7 −
  π), a division rounded to the digits written, mixed numbers, `\div`,
  ratios, `:=`, `\quad`, and equations in one unknown. Each became a rule
  of `check.verdict`, which the answer check now shares. With them:
  11 links judged in 2,100 formulas, one broken, and that one real (a
  tuning paper's `9/8 · 256/243`). The answers of e1/e2 still mark the
  quoted `e^{xx}` typo 15 times and the diode answer's division once.
- **Two slips of mine on the way.** The new store read went in between
  `equations_near`'s decorator and its definition (the wall's guard moved
  onto the wrong function; caught by a failing call, restored), and an
  empty skip list wrote `id NOT IN (NULL)`, which matches nothing. The
  step was first named `formula-check`; a step name with a hyphen breaks
  the command line's `--no-<step>` flag, so it is `equations`.
