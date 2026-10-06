"""The two extraction steps: ``extract``, the watched pass of the local
model over what is due, and ``promote``, the expensive pass over the
documents a person flagged. The door's halves are the same; what a
worker does differs."""

from __future__ import annotations

import contextlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any

from prax import models, store
from prax.client import Door
from prax.graph import extraction, ontology
from prax.text import mimes

from . import leases
from .base import HandOut, Log, ModelStep, Pass, Step, TakeIn, say

REPORTED = ("linked", "existing", "queued", "rejected", "retired")
# less text than this is a stub, a cover or an error page: not extracted
MIN_CHARS = 300


def extraction_to_dict(ex: extraction.Extraction) -> dict[str, Any]:
    """What a worker posts of an extraction, and ``extraction_from`` reads."""
    return {
        "summary": ex.summary,
        "triples": [asdict(t) for t in ex.triples],
        "unmapped": ex.unmapped,
        "usage": ex.usage,
    }


def extraction_from(data: dict[str, Any]) -> extraction.Extraction:
    triples = [extraction.Triple(**t) for t in data.get("triples") or []]
    return extraction.Extraction(
        triples=triples,
        unmapped=list(data.get("unmapped") or []),
        summary=str(data.get("summary") or ""),
        usage=dict(data.get("usage") or {}),
    )


