"""The parse step: the readings a person or the door asked for, then the
captures waiting for their text; the worker reads each and the door
files the text and asks for what follows (``pipeline.follow_ups``)."""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, Self

from prax import models, parsers, store
from prax import steps as steps_mod
from prax.capture import inbox, pipeline
from prax.client import Door
from prax.ml import usage
from prax.parsers import figures, queue
from prax.text import mimes

from . import READING_STEPS
from .base import HandOut, Log, Pass, Step, TakeIn, say


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
            # tokens home (prax.ml.usage) because it never writes itself
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
            if not r.get("error"):
                # a long scan read in windows: its progress, and the next
                # window asked for while pages wait (after the one just
                # finished, which would otherwise swallow the request)
                queue.continue_windows(
                    t.con,
                    doc_id,
                    stamp=stamp,
                    pages_left=r.get("pages_left"),
                    pages=r.get("pages_total"),
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

        items = p.fetch(self.name).get("items") or []
        if not items:
            return None
        p.note(f"{self.name}: {len(items)} items", total=len(items), done=0)
        # each document's results are posted as it is done, under a
        # heartbeat: a batch of books lands one book at a time, and the door
        # neither reaps the session nor re-leases the book being read
        tally: dict[str, Any] = {"applied": 0, "actions": {}}
        ids = [int(it["doc_id"]) for it in items]
        with _Beating(p.door, p.session, "parse", ids) as beating:

            def landed(rs: list[dict[str, Any]]) -> None:
                rep = p.post("parse", {"results": rs})
                tally["applied"] += int(rep.get("applied", 0))
                for k, v in (rep.get("actions") or {}).items():
                    tally["actions"][k] = tally["actions"].get(k, 0) + int(v)
                beating.landed([int(r["doc_id"]) for r in rs])

            do_parse(p.door, items, log_=p.log, landed=landed, spend=p.spend)
        return f"{tally['applied']} parsed {tally['actions'] or ''}"


REGISTERED = {"parse": Parse()}


# ------------------------------------------------ the worker's half


def _requested(
    it: dict[str, Any], *, spend: bool = False
) -> tuple[list[Any], str | None]:
    """The extractors to try for an item: a requested reading names one
    (and is refused when its model would cost money and ``spend`` was not
    given, so nobody's click spends unasked — the request stays on the
    document with that reason); otherwise the candidates for the type.

    What a reading costs is the step's model it runs
    (``store.READING_STEPS``), never the reading's own name: `figures`
    and `vision-pages` are the vision step as much as `vision` is, and
    keying on the name let them past this guard until 2026-09-23.
    """
    name = it.get("extractor")
    if not name:
        return parsers.candidates(it.get("mime") or ""), None
    try:
        ext = parsers.by_name(name)
    except KeyError as exc:
        return [], str(exc)
    step = steps_mod.READING_STEPS.get(name)
    if step and not spend:
        spec = models.resolve(step)
        if spec is not None and spec.paid:
            return [], (
                f"the {step} step is {spec.name if spec else 'none'} (paid):"
                " run it with --spend as the promote step, or point the step"
                " at a local server"
            )
    if ext.check is None and not ext.available():
        return [], f"{name} is not installed on this worker"
    return [ext], None  # a server that is not up says so itself: NotYet


def do_parse(
    door: Door,
    items: list[dict[str, Any]],
    *,
    log_: Log | None = None,
    landed: Callable[[list[dict[str, Any]]], None] | None = None,
    spend: bool = False,
) -> list[dict[str, Any]]:
    """Every item through its extractor chain. ``landed`` is called with
    each document's results as soon as it is done — the caller posts them
    then, so a batch of books lands one book at a time and not when the
    last one is read."""
    results: list[dict[str, Any]] = []

    def done(*rs: dict[str, Any]) -> None:
        results.extend(rs)
        if landed is not None:
            landed(list(rs))

    for it in items:
        doc_id = it["doc_id"]
        usage.clear()  # what this document's readings pay for, and nothing before
        exts, refused = _requested(it, spend=spend)
        if not exts:
            done(
                {
                    "doc_id": doc_id,
                    "extractor": it.get("extractor") or "none",
                    "error": refused or "no extractor for this type",
                    "requested": it.get("extractor"),
                }
            )
            continue
        try:
            data = door.get_bytes(it["original"])
        except Exception as exc:  # noqa: BLE001
            done(
                {"doc_id": doc_id, "extractor": exts[0].stamp, "error": f"fetch: {exc}"}
            )
            continue
        pages = _page_count(data)  # a fact of the original, for the door's record
        last = None
        tried: list[
            dict[str, Any]
        ] = []  # the chain's earlier attempts, posted with the outcome
        for ext in exts:
            t0 = time.monotonic()
            try:
                with (
                    _mode(ext.name, it.get("mode")),
                    figures.fetching(
                        lambda ref, d=door, i=doc_id: _figure_from_door(d, i, ref)
                    ),
                ):
                    stamp = ext.stamp
                    got = ext(
                        data, filename=it.get("filename"), previous=it.get("previous")
                    )
                    text = got.strip()
            except (parsers.NotYet, models.ServerNotReady) as exc:
                # the server it reads through is loading or down: not the
                # document's fault; deferred, so the door leaves it leased a
                # while and hands out other work meanwhile
                say(log_, f"parse doc {doc_id}: not yet — {exc}")
                done(*tried, {"doc_id": doc_id, "defer": True})
                break
            except Exception as exc:  # noqa: BLE001
                last = (ext.stamp, f"{type(exc).__name__}: {exc}")
                if ext is not exts[-1]:
                    # the chain goes on; the door records this attempt too,
                    # so it can see the chain was run and not hand the
                    # document out again (a scan refused by the first
                    # extractor, empty for the fallback, came back every
                    # cycle otherwise)
                    tried.append(
                        {
                            "doc_id": doc_id,
                            "extractor": ext.stamp,
                            "error": last[1],
                            "pages": pages,
                        }
                    )
                continue
            done(
                *tried,
                {
                    "doc_id": doc_id,
                    "extractor": stamp,
                    "text": text,
                    "seconds": round(time.monotonic() - t0, 2),
                    "force": bool(it.get("force")),
                    "requested": it.get("extractor"),
                    "keep_source": ext.annotates,
                    "pages": pages,
                    # what the reading paid for, for the door's ledger
                    "usage": usage.take(),
                    # a long scan read in windows: the pages still to go
                    "pages_left": getattr(got, "pages_left", None),
                    "pages_total": getattr(got, "pages", None),
                },
            )
            pictures = len(figures.DATA_IMAGE.findall(text))
            words = len(figures.DATA_IMAGE.sub("", text)) if pictures else len(text)
            say(
                log_,
                f"parse doc {doc_id}: {words} chars"
                + (f" + {pictures} pictures to file" if pictures else "")
                + f" ({stamp})",
            )
            break
        else:
            done(
                *tried,
                {
                    "doc_id": doc_id,
                    "extractor": last[0] if last else exts[0].stamp,
                    "error": last[1] if last else "failed",
                    "requested": it.get("extractor"),
                    "pages": pages,
                },
            )
    return results


BEAT_SECONDS = 300  # a third of the door's lease: the beat renews what is held


class _Beating:
    """A heartbeat beside a long batch: every ``BEAT_SECONDS`` the session
    is beaten (so the door does not reap it for silence) and the leases of
    the items still in flight are renewed (so the door does not hand them
    out again while a book is being read). ``landed(ids)`` takes items out
    of flight as their results are posted."""

    def __init__(
        self, door: Door, session: int | None, step: str, items: list[int]
    ) -> None:
        self.door = door
        self.session = session
        self.step = step
        self.in_flight = set(items)
        self.done = 0
        self.total = len(items)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def landed(self, ids: list[int]) -> None:
        with self.lock:
            self.in_flight.difference_update(ids)
            self.done = self.total - len(self.in_flight)
        self.beat()

    def beat(self) -> None:
        if self.session is None:
            return
        with self.lock:
            body = {
                "done": self.done,
                "total": self.total,
                "renew": {"step": self.step, "items": sorted(self.in_flight)},
            }
        with contextlib.suppress(Exception):  # a missed beat is not a failed batch
            self.door.post_json(f"/work/session/{self.session}", body)

    def _run(self) -> None:
        while not self.stop.wait(BEAT_SECONDS):
            self.beat()

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


def _figure_from_door(door: Door, doc_id: int, ref: str) -> tuple[bytes, str] | None:
    """A figure the original does not hold (a filed picture of a scanned
    page): the door serves it out of its archive."""
    try:
        data = door.get_bytes(f"/doc/{doc_id}/figure/{ref}")
    except Exception:  # noqa: BLE001 - not there: the reading skips it
        return None
    return (data, figures.media_of(data)) if data else None


def _page_count(data: bytes) -> int | None:
    """How many pages a PDF has, or None for anything else (and for a PDF
    pymupdf cannot open: the extractor will say so in its own words)."""
    if data[:5] != b"%PDF-":
        return None
    try:
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return int(doc.page_count)
    except Exception:  # noqa: BLE001
        return None


_MODE_SETTINGS = {
    "vision-pages": "PRAX_VISION_PAGES",
    "figures": "PRAX_FIGURES",
    "pymupdf4llm-ocr": "PRAX_OCR_LANGUAGE",
    "marker": "PRAX_MARKER_MODE",
    "formulas": "PRAX_FORMULA_READINGS",
}


@contextlib.contextmanager
def _mode(extractor: str, mode: str | None) -> Iterator[None]:
    """The requested mode as the extractor's setting for one call
    (``vision-pages``: every page or the scans; ``figures``: every image
    or the captioned ones; OCR: the recognizer's language)."""
    variable = _MODE_SETTINGS.get(extractor)
    if not mode or variable is None:
        yield
        return
    before = os.environ.get(variable)
    os.environ[variable] = mode
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = before
