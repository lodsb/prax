"""A piece of the graph as a file, and back (docs/graph-files.md).

An export is what is reachable from a seed: a project, a domain, a tag,
or an entity and its hops. It holds the entities and the live edges with
every provenance column, the pages with all their revisions, and for
documents only their identity (the hash of the original, the title, the
ids). It is JSON lines in a stable order, so an export kept in a
repository diffs line by line, and its first line names the ontology it
was written against, with the modules' text.

Format 2 (``prax-graph/2``, plan item AP, ``docs/global-ids.md``) names
every entity, edge and page by its global id: an entity's ``gid`` and the
``aliases`` folded into it, an edge's ends by ``gid`` and its own ``gid``
(the claim, ``edge_gid``), a page's ``gid``. An INFERRED edge a rule
derived is left out: the receiving library derives its own.

An import is a producer of its own, ``import:<source>``, with a run per
file, so it never overwrites anything. A file of format 2 settles each
entity first (``store.settle_foreign``): one of this library's own ids
lands on its row, one an earlier import of the source decided lands
there, one whose name a thing here answers to waits on the Review page
with its edges held, and one nothing answers to is new. An edge already
live for its document is counted and not written again, and the
source's live edges the file no longer has are ended, so a second
import of one file writes nothing and a newer file converges the graph
on it. A file of format 1 names entities by name alone: each edge goes
through ``store.link`` by name, after the last import of the source is
retired (``retire_run``).

Either way a triple the local ontology refuses goes to the review queue
with the reason, and the edge's original producer, run and document are
kept in its evidence. A page that exists here with other text gets the
imported text as a new revision with a note; nothing is merged by
machine. Format 2 finds the page by its ``gid``, never by an equal slug.

Not exported: chunks, vectors, the review queue, the originals, and
anything of a face (``FACE_RELATIONS``). An export runs as the request's
viewer: made for a named token (``prax export --as``), it holds only what
that token sees.

Line kinds, each a JSON object with ``kind``: ``header`` first, then
``document``, ``entity``, ``edge`` and ``page`` lines, each group in a
stable order.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

from prax import store
from prax.graph import ontology
from prax.text import markup

FORMAT = "prax-graph/2"
# who a picture shows is a person's answer about a face: faces are
# biometric and never leave the library, whoever the file is for
FACE_RELATIONS = frozenset({store.DEPICTS})
READS = ("prax-graph/1", FORMAT)
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
    edges = [
        e
        for e in edges
        if e["source_doc"] not in page_docs
        and not e["premised"]
        and e["rel"] not in FACE_RELATIONS
    ]
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
    for d in store.document_identities(con, held):
        yield _line({"kind": "document", **d})
    yield from _entity_lines(con, edges)
    for e in edges:
        yield _line(
            {
                "kind": "edge",
                "gid": edge_gid(e),
                **{k: e[k] for k in e if k not in ("id", "source_doc", "premised")},
            }
        )
    yield from _page_lines(con, slugs)


def _entity_lines(
    con: sqlite3.Connection, edges: list[dict[str, Any]]
) -> Iterator[str]:
    """The entities the edges reach, by type and name, each with its gid,
    its aliases and its labels."""
    gids = {(e["src"], e["src_type"]): e["src_gid"] for e in edges} | {
        (e["dst"], e["dst_type"]): e["dst_gid"] for e in edges
    }
    names = sorted(gids)
    labels = store.labels_of_entities(con, names)
    aliases = store.folded_gids(con, sorted(gids.values()))
    for name, etype in names:
        yield _line(
            {
                "kind": "entity",
                "name": name,
                "type": etype,
                "gid": gids[(name, etype)],
                "aliases": aliases.get(gids[(name, etype)], []),
                "labels": labels.get((name, etype), []),
            }
        )


def _page_lines(con: sqlite3.Connection, slugs: list[str]) -> Iterator[str]:
    """Each page with every revision's text."""
    for slug in slugs:
        page = store.get_page(con, slug)
        if page is None:
            continue
        yield _line(
            {
                "kind": "page",
                "gid": page["gid"],
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


def edge_gid(edge: dict[str, Any]) -> str:
    """An edge's global id: the claim, not the row. The sha256 of its source
    document's hash (empty for none), its relation and its two ends' gids,
    so every reading of one claim from one document has the same one."""
    said = "\x1f".join(
        (edge.get("source_hash") or "", edge["rel"], edge["src_gid"], edge["dst_gid"])
    )
    return "c:" + hashlib.sha256(said.encode("utf-8")).hexdigest()


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
    files = ontology.module_files()
    out = {}
    for name in sorted(ontology.current().modules):
        path = files.get(name)
        if path is not None and path.exists():
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
    # format 2: where their entities landed (own, likely, same, new,
    # left_out) and what became of their edges beyond linked and queued
    landed: dict[str, int] = field(default_factory=dict)
    edges: int = 0
    linked: int = 0
    same: int = 0
    held: int = 0
    left_out: int = 0
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
    run = store.run_name(f"import-{source}")
    rep = Report(source=source, run=run, ontology_here=ontology.current().version)
    records = [json.loads(line) for line in lines if line.strip()]
    if not records or records[0].get("kind") != "header":
        raise ValueError("not a prax graph file: the first line is no header")
    head = records[0]
    if head.get("format") not in READS:
        raise ValueError(
            f"format {head.get('format')!r}; this prax reads {', '.join(READS)}"
        )
    by_gid = head["format"] != "prax-graph/1"
    rep.ontology = head.get("ontology")
    docs = [r for r in records if r["kind"] == "document"]
    rep.documents = len(docs)
    local = store.documents_by_hash(con, [d["hash"] for d in docs])
    rep.documents_held = len(local)
    titles = {d["hash"]: d.get("title") or "" for d in docs}
    # the ids the pages' links used in the other library, to this one's
    old_ids = {int(d["id"]): d["hash"] for d in docs if d.get("id") is not None}
    if dry_run:
        return _dry(con, records, rep, local, source if by_gid else None)
    if by_gid:
        _import_by_gid(con, records, rep, local, titles, source)
    else:
        # a newer export of the same source replaces the last one's edges
        rep.retired = store.retire_run(con, producer=producer)
        _import_edges(con, records, rep, local, titles, source, _by_name)
        for r in records:
            if r["kind"] == "entity":
                rep.entities += 1
                rep.labels += _import_labels(con, r, producer, run)
    for r in records:
        if r["kind"] == "page":
            _import_page(con, r, rep, source, local, old_ids, titles)
    return rep


def _import_by_gid(
    con: sqlite3.Connection,
    records: list[dict[str, Any]],
    rep: Report,
    local: dict[str, int],
    titles: dict[str, str],
    source: str,
) -> None:
    """Format 2: the entities settled, then each live edge between them
    written, held or counted, then what the file no longer says ended."""
    producer = f"import:{source}"
    store.hold_again(con, source)  # this file's held edges replace the last one's
    landed: dict[str, store.Landing] = {}
    for r in records:
        if r["kind"] != "entity":
            continue
        if not r.get("gid") or not r.get("name") or not r.get("type"):
            raise ValueError(f"an entity line with no gid, name or type: {r}")
        rep.entities += 1
        landing = store.settle_foreign(con, source, r, run=rep.run)
        landed[landing.gid] = landing
        rep.landed[landing.state] = rep.landed.get(landing.state, 0) + 1
        rep.labels += store.add_foreign_labels(con, source, landing, run=rep.run)

    def ends(r: dict[str, Any]) -> tuple[store.Landing, store.Landing]:
        src, dst = landed.get(r["src_gid"]), landed.get(r["dst_gid"])
        if src is None or dst is None:
            raise ValueError(f"edge {r.get('gid')}: an end with no entity line")
        return src, dst

    keep = _import_edges(con, records, rep, local, titles, source, ends)
    rep.retired = store.end_unconfirmed(con, producer, keep, run=rep.run)


def _by_name(r: dict[str, Any]) -> tuple[store.Landing, store.Landing]:
    """Format 1's ends: by name alone, as ``store.link`` lands them."""
    return (
        store.Landing("", "named", None, r["src"], r["src_type"]),
        store.Landing("", "named", None, r["dst"], r["dst_type"]),
    )


def _import_edges(
    con: sqlite3.Connection,
    records: list[dict[str, Any]],
    rep: Report,
    local: dict[str, int],
    titles: dict[str, str],
    source: str,
    ends: Callable[[dict[str, Any]], tuple[store.Landing, store.Landing]],
) -> set[int]:
    """Each live edge line through ``store.import_edge`` between the ends
    ``ends`` lands it on, counted in the report; the edges it wrote or found
    here already."""
    keep: set[int] = set()
    for r in records:
        if r["kind"] != "edge":
            continue
        rep.edges += 1
        if r.get("valid_to"):
            rep.ended_skipped += 1  # history of the other library, not a fact here
            continue
        doc = local.get(r["source_hash"]) if r.get("source_hash") else None
        said, edge_id = store.import_edge(
            con,
            r,
            ends(r),
            doc=doc,
            source=source,
            producer=f"import:{source}",
            run=rep.run,
            titles=titles,
        )
        setattr(rep, said, getattr(rep, said) + 1)
        if edge_id is not None:
            keep.add(edge_id)
    return keep


def _dry(
    con: sqlite3.Connection,
    records: list[dict[str, Any]],
    rep: Report,
    local: dict[str, int],
    source: str | None,
) -> Report:
    """What an import would do, writing nothing; with ``source`` (format
    2), where each entity would land too."""
    onto = ontology.current()
    for r in records:
        if r["kind"] == "entity":
            rep.entities += 1
            rep.labels += len(r.get("labels") or [])
            if source is not None:
                state = store.foreign_state(con, source, r).state
                rep.landed[state] = rep.landed.get(state, 0) + 1
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
    gid = r.get("gid")
    if gid:  # format 2: the page of that gid, never one with an equal slug
        doc = store.page_by_gid(con, gid)
        slugs = store.page_slugs_of(con, [doc]) if doc is not None else []
        slug = slugs[0] if slugs else _free_slug(con, slug)
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
                gid=gid,  # the page it was minted as there, so it comes back here
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


def _free_slug(con: sqlite3.Connection, slug: str) -> str:
    """``slug``, or the first of ``slug-2``, ``slug-3`` … no page here has."""
    n, free = 1, slug
    while store.get_page(con, free) is not None or store.page_id(con, free):
        n += 1
        free = f"{slug}-{n}"
    return free


def _note(rev: dict[str, Any], source: str) -> str:
    said = (
        f"imported from {source},"
        f" revision {rev.get('revision')} of {rev.get('created_at')}"
    )
    return f"{said}: {rev['note']}" if rev.get("note") else said
