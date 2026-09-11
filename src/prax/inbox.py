"""Captures: uploads, sent web pages, fetched URLs, the drop folder.

Everything that is not an importer of a curated library arrives here: a
file uploaded from the UI or the API, a page a browser extension sends
with its rendered DOM, a URL a bookmarklet posts for the server to fetch,
or a file dropped into ``data/inbox/``. All of them are clients of
``prax.store`` (invariant 3): register the original bytes, index what is
cheap to index at once (text, and HTML through trafilatura, which is light
enough for the serving path), leave the rest to the parse queue
(invariant 7), and give the document its domain set.

What a capture records in ``meta``::

    source          "upload" | "capture" | "inbox"
    capture         {at, session, by}   when, in which send, from what
    tags            from the request or the sidecar, if any
    previous_capture  the last document with the same canonical URL

The drop folder is prax's own (not a source in the sense of invariant
10): a consumed file is removed, the archive holds its bytes; a file the
store refused goes to ``inbox/failed/``. A file in ``inbox/<module>/``
belongs to that domain; ``<name>.json`` next to a file is a sidecar with
``title``, ``source_url``, ``domains`` and ``tags``.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prax import config, models, ontology, store

log = logging.getLogger("prax.inbox")

HTML_TYPES = ("text/html", "application/xhtml+xml")
# a browser's own signature: sites answer 403 to anything else on sight,
# and the door fetches on a person's behalf, from their own browser's ask
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
FETCH_TIMEOUT = 30.0
FETCH_MAX_BYTES = 64 * 1024 * 1024
SETTLE_SECONDS = 2.0  # a file still being written is left for the next scan
SKIP_SUFFIXES = (".part", ".crdownload", ".tmp", ".download")
FAILED_DIR = "failed"
TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|igshid|ref|ref_src"
    r"|_hsenc|_hsmi|yclid|vero_id|s_cid)$",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def inbox_dir() -> Path:
    return config.data_dir() / "inbox"


BROWSER_DROP = "prax-inbox"  # under the browser's download folder


def browser_drop_folders() -> list[Path]:
    """Where the browser extension saves a file it could not fetch itself
    (a host that challenges everything but a navigation): the browser's
    own download folder, a ``prax-inbox`` subfolder, consumed like the
    drop folder when it exists."""
    home = Path.home()
    out = []
    for downloads in (home / "Downloads", home / "Desktop" / "Downloads"):
        p = downloads / BROWSER_DROP
        if p.is_dir():
            out.append(p)
    return out


# ------------------------------------------------------------------ URLs


def canonical_url(url: str) -> str:
    """The URL without fragment and tracking parameters, host lowercased,
    so re-captures of one page find each other."""
    parts = urllib.parse.urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not TRACKING_PARAMS.match(k)
    ]
    path = parts.path or "/"
    return urllib.parse.urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urllib.parse.urlencode(query),
            "",
        )
    )


def check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not an http(s) URL: {url!r}")


def fetch_url(url: str, *, timeout: float = FETCH_TIMEOUT) -> tuple[bytes, str, str]:
    """Fetch ``url``: the bytes, the content type and the final URL after
    redirects. Only http(s)."""
    check_url(url)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.7",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(FETCH_MAX_BYTES + 1)
        if len(data) > FETCH_MAX_BYTES:
            raise ValueError(f"{url}: larger than {FETCH_MAX_BYTES} bytes")
        ctype = (resp.headers.get_content_type() or "").lower()
        return data, ctype or "application/octet-stream", resp.geturl()


# ------------------------------------------------------------ registering


@dataclass
class Capture:
    """What one capture became."""

    doc_id: int
    created: bool
    indexed: bool
    domains: list[str] | None
    previous: int | None = None
    mime: str = ""
    duplicate_of: int | None = None  # the page said the same as this document
    replaced: int | None = None  # a bare-DOM capture this snapshot retired


def _capture_meta(
    source: str,
    *,
    session: str | None,
    by: str | None,
    tags: list[str] | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "source": source,
        "capture": {"at": _now(), "session": session, "by": by},
    }
    if tags:
        meta["tags"] = [t for t in tags if t]
    if extra:
        extra = dict(extra)
        for key in ("mode", "note"):  # what the sender says about the page
            if f"capture_{key}" in extra:
                meta["capture"][key] = extra.pop(f"capture_{key}")
        meta.update(extra)
    return meta


def _previous_capture(
    con: sqlite3.Connection, url: str | None, *, exclude: int
) -> int | None:
    if not url:
        return None
    row = con.execute(
        "SELECT id FROM documents WHERE source_url = ? AND id != ?"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id DESC LIMIT 1",
        (url, exclude),
    ).fetchone()
    return row[0] if row else None


def _same_page_as(
    con: sqlite3.Connection, url: str, data: bytes
) -> tuple[int | None, dict[str, Any] | None]:
    """An earlier live capture of ``url`` that says the same as these
    bytes, by chunk fingerprint of the extracted text: its id and meta,
    or (None, None). A page whose markup changed but whose text did not is
    the same page."""
    earlier = store.live_captures_of(con, url)
    if not earlier:
        return None, None
    try:
        from prax import parsers

        text = parsers.by_name("trafilatura")(data)
    except Exception:  # noqa: BLE001 - no extractor, or nothing extractable
        return None, None
    mine = store.fingerprint_text(text)
    for d in reversed(earlier):
        if store.similarity(mine, store.chunk_fingerprint(con, d["doc_id"])) >= (
            store.DUPLICATE_THRESHOLD
        ):
            return d["doc_id"], d["meta"]
    return None, None


def _give_domains(
    con: sqlite3.Connection, doc_id: int, domains: list[str] | None, *, by: str
) -> list[str] | None:
    """The domains asked for, else the prax.yaml rules; a document that
    already has a set keeps it and gains the asked-for ones."""
    if domains:
        for d in domains:
            store.add_domain(con, doc_id, d, by=by)
        return store.document_domains(con, doc_id)
    current = store.document_domains(con, doc_id)
    if current:
        return current
    rules = list(models.load().get("domains") or [])
    if rules:
        store.assign_domains(con, rules, ids=[doc_id])
    return store.document_domains(con, doc_id)


def _index_now(con: sqlite3.Connection, doc_id: int, mime: str) -> bool:
    """Index what is cheap: HTML through trafilatura. Everything else waits
    for the parse queue on the batch host."""
    if store.is_indexed(con, doc_id):
        return True
    if mime not in HTML_TYPES:
        return False
    from prax.parsers import queue

    try:  # force: a short page is still the page that was asked for
        action = queue.parse_one(con, doc_id, force=True)
    except Exception as exc:  # noqa: BLE001 - the queue records the attempt
        log.warning("doc %s: inline parse failed: %s", doc_id, exc)
        return False
    return action in ("created", "upgraded")


def ingest_bytes(
    con: sqlite3.Connection,
    data: bytes,
    *,
    mime: str,
    source: str,
    title: str | None = None,
    source_url: str | None = None,
    original_path: str | None = None,
    domains: list[str] | None = None,
    tags: list[str] | None = None,
    session: str | None = None,
    by: str | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> Capture:
    """Register a capture, index it when cheap, link it to an earlier
    capture of the same URL, and give it its domains."""
    url = canonical_url(source_url) if source_url else None
    meta = _capture_meta(source, session=session, by=by, tags=tags, extra=extra_meta)
    # a page sent again: the bytes differ between two visits, the text does
    # not; the same page is one document, unless this send is a snapshot
    # and the earlier one only the bare DOM, in which case this one wins
    same_id: int | None = None
    if url and mime in HTML_TYPES and source == "capture":
        same_id, same_meta = _same_page_as(con, url, data)
        if same_id is not None:
            better = store.capture_rank(meta) > store.capture_rank(same_meta or {})
            if not better:
                store.note_recapture(con, same_id, session=session, by=by)
                if domains:
                    _give_domains(con, same_id, domains, by=by or source)
                return Capture(
                    same_id,
                    False,
                    store.is_indexed(con, same_id),
                    store.document_domains(con, same_id),
                    None,
                    mime,
                    duplicate_of=same_id,
                )
    # HTML is text/* to the store, which would index the markup itself;
    # register it bare and let trafilatura produce the text
    enter = store.register if mime in HTML_TYPES else store.ingest_file
    result = enter(
        con,
        data,
        mime=mime,
        title=title,
        source_url=url,
        meta=meta,
        original_path=original_path,
    )
    doc_id = result["doc_id"]
    previous = None
    if result["created"]:
        previous = _previous_capture(con, url, exclude=doc_id)
        if previous is not None:
            m = store.get_meta(con, doc_id)
            m["previous_capture"] = previous
            store.set_meta(con, doc_id, m)
    indexed = _index_now(con, doc_id, mime)
    got = _give_domains(con, doc_id, domains, by=by or source)
    replaced = None
    if same_id is not None and result["created"] and indexed:
        # the earlier bare-DOM capture gives way to this snapshot
        for d in list(store.document_domains(con, same_id) or []):
            store.add_domain(con, doc_id, d, by="rule")
        store.retire_document(
            con,
            same_id,
            reason="replaced by a snapshot",
            duplicate_of=doc_id,
            by=by or source,
        )
        replaced = same_id
        got = store.document_domains(con, doc_id)
    return Capture(
        doc_id, result["created"], indexed, got, previous, mime, replaced=replaced
    )


_TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def html_title(data: bytes) -> str | None:
    """The page's own title, for a capture that names none."""
    m = _TITLE.search(data[:65536])
    if not m:
        return None
    import html as html_mod

    title = html_mod.unescape(m.group(1).decode("utf-8", errors="replace"))
    title = " ".join(title.split())
    return title or None


