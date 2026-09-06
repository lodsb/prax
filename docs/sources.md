# Data sources

What feeds prax, how each source maps onto the store, and what each
importer must and must not do. All of these are Stage 1 work except where
noted; Stage 0 only provides the `register` / `index_text` / `link` calls
they use. Every source is a client of `prax.store` (CLAUDE.md invariant 3)
and never writes back to where it read from (invariant 10).

## 1. Zotero library

The primary corpus: years of curated PDFs with author, date, tag and
collection metadata. The full library lives on an external disk and is
large; the local Zotero directory on the dev machine is a stale, empty
2022 install that is only useful for schema checks.

### Where Zotero keeps things

    <zotero data dir>/
      zotero.sqlite          all metadata
      storage/<ITEMKEY>/     imported attachments, one folder per attachment item
      <linked base dir>/     linked files, outside the data dir

Zotero holds an exclusive lock on `zotero.sqlite` while running. The
importer therefore always copies the file first and opens the copy with
`?mode=ro`. It never opens the live database.

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

A handful of real items (a few small PDFs, one note, one linked URL, one
item with two attachments) are exported from the library into
`tests/fixtures/zotero/` with their `zotero.sqlite` subset. This is the
corpus for importer tests and for the Stage 2 eval queries. Pick items that
are fine to commit publicly, or keep the fixture out of git and document
how to regenerate it.

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
