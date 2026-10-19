# Originals by reference

Plan item AQ. Written 2026-09-27 for audio and video. Revised
2026-10-19 into a design for any original. The user asked on
2026-10-18: "on my nas LARGE amounts of video or audio data, i dont want this to be
duplicated … there may be duplicates all over the place, too". Not
built. The questions at the end need the user's answers before stage 1.

## The problem

prax copies what it keeps. An original is archived by hash at
`data/archive/<sha256[:2]>/<sha256>` (invariant 2), and the database
holds the hash only. For papers and pages that is cheap. It also makes a
document permanent: the NAS can go and the link can rot, and the PDF is
still there.

A recording is a different size. An hour of 1080p video is 2–4 GB, and a
music library or a field-recording archive runs to terabytes. Copying it
onto the serving host doubles storage that already has a home, often a
NAS with redundancy the board lacks (invariant 7). What the library needs
of a recording is small: a transcript, chapters, a few frames, the tags.

The same NAS holds copies of one file in several folders. prax already
keeps one archive file per hash, so a second copy sent in is the same
document again (`store.register` finds it by hash). What it does not keep is where each copy was found. That is
the list a person needs to clean the NAS up.

## The design

Two facts about an original are kept apart:

- **Whether prax holds the bytes.** A held original is in the archive, as
  now. A referenced one is not: the door reads it from a place when asked.
- **Where copies of it lie.** Every place a file with that hash was seen,
  for a held original and a referenced one alike.

The 2026-09-27 version of this note kept the places in `meta.locations`.
They move to a table of their own, for three reasons. A place is
provenance of the file, not a fact about the world, so it stays out of
the graph (invariant 8) and out of the document's meta, which describes
the document. One file can have dozens of places. And a place exists
before its file is a document: the crawl sees every file, and ingests few.

### The identity stays the hash

A document's `hash` is the sha256 of the original bytes, whether prax
holds them or not. The same recording found on two machines, or copied in
later, is one document. A referenced original that is later held changes
one column, and nothing else.

### Roots: a place that survives a moved mount

A path alone is not stable. The NAS share is `\\nas\media` on the
desktop and `/mnt/media` on the board, and a share is renamed or mounted
elsewhere. So a place is a **root** and a path inside it. A root is a name
in `prax.yaml`, mapped to a directory per host:

```yaml
places:
  roots:
    nas-media: //nas/media      # this host's mount of the share
    nas-papers: //nas/papers
```

The database stores `("nas-media", "Field recordings/2019/creek.wav")`.
A host that mounts the share elsewhere changes its own `prax.yaml` and no
row. A root this host does not map is named but unreachable here.

### The schema (one migration)

`places`, one row per file seen at a place:

| column | |
|---|---|
| `root`, `path` | the key; `path` is relative, with `/` separators |
| `size`, `mtime` | as the crawl read them; a file whose two are unchanged is not read again |
| `quick` | a cheap key: the size and three 64 KB blocks (start, middle, end), hashed |
| `sha256` | the full hash, NULL until the file was read whole |
| `first_seen`, `last_seen` | UTC moments (`store.now()`) |
| `gone` | when a crawl of its root no longer found it; the row stays |

