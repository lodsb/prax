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
MIME = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rst": "text/plain",
    ".tex": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".rtf": "application/rtf",
    ".epub": "application/epub+zip",
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


def walk(roots, exts, progress=None):
    """Every file under the roots whose suffix is wanted, links not
    followed, hidden files and NAS clutter left out, as ``(path, size)``.
    With ``progress``, how far the walk has got."""
    found = folders = total = 0
    for root in roots:
        root = os.path.abspath(root)
        if os.path.isfile(root):
            if os.path.splitext(root)[1].lower() in exts:
                yield root, os.path.getsize(root)
            continue
        for here, dirs, files in os.walk(root):
            folders += 1
            if progress is not None:
                progress.show(
                    "walking: %d files (%s) in %d folders, now %s"
                    % (found, human_bytes(total), folders, fs_text(here))
                )
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".")
                and d not in SKIP_DIRS
                and not os.path.islink(os.path.join(here, d))
            )
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
                    found += 1
                    total += size
                    yield path, size


def rank(path, exts):
    """PDFs first, then text, then the rest, in the order of ``exts``."""
    return (exts.index(os.path.splitext(path)[1].lower()), path)


# ---------------------------------------------------------------- hashes


class State(object):
    """The hashes of files already hashed, by path, size and mtime."""

    def __init__(self, path):
        self.path = path
        self.seen = {}
        self.dirty = 0
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    self.seen = json.load(f).get("files", {})
            except (IOError, OSError, ValueError):
                self.seen = {}

    def hash_of(self, path, read=None):
        """The sha256 of the file, from the cache when its size and mtime
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
        self.dirty += 1
        if self.dirty >= 200:
            self.save()
        return digest

    def save(self):
        if not self.path or not self.dirty:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"version": VERSION, "files": self.seen}, f)
            if os.path.exists(self.path) and os.name == "nt":
                os.remove(self.path)  # Python 2 on Windows cannot rename over
            os.rename(tmp, self.path)
            self.dirty = 0
        except (IOError, OSError):
            pass


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
    global PROGRESS
    opts, roots = parse(sys.argv[1:] if argv is None else argv)
    exts = exts_of(opts)
    door = Door(opts.door, token_of(opts), insecure=opts.insecure)
    state = State(opts.state or None)
    host = opts.host or socket.gethostname()
    limit = int(opts.max_mb * (1 << 20))
    counts = {"seen": 0, "known": 0, "sent": 0, "duplicate": 0, "failed": 0, "large": 0}
    started = time.time()
    progress = PROGRESS = Progress(quiet=opts.quiet, every=opts.progress_every)
    found = sorted(walk(roots, exts, progress), key=lambda f: rank(f[0], exts))
    total_bytes = sum(size for _, size in found)
    progress.clear()
    say(
        opts,
        "%d files (%s) to look at under %s, found in %s"
        % (
            len(found),
            human_bytes(total_bytes),
            ", ".join(roots),
            human_time(time.time() - started),
        ),
    )
    batches = (len(found) + BATCH - 1) // BATCH
    tally = {"bytes": 0, "read": 0, "cached": 0, "since": time.time(), "where": ""}

    def status(what, force=False):
        done, elapsed = tally["bytes"], max(0.001, time.time() - tally["since"])
        rate = tally["read"] / elapsed  # bytes actually read, not the cache's
        left = total_bytes - done
        eta = " | ~%s left" % human_time(left / rate) if rate > 0 and left > 0 else ""
        progress.show(
            "%s | %d/%d files | %s of %s | %d cached | %s/s%s"
            % (
                what,
                counts["seen"],
                len(found),
                human_bytes(done),
                human_bytes(total_bytes),
                tally["cached"],
                human_bytes(rate),
                eta,
            ),
            force=force,
        )

    def read(n):  # every block hashed: a large file shows it is moving
        tally["read"] += n
        tally["bytes"] += n
        status("hashing, " + tally["where"])

    try:
        for number, start in enumerate(range(0, len(found), BATCH), 1):
            batch = []
            where = tally["where"] = "batch %d/%d" % (number, batches)
            for path, size in found[start : start + BATCH]:
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
                    batch.append((path, digest))
                    status("hashing, " + where)
                except (IOError, OSError) as exc:
                    counts["failed"] += 1
                    say(opts, "cannot read %s: %s" % (fs_text(path), exc))
            if not batch:
                continue
            status("asking the door, " + where, force=True)
            held = door.known(sorted({h for _, h in batch}))
            asked = set()
            new = []
            for path, digest in batch:
                if digest in held or digest in asked:
                    continue
                asked.add(digest)  # the same bytes twice in one tree: once
                new.append((path, digest))
            counts["known"] += len(batch) - len(new)
            if opts.dry_run:
                for path, _ in new:
                    counts["sent"] += 1
                    say(opts, "would send %s" % fs_text(path))
                continue
            for n, (path, digest) in enumerate(new, 1):
                progress.show(
                    "sending %d/%d of %s | %d sent so far"
                    % (n, len(new), where, counts["sent"])
                )
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
                    say(opts, "refused %s: HTTP %s" % (fs_text(path), exc.code))
                    continue
                except (URLError, IOError, OSError) as exc:
                    counts["failed"] += 1
                    say(opts, "could not send %s: %s" % (fs_text(path), exc))
                    continue
                if out.get("created") is False:
                    counts["duplicate"] += 1
                else:
                    counts["sent"] += 1
                say(opts, "sent %s -> doc %s" % (fs_text(path), out.get("doc_id")))
    except HTTPError as exc:
        progress.done()
        print("the door refused: HTTP %s (the token?)" % exc.code, file=sys.stderr)
        return 2
    except URLError as exc:
        progress.done()
        print("the door did not answer: %s" % exc, file=sys.stderr)
        return 2
    finally:
        state.save()
        PROGRESS = None
    progress.done()
    print(
        "%(seen)d looked at, %(known)d already there, %(sent)d " % counts
        + ("to send" if opts.dry_run else "sent")
        + ", %(duplicate)d the same as one there, %(large)d too large,"
        " %(failed)d failed" % counts + " (%s)" % human_time(time.time() - started)
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(run())
