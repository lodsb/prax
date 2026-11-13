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
  anywhere, so the next sync matches it again;
- a journal (``prax.text.journal``: a Markdown file of dated entries) is
  its entries, each a document keyed by the file's key and the entry's
  (``…/journal.md#2026-11-10b``), and its head, what stands before the
  first entry, under the file's own key (AU step 2). A new entry is one
  new document, not the file again. An entry edited after its day is a
  new version of a work (``store.state_versions``): the earlier text stays
  live, folded under the newest in a search, and carries no key any more
  (``project.later`` names the text after it). A file that becomes a
  journal retires its whole text into its head, or with no head says so.
"""

from __future__ import annotations

import functools
import hashlib
import sqlite3
from typing import Any

from prax import client, store
from prax.errors import NotFound
from prax.text import journal, markup, paths

PLAN_SHOWN = 400  # plan rows returned; the counts are always whole
SETTINGS = ("domains", "tags", "include", "exclude")
SENSITIVITIES = (None, "personal")  # what a project's notes may be marked on sync


def doc_key(name: str, remote: str | None, repo_path: str, rel: str) -> str:
    """What a synced document is found by: the remote and the path in the
    repository, or without a remote the project's name and the path."""
    return f"{remote}:{repo_path}" if remote else f"{name}/{rel}"


def _title(rel: str, text: str, name: str) -> str:
    """The first heading of a Markdown file, else its path; with the
    project's name."""
    first = None
    if rel.lower().endswith((".md", ".markdown")):
        first = next((w for _, level, w in markup.headings(text) if level <= 3), None)
    return f"{first.strip() if first else rel} ({name})"


def _entry_title(rel: str, key: str, name: str) -> str:
    """An entry's title: its key and its file, which stay when its words
    change, so its versions are one node (``document_node``)."""
    return f"{key} · {rel} ({name})"


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


class NotAllowed(PermissionError):
    """What a named token may not do to a project (the route answers 403)."""


def _named_may(
    con: sqlite3.Connection,
    req: dict[str, Any],
    name: str,
    held: dict[str, Any],
    settings: dict[str, list[str]],
    sensitivity: str | None,
    have: dict[str, dict[str, Any]],
    dry_run: bool,
) -> None:
    """What a named token may sync (AL step 9, N1): a plan of any project;
    a sync only of a project the administrator registered, with its
    settings as they are and its modules within the token's. A project
    holding a note the token does not see, or kept personal, is as if
    absent (a KeyError): its plan would name what the wall hides. The
    administrator (no viewer) may do all of it."""
    viewer = store.VIEWER.get()
    if viewer is None:
        return
    hidden = store.hidden_documents(con)
    if (sensitivity and not viewer.personal) or any(
        v["doc_id"] in hidden for v in have.values()
    ):
        raise NotFound(f"no such project: {name}")
    if dry_run:
        return
    if not held:
        raise NotAllowed(
            f"{name!r} is not a project yet: a named token syncs only a project"
            " the administrator registered (a dry run is open to it)"
        )
    stored = held.get("settings") or {}
    changed = [
        k
        for k in SETTINGS
        if req.get(k) is not None and settings[k] != _words(stored.get(k))
    ]
    if req.get("sensitivity") is not None and req["sensitivity"] != stored.get(
        "sensitivity"
    ):
        changed.append("sensitivity")
    if req.get("auto_sync") is not None and bool(req["auto_sync"]) != bool(
        held.get("auto_sync")
    ):
        changed.append("auto_sync")
    if changed:
        raise NotAllowed(
            f"a project's settings are the administrator's: {', '.join(changed)}"
        )
    if viewer.domains is not None:
        beyond = [d for d in settings["domains"] if d not in viewer.domains]
        if beyond or not settings["domains"]:
            raise NotAllowed(
                "the project is read against modules this token does not see:"
                f" {', '.join(beyond) or 'every module'}"
            )


def _settings(
    req: dict[str, Any], stored: dict[str, Any]
) -> tuple[dict[str, list[str]], str | None]:
    """The project's settings, each from the request where it says one,
    else as stored, and its sensitivity; a sensitivity that is neither
    personal nor none is a ValueError."""
    settings = {
        k: _words(req[k]) if req.get(k) is not None else _words(stored.get(k))
        for k in SETTINGS
    }
    sensitivity = (
        req.get("sensitivity")
        if req.get("sensitivity") is not None
        else stored.get("sensitivity")
    )
    if sensitivity not in SENSITIVITIES:
        raise ValueError("a project's sensitivity is personal, or none")
    return settings, sensitivity


def _file_rows(
    files: list[dict[str, Any]],
    name: str,
    remote: str | None,
    prefix: str,
    settings: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """One row a file: its path in the project and in the repository, the
    document key and the one before keys were by remote (``legacy``), its
    version, and why it is skipped, if it is. A journal is a row for its
    head and one for each entry (``_journal_rows``)."""
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
        row = {
            "path": rel,
            "repo_path": repo_path,
            "key": doc_key(name, remote, repo_path, rel),
            "legacy": f"{name}/{rel}",
            "version": str(f.get("sha256") or "")[:16],
            "file": f,
            "why": why,
        }
        rows.extend(_journal_rows(row) if not why else [row])
    return rows


def _journal_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    """A file's row, or a journal's: its head under the file's key (with
    ``empty`` when nothing stands before the first entry) and one row an
    entry, keyed ``<file key>#<entry key>``, its version the entry's hash.
    A file whose text did not come (a dry run's plan) is read as a note."""
    text = row["file"].get("text")
    if not isinstance(text, str) or not row["path"].lower().endswith(
        (".md", ".markdown")
    ):
        return [row]
    split = journal.split(text)
    if split is None:
        return [row]
    head = split.head if split.head.strip() else ""
    out = [
        {
            **row,
            "version": _hash(head)[:16] if head else "",
            "file": {**row["file"], **_bytes_of(head)},
            "journal": len(split.entries),
            "empty": not head,
        }
    ]
    for e in split.entries:
        out.append(
            {
                **row,
                "key": f"{row['key']}#{e.key}",
                "legacy": f"{row['legacy']}#{e.key}",
                "version": _hash(e.text)[:16],
                "file": {**row["file"], **_bytes_of(e.text)},
                "entry": e.key,
            }
        )
    return out


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bytes_of(text: str) -> dict[str, Any]:
    """What a row's file says of a part of it: its text, size and hash."""
    return {"text": text, "bytes": len(text.encode("utf-8")), "sha256": _hash(text)}


def _match(
    rows: list[dict[str, Any]], have: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Each row's action against what the project holds (``have``, by
    key): skip, unchanged or refresh by its key (or its legacy key),
    moved where another key holds its version, else add. The documents no
    row claimed are what is gone."""
    by_version = {v["version"]: (k, v) for k, v in have.items() if v.get("version")}
    claimed: set[str] = set()
    for r in rows:
        if r["why"]:
            r["action"] = "skip"
            continue
        found = r["key"] if r["key"] in have else None
        if found is None and r["legacy"] in have:
            found = r["legacy"]
        if r.get("empty"):
            # a journal with nothing before its first entry: the file's
            # whole text, synced before it was one, is split, and else
            # there is no document under the file's key
            if found is not None:
                claimed.add(found)
                r["doc_id"] = have[found]["doc_id"]
            r["action"] = "split" if found is not None else "none"
            continue
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
    return [
        {"path": v.get("path"), "doc_id": v["doc_id"]}
        for k, v in have.items()
        if k not in claimed
    ]


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
    settings, sensitivity = _settings(req, held.get("settings") or {})
    settings_out: dict[str, Any] = {**settings}
    if sensitivity:
        settings_out["sensitivity"] = sensitivity
    if not dry_run:
        from prax.graph import ontology

        modules = set(ontology.current().modules)
        unknown = [d for d in settings["domains"] if d not in modules]
        if unknown:
            raise ValueError(f"no ontology module {', '.join(unknown)}")
    rows = _file_rows(files, name, remote, prefix, settings)
    have = store.project_documents(
        con, name, [r["key"] for r in rows] + [r["legacy"] for r in rows]
    )
    _named_may(con, req, name, held, settings, sensitivity, have, dry_run)
    gone = _match(rows, have)
    rows = [r for r in rows if r["action"] != "none"]
    if not dry_run:
        missing = [
            r["path"]
            for r in rows
            if r["action"] in ("add", "refresh") and not r["file"].get("text")
        ]
        if missing:
            raise ValueError(f"no text sent for {', '.join(missing[:5])}")
        _apply(con, name, remote, prefix, settings, rows, sensitivity)
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
        "settings": settings_out,
        "counts": counts,
        "plan": [
            {
                k: r[k]
                for k in ("path", "entry", "action", "why", "doc_id", "was")
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
            settings=settings_out,
            auto_sync=req.get("auto_sync"),
        )
        texts = _texts(con, name)
        links = _links(con, name, prefix, texts)
        statuses = _statuses(con, name, prefix, texts)
        out["links"] = links
        out["status"] = statuses
        store.note_project_sync(
            con,
            name,
            {
                **counts,
                "links": links["added"] + links["kept"],
                "stale": statuses["stale"],
            },
        )
        out["auto_sync"] = manifest["auto_sync"]
    return out


LINK_PRODUCER = "sync"
LINK_REL = "links_to"
EVIDENCE_CHARS = 300


Want = dict[tuple[int, int, int, str], tuple[str, str | None]]


def _keep_in_step(con: sqlite3.Connection, run: str, want: Want) -> dict[str, int]:
    """Make one run's live edges what ``want`` says: ``{(source_doc,
    src_doc, dst_doc, rel): (words, world_from)}``, the evidence and the
    date (``store.sync_facts``: an edge already there is kept with its
    first words, a new one linked, one no longer wanted ended, never
    deleted, its ending recorded under the run)."""
    nodes: dict[int, tuple[str, str]] = {}

    def node(doc_id: int) -> tuple[str, str]:
        if doc_id not in nodes:
            nodes[doc_id] = store.document_node(con, doc_id)
        return nodes[doc_id]

    facts: list[store.Stated] = []
    for (source_doc, src_doc, dst_doc, rel), (words, world_from) in want.items():
        try:
            src, src_type = node(src_doc)
            dst, dst_type = node(dst_doc)
        except KeyError:
            continue  # a document without a title is no node
        facts.append(
            store.Stated(
                store.Edge(src, src_type, rel, dst, dst_type),
                source_doc,
                words[:EVIDENCE_CHARS],
                world_from,
            )
        )
    got = store.sync_facts(
        con, facts, producer=LINK_PRODUCER, run=run, quotes_count=False, end_run=run
    )
    return {"added": got.count("added"), "kept": got.count("kept"), "ended": got.ended}


Text = tuple[str, int, str, bool]  # path, document, text, whether an entry


def _texts(con: sqlite3.Connection, name: str) -> list[Text]:
    """The project's current documents with their path in the repository
    and their text: a note, a journal's head, a journal's entry."""
    out: list[Text] = []
    for v in store.project_documents(con, name).values():
        if not v.get("path"):
            continue
        doc = store.get_document(con, int(v["doc_id"]))
        out.append(
            (
                str(v["path"]),
                int(v["doc_id"]),
                str(doc.get("text") or "") if doc else "",
                bool(v.get("entry")),
            )
        )
    return out


def _by_path(texts: list[Text]) -> dict[str, int]:
    """What a path names: the note, or the journal's head; never an entry."""
    return {p: d for p, d, _, entry in texts if not entry}


