"""`prax import`: what a service or an app exported, into the library
through the door. The readers live in `prax.importers` (github, chats,
links) and are imported when the command runs, so the rest of `prax`
stays as light as it is; this is the command around them."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prax.client import Door

from . import out

if TYPE_CHECKING:
    from prax.importers import feed

WHAT = (
    "github",
    "chat",
    "links",
    "project",
    "claude",
    "citations",
    "zotero",
    "graph",
    "latex",
    "latex2html",
    "mdwiki",
)


def import_(door: Door, a: Any) -> int:
    if a.what == "github":
        return _github(door, a)
    if a.what == "chat":
        return _chat(door, a)
    if a.what == "links":
        return _links(door, a)
    if a.what == "project":
        return _project(door, a)
    if a.what == "claude":
        return _claude(door, a)
    if a.what == "citations":
        return _citations(door, a)
    if a.what == "zotero":
        return _zotero(door, a)
    if a.what == "graph":
        return _graph(door, a)
    if a.what == "latex":
        return _latex(door, a)
    if a.what == "latex2html":
        return _latex2html(door, a)
    if a.what == "mdwiki":
        return _mdwiki(door, a)
    out.fail(f"unknown source {a.what!r}", "one of: " + ", ".join(WHAT))
    return 2


def _graph(door: Door, a: Any) -> int:
    """A piece of another library's graph (a ``prax export`` file) into this
    one, as ``import:<name>``; the same name again replaces what the last
    import of it linked."""
    if len(a.files) != 1:
        out.fail("prax import graph FILE --name SOURCE")
        return 2
    path = Path(a.files[0]).expanduser()
    if not path.is_file():  # else it reads as a door that is not there
        out.fail(f"no such file: {path}")
        return 2
    source = a.name or path.stem.removesuffix(".graph")
    rep = door.post_bytes(
        "/graph/import",
        path.read_bytes(),
        params={"source": source, "dry_run": "true" if a.dry_run else "false"},
        content_type="application/x-ndjson",
    )
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    verb = "would link" if a.dry_run else "linked"
    out.say(out.bold(f"import:{rep['source']}") + out.dim(f"   {rep['run']}"))
    out.say(
        f"  documents: {rep['documents']}, {rep['documents_held']} held here"
        f" · edges: {rep['edges']}, {verb} {rep['linked']},"
        f" {rep['queued']} to the review queue"
        + (
            f", {rep['ended_skipped']} ended ones left out"
            if rep["ended_skipped"]
            else ""
        )
    )
    out.say(
        f"  pages: {rep['pages_new']} new, {rep['pages_revised']} revised,"
        f" {rep['pages_same']} the same"
        + (
            f", refused: {', '.join(rep['pages_refused'])}"
            if rep["pages_refused"]
            else ""
        )
        + (
            f" · retired the last import's {rep['retired']} edges"
            if rep["retired"]
            else ""
        )
    )
    if rep.get("ontology") and rep["ontology"] != rep.get("ontology_here"):
        out.say(
            out.dim(
                f"  written against {rep['ontology']},"
                f" read against {rep['ontology_here']}"
            )
        )
    return 0


def export(door: Door, a: Any) -> int:
    """A piece of the graph as a file (``prax.graph.graphio``)."""
    params = {
        k: v
        for k, v in {
            "project": a.project,
            "domain": a.domain,
            "tag": a.tag,
            "entity": a.entity,
            "type": a.type,
            "hops": a.hops,
            "history": "true" if a.history else None,
        }.items()
        if v not in (None, "")
    }
    if not ({"project", "domain", "tag", "entity"} & set(params)):
        out.fail("what to export?", "--project, --domain, --tag or --entity")
        return 2
    data = door.get_bytes_with("/graph/export", params)
    if a.output in (None, "-"):
        sys.stdout.buffer.write(data)
        return 0
    target = Path(a.output).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    head = json.loads(data.split(b"\n", 1)[0])
    counts = head.get("counts") or {}
    out.say(
        f"{target}: {counts.get('edges', 0)} edges from {counts.get('documents', 0)}"
        f" documents, ontology {head.get('ontology')}"
    )
    return 0


def _zotero(door: Door, a: Any) -> int:
    """A Zotero library into the door: planned here over a copy of its
    database, applied there one document at a time (POST
    /import/zotero/item). Safe to interrupt and run again: what the
    door has is skipped, a changed record is refreshed."""
    from collections import Counter

    from prax.importers import zotero

    if len(a.files) != 1:
        out.fail("prax import zotero <the Zotero data directory>")
        return 2
    zotero_dir = Path(a.files[0]).expanduser()
    if not (zotero_dir / "zotero.sqlite").is_file():
        out.fail(f"no zotero.sqlite in {zotero_dir}")
        return 2
    lib = zotero.open_library(zotero_dir)  # a copy, opened read-only
    try:
        if a.dry_run:  # the census: nothing sent, nothing written
            print(zotero.inventory(lib, limit=a.limit).report(), flush=True)
            return 0
        planned = list(zotero.plan(lib))
        if a.limit:
            planned = planned[: a.limit]
        out.say(
            out.bold("Zotero") + out.dim(f"   {len(planned)} planned · {door.base_url}")
        )
        actions: Counter[str] = Counter()
        edges = 0
        errors: list[str] = []
        for n, p in enumerate(planned, 1):
            data, text = zotero.load(p) if not p.missing else (None, None)
            files = None
            if data is not None:
                files = {"file": (p.path.name if p.path else "file", data, p.mime)}
            fields: dict[str, Any] = {"item": json.dumps(p.to_wire())}
            if text is not None:
                fields["cache_text"] = text
            try:
                r = door.post_form("/import/zotero/item", fields, files=files)
                actions[r["action"]] += 1
                edges += int(r.get("edges") or 0)
            except Exception as exc:  # noqa: BLE001 - keep going; listed at the end
                errors.append(f"{p.key}: {exc}")
                actions["error"] += 1
            if n % 50 == 0 and not a.quiet:
                out.hint(f"  {n}/{len(planned)}: {dict(actions)}")
        out.say(
            "  "
            + ", ".join(f"{v} {k}" for k, v in sorted(actions.items()))
            + f"; {edges} authored_by edges"
        )
        for e in errors[:20]:
            out.hint("  " + e)
        return 1 if errors else 0
    finally:
        lib.close()


KNOWN_BATCH = 1000  # hashes one question to the door may ask about


def _latex(door: Door, a: Any) -> int:
    """Papers with their LaTeX source (``prax.importers.latex``): each
    folder's PDF sent as the original with the text pandoc made of its
    source. A PDF the door holds already is skipped unless ``--refresh``,
    so a run can be stopped and started again."""
    import hashlib

    from prax.importers import latex

    if not a.files:
        out.fail("prax import latex <a folder of manuscripts, each a PDF and its .tex>")
    binary = latex.pandoc()
    if binary is None:
        out.fail("pandoc is needed", "pip install prax[latex], or pandoc on the PATH")
        return 2
    stamp = latex.stamp(latex.pandoc_version(binary))
    found = latex.manuscripts(Path(a.files[0]).expanduser())
    digests = {m.key: hashlib.sha256(m.pdf.read_bytes()).hexdigest() for m in found}
    held: set[str] = set()
    if not a.refresh:
        hashes = list(digests.values())
        for i in range(0, len(hashes), KNOWN_BATCH):
            got = door.post_json("/known", {"hashes": hashes[i : i + KNOWN_BATCH]})
            held.update(got.get("known") or [])
    todo = [m for m in found if digests[m.key] not in held]
    already = len(found) - len(todo)
    if a.limit:
        todo = todo[: a.limit]
    out.say(
        out.bold("LaTeX")
        + out.dim(
            f"   {len(found)} manuscripts, {already} held already,"
            f" {len(todo)} to send · {stamp} · {door.base_url}"
        )
    )
    if a.dry_run:
        for m in todo[:20]:
            out.say(f"  {m.title}  " + out.dim(f"{m.date or ''} {m.main.name}"))
        return 0
    sent = 0
    errors: list[str] = []
    for n, m in enumerate(todo, 1):
        try:
            text = latex.convert(m, binary)
        except Exception as exc:  # noqa: BLE001 - the PDF still goes, parsed as any
            errors.append(f"{m.key}: {exc}")
            text = None
        paper = {"authors": list(m.authors), "date": m.date or ""}
        fields: dict[str, Any] = {
            "title": m.title,
            "by": f"import:{latex.SOURCE}",
            "paper": json.dumps(paper),
            "tags": ",".join(a.tag or []),
            "domains": ",".join(a.domain or []),
        }
        if text:
            fields.update(text=text, text_source=stamp)
        try:
            door.post_form(
                "/ingest/file",
                fields,
                files={"file": (m.pdf.name, m.pdf.read_bytes(), "application/pdf")},
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001 - keep going; listed at the end
            errors.append(f"{m.key}: {exc}")
        if n % 25 == 0 and not a.quiet:
            out.hint(f"  {n}/{len(todo)} sent")
    out.say(f"  {sent} sent, {len(errors)} with a problem")
    for e in errors[:20]:
        out.hint("  " + e)
    return 1 if errors else 0


def _mdwiki(door: Door, a: Any) -> int:
    """A repository of papers with a Markdown wiki about them
    (``prax.importers.mdwiki``): its PDFs as documents, its wiki as pages
    whose links are the library's. Three passes: the PDFs; every page once,
    for its document; every page again with its links resolved. A page
    whose text has not changed is not written again, so a run can be
    repeated."""
    from prax.client import DoorError
    from prax.importers import mdwiki

    if not a.files:
        out.fail("prax import mdwiki <a working copy> --name NAME [--url-base URL]")
    root = Path(a.files[0]).expanduser()
    name = a.name or root.name
    tags = [name, *(a.tag or [])]
    domains = ",".join(a.domain or [])
    found_pdfs = mdwiki.pdfs(root)
    found_notes = mdwiki.notes(root, name)
    if a.limit:
        found_pdfs, found_notes = found_pdfs[: a.limit], found_notes[: a.limit]
    out.say(
        out.bold(name)
        + out.dim(
            f"   {len(found_pdfs)} PDFs, {len(found_notes)} pages · {door.base_url}"
        )
    )
    if a.dry_run:
        for shown in found_notes[:15]:
            out.say(f"  {shown.slug}  " + out.dim(shown.title[:60]))
        return 0
    errors: list[str] = []
    documents: dict[str, int] = {}
    for n, path in enumerate(found_pdfs, 1):
        topic = path.split("/", 1)[0] if "/" in path else ""
        fields: dict[str, Any] = {
            "by": f"import:{mdwiki.SOURCE}",
            "tags": ",".join([*tags, *([f"topic:{topic}"] if topic else [])]),
            "domains": domains,
        }
        if a.url_base:
            fields["source_url"] = a.url_base.rstrip("/") + "/" + path
        try:
            got = door.post_form(
                "/ingest/file",
                fields,
                files={
                    "file": (
                        path.rsplit("/", 1)[-1],
                        (root / path).read_bytes(),
                        "application/pdf",
                    )
                },
            )
            if got.get("doc_id"):
                documents[path] = int(got["doc_id"])
        except Exception as exc:  # noqa: BLE001 - keep going; listed at the end
            errors.append(f"{path}: {exc}")
        if n % 25 == 0 and not a.quiet:
            out.hint(f"  {n}/{len(found_pdfs)} PDFs")
    out.say(f"  {len(documents)} PDFs held")
    pages: dict[str, int] = {}
    written = 0

    def write(note: Any, text: str) -> None:
        nonlocal written
        try:
            held = door.get_json(f"/page/{note.slug}")
        except DoorError as exc:
            if exc.status != 404:
                raise
            held = None
        if held is not None:
            pages[note.path] = int(held["doc_id"])
            if held.get("text", "").strip() == text.strip():
                return
        where = f"{a.url_base.rstrip('/')}/{note.path}" if a.url_base else note.path
        got = door.put_json(
            f"/page/{note.slug}",
            {
                "text": text,
                "title": note.title,
                "kind": "topic",
                "author": "agent",
                "note": f"from {name}: {where}",
            },
        )
        pages[note.path] = int(got["doc_id"])
        written += 1

    entries = mdwiki.topic_entries(found_notes)
    for stage in (1, 2):
        topics = {t: pages[p] for t, p in entries.items() if p in pages}
        for note in found_notes:
            text = mdwiki.rewrite(note.text, note.path, documents, pages, topics)
            try:
                write(note, text)
            except Exception as exc:  # noqa: BLE001 - keep going; listed at the end
                errors.append(f"{note.path}: {exc}")
        if not a.quiet:
            out.hint(f"  pass {stage}: {len(pages)} pages, {written} written")
    out.say(f"  {len(pages)} pages, {written} revisions, {len(errors)} with a problem")
    for e in errors[:20]:
        out.hint("  " + e)
    return 1 if errors else 0


MIN_PAGE_CHARS = 200  # a page's own prose, figures aside: the door's MIN_CHARS


def _book_page(door: Door, fetcher: Any, book: Any, authors: list[str]) -> None:
    """The book's page in prax (``book-<name>``): its contents, each entry
    linking the document of its page. Written as the agent's: a page a
    person has edited is left as it is (the door answers 409)."""
    from prax.client import DoorError
    from prax.importers import latex2html as l2h

    documents: dict[str, int] = {}
    offset = 0
    while True:
        got = door.get_json(
            "/documents",
            {"tag": f"book:{book.slug}", "limit": 200, "offset": offset},
        )
        items = got.get("items") or []
        for item in items:
            if item.get("source_url"):
                documents[str(item["source_url"])] = int(item["id"])
        offset += len(items)
        if not items or offset >= int(got.get("total") or 0):
            break
    index = fetcher.get(book.url)
    if index is None:
        return
    entries = l2h.contents(book.url, index.decode("utf-8", "replace"))
    text = l2h.contents_page(book, entries, documents, authors)
    try:
        door.put_json(
            f"/page/book-{book.slug}",
            {
                "text": text,
                "title": book.title,
                "kind": "topic",
                "author": "agent",
                "note": "the book's contents, as imported",
            },
        )
        out.say(f"  page book-{book.slug}: {len(entries)} entries")
    except DoorError as exc:
        if exc.status != 409:
            raise
        out.hint(f"  page book-{book.slug}: a person's edit stands")


def _latex2html(door: Door, a: Any) -> int:
    """Books on the web written with latex2html and MathJax
    (``prax.importers.latex2html``): every page of each book a document,
    its HTML the original and its text the page with the mathematics as
    LaTeX. The crawl keeps what it fetched under ``--cache`` and keeps
    the site's Crawl-delay; a page the door holds already is skipped."""
    import hashlib

    from prax.importers import latex2html as l2h

    if not a.files:
        out.fail("prax import latex2html <a book's index URL> …")
    cache = Path(a.cache).expanduser() if a.cache else Path.home() / ".cache/prax/crawl"
    fetcher = l2h.Fetcher(cache)
    sent = skipped = 0
    errors: list[str] = []
    for url in a.files:
        book = l2h.book(fetcher, url)
        if book is None:
            errors.append(f"{url}: not served")
            continue
        pages = book.pages[: a.limit] if a.limit else book.pages
        out.say(
            out.bold(book.short)
            + out.dim(
                f"   {len(book.pages)} pages, {len(pages)} this run · every"
                f" {fetcher.pause(url):g} s · {door.base_url}"
            )
        )
        if a.dry_run:
            for p in pages[:10]:
                out.say("  " + p)
            continue
        defined = l2h.book_macros(fetcher, book)
        picture = l2h.data_url(fetcher)
        for n, page in enumerate(pages, 1):
            data = fetcher.get(page)
            if data is None:
                errors.append(f"{page}: not served")
                continue
            if not a.refresh:
                digest = hashlib.sha256(data).hexdigest()
                known = door.post_json("/known", {"hashes": [digest]}).get("known")
                if known:
                    skipped += 1
                    continue
            html = data.decode("utf-8", "replace")
            try:
                text = l2h.to_markdown(html, page, picture, defined)
            except Exception as exc:  # noqa: BLE001 - the page still goes, as HTML
                errors.append(f"{page}: {exc}")
                text = ""
            # a chapter's page that only lists its sections has nothing of
            # its own: its sections are the pages. Nor has a slide of a
            # title and three bullets: the door takes under 200 characters
            # for no text (queue.MIN_CHARS), and the page would sit textless
            prose = [
                x for x in text.splitlines() if x and not x.startswith(("#", "!["))
            ]
            if sum(len(x) for x in prose) < MIN_PAGE_CHARS:
                skipped += 1
                continue
            title = f"{l2h.page_title(html) or page.rsplit('/', 1)[-1]} ({book.short})"
            fields: dict[str, Any] = {
                "title": title,
                "source_url": page,
                "by": f"import:{l2h.SOURCE}",
                "paper": json.dumps({"authors": a.author or [], "journal": book.title}),
                "tags": ",".join([*(a.tag or []), f"book:{book.slug}"]),
                "domains": ",".join(a.domain or []),
            }
            fields["text_source"] = l2h.stamp()
            name = page.rsplit("/", 1)[-1] or "index.html"
            try:
                # the text as a file part: with its pictures it passes the
                # 1 MB the door's form allows a field
                door.post_form(
                    "/ingest/file",
                    fields,
                    files={
                        "file": (name, data, "text/html"),
                        "text_file": ("text.md", text.encode("utf-8"), "text/markdown"),
                    },
                )
                sent += 1
            except Exception as exc:  # noqa: BLE001 - keep going; listed at the end
                errors.append(f"{page}: {exc}")
            if n % 25 == 0 and not a.quiet:
                out.hint(f"  {n}/{len(pages)} · {fetcher.fetched} fetched")
        if len(book.pages) > 1:
            try:
                _book_page(door, fetcher, book, a.author or [])
            except Exception as exc:  # noqa: BLE001 - the pages are in all the same
                errors.append(f"{book.url}: its contents page: {exc}")
    out.say(f"  {sent} sent, {skipped} held already, {len(errors)} with a problem")
    for e in errors[:20]:
        out.hint("  " + e)
    return 1 if errors else 0


