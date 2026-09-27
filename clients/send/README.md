# prax_send

One Python file that sends a tree of files from another machine to a
prax door (the service that holds your library), only what the door
does not hold yet. The standard library
only, Python 2.7 or 3. Copy it to a NAS, an old laptop or a server and
run it there; nothing is installed and nothing on that machine changes.

    export PRAX_TOKEN=…            # or --token-file FILE
    python prax_send.py /volume1/papers --door http://prax:8000 --dry-run
    python prax_send.py /volume1/papers --door http://prax:8000 --tags from:nas

What it does, in order:

1. Walks the trees given, without following links. Hidden files, Office
   lock files (`~$…`) and a NAS's own folders (`@eaDir`, `#recycle`,
   snapshots, `$RECYCLE.BIN`) are left out.
2. Keeps the files the door can read: PDF, text, Markdown, HTML, Word,
   ODT, RTF, EPUB, DjVu, CSV (`--ext` for another list, `--images` to add
   pictures). PDFs go first, then text, then the rest.
3. Hashes each file (sha256 of its bytes, which is what the store calls a
   document) and remembers the hash by path, size and modification time
   in `~/.prax_send.json` (`--state`). A second run hashes only what
   changed.
4. Asks the door which hashes it already holds (`POST /known`, 500 a
   request), and uploads the rest (`POST /ingest/file`). The same bytes
   twice in one tree are sent once.
5. Each document it creates records where it came from, `meta.origin`:
   this machine's name (`--host`) and the file's path.

`--domains` and `--tags` apply to every file. `--max-mb` (256, the door's
default limit) skips larger files. `--state=` (empty, written as one
argument, which PowerShell also passes on) keeps no hashes at all.
`--insecure` accepts a self-signed certificate. A refused token stops the
run at once (exit 2); a file that fails is counted and the run goes on
(exit 1 at the end).

While it runs you see where it is. It shows, in turn:

- the files and folders found while it walks the tree
- the files and bytes hashed, how many came from the cache, the speed
  and the time left
- each batch it asks about, and each file it sends

In a terminal that is one line, updated in place. When the output goes
to a log (a job under `nohup`), it writes the line every ten seconds
instead (`--progress-every`). The progress goes to stderr, so the list of
files on stdout stays clean to pipe or grep. `--quiet` turns it off.

    nohup python prax_send.py /volume1/papers --door http://prax:8000 > send.log 2>&1 &
    tail -f send.log

Audio and video are not sent. How the library should hold a file too
large to copy, by reference to where it lives, is written down in
`docs/media-by-reference.md` and not built.
