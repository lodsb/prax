"""Zotero importer: a read-only walk over a *copy* of ``zotero.sqlite`` that
turns items, attachments and notes into prax documents through ``prax.store``.

Mapping and rules: ``docs/sources.md`` §1. In short:

* one document per attachment **file**, hashed from its bytes; the same file
  under several Zotero records is one document carrying every key in
  ``meta.zotero.keys``;
* parent-item metadata (creators, date, DOI, tags, collections…) rides on the
  attachment's document; items without a file become metadata-only text
  documents; linked URLs become ``text/uri-list`` documents; notes become
  text documents pointing at their parent;
* text is indexed straight from Zotero's ``.zotero-ft-cache`` when present
  and tagged ``meta.text_source = "zotero-ft-cache"`` so a later Docling pass
  can find and upgrade it; otherwise ``parsed_at`` stays NULL for the parse
  queue;
* ``authored_by`` seed edges, ``confidence = EXTRACTED``, per creator;
* idempotent: a document whose Zotero key is already recorded is skipped
  unless the Zotero record's ``dateModified`` changed, in which case its
  metadata is refreshed.

Nothing here writes to the Zotero library (invariant 10) or to SQLite
directly (invariant 3).
"""

from __future__ import annotations

import mimetypes
import shutil
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from prax import ontology, store

TEXT_SOURCE = "zotero-ft-cache"
FT_CACHE = ".zotero-ft-cache"
NON_REGULAR_TYPES = ("attachment", "note", "annotation")
AUTHOR_CREATOR_TYPES = ("author", "bookAuthor")
EDGE_KINDS = ("attachment", "url", "metadata")  # notes are not papers
LINK_MODES = {0: "imported_file", 1: "imported_url", 2: "linked_file", 3: "linked_url"}
# Zotero fields that get their own place; everything else lands in meta.fields.
_LIFTED_FIELDS = ("title", "url", "abstractNote", "date", "DOI")
_BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}


# ------------------------------------------------------------------ library


@dataclass
class Library:
    """A read-only connection to a private copy of ``zotero.sqlite``."""

    con: sqlite3.Connection
    storage: Path
    copy_path: Path

    def close(self) -> None:
        self.con.close()


