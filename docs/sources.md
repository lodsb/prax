# Data sources

What feeds prax, how each source maps onto the store, and what each
importer must and must not do. All of these are Stage 1 work except where
noted; the store provides the `register` / `index_text` / `link` calls
they use. Every source is a client of `prax.store` (CLAUDE.md invariant 3)
and never writes back to where it read from (invariant 10).

## 1. Zotero library

The primary corpus: years of curated PDFs with author, date, tag and
collection metadata. The library lives on an external drive (not always
mounted; `<zotero dir>` below). The local Zotero directory on the dev
machine is a stale, empty 2022 install that is only useful for schema
checks.

### Where Zotero keeps things

    <zotero dir>\
      zotero.sqlite          all metadata (661 MB; mostly Zotero's own full-text index)
      zotero.sqlite.bak      Zotero's automatic backups; ignore
      storage/<ITEMKEY>/     one folder per attachment item: the file plus
                             .zotero-ft-cache, Zotero's extracted plain text

Zotero holds an exclusive lock on `zotero.sqlite` while running. The
importer therefore always copies the file first and opens the copy with
`?mode=ro`. It never opens the live database.

### Inventory (read-only census, 2026-09-06)

Schema version 121, one user library, last modified November 2023, nothing
in the trash.

| What | Count |
|---|---|
| Regular items (articles, papers, books, web pages…) | 5,738 |
| of which journalArticle / conferencePaper / book / bookSection | 4,357 / 686 / 217 / 208 |
| Attachment items | 12,193 |
| PDFs on disk | 12,061, 23.2 GB |
| HTML snapshots (imported URL) | 105 |
| Linked URLs (no file) | 3 |
| Attachments with a parent item | 5,699 |
| Standalone attachments, no parent, no metadata | 6,494 |
| Attachments whose file is missing on disk | 1 (`R3Q364PJ`) |
| `.zotero-ft-cache` text files | 11,773, 0.45 GB, average 39 KB |
| Filenames shared by more than one attachment | 1,122 names over 3,609 attachments |
| Child notes | 69 (mostly arXiv "Comment:" lines) |
| Creators / item-creator links | 5,445 / 13,267 |
| Tags / collections | 278 / 24 (audio-DSP topics: music, physical modelling, filters…) |
| Items with DOI / abstract / date | 1,682 / 3,051 / 2,844 |

Consequences for the importer:

- **Every file attachment uses a `storage:` path.** No linked-file base
  directory to resolve; the three linked URLs become URL-only documents.
- **Zotero has already extracted text for 98% of the PDFs.** The
  `.zotero-ft-cache` file next to each PDF is pdftotext output. The first
  import pass indexes from that cache immediately, so search works over
  the whole library without running Docling. Docling becomes a later
  re-parse job that upgrades the text artifact (chunks are disposable,
  rationale R3). Cache text is marked `meta.text_source = "zotero-ft-cache"`
  so the upgrade job can find it.
- **Half the library is metadata-less PDFs.** The 6,494 standalone
  attachments get their title from the filename. Many filenames are arXiv
  ids (`2104.07636.pdf`); resolving those against arXiv is a later
  enrichment, not part of the import.
- **Duplicates are real.** The same paper was often saved several times
  (four copies of one 44 KB article, for instance). Hashing collapses
  them; all Zotero keys are kept in `meta.zotero.keys`.
- **Space.** A full copy import needs about 22–26 GB for the archive plus
  roughly 1.5 GB for the database and text artifacts; the external drive
  had no room for a second copy, the system drive did. The plan is a
  scratch run there with `PRAX_DATA_DIR` set, in three steps: the
  fixture, then `--limit 500`, then the whole library. A zero-copy
  variant (hard-linking into an archive on the same NTFS volume as
  `storage/`) is an option if the store ever has to live beside the
  library.

### Scratch run result (2026-09-07)

Fixture, then `--limit 500` (36 s), then the whole library (656 s, no
errors). Dry-run inventory matched the census: 12,188 file attachments,
3 linked URLs, 17 file-less items, 83 notes, 1 missing file.

| Store after import | |
|---|---|
| Documents | 9,235 (9,142 attachments, 73 notes, 17 metadata-only, 3 URL-only) |
| of which stand for several Zotero records | 1,715 (3,043 attachment rows merged by hash) |
| Archive | 17 GB for 23.4 GB referenced; database 618 MB |
| Indexed from the Zotero text cache | 8,044 documents, 372,934 chunks |
| Waiting for the parse queue (`parsed_at NULL`) | 1,098, almost all PDFs without a cache |
| Graph | 2,967 paper and 5,129 author entities, 6,756 `authored_by` edges |

Ten of the 83 notes are empty after HTML stripping and are not imported.
The two embedded-image attachments (link mode 4, no file) are dropped.
Searching the store for "extended complex Kalman filter pitch tracking"
returns the Das 2020 URL-only document and its full-text twin first.

**The cache-less backlog** (first parse-queue pass, 2026-09-07). The 1,090
PDFs Zotero had no text for split into 28 with a text layer (indexed by
pymupdf4llm, two of them through the plain fallback), 1,006 scans with no
text layer, and 56 files that are not readable PDFs (mostly truncated
downloads; MuPDF cannot open them). Zotero's cache therefore already
covered every born-digital PDF. The scans hold 21,886 pages: 937 documents
of at most 60 pages (4,531 pages, invoices, letters, short papers) go
through the bounded OCR pass; 71 scanned books (17,355 pages) wait for a
deliberate run with a raised `PRAX_OCR_MAX_PAGES`.

**OCR pass** (RapidOCR through pymupdf4llm, 87 minutes): 283 scans got
usable text (median 3.3 K characters: letters, invoices, short papers, a
few scanned articles); 613 came back empty, and a look at them shows
single-page artwork (a font-art series of decorated letters, word clouds,
schematics), not failed OCR; 42 hit MuPDF or pymupdf4llm faults on odd
files. After both passes 8,448 documents are indexed and 779 PDFs stay
pending: the 71 books, the 56 unreadable files, the artwork, and the
faulting few. Every attempt is in `meta.parse_history`.

**Upgrade pass** (2026-09-07, pymupdf4llm and trafilatura over everything
Zotero's cache had covered; about five hours of desktop time in batches of
200 documents per process): 7,732 documents now carry pymupdf4llm
Markdown with page markers, 99 HTML snapshots trafilatura text, 68 plain
MuPDF text (oversized or layout-hostile originals), and 163 kept the cache
text because the new extraction was shorter. After re-chunking, 8,448
indexed documents hold 855,731 chunks (median text chunk 742 characters),
784,742 of them with a page number; tables, figure captions and code
listings are chunks of their own. The database is 1.8 GB. Three guards came out of this pass: lone surrogates from broken
fonts are replaced before archiving, layout analysis is capped at 400
pages and 40 MB, and documents an extractor version has already tried are
skipped so batch loops always progress.

### Schema mapping

Confirmed against the local `zotero.sqlite` (61 tables). The relevant ones:

| Zotero | Role | prax |
|---|---|---|
| `items(itemID, itemTypeID, key, dateAdded, libraryID)` | one row per item, attachment, note | `documents.meta.zotero.{key, itemType, dateAdded}` |
| `itemTypes(itemTypeID, typeName)` | journalArticle, book, webpage, attachment, note… | `meta.zotero.itemType` |
| `itemData` → `itemDataValues` via `fields(fieldName)` | title, date, DOI, url, abstractNote, publicationTitle… | `documents.title`, `source_url`, rest in `meta` |
| `itemCreators` → `creators(firstName, lastName)` | authors, editors, ordered | `meta.creators`, and `authored_by` edges |
| `itemTags` → `tags(name)` | user tags | `meta.tags` |
| `collectionItems` → `collections(parentCollectionID)` | folder tree | `meta.collections` as full paths |
| `itemAttachments(parentItemID, linkMode, contentType, path)` | the file | the archived original |
| `itemNotes(parentItemID, note)` | HTML notes | a text document per note, linked to the parent |
| `deletedItems` | trash | skipped |

`linkMode` values: 0 imported file, 1 imported URL snapshot, 2 linked file,
3 linked URL. `path` forms: `storage:name.pdf` resolves to
`storage/<KEY>/name.pdf`; `attachments:rel/path` resolves against the
linked-attachment base directory; otherwise absolute. Linked URLs (mode 3)
carry no file and become URL-only documents.

### Import rules

- One prax document per attachment file, hashed from the bytes. The same
  PDF attached to two Zotero items is one document; both keys are recorded
  in `meta.zotero.keys`.
- Parent-item metadata is attached to the attachment's document. Items with
  no attachment (a bare journalArticle record) become metadata-only
  documents whose text is title plus abstract, so they are still
  searchable.
- Notes become text documents with `meta.zotero.parent` pointing at the
  parent key. HTML is reduced to text at import.
- Nothing is parsed at import. Documents are registered with
  `parsed_at NULL` and the parse queue handles PDFs later, on the batch
  host.
- Graph seeds: `paper --authored_by--> author` for every creator, with
  `confidence = EXTRACTED`, `source_doc` = the document,
  `ontology_version` = current. Tags and collections are kept in `meta`
  only for now; mapping them onto `concept` entities is a Stage 3 decision.
- Idempotent: a re-run looks up Zotero keys already recorded in
  `meta.zotero.keys` (no file is re-read or re-hashed), skips them, and
  refreshes `meta` when the record's `dateModified` changed
  (`meta.zotero.modified` holds one stamp per key).

### What the store holds after import

| `meta` key | Content |
|---|---|
| `source` | `"zotero"` |
| `zotero.kind` | `attachment`, `url`, `metadata` (item without a file) or `note` |
| `zotero.keys` / `zotero.items` | every attachment key and parent item key this document stands for |
| `zotero.item_type`, `zotero.link_mode`, `zotero.filename`, `zotero.parent` | as in Zotero |
| `creators` | ordered `{name, first, last, type}` |
| `date`, `doi`, `abstract`, `tags`, `collections` | lifted from the item |
| `fields` | every other Zotero field (publicationTitle, pages, ISBN…) |
| `text_source` | `"zotero-ft-cache"` when indexed from the cache, else null |

Columns: `title` and `source_url` from the parent item, `mime` from the
attachment, `original_path` the file's path under `storage/`.

### Workflow

1. Copy `zotero.sqlite` and the `storage/` tree (or mount the disk
   read-only).
2. `prax import zotero <copy-dir> --dry-run` prints the inventory:
   items by type, attachments by link mode, files referenced but
   missing, and duplicate hashes across items. Nothing is sent.
3. `prax import zotero <copy-dir>` sends each planned document to the
   door, which registers it through the store. Safe to interrupt and
   re-run.
4. A worker parses what came without Zotero's cached text
   (`prax work --scope all`).

### Test fixture

A handful of open-access items are copied from the library into
`tests/fixtures/zotero/` as a `storage/` subtree plus a `zotero.sqlite`
reduced to those items (25 items, 12 storage folders, 6.3 MB). This is
the corpus for importer tests and for the Stage 2 eval queries. Every
file is under a licence that allows redistribution (CC BY 3.0 and 4.0
papers from DAFx, SMC and arXiv, an IEEE open-access letter, a Stack
Exchange page under CC BY-SA, a CC BY-SA schematic sheet);
`tests/fixtures/zotero/README.md` lists each with its licence. Personal
documents in the library and papers under a publisher's copyright are
imported into the private store but never into the fixture.

| Shape | Zotero key | Notes |
|---|---|---|
| Conference paper, one PDF, two creators, a collection | `GMY9D9QD` | Ambrits & Bank, polynomial transition regions |
| Journal letter, one PDF, DOI | `GE5DX6CB` | Průša & Rajmic, STFT magnitude reconstruction |
| One file saved four times | `Z9IJ6QGS` and three twins | exercises hash dedupe and `meta.zotero.keys` |
| Item with a child note | `4MMHC9A4` | Carr & Zukowski, arXiv comment note |
| Item with three different PDFs | `CF2VW67S` | FugueGenerator, multi-attachment parent |
| Item with an HTML snapshot, 2 MB | `HR6SU62N` | Stack Exchange question, trafilatura path |
| Item with a linked URL, no file | `ZRWHFMBJ` (attachment) | Das et al., URL-only document |
| Metadata-only item, no attachment | `IIJ9PSTU` | a GitHub repository saved as a computer program |
| One standalone PDF, no parent | `UW29C5GP` | a front-panel drawing; title-from-filename path |

Rebuilt (2026-09-11) with `scripts/make_zotero_fixture.py` from those
keys. The library drive was not mounted, so the source was a
Zotero-shaped directory assembled from the importer's private copy of
`zotero.sqlite` and the archived originals, with `.zotero-ft-cache` files
written from the store's text artifacts. The earlier fixture (2026-09-07)
held publisher-copyrighted papers and was removed from the history before
the repository went public.

## 1b. Citation sources (OpenAlex, Crossref)

Not documents but edges: `prax import citations` (a job on the door) looks each
document up by DOI (or exact title) and writes `cites` edges from the
source's reference list, with the citation count in `meta.citations`.
Read-only, no key, idempotent per document. Details in
`prax.importers.citations` and `docs/howto.md` 3f.

## 2. Browser capture

Goal: move open tabs into prax with one click, from the machine where they
are open, without any intermediate service.

### Extension

- Manifest V3, one codebase for Chrome and Firefox.
- Popup with two actions: *send this tab* and *send all tabs in this
  window*. Optional: close tabs after a successful send.
- Options page: server URL (the Pi's name or address on your private
  network) and a bearer token.
- For each tab the extension injects a content script that returns
  `document.documentElement.outerHTML`, the URL, and the title, and posts
  them to `POST /ingest/html`. Capturing the rendered DOM rather than
  re-fetching server-side keeps paywalled and script-rendered pages intact.
- A bookmarklet and a mobile share target are the fallbacks; both post only
  a URL to `POST /ingest/url`, and the server fetches.
- Each send carries a `capture_session` id (timestamp plus a short random
  suffix) so "the tabs I saved on Tuesday" is a metadata query.

### Server side

- `POST /ingest/html`: archive the HTML bytes as the original, extract text
  with trafilatura, `index_text` immediately (trafilatura is light enough
  for the serving path; Docling is not).
- `POST /ingest/url`: fetch, then the same path.
- Dedupe by hash catches identical snapshots. A re-capture of a changed
  page is a new document; the API looks up existing documents with the
  same canonical URL and records `meta.previous_capture` so versions are
  linked. Canonicalization strips tracking parameters and fragments.
- Auth: bearer token in the `Authorization` header, compared in constant
  time; the API is bound to the private network's interface only. CORS
  allows the extension origin.

The server side exists (`prax.inbox`, howto 3l): `POST /ingest/html`
and `POST /ingest/url` as described, captures with `meta.capture
{at, session, by}`, canonical URLs and `meta.previous_capture`, a domain
set per capture, CORS for the extension's origin through
`PRAX_CORS_ORIGINS`. The extension itself is designed in
`docs/extension.md`.

## 3. Inbox folder

`data/inbox/` (howto 3l). The door registers any file dropped there,
indexes text at once and leaves the rest for the worker's parse step; a
subfolder names the domain, a
JSON sidecar carries title, URL, domains and tags; consumed files are
removed because the archive holds their bytes, refused ones go to
`inbox/failed/`. Useful for PDFs that arrive by mail or download and for
any future front-end (Karakeep, Linkwarden) that can write files to a
folder. The folder is prax's own, so removing what it consumed does not
break invariant 10.

## 4. The old zoetrope disk

The hash inventory in `scripts/backfill.py` already reports duplicates
across the disk. In Stage 1 it gains `--commit`, which registers each unique
file through the store with `meta.original_path` preserved. Run against a
copy, review the dedupe report, then commit. Files that also exist in the
Zotero import dedupe automatically by hash.

## 5. Later front-ends

Karakeep (organization, local AI tagging) or Linkwarden (archival) can sit
in front of the inbox. prax re-indexes everything into its own FTS5 and
vector tables, so a front-end can be swapped or removed without touching
search.

## 6. What you keep elsewhere: `prax import`

The Zotero importer opens the database on the door's host. The importers
that came after it are clients of the door (`prax.importers.feed`): they
read an export or an API, send each item through `POST /ingest` (a
document of their own) or `POST /ingest/url` (a link the door fetches),
and run wherever the `prax` command runs. Idempotence is by key: a text
item carries `meta.<source>.key` and a version (what changes when the
source's item changes), and the run lists the library's documents of its
source first; a link asks `GET /captures?url=` before fetching. `--refresh`
re-sends what changed and retires the earlier document as replaced;
`--dry-run` lists what would be sent. Every run takes `--domain` and
`--tag`.

| `prax import …` | reads | becomes | key, version |
|---|---|---|---|
| `github [USER]` | the starred repositories of a user, or of the token's account (`PRAX_GITHUB_TOKEN`, `sources.github.token`; without one GitHub allows sixty requests an hour and a README is one) | one Markdown document per repository: description, language, stars, licence, topics (as `github:<topic>` tags), when starred and last pushed, then the README | `owner/name`, `pushed_at` |
| `chat FILE…` | Telegram Desktop's JSON export (`result.json`, one chat or all), sigtop's JSON export of Signal Desktop (`sigtop export-messages -f json`, one file per conversation), WhatsApp's "Export chat" `.txt` | one document per conversation and month: a heading per day, a line per message, attachments in brackets, the links sent listed at the end; `--links` captures each link too, the message as its note | `app/conversation/YYYY-MM`, message count and last stamp |
| `links FILE…` | a browser's bookmark file (`.html`; folders become `folder:` tags), Pocket's or Raindrop's CSV (any CSV with a `url` column), a text file of URLs, Medium's export zip (`bookmarks/`, `lists/`, `highlights/` as `medium:` tags, a highlight as the note) | a capture per link, fetched by the door | the URL |
| `project [DIR]` | a project's documentation files (`.md`, `.rst`, `.txt`, `.adoc`; never `.git`, `node_modules`, a venv, build output, vendored code); a `.prax-project` file names the project, its modules, extra tags and what to include | one document per file, tagged `project:<name>`; what the Claude Code plugin syncs at session end (`docs/claude-workflow.md`) | `<name>/<path>`, the content's hash |
| `claude [DIR\|FILE…]` | a project's Claude Code sessions (`~/.claude/projects/<project>/*.jsonl`): the person's turns and the assistant's prose; tool calls, results, thinking, side chains and injected notifications left out; `--since DATE` | one document per session, titled by the session's own title, tagged `claude` and `project:<name>`, the links sent listed at the end | `<project>/<session id>`, the transcript's length |

Neither reads an app's own database: Signal Desktop's is encrypted and
sigtop is the tool that knows how to read it; a browser's bookmarks are
exported, not opened. Nothing writes back (invariant 10). Medium serves
a public story to the door's fetch; a member-only one comes back as its
preview, and the extension is the way to capture that with your own
session.

### What else could feed it

The same shape fits most of what a person keeps: a reader that yields
`Item`s, and `feed.run`. Candidates, roughly by how much of the work is
already done:

- **Any HTML or CSV of links** already works (`links`): Hacker News
  favourites (the page), Pinboard (its bookmark export is the Netscape
  file), Raindrop, Instapaper (CSV), a Mastodon or Bluesky bookmark export
  once it is a list of links.
- **Telegram groups and channels** already work (`chat`), so a channel
  someone curates is a feed.
- **Kindle highlights** (`My Clippings.txt`, or the Kindle notebook
  export): one document per book, the highlights as quotes with their
  locations — a small reader, and the book itself is often in the library.
- **RSS and Atom feeds**: a reader that polls a list of feed URLs and
  yields each entry as a link, with the feed's name as a tag; the
  captures are what the extension would have made. The run becomes a
  scheduled task like the backup.
- **YouTube** (Watch later, playlists, a channel): the video page is thin;
  the transcript is the document, fetched per video.
- **Newsletters in a mailbox**: an mbox or a folder of `.eml` files, one
  document per issue, the links inside as captures.
- **Obsidian or any Markdown folder**: the drop folder does this already
  (a subfolder names the domain).
- **Discord**: no export of one's own; a bot that mirrors a channel to the
  drop folder is the shape.
