"""A long page cut at its headings into an index page and a child page a
part, and where an old citation of the page's words lands after it."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from prax.text import blocks, markup, slugs

from ..base import (
    _committing,
    _read_archive,
    _reading,
    _serialized,
    document_hidden,
    now,
)
from ..documents import (
    _adopt_chunks,
    _set_meta,
    _set_sensitivity,
    get_meta,
)
from ..graph import (
    _invalidate_edge,
    document_node,
)
from ..retrieval import _FOLD
from .texts import (
    _LINK_EVIDENCE,
    PAGE_AUTHORS,
    linked_documents,
    slugify,
)
from .tree import (
    _seen_project_edges,
    page_id,
)
from .writes import (
    _link_project,
    _new_page,
    _write_page,
)

# a split's children a citation is followed through, at most: a child may
# be split again, and its children are looked in too
SPLIT_DEPTH = 4


def _split_page(
    con: sqlite3.Connection,
    slug: str,
    *,
    level: int | None = None,
    author: str = "agent",
    note: str | None = None,
    dry_run: bool = False,
    expected_text_hash: str | None = None,
) -> dict[str, Any]:
    """Cut a page at its headings of ``level`` into an index page and a
    child page a section, without changing a word.

    ``level`` is a heading level (2 for ``##``); without it, the highest
    level the page has two headings of. The page keeps its id, slug and
    history and becomes the index: the text before the first cut heading,
    then a link to each child. Each child is a new page under it, whose
    text is its section from its heading to the next, titled with the
    page's title and the heading, in the page's projects (those the
    splitter sees), with its sensitivity, its domains and the state of the
    ask blocks it holds. The index's text before the cut and the
    children's texts add up to the page's text byte for byte, or nothing
    is written (ValueError).

    Each child takes the original's chunks in its span, ids and vectors
    with them, so an old citation ``#doc/N?chunk=M&find=…`` names a chunk
    that is still there, in the child (``cited_place``). The page's own
    links become the children's edges as any link does; an edge given as
    ``annotates`` with no link behind it ends on the index when a child
    links the same document, and stays otherwise.

    The words stay whose they were: the index and the children are written
    as the author of the page's last text, so a person's page split by an
    agent is still a person's, and the note says who split it (``author``).
    Refused: a page that is not there (KeyError), a project's page, whose
    text is its project's note, fewer than two headings at the level, an
    ask block across a cut, a page that changed since it was read
    (``expected_text_hash``, PermissionError). ``dry_run`` says what would
    be written and writes nothing."""
    if author not in PAGE_AUTHORS:
        raise ValueError(f"author must be one of {PAGE_AUTHORS}")
    doc_id = page_id(con, slug)
    if doc_id is None or document_hidden(con, doc_id):
        raise KeyError(f"no page {slug!r}")
    row = con.execute(
        "SELECT p.slug, p.kind, d.title, d.text_hash FROM pages p"
        " JOIN documents d ON d.id = p.doc_id WHERE p.doc_id = ?",
        (doc_id,),
    ).fetchone()
    slug, kind, title = str(row["slug"]), str(row["kind"]), str(row["title"])
    if kind == "project":
        raise ValueError(
            f"{slug!r} is a project's page: its text is the project's note"
        )
    if expected_text_hash is not None and expected_text_hash != row["text_hash"]:
        raise PermissionError(
            f"page {slug!r} changed since it was read: its text hash is not"
            f" {expected_text_hash}; read it again"
        )
    text = _read_archive(row["text_hash"]).decode("utf-8") if row["text_hash"] else ""
    level, cuts = _cuts(text, level)
    head = text[: cuts[0][0]]
    parts = [
        (heading, text[start : cuts[i + 1][0] if i + 1 < len(cuts) else len(text)])
        for i, (start, heading) in enumerate(cuts)
    ]
    _refuse_split_blocks(text, [c[0] for c in cuts])
    taken: set[str] = set()
    titles: set[str] = set()
    children: list[dict[str, Any]] = []
    for heading, part in parts:
        child_slug = _free_slug(
            con, slugs.tagged(f"{slug}-{slugify(heading) or 'part'}", heading), taken
        )
        children.append(
            {
                "slug": child_slug,
                "title": _free_title(con, f"{title}: {heading}", titles),
                "text": part,
            }
        )
    plan = {
        "doc_id": doc_id,
        "slug": slug,
        "level": level,
        "index_chars": len(head),
        "children": [
            {"slug": c["slug"], "title": c["title"], "chars": len(c["text"])}
            for c in children
        ],
    }
    if dry_run:
        return {**plan, "dry_run": True}
    whose = _text_author(con, doc_id)
    revision = _last_revision(con, doc_id)
    said = note or f"split by {author} at {'#' * level} headings"
    projects = [str(r["slug"]) for r in _seen_project_edges(con, doc_id)]
    meta = dict(get_meta(con, doc_id))
    starts = [c[0] for c in cuts] + [len(text)]
    for i, child in enumerate(children):
        child["doc_id"] = _write_child(
            con,
            child,
            (doc_id, starts[i], starts[i + 1]),
            meta,
            kind=kind,
            whose=whose,
            note=f"{said}: from {slug} revision {revision}",
            parent=slug,
            projects=list(dict.fromkeys(projects)),
        )
    index = (
        (head.rstrip() + "\n\n" if head.strip() else "")
        + "\n".join(f"- [{c['title']}](#doc/{c['doc_id']})" for c in children)
        + "\n"
    )
    written = _write_page(
        con,
        slug,
        index,
        kind=kind,
        author=whose,
        note=f"{said}: {len(children)} pages",
        force=True,
    )
    _refuse_lost_words(con, text, head, children)
    _end_given_edges(
        con,
        doc_id,
        [c["text"] for c in children],
        run=f"{slug}@{written['revision']}",
    )
    after = get_meta(con, doc_id)
    page = dict(after.get("page") or {})
    page["split"] = [
        *(page.get("split") or []),
        {
            "revision": int(written["revision"]),
            "from": row["text_hash"],
            "level": level,
            "by": author,
            "at": now(),
            "children": [int(c["doc_id"]) for c in children],
        },
    ]
    after["page"] = page
    held = {b.id for b in blocks.blocks(head)}
    if after.get("asks"):
        after["asks"] = {k: v for k, v in after["asks"].items() if k in held}
    _set_meta(con, doc_id, after)
    return {
        **plan,
        "revision": int(written["revision"]),
        "text_hash": written["text_hash"],
        "children": [
            {
                "doc_id": int(c["doc_id"]),
                "slug": c["slug"],
                "title": c["title"],
                "chars": len(c["text"]),
            }
            for c in children
        ],
    }


split_page = _serialized(_committing(_split_page))


def _cuts(text: str, level: int | None) -> tuple[int, list[tuple[int, str]]]:
    """The level a page is cut at and the cuts: each heading's offset and
    words. ValueError when the page has fewer than two headings there."""
    found = markup.headings(text)
    if level is None:
        counts: dict[int, int] = {}
        for _, at, _ in found:
            counts[at] = counts.get(at, 0) + 1
        level = min((at for at, n in counts.items() if n >= 2), default=2)
    cuts = [(offset, words) for offset, at, words in found if at == level]
    if len(cuts) < 2:
        raise ValueError(
            f"the page has {len(cuts)} heading{'' if len(cuts) == 1 else 's'}"
            f" at level {level}: a split needs two"
        )
    return level, cuts


def _refuse_split_blocks(text: str, cuts: list[int]) -> None:
    """ValueError when a cut falls inside an ask block or a kept region:
    the questions pass reads a block whole, in one page."""
    spans = [(b.head[0], (b.tail or b.interior)[1]) for b in blocks.blocks(text)] + [
        (m.start(), m.end()) for m in blocks.KEEP.finditer(text)
    ]
    for start, end in spans:
        if any(start < cut < end for cut in cuts):
            raise ValueError(
                "a heading to cut at is inside an ask block or a kept region:"
                " cut at another level, or move the heading out"
            )


def _free_slug(con: sqlite3.Connection, want: str, taken: set[str]) -> str:
    """``want``, or ``want-2``…: a slug no page has, had or is taking, and
    within ``slugs.LIMIT``, cut before its number, so the page is written
    under the very slug the plan names (``slugify`` keeps it as it is)."""
    n, slug = 1, want
    while (
        slug in taken
        or con.execute(
            "SELECT 1 FROM pages WHERE slug = ? UNION ALL"
            " SELECT 1 FROM page_aliases WHERE slug = ?",
            (slug, slug),
        ).fetchone()
    ):
        n += 1
        number = f"-{n}"
        slug = want[: slugs.LIMIT - len(number)].rstrip("-") + number
    taken.add(slug)
    return slug


def _free_title(con: sqlite3.Connection, want: str, taken: set[str]) -> str:
    """``want``, or ``want (2)``…: a title no document has, so the child is
    a node of its own in the graph (a page's node is named by its title)."""
    n, title = 1, want
    while (
        title in taken
        or con.execute("SELECT 1 FROM documents WHERE title = ?", (title,)).fetchone()
    ):
        n += 1
        title = f"{want} ({n})"
    taken.add(title)
    return title


def _text_author(con: sqlite3.Connection, doc_id: int) -> str:
    last = con.execute(
        "SELECT author FROM page_revisions WHERE doc_id = ? AND change = 'text'"
        " ORDER BY revision DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    return str(last[0]) if last else "agent"


def _last_revision(con: sqlite3.Connection, doc_id: int) -> int:
    top = con.execute(
        "SELECT max(revision) FROM page_revisions WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    return int(top or 0)


def _write_child(
    con: sqlite3.Connection,
    child: dict[str, Any],
    span: tuple[int, int, int],
    meta: dict[str, Any],
    *,
    kind: str,
    whose: str,
    note: str,
    parent: str,
    projects: list[str],
) -> int:
    """One child page: its row first, then the original's chunks in its
    span (``span``: the page, start, end) moved to it, then its text, so
    the chunks whose text did not change keep their ids and vectors."""
    original, start, end = span
    child_id, _ = _new_page(
        con, child["slug"], child["text"], title=child["title"], kind=kind
    )
    _adopt_chunks(con, original, start, end, child_id)
    _write_page(
        con,
        child["slug"],
        child["text"],
        title=child["title"],
        kind=kind,
        author=whose,
        note=note,
        part_of=projects[0] if projects else None,
        parent=parent,
        force=True,
    )
    for project in projects[1:]:
        _link_project(
            con, (child["title"], "page"), project, child_id, child["slug"], 1
        )
    # the page's sensitivity and who decided it, an "open" a person
    # decided included: a rule does not suspect a child its page's
    # person released
    decided = meta.get("sensitivity") or {}
    column = con.execute(
        "SELECT sensitivity FROM documents WHERE id = ?", (original,)
    ).fetchone()[0]
    if column is not None or decided:
        _set_sensitivity(con, child_id, column, by=str(decided.get("by") or "human"))
    mine = get_meta(con, child_id)
    for key in ("domains", "domains_by"):
        if key in meta:
            mine[key] = meta[key]
    held = {b.id for b in blocks.blocks(child["text"])}
    asks = {k: v for k, v in (meta.get("asks") or {}).items() if k in held}
    if asks:
        mine["asks"] = asks
    _set_meta(con, child_id, mine)
    return child_id


def _refuse_lost_words(
    con: sqlite3.Connection, text: str, head: str, children: list[dict[str, Any]]
) -> None:
    """ValueError, taking the whole split back, when the stored texts of the
    children after the index's own words are not the page's text: the cut
    texts add up to it, and each child holds its own, by the hash of what
    it stores (a text is its content address, so nothing is read back
    under the lock: 150 children read again were a quarter of a split)."""
    stored = [
        con.execute(
            "SELECT text_hash FROM documents WHERE id = ?", (int(c["doc_id"]),)
        ).fetchone()[0]
        for c in children
    ]
    whole = head + "".join(c["text"] for c in children) == text
    held = all(
        h == hashlib.sha256(c["text"].encode("utf-8")).hexdigest()
        for h, c in zip(stored, children, strict=True)
    )
    if not (whole and held):
        raise ValueError("the split pages do not add up to the page: nothing written")


def _end_given_edges(
    con: sqlite3.Connection, doc_id: int, texts: list[str], *, run: str
) -> None:
    """An ``annotates`` or ``synthesizes`` edge the page was given with no
    link behind it ends on the index when a child links its document (the
    children's ``texts``): the child's link is the edge now. One a child
    does not link stays."""
    linked: set[str] = set()
    for child_text in texts:
        for target in linked_documents(child_text):
            try:
                linked.add(document_node(con, target)[0])
            except KeyError:
                continue
    for edge in con.execute(
        "SELECT e.id, t.name FROM edges e JOIN entities t ON t.id = e.dst"
        " WHERE e.source_doc = ? AND e.producer = 'page' AND e.valid_to IS NULL"
        " AND e.rel IN ('annotates', 'synthesizes') AND e.evidence NOT LIKE ?",
        (doc_id, _LINK_EVIDENCE + " %"),
    ).fetchall():
        if edge["name"] in linked:
            _invalidate_edge(con, int(edge["id"]), run=run)


def _split_children(con: sqlite3.Connection, doc_id: int) -> list[int]:
    """The pages a page was split into, and theirs, in text order, up to
    ``SPLIT_DEPTH`` splits down."""
    out: list[int] = []

    def walk(at: int, depth: int) -> None:
        if depth > SPLIT_DEPTH:
            return
        page = get_meta(con, at).get("page") or {}
        for split in page.get("split") or []:
            for child in split.get("children") or []:
                if int(child) not in out:
                    out.append(int(child))
                    walk(int(child), depth + 1)

    walk(doc_id, 1)
    return out


def _folded(text: str) -> str:
    return " ".join(_FOLD.findall(text.lower()))


@_reading
def cited_place(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    chunk: int | None = None,
    find: str | None = None,
) -> dict[str, Any] | None:
    """Where a citation of a split page's words lands now: ``{doc_id,
    chunk_id}`` in one of the pages it was split into, or None.

    ``chunk``, a chunk the split moved to a child, is that child's;
    else ``find``, the citation's words, is looked for in the children's
    chunks as the UI folds text. Only the page's own children are followed
    (``meta.page.split``), never a chunk elsewhere, and none the viewer may
    not see."""
    if document_hidden(con, doc_id):
        return None
    children = [c for c in _split_children(con, doc_id) if not document_hidden(con, c)]
    if not children:
        return None
    if chunk is not None:
        row = con.execute("SELECT doc_id FROM chunks WHERE id = ?", (chunk,)).fetchone()
        if row is not None and int(row[0]) in children:
            return {"doc_id": int(row[0]), "chunk_id": chunk}
    probe = _folded(find or "")[:80]
    if not probe:
        return None
    marks = ",".join("?" * len(children))
    rank = {c: i for i, c in enumerate(children)}
    hits = [
        (rank[int(r["doc_id"])], int(r["seq"]), int(r["doc_id"]), int(r["id"]))
        for r in con.execute(
            f"SELECT id, doc_id, seq, text FROM chunks WHERE doc_id IN ({marks})",
            children,
        )
        if probe in _folded(str(r["text"] or ""))
    ]
    if not hits:
        return None
    _, _, place, chunk_id = min(hits)
    return {"doc_id": place, "chunk_id": chunk_id}