def open_library(zotero_dir: Path, workdir: Path | None = None) -> Library:
    """Copy ``zotero.sqlite`` into ``workdir`` and open the copy read-only.

    The live database is never opened: Zotero holds it locked while running,
    and the importer must not write to it in any case. An up-to-date copy
    (same size and mtime) is reused.
    """
    src = zotero_dir / "zotero.sqlite"
    if not src.is_file():
        raise FileNotFoundError(src)
    workdir = workdir or Path(tempfile.mkdtemp(prefix="prax-zotero-"))
    workdir.mkdir(parents=True, exist_ok=True)
    copy = workdir / "zotero.sqlite"
    if (
        not copy.exists()
        or copy.stat().st_size != src.stat().st_size
        or (copy.stat().st_mtime < src.stat().st_mtime)
    ):
        shutil.copy2(src, copy)
    con = sqlite3.connect(f"{copy.resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return Library(con=con, storage=zotero_dir / "storage", copy_path=copy)


# -------------------------------------------------------------------- model


@dataclass
class Item:
    """A regular Zotero item (article, book, …) with its metadata resolved."""

    id: int
    key: str
    type: str
    date_added: str
    date_modified: str
    fields: dict[str, str]
    creators: list[dict[str, str]]
    tags: list[str]
    collections: list[str]

    @property
    def title(self) -> str:
        return self.fields.get("title", "")

    def meta(self) -> dict[str, Any]:
        rest = {k: v for k, v in self.fields.items() if k not in _LIFTED_FIELDS}
        return {
            "creators": self.creators,
            "date": _normalize_date(self.fields.get("date")),
            "doi": self.fields.get("DOI"),
            "abstract": self.fields.get("abstractNote"),
            "tags": self.tags,
            "collections": self.collections,
            "fields": rest,
        }


@dataclass
class Attachment:
    id: int
    key: str
    parent_id: int | None
    link_mode: int
    content_type: str | None
    path: str | None
    fields: dict[str, str]
    date_added: str
    date_modified: str

    @property
    def filename(self) -> str | None:
        if self.path and self.path.startswith("storage:"):
            return self.path[len("storage:") :]
        return None


@dataclass
class Planned:
    """One document the importer will (or would) write."""

    kind: str  # attachment | url | metadata | note
    key: str  # the Zotero key that identifies this document
    title: str
    mime: str
    source_url: str | None
    meta: dict[str, Any]
    date_modified: str
    item: Item | None = None
    path: Path | None = None  # attachment file on disk
    text: str | None = None  # inline text for url / metadata / note documents
    missing: bool = False  # file referenced but absent

    def to_wire(self) -> dict[str, Any]:
        """What the door needs to apply this item, without the file: the
        bytes and the cache text travel beside it (``prax import zotero``)."""
        return {
            "kind": self.kind,
            "key": self.key,
            "title": self.title,
            "mime": self.mime,
            "source_url": self.source_url,
            "meta": self.meta,
            "date_modified": self.date_modified,
            "text": self.text,
            "missing": self.missing,
            "original_path": str(self.path) if self.path else None,
            "creators": list(self.item.creators) if self.item else [],
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> Planned:
        """The door's side of ``to_wire``; the creators come back as a
        minimal item, enough for the edges."""
        creators = list(data.get("creators") or [])
        item = None
        if creators:
            item = Item(
                id=0,
                key=str(data["key"]),
                type="",
                date_added="",
                date_modified=str(data.get("date_modified") or ""),
                fields={"title": str(data.get("title") or "")},
                creators=creators,
                tags=[],
                collections=[],
            )
        return cls(
            kind=str(data["kind"]),
            key=str(data["key"]),
            title=str(data.get("title") or ""),
            mime=str(data.get("mime") or "application/octet-stream"),
            source_url=data.get("source_url"),
            meta=dict(data.get("meta") or {}),
            date_modified=str(data.get("date_modified") or ""),
            item=item,
            path=Path(data["original_path"]) if data.get("original_path") else None,
            text=data.get("text"),
            missing=bool(data.get("missing")),
        )


def load(p: Planned) -> tuple[bytes | None, str | None]:
    """The attachment's bytes and Zotero's cached text for it, read from
    the library on disk; nothing for the inline kinds."""
    if p.path is None:
        return None, None
    return p.path.read_bytes(), _read_cache_text(p.path)


# ----------------------------------------------------------------- reading


def _normalize_date(raw: str | None) -> str | None:
    """``"2018-04-00 4/2018"`` → ``"2018-04"``; ``"1997-00-00 1997"`` → ``"1997"``."""
    if not raw:
        return None
    iso = raw.split(" ", 1)[0]
    while iso.endswith("-00"):
        iso = iso[:-3]
    return iso or None


def _fields(zc: sqlite3.Connection, item_id: int) -> dict[str, str]:
    rows = zc.execute(
        "SELECT f.fieldName AS name, v.value AS value FROM itemData d"
        " JOIN fields f USING (fieldID) JOIN itemDataValues v USING (valueID)"
        " WHERE d.itemID = ?",
        (item_id,),
    ).fetchall()
    return {r["name"]: str(r["value"]) for r in rows}


def _creators(zc: sqlite3.Connection, item_id: int) -> list[dict[str, str]]:
    rows = zc.execute(
        "SELECT c.firstName, c.lastName, c.fieldMode, ct.creatorType"
        " FROM itemCreators ic JOIN creators c USING (creatorID)"
        " JOIN creatorTypes ct USING (creatorTypeID)"
        " WHERE ic.itemID = ? ORDER BY ic.orderIndex",
        (item_id,),
    ).fetchall()
    out = []
    for r in rows:
        first, last = r["firstName"] or "", r["lastName"] or ""
        name = last if r["fieldMode"] == 1 or not first else f"{first} {last}"
        out.append(
            {
                "name": name.strip(),
                "first": first,
                "last": last,
                "type": r["creatorType"],
            }
        )
    return out


def _tags(zc: sqlite3.Connection, item_id: int) -> list[str]:
    rows = zc.execute(
        "SELECT t.name FROM itemTags JOIN tags t USING (tagID) WHERE itemID = ?"
        " ORDER BY t.name",
        (item_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def _collection_paths(zc: sqlite3.Connection) -> dict[int, str]:
    rows = zc.execute(
        "SELECT collectionID, collectionName, parentCollectionID FROM collections"
    ).fetchall()
    by_id = {
        r["collectionID"]: (r["collectionName"], r["parentCollectionID"]) for r in rows
    }

    def path(cid: int) -> str:
        name, parent = by_id[cid]
        return f"{path(parent)}/{name}" if parent in by_id else name

    return {cid: path(cid) for cid in by_id}


def _deleted_ids(zc: sqlite3.Connection) -> set[int]:
    return {r[0] for r in zc.execute("SELECT itemID FROM deletedItems")}


def _item(zc: sqlite3.Connection, row: sqlite3.Row, colls: dict[int, str]) -> Item:
    item_id = row["itemID"]
    coll_ids = [
        r[0]
        for r in zc.execute(
            "SELECT collectionID FROM collectionItems WHERE itemID = ?", (item_id,)
        )
    ]
    return Item(
        id=item_id,
        key=row["key"],
        type=row["typeName"],
        date_added=row["dateAdded"],
        date_modified=row["dateModified"],
        fields=_fields(zc, item_id),
        creators=_creators(zc, item_id),
        tags=_tags(zc, item_id),
        collections=sorted(colls[c] for c in coll_ids if c in colls),
    )


def _attachment(zc: sqlite3.Connection, row: sqlite3.Row) -> Attachment:
    return Attachment(
        id=row["itemID"],
        key=row["key"],
        parent_id=row["parentItemID"],
        link_mode=row["linkMode"],
        content_type=row["contentType"],
        path=row["path"],
        fields=_fields(zc, row["itemID"]),
        date_added=row["dateAdded"],
        date_modified=row["dateModified"],
    )


def regular_items(lib: Library) -> Iterator[Item]:
    """Every non-deleted regular item (not attachment, note, annotation)."""
    zc = lib.con
    colls = _collection_paths(zc)
    deleted = _deleted_ids(zc)
    placeholders = ",".join("?" * len(NON_REGULAR_TYPES))
    rows = zc.execute(
        "SELECT i.itemID, i.key, i.dateAdded, i.dateModified, t.typeName"
        " FROM items i JOIN itemTypes t USING (itemTypeID)"
        f" WHERE t.typeName NOT IN ({placeholders}) ORDER BY i.itemID",
        NON_REGULAR_TYPES,
    ).fetchall()
    for row in rows:
        if row["itemID"] not in deleted:
            yield _item(zc, row, colls)


def _attachments_of(lib: Library, parent_id: int | None) -> list[Attachment]:
    zc = lib.con
    cond = "ia.parentItemID = ?" if parent_id is not None else "ia.parentItemID IS NULL"
    args: tuple[Any, ...] = (parent_id,) if parent_id is not None else ()
    rows = zc.execute(
        "SELECT ia.itemID, i.key, ia.parentItemID, ia.linkMode, ia.contentType,"
        " ia.path, i.dateAdded, i.dateModified"
        " FROM itemAttachments ia JOIN items i USING (itemID)"
        f" WHERE {cond} AND ia.itemID NOT IN (SELECT itemID FROM deletedItems)"
        " ORDER BY ia.itemID",
        args,
    ).fetchall()
    return [_attachment(zc, r) for r in rows]


def _notes_of(lib: Library, parent_id: int | None) -> list[sqlite3.Row]:
    cond = "n.parentItemID = ?" if parent_id is not None else "n.parentItemID IS NULL"
    args: tuple[Any, ...] = (parent_id,) if parent_id is not None else ()
    return lib.con.execute(
        "SELECT n.itemID, i.key, n.title, n.note, i.dateAdded, i.dateModified"
        " FROM itemNotes n JOIN items i USING (itemID)"
        f" WHERE {cond} AND n.itemID NOT IN (SELECT itemID FROM deletedItems)"
        " ORDER BY n.itemID",
        args,
    ).fetchall()


# -------------------------------------------------------------------- notes


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    lines = [ln.strip() for ln in "".join(p.parts).splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


# ----------------------------------------------------------------- planning


def _zotero_meta(
    key: str,
    kind: str,
    *,
    item: Item | None,
    att: Attachment | None = None,
    **extra: Any,
) -> dict[str, Any]:
    z: dict[str, Any] = {"kind": kind, "keys": [key]}
    if item is not None:
        z.update(
            items=[item.key],
            item_type=item.type,
            item_date_added=item.date_added,
            item_date_modified=item.date_modified,
        )
    if att is not None:
        z.update(
            link_mode=LINK_MODES.get(att.link_mode, att.link_mode),
            filename=att.filename,
            date_added=att.date_added,
            date_modified=att.date_modified,
        )
    z.update(extra)
    return z


def _plan_attachment(
    lib: Library, att: Attachment, item: Item | None
) -> Planned | None:
    """A file attachment (link modes 0–2) or a linked URL (mode 3)."""
    parent_meta = item.meta() if item else {}
    url = (item.fields.get("url") if item else None) or att.fields.get("url")
    if att.link_mode == 3:
        if not url:
            return None
        title = (item.title if item else "") or att.fields.get("title") or url
        text = "\n\n".join(t for t in (title, parent_meta.get("abstract")) if t)
        return Planned(
            kind="url",
            key=att.key,
            title=title,
            mime="text/uri-list",
            source_url=url,
            meta={
                "source": "zotero",
                "zotero": _zotero_meta(att.key, "url", item=item, att=att),
                **parent_meta,
            },
            date_modified=att.date_modified,
            item=item,
            text=text,
        )
    path = _resolve_path(lib, att)
    if path is None:
        return None
    filename = att.filename or path.name
    title = (
        (item.title if item else "") or att.fields.get("title") or Path(filename).stem
    )
    mime = (
        att.content_type
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )
    cache = path.parent / FT_CACHE
    has_cache = cache.is_file()
    return Planned(
        kind="attachment",
        key=att.key,
        title=title,
        mime=mime,
        source_url=url,
        meta={
            "source": "zotero",
            "zotero": _zotero_meta(att.key, "attachment", item=item, att=att),
            "text_source": TEXT_SOURCE if has_cache else None,
            **parent_meta,
        },
        date_modified=max(att.date_modified, item.date_modified if item else ""),
        item=item,
        path=path,
        missing=not path.is_file(),
    )


def _resolve_path(lib: Library, att: Attachment) -> Path | None:
    if not att.path:
        return None
    if att.path.startswith("storage:"):
        return lib.storage / att.key / att.path[len("storage:") :]
    if att.path.startswith("attachments:"):
        return None  # linked-file base directory: not used by this library
    return Path(att.path)


def _plan_metadata(item: Item) -> Planned:
    m = item.meta()
    text = "\n\n".join(t for t in (item.title, m.get("abstract")) if t) or item.key
    return Planned(
        kind="metadata",
        key=item.key,
        title=item.title or item.key,
        mime="text/plain",
        source_url=item.fields.get("url"),
        meta={
            "source": "zotero",
            "zotero": _zotero_meta(item.key, "metadata", item=item),
            **m,
        },
        date_modified=item.date_modified,
        item=item,
        text=text,
    )


def _plan_note(row: sqlite3.Row, item: Item | None) -> Planned | None:
    text = html_to_text(row["note"] or "")
    if not text:
        return None
    title = row["title"] or text.splitlines()[0][:120]
    z = _zotero_meta(row["key"], "note", item=item)
    if item is not None:
        z["parent"] = item.key
    return Planned(
        kind="note",
        key=row["key"],
        title=title,
        mime="text/plain",
        source_url=None,
        meta={"source": "zotero", "zotero": z},
        date_modified=row["dateModified"],
        item=item,
        text=text,
    )


def plan(lib: Library) -> Iterator[Planned]:
    """Everything the importer would write, in a stable order.

    Regular items first (each followed by its attachments and notes), then
    standalone attachments, then standalone notes.
    """
    for item in regular_items(lib):
        n_docs = 0
        for att in _attachments_of(lib, item.id):
            p = _plan_attachment(lib, att, item)
            if p is not None:
                n_docs += not p.missing
                yield p
        if n_docs == 0:
            yield _plan_metadata(item)
        for note in _notes_of(lib, item.id):
            p = _plan_note(note, item)
            if p is not None:
                yield p
    for att in _attachments_of(lib, None):
        p = _plan_attachment(lib, att, None)
        if p is not None:
            yield p
    for note in _notes_of(lib, None):
        p = _plan_note(note, None)
        if p is not None:
            yield p


# ---------------------------------------------------------------- inventory


@dataclass
class Inventory:
    items_by_type: Counter[str] = field(default_factory=Counter)
    planned_by_kind: Counter[str] = field(default_factory=Counter)
    attachments_by_link_mode: Counter[str] = field(default_factory=Counter)
    attachments_by_mime: Counter[str] = field(default_factory=Counter)
    with_text_cache: int = 0
    without_text_cache: int = 0
    missing_files: list[tuple[str, str]] = field(default_factory=list)
    bytes_total: int = 0
    duplicate_filenames: dict[str, list[str]] = field(default_factory=dict)
    duplicate_hashes: dict[str, list[str]] = field(default_factory=dict)

    def report(self) -> str:
        lines = ["Zotero inventory", "  regular items by type:"]
        lines += [f"    {t:20} {n:6}" for t, n in self.items_by_type.most_common()]
        lines.append("  planned documents by kind:")
        lines += [f"    {k:20} {n:6}" for k, n in self.planned_by_kind.most_common()]
        lines.append("  attachments by link mode:")
        lines += [
            f"    {k:20} {n:6}" for k, n in self.attachments_by_link_mode.most_common()
        ]
        lines.append("  attachments by MIME type:")
        lines += [
            f"    {k:40} {n:6}" for k, n in self.attachments_by_mime.most_common()
        ]
        lines.append(
            f"  text cache: {self.with_text_cache} with,"
            f" {self.without_text_cache} without"
        )
        lines.append(f"  bytes referenced: {self.bytes_total / 1e6:,.1f} MB")
        lines.append(f"  files missing on disk: {len(self.missing_files)}")
        lines += [f"    {k}  {p}" for k, p in self.missing_files[:20]]
        n_dup_files = sum(len(v) for v in self.duplicate_filenames.values())
        lines.append(
            "  filenames shared by several attachments:"
            f" {len(self.duplicate_filenames)} names over {n_dup_files} attachments"
        )
        if self.duplicate_hashes:
            n = sum(len(v) for v in self.duplicate_hashes.values())
            lines.append(
                f"  identical files (sha256): {len(self.duplicate_hashes)} hashes"
                f" over {n} attachments"
            )
        return "\n".join(lines)


def inventory(
    lib: Library, *, hash_files: bool = False, limit: int | None = None
) -> Inventory:
    """Dry-run census. Reads file sizes; reads file bytes only if ``hash_files``."""
    inv = Inventory()
    for item in regular_items(lib):
        inv.items_by_type[item.type] += 1
    by_name: dict[str, list[str]] = {}
    by_hash: dict[str, list[str]] = {}
    for n, p in enumerate(plan(lib)):
        if limit is not None and n >= limit:
            break
        inv.planned_by_kind[p.kind] += 1
        if p.kind == "attachment":
            z = p.meta["zotero"]
            inv.attachments_by_link_mode[str(z["link_mode"])] += 1
            inv.attachments_by_mime[p.mime] += 1
            if p.missing:
                inv.missing_files.append((p.key, str(p.path)))
                continue
            assert p.path is not None
            inv.bytes_total += p.path.stat().st_size
            if p.meta.get("text_source"):
                inv.with_text_cache += 1
            else:
                inv.without_text_cache += 1
            by_name.setdefault(p.path.name, []).append(p.key)
            if hash_files:
                by_hash.setdefault(store.sha256_file(p.path), []).append(p.key)
        elif p.kind == "url":
            inv.attachments_by_link_mode["linked_url"] += 1
    inv.duplicate_filenames = {k: v for k, v in by_name.items() if len(v) > 1}
    inv.duplicate_hashes = {k: v for k, v in by_hash.items() if len(v) > 1}
    return inv


# ------------------------------------------------------------------ writing


@dataclass
class Report:
    actions: Counter[str] = field(default_factory=Counter)
    edges: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [f"{k}={n}" for k, n in sorted(self.actions.items())]
        return (
            f"documents: {', '.join(parts) or 'none'}; edges added: {self.edges};"
            f" errors: {len(self.errors)}"
        )


def _read_cache_text(path: Path) -> str | None:
    cache = path.parent / FT_CACHE
    if not cache.is_file():
        return None
    text = cache.read_bytes().decode("utf-8", errors="replace").strip()
    return text or None


def title_repaired(meta: dict[str, Any]) -> bool:
    """A title written by the title pass or a person, not by this importer:
    a refresh from Zotero leaves it alone (``store.retitle``)."""
    src = meta.get("title_source")
    return bool(src) and src != "zotero"


def _merge_meta(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """``new`` wins, except the provenance lists, which accumulate."""
    merged = {**old, **new}
    oz, nz = old.get("zotero", {}), new.get("zotero", {})
    z = {**oz, **nz}
    for lst in ("keys", "items"):
        z[lst] = list(dict.fromkeys([*oz.get(lst, []), *nz.get(lst, [])]))
    z["modified"] = {**oz.get("modified", {}), **nz.get("modified", {})}
    merged["zotero"] = z
    if old.get("text_source") and not new.get("text_source"):
        merged["text_source"] = old["text_source"]
    return merged


def _seed_edges(con: sqlite3.Connection, p: Planned, doc_id: int, version: str) -> int:
    """``paper --authored_by--> author`` per creator; once per (title, author)."""
    if p.item is None or not p.title or p.kind not in EDGE_KINDS:
        return 0
    n = 0
    for c in p.item.creators:
        if c["type"] not in AUTHOR_CREATOR_TYPES or not c["name"]:
            continue
        edge = store.Edge(p.title, "paper", "authored_by", c["name"], "author")
        if store.find_edges(con, edge):
            continue
        store.link(
            con,
            edge,
            confidence="EXTRACTED",
            source_doc=doc_id,
            ontology_version=version,
            producer="zotero",
            run=p.key,
        )
        n += 1
    return n


class KnownKeys:
    """``{zotero key: doc id}`` answered by the store as asked, for the
    door, which cannot hold the whole index across requests."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    def __contains__(self, key: object) -> bool:
        return self.get(str(key)) is not None

    def __getitem__(self, key: str) -> int:
        doc_id = self.get(key)
        if doc_id is None:
            raise KeyError(key)
        return doc_id

    def __setitem__(self, key: str, doc_id: int) -> None:
        pass  # the store has it now

    def get(self, key: str) -> int | None:
        row = self.con.execute(
            "SELECT id FROM documents WHERE EXISTS"
            " (SELECT 1 FROM json_each(meta, '$.zotero.keys') WHERE value = ?)"
            " ORDER BY id LIMIT 1",
            (key,),
        ).fetchone()
        return int(row[0]) if row else None


def apply(
    con: sqlite3.Connection,
    p: Planned,
    known: Any,
    *,
    version: str,
    report: Report,
    data: bytes | None = None,
    text: str | None = None,
) -> str:
    """Write one planned document; return the action taken.

    ``known`` maps already-imported Zotero keys to document ids and is
    updated in place (a dict for a run over the library, ``KnownKeys``
    on the door). An attachment's ``data`` and cache ``text`` are read
    from ``p.path`` when not given (the door gets them from the client).
    """
    if p.missing:
        report.actions["missing"] += 1
        return "missing"
    meta = dict(p.meta)
    meta["zotero"] = {**meta["zotero"], "modified": {p.key: p.date_modified}}
    if p.key in known:
        doc_id = known[p.key]
        old = store.get_meta(con, doc_id)
        if old.get("zotero", {}).get("modified", {}).get(p.key) == p.date_modified:
            report.actions["skipped"] += 1
            return "skipped"
        store.set_meta(
            con,
            doc_id,
            _merge_meta(old, meta),
            title=None if title_repaired(old) else p.title,
            source_url=p.source_url,
        )
        report.actions["refreshed"] += 1
        return "refreshed"

    if p.path is not None:
        if data is None:
            data, text = load(p)
        original_path = str(p.path)
    else:
        assert p.text is not None
        data = p.text.encode("utf-8")
        text = p.text
        original_path = None
    assert data is not None
    if p.kind == "attachment" and text is None:
        meta["text_source"] = None
    r = store.register(
        con,
        data,
        mime=p.mime,
        title=p.title,
        source_url=p.source_url,
        meta=meta,
        original_path=original_path,
    )
    doc_id = r["doc_id"]
    if r["created"]:
        action = "created"
    else:
        action = "merged"
        store.set_meta(con, doc_id, _merge_meta(store.get_meta(con, doc_id), meta))
    if text is not None and not store.is_indexed(con, doc_id):
        store.index_text(con, doc_id, text)
    report.edges += _seed_edges(con, p, doc_id, version)
    known[p.key] = doc_id
    report.actions[action] += 1
    return action


def run(
    lib: Library,
    con: sqlite3.Connection,
    *,
    limit: int | None = None,
    log: Any = None,
) -> Report:
    """Import the library into the store. Safe to interrupt and re-run."""
    known = store.meta_index(con, "$.zotero.keys")
    version = ontology.current().version
    report = Report()
    for n, p in enumerate(plan(lib)):
        if limit is not None and n >= limit:
            break
        try:
            action = apply(con, p, known, version=version, report=report)
        except Exception as exc:  # noqa: BLE001 - keep going; the report lists failures
            report.errors.append((p.key, f"{type(exc).__name__}: {exc}"))
            action = "error"
        if log is not None:
            log(n, p, action)
    return report
