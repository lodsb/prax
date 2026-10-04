"""Syncing a project's written knowledge: the door's half of
``POST /projects/sync`` (stage AL, step 3).

The client (``prax.client.project_files``: the MCP tool, the CLI, the
plugin's hook) reads a working copy's tracked documents where they are and
sends them in one call. Here they are planned and, unless it is a dry run,
applied:

- each path is ``add``, ``refresh`` (its bytes changed), ``unchanged``,
  ``moved`` (the same bytes under another path of the project) or ``skip``
  with why, and a document of the project whose path no longer comes is
  ``gone``: reported, never retired by a sync;
- a document is keyed by the canonical git remote and its path in the
  repository (``github.com/a/b:docs/x.md``), so a checkout in another
  folder or on another machine finds the same documents. A folder outside
  git keys by the project's name (``synth/docs/x.md``), as the importer
  did, and a document that importer wrote is adopted under the new key;
- the manifest (``store.save_project``) is kept in prax, not in the
  repository, and ``auto_sync`` is set only when asked;
- the project page ``project-<name>`` is made once, and every synced
  document is a member of it (``part_of``), without the promote flag a
  paper added by a person gets;
- what the notes say about each other becomes ``links_to`` edges (AL step
  4): a Markdown link, a backticked path or a bare ``docs/x.md`` in one
  document that matches another document of the project exactly
  (``prax.text.paths``), the words as written for evidence, producer
  ``sync`` and run ``links:<name>``. Each sync keeps them in step: a link
  that went is ended, and a path that matches nothing yet is not kept
  anywhere, so the next sync matches it again.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from prax import client, store
from prax.text import paths

PLAN_SHOWN = 400  # plan rows returned; the counts are always whole
_HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.MULTILINE)
SETTINGS = ("domains", "tags", "include", "exclude")


def doc_key(name: str, remote: str | None, repo_path: str, rel: str) -> str:
    """What a synced document is found by: the remote and the path in the
    repository, or without a remote the project's name and the path."""
    return f"{remote}:{repo_path}" if remote else f"{name}/{rel}"


def _title(rel: str, text: str, name: str) -> str:
    """The first heading of a Markdown file, else its path; with the
    project's name, as the importer wrote it."""
    m = _HEADING.search(text) if rel.lower().endswith((".md", ".markdown")) else None
    return f"{m.group(1).strip() if m else rel} ({name})"


def _words(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value if str(v).strip()]


def _page_text(name: str, remote: str | None, prefix: str) -> str:
    where = f"`{remote}`" if remote else "a folder outside git"
    if prefix:
        where += f", folder `{prefix}`"
    return (
        f"# {name}\n\n"
        f"The written knowledge of {where}, synced by prax: its documents"
        f" carry the tag `project:{name}` and are members of this page.\n\n"
        "## Summary\n\nNot written yet: the project's agent writes it here.\n"
    )


