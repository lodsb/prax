# Web UI

A way to query the store, read documents and walk the graph without an
agent. It is a client of the HTTP door and nothing else (rationale R14):
static files in `src/prax/ui/`, served by the FastAPI process at `/ui/`,
talking to JSON endpoints. No framework, no build step, no template engine.

## Endpoints the UI uses

Existing, agent-shaped: `GET /search`, `GET /get/{id}`, `GET /chunk/{id}`,
`GET /traverse`, `POST /link`.

Added for browsing (read-only, thin wrappers over store functions):

| Endpoint | Returns |
|---|---|
| `GET /documents?limit&offset&title&source&mime&retired` | documents without text, newest first (retired ones only with `retired=1`): id, title, mime, added_at, parsed_at, source_url, meta, chunk count |
| `GET /doc/{id}/original` | the archived original bytes with their MIME type; `Content-Disposition: inline` so a PDF opens in the browser's viewer (`#page=N` from a chunk's locator) |
| `GET /doc/{id}/text` | the Markdown text artifact as `text/markdown` |
| `GET /doc/{id}/figure/{sha}` | a figure's bytes out of the document's original, by the hash the text references (`![caption](figure:<sha256>)`); immutable, cached for a year |
| `GET /doc/{id}/chunks` | the document as its chunks in order: id, seq, kind, heading, page, locator, text, table data |
| `GET /entities?q&limit` | entities whose name contains `q`: id, name, type, degree |
| `GET /ontology` | the current ontology: version, entity types, relations with domain and range |
| `GET /review?limit&offset&open&rel&unmapped` | review-queue items with the total, filtered by relation and by unmapped (untyped) versus typed items; `POST /review/{id}` closes one (`dropped`, `ontology`, or `linked` with optional type and relation overrides, which writes the edge with the item's evidence and source); `POST /review/bulk` closes every open item matching a filter; `POST /review/replay` links the typed items the current ontology accepts |
| `GET /pages?kind`, `GET /page/{slug}`, `GET /page/{slug}/revision/{n}` | pages (notes, projects, topics) with their revisions; `PUT /page/{slug}` creates or revises (409 when an agent would overwrite a person), `POST /page/{slug}/append` adds a section, `POST /project/{slug}/members` adds a document to a project |
| `GET /graph/overview?limit&min_shared` | the most connected concepts, methods, tools and datasets, the edges among them, and co-occurrence links between hubs sharing source documents |
| `POST /ask {question, limit, doctype, backend, history, steps, tokens, stream}`, `GET /ask/config`, `POST /ask/save {slug, heading, result}` | the passages (one per document, with chunk and document ids) and graph facts for a question, and an answer citing them when a backend answers (`local`, `claude`, `none`; the host's default from `/ask/config`); with `steps` (the host's default when absent; 0 is one shot) the model surfs first — searches again, reads on, walks the graph, drops what is beside the point — within `tokens` of reading (`/ask/config` gives the default and the ceiling per model), and the result carries the `trail`, `steps`, `dropped` and `reading_left`; `stream: true` answers one JSON object per line as it goes (`step`, `answering`, `answer` with the result, or `error`); `history` is the conversation so far as `[{question, answer}, …]` (the last six turns ride along for the model, and a follow-up that leans on them — short, or pointing back with "it", "that", "the second one" — is searched together with the previous question; the answer cites only this turn's passages; `turns_before` in the result says how many were used); `save` appends a result to a page as the agent with a source list and `annotates` edges |
| `GET /doc/{id}/domains`, `PUT /doc/{id}/domains {domains}`, `POST`/`DELETE /doc/{id}/domains/{name}` | the document's domain set (which ontology modules it is read against; null means every module) with the modules to choose from; replace, add one, remove one; `GET /search?domain=` keeps one domain's documents |
| `POST /ingest/file` (multipart `file`, `title`, `domains`, `tags`, `session`, `by`), `POST /ingest/html {url, html, title, domains, tags, session, mode, note}`, `POST /ingest/url {url, title, domains, tags, session}`, `GET /inbox?limit` | captures: an uploaded file, a page as the browser rendered it, a URL the door fetches; each returns the document id, whether it was new, whether it is searchable already, its domains and the previous capture of the same URL; `/inbox` lists recent captures with state, the drop folder path and the domains to choose from |
| `POST /doc/{id}/reading {extractor, mode}`, `POST /readings/bulk {extractor, mode, ids | mime | text_source | unreadable | read_figures, limit, dry_run}`, `DELETE /doc/{id}/reading`, `GET /readings` | ask for a named extractor on one document (`vision-pages` with `scans` or `all`, `figures` with `captioned` or `all`, `vision`, `pymupdf4llm-ocr`, `docling`; validated against the type, the mode against the extractor), or on a selection at once — ids, a MIME prefix, a text-source stamp prefix, the unreadable documents — one request per document, what the extractor does not read skipped and counted, `dry_run` only counting (`prax reread`); withdraw or dismiss it; the waiting requests and the recently finished ones with their outcome, plus what the vision step resolves to. The work protocol hands requests out before the pending captures, forced, with the mode; a worker refuses one whose model would cost money and says so in the outcome |
| `GET /promote?limit`, `POST /doc/{id}/promote {reason}`, `DELETE /doc/{id}/promote` | the documents flagged for the expensive model's pass with their status, and scored candidates (project members, synthesis sources, notes, library citations); set and clear the flag |
| `POST /doc/{id}/retire {reason, duplicate_of}`, `DELETE /doc/{id}/retire`, `POST /inbox/dedupe?commit=` | retire a document (out of search and the graph, row and file kept) and bring it back; retire the duplicate captures of every URL (dry run without `commit`) |
| `GET /work/{step}?limit&scope`, `POST /work/{step}`, `POST /work/session`, `POST /work/session/{id}`, `POST /vectors/merge` | the work protocol: a leased batch for parse, titles, extract or embed (`scope` captures or all), the results applied by the door, a worker's session job with heartbeats, the delta indexes folded into the main files |
| `GET /documents?domain`, `GET /doc/{id}/context?domain` | browse and the document's similar-documents list within one ontology module; the search, browse and document views carry a module selector ("every module" is no filter; a document without a domain set is in every module) |
| `POST /maintain {only}` | the maintenance pass as a job: the acronyms table, the document fields, the domain rules, the duplicate captures, the review queue's rule passes; `rechunk` only when named (howto 3n) |
| `POST /graph/resolve {apply, type, twins, embed, show}` | entity resolution: the plan (sure, twins, likely, with examples), and with `apply` a job merging the sure ones and the twins (`prax resolve`) |
| `POST /import/citations {source, ids, limit, resolve_titles, refresh, dry_run}` | the citation network from OpenAlex or Crossref as a job the door runs (`prax import citations`) |
| `POST /import/zotero/item` (multipart: `item`, `file`, `cache_text`) | one planned Zotero document written as the importer would — created, merged, refreshed, skipped or missing (`prax import zotero` plans over the copy and sends them one by one) |
| `GET /heal?check&examples`, `POST /heal` | the recurring damage in the store and its repair: placeholder entities, mangled names, self-edges, stale jobs, stale parses (howto 3m) |
| `GET /models/servers` | the `openai` model servers of `prax.yaml`, one entry per server: reachable, alias, model file, slots, vision, and the load from `/metrics` when the server exposes it (tokens per second, busy slots, requests running and waiting, prompt tokens total and cached) |
| `GET /changes`, `GET /jobs?limit`, `POST /vectors/release` | the change stamp and running-job count the UI polls; running and recent batch jobs with heartbeat, progress and note; drop the door's index views so a batch job on the same machine can replace the files |
| `GET /doc/{id}/context?limit` | what places a document in the library: extraction summary and entities, citations in and out (library documents resolved), nearest documents by vector (centroid of the document's chunk vectors, one KNN), documents sharing its entities or authors, Zotero parent, siblings, collections and tags |

## Routes (hash-based, one page)

| Route | View |
|---|---|
| `#search?q=…&mode=hybrid&kind=&doctype=` | query form with kind and document type (PDFs, web pages, images, text files, notes); results as cards: title, kind badge, heading path, page, snippet with match markers, which side found it (fts / vec ranks); a "doc" side marks hits found through the document field; a card opens `#doc/<id>?chunk=<chunk_id>`; "original" opens `/doc/<id>/original#page=N` in a new tab |
| `#promote` | the queue for the expensive model: flagged documents (who, why, when, done or pending under the promote step's model; `prax work --steps promote --spend` reads them) with an un-promote control, and candidates with their score breakdown and a promote button; a document page has "promote" in its action row, and "domains…" opening a row of boxes to tick, one per module, none ticked meaning every module (shown as `domains: family, research` in its meta line; a change under an extraction leaves that reading stale, so the worker's next extract pass reads the document again against the new set and retires the old reading, the status line saying so); "read again…" opens a small form asking for an extractor on this document — for a PDF the vision model over its figures (the captioned ones, or every image the text references) or its scanned pages (or every page), a re-read that finds the figures, OCR, Docling; for a captured page the figures or a re-read; for an image a second reading by the vision model — and a line under the actions says what became of the last request (waiting for a worker, done with the stamp and outcome, or failed with the reason) |
| `#inbox` | captures: a drop zone and file picker (several files at once, or a whole folder — hundreds are fine: they go up one at a time with a running count in the status area, a summary every ten and at the end (new, already in the store, failed with the reason), the failures listed in full; the view holds still while a batch runs and the summary survives its later re-renders; a file the store already has is reported as such, so a folder can be dropped again after an interruption), domain checkboxes and tags applied to the upload, a title for a single file; a URL form the door fetches; the drop folder's path; the recent captures with source, domains, time and state (pending; "no text found" when every extractor tried it and found none — a scan, most likely, so OCR or the vision model is asked for on its page; indexed; extracted); each result links to its document |
| `#jobs` | the batch passes running now (name, progress bar, done/total, note, heartbeat, host) and the recent ones; the navigation shows a badge with the running count; the readings asked for (how many wait in all, the newest fifty listed, and what came back with its outcome) and which model the vision step resolves to; at the foot, Health: what every ailment of `prax heal` finds right now (`GET /heal`; the check reads every chunk, so the page is drawn first and the panel filled in after, and the result is kept for the page's live refreshes until "check again" or five minutes; after a repair only the repaired ailments are looked at again), a "repair" on each ailment that can be repaired (`POST /heal` with that check — one ailment at a time, so what you meant to leave alone stays alone), an ailment's offers where it has them (`unreadable-documents`: OCR over all of them, the vision model over their scanned pages; `unread-figures`: the vision model over the figures nobody has read, captioned or every image — `POST /readings/bulk`) and, when there are several, a button that repairs them all together (a job; nothing is deleted); above them the model servers `prax.yaml` names (`GET /models/servers`): reachable or not, model file, slots, whether it sees images, and — when started with `--metrics` — requests running and waiting, tokens per second, tokens read and the share the prompt cache served |
| `#ask` (`#ask?question=…&backend=&doctype=&limit=&steps=&tokens=` asks that question on arrival; what the asking model can do: `docs/ask.md`) | a conversation laid out like a chat. The thread holds the turns — the question as a bubble on the right, then, while the model surfs, the trail as it happens (each step's action and what it brought, its note in italics under it, the newest pulsing) and a pending line naming the model; then the answer as prose with `[n]` citation chips, the trail folded under it ("N steps, M set aside"; a `[n]` in it opens the source like one in the answer), a meta line with model, steps, seconds, cost, tokens, "N sources, M cited" and "keep on page…" (a form that appends the answer to a chosen page, seeds a synthesis page, or keeps it as a standing question — `POST /questions` — a page of its own the door asks again when the library learns something about it). The composer sits at the foot of the thread and stays in view (Enter asks, Shift+Enter breaks a line; options under it: which model answers — the host's default, a named model, Claude, or bundle only — document type, passages per search, steps (how many the model may take before answering; 0 answers from the first search alone) and reading (its budget in tokens, at most what the chosen model's context holds; the ceiling follows the model chosen); "New ask" forgets the conversation). The sources of the selected turn (the newest, or the one whose "N sources" was clicked) are a sticky column beside the thread: one card per passage with its number, title (opening the document at the chunk), kind, heading, page, a snippet with "more", cited ones outlined, uncited ones dimmed, and the graph's facts about the document as chips. A plain click on `[n]` in an answer scrolls to and flashes that card (a modified click opens the document). Every answered turn is kept in the tab's `sessionStorage` (`prax.ask`); a follow-up sends the earlier turns as `history`; a reload shows the conversation instead of asking again; it ends with the tab. Under 900 px the sources stack below the thread |
| `#doc/<id>?chunk=<chunk_id>`, `#doc/<id>/<chunk_id>`, `#doc/<id>?find=<quote>`, `#doc/<id>?figures=1` | header with title, metadata (creators, date, DOI, source, tags, collections, extractor stamp, citation count, the former title when the title pass replaced it) and an action row drawn out over the page's width in three zones — at the left what the document is (mime, chunks, chars, id) and, past a hairline, how to open it ("open original", "raw text", "N figures…"); centred, what can be assigned or asked of it ("add a note" or "edit page", "domains…", "promote", "read again…", "ask again"); at the right edge what takes it out of the way ("retire…", quieter) — each zone its own element (`doc-actions-info`, `-open`, `-edit`, `-remove`) for a stylesheet; an outline of headings; the body rendered chunk by chunk, each with a kind badge and page number, tables from their grids, code as code, a figure as the image out of the original with its caption and what the vision model read in it; a reference entry as written with, under it, the library document it cites when the `references` pass matched one ("likely" with the score for a title match, "?" between twins, nothing when unmatched), and an in-text marker "[12]" in the prose linked to what entry 12 cites, or to the entry when nothing matched; an ask block of a page (`docs/ask.md` "Ask blocks") on a plate with the question on its rim, "standing question · asked <day> by <model> · ask again" (or "not yet asked · ask now"; a block edited by hand loses its plate for a rule and says "edited by hand; the door left it · answer anew", which asks for confirmation since what was written inside goes; while the pass has the block in hand — after a click, or found so on arrival, `GET /page/{slug}` says which in `asking` — the rim reads "asking…" and pulses until the change poll re-renders the page with the revision), the answer and its source list inside, the markers themselves invisible; "N figures…" in the action row (or `?figures=1`) unfolds a strip of every picture of the document (the link reads "fold the figures" while it is open, and folds it) — a paper's figures, a talk's frames, a scan's pages — each captioned (the reference's caption, else the first words of a reading), placed by page or moment, and a link to its chunk; the requested chunk highlighted and scrolled into view (`find` locates the chunk holding a quote client-side, which is how an edge's evidence in the graph panel and a review item link to their chunk; chunk ids are disposable, quotes are not); an image document shows the image itself above its description; the view takes the whole page width (the outline and the context column grow a little with it, the text gets the rest; prose keeps a measure of 80 characters, tables, code and figures may use the full column); a context column (`GET /doc/{id}/context`): summary, entity chips opening the graph, similar documents, documents sharing entities, cited by, cites (a citation the references pass matched by title says "likely", a tie between twins "?"; Crossref's and a printed id's say nothing), same authors, Zotero parent and siblings |
| `#browse?title=&source=&mime=&offset=` | paged document list with filters; a row opens the document |
| `#graph` | the overview: the 30 most connected concepts, methods, tools and datasets as a force layout; a double-click on a node opens its neighbourhood; the view takes the whole page width like a document's, the canvas getting what the panel does not |
| `#graph?q=…` / `#graph?entity=…` | entity search, then the entity's neighbourhood as a force layout on a canvas (`GET /traverse`, one hop): nodes coloured by type and sized by degree, edges dashed when inferred or ambiguous and labelled with the relation around the selected node; a click selects a node and expands it by one more hop, forty neighbours at a time (the panel says how many more there are and draws them on request); a second click on the selected node folds it again, taking with it what only that expansion brought in; the side panel lists the selected node's edges with confidence, evidence and the source document; hover for the name or the edge's evidence, drag a node to move it, drag the background to pan, wheel to zoom |
| `#pages` (standing questions listed first with what is new for each — "1 new document · ask again", or "settled · ask again anyway" — `GET /questions`, `POST /questions/run`; the ask blocks of other pages in the same group, each as its question "in <page>" with the same state, or "not yet asked · ask now", or "edited by hand; left · answer anew"; briefings in a group of their own; a question page's action row has "ask again", its meta line says when it was asked and how often it moved) | the wiki: create topic and project pages; lists by kind with revision and author; a row opens the document view with the editor, whose "+ standing question" writes an ask block's markers at the cursor (the question asked in a prompt); saving a page with a new block starts the pass that answers it |
| `#doc/<id>?edit=1` | a page's document view with the editor open: textarea, title (change it to rename the page — its address, the slug, stays, and its entity in the graph is renamed with it so its edges stay its own), change note, revision list ("edit page" in the action row opens it and reads "close the editor" while it is open; that link and Cancel close it); "add a note" on a non-page document creates an addendum linked to it; the context column shows notes on a document, a project's members, and "add to project" |
| `#review?rel=&unmapped=&offset=` | the review queue, filtered by relation and unmapped/typed: each misfit triple with its reason, evidence and source document, and a row form to drop it, mark it an ontology gap, or fix its types or relation from the current ontology and link it as an edge; "drop all matching" and "replay against ontology" act on the whole filter |

The listings and the document view are live: every ten seconds the UI
asks `GET /changes` for the store's change stamp (it moves on any commit
— a worker's heartbeat, a reading that came back, a capture), and when
it moved, the page open is drawn again in place — quietly: what it shows
stays until the new page is ready, the scroll position with it; nothing
happens while something is being typed, an upload runs or the tab is
hidden. "Loading…" appears only when a page is entered.

## Look

The identity is `docs/design/BRIEF.md`: a praxinoscope in elevation,
printed in three passes, as the mark; four values — ground, tone, key,
colour — as the whole theme; the furniture of a Victorian label without
its costume as the page. What the UI does with it:

- **The mark** is inlined in the masthead (`index.html`, the *medium*
  reduction at 40 px — the ladder says swap the file, never scale the
  full one) so the theme's custom properties reach its fills; the tab's
  favicon is the static Bindery `favicon.svg` (a favicon cannot read a
  custom property). The wordmark is live text in the grotesk.
- **Themes** are `[data-prax-theme]` blocks in `style.css` — Bindery
  (default), Dessau, Riso, Cyanotype, Night, Funk — each setting the four
  values plus the derived `--prax-panel/-dim/-faint/-rule` and
  `--prax-blend` (Night stops the colour pass multiplying). The names
  the views use (`--bg`, `--fg`, `--muted`, `--line`, `--accent`,
  `--highlight`, `--mark`, the kind colours) are drawn from them, so a
  theme is those eight lines and nothing else; the graph canvas reads
  the same names when it draws. "Follow the system" is Bindery by day
  and Night when the system prefers dark. `?theme=night` in the address
  tries one for that load without saving it.
- **Type**: Bricolage Grotesque for the chrome and every label, Literata
  for anything read — chunks, answers, snippets, captions — vendored
  under `vendor/fonts/` (OFL, latin and latin-ext subsets, loaded by
  `unicode-range`), with the brief's fallbacks.
- **Ornament**, three pieces and no more: the double rule under the
  masthead; the rule that ends in a lozenge (`rule()` in `app.js`,
  between a document's head and its body, under the sources' head);
  plates — panels held by four drawn corner ticks instead of a border,
  a shadow or a radius (`.plate`, a mask so the tick takes the theme's
  rule colour): the source cards (their ticks in the colour when cited),
  a figure, the graph canvas, the token prompt. Labels are small caps
  with wide tracking. Nothing decorative is a control.

## Settings

The gear in the header opens a small dialog: the theme (the six above,
or follow the system), the passages per ask and the hits per search. It
is kept in `localStorage` (`prax.settings`) in this browser only and
never sent to the door; `theme.js` in the head applies the theme before
the first paint, so a reload does not flash (older saved values —
light, dark, paper — map onto Bindery and Night). Other browser-side
preferences go into the same dialog and the same key.

## Files

    src/prax/ui/
      index.html        the page: nav, a view container, script tags
      lib.js            the pure helpers (escaping, hash parsing, chunk locating, citation links); also loaded by the node tests
      app.js            API client, one render function per view, the error reporter
      style.css         the six themes as custom properties, the type, the ornament, the views
      theme.js          applies the chosen theme before the first paint (loaded in <head>)
      favicon.svg       the mark, one pull and a colour, Bindery (static)
      vendor/fonts/     Bricolage Grotesque and Literata, woff2 subsets, with their OFL texts
      vendor/marked.min.js   Markdown renderer (MIT), pinned version noted in vendor/VERSIONS

## Tests and errors

`tests/test_ui_js.py` parses both scripts with `node --check` and runs
`node --test tests/ui`, the unit tests of `lib.js` (router, chunk locator,
citation links, escaping), when node is installed; nothing else executes
JavaScript, and no browser automation is part of the suite. A client
error (uncaught exception or rejected promise) is shown in the status
area and posted to `POST /ui/error`, which the door logs at warning level
with the hash and the browser, so a blank view leaves a line in the
door's log.

## Rules

- The browser never talks to SQLite or the archive directly; every byte
  comes through the door. New UI data means a new read endpoint.
- Responses stay agent-sized where an agent uses them; browsing endpoints
  are separate and may be larger (a document's chunks are one request).
- Access is the door's bearer token, inside the private network. The page loads
  without it (static files are open); the first API call that returns
  401 shows a token prompt, `POST /session` turns the token into an
  HttpOnly cookie, and the token is never kept in browser storage.
- What the UI renders is often somebody else's text: a captured page's
  Markdown, a model's answer, and marked passes raw HTML in it through.
  The door sends the UI's files with a content security policy
  (`api.UI_POLICY`): script only from the UI's own files, nothing
  inline (which is why the theme script is `theme.js`, not a `<script>`
  block), no `javascript:` links, forms only to the door, images only
  from the door or `data:`, no framing. A `<script>` or an `onerror=`
  in a document or an answer is inert. Keep it so: no inline scripts or
  handlers in `index.html` or in rendered HTML.
- No state of its own beyond the browser's: the settings (`localStorage`)
  and the ask conversation (`sessionStorage`, gone with the tab) are
  conveniences of one browser; anything worth keeping — an answer, a
  note — goes to a page behind the door. If the UI ever needs saved
  searches, that is a table behind the door.
