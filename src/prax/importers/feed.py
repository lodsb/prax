"""What the door-side importers share: an ``Item`` — one thing a source
offers, as text to keep or as a link for the door to fetch — and ``run``,
which sends a source's items through the door and skips what it already
holds.

These importers are clients of the door (``prax.client.Door``): they open
no database and run wherever the ``prax`` command runs, against the door
on this machine or another. Idempotence is by key: a text item carries
``meta.<source>.key`` (a repository's full name, a conversation's month)
and the run lists the documents of its source first; a link asks
``GET /captures`` before fetching. ``refresh`` re-sends an item whose
``version`` changed (a repository pushed to, a month with new messages)
and retires the earlier document as replaced by the new one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from prax.client import Door, DoorError

Log = Callable[[str], None]


@dataclass
class Item:
    """One thing a source offers. ``kind = "text"``: a document of the
    importer's own (Markdown), stamped ``meta.source = <source>`` and
    ``meta.<source>`` with the key and whatever the source knew; ``fill``,
    when given, is called just before sending and completes the text (a
    README is one request each, spent only on what is new). ``kind =
    "link"``: fetched by the door as a capture (``by = import:<source>``)
    with the tags and the note."""

    key: str
    kind: str = "text"  # text | link
    title: str | None = None
    url: str | None = None
    text: str | None = None
    tags: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    version: str | None = None  # what changes when the source's item changes
    note: str | None = None
    fill: Callable[[Item], None] | None = None

    @property
    def is_link(self) -> bool:
        return self.kind == "link"


@dataclass
class Report:
    source: str
    added: int = 0
    refreshed: int = 0
    seen: int = 0  # already in the library, left alone
    same: int = 0  # sent, but the door already had those exact bytes
    failed: list[tuple[str, str]] = field(default_factory=list)
    planned: list[Item] = field(default_factory=list)  # a dry run's answer

    def __str__(self) -> str:
        parts = [f"{self.added} added"]
        if self.refreshed:
            parts.append(f"{self.refreshed} refreshed")
        if self.seen:
            parts.append(f"{self.seen} already there")
        if self.same:
            parts.append(f"{self.same} same as an existing document")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return f"{self.source}: " + ", ".join(parts)


def existing(door: Door, source: str) -> dict[str, dict[str, Any]]:
    """The library's live documents of ``source`` by key: ``{key: {"doc_id",
    "version"}}``."""
    out: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        page = door.get_json(
            "/documents", {"source": source, "limit": 500, "offset": offset}
        )
        items = page.get("items") or []
        for row in items:
            own = (row.get("meta") or {}).get(source) or {}
            key = own.get("key")
            if key and key not in out:
                out[key] = {"doc_id": row["id"], "version": own.get("version")}
        offset += len(items)
        if not items or offset >= page.get("total", 0):
            break
    return out


def held_capture(door: Door, url: str) -> int | None:
    """The newest live capture of ``url`` in the library, or None."""
    try:
        rows = door.get_json("/captures", {"url": url})
    except DoorError as exc:
        if exc.status == 400:  # not an http(s) URL
            return None
        raise
    return rows[-1]["doc_id"] if rows else None


def run(
    door: Door,
    source: str,
    items: Iterable[Item],
    *,
    domains: list[str] | None = None,
    tags: list[str] | None = None,
    refresh: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    log: Log | None = None,
) -> Report:
    """Send a source's items through the door. ``domains`` and ``tags``
    go on every document; ``limit`` caps what is sent (not what is
    skipped); ``dry_run`` lists what would be sent in ``planned``."""
    say = log or (lambda _: None)
    report = Report(source)
    known: dict[str, dict[str, Any]] | None = None
    sent = 0
    for item in items:
        if limit is not None and sent >= limit:
            break
        old: int | None = None
        if item.is_link:
            if not item.url:
                report.failed.append((item.key, "a link without a URL"))
                continue
            old = held_capture(door, item.url)
            if old is not None and not refresh:
                report.seen += 1
                continue
        else:
            if known is None:
                known = existing(door, source)
            have = known.get(item.key)
            if have is not None:
                if not refresh or (item.version and have["version"] == item.version):
                    report.seen += 1
                    continue
                old = have["doc_id"]
        if dry_run:
            report.planned.append(item)
            sent += 1
            continue
        try:
            if item.fill is not None:
                item.fill(item)
            result = _send(door, source, item, domains=domains, tags=tags)
        except DoorError as exc:
            report.failed.append(
                (item.key, exc.detail or f"door answered {exc.status}")
            )
            say(f"  failed: {item.key}: {exc.detail}")
            continue
        sent += 1
        doc_id = result["doc_id"]
        if not result.get("created", True):
            report.same += 1
            say(f"  same: {item.key} (doc {doc_id})")
            continue
        if old is not None and old != doc_id:
            # a document of our own gives way to its newer self; a page
            # captured again is a new version the inbox has already linked
            # to the earlier one (meta.previous_capture)
            if not item.is_link:
                door.post_json(
                    f"/doc/{old}/retire",
                    {
                        "reason": f"replaced by a newer import from {source}",
                        "duplicate_of": doc_id,
                    },
                )
            report.refreshed += 1
            say(f"  refreshed: {item.key} (doc {doc_id}, was {old})")
        else:
            report.added += 1
            say(f"  added: {item.key} (doc {doc_id})")
    return report


def _send(
    door: Door,
    source: str,
    item: Item,
    *,
    domains: list[str] | None,
    tags: list[str] | None,
) -> dict[str, Any]:
    all_tags = [t for t in [*(tags or []), *item.tags] if t]
    if item.is_link:
        return door.post_json(
            "/ingest/url",
            {
                "url": item.url,
                "title": item.title,
                "domains": domains or None,
                "tags": all_tags or None,
                "by": f"import:{source}",
                "note": item.note,
            },
        )
    own = {"key": item.key, **item.meta}
    if item.version:
        own["version"] = item.version
    meta: dict[str, Any] = {"source": source, source: own}
    if all_tags:
        meta["tags"] = all_tags
    return door.post_json(
        "/ingest",
        {
            "text": item.text,
            "title": item.title,
            "source_url": item.url,
            "meta": meta,
            "domains": domains or None,
        },
    )
