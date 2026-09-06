# Data sources

What feeds prax, how each source maps onto the store, and what each
importer must and must not do. All of these are Stage 1 work except where
noted; Stage 0 only provides the `register` / `index_text` / `link` calls
they use. Every source is a client of `prax.store` (CLAUDE.md invariant 3)
and never writes back to where it read from (invariant 10).

## 1. Zotero library

The primary corpus: years of curated PDFs with author, date, tag and
collection metadata. The library lives on the external drive at
`R:\Zotero` (not always mounted). The local Zotero directory on the dev
machine is a stale, empty 2022 install that is only useful for schema
checks.

### Where Zotero keeps things

    R:\Zotero\
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
  roughly 1.5 GB for the database and text artifacts. Free space at the
  time of the census: C: 140 GB, I: 36 GB, R: 16 GB. The plan is a
  scratch run on C: with `PRAX_DATA_DIR=C:\prax-data`, in three steps:
  the fixture, then `--limit 500`, then the whole library. A zero-copy
  variant (hard-linking into an archive on the same NTFS volume as
  `storage/`) is an option if the store ever has to live on R: itself.

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
- Idempotent: a re-run skips documents whose hash exists and updates
  `meta.zotero` if the Zotero record changed (`dateModified` is stored).

### Workflow

1. Copy `zotero.sqlite` and the `storage/` tree (or mount the disk
   read-only).
2. `python scripts/import_zotero.py <copy-dir> --dry-run` prints the
   inventory: items by type, attachments by link mode, files referenced but
   missing, and duplicate hashes across items. Nothing is written.
3. `python scripts/import_zotero.py <copy-dir> --commit` registers
   documents through the store. Safe to interrupt and re-run.
4. Run the parse queue on the batch host.

### Test fixture

A handful of real items are copied from the library into
`tests/fixtures/zotero/` as a `storage/` subtree plus a `zotero.sqlite`
reduced to those items. This is the corpus for importer tests and for the
Stage 2 eval queries. Candidates from the census, chosen small and varied:

| Shape | Zotero key | Notes |
|---|---|---|
| Conference paper, one 8 KB PDF, DOI, abstract, one creator | `9QRPZL68` | Correlated tensor factorization for source separation |
| Journal article, 44 KB PDF, five creators, saved four times | `4L6ILMZN` and its three twins | exercises hash dedupe and `meta.zotero.keys` |
| Conference paper, 50 KB PDF, two creators | `6FRF9XDC` | HRTF model |
| Item with a child note | `EZLSQSMG` | arXiv comment note |
| Item with an HTML snapshot, ~100 KB | `97KAI26I` (attachment) | trafilatura path |
| Item with a linked URL, no file | `ZRWHFMBJ` (attachment) | URL-only document |
| Item with two PDFs, 3.2 MB total | `35UKKWHM` | multi-attachment parent |
| One standalone PDF, no parent | pick a small one | title-from-filename path |

The fixture must stay a few megabytes. Decide per item whether it is fine
to commit; otherwise keep `tests/fixtures/zotero/` out of git and document
the regeneration command in the importer.

## 2. Browser capture

Goal: move open tabs into prax with one click, from the machine where they
are open, without any intermediate service.

### Extension

- Manifest V3, one codebase for Chrome and Firefox.
- Popup with two actions: *send this tab* and *send all tabs in this
  window*. Optional: close tabs after a successful send.
- Options page: server URL (the Tailscale name of the Pi) and a bearer
  token.
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
  time; the API is bound to the Tailscale address only. CORS allows the
  extension origin.

## 3. Inbox folder

`data/inbox/`. A watcher registers any file dropped there, moves it to the
archive, and leaves `parsed_at NULL`. Useful for PDFs that arrive by mail
or download and for any future front-end (Karakeep, Linkwarden) that can
write files to a folder.

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