def _links(
    con: sqlite3.Connection, name: str, prefix: str, texts: list[Text]
) -> dict[str, int]:
    """Keep the project's ``links_to`` edges in step with its texts, a
    journal's entries' among them. Returns the counts, ``unmatched`` being
    the references to paths no document of the project has (yet)."""
    by_path = _by_path(texts)
    want: Want = {}
    unmatched = 0
    for path, doc_id, text, _entry in texts:
        for candidates, words in paths.references(text, path, prefix):
            hit = next((by_path[c] for c in candidates if c in by_path), None)
            if hit is None:
                unmatched += 1
            elif hit != doc_id:
                want.setdefault((doc_id, doc_id, hit, LINK_REL), (words, None))
    return {**_keep_in_step(con, f"links:{name}", want), "unmatched": unmatched}


def _statuses(
    con: sqlite3.Connection, name: str, prefix: str, texts: list[Text]
) -> dict[str, int]:
    """What each note says of itself (``prax.text.status``): kept as
    ``meta.status`` (by ``sync``, gone when the line goes; a person's is
    never touched), and a replacement the line names as an edge from it to
    the stale note (``supersedes``, ``invalidates`` for an invalid one),
    the line for evidence and its date as the world's time. A journal's
    status is its head's; an entry's words are not read for one (a
    correction relabels its first text superseded, not the entry)."""
    from prax.text import status as status_mod

    by_path = _by_path(texts)
    words = status_mod.lexicon_words()
    want: Want = {}
    stale = 0
    for path, doc_id, text, entry in texts:
        if entry:
            continue
        st = status_mod.read(text, path, prefix, words)
        said = (
            None
            if st is None
            else {
                "state": st.state,
                "since": st.since,
                "words": st.words[:EVIDENCE_CHARS],
                "by": "sync",
            }
        )
        meta = store.update_meta(con, doc_id, functools.partial(_said, said))
        if (meta.get("status") or {}).get("by") not in (None, "sync"):
            continue  # a person said it
        if st is None or not st.stale:
            continue
        stale += 1
        rel = status_mod.stale_rel(st.state)
        for candidates, _words in st.refs:
            hit = next((by_path[c] for c in candidates if c in by_path), None)
            if hit is not None and hit != doc_id:
                want.setdefault((doc_id, hit, doc_id, rel), (st.words, st.since))
    return {**_keep_in_step(con, f"status:{name}", want), "stale": stale}


