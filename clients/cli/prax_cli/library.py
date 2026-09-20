"""What a person does with the library: find something, ask about it, put
something in, read one, follow the graph, open a page. Every command here
is one call to the door and a readable answer."""

from __future__ import annotations

import json
import sys
import textwrap
import webbrowser
from pathlib import Path
from typing import Any

from prax.client import Door

from . import out

# ------------------------------------------------------------------ search


def search(door: Door, a: Any) -> int:
    query = " ".join(a.query)
    params: dict[str, Any] = {"q": query, "limit": a.limit, "mode": a.mode}
    for key in ("kind", "domain", "doctype"):
        if getattr(a, key, None):
            params[key] = getattr(a, key)
    hits = door.get_json("/search", params)
    if a.json:
        print(json.dumps(hits, indent=2))
        return 0
    if not hits:
        out.say(f"Nothing found for {query!r}.")
        out.hint(
            "Try fewer words, or --mode fts for the exact spelling."
            " A document is only searchable once a worker has parsed it"
            " (prax inbox)."
        )
        return 1
    for i, h in enumerate(hits, 1):
        title = h.get("title") or "(untitled)"
        right = f"doc {h['doc_id']}"
        room = max(20, out.width() - len(right) - 6)
        head = f"{i:2}. " + (title if len(title) <= room else title[: room - 1] + "…")
        pad = " " * max(1, out.width() - len(head) - len(right))
        out.emit(out.bold(head) + pad + out.dim(right))
        if h.get("snippet"):
            out.snippet(h["snippet"])
        where = [h.get("kind") or "text"]
        if h.get("page"):
            where.append(f"page {h['page']}")
        if h.get("heading"):
            path = " / ".join(h["heading"])
            where.append(path if len(path) <= 44 else path[:43] + "…")
        out.hint("    " + " · ".join(where))
    out.say()
    out.hint(
        f"Read one: prax show {hits[0]['doc_id']}"
        f"   ·   in the browser: prax open {hits[0]['doc_id']}"
    )
    return 0


# --------------------------------------------------------------------- ask


_STEP_WORDS = {
    "search": "searched",
    "read": "read",
    "facts": "facts of",
    "walk": "walked",
    "similar": "like",
    "drop": "set aside",
    "answer": "enough read",
    "error": "failed",
}


def trail_line(step: dict[str, Any]) -> str:
    """One printed line for a surfing step: what it did and what it brought."""
    verb = _STEP_WORDS.get(step.get("action") or "", step.get("action") or "?")
    arg = step.get("arg") or ""
    head = f"{verb} {arg}".strip()
    result = step.get("result") or ""
    if step.get("action") in ("answer", "error"):
        head = f"{verb}: {result}" if step.get("action") == "error" else verb
        result = ""
    line = f"  {step.get('n', '?')}. {head}"
    if result:
        line += f" → {result[:90]}"
    if step.get("note"):
        line += f"\n     {step['note']}"
    return line


def ask(door: Door, a: Any) -> int:
    question = " ".join(a.question)
    body: dict[str, Any] = {"question": question, "limit": a.limit}
    if a.doctype:
        body["doctype"] = a.doctype
    if a.steps is not None:
        body["steps"] = a.steps
    if a.tokens is not None:
        body["tokens"] = a.tokens
    if not a.answer:
        body["backend"] = "none"
        result = door.post_json("/ask", body)
    elif a.json or a.steps == 0:
        result = door.post_json("/ask", body)
    else:
        # the trail as it happens, then the answer
        result = None
        for event in door.post_lines("/ask", {**body, "stream": True}):
            kind = event.get("event")
            if kind == "step":
                out.hint(trail_line(event["step"]))
            elif kind == "answering":
                out.hint(f"  writing the answer from {event.get('passages')} passages…")
            elif kind == "answer":
                result = event["result"]
            elif kind == "error":
                out.fail(event.get("detail") or "the ask failed")
                return 1
        if result is None:
            out.fail("the door closed the stream without an answer")
            return 1
        out.say()
    if a.json:
        print(json.dumps(result, indent=2))
        return 0
    passages = result.get("passages") or []
    if not passages:
        out.say(f"Nothing in the library speaks to {question!r}.")
        return 1
    if result.get("answer"):
        out.say(out.bold(question))
        out.say()
        for para in result["answer"].split("\n"):
            for line in textwrap.wrap(para, out.width()) or [""]:
                out.say(line)
        out.say()
        for c in result.get("citations") or []:
            out.hint(
                f"  [{c.get('n')}] {c.get('title') or ''}"
                f" (doc {c.get('doc_id')}, chunk {c.get('chunk_id')})"
            )
        told = [f"answered by {result.get('model')}"]
        if result.get("steps"):
            dropped = len(result.get("dropped") or [])
            told.append(
                f"{result['steps']} steps"
                + (
                    f", {dropped} passage{'s' if dropped > 1 else ''} set aside"
                    if dropped
                    else ""
                )
            )
        if result.get("seconds"):
            told.append(f"{result['seconds']} s")
        if result.get("cost_usd"):
            told.append(f"${result['cost_usd']:.4f}")
        out.say()
        out.hint(" · ".join(told))
    else:
        out.say(out.bold(f"{len(passages)} passages for: {question}"))
        out.say()
        for p in passages:
            head = f"[{p['n']}] {p.get('title') or '(untitled)'}"
            right = f"doc {p['doc_id']}"
            pad = " " * max(1, out.width() - len(head) - len(right))
            out.emit(out.bold(head) + pad + out.dim(right))
            text = " ".join((p.get("text") or "").split())
            for line in textwrap.wrap(text[:600], out.width() - 4):
                out.say("    " + line)
            out.say()
        out.hint(
            "Answer it yourself from these, or let the door's model answer:"
            f" prax ask --answer {question!r}"
        )
    if a.save:
        kept = door.post_json(
            "/ask/save",
            {"slug": a.save, "result": result, "create": "topic"},
        )
        out.say()
        out.say(f"Kept on page {a.save} (revision {kept.get('revision')}).")
    if getattr(a, "stand", False):
        options = {k: body.get(k) for k in ("steps", "tokens", "doctype", "limit")}
        stood = door.post_json("/questions", {"result": result, "options": options})
        out.say()
        out.say(
            f"Standing question {stood['slug']} (doc {stood['doc_id']}): asked again"
            " when the library learns something; prax questions shows it."
        )
    return 0