def sync(con: sqlite3.Connection, req: dict[str, Any]) -> dict[str, Any]:
    """Plan a project's files and, unless ``dry_run``, apply the plan. The
    request is ``prax.client.project_files``' answer with the settings
    beside it (``name``, ``domains``, ``tags``, ``include``, ``exclude``,
    ``auto_sync``, ``dry_run``). Unknown domains and a missing text are a
    ValueError, raised before anything is written."""
    remote = req.get("remote") or None
    prefix = str(req.get("prefix") or "").strip("/")
    dry_run = bool(req.get("dry_run", True))
    files: list[dict[str, Any]] = list(req.get("files") or [])
    known = store.project_at(con, remote, prefix)
    name = str(
        req.get("name")
        or (known or {}).get("name")
        or req.get("root_name")
        or (remote.rsplit("/", 1)[-1] if remote else "")
    ).strip()
    if not name:
        raise ValueError("a project needs a name")
    held = store.project_named(con, name) or known or {}
    stored = held.get("settings") or {}
    settings = {
        k: _words(req[k]) if req.get(k) is not None else _words(stored.get(k))
        for k in SETTINGS
    }
    if not dry_run:
        from prax.graph import ontology

        modules = set(ontology.current().modules)
        unknown = [d for d in settings["domains"] if d not in modules]
        if unknown:
            raise ValueError(f"no ontology module {', '.join(unknown)}")
    rows: list[dict[str, Any]] = []
    for f in files:
        rel = str(f.get("path") or "").strip("/")
        if not rel:
            continue
        repo_path = f"{prefix}/{rel}" if prefix else rel
        why = client.project_skip(
            rel,
            int(f.get("bytes") or 0),
            include=settings["include"],
            exclude=settings["exclude"],
        )
        rows.append(
            {
                "path": rel,
                "repo_path": repo_path,
                "key": doc_key(name, remote, repo_path, rel),
                "legacy": f"{name}/{rel}",
                "version": str(f.get("sha256") or "")[:16],
                "file": f,
                "why": why,
            }
        )
    have = store.project_documents(
        con, name, [r["key"] for r in rows] + [r["legacy"] for r in rows]
    )
    by_version = {v["version"]: (k, v) for k, v in have.items() if v.get("version")}
    claimed: set[str] = set()
    for r in rows:
        if r["why"]:
            r["action"] = "skip"
            continue
        found = r["key"] if r["key"] in have else None
        if found is None and r["legacy"] in have:
            found = r["legacy"]
        if found is not None:
            claimed.add(found)
            old = have[found]
            r["doc_id"] = old["doc_id"]
            r["old_key"] = found
            r["action"] = "unchanged" if old["version"] == r["version"] else "refresh"
            continue
        moved = by_version.get(r["version"]) if r["version"] else None
        if moved is not None and moved[0] not in claimed:
            claimed.add(moved[0])
            r["doc_id"] = moved[1]["doc_id"]
            r["old_key"] = moved[0]
            r["was"] = moved[1].get("path")
            r["action"] = "moved"
            continue
        r["action"] = "add"
    gone = [
        {"path": v.get("path"), "doc_id": v["doc_id"]}
        for k, v in have.items()
        if k not in claimed
    ]
    if not dry_run:
        missing = [
            r["path"]
            for r in rows
            if r["action"] in ("add", "refresh") and not r["file"].get("text")
        ]
        if missing:
            raise ValueError(f"no text sent for {', '.join(missing[:5])}")
        _apply(con, name, remote, prefix, settings, rows)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    counts["gone"] = len(gone)
    skipped_here = sum(1 for r in rows if r["action"] == "skip")
    for row in (req.get("skipped") or {}).values():
        counts["skip"] = counts.get("skip", 0) + int(row.get("count") or 0)
    out: dict[str, Any] = {
        "name": name,
        "remote": remote,
        "prefix": prefix,
        "dry_run": dry_run,
        "settings": settings,
        "counts": counts,
        "plan": [
            {
                k: r[k]
                for k in ("path", "action", "why", "doc_id", "was")
                if r.get(k) is not None
            }
            for r in rows
            if r["action"] != "unchanged" or len(rows) <= PLAN_SHOWN
        ][:PLAN_SHOWN],
        "skipped": req.get("skipped") or {},
        "gone": gone,
        "page": f"project-{store.slugify(name)}",
    }
    if skipped_here:
        out["skipped_by_door"] = skipped_here
    if not dry_run:
        manifest = store.save_project(
            con,
            name,
            remote=remote,
            prefix=prefix,
            settings=settings,
            auto_sync=req.get("auto_sync"),
        )
        links = _links(con, name, prefix)
        out["links"] = links
        store.note_project_sync(
            con, name, {**counts, "links": links["added"] + links["kept"]}
        )
        out["auto_sync"] = manifest["auto_sync"]
    return out


LINK_PRODUCER = "sync"
LINK_REL = "links_to"
EVIDENCE_CHARS = 300


