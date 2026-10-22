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

## A large tree

On a NAS of several terabytes the sender works a batch of folders at a
time as it walks, so it starts sending at once. PDFs and text go first
within each batch. A real run records each folder it finished in the
state file, with the folder's modification time. The next run passes
over those folders, unless a file was added or removed there since.
Stop it with Ctrl-C whenever you like, and start it again later: it goes
on where it was. `--again` looks at the finished folders too. A dry run
records nothing.

The state file is a journal: one short line per file hashed and per
folder finished, written as it happens. A stop loses at most the line
being written.

Some folders are left out on purpose:

- a copied Windows profile's junk (`AppData`, `Application Data`,
  `Local Settings` and the like). A backup that followed those links
  copied them into themselves, thousands of levels deep;
- a backed-up system drive's own folders (`Windows`, `Program Files`,
  `ProgramData`, the recycle bin);
- a program on a Mac, which is a folder (`Name.app`, and plug-in
  bundles such as `.component` and `.vst`): its credits and help files;
- a folder whose name appears three times in a row in its path, which
  is such a loop;
- anything below `--max-depth` levels (40);
- any folder named with `--skip NAME`, as often as you like.

The last line of a run says how many folders were left out.

## Where every file lies (`--places`)

With `--places NAME` the sender sends no file. It records where every
file under the tree lies, of any kind: its path, size, modification time
and full sha256, posted in batches to the door's `POST /places`. `NAME`
is the share's name in the door's prax.yaml (`places.roots`), so the
same rows hold however a host mounts the share.

    python prax_send.py /volume1/media --places nas-media --door http://prax:8000
    python prax_send.py /volume1/media/photos/icloud --places nas-media         --under photos/icloud --door http://prax:8000

Run it on the NAS: it reads every file once, at the disks' speed (about
14 hours for 5 TB at 100 MB/s), and the journal keeps each hash while a
file's size and modification time stay, so a later run reads only what
is new or changed. A run that read every file ends the crawl, and the
door marks each file of the share (or of the `--under` folder) that it
did not see again as gone; the row stays, so prax can still say where
the file was and whether a copy is left. A run with a file it could not
read leaves the crawl open and marks nothing gone. `--under` crawls one
folder of the share alone, such as a folder a new dump went into.

The door then answers where a file is (`GET /places`, the MCP `where`
tool), what lies at several places (`GET /places/duplicates`) and what
lies at one place only and is not in prax's archive (`GET
/places/single`). All of it is for the administrator's token only.

Audio and video are not sent. Reading a file from its place instead of
copying it in is stage 3 of `docs/media-by-reference.md`, not built
yet.