def _citations(door: Door, a: Any) -> int:
    """The citation network, fetched by the door: a job to follow."""
    from . import running

    body = {
        "source": getattr(a, "source", None) or "openalex",
        "limit": getattr(a, "limit", None),
        "ids": getattr(a, "ids", None),
        "resolve_titles": bool(getattr(a, "resolve_titles", False)),
        "refresh": bool(getattr(a, "refresh", False)),
        "dry_run": bool(getattr(a, "dry_run", False)),
    }
    r = door.post_json("/import/citations", body)
    if r["dry_run"]:
        if a.json:
            print(json.dumps(r, indent=2))
        else:
            n = out.num(r["selected"])
            out.say(f"{n} documents would be looked up at {r['source']}")
        return 0
    if not a.json:
        out.say(
            out.bold("Citations")
            + out.dim(f"   {r['selected']} documents at {r['source']} · job {r['job']}")
        )
    return running.follow_job(door, r["job"], quiet=a.json, what="the import")


def _github(door: Door, a: Any) -> int:
    from prax.importers import github

    token = a.token_github or github.token()
    user = a.user or github.user()
    if not user and not token:
        out.fail(
            "whose stars? give a user name, or a token for your own",
            "prax import github octocat · PRAX_GITHUB_TOKEN=… prax import github",
        )
        return 2
    api = github.GitHub(token=token)
    who = user or "your account"
    out.say(
        out.bold("GitHub stars")
        + out.dim(f"   {who}" + ("" if token else " · no token: 60 requests an hour"))
    )
    try:
        return _run(door, a, github.SOURCE, github.items(api, user, log=out.hint))
    except RuntimeError as exc:
        out.fail(str(exc))
        return 1


