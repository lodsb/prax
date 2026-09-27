#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""prax_send: send a tree of files to a prax door, only what it lacks.

One file, the standard library only, for Python 2.7 and 3: copy it to a
NAS, an old laptop or a server and run it there. It walks the tree, hashes
each file (sha256 of the bytes, the store's own identity for a document),
asks the door in batches which hashes it already holds (POST /known), and
uploads the rest (POST /ingest/file), PDFs and text first. Nothing on the
sending machine is changed; the hashes are remembered in a state file, so
a second run over the same tree hashes only what changed.

    python prax_send.py /volume1/papers --door http://prax:8000
    python prax_send.py ~/Documents --door https://prax.local:8443 \\
        --domains research --tags from:nas --dry-run

The token is PRAX_TOKEN in the environment, or --token-file (a file
holding it); --token on the command line works too but shows in the
process list. Each document keeps where it came from as meta.origin
(this host's name and the file's path), which is how a file stays
findable on the machine that has it.
"""

from __future__ import print_function

import hashlib
import json
import mimetypes
import optparse
import os
import socket
import sys
import time
import uuid

try:  # Python 3
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # Python 2
    from urllib2 import HTTPError, Request, URLError, urlopen

PY2 = sys.version_info[0] == 2
VERSION = "1"

# what the door can read, in the order they are sent: PDFs and text first
DOCUMENTS = [
    ".pdf",
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".tex",
    ".html",
    ".htm",
    ".xhtml",
    ".docx",
    ".doc",
    ".odt",
    ".rtf",
    ".epub",
    ".djvu",
    ".djv",
    ".csv",
]
IMAGES = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff"]
# a NAS's own clutter, and the usual hidden places
SKIP_DIRS = {
    "@eaDir",
    "#recycle",
    "#snapshot",
    ".snapshot",
    ".snapshots",
    "$RECYCLE.BIN",
    "System Volume Information",
    "lost+found",
    "node_modules",
    "__pycache__",
}
# a Windows profile copied by a backup: these are junctions on Windows, and
# a tool that followed them wrote "Application Data" inside itself over and
# over (798,470 folders of it on a NAS, 2026-09-28). Nothing to send there
WINDOWS_PROFILE = {
    "AppData",
    "Application Data",
    "Local Settings",
    "Temporary Internet Files",
    "INetCache",
    "Cookies",
    "NetHood",
    "PrintHood",
    "Recent",
    "SendTo",
    "Templates",
    "Start Menu",
}
REPEATS = 3  # a folder name this many times in a row in a path is a loop
MAX_DEPTH = 40
MIME = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rst": "text/plain",
    ".tex": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".rtf": "application/rtf",
    ".epub": "application/epub+zip",
    ".djvu": "image/vnd.djvu",
    ".djv": "image/vnd.djvu",
    ".webp": "image/webp",
}
BATCH = 500  # hashes per question; the door takes up to 1000
READ = 1 << 20


def say(opts, text):
    if opts.quiet:
        return
    if PROGRESS is not None:
        PROGRESS.clear()  # a line of its own, not over the status
    try:
        print(text)
    except UnicodeEncodeError:  # a terminal that cannot show a character
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, "replace").decode(enc, "replace"))
    sys.stdout.flush()


# ---------------------------------------------------------------- progress

PROGRESS = None  # the run's Progress, which say() steps around


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0
    return "%.1f TB" % n


def human_time(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return "%d s" % seconds
    if seconds < 5400:
        return "%d min" % round(seconds / 60.0)
    return "%.1f h" % (seconds / 3600.0)


class Progress(object):
    """What the run is doing, on stderr so the lines on stdout stay clean.

    In a terminal it is one status line, rewritten in place a few times a
    second. Piped or logged (a NAS job under nohup), it is a line every
    ``every`` seconds instead, so a log shows the run moving.
    """

    def __init__(self, quiet=False, every=10.0, stream=None):
        self.stream = stream or sys.stderr
        self.quiet = quiet
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.every = 0.25 if self.tty else max(1.0, every)
        self.last = 0.0
        self.shown = 0  # characters of the status line on screen

    def width(self):
        try:
            import shutil

            return max(40, shutil.get_terminal_size((100, 20)).columns - 1)
        except (ImportError, AttributeError, ValueError, OSError):
            return 99

    def show(self, text, force=False):
        if self.quiet:
            return
        now = time.time()
        if not force and now - self.last < self.every:
            return
        self.last = now
        if self.tty:
            text = text[: self.width()]
            pad = max(0, self.shown - len(text))
            self._write("\r" + text + " " * pad)
            self.shown = len(text)
        else:
            self._write(time.strftime("%H:%M:%S ") + text + "\n")

    def clear(self):
        if self.tty and self.shown:
            self._write("\r" + " " * self.shown + "\r")
            self.shown = 0

    def done(self):
        self.clear()

    def _write(self, text):
        try:
            self.stream.write(text)
        except UnicodeEncodeError:
            enc = getattr(self.stream, "encoding", None) or "ascii"
            self.stream.write(text.encode(enc, "replace").decode(enc, "replace"))
        self.stream.flush()


# the encodings a file name that is not UTF-8 is tried in: the Windows and
# Latin-1 code pages old NAS shares and Samba mounts are full of. Latin-1
# decodes any byte, so it is last and always answers.
LEGACY = ("cp1252", "latin-1")


def _decode_name(raw):
    """File name bytes as text: UTF-8 when they are, else a legacy code page."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in LEGACY:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def fs_text(path):
    """A path as text that can be printed, sent and stored.

    Python 3 hands a file name that is not UTF-8 over with each bad byte as
    a lone surrogate (an "ä" written as the Latin-1 byte 0xE4 arrives as
    "\\udce4"). The file opens fine under that name, but the text cannot be
    printed or encoded, so the original bytes are taken back and decoded:
    UTF-8 when they are, else the Windows or Latin-1 code page.
    """
    if PY2:
        if isinstance(path, str):
            return _decode_name(path)
        return path
    try:
        path.encode("utf-8")
        return path
    except UnicodeEncodeError:
        raw = path.encode("utf-8", "surrogateescape")
        return _decode_name(raw)


# ---------------------------------------------------------------- the tree


def _looping(parts):
    """Whether the end of a path repeats one folder name ``REPEATS`` times:
    a junction a copy followed into itself."""
    tail = parts[-REPEATS:]
    return len(tail) == REPEATS and len(set(tail)) == 1


def walk(roots, exts, skip=(), max_depth=MAX_DEPTH, progress=None, stats=None):
    """The folders under the roots, each as ``(folder, [(path, size), ...])``
    with the files whose suffix is wanted, links not followed. Left out:
    hidden files and folders, a NAS's clutter, a copied Windows profile's
    junk (``WINDOWS_PROFILE``), the names in ``skip``, a folder whose name
    repeats ``REPEATS`` times at the end of its path, one reached twice (by
    its device and inode), and anything deeper than ``max_depth``.
    ``stats`` counts what was walked and what was left out."""
    stats = stats if stats is not None else {}
    for key in ("folders", "files", "bytes", "left_out"):
        stats.setdefault(key, 0)
    leave = SKIP_DIRS | WINDOWS_PROFILE | set(skip)
    for root in roots:
        root = os.path.abspath(root)
        if os.path.isfile(root):
            if os.path.splitext(root)[1].lower() in exts:
                yield os.path.dirname(root), [(root, os.path.getsize(root))]
            continue
        base = len(root.rstrip(os.sep).split(os.sep))
        visited = set()
        for here, dirs, files in os.walk(root):
            stats["folders"] += 1
            if progress is not None:
                progress.show(
                    "walking: %d folders, %d files (%s) so far, now %s"
                    % (
                        stats["folders"],
                        stats["files"],
                        human_bytes(stats["bytes"]),
                        fs_text(here),
                    )
                )
            try:
                st = os.stat(here)
                visited.add((st.st_dev, st.st_ino))
            except OSError:
                pass
            parts = here.rstrip(os.sep).split(os.sep)
            kept = []
            for d in sorted(dirs):
                full = os.path.join(here, d)
                if d.startswith(".") or d in leave or os.path.islink(full):
                    stats["left_out"] += 1
                    continue
                if _looping(parts + [d]) or len(parts) + 1 - base > max_depth:
                    stats["left_out"] += 1
                    continue
                try:
                    st = os.stat(full)
                    if (st.st_dev, st.st_ino) in visited:
                        stats["left_out"] += 1
                        continue  # reached before by another way
                except OSError:
                    pass
                kept.append(d)
            dirs[:] = kept
            wanted = []
            for name in sorted(files):
                if name.startswith((".", "~$")):
                    continue
                path = os.path.join(here, name)
                if os.path.islink(path):
                    continue
                if os.path.splitext(name)[1].lower() in exts:
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        size = 0  # gone, or unreadable: the hashing says which
                    stats["files"] += 1
                    stats["bytes"] += size
                    wanted.append((path, size))
            if wanted:
                yield here, wanted


def rank(path, exts):
    """PDFs first, then text, then the rest, in the order of ``exts``."""
    return (exts.index(os.path.splitext(path)[1].lower()), path)


# ---------------------------------------------------------------- hashes


def _mtime(folder):
    try:
        return int(os.stat(folder).st_mtime)
    except OSError:
        return None


class State(object):
    """What earlier runs learned, kept as a journal of JSON lines that only
    grows: a hashed file (``{"f": path, "s": size, "m": mtime, "h": sha256}``)
    and a folder a real run finished (``{"d": folder, "m": mtime}``), each a
    line written as it happens, the latest line for a path winning when the
    journal is read. A tree of a million files would otherwise rewrite a
    file of a hundred megabytes every batch; a run stopped hard loses at
    most the line it was writing. A state file of the older single-document
    shape is read and turned into a journal.
    """

    def __init__(self, path):
        self.path = path
        self.seen = {}  # path -> [size, mtime, sha256]
        # folders a real run sent in full, with their mtime then: passed over
        # while the mtime stays (a file added or removed changes it)
        self.done = {}
        self.out = None
        if path and os.path.exists(path):
            self._read(path)

    def _read(self, path):
        try:
            with open(path) as f:
                head = f.read(1)
                f.seek(0)
                if head == "{" and not f.readline().rstrip().endswith("}"):
                    f.seek(0)  # the older shape: one document, not lines
                    kept = json.load(f)
                    self.seen = kept.get("files", {})
                    self.done = dict(kept.get("done") or {})
                    self._rewrite()
                    return
                f.seek(0)
                for line in f:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue  # a line cut short by a stop: the rest holds
                    if "f" in item:
                        self.seen[item["f"]] = [item["s"], item["m"], item["h"]]
                    elif "d" in item:
                        self.done[item["d"]] = item["m"]
        except (IOError, OSError, ValueError):
            self.seen, self.done = {}, {}

    def _rewrite(self):
        """The journal written anew from what is held (once, on conversion)."""
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            f.writelines(
                json.dumps({"f": key, "s": size, "m": mtime, "h": digest}) + "\n"
                for key, (size, mtime, digest) in self.seen.items()
            )
            f.writelines(
                json.dumps({"d": key, "m": mtime}) + "\n"
                for key, mtime in self.done.items()
            )
        if os.name == "nt" and os.path.exists(self.path):
            os.remove(self.path)  # Python 2 on Windows cannot rename over
        os.rename(tmp, self.path)

    def _note(self, item):
        if not self.path:
            return
        try:
            if self.out is None:
                self.out = open(self.path, "a")  # noqa: SIM115 - kept open for the run, closed by close()
            self.out.write(json.dumps(item) + "\n")
        except (IOError, OSError):
            self.path = None  # nowhere to keep it: the run goes on without

    def hash_of(self, path, read=None):
        """The sha256 of the file, from the journal when its size and mtime
        are what they were. ``read(n)`` is told of every block read, so a
        large file shows progress while it is hashed."""
        st = os.stat(path)
        key = fs_text(path)
        known = self.seen.get(key)
        if known and known[0] == st.st_size and known[1] == int(st.st_mtime):
            return known[2]
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                block = f.read(READ)
                if not block:
                    break
                h.update(block)
                if read is not None:
                    read(len(block))
        digest = h.hexdigest()
        self.seen[key] = [st.st_size, int(st.st_mtime), digest]
        self._note({"f": key, "s": st.st_size, "m": int(st.st_mtime), "h": digest})
        return digest

    def finished(self, folder):
        """Whether a real run sent this folder in full, as it is now."""
        was = self.done.get(fs_text(folder))
        return was is not None and was == _mtime(folder)

    def finish(self, folder):
        key = fs_text(folder)
        self.done[key] = _mtime(folder)
        self._note({"d": key, "m": self.done[key]})

    def save(self):
        """The lines written so far on disk (a batch done, or the run)."""
        if self.out is not None:
            try:
                self.out.flush()
            except (IOError, OSError):
                pass

    def close(self):
        self.save()
        if self.out is not None:
            self.out.close()
            self.out = None


# ---------------------------------------------------------------- the door


class Door(object):
    def __init__(self, url, token, insecure=False, timeout=600):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.context = None
        if insecure:
            import ssl

            if hasattr(ssl, "_create_unverified_context"):
                self.context = ssl._create_unverified_context()

    def _open(self, path, body, content_type):
        req = Request(self.url + path, data=body)
        req.add_header("Content-Type", content_type)
        req.add_header("User-Agent", "prax_send/" + VERSION)
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        kw = {"timeout": self.timeout}
        if self.context is not None:
            kw["context"] = self.context
        resp = urlopen(req, **kw)
        try:
            return json.loads(resp.read().decode("utf-8"))
        finally:
            resp.close()

    def known(self, hashes):
        body = json.dumps({"hashes": hashes}).encode("utf-8")
        return set(self._open("/known", body, "application/json")["known"])

    def send(self, path, fields):
        boundary = "prax" + uuid.uuid4().hex
        name = os.path.basename(fs_text(path))
        ext = os.path.splitext(name)[1].lower()
        mime = MIME.get(ext) or mimetypes.guess_type(name)[0]
        mime = mime or "application/octet-stream"
        parts = []
        for key in sorted(fields):
            value = fields[key]
            if value is None or value == "":
                continue
            parts.append(
                (
                    '--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n'
                    % (boundary, key)
                ).encode("utf-8")
                + value.encode("utf-8")
                + b"\r\n"
            )
        quoted = name.replace("\\", "_").replace('"', "'")
        with open(path, "rb") as f:
            data = f.read()
        parts.append(
            (
                '--%s\r\nContent-Disposition: form-data; name="file";'
                ' filename="%s"\r\nContent-Type: %s\r\n\r\n' % (boundary, quoted, mime)
            ).encode("utf-8")
            + data
            + b"\r\n"
        )
        body = b"".join(parts) + ("--%s--\r\n" % boundary).encode("utf-8")
        return self._open(
            "/ingest/file", body, "multipart/form-data; boundary=" + boundary
        )


# ---------------------------------------------------------------- the run


def parse(argv):
    p = optparse.OptionParser(
        usage="%prog ROOT [ROOT ...] --door URL [options]",
        description="Send the files under ROOT that the prax door does not hold yet.",
        version=VERSION,
    )
    p.add_option("--door", default=os.environ.get("PRAX_DOOR"), help="the door's URL")
    p.add_option("--token", default=None, help="the door's token (else PRAX_TOKEN)")
    p.add_option("--token-file", default=None, help="a file holding the token")
    p.add_option(
        "--ext",
        default=None,
        help="the suffixes to send, comma-separated (default: %s)"
        % ",".join(e[1:] for e in DOCUMENTS),
    )
    p.add_option("--images", action="store_true", help="send pictures as well")
    p.add_option("--domains", default=None, help="ontology modules, comma-separated")
    p.add_option("--tags", default=None, help="tags for every file, comma-separated")
    p.add_option(
        "--host",
        default=None,
        help="this machine's name in meta.origin (default: its hostname)",
    )
    p.add_option("--max-mb", type="float", default=256.0, help="skip larger files")
    p.add_option(
        "--state",
        default=os.path.join(os.path.expanduser("~"), ".prax_send.json"),
        help="where the hashes are remembered (--state= for nowhere)",
    )
    p.add_option("--dry-run", action="store_true", help="say what would be sent")
    p.add_option(
        "--skip",
        action="append",
        default=[],
        metavar="NAME",
        help="a folder name to leave out, wherever it is (repeatable)",
    )
    p.add_option(
        "--max-depth",
        type="int",
        default=MAX_DEPTH,
        help="folders deeper than this under a root are left out",
    )
    p.add_option(
        "--again",
        action="store_true",
        help="look at the folders an earlier run finished, too",
    )
    p.add_option("--insecure", action="store_true", help="accept any TLS certificate")
    p.add_option("--quiet", action="store_true", help="only the summary")
    p.add_option(
        "--progress-every",
        type="float",
        default=10.0,
        metavar="SECONDS",
        help="how often a progress line is written when not in a terminal",
    )
    opts, roots = p.parse_args(argv)
    if not roots:
        p.error("which tree? (ROOT)")
    if not opts.door:
        p.error("which door? (--door or PRAX_DOOR)")
    return opts, roots


def token_of(opts):
    if opts.token:
        return opts.token
    if opts.token_file:
        with open(opts.token_file) as f:
            return f.read().strip()
    return os.environ.get("PRAX_TOKEN", "")


def exts_of(opts):
    if opts.ext:
        wanted = ["." + e.strip().lower().lstrip(".") for e in opts.ext.split(",")]
    else:
        wanted = list(DOCUMENTS)
    if opts.images:
        wanted += [e for e in IMAGES if e not in wanted]
    return wanted


def run(argv=None):
    """Walk, hash, ask and send, a batch of folders at a time as the walk
    finds them, so a tree of millions of files starts sending at once and
    an interrupted run does not walk it all again: a real run records each
    folder it finished (every file sent or already there) in the state
    file, and the next run passes over those (``--again`` looks at them
    too). A dry run records none."""
    global PROGRESS
    opts, roots = parse(sys.argv[1:] if argv is None else argv)
    exts = exts_of(opts)
    door = Door(opts.door, token_of(opts), insecure=opts.insecure)
    state = State(opts.state or None)
    host = opts.host or socket.gethostname()
    limit = int(opts.max_mb * (1 << 20))
    counts = {
        "seen": 0,
        "known": 0,
        "sent": 0,
        "duplicate": 0,
        "failed": 0,
        "large": 0,
        "done_before": 0,
    }
    walked = {}
    started = time.time()
    progress = PROGRESS = Progress(quiet=opts.quiet, every=opts.progress_every)
    tally = {"bytes": 0, "read": 0, "cached": 0, "since": time.time()}

    def status(what, force=False):
        elapsed = max(0.001, time.time() - tally["since"])
        progress.show(
            "%s | %d folders walked, %d files looked at (%s), %d cached | %d %s"
            " | %s/s"
            % (
                what,
                walked.get("folders", 0),
                counts["seen"],
                human_bytes(tally["bytes"]),
                tally["cached"],
                counts["sent"],
                "to send" if opts.dry_run else "sent",
                human_bytes(tally["read"] / elapsed),
            ),
            force=force,
        )

    def read(n):  # every block hashed: a large file shows it is moving
        tally["read"] += n
        tally["bytes"] += n
        status("hashing")

    def flush(folders):
        """Hash, ask about and send the files of these folders; then mark
        the folders finished whose every file went through."""
        failed = set()
        hashed = []
        for folder, files in folders:
            for path, size in sorted(files, key=lambda f: rank(f[0], exts)):
                counts["seen"] += 1
                try:
                    if size > limit:
                        counts["large"] += 1
                        tally["bytes"] += size
                        say(opts, "too large, left: %s" % fs_text(path))
                        continue
                    before = tally["read"]
                    digest = state.hash_of(path, read)
                    if tally["read"] == before:  # from the cache: nothing read
                        tally["cached"] += 1
                        tally["bytes"] += size
                    hashed.append((folder, path, digest))
                    status("hashing")
                except (IOError, OSError) as exc:
                    counts["failed"] += 1
                    failed.add(folder)
                    say(opts, "cannot read %s: %s" % (fs_text(path), exc))
        held = set()
        for start in range(0, len(hashed), BATCH):
            status("asking the door", force=True)
            held |= door.known(sorted({h for _, _, h in hashed[start : start + BATCH]}))
        asked = set()
        for folder, path, digest in hashed:
            if digest in held or digest in asked:
                counts["known"] += 1
                continue
            asked.add(digest)  # the same bytes twice in one tree: once
            if opts.dry_run:
                counts["sent"] += 1
                say(opts, "would send %s" % fs_text(path))
                continue
            status("sending")
            fields = {
                "by": "send",
                "domains": opts.domains,
                "tags": opts.tags,
                "origin": json.dumps({"host": host, "path": fs_text(path)}),
            }
            try:
                out = door.send(path, fields)
            except HTTPError as exc:
                if exc.code in (401, 403):
                    raise
                counts["failed"] += 1
                failed.add(folder)
                say(opts, "refused %s: HTTP %s" % (fs_text(path), exc.code))
                continue
            except (URLError, IOError, OSError) as exc:
                counts["failed"] += 1
                failed.add(folder)
                say(opts, "could not send %s: %s" % (fs_text(path), exc))
                continue
            if out.get("created") is False:
                counts["duplicate"] += 1
            else:
                counts["sent"] += 1
            say(opts, "sent %s -> doc %s" % (fs_text(path), out.get("doc_id")))
        if not opts.dry_run:
            for folder, _ in folders:
                if folder not in failed:
                    state.finish(folder)
            state.save()

    say(opts, "looking under %s" % ", ".join(roots))
    try:
        pending, waiting = [], 0
        for folder, files in walk(
            roots, exts, opts.skip, opts.max_depth, progress, walked
        ):
            if not opts.again and state.finished(folder):
                counts["done_before"] += 1
                continue
            pending.append((folder, files))
            waiting += len(files)
            if waiting >= BATCH:
                flush(pending)
                pending, waiting = [], 0
        if pending:
            flush(pending)
    except HTTPError as exc:
        progress.done()
        print("the door refused: HTTP %s (the token?)" % exc.code, file=sys.stderr)
        return 2
    except URLError as exc:
        progress.done()
        print("the door did not answer: %s" % exc, file=sys.stderr)
        return 2
    finally:
        state.close()
        PROGRESS = None
    progress.done()
    print(
        "%(seen)d looked at, %(known)d already there, %(sent)d " % counts
        + ("to send" if opts.dry_run else "sent")
        + ", %(duplicate)d the same as one there, %(large)d too large,"
        " %(failed)d failed"
        % counts
        + ", %d folders finished before" % counts["done_before"]
        + ", %d folders left out" % walked.get("left_out", 0)
        + " (%s)" % human_time(time.time() - started)
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(run())
