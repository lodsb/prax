"""A piece of the graph as a file, and back (docs/graph-files.md).

An export is what is reachable from a seed: a project, a domain, a tag,
or an entity and its hops. It holds the entities and the live edges with
every provenance column, the pages with all their revisions, and for
documents only their identity (the hash of the original, the title, the
ids). It is JSON lines in a stable order, so an export kept in a
repository diffs line by line, and its first line names the ontology it
was written against, with the modules' text.

An import is a producer of its own, ``import:<source>``, with a run per
file, so it never overwrites anything. Each edge goes through
``store.link``: a name this library knows lands on its entity, and a
triple the local ontology refuses goes to the review queue with the
reason. The edge's original producer, run and document are kept in its
evidence. Importing a newer export of the same source first retires
what the last one linked (``retire_run``), so the graph converges on the
file. A page that exists here with other text gets the imported text as
a new revision with a note; nothing is merged by machine.

Not exported: chunks, vectors, the review queue, the originals.

Line kinds, each a JSON object with ``kind``: ``header`` first, then
``document``, ``entity``, ``edge`` and ``page`` lines, each group in a
stable order.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

from prax import markup, ontology, store

FORMAT = "prax-graph/1"
MAX_HOPS = 2


@dataclass
class Seed:
    project: str | None = None
    domain: str | None = None
    tag: str | None = None
    entity: str | None = None
    type: str | None = None  # the entity's type, when its name is several things
    hops: int = 1
    history: bool = False  # ended edges too

    def named(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, False)}


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"


def export(con: sqlite3.Connection, seed: Seed) -> Iterator[str]:
    """The sub-graph a seed reaches, as JSON lines."""
    if not (seed.project or seed.domain or seed.tag or seed.entity):
        raise ValueError("a seed: a project, a domain, a tag or an entity")
    doc_ids = store.seed_documents(
        con, project=seed.project, domain=seed.domain, tag=seed.tag
    )
    edges = store.subgraph_edges(con, doc_ids=doc_ids, history=seed.history)
    if seed.entity:
        edge_ids = _walk(con, seed.entity, seed.type, seed.hops)
        edges = store.subgraph_edges(
            con, edge_ids=edge_ids + [e["id"] for e in edges], history=seed.history
        )
    held = sorted(set(doc_ids) | {e["source_doc"] for e in edges if e["source_doc"]})
    slugs = store.page_slugs_of(con, held)
    # a page's own edges come from its links, and importing the page makes
    # them again from its text: exported as well, they would be there twice
    page_docs = {
        int(page["doc_id"])
        for page in (store.get_page(con, slug) for slug in slugs)
        if page is not None
    }
    edges = [e for e in edges if e["source_doc"] not in page_docs]
    onto = ontology.current()
    yield _line(
        {
            "kind": "header",
            "format": FORMAT,
            "exported_at": store.now(),
            "seed": seed.named(),
            "ontology": onto.version,
            "modules": _module_texts(),
            "counts": {"documents": len(held), "edges": len(edges)},
        }
    )
    documents = store.document_identities(con, held)
    for d in documents:
        yield _line({"kind": "document", **d})
    names = sorted(
        {(e["src"], e["src_type"]) for e in edges}
        | {(e["dst"], e["dst_type"]) for e in edges}
    )
    labels = store.labels_of_entities(con, names)
    for name, etype in names:
        yield _line(
            {
                "kind": "entity",
                "name": name,
                "type": etype,
                "labels": labels.get((name, etype), []),
            }
        )
    for e in edges:
        yield _line(
            {
                "kind": "edge",
                **{k: e[k] for k in e if k not in ("id", "source_doc")},
            }
        )
    for slug in slugs:
        page = store.get_page(con, slug)
        if page is None:
            continue
        yield _line(
            {
                "kind": "page",
                "slug": slug,
                "title": page["title"],
                "page_kind": page["kind"],
                "revisions": [
                    {
                        "revision": r["revision"],
                        "author": r["author"],
                        "note": r["note"],
                        "created_at": r["created_at"],
                        "text": store.page_revision_text(con, slug, r["revision"]),
                    }
                    for r in page["revisions"]
                ],
            }
        )


def _walk(
    con: sqlite3.Connection, name: str, etype: str | None, hops: int
) -> list[int]:
    """The edges of an entity, and with two hops those of what it touches."""
    hops = max(1, min(hops, MAX_HOPS))
    first = store.traverse(con, name, 1, limit=0, type=etype)
    ids = [int(e["edge_id"]) for e in first]
    if hops > 1:
        seen = {(name, etype)}
        for e in first:
            for other in ((e["src"], e["src_type"]), (e["dst"], e["dst_type"])):
                if other in seen:
                    continue
                seen.add(other)
                ids += [
                    int(x["edge_id"])
                    for x in store.traverse(con, other[0], 1, limit=0, type=other[1])
                ]
    return sorted(set(ids))


def _module_texts() -> dict[str, str]:
    """The ontology modules' own files, so an export says exactly what its
    types and relations meant when it was written."""
    from prax import config

    folder = config.ONTOLOGY_PATH
    out = {}
    for name in sorted(ontology.current().modules):
        path = folder / f"{name}.yaml"
        if path.exists():
            out[name] = path.read_text(encoding="utf-8")
    return out


# ---------------------------------------------------------------- import


@dataclass
class Report:
    source: str
    run: str
    ontology: str | None = None
    ontology_here: str | None = None
    documents: int = 0
    documents_held: int = 0
    entities: int = 0
    labels: int = 0
    edges: int = 0
    linked: int = 0
    queued: int = 0
    ended_skipped: int = 0
    retired: int = 0
    pages_new: int = 0
    pages_revised: int = 0
    pages_same: int = 0
    pages_refused: list[str] = field(default_factory=list)


def import_lines(
    con: sqlite3.Connection,
    lines: Iterable[str],
    *,
    source: str,
    dry_run: bool = False,
) -> Report:
    """Read an export into this library as ``import:<source>``. A dry run
    writes nothing and says what would happen: which documents are held
    here, how many edges the local ontology takes and how many would go to
    the review queue, which pages are new or changed."""
    source = source.strip() or "file"
    producer = f"import:{source}"
    run = f"import-{source}-{store.now().replace(':', '').replace('-', '')}"
    rep = Report(source=source, run=run, ontology_here=ontology.current().version)
    records = [json.loads(line) for line in lines if line.strip()]
    if not records or records[0].get("kind") != "header":
        raise ValueError("not a prax graph file: the first line is no header")
    head = records[0]
    if head.get("format") != FORMAT:
        raise ValueError(f"format {head.get('format')!r}; this prax reads {FORMAT}")
    rep.ontology = head.get("ontology")
    docs = [r for r in records if r["kind"] == "document"]
    rep.documents = len(docs)
    local = store.documents_by_hash(con, [d["hash"] for d in docs])
    rep.documents_held = len(local)
    titles = {d["hash"]: d.get("title") or "" for d in docs}
    # the ids the pages' links used in the other library, to this one's
    old_ids = {int(d["id"]): d["hash"] for d in docs if d.get("id") is not None}
    if dry_run:
        return _dry(con, records, rep, local)
    # a newer export of the same source replaces the last one's edges
    rep.retired = store.retire_run(con, producer=producer)
    for r in records:
        if r["kind"] == "edge":
            _import_edge(con, r, rep, local, titles, producer, run)
    for r in records:
        if r["kind"] == "entity":
            rep.entities += 1
            rep.labels += _import_labels(con, r, producer, run)
    for r in records:
        if r["kind"] == "page":
            _import_page(con, r, rep, source, local, old_ids, titles)
    return rep


def _dry(
    con: sqlite3.Connection,
    records: list[dict[str, Any]],
    rep: Report,
    local: dict[str, int],
) -> Report:
    onto = ontology.current()
    for r in records:
        if r["kind"] == "entity":
            rep.entities += 1
            rep.labels += len(r.get("labels") or [])
        elif r["kind"] == "edge":
            rep.edges += 1
            if r.get("valid_to"):
                rep.ended_skipped += 1
                continue
            try:
                onto.check_edge(
                    onto.canonical_type(r["src_type"]),
                    onto.canonical_relation(r["rel"]),
                    onto.canonical_type(r["dst_type"]),
                )
                rep.linked += 1
            except ValueError:
                rep.queued += 1
        elif r["kind"] == "page":
            here = store.get_page(con, r["slug"])
            revisions = r.get("revisions") or []
            if here is None:
                rep.pages_new += 1
            elif revisions and revisions[-1]["text"] == here["text"]:
                rep.pages_same += 1
            else:
                rep.pages_revised += 1
    return rep


def _import_edge(
    con: sqlite3.Connection,
    r: dict[str, Any],
    rep: Report,
    local: dict[str, int],
    titles: dict[str, str],
    producer: str,
    run: str,
) -> None:
    rep.edges += 1
    if r.get("valid_to"):
        rep.ended_skipped += 1  # history of the other library, not a fact here
        return
    doc_hash = r.get("source_hash")
    doc = local.get(doc_hash) if doc_hash else None
    origin = f"{r.get('producer') or '?'}/{r.get('run') or '?'}"
    if doc_hash and doc is None:
        origin += (
            f", from “{titles.get(doc_hash) or doc_hash[:12]}” (sha256 {doc_hash[:12]})"
        )
    evidence = ((r.get("evidence") or "").strip() + f" [imported: {origin}]").strip()
    edge = store.Edge(r["src"], r["src_type"], r["rel"], r["dst"], r["dst_type"])
    try:
        store.link(
            con,
            edge,
            confidence=r.get("confidence") or "AMBIGUOUS",
            source_doc=doc,
            evidence=evidence,
            producer=producer,
            run=run,
        )
        rep.linked += 1
    except ValueError as exc:
        store.queue_review(
            con,
            src=edge.src,
            src_type=edge.src_type,
            rel=edge.rel,
            dst=edge.dst,
            dst_type=edge.dst_type,
            reason=f"{exc} (imported, written against {r.get('ontology_version')})",
            source_doc=doc,
            evidence=evidence,
            ontology_version=r.get("ontology_version"),
        )
        rep.queued += 1


def _import_labels(
    con: sqlite3.Connection, r: dict[str, Any], producer: str, run: str
) -> int:
    got = store.entity_answering(con, r["name"], r["type"]) if r.get("labels") else None
    if got is None:
        return 0
    n = 0
    for label in r["labels"]:
        store.add_label(
            con,
            int(got),
            str(label["label"]),
            lang=label.get("lang"),
            kind="alt",
            producer=producer,
            run=run,
        )
        n += 1
    return n


def _relink(text: str, old_ids: dict[int, str], local: dict[str, int]) -> str:
    """A page's ``[title](#doc/N)`` links, from the other library's ids to
    this one's; a link to a document not held here keeps its title only."""

    def swap(m: Any) -> str:
        doc_hash = old_ids.get(int(m.group(1)))
        here = local.get(doc_hash) if doc_hash else None
        return f"](#doc/{here})" if here else "]"

    out = markup.DOC_LINK.sub(swap, text)
    # "[title]" left bare by a missing target reads as the title
    return out


