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
from .base import BatchStep, HandOut, Pass, TakeIn, ready, resting


class Faces(BatchStep):
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
        return self.batches(p)

    def batch(
        self, p: Pass, items: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        p.note(f"{self.name}: {len(items)} pictures", total=len(items), done=0)
        return {"results": [self._read(p, it) for it in items]}, {}

    def line(self, tally: dict[str, int], first: str) -> str:
        return (
            f"{tally['applied']} pictures ({tally.get('found', 0)} with faces,"
            f" {tally.get('none', 0)} without, {tally['errors']} not read{first})"
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


class FaceVectors(BatchStep):
    """What each face looks like (plan item AT step 2): the door hands out
    the pictures holding faces with no vector, the worker aligns each face
    in the preview, gives it the door's recogniser's numbers (``faces.model``,
    SFace's 128 by default; the hand-out names it) and a small crop, and
    the door adds the vectors to the face index
    (``store.apply_face_vectors``)."""

    name = "facevectors"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        skip = frozenset(leases.leased(self.name))
        model = store.face_model()
        due = store.face_vectors_due(h.con, model, limit=h.limit, skip=skip)
        if not due:
            _save(force=True)
        h.lease([d["doc_id"] for d in due])
        items = [{**d, "preview": f"/doc/{d['doc_id']}/preview"} for d in due]
        return {**h.batch(items), "model": model}

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        model = str(t.payload.get("model") or "")
        if model != (mine := store.face_model()):
            raise ValueError(f"the door's faces are {mine}, not {model!r}")

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
        return self.batches(p)

    def batch(
        self, p: Pass, items: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        n = sum(len(it["faces"]) for it in items)
        p.note(f"{self.name}: {n} faces", total=n, done=0)
        model = faces.recognizer(self.handed.get("model")).name  # the door's
        results = [self._read(p, it, model) for it in items]
        body = {"model": model, "results": results}
        return body, {"faces": sum(len(r.get("faces") or []) for r in results)}

    def line(self, tally: dict[str, int], first: str) -> str:
        return (
            f"{tally.get('faces', 0)} faces in {tally['applied']} pictures"
            f" ({tally['errors']} not read{first})"
        )

    @staticmethod
    def _read(p: Pass, it: dict[str, Any], model: str) -> dict[str, Any]:
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
                    "vector": faces.vector(faces.align(image, points, model), model),
                    "crop": base64.b64encode(faces.crop(image, f["box"])).decode(
                        "ascii"
                    ),
                }
            )
        return {**out, "faces": read}


REGISTERED = {"faces": Faces(), "facevectors": FaceVectors()}
