"""The faces step: where the faces in a picture are (``prax.ml.faces``,
``store.apply_faces``; plan item AT step 1).

The door hands out the pictures the photos step made a preview of; the
worker fetches each preview through the door, finds the faces with YuNet
on its CPU and posts their boxes and landmarks back. Nothing is named
here: grouping and naming are later steps. A face never leaves the host:
no paid model, no API.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import asdict
from typing import Any

from prax import store
from prax.ml import faces

from . import leases
from .base import HandOut, Pass, Step, TakeIn, ready, resting

# a worker's pass asks for batch after batch for this long, as the photos
# step does: a preview is read in some 45 ms (2026-10-27)
RUN_SECONDS = 90


def _first_error(rep: dict[str, Any]) -> str:
    """The first error the door answered a post with, for the pass's line."""
    errors = rep.get("errors") or []
    if not errors:
        return ""
    e = errors[0]
    return f"; the first, doc {e.get('doc_id')}: {str(e.get('error'))[:160]}"


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
        if not faces.available() or resting(self.name):
            return None
        if (trouble := ready(self.name, faces._session)) is not None:
            return trouble
        start = time.monotonic()
        tally = {"applied": 0, "found": 0, "none": 0, "errors": 0}
        first = ""
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
            first = first or _first_error(rep)
            if tally["errors"] and not tally["applied"]:
                break  # nothing lands: the next pass tries again
        if not any(tally.values()):
            return None
        return (
            f"{tally['applied']} pictures ({tally['found']} with faces,"
            f" {tally['none']} without, {tally['errors']} not read{first})"
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


# the door writes the face index's delta when this long has passed since
# it last did, or when nothing more is due: a save rewrites the whole
# delta, a few megabytes by the end of a library's first read
SAVE_SECONDS = 30.0
_saved_at = {"at": 0.0, "unsaved": 0}


def _save(*, force: bool = False) -> None:
    if not _saved_at["unsaved"]:
        return
    if force or time.monotonic() - _saved_at["at"] >= SAVE_SECONDS:
        store.save_face_vectors()
        _saved_at.update(at=time.monotonic(), unsaved=0)


class FaceVectors(Step):
    """What each face looks like (plan item AT step 2): the door hands out
    the pictures holding faces with no vector, the worker aligns each face
    in the preview, gives it SFace's 128 numbers and a small crop, and the
    door adds the vectors to the face index (``store.apply_face_vectors``)."""

    name = "facevectors"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        skip = frozenset(leases.leased(self.name))
        due = store.face_vectors_due(h.con, store.FACE_MODEL, limit=h.limit, skip=skip)
        if not due:
            _save(force=True)
        h.lease([d["doc_id"] for d in due])
        return h.batch([{**d, "preview": f"/doc/{d['doc_id']}/preview"} for d in due])

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        model = str(t.payload.get("model") or "")
        if model != store.FACE_MODEL:
            raise ValueError(f"the door's faces are {store.FACE_MODEL}, not {model!r}")

        def apply(doc_id: int, r: dict[str, Any]) -> str:
            got = store.apply_face_vectors(
                t.con,
                doc_id,
                faces=[
                    {
                        **f,
                        "crop": base64.b64decode(f["crop"]) if f.get("crop") else None,
                    }
                    for f in r.get("faces") or []
                ],
                model=model,
            )
            _saved_at["unsaved"] += len(r.get("faces") or [])
            return got

        t.out["actions"] = t.each(apply)
        _save()
        return t.out

    def run(self, p: Pass) -> str | None:
        if not faces.available() or resting(self.name):
            return None
        if (trouble := ready(self.name, faces._recognizer)) is not None:
            return trouble
        start = time.monotonic()
        tally = {"applied": 0, "faces": 0, "errors": 0}
        first = ""
        while time.monotonic() - start < RUN_SECONDS:
            items = p.fetch(self.name).get("items") or []
            if not items:
                break
            n = sum(len(it["faces"]) for it in items)
            p.note(f"{self.name}: {n} faces", total=n, done=0)
            results = [self._read(p, it) for it in items]
            rep = p.post(self.name, {"model": faces.RECOGNIZER, "results": results})
            tally["applied"] += int(rep.get("applied", 0))
            tally["faces"] += sum(len(r.get("faces") or []) for r in results)
            tally["errors"] += len(rep.get("errors") or [])
            first = first or _first_error(rep)
            if tally["errors"] and not tally["applied"]:
                break
        if not any(tally.values()):
            return None
        return (
            f"{tally['faces']} faces in {tally['applied']} pictures"
            f" ({tally['errors']} not read{first})"
        )

    @staticmethod
    def _read(p: Pass, it: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image

        out: dict[str, Any] = {"doc_id": it["doc_id"]}
        try:
            data = p.door.get_bytes(it["preview"])
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as exc:  # noqa: BLE001 - one picture must not stop the rest
            return {**out, "error": f"preview: {exc}"}
        w, h = image.size
        read = []
        for f in it["faces"]:
            points = [(x * w, y * h) for x, y in f["landmarks"]]
            read.append(
                {
                    "id": f["id"],
                    "vector": faces.vector(faces.align(image, points)),
                    "crop": base64.b64encode(faces.crop(image, f["box"])).decode(
                        "ascii"
                    ),
                }
            )
        return {**out, "faces": read}


REGISTERED = {"faces": Faces(), "facevectors": FaceVectors()}