def _chat(door: Door, a: Any) -> int:
    from prax.importers import chats

    conversations = []
    for path in a.files:
        try:
            found = chats.read(Path(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            out.fail(f"{path}: {exc}")
            return 2
        conversations += found
        for c in found:
            out.hint(
                f"  {path}: {c.name} ({c.app}), {out.num(len(c.messages))} messages"
            )
    if not conversations:
        out.fail("no conversations in those files")
        return 2
    out.say(
        out.bold("Chats")
        + out.dim(
            f"   {len(conversations)} conversations by month"
            + (" · links captured too" if a.links else "")
        )
    )
    return _run(door, a, chats.SOURCE, chats.items(conversations, with_links=a.links))


def _links(door: Door, a: Any) -> int:
    from prax.importers import links

    def everything() -> Iterable[feed.Item]:
        for path in a.files:
            yield from links.read(Path(path))

    try:
        items = list(everything())
    except (OSError, ValueError) as exc:
        out.fail(str(exc))
        return 2
    files = out.plural(len(a.files), "file")
    out.say(
        out.bold("Links")
        + out.dim(f"   {out.num(len(items))} in {files}; the door fetches each")
    )
    return _run(door, a, links.SOURCE, items)


def _project(door: Door, a: Any) -> int:
    """``prax import project`` is ``prax sync --apply --all-files``: one
    path to the door (``POST /projects/sync``), so a project's documents
    have one key and one title however they were sent."""
    from types import SimpleNamespace

    root = Path(a.files[0]) if a.files else Path.cwd()
    if not root.is_dir():
        out.fail(f"{root}: not a directory")
        return 2
    return sync(
        door,
        SimpleNamespace(
            root=str(root),
            apply=not a.dry_run,
            name=a.name,
            domain=a.domain,
            tag=a.tag,
            include=None,
            exclude=None,
            all_files=True,  # the documents under the directory, as before
            if_auto=False,
            auto=None,
            personal=False,
            json=getattr(a, "json", False),
            quiet=a.quiet,
        ),
    )


def _claude(door: Door, a: Any) -> int:
    from datetime import datetime

    from prax.importers import claude

    paths = [Path(p) for p in a.files] or [Path.cwd()]
    since = None
    if a.since:
        try:
            since = datetime.fromisoformat(a.since)
        except ValueError:
            out.fail(f"--since {a.since!r}: not a date (YYYY-MM-DD)")
            return 2
    found = list(claude.sessions(paths, since=since))
    if not a.quiet:
        where = ", ".join(str(p) for p in paths)
        out.say(
            out.bold("Claude Code sessions")
            + out.dim(f"   {out.plural(len(found), 'session')} for {where}")
        )
        for s in found[:12]:
            day = s.started.strftime("%Y-%m-%d") if s.started else "?"
            out.hint(
                f"  {day}  {(s.title or s.id[:8])[:50]}  ·  {len(s.messages)} turns,"
                f" {s.tools} tool calls left out"
            )
        if len(found) > 12:
            out.hint(f"  … and {len(found) - 12} more")
    if not found:
        if not a.quiet:
            out.hint(
                "  no transcripts: Claude Code keeps them under ~/.claude/projects/;"
                " give a project directory, a transcript or a folder of them"
            )
        return 0
    return _run(door, a, claude.SOURCE, claude.items(found))


def _run(door: Door, a: Any, source: str, items: Iterable[feed.Item]) -> int:
    from prax.importers import feed

    quiet = a.quiet or a.json
    report = feed.run(
        door,
        source,
        items,
        domains=a.domain or None,
        tags=a.tag or None,
        refresh=a.refresh,
        limit=a.limit,
        dry_run=a.dry_run,
        log=None if quiet else out.hint,
    )
    if a.json:
        print(
            json.dumps(
                {
                    "source": report.source,
                    "added": report.added,
                    "refreshed": report.refreshed,
                    "seen": report.seen,
                    "same": report.same,
                    "failed": report.failed,
                    "planned": [
                        {"key": i.key, "title": i.title, "url": i.url, "tags": i.tags}
                        for i in report.planned
                    ],
                },
                indent=2,
            )
        )
        return 0 if not report.failed else 1
    if a.dry_run:
        out.say()
        out.say(
            out.bold(f"Would add {len(report.planned)}")
            + out.dim(f"   {report.seen} already there")
        )
        for i in report.planned[:60]:
            out.hint(
                f"  {i.title or i.url or i.key}"
                + (out.dim(f"  {i.url}") if i.url and i.title else "")
            )
        if len(report.planned) > 60:
            out.hint(f"  … and {len(report.planned) - 60} more")
        out.hint("Nothing was sent. Drop --dry-run to import.")
        return 0
    if a.quiet:
        for key, why in report.failed[:20]:
            out.warn(f"{source}: {key}: {why}")
        return 0 if not report.failed else 1
    out.say()
    out.say(out.bold(str(report)))
    for key, why in report.failed[:20]:
        out.hint(f"  failed: {key}: {why}")
    if report.added or report.refreshed:
        out.hint("A worker reads what arrived: prax work --watch · prax jobs")
    return 0 if not report.failed else 1


GRAPH_FILE = Path(".prax") / "graph.jsonl"


def _without_moment(data: bytes) -> bytes:
    """A graph file with its header's ``exported_at`` taken out: what is the
    same when nothing in the graph changed."""
    newline = bytes([10])
    head, _, rest = data.partition(newline)
    try:
        header = json.loads(head)
    except ValueError:
        return data
    header.pop("exported_at", None)
    return json.dumps(header, sort_keys=True).encode() + newline + rest


def _refresh_graph_file(door: Door, root: Path, name: str) -> bool:
    """The session-end hook's last part (docs/graph-files.md): a project
    that keeps its graph beside it, ``.prax/graph.jsonl``, has it exported
    again (``prax export --project``) and written when anything but the
    moment of export changed, so a copy kept in git diffs only when the
    graph did. A project without the file is left without one: exporting
    once by hand is how a project chooses to keep it."""
    target = root / GRAPH_FILE
    if not name or not target.is_file():
        return False
    data = door.get_bytes_with("/graph/export", {"project": name})
    if _without_moment(data) == _without_moment(target.read_bytes()):
        return False
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return True


def sync(door: Door, a: Any) -> int:
    """A project's written knowledge to the library (``POST /projects/sync``):
    the plan by default, the sync with ``--apply``. ``--if-auto`` is the
    session-end hook's: it syncs only a project whose manifest says
    ``auto_sync``, or one with the older ``.prax-project`` file."""
    from prax.client import DoorError, project_files
    from prax.importers import project

    root = Path(a.root or ".").expanduser()
    legacy = (root / project.SETTINGS_FILE).is_file()
    cfg = project.settings(root) if legacy else None
    include = a.include or (cfg.include if cfg else [])
    exclude = a.exclude or (cfg.exclude if cfg else [])
    apply = bool(a.apply)
    try:
        if a.if_auto and not legacy:
            where = project_files(root, include=include, exclude=exclude, texts=False)
            if not where["remote"]:
                return 0
            got = door.get_json(
                "/projects", {"remote": where["remote"], "prefix": where["prefix"]}
            )
            if not (got.get("project") or {}).get("auto_sync"):
                return 0
        if a.if_auto:
            apply = True
        files = project_files(
            root,
            include=include,
            exclude=exclude,
            tracked_only=not a.all_files,
            texts=apply,
        )
        body = {
            **files,
            "name": a.name or (cfg.name if cfg else None),
            "domains": a.domain or (cfg.domains if cfg and cfg.domains else None),
            "tags": a.tag or (cfg.tags if cfg and cfg.tags else None),
            "include": include or None,
            "exclude": exclude or None,
            "auto_sync": a.auto,
            "sensitivity": "personal" if a.personal else None,
            "dry_run": not apply,
        }
        res = door.post_json("/projects/sync", body)
        if a.if_auto and not res.get("dry_run"):
            _refresh_graph_file(door, root, str(res.get("name") or ""))
    except (ValueError, OSError) as exc:
        out.fail(str(exc))
        return 2
    except DoorError as exc:
        out.fail(exc.detail or str(exc))
        return 1
    if a.json:
        print(json.dumps(res, indent=2))
        return 0
    if a.quiet:
        return 0
    where_text = res.get("remote") or str(root.resolve())
    if res.get("prefix"):
        where_text += f" · {res['prefix']}/"
    out.say(out.bold("Project") + out.dim(f"   {res['name']} · {where_text}"))
    counts = res.get("counts") or {}
    out.say(
        "  "
        + ", ".join(f"{n} {k}" for k, n in counts.items() if n)
        + (out.dim("   (a dry run: nothing sent)") if res.get("dry_run") else "")
    )
    shown = [
        p for p in res.get("plan") or [] if p["action"] not in ("unchanged", "skip")
    ]
    for p in shown[:40]:
        extra = f" (was {p['was']})" if p.get("was") else ""
        out.hint(f"  {p['action']:<9} {p['path']}{extra}")
    if len(shown) > 40:
        out.hint(f"  … and {len(shown) - 40} more")
    for why, row in (res.get("skipped") or {}).items():
        examples = ", ".join(row.get("examples") or [])
        out.hint(f"  skipped {row.get('count')}: {why}" + out.dim(f"  ({examples})"))
    for g in res.get("gone") or []:
        out.hint(f"  gone      {g.get('path')} (doc {g['doc_id']}; left as it is)")
    if res.get("dry_run"):
        out.hint("  prax sync --apply sends it")
    else:
        auto = "on" if res.get("auto_sync") else "off"
        out.hint(f"  page project-{res['name']} · session-end sync {auto} (--auto)")
    return 0
