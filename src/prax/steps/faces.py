"""The faces step: where the faces in a picture are (``prax.ml.faces``,
``store.apply_faces``; plan item AT step 1).

The door hands out the pictures the photos step made a preview of; the
worker fetches each preview through the door, finds the faces with YuNet
on its CPU and posts their boxes and landmarks back. Nothing is named
here: grouping and naming are later steps. A face never leaves the host:
no paid model, no API.
"""

from __future__ import annotations

import io
import time
from dataclasses import asdict
from typing import Any

from prax import store
from prax.ml import faces

from . import leases
from .base import HandOut, Pass, Step, TakeIn

# a worker's pass asks for batch after batch for this long, as the photos
# step does: a preview is read in some 45 ms (2026-10-27)
RUN_SECONDS = 90


class Faces(Step):
    name = "faces"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        skip = frozenset(leases.leased(self.name))
        due = store.faces_due(h.con, faces.VERSION, limit=h.limit, skip=skip)
        h.lease(due)
        return h.batch([{"doc_id": d, "preview": f"/doc/{d}/preview"} for d in due])

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        def apply(doc_id: int, r: dict[str, Any]) -> str:
            return store.apply_faces(
                t.con,
                doc_id,
                faces=list(r.get("faces") or []),
                version=int(r.get("v") or faces.VERSION),
                by=str(r.get("by") or t.worker),
                width=int(r.get("width") or 0),
                height=int(r.get("height") or 0),
            )

        # an ``error`` (the preview could not be fetched or opened) is
        # released and not kept: the picture is handed out again
        t.out["actions"] = t.each(apply)
        return t.out

    def run(self, p: Pass) -> str | None:
        if not faces.available():
            return None
        start = time.monotonic()
        tally = {"applied": 0, "found": 0, "none": 0, "errors": 0}
        while time.monotonic() - start < RUN_SECONDS:
            items = p.fetch(self.name).get("items") or []
            if not items:
                break
            p.note(f"{self.name}: {len(items)} pictures", total=len(items), done=0)
            rep = p.post(self.name, {"results": [self._read(p, it) for it in items]})
            acts = rep.get("actions") or {}
            tally["applied"] += int(rep.get("applied", 0))
            tally["found"] += int(acts.get("found", 0))
            tally["none"] += int(acts.get("none", 0))
            tally["errors"] += len(rep.get("errors") or [])
            if tally["errors"] and not tally["applied"]:
                break  # nothing lands: the next pass tries again
        if not any(tally.values()):
            return None
        return (
            f"{tally['applied']} pictures ({tally['found']} with faces,"
            f" {tally['none']} without, {tally['errors']} not read)"
        )

    @staticmethod
    def _read(p: Pass, it: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image

        out: dict[str, Any] = {
            "doc_id": it["doc_id"],
            "v": faces.VERSION,
            "by": faces.stamp(),
        }
        try:
            data = p.door.get_bytes(it["preview"])
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as exc:  # noqa: BLE001 - one picture must not stop the rest
            return {**out, "error": f"preview: {exc}"}
        found = faces.detect(image)
        return {
            **out,
            "width": image.width,
            "height": image.height,
            "faces": [asdict(f) for f in found],
        }


REGISTERED = {"faces": Faces()}
