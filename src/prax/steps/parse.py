"""The parse step: the readings a person or the door asked for, then the
captures waiting for their text; the worker reads each and the door
files the text and asks for what follows (``pipeline.follow_ups``)."""

from __future__ import annotations

from typing import Any

from prax import inbox, mimes, models, pipeline, store
from prax.parsers import queue

from . import READING_STEPS
from .base import HandOut, Pass, Step, TakeIn


def vision_is_free() -> bool:
    """Whether a reading by the vision model costs nothing (a local
    server): the readings the door asks for on its own are only those."""
    return pipeline.vision_is_free()


def ask_reading(con: Any, doc_id: int, extractor: str) -> bool:
    """A reading request the door places for a capture (an image to
    describe, figures to read) when the model is free and nobody asked
    for one yet."""
    meta = store.get_meta(con, doc_id)
    if meta.get("reading"):
        return False
    store.request_reading(con, doc_id, extractor, by="door")
    return True


def nothing_to_read(con: Any, doc_id: int, req: dict[str, Any]) -> bool:
    """Whether a figures request has nothing left for the vision model:
    every figure it would read is read. Only the figures reading knows
    this cheaply; every other reading is handed out as it stands."""
    if req.get("extractor") != "figures":
        return False
    try:
        spec = models.resolve("vision")
    except Exception:  # noqa: BLE001 - a config error is not this queue's
        return False
    if spec is None:
        return False
    every = str(req.get("mode") or "") == "all"
    return store.figures_to_read(con, doc_id, model=spec.runtime_name, every=every) == 0


def takes_previous(extractor: str) -> bool:
    from prax import parsers

    try:
        return parsers.by_name(extractor).previous
    except KeyError:
        return False


def reading_step(extractor: str) -> str:
    """The step a reading ran, for the ledger's row: the vision step for
    a figure or a page, and the reading's own where it has one."""
    return READING_STEPS.get(extractor, "vision")


def _file_name(path: str | None) -> str | None:
    return path.replace("\\", "/").rsplit("/", 1)[-1] if path else None


