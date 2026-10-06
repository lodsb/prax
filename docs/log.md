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

## 2026-10-02: AD2, step 5 — an equation by its number

- **What.** `surf.named_equations`: "equation (5)", "eq. (14)",
  "equations (3) and (4)" in a question bring those formula chunks in
  after the first search, as step 0's reads, before the model's turn.
- **One document for all the numbers.** Taking each number from the first
  of the search's documents that had one brought Helmholtz's (1) into a
  Dattorro question and Strogatz's (4) into an RMS one. The rule now
  takes the earliest document with the most of the numbers named, out of
  the first eight. Dry run on the live store, read-only: the right paper
  for all 7 of the 8 equation-naming questions that needed one; the
  resonator question starts with Dattorro's (5).
- **Not measured in the eval yet.** The five runs going now ran on the
  door before steps 4 and 5; they measure steps 1 and 2.

## 2026-10-02: the five runs after AD2 steps 1 and 2

- **Measured by machine** (`scripts/score_maths.py`, the 10 questions it
  scores; runs f1–f5 against d1, d2, e1, e2, all scored by the current
  scorer). Mean right out of 10, range in brackets:
  tools on grounded 6.75 (6–7) before, 6.40 (5–8) after; on open 8.50
  (8–9), 8.40 (7–10); off grounded 5.00 (3–7), 6.00 (5–7); off open 7.50
  (7–8), 6.80 (6–7). No change past the spread.
- **The calls changed, the answers did not.** The tool was used in 82 of
  200 tool-on asks (41%), all 176 calls as JSON, 30 of them failed (17%,
  from 21–35%): passages that would not read (8), a string cut off before
  its quote (7), the rest scattered. The RC question is still declined in
  grounded mode with the tool's 0.01 s in the note: cf71a69 did not take.
- **The scorer, out of sample.** f1's 40 machine-scored answers scored by
  hand after the scorer was tuned: 36 of 40 agree (90%; in sample 88%).
  Kept in `tests/eval/maths-hand-scores.yaml`.
- **A regression the hand pass found.** cf71a69 put "a result in the note
  was computed by a calculator" into the grounded prompt with tools off
  too, and a tools-off answer wrote "the note states the result is 13.32
  mA, which was computed by a calculator": a note it invented. Goes to the
  review round.
- An earlier comparison in this session used scores from before the
  scorer stripped the check's marks and refused declines; it showed a
  drop that is not there.

## 2026-10-02: the review round of AD2

An independent review of 6bba583..HEAD (a fresh agent, report only)
found ten defects and called the text heuristics duct tape. Worked off in
four commits:

- **Results and prompt** (f45f3d4): the tools' results are a section of
  the bundle; the pack's words about them are in the prompt only when
  there are results. A sentence in every prompt had a tools-off answer
  invent a calculator note.
- **Judging and checks** (bad4bbb): `runtime.judge` decides a link on
  the parsed expressions (a solving step was marked "does not hold"; a
  name pattern let `x^2 - 1` pass unjudged); the answer is left as
  written and the checks are annotations with spans, shown under the
  answer; numbers inside maths, code and links are left alone; the
  equations step stamps a check with its LaTeX's sha, survives a failed
  batch, and takes 50 at a time; the scorer no longer counts a declining
  "no" or a value the question gave. Measured: 3,600 library formulas,
  26 links judged, 3 broken and false as printed; seven runs' answers,
  no false mark.
- **Passages as LaTeX, the step held to its shape** (this commit): a
  library formula goes to the runtime as LaTeX beside a placeholder,
  never as plain notation read back (`y[n]`, `H(z)` and `V_{T,1}` had
  failed or misread); a function's name is the function only where it is
  called (a variable `W` had failed as LambertW); the free-text maths
  line and its ~15 patterns are gone, and the JSON step is checked key by
  key (a list for `values` had crashed the ask).
- **Process.** Tests that no tracked source file holds a control
  character (four came in through shell edits) and that no pack module
  assigns a name twice (twice a new table replaced an existing one of
  the same name: `BRACED`, then `FUNCTIONS`).

## 2026-10-02: the five runs after the review round (g1–g5)

- **Machine-scored, mean right of 10** (before AD2 / f, steps 1–2 / g,
  everything): tools on grounded 6.75 / 6.40 / 7.00; on open 8.50 / 8.40
  / 8.60; off grounded 5.00 / 6.00 / 7.00; off open 7.50 / 6.80 / 7.80.
  Grounded mode gains with and without tools: most of it is the equation
  by number (step 5), which does not depend on the tools.