def ingest_html(
    con: sqlite3.Connection,
    html: str | bytes,
    *,
    url: str,
    title: str | None = None,
    domains: list[str] | None = None,
    tags: list[str] | None = None,
    session: str | None = None,
    by: str | None = "extension",
    mode: str | None = None,
    note: str | None = None,
) -> Capture:
    """A page as the browser rendered it (the extension's path). ``mode``
    says what the page is (``snapshot``: self-contained, ``dom``: the bare
    document) and ``note`` why, both kept under ``meta.capture``."""
    data = html.encode("utf-8") if isinstance(html, str) else html
    extra: dict[str, Any] = {}
    if mode or note:
        extra["capture_mode"] = mode
        extra["capture_note"] = note
    return ingest_bytes(
        con,
        data,
        mime="text/html",
        source="capture",
        title=title or html_title(data),
        source_url=url,
        domains=domains,
        tags=tags,
        session=session,
        by=by,
        extra_meta=extra or None,
    )


def ingest_url(
    con: sqlite3.Connection,
    url: str,
    *,
    title: str | None = None,
    domains: list[str] | None = None,
    tags: list[str] | None = None,
    session: str | None = None,
    by: str | None = "url",
) -> Capture:
    """Fetch a URL server-side (bookmarklet, share target, MCP) and keep
    what came back: a page, a PDF, anything."""
    check_url(url)
    data, ctype, final = fetch_url(url)
    mime = "text/html" if ctype in HTML_TYPES else ctype
    name = urllib.parse.urlsplit(final).path.rsplit("/", 1)[-1] or None
    return ingest_bytes(
        con,
        data,
        mime=mime,
        source="capture",
        title=title or (html_title(data) if mime in HTML_TYPES else name),
        source_url=final,
        original_path=name if mime not in HTML_TYPES else None,
        domains=domains,
        tags=tags,
        session=session,
        by=by,
        extra_meta={"requested_url": url}
        if canonical_url(url) != canonical_url(final)
        else None,
    )


