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
| `GET /documents?limit&offset&title&source&mime` | documents without text, newest first: id, title, mime, added_at, parsed_at, source_url, meta, chunk count |
| `GET /doc/{id}/original` | the archived original bytes with their MIME type; `Content-Disposition: inline` so a PDF opens in the browser's viewer (`#page=N` from a chunk's locator) |
| `GET /doc/{id}/text` | the Markdown text artifact as `text/markdown` |
| `GET /doc/{id}/chunks` | the document as its chunks in order: id, seq, kind, heading, page, locator, text, table data |
| `GET /entities?q&limit` | entities whose name contains `q`: id, name, type, degree |

## Routes (hash-based, one page)

| Route | View |
|---|---|
| `#search?q=…&mode=hybrid&kind=` | query form; results as cards: title, kind badge, heading path, page, snippet with match markers, which side found it (fts / vec ranks); a card opens `#doc/<id>?chunk=<chunk_id>`; "original" opens `/doc/<id>/original#page=N` in a new tab |
| `#doc/<id>?chunk=<chunk_id>` | header with title, metadata (creators, date, DOI, source, tags, collections, extractor stamp) and "open original"; an outline of headings; the body rendered chunk by chunk, each with a kind badge and page number, tables from their grids, code as code; the requested chunk highlighted and scrolled into view |
| `#browse?title=&source=&mime=&offset=` | paged document list with filters; a row opens the document |
| `#graph/<entity>` (Stage 3) | entity search, neighbourhood as an SVG force layout, expand by click, edges labelled with relation and confidence, source documents one click away; `link` through the API |

## Files

    src/prax/ui/
      index.html        the page: nav, a view container, script tags
      app.js            hash router, API client, one render function per view
      style.css         layout, badges, highlight; light and dark via prefers-color-scheme
      vendor/marked.min.js   Markdown renderer (MIT), pinned version noted in vendor/VERSIONS

## Rules

- The browser never talks to SQLite or the archive directly; every byte
  comes through the door. New UI data means a new read endpoint.
- Responses stay agent-sized where an agent uses them; browsing endpoints
  are separate and may be larger (a document's chunks are one request).
- Access is the door's bearer token, over Tailscale only.
- No state of its own; if the UI ever needs saved searches or notes, that
  is a table behind the door.