class Parse(Step):
    name = "parse"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        from prax import parsers

        items: list[dict[str, Any]] = []
        # requested readings first: a person asked, whatever the scope; the
        # whole queue, oldest first — the status view's newest fifty hid
        # the 228 marker requests behind the follow-ups placed after them
        offered: set[int] = set()  # one reading a document a batch
        # a reading that runs no model goes first, whenever it was asked
        # for: it costs seconds of CPU, and it makes the pictures the
        # expensive readings then read. Among equals, oldest first, so a
        # burst of new requests never hides the ones behind it
        requests = sorted(
            store.reading_requests(h.con, limit=None, oldest_first=True),
            key=lambda r: (r["extractor"] in READING_STEPS, r["id"]),
        )
        for req in requests:
            doc_id = req["doc_id"]
            if len(items) >= h.limit or not h.free(doc_id):
                continue
            if doc_id in offered:
                # its other readings wait their turn: two annotating
                # readings of one document in a batch are computed from
                # the same text, and the second would undo the first
                continue
            doc = store.get_document(h.con, doc_id, max_chars=0)
            if doc is None:
                continue
            if nothing_to_read(h.con, doc_id, req):
                # every figure this model reads has been read: the request
                # would cost a fetch, a parse and a round trip to come back
                # "same". Half the queue was this on 2026-09-23
                store.cancel_reading(h.con, doc_id)
                continue
            item = {
                "doc_id": doc_id,
                "mime": doc["mime"],
                "filename": _file_name(doc.get("original_path")),
                "original": f"/doc/{doc_id}/original",
                "old_len": doc["text_len"],
                "extractor": req["extractor"],
                "mode": req.get("mode"),
                "force": True,
            }
            if doc["text_len"] and takes_previous(req["extractor"]):
                # the extractor works on the current text: a second reading
                # of an image joins the first (vision.merge_readings), the
                # figures' readings and references go into the parsed text
                held = store.get_document(h.con, doc_id)
                item["previous"] = held["text"] if held else None
            items.append(item)
            offered.add(doc_id)
        waiting = list(inbox.pending_captures(h.con))
        if h.scope == "all":
            # the backlog pass also brings texts up to date: documents read
            # by an extractor prax has revised since, a few per pass
            waiting += [
                i for i in queue.stale(h.con, limit=h.limit) if i not in waiting
            ]
        for doc_id in waiting:
            if len(items) >= h.limit or not h.free(doc_id):
                continue
            from prax import work

            if not work._in_scope(h.con, doc_id, h.scope):
                continue
            doc = store.get_document(h.con, doc_id, max_chars=0)
            if doc is None:
                continue
            named = doc["meta"].get("parser")  # a document may name its parser
            exts = parsers.candidates(doc["mime"] or "", named)
            if not exts:
                # an image has no parser of its own: the vision model reads
                # it, as a reading the door asks for when that is free
                if mimes.is_picture(doc["mime"]) and vision_is_free():
                    ask_reading(h.con, doc_id, "vision")
                continue
            if any(queue._seen(doc["meta"], e.stamp) for e in exts):
                # the chain was run and found nothing (a scan without a
                # text layer): a reading asked for on its page (OCR, the
                # vision model) is the way on, not another round
                continue
            items.append(
                {
                    "doc_id": doc_id,
                    "mime": doc["mime"],
                    "filename": _file_name(doc.get("original_path")),
                    "original": f"/doc/{doc_id}/original",
                    "old_len": doc["text_len"],
                    **({"extractor": named} if named else {}),
                }
            )
        h.lease([i["doc_id"] for i in items])
        return h.batch(items)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        """A reading's error is its outcome here, which ``apply_parse``
        records, rather than a result to set aside."""
        from prax import work

        def apply(doc_id: int, r: dict[str, Any]) -> str:
            stamp = str(r.get("extractor") or t.worker)
            action = queue.apply_parse(
                t.con,
                doc_id,
                stamp=stamp,
                text=r.get("text"),
                error=r.get("error"),
                seconds=float(r.get("seconds") or 0.0),
                force=bool(r.get("force")),
                keep_source=bool(r.get("keep_source")),
                pages=int(r["pages"]) if r.get("pages") else None,
            )
            # what the reading paid for, if anything: the worker carries the
            # tokens home (prax.usage) because it never writes itself
            for model, tokens in (r.get("usage") or {}).items():
                work._note_spend(
                    t.con,
                    reading_step(str(r.get("requested") or "")),
                    tokens,
                    doc_id=doc_id,
                    model=str(model),
                )
            if r.get("requested"):
                store.finish_reading(
                    t.con, doc_id, outcome=action, stamp=stamp, error=r.get("error")
                )
            # the edges of the process graph: what the door asks to be read
            # next (pipeline.follow_ups: the formulas of a marker read, the
            # polish of an automatic transcript, the figures of a capture)
            pipeline.follow_ups(t.con, doc_id, stamp=stamp, action=action)
            return action

        actions: dict[str, int] = {}
        for r in t.results:
            doc_id = int(r["doc_id"])
            t.release([doc_id])
            try:
                action = apply(doc_id, r)
            except Exception as exc:  # noqa: BLE001 - one result must not stop the rest
                t.out["errors"].append(
                    {"doc_id": doc_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            actions[action] = actions.get(action, 0) + 1
            t.out["applied"] += 1
        t.out["actions"] = actions
        return t.out

    def run(self, p: Pass) -> str | None:
        from prax import worker

        items = p.fetch(self.name).get("items") or []
        if not items:
            return None
        p.note(f"{self.name}: {len(items)} items", total=len(items), done=0)
        # each document's results are posted as it is done, under a
        # heartbeat: a batch of books lands one book at a time, and the door
        # neither reaps the session nor re-leases the book being read
        tally: dict[str, Any] = {"applied": 0, "actions": {}}
        ids = [int(it["doc_id"]) for it in items]
        with worker._Beating(p.door, p.session, "parse", ids) as beating:

            def landed(rs: list[dict[str, Any]]) -> None:
                rep = p.post("parse", {"results": rs})
                tally["applied"] += int(rep.get("applied", 0))
                for k, v in (rep.get("actions") or {}).items():
                    tally["actions"][k] = tally["actions"].get(k, 0) + int(v)
                beating.landed([int(r["doc_id"]) for r in rs])

            worker.do_parse(p.door, items, log_=p.log, landed=landed, spend=p.spend)
        return f"{tally['applied']} parsed {tally['actions'] or ''}"


REGISTERED = {"parse": Parse()}
