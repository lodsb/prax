# Using prax from scripts, agents and other tools

Everything in prax goes through one HTTP service, the door. The web UI,
the `prax` command, the browser extension and the MCP server are all
clients of it, and so is anything you write. There is no second API and
no way in through the database file.

This page is the practical side: the shapes of the calls, a few working
recipes, and the contracts a tool can rely on. What the endpoints
return in detail is in `docs/ui.md`. The reasoning is in
`docs/architecture.md`.

## Pointing at a door

    export PRAX_DOOR=http://127.0.0.1:8000     # or the board on your network
    export PRAX_TOKEN=…                        # when the door asks for one

The `prax` command takes `--door` and `--token` too, so one shell can
talk to the laptop's store and the next to the one in the hall
cupboard. A door on loopback without a token answers loopback only.
Over a network it wants `Authorization: Bearer …`.

## From a shell

Every command that reads prints JSON with `--json`, so the library is a
few lines of shell away from anything:

    # the ten documents a query finds, as a table
    prax search --json "wave digital filter" -n 10 \
      | jq -r '.[] | [.doc_id, .title] | @tsv'

    # what the papers about one method cite, counted
    curl -s -H "Authorization: Bearer $PRAX_TOKEN" \
      "$PRAX_DOOR/traverse?entity=antiderivative+antialiasing&hops=1" \
      | jq -r '.[] | select(.source_doc) | .source_doc' | sort -u \
      | while read -r id; do
          curl -s -H "Authorization: Bearer $PRAX_TOKEN" \
            "$PRAX_DOOR/doc/$id/context" | jq -r '.cites[]?.title'
        done | sort | uniq -c | sort -rn | head

    # what came in today
    curl -s -H "Authorization: Bearer $PRAX_TOKEN" \
      "$PRAX_DOOR/documents?limit=50" \
      | jq -r --arg day "$(date +%Y-%m-%d)" \
          '.items[] | select(.added_at | startswith($day)) | .title'

    # has anything changed since the last run?
    curl -s -H "Authorization: Bearer $PRAX_TOKEN" "$PRAX_DOOR/changes"

`GET /changes` returns a stamp that moves whenever the store did. Keep
the last one in a file, and a cron job can work only when there is
work. The UI polls exactly this.

## The endpoints a tool wants

| Call | What it gives |
|---|---|
| `GET /search?q=…&limit=&doctype=&domain=&kind=` | compact hits: document id, chunk id, title, a snippet, and which side found it. Stopwords and the entries of reference lists are left aside; `kind=reference` searches those on purpose |
| `GET /get/{id}?offset=&max_chars=` | one document as a window, with `text_len` so you can page |
| `GET /chunk/{id}`, `GET /doc/{id}/chunks` | one addressable region, or all of a document's: text, kind, heading path, page, and `data`. `data` holds a table's grid, a figure's reference, an equation's LaTeX, or a reference entry's number, surnames, year and title, with `cited` (the library document it cites, the score, how) once matched |
| `GET /doc/{id}/context` | what places a document: summary, entities, citations both ways, similar documents, its pages and projects |
| `GET /traverse?entity=…&hops=1` | the graph around a name, every edge with its evidence and producer |
| `GET /entities?q=` | names, types, how connected each is |
| `POST /ask {question, steps, stream}` | passages and graph facts, and an answer with citations when the host has a model (`docs/ask.md`) |
| `POST /questions {result, options}` | keep an ask's result as a standing question: a page asked again when the library learns something about it |
| `GET /questions` | each standing question with what is new for it: the question pages first, then the ask blocks of other pages as `slug#id`. A row carries `held` when a hand edited the block's interior and `asking` while the pass has it in hand |
| `POST /questions/run {slug, force, briefing, release}` | ask again what is due, as a job. `slug` may name a page or a block. `release` answers a held block anew. `briefing` writes the day's page after |
| `GET /page/{slug}` | the page with its text, revisions and `blocks`: each ask block with its state, and `asking` naming the blocks the pass is answering right now (`asking_page` for a question page). An ask block is `<!-- prax:ask id=q1 "…" -->` … `<!-- /prax:ask id=q1 -->` in any page's text (`docs/ask.md`, "Ask blocks") |
| `PUT /page/{slug}`, `POST /page/{slug}/append` | a Markdown page, or a section appended to one. A page saved with a block not yet answered starts the pass for it and answers `job` |
| `POST /ingest`, `/ingest/file`, `/ingest/url` | text, a file, or a URL for the door to fetch |
| `POST /link` | one edge, with its evidence and your name as producer |
| `GET /documents?tag=&domain=&doctype=` | the library filtered, newest first |
| `GET /stats`, `GET /jobs`, `GET /changes` | what the store holds, what is running, whether anything moved |

Responses are small on purpose: snippets and ids, never whole
documents. A model's context stays cheap, and a script pages through
what it needs.

## From an agent

An agent gets everything above and nothing more. There is no second
API and no privileged path. `prax.mcp_server` puts the same door in
front of any MCP client as tools: `search`, `get`, `get_chunk`,
`context`, `documents`, `traverse`, `link`, `ask`, `get_page`,
`write_page`, `append_page`, `ingest`, `ingest_file`, `capture_url`,
`promote`, `set_domains`. It is a proxy: one HTTP call per tool, no
logic of its own. The door's handlers are the whole contract, and an
agent can do nothing a script could not.

    PRAX_DOOR=http://127.0.0.1:8000 PRAX_TOKEN=… python -m prax.mcp_server

Two things make an agent's work safe to keep beside your own:

- **Everything is addressable.** Document ids, chunk ids with character
  ranges, entity names, page slugs. An answer can point at the lines it
  came from.
- **Everything written carries its producer.** An edge says which
  model or person wrote it, from which document, under which ontology
  version, with the sentence it was read from. A whole run can be
  retired later without touching anyone else's work. A model never
  overwrites a person's page; it appends.

The ontology is the schema a workflow can trust. It is typed,
versioned, stamped on every edge, and written by hand in
`ontology/*.yaml`. Every recipe `calls_for` its ingredients and every
build is `made_with` its parts, so a shopping list for three recipes
is a traversal, not a prompt.

A worked example: `clients/claude-plugin/` packages the MCP server for
Claude Code with a skill and a few commands
(`docs/claude-workflow.md`). Nothing in the library depends on it. Any
MCP client, or plain HTTP, reaches the same tools.

## Adding a source

An importer is a reader that yields items: either a document of its
own with a key and a version, or a link for the door to fetch.
`prax.importers.feed.run` sends them through the door and skips what
the library already holds. The GitHub, chat-export and links importers
are one file each. `docs/sources.md` lists what exists and what would
fit.

The drop folder takes anything a parser can read. A subfolder names
the ontology module to read it against. The browser extension posts
what a browser can see, including pages behind a login, as a
self-contained snapshot.

## What you can rely on

- **One writer.** The door is the only process that writes; every
  client is HTTP. Nothing you run can corrupt the store by racing it.
- **The originals are never derived.** They sit in `archive/` under
  their SHA-256. Everything else is a model's or a parser's work over
  them: text, chunks, vectors, edges, summaries, figure readings. It is
  kept so it need not be repeated, and it can be made again from the
  originals alone.
- **Formats you can leave with.** One SQLite file, a folder of
  originals named by hash, Markdown pages, YAML ontology modules.
- **Nothing is spent without being asked.** A worker refuses a paid
  model unless the command said so. The door only ever asks for
  readings a local model can do.
