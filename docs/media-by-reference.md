# Audio and video by reference

2026-09-27. Written down, not built. niggles.txt asked: "later the question
is what happens with audio and video data (can we reference that
externally and in a stable way, even for networked connections?)".
`clients/send/prax_send.py` sends documents and leaves media alone until
this is decided.

## The problem

prax copies what it keeps. An original is archived by hash
(invariant 2), and the database holds the hash only. For papers and pages
that is cheap and it is what makes a document permanent: the NAS can go,
the link can rot, and the text and the PDF are still there.

A recording is different in size. An hour of 1080p video is 2–4 GB, and
a music library or a field-recording archive runs to terabytes. Copying
that onto the serving host (a Pi-class board or an N100 with one disk,
invariant 7) doubles storage that already has a home, often a NAS with
redundancy the prax host lacks. What the library needs of a recording is
its *text*: a transcript, chapters, the frames worth describing, the
metadata. That is small, and it is what search, the graph and `ask` read.

## What "by reference" would mean

A document whose original is not in the archive, and says where it is.

- **The identity stays the hash.** The sender hashes the file where it
  lives, as it does now. A document's `hash` is still the sha256 of the
  original bytes, so the same recording found on two machines, or later
  copied in, is still one document. What changes is that
  `data/archive/<hash>` may be absent. A new flag in `meta` says so.
- **Where it is: a list of locations, not one.** `meta.locations`, each
  with a host, a path, and when it was last seen there. A location is
  not stable by itself: a NAS renames a share, a disk is mounted
  elsewhere, a file moves. The hash is stable, so a location is a hint
  that can be checked (hash the file there again and compare) and
  refreshed by the next sender run, never an identity. `meta.origin`,
  which the sender writes today, is the first location.
- **Reachable how.** Three shapes, from least to most machinery:
  1. *Named only.* The library records where the file is and does not
     reach it. The player in the UI says "on nas:/volume1/…". Enough for
     search and the graph, and it needs nothing from the network.
  2. *Served by the owner.* The machine that has the file serves it
     itself: a NAS's own HTTP or SMB share, reached by a URL that
     includes the host. The UI's player points at that URL. This
     breaks when the reader is off the LAN, and it puts the NAS's
     credentials in a question prax does not answer today.
  3. *Proxied by the door.* The door streams the bytes from the location
     on request (HTTP range requests for seeking). The reader only ever
     talks to the door, which already has the token and the private
     network (Tailscale or the like). This is the stable answer for
     networked access, and the most machinery: the door needs a way to
     read the NAS (a mount, or a small agent beside the sender).
- **What is copied anyway.** The derived artifacts, never the original:
  the transcript text (`text_hash`, content-addressed as now), a few
  keyframes as figures (filed by hash like a scanned page's crops), a
  cover image, the tags. A video capture from the browser already works
  this way: `prax.parsers.video` keeps the transcript and frames and
  names the player in `meta.video`. A recording by reference is that
  shape with a file location instead of a provider's URL.

## Questions to settle first

1. **Where transcription runs.** A worker step (Whisper-class, on the
   machine with the card) needs the bytes, so it reads them from the
   location, which is shape 2 or 3 from the worker's side.
2. **Whether invariant 2 bends.** "Originals live at archive/<hash>"
   becomes "originals live at archive/<hash>, or are named by hash and
   location". That is a change to `CLAUDE.md`, to `prax heal` (a missing
   archive file is an ailment today), and to backup (which copies the
   archive and would now copy only what is there).
3. **What a stale location does.** The next sender run re-hashes and
   updates `last_seen`. A document none of whose locations answer is
   still searchable; the UI says the original is out of reach.
4. **Which size makes a file a reference.** A threshold (say 256 MB, the
   door's upload limit) or a kind (audio and video always). A kind is
   simpler to explain; a threshold treats a 20 MB voice memo as the small
   document it is.

## Recommendation

Build shape 1 first: `meta.locations`, the sender able to register a
file by hash and location without uploading it, and the transcript step
reading from a location the worker can reach. That makes a media archive
searchable without moving a byte of it. Shape 3 waits until a person
wants to play a recording from outside the LAN.