def _links(con: sqlite3.Connection, name: str, prefix: str) -> dict[str, int]:
    """Keep the project's ``links_to`` edges in step with its texts: add
    the links that are new, end the ones that went. Returns the counts,
    ``unmatched`` being the references to paths no document of the
    project has (yet)."""
    run = f"links:{name}"
    have = store.project_documents(con, name)
    by_path = {str(v["path"]): int(v["doc_id"]) for v in have.values() if v.get("path")}
    want: dict[tuple[int, int], str] = {}
    unmatched = 0
    for path, doc_id in by_path.items():
        doc = store.get_document(con, doc_id)
        for candidates, words in paths.references(
            str(doc.get("text") or "") if doc else "", path, prefix
        ):
            hit = next((by_path[c] for c in candidates if c in by_path), None)
            if hit is None:
                unmatched += 1
            elif hit != doc_id:
                want.setdefault((doc_id, hit), words)
    existing = {
        (int(e["source_doc"]), str(e["dst"])): int(e["id"])
        for e in store.run_edges(con, producer=LINK_PRODUCER, run=run)
        if e["rel"] == LINK_REL and e["source_doc"] is not None
    }
    nodes: dict[int, tuple[str, str]] = {}

    def node(doc_id: int) -> tuple[str, str]:
        if doc_id not in nodes:
            nodes[doc_id] = store.document_node(con, doc_id)
        return nodes[doc_id]

    added = kept = 0
    held: set[tuple[int, str]] = set()
    for (src_doc, dst_doc), words in want.items():
        try:
            src, src_type = node(src_doc)
            dst, dst_type = node(dst_doc)
        except KeyError:
            continue  # a document without a title is no node
        key = (src_doc, dst)
        if key in held:
            continue
        held.add(key)
        if key in existing:
            kept += 1
            continue
        store.link(
            con,
            store.Edge(src, src_type, LINK_REL, dst, dst_type),
            source_doc=src_doc,
            evidence=words[:EVIDENCE_CHARS],
            producer=LINK_PRODUCER,
            run=run,
        )
        added += 1
    ended = 0
    for key, edge_id in existing.items():
        if key not in held:
            store.invalidate_edge(con, edge_id)
            ended += 1
    return {"added": added, "kept": kept, "ended": ended, "unmatched": unmatched}


def _apply(
    con: sqlite3.Connection,
    name: str,
    remote: str | None,
    prefix: str,
    settings: dict[str, list[str]],
    rows: list[dict[str, Any]],
) -> None:
    slug = f"project-{store.slugify(name)}"
    if store.get_page(con, slug) is None:
        store.write_page(
            con,
            slug,
            _page_text(name, remote, prefix),
            title=name,
            kind="project",
            author="agent",
            note="made by the project's first sync",
        )
    tags = [f"project:{name}", *settings["tags"]]
    for r in rows:
        action = r["action"]
        f = r["file"]
        own = {
            "key": r["key"],
            "name": name,
            "path": r["repo_path"],
            "remote": remote,
            "version": r["version"],
            "sha256": f.get("sha256"),
            "bytes": f.get("bytes"),
            "modified": f.get("modified"),
        }
        if action in ("unchanged", "moved"):
            if r.get("old_key") != r["key"] or action == "moved":
                meta = store.get_meta(con, r["doc_id"])
                meta[store.PROJECT_SOURCE] = {
                    **(meta.get(store.PROJECT_SOURCE) or {}),
                    **own,
                }
                store.set_meta(con, r["doc_id"], meta)
            store.add_to_project(con, slug, r["doc_id"], promote=False)
            continue
        if action not in ("add", "refresh"):
            continue
        got = store.ingest_text(
            con,
            str(f["text"]),
            title=_title(r["path"], str(f["text"]), name),
            meta={
                "source": store.PROJECT_SOURCE,
                store.PROJECT_SOURCE: own,
                "tags": tags,
            },
        )
        doc_id = int(got["doc_id"])
        if not got.get("created", True):
            # these bytes are a document already: someone else's capture of
            # the same file, or this project's own under a key it lost
            meta = store.get_meta(con, doc_id)
            if meta.get("source") == store.PROJECT_SOURCE:
                meta[store.PROJECT_SOURCE] = {
                    **(meta.get(store.PROJECT_SOURCE) or {}),
                    **own,
                }
                meta["tags"] = sorted({*(meta.get("tags") or []), *tags})
                store.set_meta(con, doc_id, meta)
            r["action"] = "same"
        for d in settings["domains"]:
            store.add_domain(con, doc_id, d)
        if action == "refresh" and r.get("doc_id") and r["doc_id"] != doc_id:
            store.retire_document(
                con,
                r["doc_id"],
                reason=f"replaced by a newer sync of project {name}",
                duplicate_of=doc_id,
            )
        r["doc_id"] = doc_id
        store.add_to_project(con, slug, doc_id, promote=False)
