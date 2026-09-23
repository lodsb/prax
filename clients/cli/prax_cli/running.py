"""Keeping it running: what the store holds, what is going on, the worker,
the door itself, and a check-up when something feels wrong."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

from prax.client import Door, DoorError

from . import out

# A document type in words, so the status line reads like a sentence.
TYPE_WORDS = {
    "pdf": ("PDF", "PDFs"),
    "web": ("web page", "web pages"),
    "image": ("image", "images"),
    "text": ("text file", "text files"),
    "note": ("note", "notes"),
    "page": ("wiki page", "wiki pages"),
}

# ------------------------------------------------------------------ status


def _job_line(j: dict[str, Any]) -> str:
    where = f" on {j['host']}" if j.get("host") else ""
    note = f" — {j['note']}" if j.get("note") else ""
    stale = out.paint(" (no heartbeat)", "yellow") if j.get("stale") else ""
    return f"{j.get('name')}{where}{note}{stale}"


def status(door: Door, a: Any) -> int:
    stats = door.get_json("/stats")
    jobs = door.get_json("/jobs", {"limit": 1})
    if a.json:
        print(json.dumps({"stats": stats, "jobs": jobs}, indent=2))
        return 0
    docs = stats["documents"]
    vectors = stats["vectors"]
    graph = stats["graph"]
    out.say(out.bold("prax") + out.dim(f" · {door.base_url}"))
    out.say()
    out.field(
        "Library",
        f"{out.num(docs['total'])} documents · {out.num(docs['with_text'])} with text"
        + (f" · {out.num(docs['retired'])} retired" if docs["retired"] else ""),
    )
    if docs.get("by_type"):
        out.field("", out.counts(docs["by_type"], names=TYPE_WORDS), 12)
    if docs.get("by_source"):
        out.field("", "from " + out.counts(docs["by_source"]), 12)
    chunks = stats["chunks"]
    out.field(
        "Text",
        f"{out.num(chunks['total'])} chunks ({out.counts(chunks['by_kind'])})",
    )
    embedder = vectors.get("embedder") or "no embedder"
    doc_vectors = sum((vectors.get("documents") or {}).values())
    out.field(
        "Vectors",
        f"{out.num(vectors.get('rows', 0))} chunk vectors,"
        f" {out.num(doc_vectors)} document vectors · {embedder}",
    )
    out.field(
        "Graph",
        f"{out.num(graph['edges'])} live edges · {out.num(graph['entities'])} entities"
        f" ({out.num(graph['merged'])} merged aliases)",
    )
    if graph.get("by_producer"):
        out.field("", out.counts(graph["by_producer"], limit=4) + " …", 12)
    if graph.get("by_type"):
        out.field("", out.counts(graph["by_type"], limit=5) + " …", 12)
    domains = stats["domains"]["documents"]
    onto = stats["ontology"]
    out.field(
        "Ontology",
        onto["version"]
        + (f" · {out.counts(domains)}" if domains else "")
        + (
            f" · {out.num(stats['domains']['unset'])} in every module"
            if stats["domains"]["unset"]
            else ""
        ),
    )
    out.field(
        "Review",
        out.plural(stats["review"]["open"], "open item")
        + (f" · {out.plural(stats['pages'], 'page')}" if stats["pages"] else "")
        + (
            f" · {out.plural(stats['acronyms'], 'acronym')}"
            if stats["acronyms"]
            else ""
        ),
    )
    out.field(
        "Store",
        f"{out.size(stats['store']['db_bytes'])} database"
        + (
            f" + {out.size(vectors['index']['bytes'])} vector index"
            if (vectors.get("index") or {}).get("bytes")
            else ""
        )
        + f" at {stats['store']['path']}",
    )
    money = None
    with contextlib.suppress(Exception):  # an older door has no ledger
        money = door.get_json("/spending", {"days": 30, "limit": 1})
    if money and (money["ledger"]["calls"] or any(money["budget"]["limits"].values())):
        b = money["budget"]
        spent, lim = b["spent"], b["limits"]
        parts = [
            f"{spent['day']:.2f} today"
            + (f" of {lim['daily_usd']:.2f}" if lim["daily_usd"] else ""),
            f"{spent['month']:.2f} this month"
            + (f" of {lim['monthly_usd']:.2f}" if lim["monthly_usd"] else ""),
        ]
        line = "USD " + " · ".join(parts)
        if not b["ok"]:
            line += " · " + out.paint("paid steps held", "red")
        out.field("Spending", line)
    running = jobs.get("running") or []
    if running:
        out.lines("Running", [_job_line(j) for j in running])
    else:
        out.field("Running", "nothing")
    host = jobs.get("host") or {}
    if host.get("ram_total_mb"):
        free = host["ram_free_mb"] / 1024
        total = host["ram_total_mb"] / 1024
        line = f"{host.get('name', '')} · {free:.1f} of {total:.1f} GB RAM free"
        if host.get("commit_limit_mb"):
            line += (
                f" · commit headroom {host['commit_free_mb'] / 1024:.1f}"
                f" of {host['commit_limit_mb'] / 1024:.1f} GB"
            )
        out.field("Host", line)
    return 0


# -------------------------------------------------------------------- jobs


def jobs(door: Door, a: Any) -> int:
    listing = door.get_json("/jobs", {"limit": a.limit})
    if a.json:
        print(json.dumps(listing, indent=2))
        return 0
    running = listing.get("running") or []
    if running:
        out.say(out.bold(f"Running ({len(running)})"))
        for j in running:
            progress = (
                f"{out.num(j['done'])} of {out.num(j['total'])}"
                if j.get("total")
                else (out.num(j["done"]) if j.get("done") else "")
            )
            out.say(f"  {_job_line(j)}")
            started = out.when(j.get("started_at"))
            out.hint(
                f"     started {started}"
                + (f" · {progress} done" if progress else "")
                + (f" · heartbeat {j['age']} s ago" if j.get("age") is not None else "")
            )
    else:
        out.say("Nothing running.")
        out.hint("A worker keeps captures moving: prax work --watch")
    recent = listing.get("recent") or []
    if recent:
        out.say()
        out.say(out.bold("Lately"))
        rows = [
            [
                out.when(j.get("finished_at")),
                j.get("name", ""),
                out.paint(j.get("status", ""), "red")
                if j.get("status") == "failed"
                else j.get("status", ""),
                (j.get("note") or "")[: max(20, out.width() - 40)],
            ]
            for j in recent
        ]
        out.table(rows, headers=["when", "job", "how", "what"])
    return 0


# ------------------------------------------------------------------- inbox


def inbox(door: Door, a: Any) -> int:
    view = door.get_json("/inbox", {"limit": a.limit})
    if a.json:
        print(json.dumps(view, indent=2))
        return 0
    recent = view.get("recent") or []
    waiting = [r for r in recent if not r.get("indexed")]
    unread = [r for r in recent if r.get("indexed") and not r.get("extracted")]
    out.field("Drop folder", str(view.get("inbox_dir")))
    out.hint(
        "            anything put there is taken in by the door within half a minute"
    )
    out.say()
    if not recent:
        out.say("Nothing has come in yet.")
        out.hint("Add something: prax add <file, folder or URL>")
        return 0
    out.say(out.bold(f"Latest {len(recent)}"))
    rows = []
    for r in recent:
        state = (
            out.paint("pending", "yellow")
            if not r.get("indexed")
            else ("read" if r.get("extracted") else "indexed")
        )
        rows.append(
            [
                f"doc {r['doc_id']}",
                (r.get("title") or "")[:46],
                r.get("source") or "",
                "/".join(r.get("domains") or []) or "—",
                state,
                out.when((r.get("capture") or {}).get("at"), "%d %b %H:%M"),
            ]
        )
    out.table(rows, headers=["", "title", "from", "domains", "state", "when"])
    out.say()
    if waiting:
        out.hint(
            f"{len(waiting)} wait to be parsed and {len(unread)} to be read into"
            " the graph: prax work"
        )
    elif unread:
        out.hint(f"{len(unread)} wait to be read into the graph: prax work")
    else:
        out.hint("All of these are parsed, read and embedded.")
    return 0


# -------------------------------------------------------------------- work


def work(a: Any) -> int:
    """Do the model work the door hands out: parse, titles, extract, embed."""
    try:
        from prax import inbox as inbox_mod
        from prax import worker
    except ImportError as exc:  # the thin install has no parsers or embedder
        out.fail(f"this machine cannot do the work: {exc}")
        out.hint('Install what a worker needs: pip install "prax[work]"')
        return 2
    steps = tuple(
        s
        for s in (x.strip() for x in a.steps.split(","))
        if s in worker.STEPS and not getattr(a, f"no_{s}", False)
    )
    if not steps:
        out.fail("no steps left to do", "drop one of the --no-… switches")
        return 2
    door = Door(a.door, token=a.token)
    door.get_json("/health")  # a clear error here beats one mid-pass
    folders = [Path(f) for f in (a.also or [])] or inbox_mod.browser_drop_folders()
    domains = [d.strip() for d in (a.domains or "").split(",") if d.strip()] or None
    say = None if a.quiet else (lambda t: print(t, flush=True))
    out.hint(
        f"worker {door.name} → {a.door} · steps {', '.join(steps)} · scope {a.scope}"
        + ("" if a.watch else " · one pass")
    )
    try:
        worker.watch(
            door,
            interval=a.interval,
            steps=steps,
            scope=a.scope,
            limit=a.limit,
            workers=a.workers,
            folders=folders,
            domains=domains,
            spend=bool(getattr(a, "spend", False)),
            log_=say,
            once=not a.watch,
            nightly=a.nightly if a.watch else None,
            nightly_limit=a.nightly_limit,
        )
    except KeyboardInterrupt:
        out.say("stopped")
    return 0


# ------------------------------------------------------------------- serve


def serve(a: Any) -> int:
    """Run the door on this machine."""
    try:
        import uvicorn
    except ImportError:
        out.fail("uvicorn is not installed", 'pip install "prax[serve]"')
        return 2
    from prax import config

    tls = bool(a.ssl_certfile or a.ssl_keyfile)
    if tls and not (a.ssl_certfile and a.ssl_keyfile):
        out.fail("HTTPS needs both --ssl-certfile and --ssl-keyfile")
        return 2
    scheme = "https" if tls else "http"
    out.say(f"prax is listening on {scheme}://{a.host}:{a.port}")
    out.hint(f"  the web UI:   {scheme}://{a.host}:{a.port}/ui/")
    out.hint(f"  the store:    {config.data_dir()}")
    out.hint("  stop it with ctrl-c")
    uvicorn.run(
        "prax.api:app",
        host=a.host,
        port=a.port,
        reload=a.reload,
        log_level="warning",
        ssl_certfile=a.ssl_certfile,
        ssl_keyfile=a.ssl_keyfile,
    )
    return 0


# ------------------------------------------------------------------ models


def models(a: Any) -> int:
    from prax import models as models_mod

    if a.fetch:
        from prax import fetch

        spec = models_mod.spec(a.fetch)
        if spec is None:
            out.fail(f"no model named {a.fetch!r} in prax.yaml")
            out.hint("Names in the file: " + (", ".join(models_mod.names()) or "none"))
            return 2
        if not (spec.repo and spec.file):
            out.fail(f"{a.fetch} names no repo and file to fetch")
            out.hint(
                "Add repo: and file: to that models entry (prax.example.yaml shows it)"
            )
            return 2
        out.say(f"{spec.repo}/{spec.file}")
        last = [0.0]

        def progress(done: int, total: int | None) -> None:
            import time

            now = time.monotonic()
            if now - last[0] < 0.5 and (total is None or done < total):
                return
            last[0] = now
            print(
                f"\r  {fetch.human(done)} of {fetch.human(total)}",
                end="",
                flush=True,
            )

        path = fetch.model_file(spec.repo, spec.file, progress=progress)
        out.say(f"\n{path}")
        out.hint(
            f"Serve it: run: {{llama-server: {{model: {a.fetch}}}}} in prax.yaml,"
            " then prax up (docs/howto.md 3h)"
        )
        return 0
    rows = []
    for step in models_mod.STEPS:
        try:
            spec = models_mod.resolve(step)
        except Exception as exc:  # noqa: BLE001 - a bad file is what we report
            rows.append([step, out.paint("misconfigured", "red"), str(exc)[:50]])
            continue
        if spec is None:
            rows.append([step, "—", "nobody: the caller's own model does it"])
            continue
        where = spec.base_url or ("the Claude API" if spec.kind == "claude" else "")
        rows.append([step, spec.name, f"{spec.kind} · {where}".rstrip(" ·")])
    out.table(rows, headers=["step", "model", "where"])
    out.say()
    out.hint(f"From {models_mod.config_path()}")
    return 0


# -------------------------------------------------------------------- heal


def _example_line(name: str, row: dict[str, Any]) -> str:
    """One finding in a few words, whatever kind of row it is."""
    if "duplicate_of" in row:  # a twin document and its keeper
        title = (row.get("title") or f"doc {row.get('id')}")[:50]
        twin = out.plural(row.get("edges", 0), "edge")
        keeper = out.plural(row.get("keeper_edges", 0), "edge")
        return (
            f"{title!r} · doc {row.get('id')} ({twin}) → doc {row.get('duplicate_of')}"
            f" ({keeper}), {row.get('similarity', 0):.2f} alike"
        )
    if "edges" in row:  # an entity and what it carries
        carries = out.plural(row["edges"], "edge")
        said = f"{row.get('name')!r} ({row.get('type')}) · {carries}"
        if row.get("cleaned"):
            said += f" → {row['cleaned']!r}"
        return said
    if "read_by" in row:  # an extraction the text was read out from under
        title = (row.get("title") or f"doc {row.get('id')}")[:60]
        return (
            f"{title!r} · extracted {str(row.get('extracted_at') or '')[:10]},"
            f" read again by {row.get('read_by')} {str(row.get('read_at') or '')[:10]}"
        )
    if "why" in row:  # an original that is not a document, and why
        title = (row.get("title") or f"doc {row.get('id')}")[:50]
        return f"{title!r} · doc {row.get('id')} · {row['why']}"
    if "pages" in row and "per_page" in row:  # a thin text
        title = (row.get("title") or f"doc {row.get('id')}")[:50]
        return (
            f"{title!r} · doc {row.get('id')} · {out.num(row['pages'])} pages,"
            f" {row['per_page']} bytes of text a page"
        )
    if "rel" in row:  # an edge or a review item
        parts = [str(row.get("rel"))]
        if row.get("name"):
            parts.append(str(row["name"]))
        if row.get("src") and row.get("dst"):
            parts.append(f"{row['src']} → {row['dst']}")
        if row.get("title"):
            parts.append(str(row["title"])[:40])
        if row.get("source_doc"):
            parts.append(f"doc {row['source_doc']}")
        return " · ".join(parts)
    if "mime" in row:
        return (
            f"{row['mime']} · {row.get('documents')} documents"
            f" · doc {row.get('first_id')}"
        )
    if "chunks" in row:
        return f"{out.num(row['chunks'])} chunks · {row.get('model')}"
    if "host" in row:  # a job
        return f"{row.get('name')} on {row.get('host')} · since {row.get('updated_at')}"
    return str(row)


def reread(door: Door, a: Any) -> int:
    """A reading request over a selection, through the door."""
    body = {
        "extractor": a.extractor,
        "mode": a.mode,
        "ids": a.ids,
        "mime": a.mime,
        "text_source": a.text_source,
        "title": a.title,
        "unreadable": a.unreadable,
        "thin": a.thin,
        "doctype": a.doctype,
        "unpolished": a.unpolished,
        "read_figures": a.read_figures,
        "unread_figures": a.unread_figures,
        "read_formulas": a.read_formulas,
        "unread_formulas": a.unread_formulas,
        "maths": a.maths,
        "limit": a.limit,
        "dry_run": a.dry_run,
    }
    r = door.post_json("/readings/bulk", body)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    what = a.extractor + (f" ({a.mode})" if a.mode else "")
    if r["dry_run"]:
        out.say(f"{out.num(r['selected'])} documents would be asked for {what}")
        return 0
    out.say(
        f"asked for {what} on {out.num(r['requested'])} of {r['selected']} documents"
        + (
            f"; {r['skipped']} skipped (the extractor does not read them)"
            if r["skipped"]
            else ""
        )
    )
    out.hint("  a worker takes them before the pending captures; Jobs shows what waits")
    if a.wait:
        return wait_for_readings(
            door, extractor=a.extractor, timeout=a.timeout, every=a.every
        )
    return 0


def readings(door: Door, a: Any) -> int:
    """The reading queue: what waits, per extractor, and the recent
    outcomes; ``--wait`` blocks until it has drained."""
    if a.wait:
        return wait_for_readings(
            door, extractor=a.extractor, timeout=a.timeout, every=a.every
        )
    view = door.get_json("/readings", {"limit": a.limit})
    if a.json:
        print(json.dumps(view, indent=2))
        return 0
    by = view.get("by_extractor") or {}
    if a.extractor:
        by = {k: v for k, v in by.items() if k == a.extractor}
    if by:
        out.say(out.bold(f"Waiting ({out.num(sum(by.values()))})"))
        for name, n in by.items():
            out.say(f"  {out.num(n):>7}  {name}")
        out.hint("  a worker takes them before the pending captures; --wait blocks")
    else:
        out.say("Nothing waiting.")
    recent = [
        r
        for r in view.get("recent") or []
        if not a.extractor or r.get("extractor") == a.extractor
    ]
    if recent:
        out.say()
        out.say(out.bold("Lately"))
        rows = [
            [
                out.when(r.get("finished_at") or r.get("at")),
                str(r.get("doc_id")),
                r.get("extractor") or "",
                out.paint(r.get("state", ""), "red")
                if r.get("state") == "error"
                else r.get("state", ""),
                (r.get("error") or r.get("outcome") or r.get("title") or "")[
                    : max(20, out.width() - 48)
                ],
            ]
            for r in recent
        ]
        out.table(rows, headers=["when", "doc", "reading", "how", "what"])
    return 0


def wait_for_readings(
    door: Door,
    *,
    extractor: str | None = None,
    timeout: float | None = None,
    every: float = 30,
) -> int:
    """Block until no reading request waits (for ``extractor``, or at
    all), a line whenever the count moves; 0 when drained, 2 on the
    timeout (minutes). What a script that swaps the card to marker and
    back does between its steps — the same line on every platform."""
    started = time.monotonic()
    last: int | None = None
    what = extractor or "readings"
    while True:
        view = door.get_json("/readings", {"limit": 1})
        by = view.get("by_extractor") or {}
        n = int(by.get(extractor, 0)) if extractor else int(view.get("waiting") or 0)
        if n == 0:
            out.say(f"{what}: nothing waiting")
            return 0
        if n != last:
            out.say(
                f"{time.strftime('%H:%M')} {out.num(n)} {what} waiting"
                + (f" ({int((time.monotonic() - started) / 60)} min)" if last else "")
            )
            last = n
        if timeout and time.monotonic() - started > timeout * 60:
            out.fail(f"{what}: {out.num(n)} still waiting after {timeout:g} min")
            return 2
        time.sleep(every)


def heal(door: Door, a: Any) -> int:
    """What is wrong with the store, and (with --apply) the repair."""
    params: dict[str, Any] = {}
    if a.check:
        params["check"] = ",".join(a.check)
    found = door.get_json("/heal", params)
    if a.json and not a.apply:
        print(json.dumps(found, indent=2))
        return 0
    ailments = found["ailments"]
    hurt = [x for x in ailments if x["count"]]
    clear = [x["name"] for x in ailments if not x["count"]]
    out.say(
        out.bold("The store's health")
        + out.dim(f"   {len(hurt)} of {len(ailments)} ailments found")
    )
    out.say()
    for x in hurt:
        count = out.num(x["count"]) + ("+" if x["capped"] else "")
        out.say(f"{out.paint(x['name'], 'yellow')}   {out.bold(count)}")
        for line in textwrap.wrap(x["what"], max(30, out.width() - 4)):
            out.hint("    " + line)
        for row in x["examples"]:
            out.hint("      · " + _example_line(x["name"], row))
        head = "    repair: " if x["repairable"] else "    by hand: "
        for i, line in enumerate(
            textwrap.wrap(x["fix"], max(30, out.width() - len(head)))
        ):
            out.hint((head if i == 0 else " " * len(head)) + line)
        out.say()
    if clear:
        head = "clear: "
        for i, line in enumerate(
            textwrap.wrap(", ".join(clear), max(30, out.width() - len(head)))
        ):
            out.hint((head if i == 0 else " " * len(head)) + line)
        out.say()
    if not hurt:
        out.say("Nothing to repair.")
        return 0
    if not a.apply:
        which = f" --check {','.join(a.check)}" if a.check else ""
        out.hint(f"Nothing was changed. Repair it with: prax heal --apply{which}")
        return 0
    repairable = [x["name"] for x in hurt if x["repairable"]]
    if not repairable:
        out.say("Nothing here can be repaired from the store; see above.")
        return 0
    done = door.post_json("/heal", {"checks": a.check or repairable})
    if a.json:
        print(json.dumps(done, indent=2))
        return 0
    out.say(out.bold("Repaired"))
    for name, result in done.items():
        if isinstance(result, dict):
            out.say(
                f"  {name}: {out.num(result['repaired'])} of {out.num(result['found'])}"
            )
        else:
            out.say(f"  {name}: {result}")
    out.hint(
        "Edges were ended, not deleted: they stay as history with a"
        " valid_to (invariant 8). Run `prax heal` again to see what is left."
    )
    return 0


# ------------------------------------------------------------------ doctor


def _mark(ok: bool | None) -> str:
    if ok is None:
        return out.paint(" ?? ", "yellow")
    return out.paint(" ok ", "green") if ok else out.paint(" -- ", "red")


def doctor(door: Door, a: Any) -> int:
    """Is everything a person needs actually there?"""
    rows: list[tuple[bool | None, str, str]] = []
    reachable = True
    try:
        health = door.get_json("/health")
        rows.append((True, "the door", f"{door.base_url} · {health.get('auth')}"))
    except Exception as exc:  # noqa: BLE001 - that is the finding
        reachable = False
        rows.append((False, "the door", f"{door.base_url}: {exc}"))
    if reachable:
        try:
            stats = door.get_json("/stats")
            rows.append(
                (
                    True,
                    "the store",
                    (
                        f"{out.num(stats['documents']['total'])} documents ·"
                        f" {out.size(stats['store']['db_bytes'])} ·"
                        f" {stats['store']['path']}"
                    ),
                )
            )
            vectors = stats["vectors"]
            rows.append(
                (
                    bool(vectors.get("available")),
                    "vectors",
                    f"{out.num(vectors.get('rows', 0))} vectors ·"
                    + f" {vectors.get('embedder') or 'no embedder'}"
                    + (
                        ""
                        if vectors.get("available")
                        else " (usearch missing: search is keywords only)"
                    ),
                )
            )
            rows.append((True, "ontology", stats["ontology"]["version"]))
        except Exception as exc:  # noqa: BLE001
            rows.append((False, "the store", str(exc)))
    try:
        from prax import models as models_mod

        steps = []
        for step in models_mod.STEPS:
            try:
                spec = models_mod.resolve(step)
            except Exception:  # noqa: BLE001
                steps.append(f"{step}=bad")
                continue
            steps.append(f"{step}={spec.name if spec else 'none'}")
        rows.append((True, "models", " · ".join(steps)))
    except Exception as exc:  # noqa: BLE001
        rows.append((None, "models", str(exc)))
    have = []
    missing = []
    for name, what in (
        ("usearch", "vector index"),
        ("onnxruntime", "embeddings"),
        ("tokenizers", "embeddings"),
        ("pymupdf4llm", "PDF text"),
        ("trafilatura", "web pages"),
        ("magika", "code detection"),
        ("anthropic", "the Claude API"),
        ("mcp", "the Claude Code tools"),
    ):
        try:
            __import__(name)
            have.append(name)
        except ImportError:
            missing.append(f"{name} ({what})")
    rows.append((not missing, "packages", ", ".join(have) or "none"))
    if missing:
        rows.append((None, "", "missing: " + ", ".join(missing)))
    rows.append(
        (
            bool(os.environ.get("ANTHROPIC_API_KEY")),
            "Claude API key",
            "ANTHROPIC_API_KEY is set"
            if os.environ.get("ANTHROPIC_API_KEY")
            else "unset: the Claude steps would refuse (the local ones do not care)",
        )
    )
    try:
        from prax import hostinfo

        mem = hostinfo.memory()
        if mem.get("ram_total_mb"):
            tight = (mem.get("commit_free_mb") or 0) < 4096
            rows.append(
                (
                    not tight,
                    "this machine",
                    f"{mem['ram_free_mb'] / 1024:.1f} of"
                    + f" {mem['ram_total_mb'] / 1024:.1f} GB RAM free"
                    + (
                        f" · commit headroom {mem['commit_free_mb'] / 1024:.1f} GB"
                        if mem.get("commit_free_mb")
                        else ""
                    ),
                )
            )
    except Exception:  # noqa: BLE001, S110 - a missing number is not a finding
        pass  # nothing to say about memory on a host that will not tell us
    for ok, label, text in rows:
        out.emit(f"[{_mark(ok)}] {out.bold(label.ljust(14))} {text}")
    if not reachable:
        out.say()
        out.hint("Start a door here with `prax serve`, or name another with --door.")
        return 1
    return 0


def overview(door: Door) -> int:
    """`prax` with nothing after it: where the library is and what to type."""
    out.say(out.bold("prax") + out.dim(f" — your library at {door.base_url}"))
    out.say()
    try:
        stats = door.get_json("/stats")
        jobs = door.get_json("/jobs", {"limit": 1})
        running = len(jobs.get("running") or [])
        out.say(
            f"  {out.num(stats['documents']['total'])} documents ·"
            f" {out.num(stats['graph']['edges'])} edges ·"
            f" {running} job{'' if running == 1 else 's'} running"
            + out.dim("        (prax status for the rest)")
        )
    except DoorError as exc:  # a door, but not one that knows this call
        out.say(f"  {out.paint('the door answered ' + str(exc.status), 'yellow')}")
        out.hint(
            "  It may be running older code than this command: restart it"
            " (prax serve), or point at another with --door."
        )
    except Exception as exc:  # noqa: BLE001 - the overview still helps
        out.say(f"  {out.paint('no door there', 'yellow')} ({type(exc).__name__})")
        out.hint("  Start one here: prax serve   ·   Elsewhere: prax --door <url> …")
    out.say()
    for label, examples in (
        (
            "Find things",
            [
                "prax search feedback delay networks",
                "prax ask why do FDNs colour the tail",
            ],
        ),
        (
            "Put things in",
            ["prax add paper.pdf", "prax add https://example.org/article"],
        ),
        ("Look at one", ["prax show 9671", "prax open 9671"]),
        ("Keep it going", ["prax work --watch", "prax jobs", "prax doctor"]),
    ):
        out.lines(label, examples, 16)
        out.say()
    out.hint("prax --help for everything (graph, pages, inbox, models, serve).")
    return 0


def print_version() -> int:
    try:
        import importlib.metadata as meta

        version = meta.version("prax")
    except Exception:  # noqa: BLE001
        version = "unknown"
    print(f"prax {version} (python {sys.version.split()[0]})")
    return 0


# -------------------------------------------------------------- job runs


def follow_job(door: Door, job_id: int, *, quiet: bool, what: str = "the job") -> int:
    """Poll a job the door started until it is over, printing each new
    note; the exit code says how it ended."""
    import httpx

    last = ""
    misses = 0
    while True:
        try:
            row = door.get_json(f"/jobs/{job_id}")
        except httpx.HTTPError as exc:
            # a poll the door dropped (a keep-alive connection it closed
            # under load, a restart between two polls): the job outlives it
            misses += 1
            if misses > 30:
                raise
            if not quiet and misses == 1:
                out.hint(
                    f"  (the door did not answer a poll: {type(exc).__name__}; waiting)"
                )
            time.sleep(2)
            continue
        misses = 0
        note = row.get("note") or ""
        if row["status"] != "running":
            break
        if note != last and not quiet:
            out.hint("  " + note)
            last = note
        time.sleep(2)
    if quiet:
        print(json.dumps(row, indent=2))
        return 0 if row["status"] == "done" else 1
    if row["status"] == "done":
        out.say("  " + note.removeprefix("done: "))
        return 0
    out.fail(f"{what} {row['status']}: {note}")
    return 1


# ---------------------------------------------------------------- maintain


def maintain(door: Door, a: Any) -> int:
    """Ask the door for the maintenance pass and follow the job."""
    only = [p.strip() for p in (a.only or "").split(",") if p.strip()] or None
    if getattr(a, "rechunk", False):
        only = (only or []) + ["rechunk"]
    started = door.post_json("/maintain", {"only": only})
    if not a.json:
        out.say(
            out.bold("Maintenance")
            + out.dim(f"   {', '.join(started['passes'])} · job {started['job']}")
        )
    return follow_job(door, started["job"], quiet=a.json, what="the pass")


# --------------------------------------------------------------- questions


def questions(door: Door, a: Any) -> int:
    """The standing questions and what is new for each; --ask runs the
    re-asks (and --briefing the day's page) as a job and follows it."""
    if a.ask is not None or a.briefing:
        body: dict[str, Any] = {"force": a.force, "briefing": a.briefing}
        if a.ask:
            body["slug"] = a.ask
        if getattr(a, "release", False):
            body["release"] = True
            body["force"] = True  # a release is a re-ask
        started = door.post_json("/questions/run", body)
        if not a.json:
            out.say(
                out.bold("Questions")
                + out.dim(
                    f"   {'question ' + a.ask if a.ask else 'every question'}"
                    + (" · the briefing" if a.briefing else "")
                    + f" · job {started['job']}"
                )
            )
        return follow_job(door, started["job"], quiet=a.json, what="the questions")
    rows = door.get_json("/questions")
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        out.say("No standing questions. Start one: prax ask --answer --stand …")
        return 0
    due = sum(1 for r in rows if r["due"])
    out.say(
        out.bold("Standing questions")
        + out.dim(f"   {len(rows)}, {due} with something new")
    )
    out.say()
    for r in rows:
        if r.get("held"):
            mark = out.paint("held", "yellow")
        elif r["due"]:
            mark = out.paint("new", "yellow")
        else:
            mark = out.dim("settled")
        out.say(f"{mark}   {out.bold(r['question'] or r['slug'])}")
        when = (r.get("asked_at") or "")[:16].replace("T", " ")
        out.hint(
            f"    {r['slug']} · doc {r['doc_id']} · revision {r['revision']}"
            + (f" · in {r['title']}" if r.get("block") else "")
            + (
                f" · asked {when} by {r.get('model') or '?'}"
                if r.get("asked_at")
                else " · not yet asked"
            )
            + (
                f" · moved {r['history']} time{'s' if r['history'] != 1 else ''}"
                if r.get("history")
                else ""
            )
        )
        if r.get("held"):
            out.hint(
                "      · edited by hand; the door left it. prax questions --ask"
                f" {r['slug']} --release answers it anew"
            )
        for n in r.get("new") or []:
            out.hint(f"      · {n['title']} — {n['why']}")
        if r.get("reread"):
            out.hint(f"      · {len(r['reread'])} source(s) read again")
    if due:
        out.say()
        out.hint("Ask them again: prax questions --ask")
    return 0


# ----------------------------------------------------------------- resolve


def resolve(door: Door, a: Any) -> int:
    """Entity resolution: the plan, and with --apply the sure merges."""
    body = {
        "apply": a.apply,
        "type": a.type,
        "twins": a.twins,
        "subtypes": getattr(a, "subtypes", False),
        "likely": not a.no_likely,
        "show": a.show,
    }
    r = door.post_json("/graph/resolve", body)
    if a.json and not a.apply:
        print(json.dumps(r, indent=2))
        return 0
    plan = r["plan"]
    if not a.json:
        out.say(
            out.bold("Entity resolution")
            + out.dim(
                f"   {plan['sure']['count']} sure,"
                f" {plan.get('subtypes', {}).get('count', 0)} subtypes,"
                f" {plan['twins']['count']} twins,"
                f" {plan['likely']['count']} likely"
            )
        )
        for tier in ("sure", "subtypes", "twins", "likely"):
            if tier not in plan:
                continue
            for c in plan[tier]["examples"]:
                pair = f"{c['drop']!r} -> {c['keep']!r}"
                out.hint(f"  {tier:6} {c['score']:.2f} [{c['type']}] {pair}")
            more = plan[tier]["count"] - len(plan[tier]["examples"])
            if more > 0:
                out.hint(f"  … {more} more {tier}")
        computed = plan["likely"].get("computed") or {}
        if computed:
            when = ", ".join(
                f"{t} {out.when(at)}" for t, at in sorted(computed.items())
            )
            out.hint(f"  likely pairs computed by a worker: {when}")
        elif not a.no_likely:
            out.hint(
                "  no likely pairs yet: a worker's resolve step computes them"
                " (prax work --steps resolve)"
            )
    if not a.apply:
        if not a.json:
            out.hint(
                "  --apply merges the sure ones"
                + (" and the subtypes" if getattr(a, "subtypes", False) else "")
                + (" and the twins" if a.twins else "")
            )
        return 0
    return follow_job(door, r["job"], quiet=a.json, what="the resolution")


# ------------------------------------------------------------------ backup


def backup(door: Door, a: Any) -> int:
    """Ask the door to copy its store to a directory on its host, and
    follow the job until it is done."""
    started = door.post_json(
        "/backup", {"dest": a.dest, "archive": not getattr(a, "no_archive", False)}
    )
    job_id = started["job"]
    if not a.json:
        out.say(out.bold("Backup") + out.dim(f"   to {started['dest']} · job {job_id}"))
    last = ""
    while True:
        row = door.get_json(f"/jobs/{job_id}")
        note = row.get("note") or ""
        if row["status"] != "running":
            break
        if note != last and not a.json:
            out.hint("  " + note)
            last = note
        time.sleep(2)
    if a.json:
        print(json.dumps(row, indent=2))
        return 0 if row["status"] == "done" else 1
    if row["status"] == "done":
        out.say("  " + note.removeprefix("done: "))
        out.hint(
            "  the copy is a store: PRAX_DATA_DIR pointed at it opens it;"
            " prax heal there finds any vector the delta missed"
        )
        return 0
    out.fail(f"the backup {row['status']}: {note}")
    return 1