Indexes on `sha256` and on `quick`. A document's places are `places
WHERE sha256 = documents.hash`. No foreign key, because a place may hold
a file that is not a document.

`documents` gains `held INTEGER NOT NULL DEFAULT 1`. Every document
today is held, so the default is the migration.

### The crawl

`prax import places <root>` is a client, like the other importers
(invariant 4). It runs on a machine that maps the root, and walks it the way
`prax_send.py` walks a folder. A file whose size and mtime match its row is
skipped. Any other file gets a `quick` key. Its full hash is read only
when the file is to become a document, or when its `quick` key is shared
by another file. That second case is where a duplicate may be. The client
posts what it saw in batches to `POST /places`. A crawl that ends marks
the rows of its root it did not see as `gone`.

Full hashing is the cost: gigabit Ethernet reads about 110 MB/s, so one
terabyte is some 2.5 hours. The `quick` key keeps that cost to the
files that need it.

With `--reference`, a new file of a referenced kind becomes a document
without its bytes: `POST /ingest/reference` with the hash, MIME type,
title, root and path. The other files are sent as now and held. Which
kinds are referenced is question 2 below.

### Reading a referenced original

One function says where an original's bytes are:
`store.original_source(hash) -> Path | None`. It returns the archive
file when held. Otherwise it returns the first reachable place, checked
by size and mtime against its row. The places the code reads an original
today all go through it:

- `store.original_info`, which serves `GET /doc/{id}/original`, a page
  image and a figure;
- `capture.pipeline.assess`;
- the `markup`, `published` and `quality` passes of `store.maintain`;
- `store.documents.readings.page_counts` and
  `store.repair.documents._not_documents`.

`archive_path` stays for text artifacts, which are always held.

A worker needs no change. It already fetches an original only from `GET
/doc/{id}/original` (`prax.work`, `steps.parse`, `steps.extract`). The
door streams a referenced file from its place with HTTP range requests.
A video can then be played in the UI and seeked. When no place is
reachable, the route answers 404 with `out of reach` and the roots it
tried. The step then fails that document as it would for a bad original.

The door must map the root to serve a file. A board without the NAS
mounted keeps the document searchable by what was already read from it.
The original and its figures are then out of reach.

### What is copied anyway

What was derived from a file: the text artifact, figures filed as
artifacts of their own, the tags. A video capture from the browser
already works this way (`prax.parsers.video`, `meta.video`). A
recording's keyframes are filed like a scanned page's crops
(`figures.FILED`), so they are not lost when the NAS is down. A
referenced PDF's figures are not filed: they are served out of the
original by hash (CLAUDE.md, "Conventions"). That is one reason to keep
PDFs held (question 2).

### What else changes

- **Invariant 2** becomes: "Originals live at
  `data/archive/<sha256[:2]>/<sha256>`, or are referenced: named by hash
  and found at a place (`places`)." The rest of it holds as written.
- **Backup** copies the archive, so it holds no referenced original. Its
  report adds `referenced`: how many originals and bytes are not in the
  copy, so a restore says what it cannot bring back.
- **Heal** gains `unreachable-originals`: referenced documents with no
  reachable place. The ailment names the roots the documents use.
- **The wall.** A path can be personal. The `private` rules read
  `original_path` and `meta.origin.path`, and they read a document's
  places too. A named token sees a document's places only through `get`,
  and only for a document it may see.
- **The UI.** A document page lists its places, with the gone ones
  struck through. The Admin page gains a duplicates list: groups of
  places sharing a full hash, largest first.
- **Size.** A row is about 150 bytes, so half a million files make some
  75 MB of table. That is within the board's means; the index on `quick`
  serves the duplicates query.

## Stages

1. **The table and the one reader.** The migration, `places.roots`, and
   `store.original_source`, with every site above going through it. The
   tests use a temporary root. Nothing is referenced yet, so it must give
   the same answers as now.
2. **The crawl.** `POST /places`, `prax import places`, and the `quick`
   key. Places recorded for held documents, and the duplicates list
   (`GET /places/duplicates`). This is useful before stage 3: it finds
   the NAS's duplicates without referencing anything.
3. **References.** `POST /ingest/reference`, `--reference`, and range
   serving from a place. Then the backup's count, heal's ailment and the
   UI's places.
4. **A recording's text.** A transcription step (Whisper-class) reading
   the original through the door. It needs the card, so it waits for the
   new PSU. Until then a referenced recording is found by its title, its
   path's words and its tags.

## Questions for the user

1. **Does the board mount the NAS?** If it does, the door serves a
   referenced file from anywhere on the private network. If only the
   desktop does, referenced originals are out of reach whenever the
   desktop door is not the one asked.
2. **What is referenced?** My recommendation: audio and video from a
   `--reference` crawl, and every other file held. PDFs are small next to
   recordings, their figures and pages are served out of the original,
   and holding them keeps them when the NAS is gone. The alternative is a
   size line (say `door.max_upload_mb`, 256 MB). It also catches a large
   scanned book, but a 20 MB voice memo stays held.
3. **Does the crawl list every file, or only the kinds prax reads?**
   Every file finds every duplicate on the NAS (a copied photo folder
   too), at the cost of more rows and `quick` reads. Only prax's kinds
   keeps the table to what the library can use.
