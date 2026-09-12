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
| `POST /ask {question, limit, doctype, backend}`, `GET /ask/config`, `POST /ask/save {slug, heading, result}` | the passages (one per document, with chunk and document ids) and graph facts for a question, and an answer citing them when a backend answers (`local`, `claude`, `none`; the host's default from `/ask/config`); `save` appends a result to a page as the agent with a source list and `annotates` edges |
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
| `#ask?question=…&backend=&doctype=&limit=` | a question, which model answers (the host's default, the local model, Claude, or bundle only), document type and passage count; the answer rendered with `[n]` as links to the cited chunk; "keep on page" appends it to a chosen page; the passages as cards, cited ones marked, each with its graph facts as chips |
| `#doc/<id>?chunk=<chunk_id>`, `#doc/<id>/<chunk_id>`, `#doc/<id>?find=<quote>` | header with title, metadata (creators, date, DOI, source, tags, collections, extractor stamp, citation count, the former title when the title pass replaced it) and "open original"; an outline of headings; the body rendered chunk by chunk, each with a kind badge and page number, tables from their grids, code as code; the requested chunk highlighted and scrolled into view (`find` locates the chunk holding a quote client-side, which is how an edge's evidence in the graph panel and a review item link to their chunk; chunk ids are disposable, quotes are not); an image document shows the image itself above its description; a context column (`GET /doc/{id}/context`): summary, entity chips opening the graph, similar documents, documents sharing entities, cited by, cites, same authors, Zotero parent and siblings |
| `#browse?title=&source=&mime=&offset=` | paged document list with filters; a row opens the document |
| `#graph` | the overview: the 30 most connected concepts, methods, tools and datasets as a force layout; a double-click on a node opens its neighbourhood |
| `#graph?q=…` / `#graph?entity=…` | entity search, then the entity's neighbourhood as an SVG force layout (`GET /traverse`, one hop): nodes coloured by type and sized by degree, edges labelled with the relation, dashed when inferred or ambiguous; a click selects a node and expands it by one more hop; the side panel lists the selected node's edges with confidence, evidence and the source document; drag to pan, wheel to zoom |
| `#pages` | the wiki: create topic and project pages; lists by kind with revision and author; a row opens the document view with the editor |
| `#doc/<id>?edit=1` | a page's document view with the editor open: textarea, title, change note, revision list; "add a note" on a non-page document creates an addendum linked to it; the context column shows notes on a document, a project's members, and "add to project" |
| `#review?rel=&unmapped=&offset=` | the review queue, filtered by relation and unmapped/typed: each misfit triple with its reason, evidence and source document, and a row form to drop it, mark it an ontology gap, or fix its types or relation from the current ontology and link it as an edge; "drop all matching" and "replay against ontology" act on the whole filter |

## Files

    src/prax/ui/
      index.html        the page: nav, a view container, script tags
      lib.js            the pure helpers (escaping, hash parsing, chunk locating, citation links); also loaded by the node tests
      app.js            API client, one render function per view, the error reporter
      style.css         layout, badges, highlight; light and dark via prefers-color-scheme
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
  HttpOnly cookie, and nothing is kept in browser storage.
- No state of its own; if the UI ever needs saved searches or notes, that
  is a table behind the door.