class Extract(Step):
    name = "extract"

    def due(self, h: HandOut) -> list[int]:
        onto = ontology.current()
        return store.select_for_extraction(
            h.con,
            ontology_version=onto.version,
            min_chars=MIN_CHARS,
            onto=onto,
            sources=tuple(store.CAPTURE_SOURCES) if h.scope == "captures" else None,
            skip_mime_prefix="image/",
        )

    def item(self, h: HandOut, doc_id: int) -> dict[str, Any]:
        doc = extraction.build_input(h.con, doc_id)
        return {
            "doc_id": doc_id,
            "title": doc.title,
            "header": doc.header,
            "text": doc.text,
            "domains": doc.domains,
            "ontology_version": doc.ontology().version,
        }

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        due = self.due(h)
        if due is None:
            return h.nothing()
        # the scope is in what is due already; a flag is explicit
        return h.documents(due, lambda d: self.item(h, d), scoped=False)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        extractor = str(t.payload.get("extractor") or t.worker)
        run = t.run("promote" if self.name == "promote" else "work")
        totals = extraction.ApplyReport()
        for r in t.results:
            doc_id = int(r["doc_id"])
            t.release([doc_id])
            if r.get("error"):
                t.out["errors"].append({"doc_id": doc_id, "error": r["error"]})
                with contextlib.suppress(Exception):  # the note is a nicety
                    extraction.note_failure(
                        t.con, doc_id, str(r["error"]), extractor=extractor
                    )
                continue
            try:
                ex = extraction_from(r["extraction"])
                leases.note_spend(t.con, self.name, ex.usage, doc_id=doc_id, run=run)
                rep = extraction.apply(t.con, doc_id, ex, extractor=extractor, run=run)
            except Exception as exc:  # noqa: BLE001 - one result must not stop the rest
                t.out["errors"].append(
                    {"doc_id": doc_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            for k in REPORTED:
                setattr(totals, k, getattr(totals, k) + getattr(rep, k))
            t.out["applied"] += 1
        t.out["report"] = {k: getattr(totals, k) for k in REPORTED}
        return t.out

    def refusal(self, p: Pass) -> str | None:
        spec = models.resolve("extract")
        if spec is None or spec.paid:
            return "skipped: the extract step is " + (
                f"{spec.name} (paid)" if spec else "not configured"
            )
        return None

    def work(self, p: Pass, items: list[dict[str, Any]]) -> tuple[Any, list[Any]]:

        ext = extraction.current("extract")
        return ext, do_extract(items, ext, workers=p.workers, log_=p.log)

    def run(self, p: Pass) -> str | None:
        refused = self.refusal(p)
        if refused:
            return refused
        items = p.fetch(self.name).get("items") or []
        if not items:
            return None
        p.note(f"{self.name}: {len(items)} items", total=len(items), done=0)
        ext, results = self.work(p, items)
        prefix = "promote" if self.name == "promote" else "work"
        rep = p.post(
            self.name,
            {
                "results": results,
                "extractor": ext.name,
                "run": f"{prefix}-{time.strftime('%Y%m%dT%H%M%S')}",
            },
        )
        line = f"{rep.get('applied', 0)} documents: {rep.get('report')}"
        for e in rep.get("errors") or []:
            line += f"; doc {e['doc_id']} failed: {str(e['error'])[:160]}"
        return line


class Promote(Extract):
    """The flagged documents the promote step's model has not read
    (whatever the scope: a flag is explicit); images included, since the
    expensive pass reads the picture again first."""

    name = "promote"

    def due(self, h: HandOut) -> list[int] | None:  # type: ignore[override]
        try:
            producer = extraction.current("promote").name
        except RuntimeError:  # the step is off on this host
            return None
        return [
            d["doc_id"]
            for d in store.promoted_documents(h.con, producer=producer)
            if not d["done"]
        ]

    def item(self, h: HandOut, doc_id: int) -> dict[str, Any]:
        item = super().item(h, doc_id)
        row = store.get_document(h.con, doc_id)
        if row and mimes.is_picture(row["mime"]):
            path = row.get("original_path")
            item["image"] = {
                "original": f"/doc/{doc_id}/original",
                "filename": path.replace("\\", "/").rsplit("/", 1)[-1]
                if path
                else None,
                "previous": row["text"],
            }
        return item

    def refusal(self, p: Pass) -> str | None:
        spec = models.resolve("promote")
        if spec is None:
            return "skipped: the promote step is not configured"
        if spec.paid and not p.spend:
            return f"skipped: the promote step is {spec.name} (paid); --spend runs it"
        return None

    def work(self, p: Pass, items: list[dict[str, Any]]) -> tuple[Any, list[Any]]:

        spec = models.resolve("promote")
        assert spec is not None
        ext = extraction.current("promote")
        return ext, do_promote(p.door, items, ext, spec, log_=p.log)


# one short call a document, and most documents have no sentence to read
WORLD_DATES_BATCH = 30


class WorldDates(ModelStep):
    """When a fact holds in the world, read from the sentences that date it
    (AL step 5, ``prax.graph.worlddates``). The door hands out a document's
    candidate sentences; a document with none is stamped without a call.
    The door reads the sentences again when the answer comes back, so a
    fact is checked against what it handed out, never against what a
    worker says it was shown."""

    name = "worlddates"

    def available(self, spec: models.ModelSpec | None) -> bool:
        """A served model only: the judge of each fact reads its answer's
        log probabilities, which an API model does not give."""
        return spec is not None and spec.kind == "openai"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        from prax.graph import worlddates

        if models.resolve(self.name) is None:
            return h.nothing()

        def build(doc_id: int) -> dict[str, Any] | None:
            said = worlddates.sentences(store.text_chunks(h.con, doc_id))
            title = store.document_titles(h.con, [doc_id]).get(doc_id) or ""
            return {"doc_id": doc_id, "title": title, "sentences": said}

        return h.documents(
            store.world_dates_needed(h.con, limit=WORLD_DATES_BATCH * 2),
            build,
            scoped=False,
            limit=WORLD_DATES_BATCH,
        )

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        from prax.graph import worlddates

        run = t.run()
        spec = models.resolve(self.name)
        model = str(t.payload.get("model") or (spec.name if spec else t.worker))
        totals = {"restated": 0, "linked": 0, "existing": 0, "refused": 0}

        def apply(doc_id: int, r: dict[str, Any]) -> None:
            said = worlddates.sentences(store.text_chunks(t.con, doc_id))
            got = worlddates.apply(
                t.con, doc_id, list(r.get("facts") or []), said, model=model, run=run
            )
            for k in totals:
                totals[k] += getattr(got, k)

        t.each(apply)
        t.out.update(totals)
        return t.out

    def do(self, items: list[dict[str, Any]], runtime: Any, log: Log | None) -> Any:
        from prax.graph import ontology, worlddates

        onto = ontology.current()
        out: list[dict[str, Any]] = []
        for it in items:
            said = list(it.get("sentences") or [])
            if not said:
                out.append({"doc_id": it["doc_id"], "facts": []})
                continue
            try:
                facts, _usage = worlddates.read(runtime, it["title"], said, onto)
            except Exception as exc:  # noqa: BLE001 - one document must not stop the rest
                out.append({"doc_id": it["doc_id"], "error": str(exc)[:200]})
                continue
            out.append(
                {
                    "doc_id": it["doc_id"],
                    "facts": [worlddates.as_result(f) for f in facts],
                }
            )
        dated = sum(len(r.get("facts") or []) for r in out)
        say(log, f"worlddates: {dated} dated facts in {len(out)} documents")
        return out

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        return (
            f"{rep.get('restated', 0)} facts dated, {rep.get('linked', 0)} new,"
            f" {rep.get('existing', 0)} dated already,"
            f" {rep.get('applied', 0)} documents"
        )


REGISTERED = {s.name: s for s in (Extract(), Promote(), WorldDates())}


# ------------------------------------------------ the worker's half


def do_extract(
    items: list[dict[str, Any]],
    ext: extraction.Extractor,
    *,
    workers: int = 1,
    log_: Log | None = None,
) -> list[dict[str, Any]]:
    def one(it: dict[str, Any]) -> dict[str, Any]:
        doc = extraction.DocumentInput(
            it["doc_id"],
            it.get("title") or "",
            it.get("header") or "",
            it.get("text") or "",
            it.get("domains"),
        )
        try:
            result = ext.extract(doc)
            if not result.triples and result.usage.get("dropped_lines"):
                result = ext.extract(doc)
            say(log_, f"extract doc {it['doc_id']}: {len(result.triples)} triples")
            return {
                "doc_id": it["doc_id"],
                "extraction": extraction_to_dict(result),
            }
        except models.ServerNotReady as exc:
            # the model server is loading or down: not the document's
            # fault, no error recorded against it; deferred, so the door
            # leaves it leased a while and hands out other work meanwhile
            say(log_, f"extract doc {it['doc_id']}: not yet — {exc}")
            return {"doc_id": it["doc_id"], "defer": True}
        except Exception as exc:  # noqa: BLE001
            return {"doc_id": it["doc_id"], "error": f"{type(exc).__name__}: {exc}"}

    if workers > 1 and isinstance(ext, extraction.LocalExtractor):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return [r for r in pool.map(one, items) if r]
    return [r for r in (one(it) for it in items) if r]


def do_promote(
    door: Door,
    items: list[dict[str, Any]],
    ext: extraction.Extractor,
    spec: models.ModelSpec,
    *,
    log_: Log | None = None,
) -> list[dict[str, Any]]:
    """The expensive pass over flagged documents: a promoted image is
    first described again by the promote model (a Claude one; a server
    that cannot see leaves the reading as it is), the reading posted as a
    parse result so the document carries it, and the extraction reads
    that; then the extraction, as for the extract step."""
    from prax.parsers import vision

    for it in items:
        image = it.get("image")
        if not image or spec.kind != "claude":
            continue
        try:
            data = door.get_bytes(image["original"])
            text = vision.describe(
                data,
                filename=image.get("filename"),
                model=spec.model,
                previous=image.get("previous"),
            )
        except Exception as exc:  # noqa: BLE001 - the extraction still runs
            say(log_, f"promote doc {it['doc_id']}: reading failed: {exc}")
            continue
        door.post_json(
            "/work/parse",
            {
                "results": [
                    {
                        "doc_id": it["doc_id"],
                        "extractor": f"vision/1+{spec.runtime_name}",
                        "text": text,
                        "force": True,
                    }
                ]
            },
        )
        it["text"] = text
        say(log_, f"promote doc {it['doc_id']}: read again by {spec.runtime_name}")
    return do_extract(items, ext, workers=1, log_=log_)
