# Originals by reference

Plan item AQ. Written 2026-09-27 for audio and video. Revised
2026-10-19 into a design for any original. Stages 1 to 3 built
(2026-10-19, -22 and -23). The user asked on
2026-10-18: "on my nas LARGE amounts of video or audio data, i dont want this to be
duplicated … there may be duplicates all over the place, too".
The question at the end was answered on 2026-10-22 (photos) and
2026-10-23 (audio and video), so stage 3 can start.

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

Listing a root and serving from it are two different reaches. The NAS
is reached over ssh today, and that is enough to list it: the crawler
runs on the NAS (below). Serving a file needs the door to open it, so
the door's host mounts the share (Samba). A root with no mount on the
door's host is listed and searchable, and its files are out of reach.

### The schema (one migration)

`places`, one row per file seen at a place:

| column | |
|---|---|
| `root`, `path` | the key; `path` is relative, with `/` separators |
| `size`, `mtime` | as the crawl read them; a file whose two are unchanged is not read again |
| `sha256` | the full hash of the bytes |
| `first_seen`, `last_seen` | UTC moments (`store.now()`) |
| `gone` | when a crawl of its root no longer found it; the row stays |

An index on `sha256`. A document's places are `places
WHERE sha256 = documents.hash`. No foreign key, because a place may hold
a file that is not a document.

`documents` gains `held INTEGER NOT NULL DEFAULT 1`. Every document
today is held, so the default is the migration.

### The crawl

The crawler is `clients/send/prax_send.py`, which already does most of
it. On 2026-09-28 it ran on the NAS itself over ssh and walked 5 TB
(`docs/log.md`). It leaves out junctions, loops and a system drive's
folders, hashes each file where it lies, and keeps a journal of what it
hashed, so a stopped run resumes. A new `--places <root>` mode lists
every file under the root, of any kind, and posts each one's path, size,
mtime and sha256 to `POST /places` in batches. It sends no bytes. It is a
client of the door like the other importers (invariant 4).

Every file is hashed in full, once. Running on the NAS, that reads at
the disks' speed, not the network's. A small NAS's CPU may hold sha256
to about 100 MB/s, so the first run over 5 TB takes some 14 hours, one
night and a morning. After that a file whose size and mtime match its row is
not read again. A run that ends marks the rows of its root it did not
see as `gone`.

A full hash for every file is what makes the questions below
answerable. The 2026-10-19 draft of this note used a cheap key (size and
sampled blocks) instead. That key can say that two files may be one, but
not that a file moved, so it was dropped.

With `--reference`, a new file of a referenced kind becomes a document
without its bytes: `POST /ingest/reference` with the hash, MIME type,
title, root and path. The other files are sent as now and held. Which
kinds are referenced is the question at the end.

### Asking where data is

Because every place carries a full hash, three questions become reads
of the table:

- **Where is this?** The places of a document, a hash or a path: the
  ones last seen, and the gone ones with when they went.
- **It moved or was deleted: is there another copy?** The other places
  with the same hash that are not gone. A file gone at one path and seen
  at another in the same run was moved, and the answer says so.
- **What exists only once?** The hashes with one live place, largest
  first. That is the list of what a disk failure would lose.

The door answers them at `GET /places` (by `doc`, `hash` or `root` and
`path`) and `GET /places/single`. The MCP server gets a `where` tool, so
an agent can ask "where is the creek recording" and get the places in a
few hundred bytes (invariant 6).

The places are not nodes or edges of the graph. An edge is evidence: a
document said it, with a quote, a confidence and a run (invariant 8). A
place is an observation of a crawl that changes every night. Half a
million `stored_at` edges would also swamp a walk: the NAS would be the
largest hub in the library. What can be an entity is the root itself,
the NAS as a `device` that documents mention ("the NAS's RAID"). A walk
from it then reaches what the library says about it, and `where` reaches
what lies on it.

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
PDFs held (the question at the end).

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
  75 MB of table. That is within the board's means; the index on `sha256`
  serves the duplicates query.

## Stages

1. **The table and the one reader.** The migration, `places.roots`, and
   `store.original_source`, with every site above going through it. The
   tests use a temporary root. Nothing is referenced yet, so it must give
   the same answers as now.
2. (done 2026-10-22) **The crawl and `where`.** `POST /places`, `prax_send.py --places`,
   the reads of "Asking where data is", the `where` tool and the
   duplicates list (`GET /places/duplicates`). This is useful before
   stage 3: it finds the NAS's duplicates and answers where a file is
   without referencing anything, and it needs no mount.
3. (done 2026-10-23) **References.** `POST /ingest/reference` and
   `prax_send.py --places NAME --reference`; range serving from a place;
   the backup's `referenced` count; heal's `unreachable-originals`; the
   places on a document's page and in `get`. What was built differs from
   the sections above in four places, below.
4. **A recording's text.** A transcription step (Whisper-class) reading
   the original through the door. It needs the card, so it waits for the
   new PSU. Until then a referenced recording is found by its title, its
   path's words and its tags.

## What stage 3 built (2026-10-23)

- **One request per batch, not per file.** `POST /ingest/reference`
  takes a crawl's batch (at most `store.KNOWN_BATCH` entries, the shape
  `POST /places` takes, each with an optional `mime`). It records every
  entry as a place and makes each new file of a referenced kind a
  document: `held` 0, the file name as its title, `meta.source`
  `reference` and the capture's who and when (`inbox.ingest_references`,
  `store.register_references`). A known hash is counted and left alone.
  With `--reference` the sender posts its batches there instead of to
  `/places`. The door decides the kind by the file name
  (`mimes.of_name`), so the sender needs no table of its own.
- **Which kinds** is `places.reference` in prax.yaml, by default
  `audio/`, `video/` and `image/` (`store.REFERENCED`). DjVu is an image
  type and a document, so it stays held.
- **A Live Photo's clip** is a `.mov` with a picture of the same name in
  the same folder (`.heic`, `.heif`, `.jpg`, `.jpeg`). It is recorded as
  a place and made no document (`store.live_photo_clip`). The sender
  puts a folder's pictures before its clips, so the picture's place is
  there when the clip is looked at. A clip in the next batch from its
  picture is still found, as that place was posted first.
- **Not read yet**: a recording's tags and a photo's EXIF, preview and
  thumbnail. A referenced file is found by its file name and the
  folders it lies in, which its search field holds (`document_field`:
  "audio recording" or "video", and the folder path), until a reading
  adds more. The tags need a reader that opens the file through the
  door; it belongs with stage 4 for recordings and with AR's photo
  stage for pictures.

Three things a referenced document is kept out of. It is not a waiting
capture (`CAPTURE_SOURCES`), so the parse step never hands it to a
worker and never asks the vision model about a referenced picture by
itself: 20,000 personal photos are not sent to a model unasked. Heal's
`not-documents` does not open it for a header, and
`documents-without-an-extractor` does not count it.

A referenced document that is later sent with its bytes is held from
then on (`store.register` sets `held` 1). The private rules read its
places' paths as they read `original_path`, so a photo under a path in
`private.paths` is suspected when it is registered.

Every referenced original is suspected from its registration, whatever
its path (`private.referenced`, on by default; the user, 2026-10-23). A
phone's photos, videos and voice memos are personal more often than
not, and a named token without `--personal` sees one only after a
person releases it. The cue is `referenced: image` (or `audio`,
`video`) in `meta.private.cues`, and `meta.sensitivity.by` is `rules`,
so the nightly `private` pass leaves it as it is and never marks one a
person released. The Admin page's "personal?" list leaves the
referenced ones out and counts them (`GET /documents/suspected`,
`referenced` in the answer; `?referenced=true` lists them). Releasing
a group at once, an album or a folder or a trip, belongs to a media
view of its own, which is not built yet. A host whose share holds no
one's private life sets `private: {referenced: false}`.

`unreachable-originals` answers for a root this door does not map, or
whose directory is missing, without opening a file. A mounted root is
looked at file by file, at most `store.REACH_CHECKS` (2,000) files a
call, so heal does not walk a share of 20,000 photos over the network.

The Admin page's duplicates list is not built yet; `GET
/places/duplicates` answers it.

The document page of a referenced original whose share is not mounted
says "offline for now" with its places and a "try again" link, which
asks the door again and draws the page afresh with its player or its
picture once the share answers. Neither is drawn while it is offline,
and a referenced picture's page does not suggest the vision model.

## Questions for the user

Two were answered on 2026-10-19. The NAS is reached over ssh and
could be mounted with Samba, so the crawler runs on the NAS and the
door mounts the share to serve from it. The crawl lists every file, so
prax can name the other copies when one moves or is deleted. The
third was answered in two parts:

1. **What is referenced?** Photos, audio and video; every other file is
   held. Photos were answered on 2026-10-22, on the user's plan to copy
   the phone's library onto the NAS. prax keeps their metadata, a
   preview and a thumbnail as derived artifacts (`docs/biography.md`,
   §7). Audio and video were answered on 2026-10-23: "audio and video
   are referenced in the same way". PDFs stay held: they are small next
   to recordings, their figures and pages are served out of the
   original, and holding them keeps them when the NAS is gone. The size
   line (`door.max_upload_mb`) was the alternative and is not built.

   A referenced recording is read like a photo, before any model: its
   container's tags (ID3, Vorbis comments, MP4 atoms: title, artist,
   album, date, duration) go into `meta`. Its derived artifacts are a
   poster frame for a video, and later the transcript (stage 4).
   Whether it is referenced decides nothing about what it is about. A
   home video and a master of a release are both referenced, and
   `meta.domains` puts one in biography and the other in biography and
   studio (`docs/biography.md`, §8).
