"""MCP server: a proxy of the HTTP door. Run: python -m prax.mcp_server

Every tool is one call to the door (``PRAX_DOOR``, default
``http://127.0.0.1:8000``, with ``PRAX_TOKEN`` when the door asks for one)
through ``prax.client.Door``; this process imports no store module and
opens no database (CLAUDE.md invariants 4 and 5). No business logic here:
the door's handlers are the contract, and an error from the door comes
back as ``{"error": ...}`` so the model can read it.
"""

from __future__ import annotations

import mimetypes
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from prax.client import DEFAULT_DOOR, Door, DoorError, _git, project_files

mcp = MCPServer(
    "prax",
    instructions=(
        "A personal research library: search returns compact hits with ids,"
        " get and get_chunk open them, traverse walks the graph, ask bundles"
        " passages for a question, pages keep what is worth keeping."
    ),
)
_door: Door | None = None


def configure(
    *, base_url: str | None = None, token: str | None = None, client: Any = None
) -> Door:
    """Point the proxy at a door (tests pass a test client)."""
    global _door
    if base_url is None and token is None:
        _door = Door.from_env(name="mcp", client=client)
    else:
        _door = Door(
            base_url or os.environ.get("PRAX_DOOR") or DEFAULT_DOOR,
            token=token if token is not None else os.environ.get("PRAX_TOKEN"),
            client=client,
            name="mcp",
        )
    return _door


def door() -> Door:
    return _door or configure()