def ingest_upload(
    con: sqlite3.Connection,
    data: bytes,
    *,
    filename: str | None,
    mime: str | None = None,
    title: str | None = None,
    source_url: str | None = None,
    domains: list[str] | None = None,
    tags: list[str] | None = None,
    session: str | None = None,
    by: str | None = "upload",
) -> Capture:
    """A file handed over the door (the UI's upload, a script)."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1] or None
    if not mime or mime == "application/octet-stream":
        mime = mimetypes.guess_type(name or "")[0] or "application/octet-stream"
    return ingest_bytes(
        con,
        data,
        mime=mime,
        source="upload",
        title=title or name,
        source_url=source_url,
        original_path=name,
        domains=domains,
        tags=tags,
        session=session,
        by=by,
    )


# ------------------------------------------------------------ the folder


@dataclass
class ScanReport:
    registered: list[int] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    waiting: int = 0  # still being written, or a sidecar without its file

    def __str__(self) -> str:
        return (
            f"{len(self.registered)} registered, {len(self.duplicates)} already"
            f" known, {len(self.failed)} failed, {self.waiting} waiting"
        )


def _sidecar(path: Path) -> tuple[Path | None, dict[str, Any]]:
    side = path.with_name(path.name + ".json")
    if not side.is_file():
        return None, {}
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("%s: unreadable sidecar: %s", side, exc)
        return side, {}
    return side, data if isinstance(data, dict) else {}


def _is_sidecar_name(path: Path) -> bool:
    """``report.pdf.json`` is a sidecar for ``report.pdf``; ``notes.json``
    is a file of its own."""
    stem = path.name[:-5]
    return "." in stem and not stem.startswith(".")


def _settled(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime >= SETTLE_SECONDS
    except OSError:
        return False


def _folder_domains(root: Path, path: Path) -> list[str]:
    """``inbox/<module>/…`` puts the file in that domain."""
    rel = path.relative_to(root).parts
    modules = ontology.current().modules
    return (
        [rel[0]]
        if len(rel) > 1 and rel[0] in modules and rel[0] != ontology.CORE
        else []
    )


def scan(
    con: sqlite3.Connection,
    root: Path | None = None,
    *,
    session: str | None = None,
    consume: bool = True,
    domains: list[str] | None = None,
) -> ScanReport:
    """Register every file in the drop folder once, remove what was taken,
    move what the store refused to ``failed/``. With ``consume`` off the
    folder is somebody's (a download folder, a project's PDFs): files stay
    where they are, nothing is moved, and the store's hash keeps a second
    run from registering them again. ``domains`` applies to every file
    that names none itself."""
    root = root or inbox_dir()
    root.mkdir(parents=True, exist_ok=True)
    failed_dir = root / FAILED_DIR
    report = ScanReport()
    session = session or f"inbox-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if failed_dir in path.parents or not path.exists():
            continue  # a sidecar goes with its file
        if path.name.startswith(".") or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.suffix == ".json" and _is_sidecar_name(path):
            if not path.with_name(path.name[:-5]).exists():
                report.waiting += 1  # the file it belongs to is still coming
            continue  # a sidecar; handled with its file
        if not _settled(path):
            report.waiting += 1
            continue
        side, extra = _sidecar(path)
        doms = (
            list(extra.get("domains") or [])
            or _folder_domains(root, path)
            or list(domains or [])
        )
        try:
            data = path.read_bytes()
            cap = ingest_bytes(
                con,
                data,
                mime=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                source="inbox",
                title=extra.get("title") or path.stem,
                source_url=extra.get("source_url"),
                original_path=str(path.relative_to(root)).replace("\\", "/"),
                domains=doms or None,
                tags=list(extra.get("tags") or []) or None,
                session=session,
                by="inbox",
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the scan
            log.warning("%s: %s", path, exc)
            report.failed.append(str(path))
            if consume:
                failed_dir.mkdir(exist_ok=True)
                shutil.move(str(path), failed_dir / path.name)
                if side:
                    shutil.move(str(side), failed_dir / side.name)
            continue
        (report.registered if cap.created else report.duplicates).append(cap.doc_id)
        if consume:
            path.unlink()
            if side:
                side.unlink()
    if consume:
        for d in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            if d != failed_dir and not any(d.iterdir()):
                d.rmdir()
    return report


def pending_captures(con: sqlite3.Connection) -> list[int]:
    """Captures the door only registered (PDFs, images): what the batch
    host's watcher parses. The curated imports' own backlog is not
    included; ``parse_pending.py --pending`` is for that."""
    return [
        r[0]
        for r in con.execute(
            "SELECT id FROM documents WHERE text_hash IS NULL"
            " AND json_extract(meta, '$.retired') IS NULL"
            " AND json_extract(meta, '$.source') IN ('upload', 'capture', 'inbox')"
            " ORDER BY id"
        )
    ]


# ------------------------------------------------------------- the list


def recent(con: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    """The latest captures with their state: indexed, extracted, domains."""
    rows = con.execute(
        "SELECT id, title, mime, source_url, text_hash, meta FROM documents"
        " WHERE json_extract(meta, '$.source') IN ('upload', 'capture', 'inbox')"
        " AND json_extract(meta, '$.retired') IS NULL"
        " ORDER BY json_extract(meta, '$.capture.at') DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        out.append(
            {
                "doc_id": r["id"],
                "title": r["title"],
                "mime": r["mime"],
                "source_url": r["source_url"],
                "source": meta.get("source"),
                "capture": meta.get("capture") or {},
                "domains": meta.get("domains"),
                "tags": meta.get("tags") or [],
                "indexed": bool(r["text_hash"]),
                "extracted": bool(meta.get("extraction")),
                "previous_capture": meta.get("previous_capture"),
                "recaptured": len(meta.get("recaptured") or []),
            }
        )
    return out
