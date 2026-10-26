"""The photos step: a picture's EXIF and its two smaller copies
(``prax.parsers.photos``, ``store.apply_photo``; ``docs/biography.md`` §7).
Beside it the geocode step: the town a picture's GPS position lies in,
as facts of the graph (``prax.graph.gazetteer``, ``store.apply_place``).

No model: the worker fetches each picture's original through the door,
which reads a referenced one from its share, and posts what it found and
the two JPEG copies back. The door hands out only pictures it reaches, so
an unmounted share costs nothing and its pictures wait for it. Over every
picture, not over captures, so scope does not apply: a referenced
photo is never a capture.
"""

from __future__ import annotations

import base64
import functools
import time
from dataclasses import asdict
from typing import Any

from prax import store
from prax.graph import gazetteer
from prax.parsers import photos

from . import leases
from .base import HandOut, Pass, Step, TakeIn

# a worker's pass asks for batch after batch for this long: one batch of
# ten a pass read 14,000 photos of a first crawl at some ten a minute
# (2026-10-26), and a picture is a quick read
RUN_SECONDS = 90


class Photos(Step):
    name = "photos"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        skip = frozenset(leases.leased(self.name))
        due = store.photos_due(h.con, photos.VERSION, limit=h.limit, skip=skip)
        items = [{"doc_id": d, "original": f"/doc/{d}/original"} for d in due]
        h.lease(due)
        return h.batch(items)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        def apply(doc_id: int, r: dict[str, Any]) -> str:
            by = str(r.get("by") or t.worker)
            version = int(r.get("v") or photos.VERSION)
            if r.get("unreadable"):
                return store.photo_failed(
                    t.con, doc_id, error=str(r["unreadable"]), version=version, by=by
                )
            copies = {k: base64.b64decode(r[k]) for k in store.PHOTO_COPIES if r.get(k)}
            return store.apply_photo(
                t.con,
                doc_id,
                facts=dict(r.get("facts") or {}),
                copies=copies,
                version=version,
                by=by,
            )

        # an ``error`` (the original could not be fetched: a share gone
        # between the hand-out and the read) is released and not kept, so
        # the picture is handed out again once it is reachable
        t.out["actions"] = t.each(apply)
        return t.out

    def run(self, p: Pass) -> str | None:
        if not photos.available():
            return None  # no HEIC reader here: a phone's photos would all fail
        by = photos.stamp()
        start = time.monotonic()
        tally = {"applied": 0, "read": 0, "failed": 0, "errors": 0}
        while time.monotonic() - start < RUN_SECONDS:
            items = p.fetch(self.name).get("items") or []
            if not items:
                break
            p.note(f"{self.name}: {len(items)} pictures", total=len(items), done=0)
            rep = p.post(
                self.name, {"results": [self._read(p, it, by) for it in items]}
            )
            acts = rep.get("actions") or {}
            tally["applied"] += int(rep.get("applied", 0))
            tally["read"] += int(acts.get("read", 0))
            tally["failed"] += int(acts.get("failed", 0))
            tally["errors"] += len(rep.get("errors") or [])
            if tally["errors"] and not tally["applied"]:
                break  # nothing lands: the next pass tries again
        if not any(tally.values()):
            return None
        return (
            f"{tally['applied']} pictures ({tally['read']} read,"
            f" {tally['failed']} unreadable, {tally['errors']} not fetched)"
        )

    @staticmethod
    def _read(p: Pass, it: dict[str, Any], by: str) -> dict[str, Any]:
        out: dict[str, Any] = {"doc_id": it["doc_id"], "v": photos.VERSION, "by": by}
        try:
            data = p.door.get_bytes(it["original"])
        except Exception as exc:  # noqa: BLE001 - one picture must not stop the rest
            return {**out, "error": f"fetch: {exc}"}
        try:
            got = photos.read(data)
        except ValueError as exc:
            return {**out, "unreadable": str(exc)}
        return {
            **out,
            "facts": got["facts"],
            **{k: base64.b64encode(got[k]).decode("ascii") for k in store.PHOTO_COPIES},
        }


@functools.cache
def _gazetteer() -> gazetteer.Gazetteer:
    """Read once a worker: some 160,000 towns, a few seconds."""
    return gazetteer.load()


class Geocode(Step):
    """Where a picture was taken: the door hands out the positions the
    photos step read, the worker names the town each lies in, and the
    door writes it as facts (``store.apply_place``). No original is read,
    and no model runs."""

    name = "geocode"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        skip = frozenset(leases.leased(self.name))
        due = store.places_due(h.con, gazetteer.VERSION, limit=h.limit, skip=skip)
        h.lease([d["doc_id"] for d in due])
        return h.batch(due)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        def apply(doc_id: int, r: dict[str, Any]) -> str:
            return store.apply_place(
                t.con,
                doc_id,
                place=r.get("place"),
                version=int(r.get("v") or gazetteer.VERSION),
                by=str(r.get("by") or t.worker),
            )

        t.out["actions"] = t.each(apply)
        return t.out

    def run(self, p: Pass) -> str | None:
        if not gazetteer.available():
            return None  # offline and no files: nothing to name a place by
        start = time.monotonic()
        tally = {"applied": 0, "placed": 0, "nowhere": 0, "errors": 0}
        while time.monotonic() - start < RUN_SECONDS:
            items = p.fetch(self.name).get("items") or []
            if not items:
                break
            p.note(f"{self.name}: {len(items)} positions", total=len(items), done=0)
            places = _gazetteer()
            results = []
            for it in items:
                found = places.nearest(float(it["lat"]), float(it["lon"]))
                results.append(
                    {
                        "doc_id": it["doc_id"],
                        "v": gazetteer.VERSION,
                        "by": gazetteer.stamp(),
                        "place": asdict(found) if found else None,
                    }
                )
            rep = p.post(self.name, {"results": results})
            acts = rep.get("actions") or {}
            tally["applied"] += int(rep.get("applied", 0))
            tally["placed"] += int(acts.get("placed", 0))
            tally["nowhere"] += int(acts.get("nowhere", 0))
            tally["errors"] += len(rep.get("errors") or [])
            if tally["errors"] and not tally["applied"]:
                break
        if not any(tally.values()):
            return None
        return (
            f"{tally['applied']} positions ({tally['placed']} placed,"
            f" {tally['nowhere']} near no town)"
        )


REGISTERED = {"photos": Photos(), "geocode": Geocode()}
