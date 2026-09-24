# Web UI

A way to query the store, read documents and walk the graph without
an agent. It is a client of the HTTP door and nothing else (rationale
R14): static files in `src/prax/ui/`, served by the FastAPI process at
`/ui/`, talking to JSON endpoints. No framework, no build step, no
template engine.

## Endpoints the UI uses

The agent-shaped ones it shares with every client: `GET /search`, `GET
/get/{id}`, `GET /chunk/{id}`, `GET /traverse`, `POST /link`.

The ones added for browsing, each a thin wrapper over a store function:

| Endpoint | Returns |
|---|---|
| `GET /documents?limit&offset&title&source&mime&retired` | documents without text, newest first: id, title, mime, added_at, parsed_at, source_url, meta, chunk count. Retired ones only with `retired=1` |
| `GET /doc/{id}/original` | the archived original bytes with their MIME type. `Content-Disposition: inline`, so a PDF opens in the browser's viewer; `#page=N` comes from a chunk's locator |
| `GET /doc/{id}/text` | the Markdown text artifact as `text/markdown` |
| `GET /doc/{id}/figure/{sha}` | a figure's bytes out of the document's original, by the hash the text references (`![caption](figure:<sha256>)`). Immutable, cached for a year |
| `GET /doc/{id}/chunks` | the document as its chunks in order: id, seq, kind, heading, page, locator, text, table data |
| `GET /entities?q&limit` | entities whose name contains `q`: id, name, type, degree |
| `GET /ontology` | the current ontology: version, entity types, relations with domain and range |
| `GET /review?limit&offset&open&rel&unmapped` | review-queue items with the total, filtered by relation and by unmapped (untyped) versus typed |
| `POST /review/{id}`, `POST /review/bulk`, `POST /review/replay` | close one item as `dropped`, `ontology`, or `linked` (with optional type and relation overrides; `linked` writes the edge with the item's evidence and source); close every open item matching a filter; link the typed items the current ontology accepts |
| `GET /pages?kind`, `GET /page/{slug}`, `GET /page/{slug}/revision/{n}` | pages (notes, projects, topics) with their revisions |
| `PUT /page/{slug}`, `POST /page/{slug}/append`, `POST /project/{slug}/members` | create or revise a page (409 when an agent would overwrite a person); add a section; add a document to a project |
| `GET /graph/overview?limit&min_shared` | the most connected concepts, methods, tools and datasets, the edges among them, and co-occurrence links between hubs sharing source documents |
| `POST /ask {question, limit, doctype, backend, history, steps, tokens, stream}` | the passages (one per document, with chunk and document ids) and graph facts for a question, and an answer citing them when a backend answers (`local`, `claude`, `none`; the host's default from `/ask/config`). See below for `steps`, `history` and `stream` |
| `GET /ask/config` | the default backend, and the default and the ceiling of the reading budget per model |
| `POST /ask/save {slug, heading, result}` | append a result to a page as the agent, with a source list and `annotates` edges |
| `GET /doc/{id}/domains`, `PUT /doc/{id}/domains {domains}`, `POST` and `DELETE /doc/{id}/domains/{name}` | the document's domain set (the ontology modules it is read against; null means every module) with the modules to choose from; replace it, add one, remove one. `GET /search?domain=` keeps one domain's documents |
| `POST /ingest/file` (multipart `file`, `title`, `domains`, `tags`, `session`, `by`), `POST /ingest/html {url, html, title, domains, tags, session, mode, note}`, `POST /ingest/url {url, title, domains, tags, session}` | captures: an uploaded file, a page as the browser rendered it, a URL the door fetches. Each returns the document id, whether it was new, whether it is searchable already, its domains, and the previous capture of the same URL |
| `GET /inbox?limit` | recent captures with their state, the drop folder's path, and the domains to choose from |
| `GET /doc/{id}/routes` | the document's state (text stamp and length, pages, whether it looks like a scan, figures and equations with how many have a reading, extraction, promote flag, waiting reading) and the routes from it, grouped `text`, `figures`, `formulas`, `graph`. Each route has a label, a detail, the action a button sends (a reading request with extractor and mode, an extraction request, the promote flag), the model the step resolves to on this host and whether it is paid, whether it is available, and whether it is requested already (`prax.routes`). A page has no routes |
| `POST /doc/{id}/reading {extractor, mode}`, `DELETE /doc/{id}/reading` | ask for a named extractor on one document, or withdraw the request. Extractors: `vision-pages` with `scans` or `all`, `figures` with `captioned`, `all`, `again` or `all-again`, `formulas` with `new` or `again`, `marker` with `fast` or `balanced`, `vision`, `polish`, `pymupdf4llm-ocr` with a language, `pymupdf4llm`, `trafilatura`, `docling`. The extractor is validated against the document's type and the mode against the extractor |
| `POST /doc/{id}/extract {by}` | ask for the document's graph to be read again by the extract step's model. The stamp goes to `meta.extraction_history`, `meta.extraction_stale.requested` marks the request, and the worker's next extract pass takes the document first, whatever its scope. The new reading retires the producer's earlier edges, history kept. 400 without text |
| `POST /readings/bulk {extractor, mode, ids | mime | text_source | unreadable | read_figures, limit, dry_run}` | the same on a selection at once: ids, a MIME prefix, a text-source stamp prefix, the unreadable documents. One request per document; what the extractor does not read is skipped and counted; `dry_run` only counts (`prax reread`) |
| `GET /readings` | the waiting requests, the recently finished ones with their outcome, and what the vision step resolves to. The work protocol hands requests out before the pending captures, forced, with the mode. A worker refuses one whose model would cost money and says so in the outcome |
| `GET /promote?limit`, `POST /doc/{id}/promote {reason}`, `DELETE /doc/{id}/promote` | the documents flagged for the expensive model's pass with their status, and scored candidates (project members, synthesis sources, notes, library citations); set and clear the flag |
| `POST /doc/{id}/retire {reason, duplicate_of}`, `DELETE /doc/{id}/retire` | retire a document (out of search and the graph; row and file kept) and bring it back |
| `POST /inbox/dedupe?commit=` | retire the duplicate captures of every URL; a dry run without `commit` |
| `GET /work/{step}?limit&scope`, `POST /work/{step}`, `POST /work/session`, `POST /work/session/{id}`, `POST /vectors/merge` | the work protocol: a leased batch for parse, titles, summaries, vocabulary, extract or embed (`scope` is captures or all); the results, applied by the door; a worker's session job with heartbeats; the delta indexes folded into the main files |
| `GET /documents?domain`, `GET /doc/{id}/context?domain` | browse, and a document's similar-documents list, within one ontology module. The search, browse and document views carry a module selector; "every module" is no filter, and a document without a domain set is in every module |
| `POST /maintain {only}` | the maintenance pass as a job: the acronyms table, the document fields, the domain rules, the duplicate captures, the review queue's rule passes. `rechunk` only when named (howto 3n) |
| `POST /graph/resolve {apply, type, twins, embed, show}` | entity resolution: the plan (sure, twins, likely, with examples); with `apply`, a job merging the sure ones and the twins (`prax resolve`) |
| `POST /graph/unmerge {run}` | a round of merging and renaming taken back whole: every entity that run folded stands on its own again, every one it renamed is called what it was called, the labels it wrote are gone (`prax resolve --unmerge`) |
| `POST /import/citations {source, ids, limit, resolve_titles, refresh, dry_run}` | the citation network from OpenAlex or Crossref, as a job the door runs (`prax import citations`) |
| `POST /import/zotero/item` (multipart: `item`, `file`, `cache_text`) | one planned Zotero document written as the importer would: created, merged, refreshed, skipped or missing. `prax import zotero` plans over the copy and sends them one by one |
| `GET /heal?check&examples`, `POST /heal` | the recurring damage in the store and its repair: placeholder entities, mangled names, self-edges, stale jobs, stale parses (howto 3m) |
| `GET /models/servers` | the `openai` model servers of `prax.yaml`, one entry per server: reachable, alias, model file, slots, vision, and the load from `/metrics` when the server exposes it (tokens per second, busy slots, requests running and waiting, prompt tokens total and cached) |
| `GET /changes`, `GET /jobs?limit` | the change stamp and running-job count the UI polls; the running and recent batch jobs with heartbeat, progress and note |
| `GET /spending?days&limit` | what the paid steps have cost: the budget and what is left of it today and this month, and the ledger by step, by model and call by call (`prax.budget`, `store.spending`) |
| `GET /work/demand` | what waits for a role that has to be running to do it: the reading requests per extractor, and per role of `prax up` (`prax.work.ROLE_WORK`). The supervisor asks this to know when a borrowed card can go back |
| `GET /up`, `POST /up/command {cmd, name \| to, group, back_when}` | what `prax up` runs on the door's host, its groups, the cards and what waits; and a role change asked of it — `start`, `stop`, `restart`, `swap`, `unswap`. The door writes the command file the tray and the CLI write, and answers 409 when no supervisor runs there |
| `POST /vectors/release` | drop the door's index views, so a batch job on the same machine can replace the files |
| `GET /doc/{id}/context?limit` | what places a document in the library: extraction summary and entities, citations in and out (library documents resolved), the nearest documents by vector (the centroid of the document's chunk vectors, one KNN), documents sharing its entities or authors, Zotero parent, siblings, collections and tags |

`POST /ask` in detail. With `steps` the model surfs first: it searches
again, reads on, walks the graph and drops what is beside the point,
within `tokens` of reading. The host's default applies when `steps` is
absent; 0 is one shot. `/ask/config` gives the default and the ceiling
of `tokens` per model. The result carries the `trail`, `steps`,
`dropped` and `reading_left`. With `stream: true` the door answers one
JSON object per line as it goes: `step`, `answering`, `answer` with the
result, or `error`. `history` is the conversation so far, as
`[{question, answer}, …]`. The last six turns ride along for the model.
A follow-up that leans on them, either short or pointing back with
"it", "that" or "the second one", is searched together with the
previous question. The answer cites only this turn's passages.
`turns_before` in the result says how many turns were used.

## Routes

One page, hash-based routing. What each route shows:

### `#search?q=…&mode=hybrid&kind=&doctype=`

The query form, with kind and document type (PDFs, web pages, images,
text files, notes). Results are cards: title, kind badge, heading
path, page, snippet with match markers, and which side found it (fts
and vec ranks). A "doc" side marks a hit found through the document
field. A card opens `#doc/<id>?chunk=<chunk_id>`. "original" opens
`/doc/<id>/original#page=N` in a new tab.

### `#browse?title=&source=&mime=&offset=`

A paged document list with filters. A row opens the document.

### `#doc/<id>`

Also `#doc/<id>?chunk=<chunk_id>`, `#doc/<id>/<chunk_id>`,
`#doc/<id>?find=<quote>`, `#doc/<id>?figures=1`.

The header has the title and the metadata: creators, date, DOI,
source, tags, collections, extractor stamp, citation count, and the
former title when the title pass replaced it. Under it the action row
is drawn out over the page's width in three zones. At the left is what
the document is (mime, chunks, chars, id) and, past a hairline, how to
open it: "open original", "raw text", "N figures…". Centred is what
can be assigned or asked of it: "add a note" or "edit page",
"domains…", "process…", "un-promote" while the flag is set, "ask
again". At the right edge
is what takes it out of the way: "retire…", drawn quieter. Each zone
is its own element (`doc-actions-info`, `-open`, `-edit`, `-remove`)
for a stylesheet.

"domains…" opens a row of boxes to tick, one per module; none ticked
means every module. The set shows as `domains: family, research` in
the meta line. A change under an extraction leaves that reading stale:
the worker's next extract pass reads the document again against the
new set and retires the old reading, and the status line says so.

"process…" opens a dialog with the routes from this document
(`GET /doc/{id}/routes`). A line at the top is the document's state.
It names the text's stamp and length, the pages, and whether the
document looks like a scan (no text at all, or under 100 bytes a
page). It counts the figures and equations and how many have a
reading, names the graph's producer and date, and shows the last parse
when it failed. The routes are grouped. *The text* of a PDF: OCR
(with a language field), the vision model over the scanned pages or
over every page, marker for the mathematics, the default extractor
again, Docling. Of a captured page: the extractor again, and the
polish model for a talk's transcript. Of an image: a second reading by
the vision model. *The figures*: the ones nobody has read, every one
again under the current prompt (this model's earlier readings
replaced, another model's kept), and for a PDF the images no caption
claims (a manual's screenshots; often decoration). *The equations*: the unread ones, or every one again. *The
graph*: the local model again, with the earlier reading's edges
retired, and the promote flag for the expensive model. Each route is a
button with the detail beside it and the model the step resolves to on
this host, marked `paid` when it is Claude. A route without a model, or
with nothing to work on, is greyed with the reason. A route already
asked for reads `requested`, with `cancel` for a reading. A paid
reading asks for confirmation. The buttons post `/doc/{id}/reading`,
`/doc/{id}/extract` and `/doc/{id}/promote`; the page is drawn again
when the dialog closes. A line under the actions says what became of
the last reading request: waiting for a worker, done with the stamp and
outcome, or failed with the reason.

"N figures…" (or `?figures=1`) unfolds a strip of every picture of the
document: a paper's figures, a talk's frames, a scan's pages. Each is
captioned by the reference's caption, else by the first words of a
reading, placed by page or moment, with a link to its chunk. The link
reads "fold the figures" while the strip is open, and folds it.

The body is rendered chunk by chunk, each with a kind badge and page
number. Tables come from their grids, code as code. A figure is the
image out of the original with its caption and what the vision model
read in it. A reference entry appears as written, and under it the
library document it cites when the `references` pass matched one:
"likely" with the score for a title match, "?" between twins, nothing
when unmatched. An in-text marker "[12]" in the prose links to what
entry 12 cites, or to the entry itself when nothing matched.

An ask block of a page (`docs/ask.md`, "Ask blocks") sits on a plate
with the question on its rim: "standing question · asked <day> by
<model> · ask again", or "not yet asked · ask now". A block edited by
hand loses its plate for a rule and says "edited by hand; the door
left it · answer anew"; that link asks for confirmation, since what
was written inside goes. While the pass has the block in hand, after a
click or found so on arrival (`GET /page/{slug}` says which in
`asking`), the rim reads "asking…" and pulses until the change poll
re-renders the page with the revision. The answer and its source list
are inside; the markers themselves are invisible.

The requested chunk is highlighted and scrolled into view. `find`
locates the chunk holding a quote client-side. That is how an edge's
evidence in the graph panel and a review item link to their chunk:
chunk ids are disposable, quotes are not. An image document shows the
image itself above its description.

The view takes the whole page width. The outline and the context
column grow a little with it and the text gets the rest. Prose keeps a
measure of 80 characters; tables, code and figures may use the full
column. The context column (`GET /doc/{id}/context`) shows the summary,
entity chips that open the graph, similar documents, documents sharing
entities, cited by, cites, same authors, and the Zotero parent and
siblings. A citation the references pass matched by title says
"likely", a tie between twins "?"; Crossref's and a printed id's say
nothing.

### `#doc/<id>?edit=1`

A page's document view with the editor open: the textarea, the title,
a change note, the revision list. Change the title to rename the page.
Its address, the slug, stays, and its entity in the graph is renamed
with it, so its edges stay its own. "edit page" in the action row
opens the editor and reads "close the editor" while it is open; that
link and Cancel close it. "+ standing question" writes an ask block's
markers at the cursor, with the question asked in a prompt; saving a
page with a new block starts the pass that answers it. "add a note" on
a non-page document creates an addendum linked to it. The context
column shows the notes on a document, a project's members, and "add to
project".

### `#ask`

`#ask?question=…&backend=&doctype=&limit=&steps=&tokens=` asks that
question on arrival. What the asking model can do is in `docs/ask.md`.

The view is a conversation laid out like a chat. The thread holds the
turns. A question is a bubble on the right. While the model surfs, the
trail appears as it happens: each step's action and what it brought,
its note in italics under it, the newest pulsing, and a pending line
naming the model. Then comes the answer as prose with `[n]` citation
chips, and the trail folded under it ("N steps, M set aside"; a `[n]`
in the trail opens the source like one in the answer). A meta line
gives the model, steps, seconds, cost, tokens, "N sources, M cited",
and "keep on page…". That form appends the answer to a chosen page,
seeds a synthesis page, or keeps it as a standing question (`POST
/questions`): a page of its own that the door asks again when the
library learns something about it.

The composer sits at the foot of the thread and stays in view. Enter
asks; Shift+Enter breaks a line. Its options:

- which model answers: the host's default, a named model, Claude, or
  bundle only;
- the document type;
- passages per search;
- steps: how many the model may take before answering. 0 answers from
  the first search alone;
- reading: its budget in tokens, at most what the chosen model's
  context holds. The ceiling follows the model chosen.

"New ask" forgets the conversation.

The sources of the selected turn are a sticky column beside the
thread. The selected turn is the newest, or the one whose "N sources"
was clicked. Each passage is a card with its number, title (opening
the document at the chunk), kind, heading, page, a snippet with
"more", and the graph's facts about the document as chips. Cited
cards are outlined, uncited ones dimmed. A plain click on `[n]` in an
answer scrolls to and flashes that card; a modified click opens the
document. Every answered turn is kept in the tab's `sessionStorage`
(`prax.ask`). A follow-up sends the earlier turns as `history`. A
reload shows the conversation instead of asking again, and it ends
with the tab. Under 900 px the sources stack below the thread.

### `#graph`

The overview: the 30 most connected concepts, methods, tools and
datasets as a force layout. A double-click on a node opens its
neighbourhood. The view takes the whole page width like a document's;
the canvas gets what the panel does not.

### `#graph?q=…` and `#graph?entity=…`

Entity search, then the entity's neighbourhood as a force layout on a
canvas (`GET /traverse`, one hop). Nodes are coloured by type and sized
by degree. Edges are dashed when inferred or ambiguous, and labelled
with the relation around the selected node. A click selects a node
and expands it by one more hop, forty neighbours at a time; the panel
says how many more there are and draws them on request. A second
click on the selected node folds it again and takes with it what only
that expansion brought in. The side panel lists the selected node's
edges with confidence, evidence and the source document. Hover for the
name or the edge's evidence. Drag a node to move it, drag the
background to pan, wheel to zoom.

### `#pages`

The wiki. Create topic and project pages; the lists are by kind, with
revision and author, and a row opens the document view with the
editor. Standing questions are listed first, each with what is new for
it: "1 new document · ask again", or "settled · ask again anyway"
(`GET /questions`, `POST /questions/run`). The ask blocks of other
pages are in the same group, each as its question "in <page>", with
the same state, or "not yet asked · ask now", or "edited by hand; left
· answer anew". Briefings are a group of their own. A question page's
action row has "ask again", and its meta line says when it was asked
and how often it moved.

A comment section and an advertisement are folded to one line saying
what they are ("what readers wrote · 3.4k characters", "advertisement ·
brilliant · “20% off”"); a click opens either. A recipe's ingredients
are the box they are on the page: for how many people, then every line
with its amount in bold, grouped as the recipe groups them.

### `#promote`

The queue for the expensive model. The flagged documents are listed
with who flagged them, why, when, and whether they are done or pending
under the promote step's model (`prax work --steps promote --spend`
reads them), each with an un-promote control. When something is
pending and nothing on the host would read it — no worker asks for a
paid step unless the run names it — a line says why and what would
move it. The same line sits under a requested route in the process
dialog. The candidates are
listed with their score breakdown and a promote button. A document
page has the flag under "process…" in its action row, and "un-promote"
beside it while the flag is set.

### `#inbox`

Captures. A drop zone and file picker take several files at once, or
a whole folder; hundreds are fine. They go up one at a time with a
running count in the status area, a summary every ten and at the end
(new, already in the store, failed with the reason), and the failures
listed in full. The view holds still while a batch runs, and the
summary survives its later re-renders. A file the store already has is
reported as such, so a folder can be dropped again after an
interruption. Domain checkboxes and tags apply to the upload, and a
title to a single file. A URL form has the door fetch a page. The drop
folder's path is shown. The recent captures are listed with source,
domains, time and state. The states are pending, indexed, extracted,
and "no text found" when every extractor tried it and found none. That
last one is a scan, most likely, so OCR or the vision model is asked
for on its page. Each result links to its document.

### `#jobs`

The batch passes running now (name, progress bar, done/total, note,
heartbeat, host) and the recent ones. The navigation shows a badge
with the running count. The readings asked for are listed: how many
wait in all, the newest fifty, what came back with its outcome, and
which model the vision step resolves to. Above them are the model
servers `prax.yaml` names (`GET /models/servers`): reachable or not,
model file, slots, whether it sees images, and, when started with
`--metrics`, requests running and waiting, tokens per second, tokens
read and the share the prompt cache served.

Under them is this host (`GET /up`): what `prax up` runs, and the
groups of roles that share a card (`docs/howto.md` 4b). A group says
who holds it, what waits for the roles that are down (`GET
/work/demand`), the cards' free memory, and a button that hands it
over (`POST /up/command`, which writes the supervisor's command file).
A swap comes back on its own when nothing waits for the borrower. On a
host without `prax up`, or one whose roles share nothing, the panel
says so.

Under that is Spending, on a host whose steps cost money: today and
this month against the budget's two numbers (`budget.daily_usd`,
`budget.monthly_usd`), then what each step and model cost over the last
thirty days. A host with only local models has an empty ledger and says
so in a line. When a limit is reached the panel says the paid steps are
held until the day or the month turns.

At the foot is Health: what every ailment of `prax heal` finds right
now (`GET /heal`). The check reads every chunk, so the page is drawn
first and the panel filled in after. The result is kept for the page's
live refreshes until "check again" or five minutes; after a repair only
the repaired ailments are looked at again. Each ailment that can be
repaired has a "repair" (`POST /heal` with that check, one ailment at a
time, so what you meant to leave alone stays alone). An ailment's
offers appear where it has them: `unreadable-documents` offers OCR
over all of them or the vision model over their scanned pages;
`unread-figures` offers the vision model over the figures nobody has
read, captioned or every image (`POST /readings/bulk`). When there are
several, a button repairs them all together, as a job. Nothing is
deleted.

### `#review?rel=&unmapped=&offset=`

The review queue, filtered by relation and by unmapped versus typed.
Each misfit triple is shown with its reason, evidence and source
document, and a row form to drop it, mark it an ontology gap, or fix
its types or relation from the current ontology and link it as an
edge. "drop all matching" and "replay against ontology" act on the
whole filter.

## Live views

The listings and the document view are live. Every ten seconds the UI
asks `GET /changes` for the store's change stamp. The stamp moves on
any commit: a worker's heartbeat, a reading that came back, a capture.
When it moved, the page that is open is drawn again in place, quietly.
What it shows stays until the new page is ready, and the scroll
position with it. Nothing happens while something is being typed, an
upload runs, or the tab is hidden. "Loading…" appears only when a page
is entered.

## Look

The identity is `docs/design/BRIEF.md`: a praxinoscope in elevation,
printed in three passes, as the mark; four values (ground, tone, key,
colour) as the whole theme; the furniture of a Victorian label without
its costume as the page. What the UI does with it:

- **The mark** is inlined in the masthead (`index.html`, the *medium*
  reduction at 40 px; the ladder says swap the file, never scale the
  full one), so the theme's custom properties reach its fills. The
  tab's favicon is the static Bindery `favicon.svg`, because a favicon
  cannot read a custom property. The wordmark is live text in the
  grotesk.
- **Themes** are `[data-prax-theme]` blocks in `style.css`: Bindery
  (default), Dessau, Riso, Cyanotype, Night, Funk. Each sets the four
  values plus the derived `--prax-panel`, `-dim`, `-faint`, `-rule` and
  `--prax-blend` (Night stops the colour pass multiplying). The names
  the views use (`--bg`, `--fg`, `--muted`, `--line`, `--accent`,
  `--highlight`, `--mark`, the kind colours) are drawn from them, so a
  theme is those eight lines and nothing else. The graph canvas reads
  the same names when it draws. "Follow the system" is Bindery by day
  and Night when the system prefers dark. `?theme=night` in the address
  tries one for that load without saving it.
- **Type.** Bricolage Grotesque for the chrome and every label.
  Literata for anything read: chunks, answers, snippets, captions.
  Both are vendored under `vendor/fonts/` (OFL, latin and latin-ext
  subsets, loaded by `unicode-range`), with the brief's fallbacks.
- **Ornament**, three pieces and no more. The double rule under the
  masthead. The rule that ends in a lozenge (`rule()` in `core.js`,
  between a document's head and its body and under the sources' head).
  Plates: panels held by four drawn corner ticks instead of a border, a
  shadow or a radius (`.plate`; a mask, so the tick takes the theme's
  rule colour). The plates are the source cards (their ticks in the
  colour when cited), a figure, the graph canvas, an ask block, the
  token prompt. Labels are small caps with wide tracking. Nothing
  decorative is a control.

## Settings

The gear in the header opens a small dialog: the theme (the six above,
or follow the system), the passages per ask, and the hits per search.
The settings are kept in `localStorage` (`prax.settings`) in this
browser only and never sent to the door. `theme.js` in the head applies
the theme before the first paint, so a reload does not flash. Older
saved values (light, dark, paper) map onto Bindery and Night. Other
browser-side preferences go into the same dialog and the same key.

## Files

    src/prax/ui/
      index.html        the page: nav, a view container, the script tags in load order
      lib.js            the pure helpers (escaping, hash parsing, chunk locating, citation links, the host and spending panels); also loaded by the node tests
      core.js           the API client, the session, the status line, and the helpers every view uses
      view-<name>.js    one file per view: search, doc, browse, graph, pages, review, ask, promote, inbox, jobs
      boot.js           the change poll, the views map, the router, the error reporter, the first render
      style.css         the six themes as custom properties, the type, the ornament, the views
      theme.js          applies the chosen theme before the first paint (loaded in <head>)
      favicon.svg       the mark, one pull and a colour, Bindery (static)
      vendor/fonts/     Bricolage Grotesque and Literata, woff2 subsets, with their OFL texts
      vendor/marked.min.js   Markdown renderer (MIT), pinned version noted in vendor/VERSIONS

## Tests and errors

`tests/test_ui_js.py` parses every script with `node --check` and runs
`node --test tests/ui`, the unit tests of `lib.js` (router, chunk
locator, citation links, escaping), when node is installed. Nothing
else executes JavaScript, and no browser automation is part of the
suite. A client error (an uncaught exception or a rejected promise) is
shown in the status area and posted to `POST /ui/error`. The door logs
it at warning level with the hash and the browser, so a blank view
leaves a line in the door's log.

## Rules

- The browser never talks to SQLite or the archive directly. Every
  byte comes through the door. New UI data means a new read endpoint.
- Responses stay agent-sized where an agent uses them. Browsing
  endpoints are separate and may be larger; a document's chunks are one
  request.
- Access is the door's bearer token, inside the private network. The
  page loads without it, since the static files are open. The first API
  call that returns 401 shows a token prompt. `POST /session` turns the
  token into an HttpOnly cookie, and the token is never kept in browser
  storage.
- What the UI renders is often somebody else's text: a captured page's
  Markdown, a model's answer. marked passes raw HTML in it through. The
  door sends the UI's files with a content security policy
  (`api.UI_POLICY`). It allows script only from the UI's own files and
  nothing inline, which is why the theme script is `theme.js` and not
  a `<script>` block. It allows no `javascript:` links, forms only to
  the door, images only from the door or `data:`, and no framing. A
  `<script>` or an `onerror=` in a document or an answer is inert. Keep it so: no inline scripts or
  handlers in `index.html` or in rendered HTML.
- The UI has no state of its own beyond the browser's. The settings
  (`localStorage`) and the ask conversation (`sessionStorage`, gone
  with the tab) are conveniences of one browser. Anything worth
  keeping, an answer or a note, goes to a page behind the door. If the
  UI ever needs saved searches, that is a table behind the door.