# --------------------------------------------------------------------- add


def _upload(door: Door, path: Path, a: Any) -> dict[str, Any]:
    fields: dict[str, str] = {"by": "cli"}
    if a.title:
        fields["title"] = a.title
    if a.domain:
        fields["domains"] = ",".join(a.domain)
    return door.upload(path, fields)


def _files_of(folder: Path, recursive: bool) -> list[Path]:
    walk = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(
        p
        for p in walk
        if p.is_file() and not p.name.startswith(".") and p.suffix != ".json"
    )


def add(door: Door, a: Any) -> int:
    targets: list[str] = list(a.what)
    added, pending, failed = 0, 0, 0
    for target in targets:
        if target == "-":
            text = sys.stdin.read()
            if not text.strip():
                out.warn("nothing on standard input")
                failed += 1
                continue
            r = door.post_json(
                "/ingest",
                {
                    "text": text,
                    "title": a.title or "note from the terminal",
                    "domains": a.domain or None,
                },
            )
            out.say(
                f"text  ·  doc {r['doc_id']}"
                + ("" if r["created"] else "  (had it already)")
            )
            added += 1
            continue
        if target.startswith(("http://", "https://")):
            r = door.post_json(
                "/ingest/url",
                {
                    "url": target,
                    "title": a.title,
                    "domains": a.domain or None,
                    "by": "cli",
                },
            )
            state = "indexed" if r.get("indexed") else "pending"
            out.say(f"{target}\n      doc {r['doc_id']}  ({state})")
            added += 1
            pending += 0 if r.get("indexed") else 1
            continue
        path = Path(target).expanduser()
        if not path.exists():
            out.warn(f"no such file, folder or URL: {target}")
            failed += 1
            continue
        paths = _files_of(path, a.recursive) if path.is_dir() else [path]
        if path.is_dir() and not paths:
            out.warn(f"{path} holds no files to add")
            continue
        for p in paths:
            try:
                r = _upload(door, p, a)
            except Exception as exc:  # noqa: BLE001 - one bad file is not the run
                out.warn(f"{p.name}: {exc}")
                failed += 1
                continue
            state = "indexed" if r.get("indexed") else "pending"
            note = "" if r.get("created") else "  (had it already)"
            out.say(f"{p.name}\n      doc {r['doc_id']}  ({state}){note}")
            added += 1
            pending += 0 if r.get("indexed") else 1
    if added:
        out.say()
        out.say(f"{added} added" + (f", {failed} refused" if failed else ""))
    if pending:
        out.hint(
            f"{pending} wait for a worker to read them:"
            " run one with `prax work` (or `prax work --watch` to keep it going)."
        )
    return 1 if failed and not added else 0


# -------------------------------------------------------------------- show


