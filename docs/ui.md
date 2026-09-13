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
| `GET /doc/{id}/chunks` | the document as its chunks in order: id, seq, kind, heading, page, locator, text, table data |
| `GET /entities?q&limit` | entities whose name contains `q`: id, name, type, degree |
| `GET /ontology` | the current ontology: version, entity types, relations with domain and range |
| `GET /review?limit&offset&open&rel&unmapped` | review-queue items with the total, filtered by relation and by unmapped (untyped) versus typed items; `POST /review/{id}` closes one (`dropped`, `ontology`, or `linked` with optional type and relation overrides, which writes the edge with the item's evidence and source); `POST /review/bulk` closes every open item matching a filter; `POST /review/replay` links the typed items the current ontology accepts |
| `GET /pages?kind`, `GET /page/{slug}`, `GET /page/{slug}/revision/{n}` | pages (notes, projects, topics) with their revisions; `PUT /page/{slug}` creates or revises (409 when an agent would overwrite a person), `POST /page/{slug}/append` adds a section, `POST /project/{slug}/members` adds a document to a project |
| `GET /graph/overview?limit&min_shared` | the most connected concepts, methods, tools and datasets, the edges among them, and co-occurrence links between hubs sharing source documents |
| `POST /ask {question, limit, doctype, backend, history}`, `GET /ask/config`, `POST /ask/save {slug, heading, result}` | the passages (one per document, with chunk and document ids) and graph facts for a question, and an answer citing them when a backend answers (`local`, `claude`, `none`; the host's default from `/ask/config`); `history` is the conversation so far as `[{question, answer}, …]` (the last six turns ride along for the model, and a follow-up that leans on them — short, or pointing back with "it", "that", "the second one" — is searched together with the previous question; the answer cites only this turn's passages; `turns_before` in the result says how many were used); `save` appends a result to a page as the agent with a source list and `annotates` edges |
| `GET /doc/{id}/domains`, `PUT /doc/{id}/domains {domains}`, `POST`/`DELETE /doc/{id}/domains/{name}` | the document's domain set (which ontology modules it is read against; null means every module) with the modules to choose from; replace, add one, remove one; `GET /search?domain=` keeps one domain's documents |
| `POST /ingest/file` (multipart `file`, `title`, `domains`, `tags`, `session`, `by`), `POST /ingest/html {url, html, title, domains, tags, session, mode, note}`, `POST /ingest/url {url, title, domains, tags, session}`, `GET /inbox?limit` | captures: an uploaded file, a page as the browser rendered it, a URL the door fetches; each returns the document id, whether it was new, whether it is searchable already, its domains and the previous capture of the same URL; `/inbox` lists recent captures with state, the drop folder path and the domains to choose from |
| `GET /promote?limit`, `POST /doc/{id}/promote {reason}`, `DELETE /doc/{id}/promote` | the documents flagged for the expensive model's pass with their status, and scored candidates (project members, synthesis sources, notes, library citations); set and clear the flag |
| `POST /doc/{id}/retire {reason, duplicate_of}`, `DELETE /doc/{id}/retire`, `POST /inbox/dedupe?commit=` | retire a document (out of search and the graph, row and file kept) and bring it back; retire the duplicate captures of every URL (dry run without `commit`) |
| `GET /work/{step}?limit&scope`, `POST /work/{step}`, `POST /work/session`, `POST /work/session/{id}`, `POST /vectors/merge` | the work protocol: a leased batch for parse, titles, extract or embed (`scope` captures or all), the results applied by the door, a worker's session job with heartbeats, the delta indexes folded into the main files |
| `GET /documents?domain`, `GET /doc/{id}/context?domain` | browse and the document's similar-documents list within one ontology module; the search, browse and document views carry a module selector ("every module" is no filter; a document without a domain set is in every module) |
| `GET /heal?check&examples`, `POST /heal` | the recurring damage in the store and its repair: placeholder entities, mangled names, self-edges, stale jobs (howto 3m) |
| `GET /changes`, `GET /jobs?limit`, `POST /vectors/release` | the change stamp and running-job count the UI polls; running and recent batch jobs with heartbeat, progress and note; drop the door's index views so a batch job on the same machine can replace the files |
| `GET /doc/{id}/context?limit` | what places a document in the library: extraction summary and entities, citations in and out (library documents resolved), nearest documents by vector (centroid of the document's chunk vectors, one KNN), documents sharing its entities or authors, Zotero parent, siblings, collections and tags |

## Routes (hash-based, one page)

| Route | View |
|---|---|
| `#search?q=…&mode=hybrid&kind=&doctype=` | query form with kind and document type (PDFs, web pages, images, text files, notes); results as cards: title, kind badge, heading path, page, snippet with match markers, which side found it (fts / vec ranks); a "doc" side marks hits found through the document field; a card opens `#doc/<id>?chunk=<chunk_id>`; "original" opens `/doc/<id>/original#page=N` in a new tab |
| `#promote` | the queue for the expensive model: flagged documents (who, why, when, done or pending under the promote step's model) with an un-promote control, and candidates with their score breakdown and a promote button; a document page has "promote" in its action row, and "domains…" to set which ontology modules it is read against (shown as `domains: family, research` in its meta line) |
| `#inbox` | captures: a drop zone and file picker (several files at once, or a whole folder), domain checkboxes and tags applied to the upload, a title for a single file; a URL form the door fetches; the drop folder's path; the recent captures with source, domains, time and state (pending, indexed, extracted); each result links to its document |
| `#jobs` | the batch passes running now (name, progress bar, done/total, note, heartbeat, host) and the recent ones; the navigation shows a badge with the running count |
| `#ask` (`#ask?question=…&backend=&doctype=&limit=` asks that question on arrival) | a conversation laid out like a chat. The thread holds the turns — the question as a bubble on the right, the answer as prose with `[n]` citation chips, a meta line with model, seconds, cost, tokens, "N sources, M cited" and "keep on page…" (a form that appends the answer to a chosen page or seeds a synthesis page). The composer sits at the foot of the thread and stays in view (Enter asks, Shift+Enter breaks a line; options under it: which model answers — the host's default, a named model, Claude, or bundle only — document type, passages per turn; "New ask" forgets the conversation). The sources of the selected turn (the newest, or the one whose "N sources" was clicked) are a sticky column beside the thread: one card per passage with its number, title (opening the document at the chunk), kind, heading, page, a snippet with "more", cited ones outlined, uncited ones dimmed, and the graph's facts about the document as chips. A plain click on `[n]` in an answer scrolls to and flashes that card (a modified click opens the document). Every answered turn is kept in the tab's `sessionStorage` (`prax.ask`); a follow-up sends the earlier turns as `history`; a reload shows the conversation instead of asking again; it ends with the tab. Under 900 px the sources stack below the thread |
| `#doc/<id>?chunk=<chunk_id>`, `#doc/<id>/<chunk_id>`, `#doc/<id>?find=<quote>` | header with title, metadata (creators, date, DOI, source, tags, collections, extractor stamp, citation count, the former title when the title pass replaced it) and "open original"; an outline of headings; the body rendered chunk by chunk, each with a kind badge and page number, tables from their grids, code as code; the requested chunk highlighted and scrolled into view (`find` locates the chunk holding a quote client-side, which is how an edge's evidence in the graph panel and a review item link to their chunk; chunk ids are disposable, quotes are not); an image document shows the image itself above its description; a context column (`GET /doc/{id}/context`): summary, entity chips opening the graph, similar documents, documents sharing entities, cited by, cites, same authors, Zotero parent and siblings |
| `#browse?title=&source=&mime=&offset=` | paged document list with filters; a row opens the document |
| `#graph` | the overview: the 30 most connected concepts, methods, tools and datasets as a force layout; a double-click on a node opens its neighbourhood |
| `#graph?q=…` / `#graph?entity=…` | entity search, then the entity's neighbourhood as a force layout on a canvas (`GET /traverse`, one hop): nodes coloured by type and sized by degree, edges dashed when inferred or ambiguous and labelled with the relation around the selected node; a click selects a node and expands it by one more hop, forty neighbours at a time (the panel says how many more there are and draws them on request); a second click on the selected node folds it again, taking with it what only that expansion brought in; the side panel lists the selected node's edges with confidence, evidence and the source document; hover for the name or the edge's evidence, drag a node to move it, drag the background to pan, wheel to zoom |
| `#pages` | the wiki: create topic and project pages; lists by kind with revision and author; a row opens the document view with the editor |
| `#doc/<id>?edit=1` | a page's document view with the editor open: textarea, title, change note, revision list; "add a note" on a non-page document creates an addendum linked to it; the context column shows notes on a document, a project's members, and "add to project" |
| `#review?rel=&unmapped=&offset=` | the review queue, filtered by relation and unmapped/typed: each misfit triple with its reason, evidence and source document, and a row form to drop it, mark it an ontology gap, or fix its types or relation from the current ontology and link it as an edge; "drop all matching" and "replay against ontology" act on the whole filter |

## Settings

The gear in the header opens a small dialog: the theme (follow the
system, light, dark, or paper — a warm sepia), the passages per ask and
the hits per search. It is kept in `localStorage` (`prax.settings`) in
this browser only and never sent to the door; an inline script in
`index.html` applies the theme before the first paint, so a reload does
not flash. Themes are `data-theme` on `<html>` over the same CSS
variables: no attribute follows `prefers-color-scheme`, an explicit
choice wins over it, and the graph canvas reads the variables when it
draws, so it follows too. Other browser-side preferences go into the
same dialog and the same key.

## Files

    src/prax/ui/
      index.html        the page: nav, a view container, script tags
      lib.js            the pure helpers (escaping, hash parsing, chunk locating, citation links); also loaded by the node tests
      app.js            API client, one render function per view, the error reporter
      style.css         layout, badges, highlight; the themes as CSS variables (system, light, dark, paper)
      theme.js          applies the chosen theme before the first paint (loaded in <head>)
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