def _import_page(
    con: sqlite3.Connection,
    r: dict[str, Any],
    rep: Report,
    source: str,
    local: dict[str, int],
    old_ids: dict[int, str],
    titles: dict[str, str],
) -> None:
    slug = r["slug"]
    revisions = sorted(r.get("revisions") or [], key=lambda x: x["revision"])
    if not revisions:
        return
    here = store.get_page(con, slug)
    if here is None:
        for rev in revisions:
            store.write_page(
                con,
                slug,
                _relink(rev["text"], old_ids, local),
                title=r.get("title"),
                kind=r.get("page_kind") or "topic",
                author=rev.get("author") or "human",
                note=_note(rev, source),
                force=True,  # the other library's own history, in its order
            )
        rep.pages_new += 1
        return
    last = revisions[-1]
    text = _relink(last["text"], old_ids, local)
    if text == here["text"]:
        rep.pages_same += 1
        return
    try:
        store.write_page(
            con,
            slug,
            text,
            title=r.get("title"),
            kind=here["kind"],
            author=last.get("author") or "human",
            note=_note(last, source)
            + f"; this library's page differed (its revision {here['revision']})",
        )
        rep.pages_revised += 1
    except PermissionError:
        # an agent's text over a person's page: the door refuses that here
        # as it does anywhere, and the import says so instead of forcing it
        rep.pages_refused.append(slug)


def _note(rev: dict[str, Any], source: str) -> str:
    said = (
        f"imported from {source},"
        f" revision {rev.get('revision')} of {rev.get('created_at')}"
    )
    return f"{said}: {rev['note']}" if rev.get("note") else said