def show(door: Door, a: Any) -> int:
    doc = door.get_json(f"/get/{a.doc_id}", {"offset": a.offset, "max_chars": a.chars})
    if a.json:
        print(json.dumps(doc, indent=2))
        return 0
    meta = doc.get("meta") or {}
    title = doc.get("title") or "(untitled)"
    right = f"doc {doc['id']}"
    pad = " " * max(1, out.width() - len(title) - len(right))
    out.emit(out.bold(title) + pad + out.dim(right))
    facts = [doc.get("mime") or "?", meta.get("source") or "—"]
    if meta.get("domains"):
        facts.append("/".join(meta["domains"]))
    facts.append(f"{out.num(doc.get('text_len', 0))} characters")
    if meta.get("extraction", {}).get("ontology_version"):
        facts.append("read as " + meta["extraction"]["ontology_version"])
    elif doc.get("text_len"):
        facts.append("not read into the graph yet")
    out.hint(" · ".join(facts))
    if doc.get("source_url"):
        out.hint(doc["source_url"])
    out.say()
    text = doc.get("text") or ""
    if not text:
        out.say("No text yet: this document is registered but not parsed.")
        out.hint("A worker parses it: prax work")
        return 0
    for para in text.split("\n"):
        for line in textwrap.wrap(para, out.width()) or [""]:
            out.say(line)
    if doc.get("truncated"):
        nxt = doc["offset"] + len(text)
        out.say()
        out.hint(
            f"… {out.num(doc['text_len'] - nxt)} characters more:"
            f" prax show {a.doc_id} --offset {nxt}"
        )
    return 0


def open_doc(door: Door, a: Any) -> int:
    where = (
        f"{door.base_url}/doc/{a.doc_id}/original"
        if a.original
        else f"{door.base_url}/ui/#doc/{a.doc_id}"
    )
    out.say(where)
    if not webbrowser.open(where):
        out.warn("no browser opened; the address is above")
        return 1
    return 0


# ------------------------------------------------------------------- graph


def graph(door: Door, a: Any) -> int:
    edges = door.get_json("/traverse", {"entity": a.entity, "hops": a.hops})
    if a.json:
        print(json.dumps(edges, indent=2))
        return 0
    if not edges:
        out.say(f"Nothing in the graph about {a.entity!r}.")
        out.hint(
            "Names are what the extraction wrote; find one with"
            f" `prax search {a.entity}` and look at a document's entities."
        )
        return 1
    name = a.entity.lower()
    # An edge whose two ends are the same name is what a merge of two aliases
    # left behind; it says nothing about the neighbourhood, so it is counted
    # and kept out of the way.
    selves = [
        e for e in edges if (e.get("src") or "").lower() == (e.get("dst") or "").lower()
    ]
    useful = [e for e in edges if e not in selves]
    out.say(
        out.bold(a.entity)
        + out.dim(
            f" — {out.plural(len(useful), 'edge')} within {out.plural(a.hops, 'hop')}"
        )
    )
    shown = useful[: a.limit]
    rows = []
    for e in shown:
        src, dst = e.get("src") or "", e.get("dst") or ""
        if src.lower() == name:
            other, arrow, kind = dst, "→", e.get("dst_type") or ""
        elif dst.lower() == name:
            other, arrow, kind = src, "←", e.get("src_type") or ""
        else:  # two hops out: neither end is the entity itself
            other, arrow, kind = f"{src} → {dst}", " ", e.get("dst_type") or ""
        rows.append(
            [
                str(e.get("hop", "")),
                e.get("rel", ""),
                f"{arrow} {other}"[:64] + (kind and f"  ({kind})" or ""),
                (e.get("confidence") or "")[:9].lower(),
                f"doc {e['source_doc']}" if e.get("source_doc") else "",
            ]
        )
    out.table(rows, headers=["hop", "relation", "what", "how", "from"])
    if len(useful) > len(shown):
        out.hint(f"… {out.num(len(useful) - len(shown))} more (-n to see further)")
    if selves:
        out.hint(
            f"({out.plural(len(selves), 'edge')} from the name to itself,"
            " left by merged aliases)"
        )
    return 0


# ------------------------------------------------------------------- pages


def pages(door: Door, a: Any) -> int:
    if a.slug:
        page = door.get_json(f"/page/{a.slug}")
        if a.json:
            print(json.dumps(page, indent=2))
            return 0
        out.say(out.bold(page.get("title") or a.slug))
        out.hint(
            f"{page.get('kind')} · revision {page.get('revision')}"
            f" by {page.get('author')} · doc {page.get('doc_id')}"
        )
        out.say()
        for para in (page.get("text") or "").split("\n"):
            for line in textwrap.wrap(para, out.width()) or [""]:
                out.say(line)
        return 0
    listing = door.get_json("/pages")
    if a.json:
        print(json.dumps(listing, indent=2))
        return 0
    if not listing:
        out.say("No pages yet.")
        out.hint(
            "Pages are notes that live in the library; Claude writes them"
            " through the MCP tools, and the UI has an editor."
        )
        return 0
    rows = [
        [
            p.get("slug", ""),
            p.get("kind", ""),
            (p.get("title") or "")[:48],
            f"r{p.get('revision')}",
            p.get("author") or "",
            (p.get("updated_at") or "")[:10],
        ]
        for p in listing
    ]
    out.table(rows, headers=["slug", "kind", "title", "rev", "by", "changed"])
    out.say()
    out.hint(f"Read one: prax pages {listing[0]['slug']}")
    return 0
