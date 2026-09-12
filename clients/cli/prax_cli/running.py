"""Keeping it running: what the store holds, what is going on, the worker,
the door itself, and a check-up when something feels wrong."""

from __future__ import annotations

import json
import os
import sys
import textwrap
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
            log_=say,
            once=not a.watch,
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

    out.say(f"prax is listening on http://{a.host}:{a.port}")
    out.hint(f"  the web UI:   http://{a.host}:{a.port}/ui/")
    out.hint(f"  the store:    {config.data_dir()}")
    out.hint("  stop it with ctrl-c")
    uvicorn.run(
        "prax.api:app", host=a.host, port=a.port, reload=a.reload, log_level="warning"
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
        out.hint(f"Serve it: scripts/llama_server.ps1 -Model {path} -Slots 3")
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
    if "edges" in row:  # an entity and what it carries
        carries = out.plural(row["edges"], "edge")
        said = f"{row.get('name')!r} ({row.get('type')}) · {carries}"
        if row.get("cleaned"):
            said += f" → {row['cleaned']!r}"
        return said
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