- **The targeted questions moved.** The resonator, never right before,
  4 of 5 with tools in grounded mode (Dattorro's (5) is now read first).
  The RC time constant, declined in every run before, 4 of 5 (the
  results' own prompt). The diode current: the model computes by hand
  in 4 of 5, and the answer check now marks that number "not checked".
- **Two faults found in the scoring of them.** A step is capped at 160
  tokens, and 21 of 291 maths steps were cut off mid-string: now 400
  (`ask.STEP_TOKENS`; the grammar bounds the step anyway; a second,
  unused `surf.STEP_TOKENS` deleted). And the scorer's exclusion of the
  question's numbers dropped "19.2" from answers to "where does 19.2
  come from"; a given number now counts when it is the target. The
  scorer agrees with the hand on 177 of 200.
- **The library's 24 broken formulas, read** (CHECK_VERSION 2, 11,650
  of 17,431 checked by then): about half are extraction faults worth a
  reader's look (`2^{22} = 2^4` for `2^{2^2}`, `2^*2`, continued
  fractions cut to `\frac{1}{1+}`, `8 = \{3\}`), plus Moog's `e^{xx}` and
  a ratio wrong at its fourth digit. Five were the judge's: a decimal
  without its 0 (`.3`), a slash before a juxtaposed factor (`1/5 (…)`),
  an absolute value true on one side of zero (Dattorro's `|1 - k|`), a
  rounded constant (`e^{1.9x} = 6.7^x`), and a truncated continued
  fraction written with `=` (left marked: it is not an equality).
  The first four are rules of `judge` now, which is split into what is
  refused before reading, what is refused after, and the arithmetic;
  CHECK_VERSION 3 has the worker check every formula again.

## 2026-10-02, the afternoon: what was never extracted

- **Formulas.** Only marker reads display maths: every formula chunk is
  in a marker-read paper (243 of 347). The 8,611 papers pymupdf4llm read
  have none. About 263 of them show display maths (equation numbers left
  alone on a line, "Eq. (n)" in the prose), 77 strongly. Queued for
  marker with the 14 documents whose formulas the `equations` step found
  broken: 62 papers up to 80 pages first (1,604 pages, about a minute a
  paper), the 29 books (about 12,000 pages) for the night.
- **The graph.** 1,052 documents with text were never extracted: the
  watching worker extracts new captures only, and the backlog goes to a
  nightly pass of 100 a step, which a worker restarted after 03:00 skips.
  A one-off backlog worker (`prax work --watch --scope all --steps
  extract`) runs after marker.
- **45 PDFs without text.** 38 are cut short (no `%%EOF`), and Zotero's
  own copies are the same bytes: broken at the source, to be fetched
  again. 39 of the 45 also carried a "created" reading with the empty
  string's hash: a forced reading of nothing was indexed. Fixed:
  `queue.apply_parse` never indexes an empty text.
- **A swap that did not hold.** marker took the card from an idle
  llama-server; three minutes later the follow-up readings waiting for
  llama-server loaded it again (`_idle` reloads an idle server whose work
  waits), so both models sat on the card and in memory, and the session's
  memory guard stopped a waiting job. An idle server whose card is lent
  now waits for it and comes back with it (`Supervisor._lent_away`, the
  loan's `was_up`). The first step of stage AI's planner, measured on the
  machine it broke.
- **The 38 cut-short PDFs, fetched again where they could be.** 37 are
  loose attachments with no DOI, URL or authors (several named by an md5
  prefix, the old zoetrope store's naming), and no intact copy is on the
  Zotero drive. One had an arXiv id: fetched whole (28 pages) as document
  13376, and 4057 retired into it as its duplicate. The other 37 wait for
  their originals from wherever the user kept them.

## 2026-10-02, the evening: "do it now" (stage AI, step 2)

The plan was rewritten first. 1,086 lines of finished work moved here
verbatim, under the next heading. What stays open is grouped by
subject, and the order is AI, the AD2 tidy-up, the backlogs, then AA.

A person can now fast-forward what waits for the card. `GET
/work/demand` groups the waiting work by role and action: an
extractor's readings, or a step's items deferred because its server
was gone (`work.wanted_steps`). Each group carries its rate and hours
left. `POST /work/now {role, action}` releases that work's deferrals
and records the request (`work.do_now`). The demand names the role
under `now` until nothing of it waits, six hours at most.

The supervisor acts on it, as invariant 4 wants: the door says, `prax
up` does. At its next look at the demand (`Supervisor._do_now`) it
swaps the card to the role. When the card was lent away from that
role, it ends the loan instead. It does neither while an ask holds the
card: `work.asking()` wraps both kinds of `POST /ask`, and
`ask_holds` stays true for five minutes after one ends
(`ASK_HOLD_SECONDS`). The request stands, so the swap happens after
the answer. A borrower's `swap: auto` no longer takes the card back
from a role a person asked for; without that guard, "do it now" for
llama-server would have flipped back to marker on the next tick.

The jobs view lists the groups under the card's group, each with "do
it now", or "asked for now" once asked. "See who holds the card", a
folded step of the old card plan, turned out done already
(`hostinfo.holders`); the plan says so.

Step 3 came with it: what a swap costs. `prax up` notes each role's
load time, from start to its first health answer, and says it ("up
(181 s from start)"). It keeps the last five per role in
`run/loads.json`, so a restarted supervisor still knows them. The
status carries their median as `load_s`, and the jobs view adds "loads
in about 3 min" to a role that needs the card. The plan of step 4
reads these numbers.

## 2026-10-02, the evening: the card's plan (stage AI, step 4)

`GET /work/plan` says what the card does next, and why
(`prax.host.plan`). It reads the door's demand and the supervisor's
status, and acts on nothing. Each waiting group gets three costs in
seconds. The swap is the load time of the role that would take the
card plus that of the one that gets it back, measured by step 3, or
`LOAD_GUESS_S` (120) marked as a guess. The work is the items over the
group's rate. The wait is the age of its oldest request
(`store.waiting_since`, which also says whether a person asked).

The decision is "being served" when the role holds the card. It is
"next" for a "do it now", for 50 items or more, or once the wait has
outgrown the swap: three times it for a person's request, twenty times
for the door's or a rule's. Otherwise the group waits for the nightly
window or for its wait to grow, and the plan says how many seconds
more. With marker at 40 s and llama-server at 180 s, a person's marker
reading goes next after eleven minutes. The factors are the hysteresis
the plan of 2026-10-01 asked for; they are guesses until step 6
measures waits and swaps.

The jobs view shows each group's decision beside its "do it now".

## 2026-10-02, the evening: `prax up` follows the plan (stage AI, step 5)

The supervisor now does what the plan says. On its look at the demand,
every 20 s, it asks `/work/demand?plan=true`: one request, with the plan
built from the same demand. The first group the plan puts next gets its
role the card (`Supervisor._follow_plan`). That is a swap when the card
is free of a loan, or the loan's end when the card was lent away from
that role. Nothing moves while an ask holds the card.

It replaces two rules. `swap: auto` used to swap as soon as anything
waited. It would have taken the card from llama-server in the middle of
the maths eval to read two PDFs. `auto` now means the role follows the
plan by itself; a role without it moves only for a person's "do it now".
The `_do_now` of step 2 is folded in, since a "do it now" is just the
plan's highest reason for "next".

Two holds moved into the plan with it, so the supervisor has no rules of
its own. A role on the card that still has readings waiting keeps it;
a waiting group reads "after llama-server". A role a person asked for
goes before the rest, so the card does not flip back on the next look.
The night window is the worker's `nightly` hour plus four hours
(`plan.night_now`, local time). Any waiting group goes then.

On the desktop, marker is still `swap: ask` in `prax.yaml`. The plan
acts there only on "do it now" until `swap: auto` is added to its line.

## 2026-10-02, the evening: the AD2 tidy-up

The maths pack now keeps its tests and its pieces of the core with it.

The four `test_maths_*.py` files moved to `src/prax/packs/maths/tests/`.
Pytest collects them from the root, as CI runs it. Fixtures in
`tests/conftest.py` reach only tests under `tests/`, so the shared ones
(`data_dir`, `con`, `client`) moved to a `conftest.py` at the
repository's root; `copy_ontology` stayed, since five tests import it
from there. Mypy excludes a pack's `tests/`, as it never read `tests/`.
`test_score_maths.py` stayed in `tests/`: it tests a script of the eval,
not the pack.

Three maths pieces in the core went behind one manifest field,
`Pack.chunk_marks` (`{"check": "prax.packs.maths.formulas:nearby_note"}`):

- `store.formulas_to_check` and `set_formula_checks` became
  `chunks_to_mark` and `set_chunk_marks`, which take the chunk kind, the
  key and the field the mark is made of. Both refuse a key no pack
  declares, so a pack cannot write over the core's keys. The worker's
  wire format did not change (`latex` out, `check` back), so a worker
  started before this still works.
- `equations_near` returned `broken`; it now returns `marks`, the
  declared keys of each equation's data.
- `ask.Passage.nearby_line` printed the "does not hold" itself; it now
  prints what each running pack's note function says of its mark
  (`ask.MARK_NOTES`). On a host whose `packs:` leaves maths out, the
  note is gone, as the pack's other parts are.

Tests: 1155 passed before, and the same tests after the move.

## 2026-10-02, the night: the 29 books, and marker's own llama-server

The 29 books were queued for marker at 21:53 (a dry run first, 29 of 29)
and the card swapped to it. By 22:35 four were read.

Free RAM fell to 3.5 of 31 GB and a background job was stopped for
memory. `prax up --status` showed llama-server paused, yet a
llama-server held 10.3 GB of RAM and 13.3 GB of commit. It was marker's.
Marker 2.0 reads with surya-ocr-2, a GGUF vision model, and surya's
llama.cpp backend starts a llama-server of its own: prax's binary,
found on the `PATH`, with `--parallel 8 --ctx-size 98304` and llama.cpp's
default prompt cache of 8192 MiB in RAM. The model is 1.4 GB and sat on
the card in 3.4 GB of VRAM. The server's log held 27,589 evictions from
a full prompt cache, which OCR cannot use: every page is a new image.
Marker's server held 3.4 GB more, its two helpers 1.8 GB.

Prax's own llama-server roles carry the same 8 GB default. The fix and
the larger question (who starts the processes on the card, and which of
the OCR readers earns its place) are stage AJ of `docs/PLAN.md`.

## 2026-10-03: the prompt cache bounded (stage AJ, step 1)

Every llama-server prax starts now says how much RAM its prompt cache
may hold: `--cache-ram` from `serve.cache_ram_mb`, 2048 MiB by default
for a chat server and 0 for a reranker. llama.cpp's own default is
8192. The marker role passes `LLAMA_CPP_EXTRA_ARGS=--cache-ram 0` to
surya, which appends it to the OCR server it starts; `ocr_cache_ram_mb`
and `ocr_parallel` adjust it. OCR prompts are page images, and an
evicted one is never asked for again.

Planned as an `ocr-server` role of its own, with marker pointed at it
by `SURYA_INFERENCE_URL`. That breaks the card group. A swap to marker
pauses every other member, its OCR server too, so marker would find no
server. The server stays surya's, inside marker's process tree, until a
group can carry companions (AJ, step 2).

It takes effect when marker and llama-server next start. The night's
books were still being read, so the desktop runs the old flags until
then. 2048 MiB for the 35B is a guess at what the surf's resent context
needs; its metrics (the prompt cache's share) will say.

## 2026-10-03: the genre words in the document field (stage AA)

A document's retrieval field now names what it is, in the labeller's
words: "PDF document datasheet", "web page recipe", "text question and
answer". The words are the genre labels in `meta.genres` with `p` of
`GENRE_FIELD_P` (0.5) or more. Levels like "informational" say too
little to help a search and stay out. `qa` and `source` are written out
as "question and answer" and "source code". 12,551 of 13,395 documents
carry genres: the labeller's 12,302, the person's 194, Claude's 55.

Measured on a copy of the store, read from the live one with SQLite's
backup API. The fields were rebuilt (12,412 changed, 11 s), the document
vectors embedded again (12,414 in 475 s beside marker on the card), and
`scripts/eval_retrieval.py` was run with `queries-library.yaml` before
and after:

| mode | hit@1 | hit@3 | MRR |
|---|---|---|---|
| fts | 0.73 → 0.73 | 0.87 → 0.87 | 0.80 → 0.80 |
| vec | 0.69 → 0.69 | 0.81 → 0.81 | 0.75 → 0.75 |
| hybrid | 0.87 → 0.87 | 0.92 → 0.92 | 0.90 → 0.90 |

One query moved, from rank 8 to 9. The words do not disturb a topical
search. Whether they help one that names a kind of document is not
measured, since the set holds no such query; the next queries for it
come from the documents the person labelled. The copy was deleted after.

## 2026-10-03: mypy's strict flags over the whole package

The first strict batch (2026-09-30) covered eight packages. The plan
held the next one back until the store had an `__all__`, because 926 of
strict mode's 1,008 errors were the store's re-exports. Those errors come
from one flag, `no_implicit_reexport`, which the batch never set: the
store re-exports every name on purpose (invariant 3). Under the batch's
own flags the rest of the package had 66 errors.

- 42 `no-any-return`: a value from an untyped library returned as a
  declared type. The fix is the batch's: a typed local, then the return.
  Three roots took 17 of them at once: `steps.get` now returns a `Step`;
  `Door._check` takes and gives an `httpx.Response`, so `.content` is
  `bytes`; and the MCP tools that answer one record go through
  `_answer`, typed `dict[str, Any]`.
- 14 untyped calls into pymupdf and striprtf, which ship no types:
  `untyped_calls_exclude` names them.
- 7 unused `type: ignore`s. Five in `hostinfo` are Windows calls that
  Linux needs ignored and Windows does not: they now say
  `[attr-defined, unused-ignore]`, so both platforms pass. Two were
  stale.
- 3 helpers without types (two lambdas, the redirect handler), and one
  `dict` without arguments.

The flags moved from the overrides to `[tool.mypy]`, so a new module is
strict from its first line. `mypy` and `mypy --platform linux` pass;
1156 tests pass.

## 2026-10-03: the first client's four small bugs (stage AL)

An agent in another repository used prax for a session through the
plugin and wrote down what got in its way (`docs/PLAN.md`, AL). The four
small ones are fixed.

- **A door address without its scheme.** `PRAX_DOOR=<address>:8000`
  failed every call ("Request URL is missing an 'http://'"), while the
  MCP server looked connected. `client.door_url` adds `http://` when a
  scheme is missing; `Door` uses it.
- **A URL with a space.** `/ingest/url` turned `ValueError` and
  `OSError` into 400 and 502, and `http.client.InvalidURL`, raised by a
  literal space, into an opaque 500. `inbox.clean_url` now
  percent-encodes a space or a non-ASCII character in the path, query
  and fragment, leaving what is already encoded. A URL still not fit to
  fetch is a 400 that says so.
- **Links.** The agent found `ui/#doc/N` by reading the UI's source. The
  answers of ingest (text, URL, file), `get`, a document's context, and
  a page written or appended now carry `url`: the UI at the address the
  caller reached the door by (`api._base.ui_url`). `#page/<slug>` opens
  a page by its slug.
- **When a document came.** The MCP `documents` tool reported
  `created_at` from a field the door never sends, so always null; it
  reports `added_at`. `since` (a date or a UTC moment) keeps what was
  added then or later, on the store, the door and the tool; anything
  else is a 400.

## 2026-10-03: `status` and `health` (stage AL, step 1)

The first client could not tell "not yet" from "never" from "no worker":
24 captures sat without text for hours, and `get` said `pending: []`.
That list holds the readings a person or the door asked for, not the
parse queue a capture waits in.

`GET /work/status?ids=` (`work.status`) says, per document: `indexed`;
`processing` (a worker holds it); `reading` (what it waits for, and
`server_down` with the role of `prax up` that must be up when its
reading was deferred); `queued` (its place among the captures, or the
nightly backlog pass for an import's document); `nothing found` (the
readers ran and found no text, so a scan wants OCR or the vision
model); `failed` (the last attempt's error); and `unknown` for one the
caller may not see (stage U: the route is a named token's, with a case
in `tests/test_wall.py`). `worker.alive` holds when a worker asked for
work in the last two minutes or holds a lease now. A long marker read
asks for nothing new for twenty minutes, but renews its lease.

The MCP server has two tools more. `status(doc_ids)` passes that on.
`health()` makes one call, the same route without ids, and the answer,
a refusal or no answer tell all three of its fields: the door's
address as the server uses it, reachable or not, the token accepted or
refused, and the worker. The skill says when to use each.

## 2026-10-03: a link to a passage, across a re-sync (stage AL, step 2)

The first client asked whether its page's links to passages break when a
synced document is synced again. They do worse than break. A re-index
keeps the id of every chunk whose text is unchanged, deletes the changed
ones and inserts their successors, and SQLite gives a new row the
highest id plus one. A document synced and then synced again holds the
newest rows of the table, so its changed passages came back under their
old ids with the new text: three of seven in the probe. A link
`#doc/1/6` then opens another passage without a sign.

What is safe: a saved answer cites `[title](#doc/N)` with the heading
path and the page as text, never a chunk id. Graph edges keep a quote
as their evidence. What is not: a link an agent writes from a search
hit's `chunk_id`.

Stable chunk ids would mean rebuilding the chunks table. The fix is
the link's instead. `lib.chunkTarget` trusts `?chunk=M` only while
chunk M still holds the link's `find` words, and otherwise finds the
passage by the words, or highlights nothing rather than the wrong
thing. The plugin's skill tells an agent to link a passage as
`#doc/N?chunk=M&find=a+few+words`. `tests/test_store.py` pins down the
reuse, so nothing comes to rely on a chunk id as a name.

## 2026-10-03: when a document was published (stage AL, step 5, first item)

A person judges a source by when it was written, and prax showed that
for 1,890 of 12,837 live documents, on the document page only: Zotero's
`meta.date`, 1,072 of them a year alone.

`meta.published` now holds the date as precise as its source says it
(`2019`, `2019-07`, `2019-07-03`), its precision, and what said so.
`store.published_of` takes the most trusted source that says it: a
person (never replaced), the record (Zotero's date), what the browser
extension found on a paper's page, a page's citation tags (what
scholarly sites write for Google Scholar), its schema.org
`datePublished`, the arXiv id (the month of the first version), and a
page's generic article and Dublin Core tags, often the day it was last
edited. `prax.text.dates` reads them all, with month names in English,
German and French. Nothing is guessed: what is not clearly a date gives
nothing.

A capture writes it as it arrives (`ingest_bytes`); the `published` pass
of `prax maintain` fills the rest from what the documents hold, reading
the head of a web page's original. Search hits carry `published`; `get`
shows it; `ask`'s passages put the year beside the title, so the
answering model weighs a source's age too; the UI shows the year on a
hit and the date, with its source on hover, on a document's page.
Search and `documents` take `published_since` and `published_before`,
and leave the undated out of a filtered search.

Found on the way: the document page wrote `meta.date` into its HTML
without escaping it, harmless while only Zotero filled it; it goes
through the same escaping as everything else now.

Still to come: Crossref by DOI (the citations importer asks it
already), the first page read by the titles pass, a PDF's metadata.

## 2026-10-03, the night: dates from the first page

The `published` pass dated 2,016 documents, 126 more than the Zotero
dates shown before. Most of the other 10,800 are PDFs. Measured before
building anything more (read-only, on the live store):

- **DOIs**: only 109 of 8,221 undated PDFs print a labelled DOI in their
  first 6,000 characters, 24 more an unlabelled one. Crossref waits.
- **A heuristic**, the latest plausible year on the first page: right for
  62% of 1,836 Zotero-dated PDFs, 271 with no year at all. Not to be
  trusted.
- **A model** (the 35B on the desktop) given the title and the first
  3,000 characters, asked for `DATE | WORDS` or `none`, its answer kept
  only when the words are on the page and hold the year
  (`writing.dates.checked`): on 120 Zotero-dated PDFs it answered for 84
  (70%), and its year was Zotero's for 72 of them (86%), in 94 seconds.
  The misses were read by their words. Those marked high (the words name
  a conference, a journal, a copyright line) are mostly Zotero's other
  version of the paper: a NIME 2003 paper Zotero dates 2017 (a reprint),
  "Published online: 08 Aug 2014" against the 2015 issue, "Copyright 2005
  ACM" against 2006. Those marked medium are bare dates ("30. Mai 2007",
  "14.07.2007", ResearchGate's "Article · September 2012"): print and
  upload dates, the real errors.

So the `dates` step: a watched model step (off until `prax.yaml` names
its model, `steps.dates`), the whole library newest first, not only the
scope's captures, since a backlog of a hundred a night would take
months. Its date ranks below every other source, keeps its words and
its confidence, and a document it cannot date is marked tried and not
asked again. The page shows the words on hover, and a medium date with
a question mark. `scripts/eval_dates.py` is the measurement.

## 2026-10-03, the morning: names that swallowed their line

Looking for near-identical entity names (for entity resolution) turned
up names like `interpolation concept=INFERRED evidence=There are 11
types…` and `AUDIO dst_type=concept(confidence=EXTRACTED evidence=…`:
903 live entities, 638 live edges from 286 documents, nearly all from the
35B's extraction. The model wrote the rest of a triple line into a name
field with spaces for tabs; the grammar allows anything but a tab or a
line break in a name, then makes the line end properly, so the line
parsed.

`prax heal` already had the check (`wire-names`), and found 943; its
repair cuts the name at the syntax and cleans it, or merges it into the
entity that already carries the clean name. Run with `--apply`: 943 of
943. Nothing stopped new ones, though, and the extraction backlog was
running. `lineformat.parse` now drops a triple whose name holds a field
key (`word=`, three letters or more) and counts it as
`usage["leaked_lines"]`: a model that lost its place in a line is not
trusted with the rest of it.

## 2026-10-03: names written nearly alike, and a gate on far-reaching merges

Two pieces of Graphiti's and Utopia's resolution, measured on the library
first (read-only).

**A near tier.** prax's sure tier already merges names equal once
normalized (case, punctuation, diacritics, plurals, suffixes), so
Graphiti's exact step was there. Its fuzzy step (3-gram shingles, MinHash
with LSH, a Jaccard of 0.9) found 1,601 near pairs over 154,691 names in
94 s. Merging them outright, as Graphiti does, would have been wrong:
the 17th and the 20th ISMIR were 0.91 alike, ICASSP 2012 and 2018, and
"C code" and "C/C++ code" 1.0. So `names.near_pairs` only proposes, and
never a pair whose numbers differ (`names.same_numbers`). Within the
types the embedding tier covers it adds little (62 pairs), but paper
titles and organizations were never candidates there, since titles that
differ by a qualifier embed alike: 646 and 111 pairs. The worker's
`resolve` step computes both for its types (`RESOLVE_TYPES`), and the
pairs go to the adjudicator as any likely pair does.

**A gate.** A yes from the adjudicator is held for a person when the
merge reaches far (`store.merge_risk`): an entity with 100 or more live
edges (half the library's entities have one, 99% at most 32, the largest
2,758), or one a page says something about (3,295 entities). Utopia's
third reason, an inferred edge, would hold most merges here: 54,000 of
213,000 live edges are the extractor's own INFERRED. It waits for
rule-derived edges (stage AN). A held pair (`entity_candidates.held`,
migration 34) leaves the automatic list and heads the Review page's,
saying why.

The search turned up the leaked names of the entry before.

## 2026-10-03: written once, and when a fact holds (stage AL, step 5)

**Append-only, by trigger** (migration 35). The database itself now
refuses to delete an edge, to change an edge's fact (its ends, relation,
confidence, quote), and to change or delete a page revision or a row of
`spend`. Before this, only the code paths kept those rules, and a code
path can change without anyone noticing. Provenance and the source
document may still be mended: a duplicate's edges still move to the
survivor, and a missing producer is still backfilled.

**Two times on a fact** (migration 36). `valid_from`/`valid_to` were
called bi-temporal, but both are record time: when prax wrote an edge
and when a later reading ended it. The world's time is new:
`world_from`/`world_to`, each a date as precise as the source writes it
(`2019`, `2019-07`, `2019-07-03`) with its precision. A CHECK ties each
date's length to its precision. "Ended, date unknown" is `world_to`
NULL with precision `unknown`, as Utopia stores it. `store.link` takes
both and refuses a date `dates.parse` cannot read, so no guess is
stored. `traverse` shows them only on the facts that have them
(invariant 6). Nothing fills them yet: the extraction prompt gets
optional `from=`/`until=` fields next, measured before a pass.

**As of a day.** `store.held_at(alias, as_of)` is the one place that
builds "the edges prax held at T": written at or before T and not ended
before it. A date stands for the last moment of its span, so "as of
2026-09" means the end of September. The walk uses it, and
`/traverse?as_of=` and the MCP tool expose it. A walk as of an earlier
day still follows today's entity merges.

Later the same day, the follow-up: the "92 other places" counted the
`__pycache__` files too. The source holds 48 live-edge conditions in the
graph part of the store and 23 elsewhere in it, and each means "now",
which `held_at` without a moment writes the same way. So they stay as
written. What the test holds instead is the risk: every store read that
takes `as_of` builds it through `held_at` (or hands it on to one that
does), and no module but `edges.py` writes the past-moment form
(`test_a_moment_in_record_time_is_built_in_one_place`; a planted
breach of each kind failed it).

## 2026-10-03: a fact's witnesses, in the first hop's cap

A fact several documents state is one edge per document. In the first
hop each such row now says how many documents state its fact
(`support`, absent for one). The cap (`graph.edges`, 25 in the surfer's
`walk`) is spent on distinct facts first, the best supported first
within a relation, still round-robin over the relations; a fact's
further rows come only after every distinct fact has had its place. The
surfer's `walk` names a fact once, with "+N more" documents.

Measured on a copy of the library, over 60 entities past the cap (the
15 most connected and 45 drawn from the rest), old rule against new:

| at a cap of 25 | old | new |
|---|---|---|
| distinct facts shown | 1,449 | 1,500 |
| documents behind them | 1,536 | 1,672 |
| of each entity's 10 best-supported facts, shown | 481 | 517 |

Only 24 of the 60 carry any fact more than one document states, so the
gain sits there. For one paper, 2 of its 10 best-supported facts were
shown before and 9 after; for one author, 4 and 8. `ask` was not
re-run for it: the change reaches an answer only through a walk on such
an entity, and a run's spread (3 to 7 questions) is larger than that.

## 2026-10-03: Qwen 3.8's dense 27B, and an uncensored build of it (stage AK)

What Qwen released after 3.6: Qwen 3.8 (mid-August), whose open models
are a dense 27B and a 2.4T-A95B; a 35B-A3B is only registered in a
ModelScope commit, not released. The files, downloaded with the user's
word: unsloth's `Qwen3.8-27B-UD-Q4_K_S.gguf` (15.4 GB, sha256
`75bc9c8adba2842e72f0ab5201aaa07133c5010b566305c09187fcbdcd364017`) and
huihui-ai's `Huihui-Qwen3.8-27B-abliterated-UD-DW-Q4_K_S.gguf` (15.6 GB,
sha256 `f10c26c7d07b056bccc60e09ec427a0861a7df554519e97ffd115f5f45900176`),
abliterated from unsloth's files, so the two differ by the ablation
alone. No projector: every measure is text. llama.cpp b10900 runs both.

Each was served by `prax up` in turn (`server-27b`, `server-27b-u` in
prax.yaml, `run: llama-server` pointed at it), the worker paused, so no
pass wrote with a model under test. The 35B ran the same sets the same
afternoon with today's code.

| | 35B-A3B (3.6) | 27B (3.8) | 27B abliterated |
|---|---|---|---|
| writing, tok/s | 149 | 50 | 50 |
| reading a 2,300-token prompt, tok/s | 2,200 | 2,450 | 2,400 |
| card in use (with the display) | 22.1 GB | 16.9 GB | 17.1 GB |
| extraction, 14 documents: seconds | 141 | 422 | 440 |
| triples / valid against the ontology | 188 / 141 | 240 / 225 | 255 / 223 |
| overlap with the live edges (196) | 74 | 74 | 79 |
| maths, right of 10: tools on, grounded | 5 | 9 | 10 |
| tools on, open | 7 | 10 | 9 |
| tools off, grounded | 4 | 6 | 7 |
| tools off, open | 8 | 8 | 8 |
| maths eval, 80 asks: seconds | 1,145 | 1,935 | 2,052 |
| personal documents, 40 × 2 tasks: declined or hedged | 0 | 0 | 0 |

The maths numbers are one run each, machine-scored (`score_maths.py`);
the 35B's five runs of 2026-10-02 averaged 7.0, 8.6, 7.0 and 7.8, so
its 5 and 4 today are the low end of its spread, and the 27B's 9 and 10
with the tool are past it. The 27B calls the calculator more (60 maths
steps against 45) and writes valid triples more often (94% against
75%), at a third of the speed. The extraction overlap is measured
against edges the 35B mostly wrote, which favours it.

The abliterated build loses nothing measurable here. It gains nothing
either: on the owner's personal documents (`scripts/eval_refusals.py`,
counts only) none of the three models declined or hedged a summary or
a list of names and amounts. What the original refuses is a question of
another kind, which the user tries by hand.

Nothing switches yet. A split is what the numbers suggest: the 27B for
`ask` and the surf, where a third of the speed costs seconds per
answer, and the 35B for the bulk passes. Only one model fits the card
at a time, so it needs a measurement of the swap first.

By hand (the user, the evening): the abliterated build answers what the
original refuses ("definitely uncensored"), and its tool use feels more
fluent. In the maths eval it took fewer calculator steps than the plain
27B (46 against 60) for the same scores, which fits that reading: fewer
calls that miss.

Found on the way: with the worker paused, an `ask` did not wake an idled
llama-server. The door answered "it has been asked for", and nothing
loaded until `prax up --start llama-server`.

**The host switched** (the user, 19:20: "keep the uncensored 27B, delete
the plain one, start the worker"). `run: llama-server` and every local
step now name `server-27b-u`, so the provenance stamps name the model
that wrote them. Two steps are off until they are fitted to it: `vision`
(the abliterated repository has no projector; unsloth's
`mmproj-F16.gguf` for Qwen 3.8, 928 MB, is the candidate) and
`adjudicate` (its Platt fit is the 35B's; `eval_confidence.py platt`
again on the Review page's decisions). The plain 27B is deleted; the
35B's file (21 GB) stays until the user says.

**`adjudicate` refitted for the 27B** (the user: "do the adjudicate
recalibration first"). `eval_confidence.py gold`, `ask`, `platt` on the
296 pairs a person has decided on the Review page (205 same), 74 s of
the model:

| | agrees with the person | at 0.9: settled, agreeing | at 0.95 |
|---|---|---|---|
| 35B, 2026-09-28 (221 pairs) | 0.81 (ECE 0.031) | 40%, 87 of 89 | 30%, 66 of 67 |
| 27B abliterated (296 pairs) | 0.77 (ECE 0.050) | 36%, 98 of 107 | 16%, 47 of 47 |

The sets differ (the person decided 75 more pairs since), but the 27B
is the weaker judge of sameness. `steps.adjudicate` names it again with
its own fit (`platt: {a: 0.5525, b: 0.5186}`) and `settle: 0.95`: it
settles fewer pairs, and the rest wait on the Review page with its
number. Pairs the 35B already scored keep the 35B's number
(`scored_pairs` are not asked again).

**`vision` back on, with a projector for the 27B** (the user: "work on
the vision projector first"). huihui's repository has none; unsloth's
`mmproj-F16.gguf` for Qwen3.8-27B (928 MB, sha256
`cbb841a9ee0636b2ec172f5bb8df2ea8dfeb01e90fe7c6126581d662a0b4e43e`) is
served beside the abliterated weights, since the ablation leaves the
vision tower alone (`serve.mmproj` by absolute path, `image_max_tokens:
1024`). The card holds 18.6 GB with the display, 1.5 GB more than
without it. On three figures the 35B had read, the 27B's readings were
as coherent and caught the labels, each model misreading one thing the
other got right (the 35B read "Bass 82.aif", the 27B "Base B2 af").
Through the pipeline: document 13449's three figures in 35 s, stamped
`figures/1-r2+qwen3.8-27b-abliterated-ud-dw-q4ks`. 3,625 documents still
have figures nobody read; that run waits for the user.

## 2026-10-03, the evening: three small fixes, and retrieval in parts

- **An ask wakes an idled model under another name.** The demand an
  ask records was there all along; `work.role_of_step` matched a step to
  a role by the model entry's name only, and during AK the steps named
  the 35B's entry while `run:` served the 27B's. It now also matches a
  model at the served address (9f15efa).
- **One tray a data directory** (`run/tray.pid`, `tray.claim`), so a
  second icon is refused while the first lives (75e77a3).
- **`store.retrieval` as a package.** It passed 2,000 lines (2,038), the
  line invariant 3 draws. Seven parts in `ORDER`: `knobs`, `compounds`,
  `query`, `legs`, `vectors`, `fusion`, `similar`. The trap the plan
  named was the tests' module flags: `test_senses` set
  `retrieval.SENSES = False` by assignment, which on a package creates a
  new attribute and tests nothing. The five switches (`SENSES`,
  `INFLECT`, `DOMAIN_PRIOR`, `DELTA_MERGE_AT`, `DELTA_MERGE_MIN`) are
  attributes of one object, `retrieval.knobs`, read at call time, and
  the package refuses a knob set on itself (`_Package.__setattr__`)
  naming the right place. `text.compounds` moved in as a part, with
  `term_documents` beside it, so it no longer reaches up to the store.
  No behaviour changed: the suite (1,201) passed unchanged but for the
  knobs' address, and the order test caught a planted backward import.
- **A capture is not starved by a queue of readings.** The figure
  backlog (3,625 documents, about 15 an hour) filled every parse batch,
  because the hand-out gave the readings the batch and the captures what
  was left: a page sent from the browser would have waited ten days.
  Waiting captures now keep up to half of each batch, counted by the
  ones that go out (`steps.parse._capture_item`), so a capture tried and
  left alone keeps no room from the readings.

## 2026-10-03, the night: syncing a project as a tool (stage AL, step 3)

The first client synced a subproject's 27 documents through the CLI. It
needed the virtual environment's path, a call out of its sandbox, and it
took in about 90 vendored CMake files. Its notes became the shape of the
tool.

**One call, planned at the door.** An MCP tool makes one HTTP call
(invariant 5), so the plan moved from the client to the door:
`POST /projects/sync` takes the files and answers with the plan, each
path `add`, `refresh`, `unchanged`, `moved` or `skip` with why, and the
documents of the project that are `gone` (reported, never retired by a
sync). It is a dry run unless `dry_run: false`
(`prax.capture.projects`).

**Which files is the client's.** `prax.client.project_files` lists what
git tracks (`git ls-files`; untracked only when asked), keeps the
document suffixes, and leaves out build and vendored folders, now with
CMake's (`_deps/`, `CMakeFiles/`, `*-subbuild/`, `*-build/`). A dry run
sends no text. The rule lives in `prax.client` because the proxy may
import nothing else of prax; `prax.importers.project` and the door use
the same one (`project_skip`).

**Keys.** A document is keyed by the canonical git remote and its path
in the repository (`github.com/a/b:docs/x.md`; `canonical_remote` folds
`git@host:a/b.git`, `https://user:secret@host/a/b` and the rest into
one, and no secret leaves the machine). A checkout in another folder or
on another machine finds the same documents, and a subdirectory is a
project of its own. A document the old importer wrote (`<name>/<path>`)
is adopted under the new key.

**The manifest in prax** (`projects`, migration 38): the name, the
remote and folder (one project a working copy), the settings, and
`auto_sync`. The session-end hook runs `prax sync --if-auto`, which asks
the manifest; nothing in the repository switches it on for a colleague.
An older `.prax-project` still counts and still names the archive
choices.

**The page.** The first sync makes `project-<name>` and links every
synced document to it (`part_of`), without the promote flag a paper
added by a person gets, so dozens of notes do not queue for the paid
pass. The summary on it is the agent's to write.

Surfaces: the `sync_project` MCP tool, `prax sync [ROOT] [--apply]
[--auto]`, `GET /projects`, the plugin's `/prax:sync`, the scope and
remember commands (the project's name comes from a dry run now). The
route takes the administrator's token only: its plan names document ids
a named token might not be allowed to see. Tested on invented
repositories (`tests/test_project_files.py`, `tests/test_projects.py`,
the MCP and CLI tests); the client's own project is the acceptance set,
on Monday.

## 2026-10-04: documents linked to documents (stage AL, step 4)

Documents were graph nodes already (`cites`, `annotates`, a page's
`[title](#doc/N)`), but nothing said what one note says of another.

**Core 4.** Three relations, document to document: `links_to` (a link or
a path in the text, written by a sync, never guessed), `supersedes` and
`invalidates` ("the changelog invalidates the re-baseline table"), with
aliases that flip (`superseded_by`, `replaced_by`, `invalidated_by`). The
bump only adds relations, and core is in every document's set: as with
research 8 to 9, migration 39 moves the extraction stamps
(`"core3+` to `"core4+`) instead of putting the whole library back in the
extraction queue. Edges keep the version they were written under.

**At sync.** `prax.text.paths` finds what a text refers to: a Markdown
link resolved against the file's folder, a path in backticks, a bare
`docs/x.md`, each with its candidates (from the repository's root, the
project's folder, the file's folder). Matched exactly against the
project's own paths, each is a `links_to` edge with the words as written
for evidence (producer `sync`, run `links:<name>`). Each sync keeps them
in step: new links added, links that went ended (`store.run_edges`), and
a path that matches nothing is counted (`unmatched`) and not stored, so
the next sync finds the file once it arrives. A refreshed note's edges
move to its new document with it, and the ones its new text no longer
holds are ended.

**`doc:N` as an end.** `link` takes `doc:N` on either side: the
document's title and the type its entity already has
(`store.document_node`; a page is a page). A hidden document is a 404,
as if absent, and a chunk id as evidence is a 400: evidence is the
words. `traverse("doc:N")` walks from a document, and its context
carries `linked`, its edges out and in.

Left of step 4: functional relations and a pass proposing `contradicts`
between facts that disagree, a stage of its own.

## 2026-10-04: what is current (stage AL, step 5)

13 of the first client's 27 project documents say they are retired,
superseded or invalid, and an agent that quoted one as current repeated
the failure that cost months of a pitch.

**What a note says of itself** (`prax.text.status`): front matter
(`status: retired`, `retired: 2026-10-02`, `superseded_by: …`) and an
explicit status line near the top (`> **Status:** superseded 2026-10-02
by [the new plan](plan-v2.md)`, `RETIRED 2026-09`, `**Deprecated**
since …`). A state word must open a line in the first 30, so "the
retired voice board" in a heading or prose says nothing. Only a
project's own notes are read this way; a paper's "superseded by" is
about other people's work.

**At sync** (`projects._statuses`): the state is `meta.status` (by
`sync`, gone when the line goes, a person's never touched), and a
replacement the line names, matched exactly against the project's
paths, is an edge from it to the stale note: `supersedes`, or
`invalidates` for an invalid one, the line for evidence and its date as
the world's time (`world_from`). Kept in step per run (`status:<name>`)
by the helper the links share now.

**In search and `ask`** (`store.staleness`): a hit whose document is
stale by its own status or by a live `supersedes`/`invalidates` edge
(a sync, an agent's `link`, an extraction) carries `stale` (state,
since, `replaced_by` with ids the viewer may see) and moves five places
down, a shift rather than a score so it means the same with a reranker;
five extra candidates are fetched so a current one can move up.
`include_stale` keeps the plain order. `ask` labels the passage
"[no longer current: superseded 2026-10-02 by …]", and the UI says it
beside the hit and in the document's date line.

The first query for the edges let the planner begin from the edges
table, which a `rel` filter cannot narrow: 58 ms a search on the live
store. Begun from the titles (the name index), the entities merged into
them and `idx_edges_dst`, in that order (`CROSS JOIN`, materialized),
it is 0.2 ms.

Not measured yet: the client's own question ("the current candidate and
its figure"), which needs its project re-synced so the status lines are
read; Monday.

## 2026-10-04: captures and pages, four of five (stage AL, step 6)

**The append that took over 120 s.** On a copy of the store the append
itself is 0.04 s (0.6 s cold): re-indexing the page, a revision, its
edges. The rest of the 120 s was waiting for the write lock, which every
write takes one at a time. The client's session predates the door logs
kept now, so the holder that day cannot be named; since then the logs
show writes kept waiting up to 28 s (a worker's session note behind an
embed post of 16.5 s and a bulk readings request of 12.4 s). So the
lock says it now: a write that waited over 5 s is logged with the write
that held the lock before it, one that held it over 10 s is logged too
(`store.base`, `LOCK_WAIT_SLOW`, `LOCK_HOLD_SLOW`). The maintenance
passes, run one by one on the copy, held it under 10 s each; the
document fields pass, which rebuilt all ~13,000 fields in one hold, now
lets go every 200 (`FIELD_BATCH`). The same run found the nightly
`languages` pass failing on a unique label index, a fault of its own.
Answering an append before the write is applied waits until the log
names a holder worth that change: an agent would otherwise append and
then not find its own section.

**`update_section`** replaces the body under a heading (or adds the
section), for what an agent keeps current. A section whose current body
a person saved is refused unless forced, and an ask block's section is
the questions pass's. The heading is found by `markup.section_span`
(outside fenced code, up to the next heading of the same level).

**`set_title`** (`PUT /doc/{id}/title`) over `store.retitle`, and
**batch capture** (`POST /ingest/urls`, the `capture_urls` tool): up to
20 URLs four at a time, one result per URL in order, a failure never
failing the others; the request's viewer goes with each thread.

**A page's lifecycle.** `GET /page` says `lifecycle: stale` when a
document the page links, annotates or synthesizes is no longer current
(`store.staleness`, as search reads it), with `stale_sources` naming
each and its replacement, and the page's header says it. The page is
never changed for it. "Contradicted" waits for the contradiction pass.

**The nightly `languages` pass failed every night** on the unique label
index: a label without a language whose twin (the same entity, the same
words) already carried the language the pass would give it. It now sets
the language where it can (`UPDATE OR IGNORE`) and counts the rest as
`labels_twinned` (6,337 on a copy of the store); nothing is deleted, the
twin says the name already.

**What the door cannot fetch waits for the browser** (the fifth of step
6). 7 of the first client's 30 captures failed on a TLS chain the server
did not trust, a 403 or a bot check: things a browser gets past. Such a
failure (a 401/403/429, `BotCheck`, a certificate or SSL error; never a
404) is now a request (`capture_requests`, migration 40, one waiting a
URL) and the answer a 202 with `queued_for_extension`, in a batch per
item. The extension asks for the waiting ones every 5 minutes (an alarm,
the new `alarms` permission), fetches each with the person's session (a
PDF directly, a page in a background tab captured like a tab), uploads
it by its usual routes and posts which request it was; the door then
gives the document the request's title and domains. A failed try is
posted too, and after three the request is failed. The options page
turns it off. The extension's test bed passes in Chrome but for two
checks of the keyboard send that failed the same way before this change;
Firefox could not be run with Waterfox open.

## 2026-10-04, the afternoon: bibliographies, privacy on write, and the client's page (AL steps 7 and 8)

**`references(doc_id)` and `cited_but_missing(set)`** (`store.references_of`,
`store.cited_but_missing`). Both read the reference entries a paper's
list was cut into, never `cites` edges: the "OnsetDetector.LL" the plan
noted came from the 35B's extraction, which typed madmom's program, named
in onset papers, as a cited document; an entry cannot be that. A work is
one DOI, else one arXiv id, else one folded title, and the missing are
ranked by how many papers of the set cite them. A title the library holds
exactly, or one it extends or that extends it by whole words (at least
four), counts as held: "Maximum filter vibrato suppression for onset
detection" is the library's "… (SuperFlux)" before the matching pass
links it. On the client's onset page (44 linked papers, 36 with a list)
the top of the list is what the field cites: Bello 2004 and Böck 2012,
seven papers each, the ENST and MDB drum sets. Open access is the DOI and
arXiv links for now; a lookup per entry (Unpaywall, OpenAlex) is a
network call per work and waits.

**`sensitivity: personal` on write.** `POST /ingest`, `PUT /page` and the
project sync take it (kept with the project's settings), so a colleague's
notes or a code review can live in prax behind the wall. Only `personal`:
a writer may hide what it writes, never open what is hidden.

**The client's page** (doc 13470, its check of the AL changes from its
side; its O6 and O7 were done the same day): `sync_project` and
`ingest_file` read within the git repository of the working directory
(O1); `status` says `searchable` when a document waiting for figures has
its text (O3); `request_reading` asks OCR, the vision model or marker by
name (O4); `health` tells a refused token (401) from a route not allowed
(403, most often a door older than the client) and names both commits
(O5). O2, a ready passage link on every hit, is against invariant 6 and
waits for the user.

**O2, after all: a passage link on every agent's hit.** Measured first:
a search hit is 220 to 520 bytes as the MCP tool returns it (median
317), a search of ten 3 to 4.4 KB; the link adds about 45, some 15%,
where invariant 6 was written against a 3.4 MB answer. The user: build
it. `store.cite_link` gives `#doc/N?chunk=M&find=…`, the words a run of
four (else six) of the passage as the UI folds text, that no other
passage of the document holds, so the UI's exact-match path lands on
it whatever a re-chunk does to the id; a passage the document repeats
(a running header) gets the id alone. Checked in memory against the
document's passages up to 3,000 of them, by a phrase query beyond
(four tries at most), within 100 ms a search (`CITE_BUDGET`); a later
hit gets the id alone. Only an agent's search pays for it (`brief`),
and `ask`'s passages carry it too. On six searches of the live store,
51 of 60 hits got their words, for 23 to 116 ms a search.

The first walk as of a day on the live store took 44 s: the indexes on
`edges(src)` and `edges(dst)` are partial (live edges only), so the
condition `held_at` writes for a past moment scanned every edge at each
step. Migration 37 adds full indexes on `(src, valid_from)` and
`(dst, valid_from)`, built in 0.7 s on a copy. Two hops as of
2026-09-20 from two entities went from 1,553 s and 275 s to 0.07 s and
0.01 s; the walks of now did not change (0.03–0.15 s). It was a missing
index, not a sign for the path index of stage AM, which must carry the
record times all the same.

## 2026-10-04, the afternoon: rules over the graph (stage AN, first part)

**The ontology annotated.** A relation now says what follows from it:
`transitive` (`part_of`, `located_in`, `has_part`, `supersedes`,
`succeeds`, `derived_from`), `symmetric` (`compatible_with`), `functional`
(`published_in`), `inverse_of` (none yet: prax states a relation one way
and maps the other through aliases), `kind` (state, event, eternal), and
`same_as` in schema.org, SKOS, PROV-O and Dublin Core where a term matches
exactly; core's types carry `same_as` too (`schema:Person`,
`skos:Concept`). None of it changes what validates, so no module's
version moves, as with `naming:`. `ontology.lint` refuses at load a
relation transitive and functional at once, symmetric between types it
cannot hold the other way, an `inverse_of` that names nothing or is not
mutual, an unknown kind, a subtype cycle.

**The rule pass** (`store.derive_rules`, the graph package's `rules`
part): the closure of each transitive relation (chains of six at most,
20,000 a relation at most), the converse of each symmetric one, the
inverse of a declared inverse. A derived edge is INFERRED, producer
`rule:<kind>`, run `rule:<relation>`, its world date the latest of its
premises', and its premises are kept (`edge_premises`, migration 41;
`GET /edge/{id}/why`, the `why` tool). An asserted edge is never derived
again. Premises come only from open documents, so a personal note's facts
derive nothing a restricted token could see. Each pass derives everything
again and compares: new derivations linked, kept ones' premises renewed,
those that no longer follow ended. Constraints are findings: the
`functional-conflicts` ailment of `prax heal` lists a subject with two
values of a functional relation, for a person.

**Measured on a copy of the library.** One pass: 2.9 s, 306 edges
(`compatible_with` 192, `part_of` 110, `has_part` 3, `succeeds` 1;
`located_in` none, since the ontology lets no place be in a place); a
second pass 0.3 s, everything kept. Read by hand, 20 `part_of`
derivations: 5 right (a book's chapter, a lecture in its course), 15
wrong, nearly all on a premise the extraction wrote backwards
("Technische Universität München part_of Lecture Distributed Problem
Solving", a course `part_of` an exercise sheet). A closure carries such a
premise into every chain through it. So the pass stays on request
(`prax maintain --only rules`), not on the door's clock; its use now is
as a detector, an absurd derivation pointing at the backwards fact under
it. The way on is a direction check of `part_of` at extraction, and then
the measurement again. The functional findings are noisy for a like
reason: most of the 200+ subjects with two venues are one venue under
two names ("ISMIR", "11th International Society for Music Information
Retrieval Conference"), which is entity resolution's work.

## 2026-10-04, the afternoon: which end of a part_of is the part (stage AN)

The rule pass measured 5 of 20 `part_of` derivations right, the rest
built on facts written backwards. A sample of the 866 live asserted
`part_of` edges of open documents, read by hand, showed three ways to be
wrong: the wrong way round ("Diskrete Strukturen II part_of
Übungsblatt 07", "Technische Universität München part_of Chair for
Informatics IX"), right neither way ("Technische Universität München
part_of Erasmus", a book `part_of` its publisher), or about a kind of
thing rather than one ("Aufgabe 1 part_of course", 14 edges into one
entity named "research project").

**The check** (`ontology.part_of_suspect`) reads the names against a new
`part_of` section of `ontology/lexicon.yaml`, so it bumps no version:
part words (Übungsblatt, Klausur, homework), words that count only with a
number after them (Teil 1, Vol 6, Chapter 32, Lecture 4), whole words
(course, journal, proceedings, manual, standard), generic wholes matched
whole ("course", "research project"), and four organization ranks (lab,
institute or department, faculty, university). It answers with one of
three verdicts. `reversed` is structural: the source ranks above the
destination as an organization, or the destination is the source's name
and a number more ("Dalil al Angham - 3.pdf") or its exam ("Klausur zur
Vorlesung X"). `misfit` holds neither way: a generic whole, an
institution leading the source's name part of something that is not an
organization, an organization part of a document, a document part of an
organization. `doubtful` is a cue alone: the destination names a part
and the source does not, or the source names a whole. A topic sits in an
exercise sheet as often as a course is wrongly said to, so a doubt is
for a person. Two guards keep the common right cases out: a destination
that also names a whole ("Leonardo Music Journal Vol 21"), and two names
sharing a content word (a section in "Chapter 6. Quadrature"). "Chapter
32- The Laplace Transform" is the chapter itself, not a part of the
transform. "mit" is not a rank: in German it means "with".

**Where it acts.** `Ontology.check_names` raises beside `check_edge` in
every writer of a model's or a rule's `part_of`: the extraction, the
typing pass, the review rules and the replay. A suspect takes the
misfit's way, to the review queue with its reason (`part_of doubtful:
…`). The rule pass stands on no suspect (`store.part_of_suspects`). The
`backwards-part-of` ailment of `prax heal` lists the edges already
written. Its repair turns a reversed edge round (old ended, new INFERRED,
producer `heal:part_of-direction`, same document and evidence) and ends a
misfit; a doubtful one is listed and not changed. The user had it applied to the
live store the same evening (`prax heal --check backwards-part-of
--apply`): 82 of 110 mended, 15 turned round and 67 ended, the 28
doubtful left. One turned edge reads as doubtful the other way round
("Lecture Course WS 2010/11 Distributed Problem Solving", a course cue
on its source); a doubt is never mended, so nothing flips back. The
rule pass is a pass of the nightly `prax maintain` from then on.

**Measured on a copy.** Of the 866 edges, 15 reversed, 66 misfits and 28
doubtful. Read by hand: reversed 13 of 14 right (one pair of unrelated
companies, "Palo Alto Research Center" and "Sony BA Laboratories",
turned by rank); the 50 misfits into a generic whole all right, the
other 16 right in 15; doubtful 12 of 21.
The rule pass then: 755 premises, 23 `part_of` derivations where there
were 110, in 1.1 s. Read by hand, 20 of them: 18 right (the sections of
"Getting Started With SuperCollider", the lectures of Distributed
Problem Solving, a lab in CNRS). The 2 wrong stand on a fact typed
organization at both ends that names a book, and on a course said to be
part of another course. One backwards fact still fans out: before
"manual" was a whole word, "BSD Library Functions Manual part_of
Archive_Write_New" alone made nine of forty derivations wrong.

## 2026-10-04, the evening: what a web page says about itself (stage AN)

Of the library's 559 open web pages, 328 describe themselves in
schema.org JSON-LD: articles and posts with their authors and publisher,
recipes with their ingredients and cuisine. The date reader saw 80 of
them. Its pattern wanted the script tag's `type` quoted, and the news
sites write `type=application/ld+json`, which is valid HTML. The
pattern now takes both; it lives in `prax.text.schemaorg.JSONLD`, and
`prax.text.dates` uses it. On a copy the `published` pass then dated 141
more documents, all by `jsonld`.

**The reader** (`prax.text.schemaorg`, no model). `nodes` finds every
JSON-LD object, a `@graph` opened. `own` picks the page's own work, the
most telling type first (`OWN_TYPES`: a recipe before an article), never
the site, its logo or a breadcrumb. `said` reduces it to names: authors
(people, and organizations apart), the publisher through its `@id`, the
date, and of a recipe its ingredients and cuisine. A news site holds its
recipes in an `ItemList` beside the `Article`; a recipe with
`recipeIngredient` is full, one without is a teaser for another page and
is passed over. An ingredient line becomes the names it holds, without
amount, unit, measure words, note or preparation: "500 g Zwetschgen,
entsteint" is Zwetschgen, "Salz und Pfeffer" two, "a generous pinch of
flaky salt" flaky salt (`ingredient_name`, over `prax.text.ingredients`).
Microdata is not read: 24 pages carry it.

**The `markup` pass** of `prax maintain`, nightly, writes the facts
(`markup_facts`) EXTRACTED with producer and run `jsonld`: the document
`authored_by` its authors, `published_by` its publisher with the
publication date as the fact's world date, and of a recipe `calls_for`
and `belongs_to`. A page with one full recipe is that recipe; a page
with several holds each as a recipe `part_of` it. A fact goes through
`check_edge` and `check_names` like any; what does not fit is counted.
Each page is stamped with its original's hash (`meta.markup`), so it is
read once and again only when a re-capture replaced it, the earlier
reading then ended. The stamp keeps the page's own schema.org type and,
where a genre says the same, the genre it suggests (`meta.markup.genre`:
recipe 59, article 53, news 42, forum 3).

**Genres in schema.org's words.** `same_as:` in `ontology/genres.yaml`
maps a genre to its schema.org type where one says exactly the same:
paper `ScholarlyArticle`, recipe `Recipe`, tutorial `HowTo`, review
`Review` and 13 more. An essay, a schematic or a contract has none, and
says nothing rather than something near. It is not given to the
labelling model, so it moves no measured number and no version.
`Facet.standard` and `Facet.label_for` read it both ways; the Review
page's vocabulary carries it.

**Measured on a copy.** One pass, 13.5 s: 315 pages with something to
file, 1,336 new facts (`calls_for` 751, `authored_by` 296,
`published_by` 272, `part_of` 11, `belongs_to` 6), 159 already stated
by another producer, nothing refused. A German recipe's `Mehl` lands on
the entity `flour`, which answers to that label since the vocabulary
pass. Read by hand, 40 facts: 36 clean; the rest were ingredient names
carrying a preparation or a count word ("Haselnüsse geröstet", "Köpfe
Chicorée") and an organization the page calls a person. The
ingredient names were cleaned in three rounds against samples of 30 to
50, and the last sample of 50 held 2 still doubtful ("mint sprigs
leaves", "tender plain white tofu").

## 2026-10-04, the evening: what changed in a period (AL step 5)

"As of a day" was there (`traverse(as_of=)`); "what changed between two
days" was not. `store.changes` answers it on either time an edge
carries, through `GET /graph/changes` and the MCP tool `changes`. On record
time (the default) it lists the facts prax came to hold in the period
(`added`) and those it stopped holding (`ended`); with `world=true`, the
facts that began and ended in the world then, as their sources state
(`began`, `ended`). A world date meets a period when their spans meet,
each read at the coarser precision: "2026" meets September 2026, and so
does "2026-09-14". Each side carries its count by relation and the
newest 20 facts with `left_out`; `entity` (its name or a label, with
what was merged into it), `rel` and `domain` narrow it. The rule pass's
derivations are left out unless `derived`, since it re-derives nightly.
A hidden document counts as absent, and `test_wall` checks it.

The first version listed re-readings as news. On a copy of the library,
the 4th of October had 2,684 facts added and 939 ended, of which 1,142
and 936 were `cites`: the references pass matching the same citations
again. A fact added that was already held when the period began, or
ended while another edge still states it, is now left out unless
`rereadings=true`. The same day then reads 1,648 added and 34 ended:
the day's real news was the schema.org facts and new documents.

The record-time period is `changed_between` in `store.graph.edges`,
beside `held_at`, so `test_a_moment_in_record_time_is_built_in_one_place`
still holds every record-time condition to one module. Migration 42
indexes `valid_from`, `valid_to`, `world_from` and `world_to`. Before
it, a day took 1.2 s and a year 7.2 s, scanning every edge; after it,
0.37 s and 2.2 s with the re-reading test. An entity's changes take
14 ms. An answer is 2 to 11 KB.

## 2026-10-04, the evening: how is A connected to B (stage AM)

An agent walking the graph spends a call and a context's worth of edges
on every hop. `connect(a, b)` answers in one call: the two or three best
paths of up to four facts, each hop with its relation, how many documents
state it, one of them and its quote, about 2 KB. The design was the
private research note's, section 9; its order was a prototype and an
evaluation set first, because a confident path of weak edges is worse
than none.

**The cost of a path.** A hop costs by its relation (strong ones such as
`cites`, `uses`, `authored_by` 1, the rest 2, `mentions` and `annotates`
3), times its confidence class (INFERRED 1.35, AMBIGUOUS 2), divided by
1 + log2 of the documents that state it. Passing through an entity costs
0.5 times log(1 + its degree), as inverse document frequency does in
text search; past 1,500 facts an entity is an end only. A path at or
under cost 6 is sound. Paths further away exist, are left out, and are
counted (`weak_left_out`, `best_cost`); `weak=true` shows them, marked.
The rule pass's derivations are no hop (a path already composes facts),
and neither is a briefing page: "What arrived" links a recipe to a DSP
paper by the day they came in. That was every path the first run found
between a recipe and a paper.

**The evaluation** (`scripts/eval_paths.py`, read-only). Pairs with a
known connection: a library paper citing another, as the references
pass matched it, with the direct facts between them banned; and two
documents linked from the same topic page, with the pages banned. Pairs
with mostly none: a recipe and a research paper, and two random research
papers. On a copy of the library (122,898 entities, 197,083 facts):

| set | pairs | any path | cost <= 5 | cost <= 6 | cost <= 8 |
|---|---|---|---|---|---|
| a paper and one it cites | 150 | 145 | 86 | 111 | 136 |
| two documents of a topic page | 91 | 84 | 32 | 41 | 61 |
| a recipe and a paper | 150 | 0 | 0 | 0 | 0 |
| two random research papers | 300 | 161 | 6 | 7 | 16 |

The weights came from a sweep of the hub cost (0.35, 0.5, 0.7) and of
the middle relations' cost (1.6, 2.0) against these sets. 0.5 and 2.0
kept the random pairs under 2% at the line with the most positives;
`published_in` moved to the middle, since "both published in arXiv" is
no connection. Read by hand, most random pairs under the line were real:
a shared author, a co-citation chain, the same method (two papers that
use hidden Markov models). The false ones passed through an institution
or a venue the extraction typed oddly. The topic pairs' paths read as
the survey's own reasoning: Self-RAG cites Toolformer, Mem0 and Basic
Memory both use Codex.

**The index** (`prax.graph.paths`, pure; the store's part
`store.graph.paths`). Compact arrays: entities as nodes, one fact per
source, relation and target in a CSR adjacency, and the edges behind each
fact with their documents. The two ends are grown halfway (two hops each
for four) and met in the middle; the paths are the best through distinct
entities. The wall is a filter in the loop: a fact is crossable when one
of its documents is visible to the viewer, and `test_wall` holds that a
named token gets no path over a personal document's facts. `as_of` builds
an index of the edges held then (0.9 s) and keeps none. The design
sketched a memory-mapped file with a delta; at this size it is not
needed. The build takes 1.2 s and some 31 MB (194 MB at its peak). The
door keeps one index per database file and rebuilds it when the edges
have changed and the one held is five minutes old. A query takes about
1 ms in the index and some 90 ms in all, most of it resolving the two
names (`senses`, as `traverse` does). CLAUDE.md's decision threshold
says when the file comes: ten times the facts, or a build past ten
seconds.

## 2026-10-04, the evening: one venue under many names (resolution's venue tier)

The `functional-conflicts` finding of stage AN listed 345 papers
"published in" two venues or more (200 shown, the rest past its cap).
Read, most were not conflicts: one venue under two names ("DAFx" and
"International Conference on Digital Audio Effects"), or a series beside
one of its editions ("NIME" and "NIME 2010", "ISMIR" and "ISMIR 2008 –
Session 3a"). The likely tier of resolution compares names by embedding,
and an acronym is far from its expansion there.

**Reading a venue name** (`prax.graph.venues.read`, no model). A name
becomes its series (the words that stay from year to year), its edition
(a year, written "2008", "'04", "-17", "23", or an ordinal, "26th",
"Thirty-Sixth", "3d") and its acronym. Taken off the series: "Proceedings
of" when what is left is a meeting or an acronym ("Proceedings of the
IEEE" and "Proceedings of the Musical Association" are journals and keep
it), a session after a dash, a volume or issue, and past the first comma
a place and a date when the part before carries the venue's own acronym
("DAFX 12, …, York, UK, September 17-21"; "IEEE Transactions on Systems,
Man, and Cybernetics" is one title with commas). A name of only months
or semesters is no venue. A plain number stays: "Lecture 6" and "Lecture
10" are two things.

**Grouping.** Two names are one series when a form of their series
meets: its words; the words without a publisher ("IEEE ICASSP"); for a
bare acronym, the expansions the library's acronyms table holds that are
venue-like and whose initials the acronym is; for a venue-like name, the
acronym it carries when its letters are the words' initials (ICASSP) or
when the name writes it in brackets ("… Digital Audio Effects
(DAFx-06)"). The rules came from reading what joined wrongly: every
company through "GmbH", "AI Magazine" with "AI & Society" (no acronym
under three letters), SIAM's journals with each other (SIAM is not their
initials), and OOPSLA under ECOOP, a wrong expansion in the table. In a
group, names of one edition merge into the most connected; one year with
two ordinals is two editions (the 122nd and 123rd AES Conventions were
both in 2007); every edition is `part_of` the group's bare series.

**In resolution.** The `venue` tier runs after the sure ones in
`resolution.plan`, on the door's clock with them (`prax resolve`), its
merges and links in the round's run, so `unmerge_run` takes it back
whole. The `functional-conflicts` finding now takes two values where one
is `part_of` the other for one answer.

**Measured on a copy.** 96 merges and 184 edition links in 0.2 s; a
second round plans nothing. The findings went from 345 to 247. The 96
merges, read one by one, held a few weak ones (two junk entities, "TUM"
and "in.tum", and the joint ICMC and SMC conference kept apart only
because its name says "joint"). Earlier likely-tier rounds had folded
some editions into "DAFx" itself; those stay until a person takes their
round back. Most of the 247 left are not venues at all: publishers
("Oxford University Press", "Wiley"), universities, dates, an exercise
sheet. That is a typing question for the extraction, and the reason the
contradiction pass waits.

## 2026-10-04, the night: `store.repair` in parts

`store.repair` had grown to 1,890 lines with today's ailments, near the
line CLAUDE.md draws at two thousand. It is a package of four parts now,
in this order: `common` (the `Ailment` shape, the caps on one pass, and
the repairs several ailments share: ending edges, closing review items
and jobs), `graph` (entities named after the prompt, mangled and split
names, loops, container citations, `part_of` the wrong way round,
functional conflicts, slow walks, stray versions), `documents` (twins,
files that are no documents, failed parses and readings, stale parses and
extractions, the edges and review items of retired documents) and
`ailments` (the list, `health`, `heal`). Its `__init__` re-exports every
name, so `store.repair.<name>` and `store.health` read as before.

The split was done by a script that moved each top-level statement whole,
and checked afterwards: all 87 statements of the old module compare equal,
syntax tree for syntax tree, with their copies in the parts. One test set
`SLOW_WALK_MS` on the package; it now sets it on the part that reads it,
since a name set on the package does not reach the part (as with
`retrieval.knobs`).

## 2026-10-04, the night: a heading is capped in a hit

Found on 2026-10-03 in doc 9522, a patent read in two columns: a heading
of over 2,000 characters rode along with every search hit and `ask`
passage of the document. Marker's re-read mended 9522 (its longest
heading is 119 characters now), but the shape is common to bad parses:
1,044 chunks in 30 documents carry a heading path over 1,000 characters
and 60 over 2,000, a book's epigraph or a page's running text read as a
heading.

`store.short_heading` caps the path where every read of a chunk is
shaped (`_chunk_shape`): each level cut at a word to 120 characters and
marked "…", and only the 4 nearest levels, the first marked "… " when
more were left out. Search hits, `ask` passages, the surfer's reads and
`get_chunk` all carry the short path; the chunk's text and the
document's outline (`sections`) read the full one. On a copy of the
library a search of ten hits that met such a document went from 5,805
to 4,085 bytes, its one bad heading from 1,879 characters to 180.

## 2026-10-04, the night: a publisher is no venue (`not-venues`)

After the venue tier, most of the 247 "published in two venues" findings
were papers said to be published in something that is no venue: a
publisher ("Oxford University Press", 24 edges), a company (a plug-in
maker for its manual), a university or a faculty, a semester or an
exercise sheet. The extraction typed them as venues because a venue was
what the relation asked for.

What a name says it is instead is a new section of the lexicon,
`not_a_venue`, read by `prax.graph.venues.not_a_venue`: `publisher`
(press, Verlag, publishing, the big houses by name), `company` (GmbH,
Inc., Ltd, AG, LLC), `institution` (university in five languages,
institute, faculty, department, laboratory, conservatory) and `none`
(dates and semesters, exercise sheets, lectures, licences). A name with a
venue word (journal, proceedings, conference, review, letters…) is a
venue whatever else it says, so "Journal of the Audio Engineering
Society", "Proceedings of the IEEE" and "Psychological Research" stay; so
does "Acta Universitatis Upsaliensis", which no cue matches. The first
cut used the lexicon's general organization words and caught journals on
"systems", "research" and "technology"; these are narrower.

The `not-venues` ailment of `prax heal` lists them. Its repair ends each
`published_in` edge into one and writes what it meant beside it:
`published_by` the publisher or company, `written_at` the institution,
both of an organization by the same name, with the old edge's document,
evidence and world dates, INFERRED, producer and run `heal:not-venues`.
A date or an exercise sheet gets nothing. On a copy: 291 entities (105
institutions, 84 publishers, 42 companies, 60 nothing) and 738 edges;
after the venue tier and this, the findings went from 345 to 157. What
is left is the material of the contradiction pass: real disagreements
(a paper in DAFx-14 and in ICASSP), junk values ("DRAFT"), and spellings
no rule folds ("Applied Sciences", "Apl. Sci.").

## 2026-10-04, the night: two facts that cannot both hold (the conflicts pass)

AL step 4's last item: a pass that keeps the disagreements of a
functional relation visible, never ending a fact for them. The plan said
"`contradicts` edges between facts", but an edge of the graph joins two
entities, and a conflict joins two facts; an edge between "DAFx-14" and
"ICASSP 2014" would say something false about two conferences. So the
conflicts live beside the edges, as the rule pass's premises do:
`edge_conflicts` (migration 43), the two edges, the relation, producer
`rule:functional`, run `conflicts:<relation>`, when found and when ended.

The `conflicts` pass of `prax maintain`, nightly, after the rule pass
(`store.find_conflicts`): per relation the ontology calls functional
(`published_in` today), the live asserted edges grouped by subject, the
values by their root under `part_of` (`store.part_of_roots`, which the
`functional-conflicts` finding now shares), one edge standing for each
value (an EXTRACTED one first, then the oldest), six pairs a subject at
most. Pairs that disagree and were not recorded are added; recorded ones
that no longer disagree (an edge ended, a merge, a `part_of` that joined
the two values) are ended. `traverse` marks a fact another contradicts
(`disputed`: how many), counting only those the viewer may see, and `why`
lists them with their documents and quotes (`store.edge_conflicts`,
scrubbed by the wall).

On a copy after the venue tier and `not-venues`: 224 conflicts in 0.25 s,
a second pass 0.18 s with nothing changed. Of the 214 between open
documents, 148 pair two values one document gave ("huggingface.co" and
"GitHub", a journal with and without its volume and pages): an
extraction's own ambiguity, not two sources at odds. The other 66 are
documents disagreeing, some for real (a paper in NIME 2011 and in ITS
2010). A person reads them; nothing is decided by the pass.

## 2026-10-05, the night: the review of three days, and its fix round

**The review.** A workflow of ten agents read everything changed since
`1c650c4^` (89 commits, migrations 35 to 43), five reviewers by area
(data safety, the wall, the new logic, contracts and sizes, docs and
tests), each finding re-checked by a skeptic told to refute it: 27
confirmed, none refuted, one found twice. They were fixed in five
commits, each fix with a test in `tests/test_review_fixes.py` that fails
on the code the review read.

**The worst one, caught before it ran.** `retire_reading` drops a
document's open review items as well as the producer's edges, right for
an extraction that queues its misfits again, wrong for a pass that
queues none. The new `markup` pass would have dropped all 2,100 open
items on HTML documents on its first night; it had not run on the live
store. The older `references` pass had done it since 2026-09-20. Both
now pass `reviews=False`. The items it dropped were found by the time
windows of the maintain jobs that ran it (after the `review` pass in
each), less those whose document was re-extracted then and those open
again since: 2,617, reopened through the door (`store.reopen_reviews`,
`POST /review/reopen`), 2,615 of them (two were queued again in the
meantime). The open review queue went from 21,292 to 23,907.

**The nightly maintain had failed since 2026-09-30**, every night, at
`UNIQUE constraint failed: index 'idx_entity_labels_one'`, about seven
minutes in: where the `languages` pass starts. It is the failure
`e354c54` fixed on the afternoon of 2026-10-04; a full maintain on a
fresh copy of the live store (SQLite's backup, read-only on the live
side) ran clean in 528 s. The passes after `languages` (published,
private, names, attachment, communities, histories, and since tonight
rules, conflicts and markup) had not run at night for five nights.

**Data safety.** A derived edge a merge folded onto another pair was not
tracked, so it outlived its premises: the rule pass now tracks every one
on a pair. The heal repairs were not undoable: retiring their run ended
the replacements and could not bring back what they had ended. A repair
now records the edges it ends (`edge_endings`, migration 44), and
`store.restore_run` (`POST /graph/restore`) undoes the run whole, the
ended facts stated again as new edges. The `backwards-part-of` repair
applied on 2026-10-04 predates the record and cannot be undone this way.
`unmerge_run` now ends the edges its round wrote (the venue tier's
edition links).

**The wall.** Seven places where hidden was not absent: a rule-derived
edge quoting a premise from a hidden document (hidden now with any such
premise, `store.hidden_by_premise`); `why` on a hidden edge; staleness
from a hidden document's `supersedes`; `changes` telling a hidden-only
name from an unknown one, and a hidden edge making a visible one a
re-reading; a capture landing on, naming or giving domains to a hidden
duplicate; path costs and hub prices counting hidden witnesses. On this
library the one named token sees every module, so the module half of
these did not apply; the personal half did.

**Contracts.** `references` returned a book's whole list, 7,249 entries
and 2 MB in one call: now 50 a page (at most 300), with `offset` and
`left_out`, numbered entries in their numbers' order (the client's N3).

**Logic.** `connect` could return a path crossing one fact twice and
could lose a path to a node reached more cheaply in more hops: it grows
layer by layer now and meets only simple paths, the evaluation unchanged
(111/150, 41/91, 7/300, 0/150; 3.5 ms a query). Two editions of one
series were taken for one answer: only an ancestor makes one now
(`part_of_ancestry`). The venue reader merged IEEE MultiMedia into ACM
Multimedia and read "24th" as 2024; lexicon cues ending in punctuation
never matched; "1/2 cup milk" became an ingredient "/2 cup milk";
`traverse` had lost its read guard to a helper inserted above it;
`changes` left out an edge written in the very second of `since`.

**Docs and tests.** A refused `POST /ingest` or `PUT /page` had already
written its document, open, though it asked to be personal: both check
first now. The flaky `test_up` was the status file read while being
replaced, two writers sharing one temporary file: one writer at a time,
a file each, and a reader that tries again. Two tests leaned on timing
(a 50 ms sleep, a period from a later `now()`).

**The client's second page** (doc 13470) came in during the round: N3 is
in; N1, N2, N4 and G1 to G4 are AL step 9 of the plan.

## 2026-10-05, the night: the quality review, and its first step

The project's skill `.claude/skills/code-review` ran in full mode over
the same three days: five reviewers by dimension, a skeptic each. 24
findings confirmed (three found twice), 2 refuted; all of them, with
evidence and fixes, are in `docs/eval/code-review-2026-10-05.md`, and
their four steps in `docs/PLAN.md`. Step 1:

- **What "the same venue" means** (finding 1). `ontology/sameness.yaml`
  told every judge that a series and its editions, and two editions, are
  one thing; the venue tier keeps them apart and links them `part_of`,
  and the likely tier still offered such pairs to the model, which could
  undo the tier (the log of 2026-10-04 notes editions folded into DAFx
  that way). The rule now says they are two (its version 2), and the
  likely tier never offers two venue names of different editions. The
  judge's measurement with the new wording (`steps.adjudicate.platt`)
  waits for the card.
- **`cited_but_missing`** (finding 2): the planner, with no statistics,
  walked the kind index over all 158,645 reference chunks for a handful
  of documents (65-90 ms). `+kind` was quicker warm but read every chunk
  of the documents cold (1.5 s for 500). A partial index of the
  reference chunks by document (migration 45), named in the query:
  0-23 ms.
- **`cite`** (finding 14): the phrase query is bounded by the
  document's range of chunk ids, so the keyword index walks its
  postings, not the library's. On a copy, a common phrase in a book of
  13,680 chunks: 31 s unbounded, 43 ms bounded, the same result. The
  in-memory fold checks the budget as it goes.
- **The path index** (findings 12, 13): its stamp was one query of both
  maxima, which scanned the edges table (62 ms a call): two indexed
  subqueries now, and none at all while the index is fresh. An index of
  a past moment (`as_of`) is kept, the last two by moment and stamp, and
  built under the lock, so two requests cannot stack two peaks of 150 MB.
- **A named token's hidden set** (finding 24) is read once for a state
  of the store (the viewer, the file, `data_version`, the connection's
  own changes): a search had scanned every document two or three times.
- **"1/2 cup"** (finding 10) is an amount in the general ingredient
  parser too, not only in the schema.org reader; recipes need a
  `--rechunk` for their ingredient chunks to read it.

## 2026-10-05, the quality review's second step: one rule in one place

Five rules that were written twice, each now written once
(`docs/eval/code-review-2026-10-05.md`, findings 3, 4/23, 16, 17, 18;
the tests are in `tests/test_quality_fixes.py`, `tests/test_importers.py`
and `tests/test_markup.py`).

- **The project sync** (finding 3). `prax import project` had its own
  key, title and file walk beside `POST /projects/sync`, so the same
  project sent both ways could become two sets of documents. It is now
  `prax sync --apply --all-files`: one request to the door. The importer
  keeps only the `.prax-project` reader. The door still finds a document
  by its old `name/path` key, so the earlier imports are refreshed, not
  added again. One change in what it does: the first import of a
  directory now saves a project manifest and its page, as a sync always
  did (the plugin's `/archive` command imports the memory folder this
  way).
- **A functional conflict** (findings 4 and 23). The heal check
  `functional-conflicts` counted rule-derived edges; the conflicts pass
  did not. Both now read `store.functional_breaches`: the subjects whose
  live asserted edges give two answers, finer and coarser being one.
- **A page's date in JSON-LD** (finding 16). The dates reader walked the
  JSON-LD itself and took the first `datePublished` anywhere, so a list
  of linked articles above the article dated the page. It now reads
  `schemaorg.nodes` and prefers `schemaorg.own`.
- **An equation's number** (finding 17). The chunker's pattern did not
  know the separators the maths pack learned on 2026-10-02 (`, (3)`,
  `~(3)`, two spaces); the pack had its own. Both use `markup.eq_number`
  now. A `prax maintain --rechunk` fills `data.number` for the formulas
  written in those forms.
- **What is stale** (finding 18). The stale states and the relation for
  each were in `text/status.py`, `store/retrieval/fusion.py` and
  `capture/projects.py`. They are `status.STALE`, `status.STALE_RELS` and
  `status.stale_rel` now.

## 2026-10-05, the quality review's third step: words in data

Findings 6, 7, 8, 9/15, 11, 19/21 and 22
(`docs/eval/code-review-2026-10-05.md`); their tests are in
`tests/test_quality_fixes.py`.

- **A pack's lexicon.** A pack may now carry a `lexicon.yaml`, sections
  beside the core file's, read with `ontology.lexicon().section(name)`;
  a section two files hold is refused. A YAML trap came with it: a bare
  `on` in a word list is the boolean true, and a test now refuses any
  boolean in a lexicon.
- **The venue words** (findings 6, 9, 15) moved from `graph/venues.py`
  into the research pack's lexicon (`venues:`): the leading phrases, the
  abbreviations, the stop words, the venue and event words, the
  publishers before a series, the ordinal suffixes and the spelled
  ordinals. The month names come from `prax.text.dates`, and the legal
  forms from the core lexicon's company cues. The move was checked first
  on its own: the plan over the live names was the same with the old
  module and the new one. French, Spanish, Italian and German words were
  added after it ("Actes du", "Actas del", "12e", "3er", congrès,
  congreso, Tagung…): on this library they add one merge, two names of
  one venue apart only by an article (both behind the wall, read by
  their shape, not their names).
- **`scripts/eval_venues.py`** measures the tier against the Zotero
  records of the papers that name a venue. Of the 92 merges of the
  night's run, 5 have a record behind them and all 5 agree; of the 183
  edition links, 27 do and 22 agree, the other five records naming a
  session, a place or a university. `--save` and `--diff` show what a
  change to the words moves before it merges at night.
- **The status reader** (finding 7) wants a status line's shape now: a
  key, or the state word alone, emphasised, in capitals, or before a
  colon, a dash, a date or a word like "by" or "since". "Archived copies
  of the datasets live on the NAS" no longer retires a note. The words
  are the core lexicon's `status:` section, with German and French.
- **The ingredient words** (finding 8) are the craft pack's, one list a
  language. A page's `meta.lang` picks the list; a page never indexed is
  detected, or scored by whose ingredient words its lines use. A
  language with no list gives no `calls_for` facts from markup rather
  than "de farine". On the 61 recipe pages of the library the names are
  the same as before.
- **A document as a node** (finding 11): page links, a project's
  members and citations now type a document as `store.document_node`
  does. A document with no entity yet is the one type its domains allow
  (a research document is a `paper`), else a `document`. 733 titles
  already have twins of two document types; folding them is in the plan.
- **A relation's strength** (findings 19, 21) is `strength: strong|weak`
  in its module, checked by `lint`, bumping no version; `paths` reads it
  from the composed ontology. The map is the old lists exactly, less
  `evaluates`, which no module declares, and `compares`, an alias.
  `eval_paths.py` on today's store: cites 106/150, topic 39/91, random
  5/300, recipe-paper 0/150 (111, 41, 7 and 0 on 2026-10-04, before the
  night's merges and the not-venues repair, which ended 739
  `published_in` edges some paths ran through).
- **A page's own schema.org type** (finding 22) may be any type a genre
  names (`genres.standards()`): a Q&A page, a thesis or source code now
  gives its author. Pages already read keep their stamp until they are
  captured again.

## 2026-10-05, the afternoon: the client's second page, and a judge measured again

**The judge's re-measure.** The sameness rule's version 2 says a series
and its editions are two things. Asked again on the person's 296
decisions (`eval_confidence.py gold/ask/platt`, the 27B), the local
model agrees 0.77 as before, its calibration a little better (ECE
0.043, 0.050 on 2026-10-03); at the 0.95 line it settles 11% of pairs at
34 of 34 (16% at 47 of 47 before). Of the 18 venue pairs, three are two
editions apart: the person had called two of them one thing before the
rule said otherwise, and the judge now calls all three two. The fit is
in the host's `prax.yaml` (`steps.adjudicate.platt: {a: 0.4877, b:
0.4917}`).

**The client's second page** (AL step 9, `tests/test_al_step9.py`):

- **G4.** A door that restarts forgets its workers: one in the middle of
  a batch asks for nothing until it is through, and the client read
  `alive: false`. `worker_state` now says why, from the supervisor's
  status file: up and not asked since the door started, stopped, or no
  supervisor at all.
- **N1.** A named token may now plan any project (`sync_project`
  dry run) and keep in step one the administrator registered, never its
  settings, and only within the token's modules; a project holding a
  note behind the wall is as if absent. Before, the client fell back to a
  scratch script and the old keys.
- **N2.** A citation's words are taken where the passage answers: around
  the query's words first, the runs with more words of their own before
  the others, and never a run holding a notice word (the lexicon's
  `notices`: copyright, permission, licence…).
- **N4.** A page number is no year ("pp. 1917–1930" dated YIN 1917); an
  author run of whole names gives each name's last word as the surname
  (it gave every first name too); a missing work with no DOI or arXiv id
  gets a Crossref search link; `min_count` leaves out what fewer of the
  set cite, and among works cited as often the one the rest of the
  library cites less comes first (`cited_in_library`: Adam 58, Bello's
  tutorial 36). The stored entries keep the old reading until a rechunk.
- **G1.** A citation the references pass matched by title with a score
  of 0.95 or more costs as a stated fact on a path. `eval_paths.py`:
  citation pairs at cost 4 or under 36 → 55, sound ones 106 as before,
  random pairs 5 of 300 as before. The client's SuperFlux to adaptive
  whitening chains cost 6.1 now (6.45), over the line by their middle
  papers' hub costs; moving the line is in the plan, for the user.
- **G3.** `changes` leaves a repair's corrections out unless
  `corrections`, and counts them per side as `corrected`.
- **G2.** The `duplicate-facts` check of `prax heal` ends the later edges
  of one fact stated twice by one reader from one document (1,711 facts,
  2,018 edges on the dry run); the same fact from two readers stays.
  Waiting for the user's word, like `document-twins` (356 documents, 450
  entities folded into the type `document_node` gives).

## 2026-10-05, the evening: the review's two plan items, and roman editions

- **A swap is a record** (finding 20). `prax up` appends one line to
  `run/swaps.jsonl` for each swap and each give-back: when, the group,
  the role, the others, and why (a person, the plan's reason, nothing
  left waiting). `scripts/eval_swaps.py` puts a day's swaps beside its
  reading waits. The waits of the last days, before the record began:
  a median of 0.3 to 0.4 hours until 2026-10-02, 16.6 on 10-04 and 37 on
  10-05, the figure readings' backlog (about 1,650 waiting).
- **Which edge corrects which** (finding 5). `edge_endings` has a
  `corrected_by` column (migration 46), set by the `backwards-part-of`
  and `not-venues` repairs to the edge they write in an ended one's
  place, and `why` on either edge says so (`corrected_by`, `corrects`).
  The repairs already applied before it say nothing.
- **Roman editions.** A roman numeral before a meeting's word is an
  edition ("Atti del XX Colloquio", "IV International Conference"), and
  then no acronym; alone it stays a name (CHI, MIX). `eval_venues.py
  --diff`: no merge and no edition link moved on this library.

## 2026-10-05, late: the line of a sound path, and two repairs applied

- **`paths.SOUND` is 7** (6 before), the user's call after the random
  pairs it lets through were read (`eval_paths.py --show random:6:7`,
  a new option): five of seven are real connections (two papers citing
  one work, two about tangible interfaces), one runs through a
  university only, one through a wrong authorship. On today's store:
  citation pairs 106 → 126 of 150, topic pairs 47 → 59 of 91, random
  pairs 5 → 12 of 300, recipe-paper 0. The client's SuperFlux to
  adaptive whitening chains (6.1) are sound.
- **Applied, on the user's word:** `document-twins` folded 450
  entities of 356 documents into the type `document_node` gives
  (`unmerge_run` of its run takes it back); `duplicate-facts` ended
  2,029 later edges of 1,722 facts (more than the dry run's 2,018: the
  folding made some new duplicates), recorded for `restore_run`. Both
  checks are clear.

## 2026-10-05, late: two old venue decisions, and a joint conference

The re-measure turned up two of the person's venue decisions of
2026-09-28 that the new rule contradicts; asked, the owner answered both
as two things. The joint ICMC/SMC conference (one meeting, held once, an
edition of each series) had been merged with the 8th SMC, and the
Computational Creativity series with its second edition. Both merges were
taken back through the door (`POST /graph/undecide` with their runs) and
both pairs recorded as different (`POST /graph/decide`). The venue tier
links the editions tonight: the 8th SMC and the second Computational
Creativity each `part_of` its series. A joint meeting is now an edition of
every series whose acronym it states (`Venue.others`), never merged into
either: `eval_venues.py --diff` adds exactly one link on the library, the
joint conference `part_of` the SMC series beside ICMC.

## 2026-10-05, the evening: the list without the card

- **N3's rest** (AL step 9). A paragraph under a references heading
  that is a biography ("**Juan Pablo Bello** received…") or long prose
  with at most one year in it is ordinary text now, not the entry before
  it; a run of author-year entries in one paragraph carries a year each
  and stays a reference. An entry that opens with a ditto mark (`~~,~~`,
  `———`, "idem") takes the entry before's surnames. On the live lists:
  354 reference chunks of 209 documents held such prose, and 1,041
  entries are ditto marks; a rechunk applies it. Read before: the first
  rule took in runs of entries too ("SINGER, Rolf … 1958a …"), which is
  why the year count is part of it.
- **AL, as Utopia does it.** Migration 47: triggers hold a world date and
  its precision together (a year is four characters, a month seven, a
  day ten; "ended, date unknown" has no date), and refuse a change to an
  edge's world dates; none of the 271 dated edges broke it. `traverse`
  anchors a fact whose source gives no world date to its document's
  publication date (`stated`), said only where the document is dated.
  An agent's `get` shows a document's lifecycle: `published`, `status`
  (what it says of itself), `stale` (no longer current, and what replaced
  it), `retired`.

## 2026-10-05, night: the extraction prompt measured, and a chunker slip

- **Standard names and world dates in the extraction prompt**, each
  behind a switch (`extraction.standard_names`, `extraction.world_dates`)
  and measured with `bench_extractor.py` on the 27B, the card shared with
  the figure readings: standard names change nothing measurable; world
  dates came back on no triple of five documents that state dated facts.
  Both stay off (`docs/eval/extraction-standard-names-and-dates-
  2026-10-05.md`). The code stays: a triple carries `world_from`/`to`, the
  line format and the schema take them, and a date lands only when its
  year is in the quote. The bench shows dates given and kept.
- **A biography's second paragraph** was still joined to the entry
  before the biography: it opens no entry, so it was taken as a wrapped
  title, and the join reached back over the biography in between. A
  paragraph continues an entry only when no prose waits between them.
  After the rechunk Bello's list holds no biography; 862 ditto entries
  carry their authors.

## 2026-10-05, late night: a search that failed for "matrix"

Found while measuring on a copy: every search with "matrix" in it failed
with "no such column: size", on the live store too. The query expansion
adds a word's graph labels, and one label of "matrix" was an extractor's
wire syntax (`matrix dst_type=concept(confidence=extracted
evidence="matrix size: 1024"…`). Its quote closed the phrase the
expression had opened, and FTS5 read "size:" as a column. Each
alternative of the expression is now its words as the index tokenizes
them, and an alternative from a label that holds "=" is left out: 2,262
labels on 2,087 entities hold such syntax, and 390 live entities have it
in their name (cleaning them is the user's call).

## 2026-10-05, late night: the facts list, measured and left out

The graph's facts as one more keyword list in the fusion (an FTS5 index
over each live edge's names, relation and quote; one vote per document
at its best fact) was built on a copy of the store and measured on the
62 library questions: hybrid MRR 0.90 without it, 0.89 with a full vote
(keyword questions up, paraphrases down), 0.90 with half a vote. Not
shipped: some 48 MB and a trigger on every edge write for no measured
gain. The set has no fact-shaped question, which is where it should
help; `docs/eval/retrieval-facts-list-2026-10-05.md`.

## 2026-10-06: the wire syntax in labels, a check of its own

The labels an extractor's wire syntax got glued to (2,296 rows) are of
three sorts. 1,776 are the preferred labels of entities whose own name
holds the syntax: `wire-names` renames those (371 entities on the dry
run). Some are `was` labels, what an entity used to be called, kept as
history. The rest are the new check's, `wire-labels` (410 on the dry
run: 385 whose words the entity already has as a label, 25 whose words
become a label): each is set aside as `kind = 'wire'`, never deleted
(`store.set_aside_label`), and the words before the syntax written
beside it under the run `heal:wire-labels`. Search already ignores them
(2026-10-05); this is the record made clean. Applied on the user's word,
names first; on a dry run after, both checks found nothing.

## 2026-10-06: AJ's reader contract

A reader is now a manifest, `prax.host.readers`, in the way a pack is
one. `readers.MARKER` names the roles marker needs (`marker`, and
`ocr-server` for surya's OCR model), what each holds (card, RAM, load
time), the settings prax passes it, the lock file of its venv, and the
logs it writes itself. The door's demand reads which role a marker
reading waits for from it (`work.ROLE_WORK`).

- **A companion moves with its role.** `ocr-server` is a served model
  like the reranker, and its `model:` must be `datalab-to/surya-ocr-2`,
  the name surya checks. `roles()` gives it marker's group and
  `on_demand` and starts it before marker. Marker gets
  `SURYA_INFERENCE_URL`, so surya spawns nothing behind `prax up`'s
  back. `Supervisor._party` is a role and its companions: `--start`,
  `--stop`, a swap and the give-back take the party, and a swap to the
  companion is a swap to marker. `_fits` adds up the party's card and,
  when the host can say, its RAM. The plan's swap cost is the slowest
  load of the party, with the manifest's `load_s` as the guess.
- **The pinned venv.** `src/prax/host/locks/marker.txt` holds the
  desktop's 89 packages (marker-pdf 2.0.0, surya-ocr 0.22.1). `prax up`
  says at marker's first start where the venv differs, and the status
  carries `drift`. `prax up --lock marker` writes a measured upgrade.
- **Its own logs.** Surya's `llamacpp_server.log` stood at 74.5 MB.
  `readers.trim_log` cuts each of the manifest's logs to its last 2 MB
  before the role starts and after it ends, never while the process
  holds the file open.
- `serve.kv_type` (default `q8_0`) lets the OCR server run f16 as surya
  does.

Not measured: the manifest's numbers are the night of 2026-10-02's.
The desktop's prax.yaml now names a `surya` model (port 8766, 8 slots
of 12,288, f16 KV, no prompt cache, `--jinja`, as surya ran it) and the
`ocr-server` role. It is live at the next start of `prax up`. Peak RAM,
VRAM and time per page are taken on the next marker evening.

## 2026-10-06: AK's split, as configuration

Stage AK measured the 27B as the better surfer and the 35B as three
times faster at the bulk passes, with one card for both. Two chat
servers can now share it:

- **A further chat server** is the role `llama-server-<name>`
  (`roles.is_chat`), with `llama-server`'s settings. `roles.ordered`
  starts it right after `llama-server`. Served roles take `on_demand`
  now, so the ask's server loads only when asked for.
- **Each step waits for its own server.** `work.role_of_step` looks at
  every chat server and the reranker. A reading waits for the role of
  its step's model (`work.role_work`, which replaces the constant
  `ROLE_WORK`): a figure for the role `vision` names, a formula reading
  for the role of `formulas`.
- **An ask wins the card for its role.** The demand names `ask_role`.
  The plan puts an `ask` group next even past a serving holder ("an ask
  waits for it"). `prax up` swaps for it whatever the role's `swap:`
  says, and the ask hold no longer stops that swap: before, an ask that
  found its server down set the five-minute hold and so blocked the very
  swap it needed. `work.asking` now records no hold for an ask that
  raised `ServerNotReady`. While the hold lasts, the ask's role keeps
  the card. `Supervisor._crowded` stops an idled server from loading
  beside a group member that holds the card: that is the plan's swap.

The host still runs the 27B for every step, and stays so (the user:
"right now I think we can stay with 27b for most tasks"). The split is
there for when a step wants the 35B's speed again. The 35B's files
were deleted the same night for disk space, on the user's word (20.9 GB
of weights and its 0.9 GB projector; C: from 46 to 66 GB free). The
`server-35b` entry stays in prax.yaml with the download line beside it.

## 2026-10-06: the document twins' second source, the extraction itself

The 450 twins folded on 2026-10-05 were blamed on page links and
citations typing every document a paper. A new twin since then had
another cause. Doc 13559, a recipe, was one extraction run
(`work-20261005T235243`, the 27B) and two entities: `recipe` on its 11
`calls_for`, `makes` and `authored_by` lines, and `document` on its
`published_by` and two `needs` lines. Those two relations declare their
domain as `document`, and the model wrote the domain's type. Both are
accepted, because a subtype passes where its parent is allowed, and
`store.link` keys an entity by name and type.

`extraction.apply` now gives the document one type per extraction
(`_one_self_type`). Every triple that names it by its title as a
document type takes the most frequent type more specific than
`document`. A tie goes to the type the graph already gives it
(`store.document_node`), and a reading that says only `document` takes
that type too. Every extractor's output goes through `apply`, so
Claude's are covered. The twin was folded first (`heal --check
document-twins --apply`, 1 of 1). The fix is live in the door at its
next restart.

## 2026-10-06: follow-up questions, measured; the focus vote left out

A set of follow-up questions did not exist, so the plan's focus-entity
vote could not be measured. `tests/eval/followups-library.yaml` holds 30
now (19 research, 5 kitchen, 4 studio, 2 computing): a first question
and a follow-up that leans on it, the expected documents chosen by
reading them and never from the graph's edges. `scripts/eval_followups.py`
runs it on a copy of the store.

Its first number is for what `ask` already does. Adding the previous
question's words to a short or pointing follow-up (`ask.search_query`)
takes MRR from 0.510 to 0.716 and hit@10 from 0.70 to 0.90.

The vote did not add to that (MRR 0.689 to 0.701 at weights 0.5 to 2),
and it is not in the code; its diff is beside the report
(`docs/eval/followups-focus-vote-2026-10-06.md`). Finding the focus took
one rule more than planned: a lone word held by more than 100 document
fields is generic, because "string" and "method" are each a dozen small
entities of different types. In the two cases the vote lost, the right
paper (docs 9778 and 9782) states no fact about the method it builds on,
so the vote raised the papers the graph does connect to it.

## 2026-10-06: names that differ only in spacing, a heal check

The plan kept "entities that differ only by case or punctuation" for
when they showed up. Case, punctuation, accents and a plural are the
resolution's sure tier already (`names.normalize`), which keeps word
boundaries. What showed up is spacing: 827 groups of one type whose
names match with the spaces taken out ("Valhalla DSP" and "ValhallaDSP",
"sub-pattern" and "subpattern", "Proceedings of DIS2002" and "… DIS
2002"). Many come from PDF text, which runs words together or spaces
them apart and leaves accents detached ("Fakulta¨t").

`prax heal --check spacing-twins` folds each group into one entity under
one signed run (`unmerge_run` takes it back). It skips names under six
characters joined up and names whose numbers differ ("SSL4000" and "SSL
400 0"). On the copy that is 783 groups, 793 entities to fold, in 4 s.
The keeper is chosen by its spelling, because its name is the one shown
(`_keeper_rank`). First come no detached accents and the smallest share
of letters in words the library's text does not use: "Csound" over "c
sound", "Collaborative Music-Making…" over "CollaborativeMusic
MakingwithInteractiveTabletops". Then not all capitals, then the most
edges. Ranking by edges alone kept the damaged spelling in five of
the first twelve, and preferring words apart kept the exploded ones.
Where both spellings are damaged the keeper is too; `mangled-names` is
the place to mend that. The dry run waits for the user's word.

## 2026-10-06, the afternoon: the readers measured, and readings lost since September

With the card (the user: "work on all the tasks that need the card"):

- **Marker with its OCR server as a role** read the 15 mathematical
  PDFs it had not read, 544 pages, at 1.67 s a page. The OCR server held
  1.4 GB resident and 5.9 GB of commit, against 10.3 and 13.3 GB on
  2026-10-02. The manifest's numbers are the measured ones now, marker's
  card 0 in fast mode (its torch is the CPU build), and a need of 0 is
  no longer read as "unknown" by the fit check.
- **The three OCR readers** on 20 pages made from born-digital PDFs,
  their text layers the reference: word error rate 0.203 for RapidOCR,
  0.124 for marker, 0.145 for vision-pages with the 27B; recall 0.924,
  0.922 and 0.943. None is retired; the routing is in the plan.
  `docs/eval/ocr-readers-2026-10-06.md`.
- **The 27B loaded beside marker.** Two formula readings asked for it
  while marker held a card it had taken "beside nothing", the 27B idle
  at the time, and the rule that an idle server waits for a lent card
  covered only a loan that did not fit. Free commit fell to 628 MB
  before it was paused by hand. The rule now holds for every loan, with
  a test for the loan that fit.
- **Figure readings lost to a replacing read.** The readings are lines
  under each figure in the text, and marker wrote its own text without
  them. 35 documents had lost 710 readings since September, 30 of them
  on earlier marker evenings. `apply_parse` now carries them over to a
  replacing read's text, from the current text or an earlier one the
  parse record names (`figures.carry_readings`, `store.earlier_texts`).
  `prax heal --check lost-figure-readings` restores the rest without a
  model; applying it waits for the user's word.
- **A manual taken for a paper.** The *Voron Cascade Assembly Manual*
  (doc 9829) scored 18 on maths density for its "(4)"-numbered steps and
  went to marker in this set. Its `pymupdf4llm` text was read again, and
  its 60 figure readings come back with the heal check.

## 2026-10-06, the evening: readings restored, commit in the fit check

- **Restored.** After the restart, `prax heal --check
  lost-figure-readings --apply` repaired 35 of 35 documents, and the
  check is clear. The Voron manual has its 60 figure readings back.
- **The fit check knows commit.** Each role's peak committed memory is
  read off its job object (`PeakJobMemoryUsed`, the whole process tree
  and, on Windows, its VRAM), kept in `run/commit.json` across runs. On
  Windows a load must leave `COMMIT_RESERVE_MB` (4 GB) free. The
  morning's case: the 27B's 18,379 MB beside marker would have left
  1.6 GB, so it waits. The manifest gives marker's 9.2 GB and the OCR
  server's 6 GB until they are measured here.
- **No manual is mathematical.** `--maths` leaves out a document labelled
  `manual` or `datasheet` (`p` 0.5 or a person's label): their "(4)"
  are steps and footnotes. On the evening's set that drops exactly the
  three it should have (the Voron manual, an army field manual, an
  op-amp datasheet) and none of the 13 papers, books and reports.

## 2026-10-06, the evening: counted drops

What an extraction leaves out without a review item is now said: an end
without a name, a confidence that is none of the three, a reference
number or a placeholder for a name, an unmapped citation without a
title, a guess the model hedged (`graph.extraction._drop_reason`,
`noise_reason`). Each reason has a count and its first example on the
extraction's stamp (`meta.extraction.drops`). `store.extraction_drops`
sums them over the library, without the documents a viewer may not
see, and the door serves it beside the review queue (`GET
/review/drops`). The `rejected` count is what it was. The numbers fill
in as documents are extracted again.

## 2026-10-06, the evening: precedents for the judge, measured

The local judge of a likely pair was shown the person's decisions most
like it (`resolution.precedents_for`, `eval_confidence.py --precedents
K`, the pair's own left out). On the 240 decided pairs of entity types,
six precedents raised the cross-validated agreement from 0.762 to
0.800. At the 0.95 line the nightly judge acts on, the plain prompt
settled more (32% against 29%) and was right as often (74/76 against
67/70). Not adopted; `docs/eval/judge-precedents-2026-10-06.md`.

Found on the way: `gold` took the heal check's `split-names` pairs as
decisions on names. They are two entities of one name a person kept
apart on their documents, 56 of the 296, and with precedents the judge
was confidently wrong on six of them. The fit of 2026-10-05 was made
with them. `gold` now keeps pairs of an entity type only. The fit on the
240 settles 32% of pairs at 74/76 against the one in use at 11% and
34/34. Changing it in prax.yaml waits for the user's word.

## 2026-10-06, the evening: what the chapter summaries are worth to `ask`

30 questions about the middle of long documents
(`tests/eval/questions-sections.yaml`, written by a subagent from a
passage 44–51% into each text, never from the summaries), asked of two
doors: the live one, and one on a copy whose 2,006 sectioned documents
had their field rebuilt without the section lines. `eval_ask.py --steps
0`. On the 17 sectioned documents, 17 against 16 were among the sources
and cited. On the 13 without sections, where the doors differ in nothing
that matters, two questions changed. So the summaries make no
difference to `ask` that asking twice does not also make: a book's
middle is found through the chunk that holds it.
`docs/eval/sections-ask-2026-10-06.md`. The sections stay for search,
where they were measured to help.

On the way, stopping the copy's door by command line also stopped the
shells around it; the live `prax up` was untouched, its pids the same.

## 2026-10-06, the night: evidence that knows where it stood

An edge's quote keeps where it stood (migration 48): its character range
in the text artifact it was read from and that text's hash, written
once (a trigger refuses a change, as migration 35's refuse a change to
the fact). Extraction records it (`text.quotes.place`: the quote as
written, else with its whitespace and case loose, else with a hyphen at
a line's end inside a word). `store.evidence_place` says whether the
quote stands where it stood, moved after a re-read (and where to), or is
gone, and `why` carries it. The `places` pass of `prax maintain` fills
the edges written before, from each document's current text; a quote
not in the text gets the hash and no range, so it is looked for once.

The pass measured on a copy: 197 s for 9,061 documents the first night.
The first version folded the whole text again for every quote it did not
find at once, which on a book is hundreds of passes over megabytes; it
ran past ten minutes and was stopped. The quote is a pattern over the
text now, which is never copied. Of 118,729 live quotes, 38,576 stand in
their document's text: 37% of the 35B's, 68% of Sonnet's, 50% of the
27B's; crossref's, the references pass's and pages' are no quotes of the
text at all. So a quote is often a paraphrase, or from a text read again
since, and "gone" in `why` can mean it never was the text's own words.

## 2026-10-06, the night: the plugin keeps a project's graph file

The last part of T's design. `prax sync --if-auto`, the Claude Code
plugin's session-end run, exports the project's graph again after its
files are sent, where the project keeps `.prax/graph.jsonl` (exported
once by hand, which is the choice to keep it). The file is written only
when more than its header's `exported_at` changed, so a copy in git
diffs only when the graph did. A project without the file gets none.
The plan's "a document's own neighbourhood" was done on 2026-09-27
(stage L, the document page's "graph" link) and is ticked.

## 2026-10-06, late: the judge refitted, on the user's word

`steps.adjudicate.platt` in the desktop's prax.yaml is `{a: 0.8183, b:
0.8627}`, the fit on the 240 decided pairs of entity types; `settle`
stays 0.95. Cross-validated it settles 32% of pairs at 74/76 against the
old fit's 11% at 34/34. The worker was restarted to read it. A night's
merges are signed with their round, so `unmerge_run` takes one back.
The old fit is in the comment beside it and in
`prax.yaml.bak-2026-10-06-platt`.

# Moved from the plan

What follows is not the record of a night. It is plan material that was
finished or set aside and moved here so the plan holds only what is open:
the items done by 2026-10-02, the orders agreed on 2026-09-26 and -27,
the stages planned on 2026-09-25 and -29, and the engineering passes as
they were planned. Each says when it was written.

## 2026-10-04: what the plan held that was done, moved here

The order of 2026-10-02 with its by-hand list, the whole of stages AL,
AN and AM as they stood when finished (AL's and AN's open items stay
in the plan), and the finished items of the plan's other sections,
verbatim.

## The order, agreed 2026-10-02

Reevaluated the day the plan was rewritten. The card is first because
everything the library still waits for (formulas, the graph backlog,
readings) runs through it, and on 2026-10-02 two roles on it at once ran
the machine out of memory. Then the small structural debt of the maths
pack, then what is running anyway, then measured improvements, then the
engineering leftovers. What waits on the user is apart.

1. **AI. A plan for the card** (below): done but its measurement, which
   needs days of running. The jobs view's "do it now", the load times,
   `GET /work/plan` and `prax up` following it are in (0a92203, 135f3da).
2. **AD2, the tidy-up**: done (1824fab).
3. **The library's backlogs, running** (2026-10-02, `docs/log.md`, "what
   was never extracted"). A backlog worker extracts the graph of about
   1,050 documents. Marker reads the 29 books on the night of
   2026-10-02, then the ~190 weaker maths candidates. The `equations`
   step checks every formula, and a broken formula that is an
   extraction slip is read again. Watched, not built.
4. **AL. The first client's feedback** (below): its small bugs, the
   signals (`status`, `health`) and citations that survive a re-chunk
   are done. Next the project sync, documents linked to documents, what
   is current (a document's own date first), then captures, pages,
   bibliographies and privacy. AN (rules over the graph, the ontology
   annotated in standard vocabularies, the markup pages already carry)
   is a candidate after AL step 4, and AM (a path index) after AL
   step 5.
5. **AJ. Readers under prax's hand** (below). On the night of 2026-10-02
   marker's own llama-server held 10 GB of RAM that no plan saw. Step 1
   first, after the night's books; steps 2 and 3 after AA.
6. **AK. Another model, measured** (below): a dense Qwen 27B and an
   uncensored build of it against the 35B on prax's own evals. Card
   time, so it runs at night or while nothing else waits.
7. **AA. Close Z** (below): the labeller's corrections, and the genre
   words in the document field, measured.
8. **The zoetrope disk and the NAS** (below): the 37 cut-short PDFs of
   `refetch-later.txt` (untracked) with the import of the old disk, when
   the NAS is reachable.
9. **Measured improvements** (below, "Retrieval and ask", "The graph"):
   the sections pass's vector arm and its worth to `ask`, document-aware
   rerank input, a compressed edge list, `confidence` as a number.
10. **The engineering leftovers** (below): `store.retrieval` and
   `store.repair` in parts, typed shapes (a search hit first), the UI
   helpers the extension copies. The strict mypy batch is done.

Waiting on the user: **AE** (the distilled surfer), **AH** (the stack,
after the prototype settles), the move to the board ("Deployment shape"),
and pushing what is committed.

### Next, by hand (noted 2026-10-03)

- **Restart once marker has read the books** (the tray: "Stop prax",
  then "Start prax"; or `prax up --stop` and `prax up --data-dir
  C:\prax-data --detach`). Before that, Jobs shows nothing waiting for
  marker, or the card back with llama-server. It brings the commits of
  2026-10-03 live: the bounded prompt caches (AJ, step 1) and the genre
  words in the document field (AA), which the first `fields` pass after
  it rebuilds, and the worker embeds again (about 8 minutes).
- **Then measure AJ step 1**: marker's peak RAM and commit, VRAM and
  time per page on a few books, against the night of 2026-10-02 (10.3 GB
  for its OCR server). The 35B's prompt-cache share in its metrics says
  whether 2048 MiB is right.
- [x] (2026-10-03: `tray.claim` keeps `run/tray.pid`; a second tray on the same data directory is refused while the first lives, a dead one's file taken over) **Two tray icons** run on the desktop (one from the autostart, one
  started by hand, most likely): quit one. If it happens again, the tray
  should refuse a second copy on the same data directory.
- [x] (2026-10-03: not the paused worker: the ask's demand was recorded, but the `ask` step named the 35B's entry while `run:` served the 27B's, so it matched no role; `work.role_of_step` now also matches a model at the served address) **An ask wakes an idled model with the worker paused** (2026-10-03,
  AK): the door said the model "has been asked for" and nothing loaded
  until `prax up --start llama-server`. The demand the supervisor reads
  should come from the door's ask itself, not only from the worker.
- **Before AK's download**: see what Qwen has released since 3.6 (a page
  names a "Qwen 3.8" lineup, not checked), then fetch with the user's
  word, from a known quantizer, names and hashes in the log.
- [x] (2026-10-03, the user said yes) **The CLI client under strict
  mypy**: `clients/cli` is in `files`, checked against the package's own
  source (`mypy_path`), so its calls are no longer `Any`. One of its
  errors was a real crash: `prax doctor` divided an unread memory figure
  (None) by 1024.
- **Push** what is committed; CI on GitHub has not seen 2026-10-02 and
  -03.

- [ ] **AL. The first client's feedback.** On 2026-10-03 an agent in
      another of the user's repositories used prax through the plugin for
      a whole session: it searched, captured about 30 papers, wrote a
      landscape page, and synced a subproject's 27 documents. It then
      wrote 16 suggestions in four priorities. Below they are merged with
      prax's own reading of them, in the order agreed with the user. The
      project's documents are the acceptance set for steps 3 to 5; their
      content and names stay out of the repository (the baseline is a
      private page in prax, and the tests use invented documents of the
      same shape).

      *Done on 2026-10-03* (71a0916): a `PRAX_DOOR` without its scheme is
      `http` (`client.door_url`); a URL with a space is percent-encoded
      and one still unfit is a 400, not a 500 (`inbox.clean_url`); ingest,
      `get`, `context` and page answers carry `url`, and `#page/<slug>`
      opens a page; `documents` reports `added_at` (it read a field the
      door never sent) and filters by `since`.

      *1. Signals: what is the matter, without the UI.*
      - [x] (2026-10-03) `status(doc_ids)`: queued (with its place), processing, done,
            failed with the reason, and whether a worker is alive. Today
            `pending: []` lists only the reading requests, not the parse
            queue a capture waits in, so a document with no text and
            nothing pending reads as "never will" when it is "worker
            down": 24 captures sat for hours that way.
      - [x] (2026-10-03) `health()`: the door's address, whether the token is
            accepted, the worker's state. One call to the door, so the
            proxy stays thin.

      *2. Citations that survive a re-chunk* (the client's #13, raised
      from P2: it corrupts quietly). Done 2026-10-03, after the check
      below changed the picture: a saved answer cites `#doc/N` and the
      heading path as text, never a chunk id, so it does not move. A
      link an agent writes to a passage does: a re-index gave a changed
      passage's id to its successor (`tests/test_store.py`). The UI now
      trusts `?chunk=M` only while chunk M still holds the link's
      `find` words, and the skill tells an agent to write them. The
      note as first written: A page's answer cites passages as
      chunk ids (`#doc/N/M`), and chunk ids are reused after a re-chunk
      (the formula check's sha guard exists for that). After a re-sync a
      citation does not break; it lands on another passage. Keep the
      quote and the heading path with each citation and resolve through
      `?find=`, as graph edges do. First a test that shows the fault.

      *3. Syncing a project as a tool* (the client's #5, #6).
      - [x] (2026-10-03: the MCP tool, `prax sync`, `POST /projects/sync`; `docs/log.md`) `sync_project(root, include, exclude, name, domains,
            dry_run=True, tracked_only=True)`: tracked files only
            (`git ls-files`), the usual build folders left out (`_deps/`,
            `CMakeFiles/`, `*-subbuild/`, `build/`, `third_party/`), a
            dry run by default with the plan as data (each path: add,
            refresh, unchanged or skip, and why), a subdirectory of a
            larger repository as a project, documents keyed by the
            remote and the path so a move makes no duplicate. Without
            this the CLI needed the venv path, a call out of the
            sandbox, and took in about 90 vendored CMake files.
      - [x] (2026-10-03: the `projects` table, migration 38; the hook asks it, `--if-auto`; an older `.prax-project` still counts) The manifest in prax, keyed by remote and path, not a
            `.prax-project` in the repository (a committed one tells
            colleagues, and switches the session-end hook on by being
            there). Auto-sync only with an explicit `auto_sync: true`.
      - [x] (2026-10-03: made by the first sync, every synced document a member; the summary is the agent's to write) The project page made with it: `project-<name>`, its members,
            a summary block the agent writes.

      *4. Documents linked to documents* (the client's #7 and #4, one
      edge model). Documents are already graph nodes: `cites`,
      `annotates`, `mentions` and `synthesizes` join them, and a page's
      `[title](#doc/N)` is an `annotates` edge. No second table.
      - [x] (2026-10-04: `prax.text.paths`, `projects._links`; producer `sync`, run `links:<name>`, kept in step) At sync, Markdown links, backtick paths and bare `docs/x.md`
            mentions matched exactly against the project's keys become
            edges (producer `sync`, EXTRACTED). The acceptance project
            has 15 links and 159 backtick references: about 174 edges
            without a model. A path that matches nothing yet is matched
            again at the next sync, not kept as a dangling node.
      - [x] (2026-10-04: `store.document_node`; a chunk id as evidence is a 400) `link` takes `doc:N` as either end. Evidence stays a quote,
            never a chunk id.
      - [x] (2026-10-04: core 4 with `links_to` beside them; migration 39 moves the stamps, so nothing is re-extracted) `supersedes` and `invalidates` in the core ontology (a
            version bump, invariant 9): "the changelog invalidates the
            re-baseline table" is a fact the graph cannot say today.
      - [x] (2026-10-04: `linked.out`/`linked.in`; `traverse("doc:N")`) `context` returns a document's edges out and in;
            `traverse("doc:N")` walks from a document.
      - [x] (2026-10-04: `edge_conflicts`, migration 43, kept by the nightly `conflicts` pass; `traverse` marks a fact `disputed`, `why` lists what disagrees; a table beside the edges, not a `contradicts` edge: an edge joins two entities, a conflict two facts) Functional relations declared in the ontology (a building is
            `located_in` one place), and a later pass that proposes
            `contradicts` edges between facts of such a relation that
            disagree, with their own producer and run, so `retire_run`
            undoes a pass. Disagreement kept visible, never a fact ended
            by a model (Cognee's opt-in pass; Graphiti ends facts only
            when both carry world dates; the research note, sections 6
            and 7).
            (2026-10-04: first the noise under it. Functional relations are
            declared (stage AN) and their breaches listed (`functional-
            conflicts`); most of the 345 were one venue under two names or
            a series beside its edition. Resolution's `venue` tier merges
            the one and links the other `part_of`, and the finding takes a
            series and its edition for one answer: 247 left, mostly
            publishers, universities and dates typed as venues. The
            `not-venues` ailment of `prax heal` mends those: 157 left on a
            copy.)

      *5. What is current* (the client's #8). 13 of the acceptance
      project's 27 documents say they are retired, superseded or invalid,
      and an agent that quotes one as current repeats the failure that
      cost months of a pitch.
      - [x] (2026-10-03: from the record, the extension's paper, a page's
            tags and markup, the arXiv id; shown and filtered; and the
            `dates` step reading the first page with a model, measured on
            Zotero's dates: answers for 70%, its year right for 86% of
            those, most misses being Zotero's other version of the paper.
            Crossref by DOI waits: only 133 of 8,221 undated PDFs print
            one near their start. A PDF's metadata is still to come)
            **A document's own date, first** (the user, 2026-10-03:
            "there is no date for the docs visible … not when it was
            created in prax but when the document was published"; a
            person judges a paper by when it was written). Only 1,890 of
            12,837 live documents have one (`meta.date`, all from
            Zotero: 1,072 a year, 512 a month, 306 a day), shown on the
            document page and nowhere else. `meta.published` = {date,
            precision (year, month, day), by, at}, filled from what
            says it, best first: Zotero; Crossref by DOI (the citations
            importer asks it already); the arXiv id (`yymm`); a web
            page's `citation_date`, `article:published_time` or
            `DC.date`; the first page read by the titles pass
            ("Published …", a conference's year, ©); a PDF's metadata,
            last, since it often says when a file was made. Shown as
            the year in search hits, `get`, `ask`'s passages (so the
            answering model weighs age too) and the UI's lists; a
            filter `published_since`/`before` on search and
            `documents`. It is also the anchor the world's time below
            is read against: a document's date, never prax's.
      - [x] (2026-10-04: `prax.text.status`, `projects._statuses`: `meta.status` and `supersedes`/`invalidates` edges, run `status:<name>`, the line's date as world time) At sync, `retired` and `superseded_by` from front matter and
            explicit status lines ("retired 2026-10-02", "superseded by
            …", "status: …"), only in synced project documents: a paper
            saying "superseded by" is about others' work.
      - [ ] (2026-10-03: the columns and the record time done: migration
            36's `world_from`/`world_to` with their precision, written by
            `store.link` and shown by `traverse` only where present;
            `store.held_at` and `traverse(as_of=)` on the door and the
            MCP tool; invariant 8 reworded. Left: extraction filling the
            world dates, measured with `bench_extractor`; a document's
            lifecycle in `meta`) **The world's time beside prax's.** Invariant 8 calls the
            edges bi-temporal, but `valid_from` is set when `store.link`
            writes an edge and `valid_to` when it is retired: both are
            when prax held the fact (transaction time), with
            `ingested_at` beside them. Nothing records when a fact holds
            in the world by its source (valid time), and "retired
            2026-10-02" or "superseded by …" in a document is that. Two
            nullable columns on edges (a migration), filled only where a
            source states a date, and the same on a document's
            lifecycle (`meta`); today's columns documented as what they
            are, and invariant 8's wording in CLAUDE.md corrected with
            it (found 2026-10-03, talking about prax as an agent's
            memory).
      - [x] (2026-10-04: `traverse(as_of=)` was there; `store.changes`, `GET /graph/changes` and the `changes` tool, record time and world time, re-readings left out; migration 42's indexes) "As of a date" and "what changed in a period" as door
            routes and MCP tools, once the world's time is on the facts
            (Utopia exposes both; the research note, section 8).
      - [ ] **As Utopia does it** (its code, the research note, section
            10): each world date with a precision (year, month, day),
            and "ended, date unknown" as a state of its own (the end
            null, its precision `unknown`), with CHECK constraints that
            tie a precision to its date; an undated fact anchored to
            its document's date (above); `supersedes` on an edge that
            corrects another, so "corrected" and "rejected" are a query;
            and **one module that builds "held at T"** for every graph
            (2026-10-03: `store.held_at` is that module and the walk
            uses it; `test_a_moment_in_record_time_is_built_in_one_place`
            holds every `as_of` read to it and the past-moment form to
            `edges.py`. The 48 live-edge conditions of the graph part
            and 23 elsewhere in the store stay as written: they mean
            "now", and rewriting them would change nothing)
            read (record time: written at or before T, not retired
            before it; no T: not retired), with a test in
            `test_invariants.py` that no read builds it itself. prax's
            `valid_from`/`valid_to` are record time and are renamed or
            documented so.
      - [x] (2026-10-04: `store.staleness`, `STALE_SHIFT` 5 places, `stale` on the hit and `ask`'s passage, `include_stale`; the measurement on the project's own question waits for its re-sync) Search and `ask` rank a stale document lower and name its
            replacement; never a filter (as the domain prior), and
            `include_stale` turns it off. Measured on the project's own
            question ("the current shipping candidate and its figure":
            answered from the decision document and the corrected
            re-baseline only).

      *6. Captures and pages* (the client's #9, #10, #14).
      - [x] (2026-10-04: the `capture_requests` table, migration 40; the 202 answer; the extension's alarm every 5 minutes and its options switch; the bed in Chrome passes but for two checks that failed before the change, Firefox not run: Waterfox was open) A failed capture (a TLS chain error, a 403, a bot check) is
            queued for the extension (`{queued_for_extension: true}`),
            which fetches it with the person's session; the upload is
            linked to the request and takes its title and domains. 7 of
            about 30 captures failed so.
      - [x] (2026-10-04: the append itself is 0.04 s on a copy; the rest was the write lock. The lock now logs a write that waited over 5 s with who held it, and one that held it over 10 s; the whole-library fields pass is batched. Answering before the write waits until the log names a holder worth it) `append_page` took over 120 s. Measure where, then answer a
            write at once and index after.
      - [x] (2026-10-04: `store.update_section`, `PUT /page/{slug}/section`, the MCP tool) `update_section(slug, heading, text)`, or a replaceable status
            block like the ask blocks.
      - [x] (2026-10-04: `lifecycle` and `stale_sources` on `GET /page`, said under the page's header; contradicted waits for the contradiction pass) A page's lifecycle: marked stale, or contradicted, when the
            documents it cites change or are superseded (Synthadoc's
            draft, active, stale, contradicted, archived).
      - [x] (2026-10-04: `PUT /doc/{id}/title`, `POST /ingest/urls`, the MCP tools `set_title` and `capture_urls`) `set_title`, and batch capture with a result per item.

      *7. Bibliographies and privacy* (the client's #11, #12).
      - [x] (2026-10-04: `store.references_of`, `store.cited_but_missing`, the routes and tools; read from the reference entries, so an extraction's `cites` to a program ("OnsetDetector.LL", the 35B's typing) is not in them; open access is the DOI and arXiv links, a lookup per entry waits) `references(doc_id)`: the parsed list, each entry in the
            library (`doc N`) or with a DOI and an open-access URL.
            `cited_but_missing(set)`: what a set of papers cites that the
            library lacks, ranked by how many cite it. First the
            reference-list detector's scope: `cites` held
            "OnsetDetector.LL" from software help files.
      - [x] (2026-10-04: on `POST /ingest`, `PUT /page`, and the sync, kept with the project's settings; only `personal`, never a way to open) `sensitivity: personal` accepted by `ingest`, `write_page` and
            the sync, behind the wall (stage U), so a colleague's notes
            can live in prax.

      *8. The client's own page* (doc 13470, written 2026-10-04 after it
      checked the AL changes from its side; O6 and O7 were done that day).
      - [x] (2026-10-04) O1: `sync_project` and `ingest_file` read within the git
            repository of the working directory by default, so a sibling
            subproject is in reach.
      - [x] (2026-10-04) O3: `status` says `searchable` for a document in
            `reading` whose text is indexed already.
      - [x] (2026-10-04) O4: the `request_reading` tool (OCR, the vision
            model, marker) over the existing route.
      - [x] (2026-10-04) O5: `health` tells a refused token (401) from a route
            not allowed (403, a door older than the client), and names the
            door's commit beside the client's.
      - [x] (2026-10-04, the user: "build it", after the numbers: a hit is
            about 300 bytes, the link 45) O2: a ready passage link on each search hit, with `find` words
            the door chooses (`cite` on an agent's hits and `ask`'s
            passages, `store.cite_link`).

      *Not now.* Search hits with a `url` each (the client's #3, at chunk
      level too): about 40 bytes a hit against invariant 6. One `ui` base
      per answer and the ids are enough to build `#doc/N?chunk=M`. The
      book view of a project (#16: an ordered table of contents, a
      "current state" path, one-file export) is cheap after steps 4 and
      5, and comes then.

      *What worked*, by its account: hybrid search with the region line
      found the existing cluster in one call; `documents(title=…)`
      checked coverage; the capture errors were clear (the bot check
      pointed to the extension); and the skill's rule that a document's
      content is data, not instructions.

- [ ] **AN. Rules over the graph: what follows from what is stated**
      (candidate, after AL step 4; the user, 2026-10-03, on standard
      vocabularies: "maybe they are more useful for a reasoning part?").
      The research note, sections 10 and 11; Utopia's `utopia-reason` is
      the worked design, under two thousand lines.
      - [x] (2026-10-04: `transitive`, `symmetric`, `functional`, `inverse_of`, `kind` and `same_as` on relations, `same_as` on types; no version bump: they change what validates in no way, as `naming:`) **The ontology annotated, not replaced**: a relation's
            characteristics in the YAML (`transitive`, `inverse_of`,
            `symmetric`, `functional`, and `state`, `event` or
            `eternal`), and `same_as` names in schema.org, SKOS and
            PROV-O where they exist. It changes what types exist in no
            way, so whether it bumps a module's version is decided with
            it.
      - [x] (2026-10-04: `ontology.lint`, run by `compose`; a contradiction refuses to load) **A lint of the ontology when it loads**: symmetric and
            asymmetric at once, transitive and functional at once, a
            subtype cycle, an inverse that is not mutual.
      - [x] (2026-10-04: `store.derive_rules`, `edge_premises` (migration 41), `GET /edge/{id}/why` and the `why` tool; on request (`prax maintain --only rules`), not on the clock: see the measurement) **A rule pass on the door's clock**: OWL RL's few rules
            (transitivity, inverses, symmetry, subproperties) as forward
            rules in SQL. A derived edge is INFERRED, producer
            `rule:<name>`, a run, its validity the intersection of its
            premises', and its premises kept (a table of edge ids), so
            "why is this here" has an answer. An asserted edge always
            wins, and a derivation that contradicts one is not written.
            A cap a relation. Retraction by recompute and diff: the pass
            derives everything again and ends what no longer follows;
            nothing is deleted, and retiring its run removes all of it.
      - [x] (2026-10-04: the `functional-conflicts` ailment of `prax heal`, report only) **Constraints as findings, not rules**: a functional relation
            with two values goes to the `contradicts` pass (AL step 4).
      - [x] (2026-10-04, on a copy: 306 derived in 2.9 s; `part_of` closure right in 5 of 20, the rest built on premises the extraction wrote backwards; so not nightly. Then the direction check, `ontology.part_of_suspect` with the lexicon's `part_of` cues: suspects go to the review queue, are no premise, and the `backwards-part-of` ailment lists and mends the written ones; the pass then wrote 23 `part_of` derivations, 18 of 20 right. The user put it on the clock the same day: a pass of the nightly `prax maintain`) Measured: how many edges a pass adds, how many are wrong on a
            sample, what it does to `traverse` and to `ask`. The path
            index (AM) gains from it: a closure of `part_of` and of
            `broader` gives a walk meaningful shortcuts.
      - [ ] **More meaning from what the library holds, in standard
            words** (the user, 2026-10-03: "the standardized ontologies
            also could make sense to extract some more meaning from our
            current chunks/docs"):
            - [x] (2026-10-04: `prax.text.schemaorg` and the `markup` pass of `prax maintain`: 1,336 facts from 315 pages; the date reader read the unquoted tag too, 141 more pages dated) the **schema.org markup web pages already carry**
              (JSON-LD, microdata): a recipe's ingredients and times, an
              article's author and `datePublished`, a product, an event,
              a person. Read at capture and by a pass over the kept HTML
              originals, no model: facts EXTRACTED with producer
              `jsonld`, and a date for `meta.published` (AL step 5);
            - [x] (2026-10-04: the genres, exact matches only, `same_as:` in genres.yaml; the subjects not yet) prax's **genres and subjects mapped** to schema.org types
              (`ScholarlyArticle`, `TechArticle`, `Recipe`, `Review`…)
              and to SKOS concept schemes, so a document's kind is said
              in words other systems and models know;
            - the **extraction prompt given the standard names** beside
              prax's (a relation's `same_as`), which may help a local
              model place a relation; a module's version bump re-reads
              what it touches, as any bump does, so it is measured on a
              sample before a pass over the library.

- [x] (2026-10-04: the prototype measured on a copy, then `prax.graph.paths` and `store.connect_entities`, `GET /graph/connect` and the `connect` tool; in memory, not a file, at this size; `scripts/eval_paths.py`) **AM. A path index: how is A connected to B** (candidate, after
      AL step 5; the user, 2026-10-03: "path questions are interesting
      though and would make sense for any agentic use/reasoning on
      facts"). A derived file beside the database, in the vector files'
      pattern (memory-mapped, a delta, a merge, rebuilt at will), holding
      per entity its edges with what a path search weighs: relation,
      confidence class, the times, the source document, the number of
      documents behind it. Not a graph engine: a second store would break
      invariant 1, double the wall, and Kùzu, the engine CLAUDE.md names,
      was archived on 2025-10-10. Its search respects what no engine
      knows: evidence per hop, a cost for hubs, preferred relations, the
      world's time (`as_of`, hence after AL step 5), the wall. An agent
      gets `connect(a, b, max_hops, relations, as_of)`: the two or three
      best paths, each hop with its quote and document. First a Python
      prototype on a copy of the store and an evaluation set (pairs with
      a known connection and pairs with none), because a confident path
      of weak edges is worse than none; then a store module and the
      tool; compiled code only if speed asks, as AH's first piece. It
      also settles the decision threshold for slow walks (an index for
      the walk's query first, then this). The design is in the private
      research note, section 9.

- [x] (2026-10-04: `store.short_heading` in `_chunk_shape`, 120 characters a level and the 4 nearest levels; marker had mended 9522, 30 documents were still past 1,000 characters) **A heading is capped in a hit** (found 2026-10-03). Doc 9522, a
      patent read in two columns, has a heading of over 2,000 characters
      of repeated text, and it rides along with every search hit and
      `ask` passage of that document. Marker reads it again (queued the
      night of 2026-10-02); a cap on a hit's `heading` would keep one bad
      parse from swelling an answer (invariant 6). Not decided.

- [x] (2026-10-03: the first hop's cap, `support` on its rows, measured in the log; search's tie-break not built: a hit carries no fact to count) **Support count as a signal** (2026-10-03, from Graphiti's episode
      count, LightRAG's summed weights, HippoRAG's source counts): a
      walk's first hop ordered by how many documents separately say each
      fact, and search's ties broken by it. prax writes one edge row per
      source, so the count is a GROUP BY. Nearly free.

- [x] (2026-10-03, as a near tier proposing to the judge, not merging) **A deterministic step before the model in entity resolution**
      (Graphiti's `dedup_helpers`): a normalised exact match with one
      candidate resolves; a short or low-entropy name goes to the model;
      otherwise 3-gram shingles, MinHash and a Jaccard of 0.9 or more
      resolve. About 50 lines; fewer calls of the local model. Merges
      stay recorded and undoable as now (`merged_by`, `merged_run`).

- [x] (2026-10-03: hubs of 100+ live edges, entities a page speaks of; rule-derived edges with AN) **A gate on automatic merges by what they touch** (Utopia's
      `execution_gate`): a merge of the adjudicated tier waits on the
      Review page when it would make two values of a functional relation,
      touch an inferred edge, or touch an entity a saved page or answer
      cites. Three store queries, no table.

- [x] (2026-10-03, migration 35: edges never deleted and their fact fixed, page revisions, spend) **Append-only by trigger** on what is a ledger already (merge
      stamps, page revisions, token uses): `BEFORE UPDATE … RAISE(ABORT)`
      in SQLite, as Utopia's audit table.

- [x] (2026-10-03: seven parts, the switches on `retrieval.knobs`, the package refusing a knob set on itself; `docs/log.md`) **`store.retrieval` as a package.** It is 1,919 lines, under the
      2,000 that CLAUDE.md sets for a split. It does four jobs: query
      expansion, the search legs, fusion, and the vector files. Its tests
      switch behaviour through module flags (`retrieval.SENSES`,
      `retrieval.DOMAIN_PRIOR`, `DELTA_MERGE_AT`). Split into parts, a
      flag set on the package would no longer reach the part that reads
      it, and the tests would pass without testing. The split needs the
      flags read through one settings object first.

- [x] (2026-10-03) **The next strict batch of mypy: the whole package.**
      Under the batch's flags the package had 66 errors, not the 1,008
      feared. The 926 re-export errors come from `no_implicit_reexport`,
      which the batch never set; the store re-exports by design. The
      flags moved to `[tool.mypy]` itself (`docs/log.md`).

- [x] (2026-10-03: `store.retrieval.compounds`, with `term_documents` beside it) **`text.compounds` asks the store** for the forms a word takes, and
  only the store uses it. It is retrieval, not text; it waits for
  `store/retrieval.py` (1,916 lines) to split at 2,000 as invariant 3
  says, and goes there.
- **Large modules:** `store/repair.py` 1,625, `host/up.py` 1,435. Neither
  is tangled. Both are long lists of the same shape (ailments, roles).

## 2026-10-02: what the plan held that was done, moved here

The plan was rewritten on 2026-10-02 to hold only what is open (the
user: "move the other stuff to the log, if it is done"). The finished
items below are moved word for word from `docs/PLAN.md` as it stood
that day; their own dates say when each was done.

### Stages AB, AC, AD0, AD (from "The order after Z")

- [x] (2026-09-30: `ask.OPEN_SYSTEM`, `mode` on `POST /ask`, the Ask view's "open" box, `meta.page.open`; docs/ask.md "Grounded and open") **AB. An open mode for `ask`** (below, under "The UI and the
      agent"; experimental). About a day with the tests.
- [x] (2026-09-30: `probeShown` and the `pdf`/`pdf-frame` routes; `POST /import/github`, `github.one`) **AC. Two extension fixes.** A PDF viewer's tab is sent as the
      viewer's frame (four documents hold only its markup), and a GitHub
      repository as a page snapshot where the starred-repository
      importer's shape would do better. Both are under "The UI and the
      agent".
- [ ] (2026-10-01: step 1 built: `prax.packs`, the registries, the eight modules in five packs, the knowledge identical by a golden run; the lexicon stays in the core, 361 names would type differently; next: maths as the first pack with capability) (2026-10-01: packs, `docs/packs.md`: a field's ontology, readers, kinds, steps, tools,
      models and extra in one package under `src/prax/packs/`; first the registries, then
      maths as the first pack) **AD0. Packs.** The user, 2026-10-01: "should we have
      something like modules for these domain/infrastructure fields?" Packs live in
      the repository, and every domain is one: its knowledge (module, sameness
      cases, cues, suggested rules) turned on by the library, its capability
      (readers, kinds, steps, tools, models, extra) by the host. First step: the
      registries and the eight existing modules moved into packs.
- [ ] (2026-10-01: the user chose a SymPy calculator with `same` as the check; step 1 done, the rules: 38% accepted, 86% of those faithful by hand, the rest refused rather than misread; step 2 done: POST /maths, the MCP tool, the surf action; next: the questions, with and without the tool) (2026-09-30: planned, `docs/symbolic-maths.md`: 17,417 formulas in 243 documents; SymPy's ANTLR parser reads 81% and about 36% plausibly faithfully; three steps and five operations; the build waits on the user's choices) **AD. Symbolic maths, planned as a stage** (niggles.txt,
      2026-09-30). A display equation is a `formula` chunk with its LaTeX
      in `data` since marker. SymPy's LaTeX parser can turn it into an
      expression kept beside it. `ask` and the surfer could then use a
      maths tool (simplify, substitute, check two equations are the same,
      solve) in a sandbox. The plan says first how many documents carry
      formulas worth it, which parser reads marker's LaTeX, and what
      "plug things together" asks of the tool in practice. Built only
      after the user has read the plan.
      (2026-10-02: step 3 measured, `docs/symbolic-maths.md`: with the
      tool, grounded mode gains 2 to 4 right answers, past the spread of
      3 to 7 questions between runs; open mode does not change. Four
      rounds of prompt and parser fixes; the wording has stopped paying.)


### Stage AD2, as it stood when its steps were done

- [ ] **AD2. The maths tool, made structural** (the user, 2026-10-02:
      "what would be good structural fixes?"). Every failure left is a
      choice the model makes: whether to call the tool, how to write the
      call, whether to trust the result. Each step takes one choice
      away. In this order:
      - [x] (2026-10-02: the pack's `tool_grammar`, `tool.surf_json`; llama-server accepts the grammar, and the diode question written as a JSON step gives 13.23 mA; measured with the machine scoring) **A step written to a schema.** The `maths` step becomes a
            JSON object (`op`, `formula`, `var`, `values`, `at`) under
            the surf's per-step grammar, so a local model writes only
            calls that parse. 21 to 30 of every 85 calls failed on
            syntax in the double runs; the parser's heuristics
            (`TRAILING_VALUES`, `FOR_VAR`, `SYMPY_ORDER`, …) go when it
            is in. Claude as the surfer gets the same shape as a tool.
      - [x] (2026-10-02: `packs/maths/check.py`; on the e1/e2 answers 17 marks, all real, after judging only links with one free symbol at most; the numbers half waits on machine scoring to be measured) **A check after the answer.** The door reads the answer's
            equations and checks each with `same` against the passages
            and the tool's results, and compares each number with the
            result that gave it. The marks ("checked", "does not hold",
            "not checked") go into the answer whatever the model wrote:
            "(not checked)" asked of the model was used once in 160
            answers.
      - [x] (2026-10-02: `scripts/score_maths.py`, 10 of 20 questions; agrees with the hand on 141 of 160 answers; the five-run measurement is next) **Scoring by machine.** Each question gets a check the
            calculator can run: a number with a tolerance, a formula by
            `same`, a yes or no. Derivations stay scored by hand. Two
            runs of one way disagree on 3 to 7 of 20, so a change is
            measured over five runs, not two; by hand that costs an hour
            of scoring per run.
      - [x] (2026-10-02: the watched step `equations`; on 2,100 formulas 11 links judged and one real error found, `docs/symbolic-maths.md`; tables row against row not built) **Formulas checked when indexed.** The calculator reads each
            `formula` chunk once and keeps in `data` whether it reads
            and, for a chain A = B = C, whether each link holds. The
            surf shows a broken chain beside the passage (the Moog
            paper's tanh typo would carry it). A table of
            antiderivatives is checked row against row (the RNN paper's
            second antiderivative of tanh, wrong as extracted). A run
            over the 243 documents with formulas, measured: how many
            chains break, and how many of those are extraction faults.
      - [x] (2026-10-02: `surf.named_equations`; the right paper for all 7 of 8 questions that needed one, on the live store) **An equation by its number.** A question that names a
            document and an equation number ("Dattorro's (5)") gets that
            formula chunk at the start of the surf, from the numbers the
            formula chunks already carry. The resonator question failed
            in all 16 answers of both double runs because no passage
            held (5).
      - [ ] *Only if the steps above leave a gap:* a question that is a
            calculation (values and a formula) goes a fixed path: the
            formula found, evaluated with the values, the model asked
            only to explain the result.
      - [x] (2026-10-02: f45f3d4, bad4bbb, b85e9c2; `docs/log.md`) **The review round.**
            An independent review of AD2 found ten defects and the text
            heuristics; worked off: results as their own section, the
            judge on parsed expressions, checks as annotations, passages
            as LaTeX, the step held to its shape, the free-text parser
            gone, the calculator's environment in CI.
      - [ ] Measure the review round: the five runs again, scored by
            machine (the last five ran before steps 4 and 5 and the
            review round).
      - [ ] *Later, a tidy-up* (the user: "tests for maths stuff should be
            bundled with the pack"; the review's D2): the pack's tests
            beside it (`src/prax/packs/maths/tests/`, collected by
            pytest), and the maths-specific pieces still in the core
            moved behind pack hooks: the "does not hold" in
            `ask.Passage.nearby_line` and `store.equations_near`, and
            `store.formulas_to_check` / `set_formula_checks` as a generic
            "chunks of a kind missing a pack's key" pair.


### Stages AF and AG

- [x] (2026-09-30: `docs/research-condensed-knowledge.md`, `docs/research-database-layout.md`, `docs/research-code-music-sound.md`; the decisions each names are the user's) **AF. Research, three questions** (the user, 2026-09-30). Each ends
      in a document under `docs/` with a proposal and what it would
      cost. Nothing is built before the user has read it.
      - *Condensed knowledge.* What besides the graph's claims holds
        knowledge in a form a model can combine: formulas as expressions
        (AD), tables and a datasheet's parameters as values, a
        definition as a term with its conditions, a procedure as steps.
        What the literature and the tools do (knowledge compilation,
        semantic parsing of papers, notebooks as knowledge), measured
        against what the library holds.
      - *The database layout.* Whether the schema still fits after 30
        migrations: what `documents.meta` carries that has earned a
        column or an index (67 keys read by name), the size and the
        indexes of `edges` and `chunks` against the queries that are
        slow, `readings` and `jobs` as they grow, and the thresholds of
        CLAUDE.md ("Decision thresholds") against today's numbers. Read
        on a copy of the store, never the live one.
      - *Other kinds of knowledge in the graph.* How code (a
        repository's modules, what calls what, which library a program
        uses), music (a score, a piece's form, harmony, a MIDI file) and
        sound (a sample, what it sounds like, an embedding of it)
        would be represented: as ontology modules on the existing
        tables, as new chunk kinds and locators (rationale R13), or as
        something the tables cannot hold. What exists for each (tree-
        sitter and call graphs, music21 and MusicXML, CLAP-like audio
        embeddings) and what the library already holds of each.

- [x] **AG. The infrastructure the research found** (the user,
      2026-09-30: before any feature from the research, after the
      surfer's pilot and what is open of AA to AD).
      `docs/research-database-layout.md` has the measurements and the
      migration sketches.
      - Done (docs/log.md, 2026-09-30): migrations 0031 to 0033 (the
        graph lookups, the live documents and the language, the
        embedding stamps); `domain_clause` through the distinct sets
        instead of a `document_domains` table; `foreign_names`,
        `unlabelled_names`, `select_for_extraction` and
        `_all_chunks_embedded` without their per-row reads; a refusal
        repeated five times is an attempt (`queue.REFUSALS`); a
        briefing's text out of `meta`; the document delta merges at the
        main file's size; `compact_vectors` rebuilds the graph, as `prax
        maintain vectors`; `journal_size_limit`. `PRAGMA optimize` was
        measured and left out (three plans got slower).
      - Run on the live store the same evening (docs/log.md): the
        histories capped at 20, `prax maintain vectors`, a `VACUUM` at 4 KB
        pages.
      - Resolved review items are kept 30 days (`REVIEW_KEEP_DAYS`, the
        user), then the nightly `review` pass deletes them; the first go
        on 2026-10-08.
      - Done by the user's word the same evening: the e5 indexes and
        `prax-before-heal.db` deleted (2.7 GB); the entity threshold of
        CLAUDE.md is now a walk time, measured by `prax heal`
        (`slow-graph-walks`).
      - From AC, done 2026-09-30: the three IEEE papers sent again as
        PDFs (13369 to 13371, read by the worker); the arXiv one was in
        the library already (13339).


### The order agreed 2026-09-27, second half (K to Z)

## The order, agreed 2026-09-27, second half (from niggles.txt)

F–J below are done, and two niggles with them (taco/tacos is I, the
maintenance workflow and its banner are J). What is left of niggles.txt,
with the graph's correctness and the two held-back features, in this
order: a bug a user sees first, then the graph views, then what the graph
reads against, then the machine, then new ways in, then the features;
the review of the lists a person decides goes last.

- [x] (2026-09-27: a module holds the modules built on it; `unassigned`;
      the select filters on change) **K. Browse loses documents when only the module changes**, and
      "unassigned" shows only a label (niggles.txt). A bug in the view or
      in `GET /documents`' domain filter; reproduce, test, fix.
- [x] (2026-09-27: `/graph/overview?domain=`, `/graph/document/{id}`,
      a module select and a `graph` link on the document page)
      **L. The graph by module, and a document's own graph.** The graph
      view filtered to one domain's entities, and a link from a document
      page into the graph seeded with that document's entities.
- [x] (2026-09-28: counted: every extracted document is read against its modules' current versions, 8,878 of them `core3+research9`; the 1,114 without one wait for their first extraction) **M. What the graph reads against.** (2026-09-27: v9 done,
      `docs/ontology-v9.md`; electronics 1 and studio 5 done,
      `docs/ontology-electronics.md`; the full re-read after stage H's
      re-extractions, as the user decided.) Ontology v9 (the relations a
      document takes name `paper` where any `document` should do), then
      the decision on the documents read against an older version (a
      full re-run or a delta pass; the 1,021 of 2026-09-10 are 6,412
      stamped `core1+research5` now, none of them captures, so the
      standing worker never draws them: about 28 hours of the local
      model at 230 an hour), then an `electronics` module for
      datasheets (pins, packages, electrical characteristics), grown from
      what the review queue holds for them, as studio was. A module for
      code waits for documents that need it.
- [x] (2026-09-27: `idle_minutes` and `trim` in `prax up`, deferrals as
      demand, the holders in the Jobs view; howto 4b)
      **N. The card and the memory when nothing runs.** Unload the GPU
      models after an idle while and load them when a queue asks (`prax
      up`'s swap exists; the plan's "let a queue ask for the card"), see
      who holds the card (the plan's "See who holds the card"), and the
      16.6 GB of mapped model pages a fresh load left in RAM on
      2026-09-27.
- [x] (2026-09-27: `clients/send/prax_send.py`, `POST /known`,
      `meta.origin`; `docs/media-by-reference.md`)
      **O. A portable sender for other machines.** One file, no
      dependencies beyond what an old system has, that walks a tree, asks
      the door by hash whether it holds each file, and sends what it does
      not (PDFs and text first). Audio and video by reference rather than
      by copy is its own design question, written down, not built.
- [x] (2026-09-27: `docs/communities.md`; search and ask as ways in
      are still open) **P. Communities** (below, "A partition, and a way to keep it"):
      the hub decision it waited on is H.
- [x] **Q. A confidence that was measured** (below; `confidence` as a number on edges is still open there). (2026-09-27: the
      tooling built, a pilot run, the full run queued behind the re-read;
      the finding so far is that the labels need a gold sample.
      2026-09-28: the adjudicator answers by pair number; with one rule
      for sameness the local model settles 60% of the pairs at 97%
      agreement with Opus; 77 wrong merges split. Open: the gold sample,
      the user's Review round under the rule, then the thresholds.)
- [x] (2026-09-28: `ontology/sameness.yaml`, `ontology.sameness()`, `GET /graph/sameness`, the Review page shows it and each pair's number) **Q2. The rule for "the same thing" as data**, after the Review
      round. `resolution.SAME_RULE` (the models' question) and the Review
      guide's rules of thumb say the same in two copies. Move it to
      `ontology/sameness.yaml` beside `lexicon.yaml`: the cases that are
      the same and those that are different, each with examples, and a
      module's own examples in its words (kitchen: a vegan variant is
      another dish; studio: a synth and its manual). `prax.graph.ontology`
      loads it, the question is built from it with the examples of the
      pair's modules, the Review page shows it from the door, and
      `docs/review.md` points to it. Like the lexicon it stays out of the
      version string: it changes how a pair is judged, not what exists.
      Not `prax.yaml`: every host serving a library judges alike.
- [x] (2026-09-27: the three tabs of the Review page, `decide_pair`,
      `unmerge_entity`, `undecide_pair`; howto "Entity resolution")
      **R. The lists a person decides, last:** a review page for the 741
      likely merges and the 1,632 split names, with the merge and the
      unmerge the door already has. Each decision is kept as a label, so
      the page also collects Q's gold sample, and it shows the merges Q
      found suspect.
- [x] (2026-09-27: not reproduced natively; two unlocked index paths
      and a third that saved over a mapped file found and closed,
      `scripts/stress_vectors.py`) **S. The door's `STATUS_BAD_STACK`
      exit** (below): a reproduction before any fix, so open-ended.
- [x] (2026-09-27: `prax export`, `prax import graph`,
      `docs/graph-files.md`; the plugin's session-end sync still open)
      **T. Sub-graph export / import**, as designed below: a feature,
      not a repair.
- [x] (2026-09-28: `prax token`, migration 0029, `tests/test_wall.py`)
      **U. Tokens and the wall** (niggles.txt, "closed domains";
      2026-09-27). The library keeps everything, and a client sees only
      what its token may. `PRAX_TOKEN` stays the administrator. Named
      tokens (`prax token add NAME [--domains …] [--personal]`) are kept
      as a hash in a `tokens` table; the secret is printed once. A token
      says which modules it sees (every one by default) and whether it sees
      personal documents (no by default). A document carries a
      `sensitivity` column: `suspected`, `personal`, or none. The filter
      sits in the store, keyed by the request's viewer, so `ask`, the
      surfer and every read inherit it: a hidden document is absent from
      search, get, chunks, lists, pages, context and the graph (an edge from
      it is not there, nor an entity only it names). A restricted token
      may call only the routes the MCP tools use; every other route answers
      403, so no unfiltered route can be reached. A test walks every
      allowed route with a hidden document.
- [x] (2026-09-28: `prax.wall.private`, `store.suspect`, the `private` pass, the "personal?" tab; 238 of 12,974 suspected in the dry run) **V. What is personal** (niggles.txt, "approximate domains"). Rules
      in `prax.yaml` (`private:`, outside the repository: the owner's name
      and other hints live there only): strong cues that mark a document
      `suspected` alone (an IBAN, a bank statement, a rental contract, a
      tax assessment), weak ones that count only together (a name with an
      invoice word), paths. The rules run when a text is indexed, so a NAS
      send closes personal files as they land, and as a `maintain` pass
      over the library. They only ever suspect: a person confirms or
      releases on the Review page's "personal?" tab, and a person's
      decision is never overturned by a rule. The Zotero library holds
      bank statements and contracts filed as research (2026-09-27: 15
      "Kontoauszug", 8 "Mietvertrag", 71 with an IBAN), which is why the
      mark is apart from the domain.
- [x] (2026-09-28: `prax.text.clutter`, `store.retire_set` / `restore_set`, the Review page's "clean up" tab; measured: 238 system and clutter documents, 55 copies, 896 help files kept) **X. Cleaning up in bulk**, after V, from the NAS dump of
      2026-09-28. A set of documents chosen by rule, shown before anything
      happens, retired in one go and restored in one go. The rules to try
      first, counted read-only on what arrived before any is built: the
      origin path (`meta.origin`, what the sender records), no text or a
      few lines of it, the usual clutter (licences, readmes, driver and
      installer notes, generated reports), near-duplicates the checksum
      does not catch (one title, nearly one text). The personal documents
      of V are confirmed in the same view. Retiring stays what it is: the
      original stays in the archive.
- [x] (2026-09-28: the Admin page, tabs tokens / personal? / clean up; `GET /private/rules`; a token's last use kept hourly; the rules shown, not edited: prax.yaml stays the one place) **W. The administrative side**, after U and V: the tokens, the
      rules and the personal documents managed from the web interface.
- [x] (2026-09-28: `parsers.Partial`, both OCR readers windowed, `queue.continue_windows`, `store.note_ocr_progress`) **Y. OCR in windows**, after W (the user's order, 2026-09-28). A
      scan past `parse.ocr_max_pages` (60) is refused today and read only
      by a one-off run with a higher budget: 71 PDFs were, and four DjVu
      books (190 to 746 pages) still have no text. Instead each pass reads
      what has text (the text layer, and the pages OCR'd before, which the
      page marks show) and OCRs the next window of pages without it. The
      door stores and indexes the text so far, so a book is searchable as
      it goes, and queues the next window while pages remain. The
      document says how far it is (`meta`, "OCR: 180 of 746 pages"). Both
      PDF and DjVu, which share the OCR engine. The parse protocol grows a
      "pages still to go" in a result. Then the 1,663 PDFs with under
      2,000 characters of text are worth a look for scans nobody asked to
      read.
- [ ] **Z. What a document is, and what it is about** (below, planned
      2026-09-29): a genre for every document, several where it is
      several, with a confidence measured against the user's own labels;
      the topics it belongs to from the graph; and the domains of the
      2,748 documents without one derived from both by rules. The
      research is in the library (docs 13315–13337).



### What a document is, and what it is about (the design of Z, 2026-09-29)

## What a document is, and what it is about (planned, 2026-09-29)

**The problem.** A domain is the set of ontology modules a document is
read against, and the one rule that sets it is `source: zotero →
research`. The 2,748 documents without a domain are mostly the NAS dump of 2026-09-28. Among them are datasheets and
electronics books that belong in `electronics`, and political,
sociological and opinion pieces that fit no module. What the extraction already says about them is thin. It types the document itself as one of a module's `self_types`. For
1,377 of them that is the fallback `document`, for 590 there is none,
and the rest are `paper` (248), `build` (219), `article` (142) and
`manual` (102), counted read-only on 2026-09-28. The self type answers "what may
this document be in the ontology", which is not the question.

**Two axes, both multi-label.** Genre and topic are separate questions.
Pang et al. (doc 2194) treat genre and subjectivity apart from topic.
Doc 1459 finds that experts, crowds and machines agree on some genres
and not others, and concludes that several facets beat one taxonomy. A document may be several
of each: a blog post that is a how-to and an opinion, a book on
electronics and on music. In the formal sense of doc 879, a document
gets a set of labels Y ⊆ L, each with its own probability.

**The genre vocabulary** is `ontology/genres.yaml`, beside the lexicon and the sameness rule. Like them it stays out of the
version string, because it says what a document is, not what exists in
the graph. Two levels,
after the web-register taxonomy of CORE (docs 13320, 13321: eight main
registers, sub-registers under them, a document that fits no sub-register
keeps the main one). The main levels, with their genres:

- informational: paper, thesis, book, lecture notes, reference entry,
  news report;
- instructional: manual, datasheet, schematic, recipe, tutorial;
- opinion: essay, column, review ("blog" was dropped in version 3: a
  venue, not a form);
- persuasion: advertisement, product page;
- narrative: story, biography, report of events;
- interactive: forum thread, Q&A, comments;
- personal and administrative: letter, invoice, contract, form;
- code.

Each genre carries one line of description: a label
described in context classifies better than a bare name (Gen-Z, doc
13336).

**Gold before model.** A "genre" tab on the Review page shows a
document (title, summary, source, the opening of its text) and takes
the user's genres. Before any model runs, the user labels about 150,
drawn across sources (NAS, Zotero, captures, the extension). Genre is
ambiguous even for people: CORE needed two of four annotators to agree,
and hybrid texts are where classifiers fail most (doc 13320). So the
gold sample is the measure, and the confidence rests on it, as for the
local adjudicator (stage Q).

**The genres step** is a worker step on the local model, like titles and
summaries. It reads the title, the summary, the section summaries when
there are any, the source and host, and the first 2,000 characters.
TnT-LLM (doc 13322) summarises before labelling because a summary
normalises length and variety, and prax has the summaries already. But
genre is a matter of form as much as content, which a summary may
lose, hence the opening. Summary-only against summary-plus-opening is
the first measurement. Three ways of getting several labels, measured
on the gold sample, the best kept:

1. a list the grammar allows, first genre first;
2. the same list with the token probabilities of every generation
   step, each genre scored by its highest probability over the steps.
   An LLM suppresses all but one label at each step, and the first
   step's distribution does not predict the final set. The maximum over
   steps improves F1 and the fit to people's answers at no extra cost
   (doc 13331);
3. a yes or no per genre, over the five nearest by vector.

**A side-quest: the decision models.** Jev (TypeSafe, a closed API)
answers several questions about one text as probabilities read from the
model rather than written by it (doc 10307 reverse-engineers it). Two
open models take the same request format (`state`, `questions`, each a
`choice`, a `noul` yes/no or a `score`). Jeff (github.com/firelex/jeff)
is Qwen3.5 0.8B and 2B and a Gemma, fine-tuned in full, one fitted
temperature. Kev (github.com/jaredpalmer/kev) is a rank-16 LoRA and a
pointer head on Qwen3.5 0.8B, 4B and 9B, or Qwen3.8 27B, one temperature
per checkpoint. A yes/no per genre over one shared reading of the
document is method 3 without a generation per genre, which is why they
are worth measuring. Measured as a fourth candidate on the same gold sample
and inputs, Jeff-2B and Kev-4B first. Jeff-2B's 4.2 GB fits on the card
beside llama-server's 20 GB; Kev-4B takes the card from it (`prax up
--swap`). Open questions the measurement answers: how they read
500–700 tokens (their benchmarks used about 200), and how far their own
temperature is from a fit on the user's labels. Both need PyTorch and
transformers, an optional extra for the worker, never the serving path.
Jev itself is not a candidate: it would send the library's text to a
company's API.

The order of the genres in the prompt is shuffled per document: the
recency and majority biases of a prompt shift a model's labels (doc
13327). For scale: ChatGPT reached micro F1 0.74 on English genre
identification, 5–7 points above a classifier fine-tuned on 1,700
labelled texts (doc 13319). Expect the local model near that, not above.

**The confidence** is a Platt fit per genre on the gold sample, as `resolution.LocalAdjudicator` does for pairs (doc 13328 on
calibration). Under a threshold a genre is kept but acts on nothing.
Stored as `meta.genres: [{genre, p}]`, with the run and the model, so
`retire_run` has something to retire. A person's genres on a document
are `by: human` and never overwritten. Confident genre words join the
document field (`document_field`, beside "PDF document"), so a search for
"datasheet for the TL072" finds the datasheet first.

**Subjects** are what a document is about in a person's words
(`ontology/subjects.yaml`: society, arts, technology, everyday, and the
subjects in each, philosophy, politics and sociology among them). The
user asked for them on 2026-09-29, when the genres had no place for
political or philosophical writing: that is a topic, not a form. They
are labelled with the genres, in the same row, and the model assigns
them the same way. A domain rule can name one (`subject: politics`).

**Topics** come from the graph, which already has them: the regions and
parts of stage P. `meta.regions: [{id, share}]` is written by the
communities pass for every document with facts, counted the way
`regions_of` counts a search's hits, and keeps each region over a
quarter. A document with no facts yet takes the regions of its nearest
documents by document vector, a Rocchio-style vote (doc 5463): a
region's profile is its documents. Region ids change when the partition
is recomputed, so nothing stores a region id as a decision.

**Domains** are derived by rules, in the one place they already live
(`domains:` in prax.yaml, `store.assign_domains`), with two new match
keys:

- `genre: datasheet` (p over the threshold): `→ [electronics]`;
- `facts: kitchen`: at least half of the document's typed facts are of
  one module's types. A document without a domain is extracted against
  every module, so its own facts say which module it reads as, and
  this outlives a recomputed partition.

Every run is a dry run first, counted per rule, and applied on the
user's word. A set a person chose (`domains_by: human`) is never
touched, as now. A document may match several rules and take their
union, since domains are a set.

**What it does not do.** No model decides a domain directly. A domain
changes what a document is read against, so a wrong one costs an
extraction. A genre or a fact share with a measured confidence is a
reason a rule can name. A `society` module (politics, economy,
opinion) is decided after this runs. The count to take: the documents whose
genre is opinion or news and whose facts are mostly core types. The
review queue's items for them say what types they ask for. The region "Civilization Collapse and
Systemic Risk" (232 documents) is where they gather now.

**Later, if the board needs it.** The board runs no model (invariant 7).
A capture landing there waits for the worker, as a title does. If that
is too slow, a logistic regression per genre over the document vectors,
trained on the model's labels, can serve on the board. Classifiers
trained on LLM labels perform comparably to those trained on people's
(doc 13329), and choosing which documents the teacher labels cuts its
cost further (doc 13330). SetFit (doc 13325) is the heavier option.
Built only when measured to be needed.

**Stages, in order:**

1. `ontology/genres.yaml`, the Review page's genre tab, and the user's
   gold sample (about 150). (2026-09-29: the file, the tab, `GET
   /genres`, `GET /documents/genre-sample`, `PUT /doc/{id}/genres`
   built; `ontology/subjects.yaml` beside it, labelled in the same row;
   the labelling is the user's.)
2. The genres step, the three methods and the two inputs measured on the
   gold sample (`scripts/eval_genres.py`, `docs/eval/genres-*.md`), and
   Jeff and Kev beside them (the side-quest above). (2026-09-29: measured,
   `docs/eval/genres-2026-09-29.md`: listed, then asked, on the summary and
   the opening, genres F1 0.60 blind against Claude's 0.64, subjects 0.58
   against 0.64, about 2.5 s a document. The step is built
   (`steps.writing.Genres`) and off; turning it on is the user's word.
   Jeff 0.8B and Kev-4B measured on the card 2026-09-30. The small
   labeller built the same day: bge-small fine-tuned on the teacher's
   and the person's labels, `steps.genres.method: small`, about 30
   minutes for the library on the CPU; `docs/howto.md`, "What a document
   is". Switching it on is the user's word.)
3. The calibration, `meta.genres`, the genres on the properties dialog
   and as a Browse filter, the document field. (2026-09-29: calibration
   and `meta.genres` in the step; the properties dialog shows genres and
   subjects with who gave them and a model's probabilities; Browse
   filters by genre and subject, a level naming everything under it. The
   document field waits: it changes what search ranks, so it goes in
   with `scripts/eval_retrieval.py` before and after.)
4. ~~`meta.regions` from the communities pass, and the vector vote for
   documents without facts.~~ Dropped by the user, 2026-10-01: nothing
   reads it, and a document's region is computed on read when a view
   needs it (`store.regions_of`).
5. The `genre` and `facts` match keys, a dry run over the 2,748
   documents without a domain, then the user's word.
6. The `society` decision, measured.


### The orders agreed 2026-09-27 and 2026-09-26 (A to J)

## The order, agreed 2026-09-27

A–E below are done. This is what follows, in this order: first what
waits only on a word, then the other half of the traverse question, then
the precondition for communities, then two things a user notices.

- [x] (2026-09-27: 2 twins retired, the promotions were already done, the
      figures a nightly slice of 150 from the door's clock) **F. What waited on a word.** The six `twin-documents` (dry run,
      then the repair), the seven pending promotions (`--spend`, capped
      at the seven, roughly $1-3), and the unread figures started in
      slices that leave the parse queue its turn.
- [x] (2026-09-27: 394 safe merges, the round on the clock, `split-names` and
      `POST /graph/merge` for the rest, merges stamped with their run) **G. One thing in pieces** (`docs/eval/fractured-names-2026-09-27.md`).
      A `heal` ailment that lists them, merges for the safe classes (the
      same type twice, a type and its subtype) under a run that
      `unmerge_run` takes back, and a rule for which unrelated type pairs
      may name one thing, measured before anything merges.
- [x] (2026-09-27, see the log) **H. The hub entities.** 164 of the 200 biggest hubs are papers,
      and the biggest is a proceedings volume at degree 1,951: decide what
      a container is and keep it out of the neighbourhoods.
      *Measured 2026-09-27: not a container.* The volume's entity is 118
      NIME papers whose title (from each PDF's metadata, and in Zotero) is
      the volume's name, merged by that name into one. 1,820 live documents
      share a title with another: course names on exercise sheets, a LaTeX
      template's sample title on 89. Most other paper hubs are long
      documents, whose degree follows their length and which neither the
      second hop nor the hub map lands on. So the fix is the titles:
      - [x] `titles.guess_title(not_title=…)`: the shared title is named to
            the model as not this one, and a guess of it is refused.
      - [x] a `shared` reason in `titles_needed` (a title three or more
            live documents carry), for the watched titles step;
      - [x] after a retitle away from a shared title, the document
            extracted again, so its facts leave the shared entity (a new
            reading retires the old);
      - [x] applied 2026-09-27 after the probe on the local model: 642
            documents retitled by the first drain, the re-extractions
            running on the standing worker.
- [x] (2026-09-27: a rare word's forms the library uses, `compounds.forms`)
      **I. taco and tacos** are different searches: a plural folded on
      the keyword side, measured on the eval set.
- [x] (2026-09-27: `GET /maintenance`, the Passes table, the banner)
      **J. One workflow for maintenance and healing**, where there are
      five commands with five shapes (`niggles.txt`).

## The order, agreed 2026-09-26

Everything below is written up somewhere in this file or in
`docs/audit/engineering-2026-09-25.md`. What was missing was an order,
and the order is load-bearing rather than a preference — each stage is
either a thing that might be broken now, a thing that makes the next
stage safe, or a thing a user would notice.

### A. Settle whether the surfer got worse — done 2026-09-26, cleared

- [x] **What the surf sees when it walks changed under it.** `prax.answering.surf`
      took `store.traverse(...)[:WALK_EDGES]`, the first N edges by id,
      and since 2026-09-25 passes `limit=WALK_EDGES` into traverse, which
      spends the cap round-robin across relations. Same count, different
      edges. The ask set's eight-step verdict fell to 42 against a
      baseline range of 43–48 on the first run that exercised it
      (`docs/eval/ask-equations-2026-09-26.md`), while every mechanical
      number rose.

      Three repeats put it at 43.7 with a range of 42-46 against the
      baseline's 45.0 (43-48), so the 42 was the bottom of a spread:
      `cited` matches the baseline to the decimal, `match` is marginally
      above, and `sources` went 90% to 100%. No regression demonstrated
      and no second arm run — `docs/eval/ask-surf-walk-2026-09-26.md`,
      which also records that 42 alone means nothing, for the next time
      the surf changes.

### B. Make the invariants able to catch things — second, because it is
### cheap and it protects everything after it

- [x] (done 2026-09-26: CLAUDE.md marks each invariant, tests/test_invariants.py,
      the `attachment` pass) The four kinds, the tests, and the measured pass: "Name what kind
      of invariant each one is", below. **Invariant 6 was breached for
      months and no test could have failed**, which is the argument for
      doing this before the refactors rather than after them: a
      structural change is exactly when a silent breach gets introduced,
      and three of the checks currently live in a scratch audit script
      instead of the suite.

### C. What a user actually notices — the two parts done 2026-09-26,
### and a design gap found under them

- [x] **German compounds** — `prax.text.compounds`, the library as its word
      list: a compound splits where both halves are terms the index holds,
      and each half goes through the graph's labels (`Olivenoel` →
      `oliven` → `olive oil`). No harm and a small gain on the 19 German
      questions (hybrid MRR 4.33 → 4.83). ("What one question in German
      found wrong", below.) 1,988 German documents, and for a compound query the
      keyword half of the hybrid contributes *nothing*. A word list, not
      a model.
- [x] **An ingredient list without a heading** — measured over the
      library with the real chunker: 20 of the 32 kitchen documents that
      had none gained one, two outside the kitchen did (a cocktail
      handbook under research, and one false alarm), none lost one.
      Applied to the kitchen domain on the live store: **36 → 56 of 68
      (53% → 82%)**, the apple recipe among them. The other two change at
      the next full rechunk.
- [x] **The list is a box, not yet edges** — it was not needed: once
      the chunk exists the model reads it, and re-extracting the 74
      kitchen documents gave 1,029 `calls_for` edges, `apple` among the
      apple recipe's sixteen (plus 50 `made_of`, which the ontology
      allows for a dish).
- [x] **The prevention dropped what the repair kept** — names are
      written as printed and translated once, by the watched
      `vocabulary` step, which keeps the printed word as a label
      (663d2df). Two faults in that step came up on the day's 94
      renames and are fixed: the document title was taken for the
      answer (`Apfel-auflauf` → "elderflower lemon bake"), and a name was
      ruled English if any English document contained it, which let out
      `Mehl`, `Zucker`, `Salz`. Now a rate
      (`docs/eval/apfelkuchen-2026-09-26.md`, "After the re-extraction").
- [x] (done 2026-09-26: `prax maintain` pass `rejudge`, 979 overturned and
      folded; a second run overturned none) **Re-judge what the old rule ruled out.** 979 of the 4,103
      entities marked `vocabulary:corpus` would be candidates under the
      rate: 59 ingredients, ~900 concepts and methods the German course
      material uses more than the English half does. Clearing the mark
      puts them back in the queue, at one local call each (the model
      hands an English word back unchanged). A data change: waits for
      the word.
- [x] **And count what uses a mechanism** — the `attachment` pass of
      `prax maintain`. The apple question shows the lesson once more:
      no German document in this library names an apple, so the route
      from `Apfelkuchen` to `apple` is built and has nothing to cross
      on. The route is only as good as the words the library holds.
- [x] **The word the other way** — the vocabulary step now also writes
      a German label for each common English name of up to three words
      the library's documents use, so a German query crosses where no
      German document printed the word. Kitchen subset dry run: 203
      names at 0.14 s each, 162 labelled, 37 the same word, 4 refused,
      about 6 wrong (blackberry jam → Himbeermarmelade, cumin → Kümmel).
      Library-wide it offers 22,389, mostly research concepts: about 50
      minutes of the local model once the door runs it.
- [x] **Layer 1 of the brand collision: a word the graph added is
      searched where its sense lives.** "Apfelkuchen" without a domain:
      the apple recipe second, after a cocktail with apple juice, and no
      Logic manual on the first page. 43 of the 44 eval questions keep
      their top ten; the German set is unchanged (9 found, 4.58).
- [x] **Layer 2: a question for one of the small domains** gets
      a soft extra vote for that domain's hits. The graph could not be
      the trigger: nothing called "apple" is the brand, and a domain of
      30 documents gives any German word a lift of 20–90 by accident.
      The trigger is the query's own candidates. "apple cake": the recipe
      goes from 4th to 2nd. "Apple Loops", "apple" and every eval question
      are unchanged.
- [x] **The brand collision** — done by the two layers above. Left: the
      manuals are still 3rd and 4th for "apple cake", which a preference
      rather than a filter accepts. Was: what stands between an English
      "apple cake" and the Logic Pro manuals. Still design work.

Brand polysemy (`apple` → Logic Pro manuals) is deliberately not here.
It is design work with no obvious shape yet, and it is the one apple
failure a user can work around by adding a word.

### D. The two quadratics — fourth, because they cost hours whenever a
### pass runs over the library and nothing else waits on them

- [x] (done 2026-09-26) The embed hand-out's cursor and the delta's save frequency ("The
      embed pass redoes its finished work twice a batch", below). Both
      small, both measured, and the re-embed that found them is done, so
      there is no rush and no reason to leave them.

### E. The structural work — last, and one stage each

- [x] **The Step object**, tests first (2026-09-27): `prax.steps` is a package, each
      step one object with `hand_out`, `take_in` and `run`; `work.py` 1,144 → 360 lines,
      `worker.py` 1,159 → 918. The worker passes of summaries, sections and
      vocabulary were pinned first, and a test keeps the dispatchers free of
      step names. Two bugs fell out: a local-model step with a paid model
      leased its batch before refusing it, and a deferred vocabulary result
      raised in the door.
- [x] (2026-09-27: `documents` and `graph`, the two past 2,000 lines, are packages of six
      parts each with an `ORDER` the invariant test reads; the largest file is now 681
      lines. `retrieval` (1,837) and `repair` (1,460) stay whole until they pass the
      same line) **The store's module split**, after the Step object, since both
      touch the largest modules and the module order is an invariant that
      B will have made testable.
- [x] (2026-09-27: 41 → 0; 30 store reads added, and a test fails on SQL
      naming a prax table outside the store; the Zotero importer's 9 read
      its source) **The reads outside the store**, which the split partly dissolves.
- [x] (2026-09-27: `models.trim_guessed`, `models.trim_measured`,
      `sections.budget`) **The three names** (`fits`, `fits_for`, `read_for`), which ride
      along with whatever touches them.

The two things already written and not yet earned — communities as nodes,
and a confidence that was measured — stay below all of this. Both are
features rather than repairs, and both read better once the surf question
is closed.


### From "Still open": what was done

- [x] (done 2026-09-26, 310 s, after stage D) **`prax maintain --rechunk`** — the `ad`, `comment` and
      `ingredients` regions only arrive with a re-chunk (10,261
      documents; a chunk whose text did not change keeps its id and its
      vector). An overnight job.
- [x] (F: `schedule: figures`, 150 documents a night) **The unread figures** — 55,607 left of 93,158, sliced so a
      request does not pre-empt the parse queue for days.
- [x] (F: none waiting on 2026-09-27) **The seven pending promotions** — `prax work --steps promote
      --spend -n 7`, roughly $1–3 on Sonnet 5; or the standing shape,
      `spend: true` on the worker role with `budget: {daily_usd: N}`.
- [x] (F: 2 left, retired into their keepers) **`twin-documents`** (6) — the repair retires documents.

### What one question in German found wrong

`docs/eval/apfelkuchen-2026-09-26.md`: "hast du ein rezept fuer
apfelkuchen?" answered no, and the same question in English found the
recipe at once. Three independent failures, each wanting a different fix.

- [x] (stage C) **German compounds are one FTS token.** `Apfelkuchen` matches
      nothing; `Apfel` and `Kuchen` each match. For a compound query the
      keyword half of the hybrid contributes nothing at all, which is why
      the German answer held a chickpea pan and an insertion sort. German
      is 1,988 documents here and compounding is how the language builds
      nouns. FTS5 takes a custom tokenizer and a splitter needs a word
      list rather than a model, so this is the cheapest of the three.
- [x] (stage C, layers 1 and 2) **A common noun loses to a brand that owns the word.** `apple`
      returns Logic Pro and Logic Express manuals — correctly, by BM25,
      since they say it far more often. Even in English the fruit is
      unreachable until "bars" disambiguates. prax handles one name
      meaning two things in the *graph* (the `proposes` pass, the review
      queue) and not at all in retrieval.
- [x] (stage C) **An ingredient list without a heading is not recognised**, so the
      cross-lingual bridge has nothing standing on it. `prax.text.ingredients`
      cuts from a "Zutaten"/"Ingredients" heading; the Guardian writes its
      quantities into the prose. The library has **38 `ingredients` chunks
      in ten thousand documents**, and the recipe in question has none —
      hence no `apple` ingredient entity, hence no German label to cross
      from. The graph's only apples are Apple Macintosh, apple loops,
      Apple Computer Inc., and `Holsapple`, `Scrapple` and `Applets`.

      This is the one to take seriously, because nothing is broken. The
      identity work of 2026-09-24 built the route and measured it
      carrying 32 of 80 German queries to new documents. Almost nothing
      is attached to it. **A mechanism that works and is unattached looks
      exactly like one that does not work**, and the way to tell them
      apart is to count what uses it — which no pass does.

### The graph

- [x] **A name is not an identity** (2026-09-27): traverse walks one of the
      things a name reaches and names the others (`senses`, `type`), in the
      door, the MCP tool, the CLI, the UI and the surfer's walk.
- [x] (stage G) **One thing in pieces** (`docs/eval/fractured-names-2026-09-27.md`):
      75 missed same-type merges (casing, ß/ss), 34 subtype pairs, ~1,700
      unrelated-type splits, mostly tool/method (640 names). A resolution
      rule for which type pairs may name one thing, with a person deciding,
      and a `heal` ailment that lists them. Apple Inc. is six entities.


- [x] (2026-09-27, `docs/ontology-v9.md`) **Ontology v9** — the relations a document takes name `paper`
      where they could name `document`, so a captured page has to be
      read as a paper for an edge to fit. A version bump and a restamp,
      not a rules change.
- [x] (stage H: shared titles) **The hub entities** — 164 of the 200 biggest hubs are papers, and
      the largest, `Proceedings of the International Conference…` at
      degree 1,951, is a container that should probably not be an entity
      at all. Cheap, and a precondition for communities.
- [x] (2026-09-27: the full re-read, as the user decided) **Decide full re-run versus delta pass** for the 1,021 documents
      extracted against an older ontology version
      (`docs/log.md`, Stage 3).

### Retrieval

- [x] (stage D, 2026-09-26: the hand-out walks on from where it was, 770 ms
      once a walk and then 1 ms against 755–800 every time, measured on a
      copy halfway through; the delta is written every 30 s
      (`door.vector_save_seconds`) and when the queue drains, and the rows a
      killed door had not written are forgotten at start) **The embed pass redoes its finished work twice a batch** (measured
      2026-09-25, mid-re-embed; "round-trip bound" was the wrong first
      answer — marshalling is 7%). Two quadratic costs, both the same
      shape: work proportional to what is already done, repeated per
      batch.

      **The hand-out scans past everything finished.**
      `pending_embeddings` is `LEFT JOIN chunk_embeddings ... WHERE
      model != ? ORDER BY c.id DESC`, which SQLite plans as `SCAN c`.
      Measured at the halfway mark: it walks **1,198,689 already-embedded
      rows** to find the next 2,000, 759 ms, and worse the further it
      gets. A cursor — hand out below the last id given — or an index
      that lets it skip.

      **Every batch rewrites the whole delta index.** `work.take_in`
      calls `store.save_vectors(model)` after *each* POST, and that
      writes the accumulated delta: 31 MB at the halfway mark, headed for
      about 800 MB before a merge. A 200-vector batch rewrites the entire
      file, which is the 10-17 s the door logs as a slow POST. Save every
      N batches or on a timer; `merge_vectors` already folds it.

      Together: 331 chunks/s of embedder delivering 98 early in a run and
      67 at the halfway mark, degrading as it goes. The workaround while
      it stands is a big batch and several workers
      (`-n 600 --workers 3 --interval 1`).

- [x] (stage I) **taco and tacos are different searches** (`niggles.txt`).
- [x] (stage D: the walk skips what it has passed) **The embed hand-out after a rechunk** — `GET /work/embed` and the
      backlog it leaves (`docs/log.md`, "The night of 2026-09-20").
- [x] (F: the slice a night) **The figures backlog, and why it was not moving** — 3,692
      waiting (`docs/log.md`, 2026-09-24).

- [x] (stage J) **One workflow for maintenance and healing** (`niggles.txt`).
- [x] **Sub-graph export / import** — the answer to "what if a repo's
      notes want to travel" (`docs/log.md`, "The night of 2026-09-20").
      (Stage T, `docs/graph-files.md`.)

- [x] (2026-09-30, stage AB) **An open mode for `ask`** (experimental; the user, 2026-09-30).
      Today both prompts pin the model to the passages ("use only the
      numbered passages… you have no other knowledge", under 250
      words), so `ask` finds and cites and never builds. An open mode
      keeps the search and the surfing (they gather the datasheet, the
      paper, the earlier note) and answers with a second prompt: cite the
      library's passages [n] where they are used, and otherwise use the
      model's own knowledge, write code, derive, compare, at the length
      the task needs, saying which part is which. `mode: grounded | open`
      on `POST /ask`, a toggle beside the model in the UI. Four things to
      get right. What is cited and what is the model's own must stay apart
      in the answer. A page kept from an open answer is marked
      (`meta.page.open`), so a later search never takes it for the
      library's evidence. The local 35B codes common things decently,
      Claude (`backend: claude`, paid) much better. There is no
      run-and-test loop, so real work in a codebase stays with Claude Code
      and prax over MCP. About a day with the tests.
- [x] (2026-09-30, stage AC) **The extension sends a PDF viewer's tab as a snapshot.** A tab
      showing `arxiv.org/pdf/…` or IEEE's `stamp.jsp` is saved by
      SingleFile as the viewer's frame, not the PDF: four documents
      (9539, 9574, 9741, 10112) hold only the frame's markup. The
      extension should send the PDF the frame shows, as it does for a
      tab whose URL ends in `.pdf`. Since 2026-09-29 such a page no
      longer falls back to plain text, so it waits as a failed parse.
- [x] (2026-09-30, stage AC) **The extension sends a GitHub repository as a page snapshot.**
      The popup says so (`lib.route`, 2026-09-30). The importer for
      starred repositories (`prax.importers.github`, a client) writes a
      better document: the README under a header of the repository's
      details (description, language, topics, licence). The route could
      send a repository in that shape, through a door route that takes
      one repository's URL. The popup's line changes with it. A small
      stage with its own tests.


### What holds the card: the reasoning of 2026-09-25

## What holds the card, and what a batch may ask for (planned, 2026-09-25)

Not a new service. `prax up` already is one: roles it keeps alive,
`on_demand` for a role that cannot fit beside the others, and
`up.swap(data_dir, to, back_when="idle")`, which is the marker evening.
What the day found missing is narrower, and in this order.


**Not** a general resource manager. The serving host has no contention
to arbitrate (invariant 7), the supervisor is `prax up` and not a second
thing beneath it (invariant 4), and most of what went wrong on 2026-09-25
was not resources at all — a stale configuration, a unit error, and the
session's own memory guard.



### Deployment shape: what was built

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


### Communities as nodes: what was built

## A level above the neighbourhood: communities as nodes (planned, 2026-09-25)

`docs/eval/traverse-neighbourhood-2026-09-25.md` measured what the second
hop returns and why it is 3.4 MB, and the repair it proposes — a path
shape and a ranking by how many documents connect an idea to the entry
point — fixes the size for one call's work. It does not give the graph
anything to say *above* the neighbourhood, which is the other half of
what a reader wants: not only "what is next to the Fourier transform"
but "what region of the library is this".

The standard answer is GraphRAG's: partition the entity graph into
communities (Leiden, hierarchically), write a summary of each with a
model, and keep the summary as a node of its own. It suits prax better
than it suits most, because the machinery is already here — the
`sections` step writes a summary per heading region with the local
model, and a community summary is the same pass over a cluster of
entities instead. Nothing paid, nothing new to install.

What it would want, in order:

- [x] **A partition, and a way to keep it.** Leiden over the live edges,
      folded to canonical entities. It is a derived index like chunks,
      so it may be rebuilt at any time; the question is what triggers a
      rebuild. Probably the `maintain` clock rather than every write.
- [x] **A summary per community**, written by the `sections` model over
      the cluster's entities and their strongest edges, stamped with
      what it read so a moved partition makes it stale rather than
      wrong — the rule `meta.sections` already follows.
- [x] (2026-09-28: traverse, the graph view and search; ask measured and left off, no gain: `docs/eval/regions-2026-09-28.md`) **A way in.** `traverse` names the community an entry point sits
      in; `search` may offer it as a hit of its own; `ask` gets a
      cheaper way in than chunks for a question about a region rather
      than a fact.
- [x] **A decision about the hubs first.** 164 of the 200 biggest hubs
      are papers, and the largest is `Proceedings of the International
      Conference…` at degree 1,951 — a container that should probably
      not be an entity. A partition computed before that is decided
      will cluster around artifacts. This is the cheap part and it
      comes first.

Built on 2026-09-27 as stage P (`docs/communities.md`): a pass, two
tables, a staleness rule and a model step.


### A confidence that was measured: what was done

## A confidence that was measured, not written (planned, 2026-09-25)

Every edge in the graph carries `confidence`: EXTRACTED, INFERRED or
AMBIGUOUS. The model *writes that word* and `extraction.py` stores it —
`t.get("confidence", "AMBIGUOUS")`. Invariant 8 says edges are evidence
rather than truth and makes confidence a column on the fact, and then
fills that column with an unvalidated claim. A model's probability of
emitting the token "EXTRACTED" is not the probability that the triple is
right.

Prompted by `doc:10307` ("Jev's Architecture Unmasked", captured
2026-09-24), which is a speculative reverse-engineering of a closed API
and says so. Nothing here depends on that API being real or on adopting
it; what the piece gets right is the critique, and it lands on prax.

Four passes have exactly the shape it describes — shared state, one
question, a fixed set of allowed answers:

| pass | the question | what it costs now |
|---|---|---|
| extraction | how sure is this triple? | a generated word |
| `typing` | which of N types is this? | a generated name |
| `vocabulary` | is this name English; does it fold? | a generated answer |
| `adjudicate` | are these two entities one thing? | Opus 5, $2.59 for 8,242 pairs |

prax reads **no logprobs anywhere** today (grammars exist, for the
surfer). A single constrained token plus its logprob gives a number out
of the model already running, at no extra cost — the answer is one token
either way.

**The experiment comes before the change, and the labels already exist.**
The adjudicated round recorded 3,683 merges and 4,559 declines: a
labelled outcome set of 8,242 decisions, which is exactly what is needed
to find out whether a local logprob predicts anything.

- [x] **Ask the local model the adjudicator's question** on those 8,242
      pairs, under a grammar that allows one token, and keep the
      logprob. A few hours of llama-server, nothing spent.
      (2026-09-27: `scripts/eval_confidence.py`, `prax.graph.calibration`. Only
      6,126 of the pairs can be labelled: the 2,704 declines still
      recorded, and 3,422 merges reconstructed from the unstamped merges
      of the likely types, against Opus's 3,683. No grammar: the mass on
      "yes" against "no" among the token's top ten. The full run starts
      when the re-read ends.)
- [x] **Score it against the labels.** Reliability diagram and Brier
      score, not accuracy: the question is whether 0.8 means 0.8, not
      whether the argmax is right. Raw logprobs from an
      instruction-tuned model are famously not calibrated, which is
      exactly why the article's subject trains against outcomes — so
      expect to need Platt or isotonic scaling fitted on part of the
      pairs and measured on the rest.
- [x] (2026-09-28: `docs/eval/confidence-2026-09-28.md`; isotonic ECE 0.022, agreement 0.80)
      **Read the full run.** It starts by itself when the re-read ends
      (`confidence_after_reread.ps1` in the session scratchpad, the answers
      and `score.md` beside it). Its numbers go into
      `docs/eval/confidence-<date>.md`, with the pilot's.
- [x] (2026-09-28: 117 merges with different numbers judged; the rule then made editions one thing) **The wrong merges it points at.** Pairs labelled "same" that the
      local model rejects near 0 (preorder/postorder traversal, a transform
      and its short-time variant) are likely bad merges in the graph,
      whoever made them. They go on the review page (stage R) as merges to
      look at, and `POST /graph/unmerge` or a single split undoes one.
- [x] (2026-09-28) **The adjudicator answers by number, not by position.** The full
      run found Opus "declining" plain spelling variants (cutoff /
      cut-off, realtime / real-time): forty pairs a call answered as a
      list of booleans, a short list padded with `False`, so one missing
      answer shifts every later one. Each answer should name its pair.
- [x] (2026-09-28: 221 decisions) **A gold sample first.** The pilot (200 pairs) found the local
      model right where the label was wrong, both ways. Opus kept apart
      LDR/LDRs and Gauss-Seidel/Gauß-Seidel, and the "same" labels hold
      preorder/postorder traversal and the fractional Fourier transform
      with its short-time variant. Agreement with Opus therefore measures
      Opus's errors too. A few hundred pairs a person has labelled (the
      review page of stage R can collect them) are what both models are
      scored against before anything acts on a threshold.
- [x] (2026-09-28: the local model at 0.9, the middle to a person, no paid tier; `resolution.LocalAdjudicator`) **Then decide what it buys.** If it calibrates, the likely tier
      can act on a threshold and send only the uncertain middle to the
      paid model, which is where the money goes. If it does not, that is
      a finding worth writing down and the paid tier stays as it is.

Deliberately after the current run of work: it is a measurement with a
possible change behind it, not a change.


### The engineering passes of 2026-09-28 and 2026-09-25

## The engineering pass of 2026-09-28: packages

Asked for by the user: "a lot of separate source code files that may be
moved to (sub)packages". There were 52 modules at the top of `prax`,
beside five packages.

- [x] **45 modules into 8 packages** by what they are about: `text`,
      `graph`, `writing`, `answering`, `ml`, `capture`, `host`, `wall`
      (CLAUDE.md, "The layout"). Moved with `git mv`, the imports
      rewritten by their syntax tree, the dotted names and paths in the
      docs rewritten too, except `docs/log.md` and `docs/eval/`, which
      record what things were called when they were written. Seven
      modules stay at the top: the foundations and the protocol the
      invariants name.
- [x] **What a call costs is `prax.ml.pricing`,** not
      `prax.graph.extraction`: the price table, the cache shares,
      `cost_usd` and `supports_effort` were used by asking, surfing, the
      pipeline, resolution, the budget and the model registry, and made
      each of them import the extraction.
- [x] **`prax.text` stands on nothing of prax** at module level, and a
      test holds it.

Found and left, each its own decision:

- [x] (2026-09-28: the rule stated and held by
      `test_the_store_stands_only_on_what_is_below_it`) **The store
      reaches up, lazily.** Twelve calls from inside functions to
      `writing`, `parsers`, `models`, `wall`, and the graph beyond the
      ontology. Moving them out would fight invariant 3: a pass that must
      touch the tables and needs a step's logic lives in the store. So the
      store is the door, not the bottom layer. At the top of a module it
      imports only config, `text`, `ml` and `graph.ontology`; above that
      only inside a function. An import at the top would be a cycle.
- [x] (2026-09-28: `worker.py` 933 lines to 274) **`steps` imported
      `worker` lazily** in every family, for the `do_*` functions. They
      live in their step's module now (`steps/parse.py` has the reading
      chain and its heartbeat), as invariant 4 says a step is one object
      with both halves; `worker.py` is the loop, the drop folder and the
      give-up counter, and `steps.base.say` the line a pass writes. The
      two copies of `_paid` are `ModelSpec.paid`.

- [x] (2026-09-28: `__init__.py` 1,706 lines to 437) **Code in a
      package's `__init__`:** the parsers are parts by what they read
      (`base`, `code`, `pdf`, `marker`, `web`, `readings`, `office`,
      `djvu`) with an `ORDER` a test holds; the `__init__` keeps the
      registry and re-exports every name. A test that replaces a parser's
      helper replaces it on its part now.

## The engineering pass of 2026-09-25, as work

`docs/audit/engineering-2026-09-25.md` is the review. These are its
findings in the order they pay, and one that came out of writing it.

### A step is an object, not three if-chains

- [x] (stage E, 2026-09-27) **`work.hand_out` (363 lines), `work.take_in` (295) and
      `worker.run_once` (267) are the same dispatch written three times**,
      over the same step names, in two modules. Adding `sections` meant
      editing all three in the same order: its batch cap in one, its
      lease in another, its results in a third, and its worker side in
      the fourth place.

      A `Step` with `hand_out`, `take_in` and `do`, registered once, so a
      new pass is an object rather than four edits. The lease and the
      scope handling are the same for every step and belong to the
      registry; what differs is the query, the payload and the model
      call.

      It touches every step, so it is a stage of its own and wants the
      tests first — the work protocol is where a mistake costs a batch of
      real work.

### The store's modules outgrew the split that was already made

- [x] (stage E, 2026-09-27) **`store/documents.py` is 2,739 lines and `store/graph.py` 2,044**,
      against the 2,000 that made `prax.api` a package on 2026-09-22.
      `graph.py` splits along what it holds — entities, edges, labels,
      traversal — and `documents.py` along documents, chunks, the
      document field. The module order is an invariant, so a split has to
      keep it and the checker in the audit script is what proves it did.

### The reads outside the store are growing, not shrinking

- [x] (41 → 0 on 2026-09-27, stage E) **26 on 2026-09-22, 38 now**, in 13 modules. The last pass left
      them with "worth doing when a module's query breaks on a schema
      change"; the count going up is the evidence that the answer was
      wrong. A store read per question is the cleaner surface. The
      importers are the bulk (`zotero.py` alone has 10) and are also the
      least coupled to the rest, so they are the place to start or the
      place to exempt deliberately.

### Three names for one idea

- [x] (renamed 2026-09-27: `trim_guessed`, `trim_measured`, `sections.budget`)
      **`models.fits`, `models.fits_for`, `sections.read_for`**, all
      added on 2026-09-25. The wrapper earns its place — the budget's
      defaults belong to the pass rather than to `models` — but nothing
      in the name says that. `sections.budget()` reads as what it is.
      Small, and the kind of thing that is free now and confusing later.

### Name what kind of invariant each one is

- [x] (stage B, 2026-09-26) **The ten invariants are held in four different ways, and the file
      does not say which.** That matters because it decides what can
      catch a breach:

      | kind | how a breach is caught | which |
      |---|---|---|
      | **checked** | a test fails | 3 (the module order, no writes outside the store), 5 (what the proxy imports), 10 (the importers open read-only) |
      | **enforced** | the code path makes it true | 2 (register hashes the bytes), 4 (the locks), 8 (`link` stamps, nothing deletes), 9 (`check_edge`) |
      | **measured** | only a number says | 6 (response size), 7 (resident RAM in the serving path) |
      | **chosen** | a standing decision with a revisit threshold | 1 (SQLite, and when to stop) |

      The point of the table is the third row. **Invariant 6 was breached
      for months and nothing noticed** — `traverse` answered 3.4 MB and
      no test could have failed, because "keep responses small" is not a
      property of the code, it is a property of an answer to a real
      question on a real library. A measured invariant needs a recurring
      measurement or it is a wish.

      So: say the kind beside each invariant in `CLAUDE.md`, put the
      checkable ones in a test (the audit script already checks three of
      them by hand), and give the measured ones a number that a pass
      reports — the largest answer `search`, `get` and `traverse` gave
      this week, and the door's resident memory.


### Later / maybe: done

- [x] The door exited once with `STATUS_BAD_STACK` (0xC0000028, a native
      unwind, 2026-09-19 23:38) while an embed post merged the 1.2 GB
      index and a search read it; `prax up` restarted it in a second.
      usearch suspected; the merge no longer holds the index lock for
      its build. Watch `logs/up.log` for another. (2026-09-27, stage S:
      four native exits in all, none since 2026-09-20; see the log.)
