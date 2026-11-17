"""The transcribe step: what a recording says (``prax.ml.speech``,
``store.apply_transcript``; plan item AQ stage 4).

The door hands out the recordings it reaches whose transcript is not of
this version; the worker downloads each original through the door into a
file of its own (a phone's video is gigabytes), reads its sound with
ffmpeg, finds the speech, singing and music in it and writes the words
of the first two, and posts the text and what it found. A recording never
leaves the host: no paid model, no API, as for a face.

The models take some 2 GB of the card. They run in a process of their
own for each batch (``speech.read_files``), so the resident worker holds
neither them nor torch between batches; a card without that room makes
the pass wait for the next
(``transcribe.device``: ``cuda``, the default; ``auto`` reads on the CPU
then; ``cpu`` always does).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from prax import config, store
from prax.ml import speech

from . import leases
from .base import BatchStep, HandOut, Pass, TakeIn

# a recording may take minutes: a batch is a few, so a lease outlives it
BATCH_MOST = 2


class Transcribe(BatchStep):
    name = "transcribe"

    def __init__(self) -> None:
        self.where = "cpu"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        skip = frozenset(leases.leased(self.name))
        due = store.transcripts_due(
            h.con, speech.VERSION, limit=min(h.limit, BATCH_MOST), skip=skip
        )
        h.lease(due)
        return h.batch([{"doc_id": d, "original": f"/doc/{d}/original"} for d in due])

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        def apply(doc_id: int, r: dict[str, Any]) -> str:
            by = str(r.get("by") or t.worker)
            version = int(r.get("v") or speech.VERSION)
            if r.get("unreadable"):
                return store.transcript_failed(
                    t.con, doc_id, error=str(r["unreadable"]), version=version, by=by
                )
            return store.apply_transcript(
                t.con,
                doc_id,
                text=str(r.get("text") or ""),
                facts=dict(r.get("facts") or {}),
                version=version,
                by=by,
            )

        # an ``error`` (the original could not be fetched) is released and
        # not kept: the recording is handed out again once it is reachable
        t.out["actions"] = t.each(apply)
        return t.out

    def run(self, p: Pass) -> str | None:
        if not speech.available():
            return None  # no ffmpeg or no torch here: another worker's step
        where = speech.device(
            config.setting("transcribe.device", "PRAX_TRANSCRIBE_DEVICE", "cuda")
        )
        if where is None:
            return None  # the card is full: the next pass looks again
        self.where = where
        return self.batches(p)

    def batch(
        self, p: Pass, items: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """The batch's originals fetched into files of their own (a phone's
        video is gigabytes), then read together by one process."""
        p.note(f"{self.name}: {len(items)} recordings", total=len(items), done=0)
        stamp = {"v": speech.VERSION, "by": speech.stamp()}
        results: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="prax-rec-") as tmp:
            fetched: list[tuple[dict[str, Any], Path]] = []
            for i, it in enumerate(items):
                path = Path(tmp) / f"original-{i}"
                try:
                    p.door.download(it["original"], path)
                except Exception as exc:  # noqa: BLE001 - one recording must not stop the rest
                    results.append({"doc_id": it["doc_id"], "error": f"fetch: {exc}"})
                    continue
                fetched.append((it, path))
            if fetched:
                try:
                    read = speech.read_files([f for _, f in fetched], self.where)
                except Exception as exc:  # noqa: BLE001 - released: read next pass
                    read = [{"error": f"read: {str(exc)[:300]}"} for _ in fetched]
                for (it, _), r in zip(fetched, read, strict=True):
                    results.append({"doc_id": it["doc_id"], **stamp, **r})
        return {"results": results}, {}

    def line(self, tally: dict[str, int], first: str) -> str:
        return (
            f"{tally['applied']} recordings ({tally.get('read', 0)} read,"
            f" {tally.get('silent', 0)} with nothing heard,"
            f" {tally.get('failed', 0)} unreadable,"
            f" {tally['errors']} not fetched{first})"
        )


REGISTERED = {"transcribe": Transcribe()}