def _own(own: dict[str, Any], tags: list[str], meta: store.DocumentMeta) -> None:
    """A project's record of its file on the document, and its tags; with
    tags, only on a document the project's own sync wrote (these bytes may
    be someone else's capture of the same file)."""
    if tags and meta.get("source") != store.PROJECT_SOURCE:
        return
    meta[store.PROJECT_SOURCE] = {**(meta.get(store.PROJECT_SOURCE) or {}), **own}
    if tags:
        meta["tags"] = sorted({*(meta.get("tags") or []), *tags})


def _said(said: dict[str, Any] | None, meta: store.DocumentMeta) -> None:
    """What a sync's reading of a note's status does to its meta: a
    person's status is kept; the sync's own is replaced, or dropped when
    the note says none any more."""
    held = meta.get("status") or {}
    if held and held.get("by") not in (None, "sync"):
        return
    if said is None:
        meta.pop("status", None)
    elif {k: held.get(k) for k in said} != said:
        meta["status"] = {**said, "at": store.now()}


def _apply(
    con: sqlite3.Connection,
    name: str,
    remote: str | None,
    prefix: str,
    settings: dict[str, list[str]],
    rows: list[dict[str, Any]],
    sensitivity: str | None = None,
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
        if r.get("entry"):
            own["entry"] = r["entry"]
        if action in ("unchanged", "moved"):
            if r.get("old_key") != r["key"] or action == "moved":
                store.update_meta(con, r["doc_id"], functools.partial(_own, own, []))
            store.add_to_project(con, slug, r["doc_id"], promote=False)
            continue
        if action == "split":
            store.retire_document(
                con,
                r["doc_id"],
                reason=f"split into its {r['journal']} entries by a sync of"
                f" project {name}",
            )
            continue
        if action not in ("add", "refresh"):
            continue
        title = (
            _entry_title(r["path"], r["entry"], name)
            if r.get("entry")
            else _title(r["path"], str(f["text"]), name)
        )
        got = store.ingest_text(
            con,
            str(f["text"]),
            title=title,
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
            store.update_meta(con, doc_id, functools.partial(_own, own, tags))
            r["action"] = "same"
        for d in settings["domains"]:
            store.add_domain(con, doc_id, d)
        if action == "refresh" and r.get("doc_id") and r["doc_id"] != doc_id:
            if r.get("entry"):
                _new_version(con, name, r["doc_id"], doc_id)
            else:
                store.retire_document(
                    con,
                    r["doc_id"],
                    reason=f"replaced by a newer sync of project {name}",
                    replaced_by=doc_id,
                    carry=(LINK_PRODUCER,),
                )
        r["doc_id"] = doc_id
        if sensitivity:
            # a colleague's notes: behind the wall from the first sync on
            store.set_sensitivity(con, doc_id, sensitivity, by="sync")
        store.add_to_project(con, slug, doc_id, promote=False)


def _new_version(con: sqlite3.Connection, name: str, old: int, new: int) -> None:
    """An entry edited after its day: its earlier text and the new one
    versions of one work (``store.state_versions``), the earlier kept live
    for a search to fold under the newest, without its key, so the next
    sync finds the new one by it."""
    store.state_versions(
        con,
        [old, new],
        by=LINK_PRODUCER,
        run=f"entries:{name}",
        evidence="an entry of a journal edited after its day",
    )
    store.update_meta(con, old, functools.partial(_earlier, new))


def _earlier(later: int, meta: store.DocumentMeta) -> None:
    held = dict(meta.get(store.PROJECT_SOURCE) or {})
    held["was"] = held.pop("key", None)
    held["later"] = later
    meta[store.PROJECT_SOURCE] = held