def _guard(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except DoorError as exc:
        return {"error": exc.detail or str(exc)}
    except (OSError, httpx.HTTPError) as exc:  # not running, not reachable
        return {
            "error": f"the door is not reachable ({exc}); PRAX_DOOR={door().base_url}"
        }


def _answer(call: Callable[[], Any]) -> dict[str, Any]:
    """One record from the door, or the reason there is none."""
    out: dict[str, Any] = _guard(call)
    return out


def _guarded_list(call: Callable[[], Any]) -> list[dict[str, Any]]:
    out = _guard(call)
    return out if isinstance(out, list) else [out]


@mcp.tool()
def search(
    query: str,
    limit: int = 10,
    kind: str | None = None,
    mode: str = "hybrid",
    rerank: bool | None = None,
    domain: str | None = None,
    doctype: str | None = None,
    published_since: str | None = None,
    published_before: str | None = None,
    include_stale: bool | None = None,
) -> list[dict[str, Any]]:
    """Search the knowledge base. Returns compact snippets + ids.

    ``hybrid`` (default) fuses BM25 keyword search with vector similarity;
    ``fts`` or ``vec`` force one side. Each hit names its chunk ``kind``
    (text, table, figure, code), section ``heading`` path and ``page``;
    a figure hit's ``figure`` is the reference of its image (the
    snippet is what a vision model saw in it). ``kind`` restricts to
    one kind, e.g. ``kind="table"`` for documents with a table about
    the query, ``kind="figure"`` for pictures. ``rerank=True`` rescores the top hits
    with a cross-encoder when one is configured. ``domain`` keeps the
    documents of one ontology module (``research``, ``studio``; documents
    without a domain set are in every module); ``doctype`` keeps pdf,
    web, image, text, note or page documents. Fetch a hit in full with
    ``get_chunk``. When most of the first hits live in one region of the
    library, the list opens with it as an item of its own
    (``kind: "region"``): its name, how much of the hits it holds, a line
    on what it covers, and its part when that is as clear; ``traverse``
    and the graph go on from there. Each hit says when its document was
    ``published`` (as precise as its source: ``2019``, ``2019-07``), so a
    source's age can be weighed; ``published_since``/``published_before``
    (a year or a date) keep a span and leave out undated documents.
    A document that is no longer current (retired, superseded, invalid:
    its own status line, or another document supersedes it) carries
    ``stale`` with the date and what replaced it, and ranks a few places
    lower; quote the replacement as the current word, or pass
    ``include_stale=True`` to keep the plain order. Each passage hit has
    ``cite``, a link that survives a re-chunk (``#doc/N?chunk=M&find=…``,
    words the door chose): paste it as the citation, after the door's
    address and ``/ui/``.
    """
    params: dict[str, Any] = {
        "q": query,
        "limit": limit,
        "mode": mode,
        "regions": True,
        "brief": True,
    }
    for k, v in (
        ("kind", kind),
        ("rerank", rerank),
        ("domain", domain),
        ("doctype", doctype),
        ("published_since", published_since),
        ("published_before", published_before),
        ("include_stale", include_stale),
    ):
        if v is not None:
            params[k] = v
    return _guarded_list(lambda: door().get_json("/search", params))


@mcp.tool()
def get_chunk(chunk_id: int) -> dict[str, Any]:
    """Fetch one chunk in full (ids come from search results).

    Returns its text, kind, heading path, locator (character range and page
    in the document) and, for tables, ``data`` with header and rows.
    """
    return _answer(lambda: door().get_json(f"/chunk/{chunk_id}"))


@mcp.tool()
def get(doc_id: int, offset: int = 0, max_chars: int = 20000) -> dict[str, Any]:
    """Fetch one document by id (ids come from search results).

    Text is windowed: ``text_len`` and ``truncated`` say whether more
    remains; call again with a larger ``offset`` to page. ``meta`` holds
    what the document is and where it belongs (source, domains, language,
    summary, ids, authors), not the history of how it was read.
    """
    return _answer(
        lambda: door().get_json(
            f"/get/{doc_id}",
            {"offset": offset, "max_chars": max_chars, "brief": True},
        )
    )


@mcp.tool()
def traverse(
    entity: str,
    hops: int = 1,
    type: str | None = None,
    domain: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Expand the knowledge graph 1-2 hops from a named entity.

    ``edges`` is the first hop: every fact the entity itself carries,
    with the evidence for each. ``hops=2`` adds ``neighbours``, a map of
    what the documents around it are also about — each with the
    relations that reach it and ``documents``, how many documents
    separately say so, which is what they are ranked by. It is capped
    per type so the papers do not crowd out the ideas, and ``left_out``
    counts the neighbours that did not fit. To go further, traverse a
    neighbour by name; there is no third hop.

    A name can be several things (apple the ingredient, Apple the
    company; a paper and the concept it is named after). Then one is
    walked, the most connected, and ``senses`` lists them all with their
    type, documents and domains; pass ``type`` to walk another.

    ``entity`` may be ``doc:N`` to walk from a library document: what it
    links to, supersedes or invalidates, and what does so to it.

    ``domain`` (a module: research, studio, computing…) keeps what that
    module's documents say, as ``search(domain=)`` does.

    ``as_of`` (``2026-09`` or a UTC moment) walks what the graph held
    then, before later readings ended some facts. A fact whose source
    says when it holds in the world carries ``world_from``/``world_to``.
    """
    params: dict[str, Any] = {"entity": entity, "hops": hops}
    if type:
        params["type"] = type
    if domain:
        params["domain"] = domain
    if as_of:
        params["as_of"] = as_of
    out = _guard(lambda: door().get_json("/traverse", params))
    return out if isinstance(out, dict) else {"error": str(out)}


@mcp.tool()
def link(
    src: str,
    rel: str,
    dst: str,
    src_type: str | None = None,
    dst_type: str | None = None,
    confidence: str = "EXTRACTED",
    source_doc: int | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Add a graph edge between two entities (created if missing).

    Either end may be ``doc:N``, a library document: it brings its own
    name and type, so ``supersedes``, ``invalidates`` and ``links_to``
    join two documents (``link("doc:12", "supersedes", "doc:7",
    source_doc=12, evidence="replaces the plan of March")``). A name
    needs its type. ``evidence`` is a short quote from ``source_doc``,
    never a chunk id. ``confidence`` is EXTRACTED, INFERRED, or AMBIGUOUS.
    """
    return _answer(
        lambda: door().post_json(
            "/link",
            {
                "src": src,
                "src_type": src_type,
                "rel": rel,
                "dst": dst,
                "dst_type": dst_type,
                "confidence": confidence,
                "source_doc": source_doc,
                "evidence": evidence,
                "producer": "agent",
            },
        )
    )


@mcp.tool()
def why(edge_id: int) -> dict[str, Any]:
    """What a derived edge follows from (an INFERRED edge whose producer is
    ``rule:…``, as ``traverse`` lists it): the stated facts of its chain,
    each with its document and evidence. For a fact ``traverse`` marks
    ``disputed``, the facts it disagrees with (``conflicts``): a paper said
    to be published in two venues, each with the document that says so.
    And where the fact's quote stood in its document's text (``place``):
    still there, moved by a re-read (with where it stands now), or gone."""
    return _answer(lambda: door().get_json(f"/edge/{edge_id}/why"))


@mcp.tool()
def changes(
    since: str,
    until: str | None = None,
    world: bool = False,
    entity: str | None = None,
    rel: str | None = None,
    domain: str | None = None,
    corrections: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """What changed in a period (``since``/``until``: ``2026-09``, a day or
    a UTC moment; no ``until`` is now). By default what the library learned
    or stopped holding then (``added``/``ended``: when prax wrote or ended
    each fact; a re-reading of a fact already held is left out). With
    ``world``, what began or ended in the world then, as
    the sources state (``began``/``ended``). Counts by relation and the
    newest facts of each side, each with its document; ``entity`` keeps one
    thing's facts, ``rel`` one relation, ``domain`` one module's documents.
    A repair's corrections (a ``part_of`` turned round, a publisher that
    was typed a venue) are counted as ``corrected`` and listed only with
    ``corrections``. ``traverse(as_of=)`` walks what was held at one moment."""
    params: dict[str, Any] = {"since": since, "limit": limit}
    for key, value in (
        ("until", until),
        ("entity", entity),
        ("rel", rel),
        ("domain", domain),
    ):
        if value:
            params[key] = value
    if world:
        params["world"] = "true"
    if corrections:
        params["corrections"] = "true"
    return _answer(lambda: door().get_json("/graph/changes", params=params))


@mcp.tool()
def connect(
    a: str,
    b: str,
    max_hops: int = 4,
    relations: list[str] | None = None,
    as_of: str | None = None,
    weak: bool = False,
    type_a: str | None = None,
    type_b: str | None = None,
) -> dict[str, Any]:
    """How ``a`` is connected to ``b`` (two names: papers, people,
    methods, devices…): the two or three best paths of at most
    ``max_hops`` facts, each hop with its relation, how many documents
    state it, one of them and its quote. A path through a hub ("machine
    learning", a university) or over weak relations costs more; only
    sound ones are shown, and an answer with none says so (``best_cost``,
    ``weak_left_out``) — no sound connection is an answer. ``relations``
    restricts the hops (``["cites", "extends"]``), ``as_of`` walks what was
    held at a moment, ``weak`` shows the paths past the line too, marked."""
    params: dict[str, Any] = {"a": a, "b": b, "max_hops": max_hops}
    for key, value in (("as_of", as_of), ("type_a", type_a), ("type_b", type_b)):
        if value:
            params[key] = value
    if relations:
        params["relations"] = ",".join(relations)
    if weak:
        params["weak"] = "true"
    return _answer(lambda: door().get_json("/graph/connect", params=params))


@mcp.tool()
def ask(
    question: str,
    limit: int = 8,
    doctype: str | None = None,
    answer: bool = False,
    history: list[dict[str, str]] | None = None,
    steps: int | None = None,
    mode: str = "grounded",
) -> dict[str, Any]:
    """The context for answering a question from the library: one numbered
    passage per document from the hybrid search (best chunk, with chunk
    and document ids) and what the graph records about those documents.
    Answer from it and cite passages as [n]; ``append_page`` keeps an
    answer worth keeping. ``answer=True`` also runs the door host's
    configured model and returns its answer with resolved citations: it
    surfs first — searches again, reads on, walks the graph, drops what
    is beside the point — for ``steps`` steps (the host's default; 0 is
    one shot, a local model then takes tens of seconds instead of a
    minute or two) and the result carries the ``trail``. ``doctype``
    keeps pdf, web, image, text, note or page documents. ``history`` is
    the conversation so far as ``[{question, answer}, …]``: a follow-up
    ("and the second method?") searches in the earlier question's
    neighbourhood and the model sees the earlier turns. ``mode="open"``
    with ``answer=True`` lets the host's model answer past the library:
    the passages cited where used, the rest its own knowledge, said so.
    """
    body: dict[str, Any] = {"question": question, "limit": limit, "doctype": doctype}
    if mode != "grounded":
        body["mode"] = mode
    if history:
        body["history"] = history
    if steps is not None:
        body["steps"] = steps
    if not answer:
        body["backend"] = "none"
    return _answer(lambda: door().post_json("/ask", body))


@mcp.tool()
def maths(
    op: str,
    a: str,
    b: str | None = None,
    var: str | None = None,
    values: dict[str, str] | None = None,
    mapping: dict[str, str] | None = None,
    language: str | None = None,
    lower: str | None = None,
    upper: str | None = None,
    notation: str = "latex",
    steps: list[str] | None = None,
) -> dict[str, Any]:
    """A SymPy calculator for formulas, on a host that runs the maths pack.
    ``op`` is one of read, same, chain, simplify, expand, factor, apart,
    together, substitute, solve, diff, integrate, series, limit, evaluate,
    code. A formula (``a``, and ``b`` for ``same``) is LaTeX, plain notation
    with ``notation="plain"`` (``x**2 + 1``), or ``chunk:<id>`` for a
    display formula of the library (ids from search results); in plain
    notation ``chunk:<id>`` may stand inside a formula (``2*chunk:123``),
    as the formula's right side, read from its LaTeX. ``chain``
    takes the steps of a derivation as ``steps`` (``a`` its first) and
    names the first link that does not hold. ``var`` names the variable
    of solve, diff, integrate, series, limit and apart (partial fractions);
    ``values`` maps names to values for evaluate and substitute, with an
    SI prefix allowed (``10k``, ``1u``, ``26mV``); ``lower``/``upper``
    bound an integral; ``mapping`` renames b's symbols to a's for ``same`` (two papers'
    notations); ``language`` is c or python for ``code``.

    Every answer shows how each formula was read: check the reading before
    trusting a result. ``same`` says how it decided (symbolic, or numeric at
    random points); use it to check each step of a derivation you wrote.
    A formula the tool cannot read whole comes back as an ``error``, never
    as an answer about part of it."""
    args: dict[str, Any] = {}
    for key, value in (
        ("var", var),
        ("values", values),
        ("language", language),
        ("lower", lower),
        ("upper", upper),
    ):
        if value is not None:
            args[key] = value
    body: dict[str, Any] = {"op": op, "a": a, "notation": notation}
    if b is not None:
        body["b"] = b
    if mapping:
        body["mapping"] = mapping
    if steps:
        body["steps"] = steps
    if args:
        body["args"] = args
    return _answer(lambda: door().post_json("/maths", body))


@mcp.tool()
def set_domains(doc_id: int, domains: list[str] | None) -> dict[str, Any]:
    """Which ontology modules a document is read against (its domains, e.g.
    ["research"], ["family", "research"] for a document that is both, or
    null for every module). A document extracted afterwards, or in a re-run
    per domain, uses only those modules' types and relations; a change
    under an extraction makes it stale (``reread`` true in the answer): the
    worker's next extract pass reads the document again against the new
    set and retires the old reading."""
    return _answer(
        lambda: door().put_json(
            f"/doc/{doc_id}/domains", {"domains": domains, "by": "agent"}
        )
    )


@mcp.tool()
def promote(doc_id: int, reason: str | None = None) -> dict[str, Any]:
    """Flag a document for the expensive model's pass (a richer extraction
    with claims and relations between methods) when it turned out to matter:
    cited in an answer, central to a question, worth a page. The pass itself
    runs later as a batch; this only queues."""
    return _answer(
        lambda: door().post_json(
            f"/doc/{doc_id}/promote", {"reason": reason, "by": "agent"}
        )
    )


def _brief(rows: Any, keys: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    out = []
    for r in list(rows or [])[:limit]:
        if isinstance(r, dict):
            out.append({k: r[k] for k in keys if k in r})
    return out


@mcp.tool()
def context(
    doc_id: int | None = None, slug: str | None = None, limit: int = 8
) -> dict[str, Any]:
    """What places a document (by id) or a page (by slug) in the library,
    compactly: its title and summary, the entities its edges point at,
    what it cites and what cites it, the nearest documents, the notes on
    it, and — for a project page — its members. Where ``search`` finds
    things, ``context`` says what the library already knows around one;
    a session on a project starts with ``context(slug="project-<name>")``.
    Follow an id with ``get`` or ``get_chunk``; a name with ``traverse``.
    """

    def call() -> dict[str, Any]:
        d = door()
        if doc_id is None:
            if not slug:
                return {"error": "give a doc_id or a page slug"}
            page = d.get_json(f"/page/{slug}")
            target = page["doc_id"]
        else:
            target = doc_id
        ctx = d.get_json(f"/doc/{target}/context", {"limit": limit})
        return {
            "doc_id": ctx.get("doc_id"),
            "title": ctx.get("title"),
            "url": ctx.get("url"),
            "summary": (ctx.get("summary") or "")[:1200],
            "page": ctx.get("page"),
            "entities": _brief(ctx.get("entities"), ("name", "type", "rel"), 40),
            "cites": _brief(ctx.get("cites"), ("doc_id", "title"), limit),
            "cited_by": _brief(ctx.get("cited_by"), ("doc_id", "title"), limit),
            "similar": _brief(ctx.get("similar"), ("doc_id", "title", "score"), limit),
            "notes": _brief(ctx.get("notes"), ("doc_id", "slug", "title"), limit),
            "members": _brief(ctx.get("members"), ("doc_id", "title"), 40),
        }

    return _answer(call)


@mcp.tool()
def status(doc_ids: list[int]) -> dict[str, Any]:
    """Where documents are on their way to being read: ``indexed``,
    ``processing``, ``reading`` (a reading waits; ``server_down`` names the
    server that must be up), ``queued`` (its ``place``), ``nothing found``
    (the readers found no text: a scan wants OCR or the vision model) or
    ``failed`` (with the ``error``), and whether a worker is ``alive``. Use
    it when a capture or an upload has no text yet."""
    ids = ",".join(str(int(i)) for i in doc_ids)
    return _answer(lambda: door().get_json("/work/status", {"ids": ids}))


@mcp.tool()
def health() -> dict[str, Any]:
    """Whether prax answers: the door's address as this server uses it,
    whether it is reachable, whether the token is accepted, and whether a
    worker is about. The first thing to call when the other tools fail."""
    d = door()
    out: dict[str, Any] = {"door": d.base_url, "client_commit": client_commit()}
    try:  # one call: an answer, a refusal or no answer say all three
        got = d.get_json("/work/status")
    except DoorError as exc:
        if exc.status == 401:
            return {**out, "reachable": True, "token": "refused"}
        if exc.status == 403:
            # the token is good and the route is not open to it: most often
            # a door older than this client, before the route was allowed
            return {
                **out,
                "reachable": True,
                "token": "accepted",
                "error": "this route is not allowed for the token: is the door older"
                " than the client?",
            }
        return {**out, "reachable": True, "token": str(exc)}
    except (OSError, httpx.HTTPError) as exc:
        return {**out, "reachable": False, "error": str(exc)}
    return {
        **out,
        "reachable": True,
        "token": "accepted",
        "worker": got.get("worker"),
        "door_commit": got.get("door"),
    }


def client_commit() -> str | None:
    """The commit of the working copy this server runs from, or None: put
    beside the door's, it says when the two are not the same prax."""
    out = _git(Path(__file__).resolve().parent, "rev-parse", "--short", "HEAD")
    return out.strip() or None if out else None


@mcp.tool()
def request_reading(
    doc_id: int, extractor: str, mode: str | None = None
) -> dict[str, Any]:
    """Ask for a reading of a document the readers found nothing in, or
    read badly: ``pymupdf4llm-ocr`` (OCR over a scan), ``vision-pages``
    (the vision model over scanned pages, ``mode`` scans or all),
    ``marker`` (maths as LaTeX), ``docling``, ``vision`` (an image). A
    worker takes it; ``status([doc_id])`` says when it is done."""
    body: dict[str, Any] = {"extractor": extractor, "by": "agent"}
    if mode:
        body["mode"] = mode
    return _answer(lambda: door().post_json(f"/doc/{doc_id}/reading", body))


@mcp.tool()
def documents(
    domain: str | None = None,
    tag: str | None = None,
    source: str | None = None,
    title: str | None = None,
    limit: int = 20,
    offset: int = 0,
    since: str | None = None,
    published_since: str | None = None,
    published_before: str | None = None,
) -> list[dict[str, Any]]:
    """List documents, newest first, without their text: by ontology
    module (``domain``), by tag (``project:<name>``, ``chat:<name>``,
    ``github:<topic>``), by source (``zotero``, ``capture``, ``github``,
    ``chat``, ``project``), or by a title substring. Each row has the id,
    title, mime, source, tags and when it was added; read one with
    ``get``. ``since`` keeps what was added at that date or moment (UTC)
    or later: ``2026-10-03``, ``2026-10-03T14:00:00Z`` ("what did the
    user just upload"). Each row says when the document was ``published``
    (as its source says it); ``published_since``/``published_before`` (a
    year or a date) keep a span."""

    def call() -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        for k, v in (
            ("domain", domain),
            ("tag", tag),
            ("source", source),
            ("title", title),
            ("since", since),
            ("published_since", published_since),
            ("published_before", published_before),
        ):
            if v:
                params[k] = v
        page = door().get_json("/documents", params)
        rows = []
        for r in page.get("items") or []:
            meta = r.get("meta") or {}
            rows.append(
                {
                    "doc_id": r.get("id"),
                    "title": r.get("title"),
                    "mime": r.get("mime"),
                    "source": meta.get("source"),
                    "tags": meta.get("tags") or [],
                    "domains": meta.get("domains"),
                    "added_at": r.get("added_at"),
                    "published": (meta.get("published") or {}).get("date"),
                }
            )
        return rows

    return _guarded_list(call)


@mcp.tool()
def get_page(slug: str) -> dict[str, Any]:
    """A page of the library's wiki: its Markdown text, kind (addendum,
    project, topic), author of the latest revision and revision list."""
    return _answer(lambda: door().get_json(f"/page/{slug}"))


@mcp.tool()
def write_page(
    slug: str,
    text: str,
    title: str | None = None,
    kind: str = "topic",
    annotates: list[int] | None = None,
    part_of: str | None = None,
    note: str | None = None,
    sensitivity: str | None = None,
) -> dict[str, Any]:
    """Create a page (kind addendum, project or topic) or replace its text
    as the agent. Refused when a person wrote the latest revision: use
    append_page then. ``annotates`` lists document ids the page is about;
    ``part_of`` names a project page's slug. Cite what you read as
    chunk ids or document ids in the text so readers can check; a link
    ``[title](#doc/N)`` in the text is an edge to that document. A
    standing question inside the page is an ask block, ``<!-- prax:ask
    id=q1 "the question" -->`` on one line and ``<!-- /prax:ask id=q1
    -->`` on the next: the door answers it between the markers and asks
    again when the library learns something (``get_page`` lists them as
    ``blocks``). ``sensitivity="personal"`` keeps the page behind the wall
    (notes on a colleague's work)."""
    return _answer(
        lambda: door().put_json(
            f"/page/{slug}",
            {
                "text": text,
                "title": title,
                "kind": kind,
                "author": "agent",
                "note": note,
                "annotates": annotates,
                "part_of": part_of,
                "sensitivity": sensitivity,
            },
        )
    )


@mcp.tool()
def append_page(
    slug: str, section: str, heading: str | None = None, note: str | None = None
) -> dict[str, Any]:
    """Add a section to an existing page as the agent, leaving what a
    person wrote untouched."""
    return _answer(
        lambda: door().post_json(
            f"/page/{slug}/append",
            {"section": section, "heading": heading, "author": "agent", "note": note},
        )
    )


@mcp.tool()
def update_section(
    slug: str, heading: str, text: str, note: str | None = None
) -> dict[str, Any]:
    """Replace the body under one heading of a page with ``text``, or add
    the section when the page has none (``section``: replaced or added).
    For what you keep up to date (a project's status, a summary), where
    ``append_page`` would add another copy each time. A section a person
    wrote is never replaced, nor one holding an ask block."""
    return _answer(
        lambda: door().put_json(
            f"/page/{slug}/section",
            {"heading": heading, "text": text, "author": "agent", "note": note},
        )
    )


@mcp.tool()
def ingest(
    text: str,
    title: str | None = None,
    source_url: str | None = None,
    sensitivity: str | None = None,
) -> dict[str, Any]:
    """Ingest raw text as a new document (deduped by content hash).
    ``sensitivity="personal"`` puts it behind the wall from the start (a
    colleague's notes, a review): only the owner and tokens allowed
    personal documents see it."""
    body = {"text": text, "title": title, "source_url": source_url, "by": "agent"}
    if sensitivity:
        body["sensitivity"] = sensitivity
    return _answer(lambda: door().post_json("/ingest", body))


@mcp.tool()
def capture_url(
    url: str, title: str | None = None, domains: list[str] | None = None
) -> dict[str, Any]:
    """Fetch a web page or file by URL and keep it: a page is indexed at
    once, a PDF waits for the worker. ``domains`` names the ontology
    modules it belongs to (e.g. ["research"]). Pass ``title`` when you
    know it: a file otherwise carries its file name or arXiv id until
    the titles pass reads one. A site that refuses the server (a 403, a
    bot check, a certificate it cannot verify) comes back as
    ``queued_for_extension``: the browser extension fetches it with the
    person's session and it arrives later; not an error, not yet a
    document."""
    return _answer(
        lambda: door().post_json(
            "/ingest/url",
            {"url": url, "title": title, "domains": domains, "by": "agent"},
        )
    )


@mcp.tool()
def capture_urls(urls: list[str], domains: list[str] | None = None) -> dict[str, Any]:
    """Fetch and keep several URLs in one call (at most 20), each as
    ``capture_url`` would: ``results`` has one entry per URL in the order
    given, the capture (``doc_id``, ``url``) or its ``error``. A URL that
    fails never fails the others."""
    items = [{"url": u, "domains": domains, "by": "agent"} for u in urls]
    return _answer(lambda: door().post_json("/ingest/urls", {"items": items}))


@mcp.tool()
def references(doc_id: int, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """A paper's reference list, an entry each in order: its number,
    title, authors, year, DOI or arXiv id as printed, and either
    ``in_library`` (the library document it cites, ``doc_id``) or ``links``
    to read it elsewhere (doi.org, arxiv.org). ``limit`` entries from
    ``offset`` (a book's list runs to thousands); ``entries`` counts the
    whole list and ``left_out`` what this answer did not carry."""
    params = {"limit": limit, "offset": offset}
    return _answer(lambda: door().get_json(f"/doc/{doc_id}/references", params=params))


@mcp.tool()
def cited_but_missing(
    doc_ids: list[int] | None = None,
    tag: str | None = None,
    project: str | None = None,
    page: str | None = None,
    limit: int = 30,
    min_count: int = 1,
) -> dict[str, Any]:
    """What a set of papers cites that the library does not hold, ranked
    by how many of them cite it: each work's title, authors, year, DOI or
    arXiv id, ``links`` and ``cited_by``. Name the set by ``doc_ids``, a
    ``tag``, a ``project`` or a ``page`` (the documents it links), e.g.
    ``cited_but_missing(page="onset-detection-landscape")``; the works
    are candidates for ``capture_url``. ``min_count`` leaves out what
    fewer of them cite; among works cited as often, the one the rest of
    the library cites less (``cited_in_library``) comes first, so a
    reference every field cites does not crowd the topic's own."""
    body = {
        "doc_ids": doc_ids,
        "tag": tag,
        "project": project,
        "page": page,
        "limit": limit,
        "min_count": min_count,
    }
    return _answer(lambda: door().post_json("/references/missing", body))


@mcp.tool()
def set_title(doc_id: int, title: str) -> dict[str, Any]:
    """Give a document its title (a file name or an arXiv id where a
    title belongs). The old one is kept in its history."""
    return _answer(
        lambda: door().put_json(f"/doc/{doc_id}/title", {"title": title, "by": "agent"})
    )


def ingest_roots() -> list[Path]:
    """Where ``ingest_file`` and ``sync_project`` may read from:
    ``PRAX_INGEST_ROOTS`` (paths separated by the OS path separator), else
    the git repository of the working directory, else the directory. The
    model names the path, on a page's say-so as much as the user's, so a
    key file or a browser profile outside the project stays out."""
    raw = os.environ.get("PRAX_INGEST_ROOTS", "").strip()
    if raw:
        parts = [x for x in raw.split(os.pathsep) if x.strip()]
        return [Path(x).expanduser().resolve() for x in parts]
    # the repository the session works in, not only its folder: syncing a
    # neighbouring subproject is the common case in a repository of many
    # (the first client, O1); outside git, the working directory
    here = Path.cwd()
    top = _git(here, "rev-parse", "--show-toplevel")
    return [Path(top.strip()).resolve()] if top and top.strip() else [here]


@mcp.tool()
def ingest_file(
    path: str, title: str | None = None, source_url: str | None = None
) -> dict[str, Any]:
    """Ingest a file from a path on this machine (the one running the MCP
    server); it is uploaded to the door. Only files under the working
    directory, or under the roots ``PRAX_INGEST_ROOTS`` names, are read.

    Text files are indexed immediately; binaries (PDF, HTML) are archived
    and left for the worker.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return {"error": f"no such file: {path}"}
    real = p.resolve()
    roots = ingest_roots()
    if not any(real == r or r in real.parents for r in roots):
        return {
            "error": f"{path} is outside the roots ingest_file may read"
            f" ({os.pathsep.join(str(r) for r in roots)}); set PRAX_INGEST_ROOTS"
            " for the MCP server, or use `prax add` yourself"
        }
    fields = {"title": title or p.name}
    if source_url:
        fields["source_url"] = source_url
    mimetypes.guess_type(p.name)  # the upload names the type from the file name
    return _answer(lambda: door().upload(p, fields))


@mcp.tool()
def sync_project(
    root: str = ".",
    name: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    domains: list[str] | None = None,
    tags: list[str] | None = None,
    dry_run: bool = True,
    tracked_only: bool = True,
    auto_sync: bool | None = None,
    sensitivity: str | None = None,
) -> dict[str, Any]:
    """Send a project's written knowledge (README, docs, notes: .md, .rst,
    .txt, .adoc) from a working copy on this machine to the library.

    A dry run by default: the answer is the plan, each path ``add``,
    ``refresh``, ``unchanged``, ``moved`` or ``skip`` with why, documents
    of the project that are ``gone``, and the counts. Run again with
    ``dry_run=false`` to apply it. Only files git tracks count
    (``tracked_only``), and build and vendored folders (``build/``,
    ``_deps/``, ``CMakeFiles/``, ``*-subbuild/``, ``node_modules/``…)
    never do. A subdirectory of a repository is a project of its own.

    Documents are keyed by the git remote and their path in the
    repository, so a checkout elsewhere finds the same ones. The project's
    settings (``name``, ``domains``, ``tags``, ``include``, ``exclude``) are
    kept in prax after the first sync and need not be sent again;
    ``auto_sync=true`` lets the plugin's session-end hook sync it on its
    own. Its page is ``project-<name>``. ``sensitivity="personal"`` keeps
    every synced note behind the wall (a colleague's project), and is kept
    with the settings. Needs the administrator token.
    """
    base = Path(root).expanduser().resolve()
    roots = ingest_roots()
    if not any(base == r or r in base.parents for r in roots):
        return {
            "error": f"{root} is outside the roots this server may read"
            f" ({os.pathsep.join(str(r) for r in roots)}); set PRAX_INGEST_ROOTS"
        }
    try:
        got = project_files(
            base,
            include=include or [],
            exclude=exclude or [],
            tracked_only=tracked_only,
            texts=not dry_run,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    body = {
        **got,
        "name": name,
        "domains": domains,
        "tags": tags,
        "include": include,
        "exclude": exclude,
        "auto_sync": auto_sync,
        "sensitivity": sensitivity,
        "dry_run": dry_run,
    }
    return _answer(lambda: door().post_json("/projects/sync", body))


if __name__ == "__main__":
    mcp.run()  # stdio transport
