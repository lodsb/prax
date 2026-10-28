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
from dataclasses import asdict
from typing import Any

from prax import store
from prax.graph import gazetteer, vocabulary
from prax.parsers import photos
from prax.text import language

from . import leases
from .base import BatchStep, HandOut, Pass, TakeIn, ready, resting


class Photos(BatchStep):
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
        return self.batches(p)

    def batch(
        self, p: Pass, items: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        p.note(f"{self.name}: {len(items)} pictures", total=len(items), done=0)
        by = photos.stamp()
        return {"results": [self._read(p, it, by) for it in items]}, {}

    def line(self, tally: dict[str, int], first: str) -> str:
        return (
            f"{tally['applied']} pictures ({tally.get('read', 0)} read,"
            f" {tally.get('failed', 0)} unreadable,"
            f" {tally['errors']} not fetched{first})"
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
        except Exception as exc:  # noqa: BLE001 - a hostile file is that file's
            return {**out, "unreadable": str(exc)[:300]}
        return {
            **out,
            "facts": got["facts"],
            **{k: base64.b64encode(got[k]).decode("ascii") for k in store.PHOTO_COPIES},
        }


@functools.cache
def _gazetteer() -> gazetteer.Gazetteer:
    """Read once a worker: some 160,000 towns, a few seconds."""
    return gazetteer.load()


class Geocode(BatchStep):
    """Where a picture was taken: the door hands out the positions the
    photos step read, the worker names the town each lies in, and the
    door writes it as facts (``store.apply_place``). No original is read,
    and no model runs."""

    name = "geocode"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        skip = frozenset(leases.leased(self.name))
        due = store.places_due(h.con, gazetteer.VERSION, limit=h.limit, skip=skip)
        h.lease([d["doc_id"] for d in due])
        # the languages a place is named in besides its own: the library's
        # and the ones its labels are given in (prax.graph.vocabulary)
        langs = sorted({language.canonical(), *vocabulary.label_languages(h.con)})
        return {**h.batch(due), "langs": langs}

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
        if not gazetteer.available() or resting(self.name):
            return None  # offline and no files: nothing to name a place by
        if (trouble := ready(self.name, _gazetteer)) is not None:
            return trouble
        return self.batches(p)

    def batch(
        self, p: Pass, items: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        p.note(f"{self.name}: {len(items)} positions", total=len(items), done=0)
        places = _gazetteer()
        langs = [str(lang) for lang in self.handed.get("langs") or []]
        results = []
        for it in items:
            found = places.nearest(float(it["lat"]), float(it["lon"]))
            place = asdict(found) if found else None
            if found is not None and langs:
                place = {**(place or {}), "names": places.names(found, langs)}
            results.append(
                {
                    "doc_id": it["doc_id"],
                    "v": gazetteer.VERSION,
                    "by": gazetteer.stamp(),
                    "place": place,
                }
            )
        return {"results": results}, {}

    def line(self, tally: dict[str, int], first: str) -> str:
        return (
            f"{tally['applied']} positions ({tally.get('placed', 0)} placed,"
            f" {tally.get('nowhere', 0)} near no town,"
            f" {tally['errors']} not placed{first})"
        )


REGISTERED = {"photos": Photos(), "geocode": Geocode()}
