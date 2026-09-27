"""The steps that write something about a document with a local model:
its title, its summary in the library's language, and what each of its
chapters is about."""

from __future__ import annotations

import contextlib
from typing import Any

from prax import models, pipeline, store, summaries

from .base import HandOut, Log, ModelStep, TakeIn

# one item is a whole book, and a book is up to forty model calls: a batch
# of thirty was 1,200 of them before anything was posted, and the worker's
# heartbeat went stale inside it (2026-09-24)
TITLE_REASONS = ("empty", "filename", "zotero-auto", "caps")

SECTIONS_BATCH = 3  # documents a batch: each is a book's worth of calls


class Titles(ModelStep):
    name = "titles"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        def build(c: tuple[int, str]) -> dict[str, Any] | None:
            doc_id, why = c
            doc = store.get_document(h.con, doc_id, max_chars=60000)
            if doc is None:
                return None
            return {
                "doc_id": doc_id,
                "why": why,
                "title": doc["title"] or "",
                "text": doc["text"],
                "filename": pipeline.file_name(doc),
                "mime": doc["mime"],
            }

        # a title many documents share is a title to replace too: the NIME
        # papers carried their volume's name, and one entity held them all
        needed = pipeline.titles_needed(
            h.con, untried_only=True, reasons=(*TITLE_REASONS, "shared")
        )
        return h.documents(needed, build)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        run = t.run()

        def tried(doc_id: int, r: dict[str, Any]) -> None:
            pipeline._mark_tried(t.con, doc_id, run, str(r["tried"]))

        def apply(doc_id: int, r: dict[str, Any]) -> None:
            got = store.retitle(
                t.con,
                doc_id,
                str(r["title"]),
                source=str(r.get("source") or t.worker),
                run=run,
                confidence=r.get("confidence"),
            )
            if got.get("changed") and r.get("why") == "shared":
                # its facts sit on the entity the shared title named, with
                # the other documents': a new reading puts them on its own
                # and retires the old one, history kept
                store.request_extraction(t.con, doc_id, by="titles")

        t.each(apply, tried=tried)
        return t.out

    def do(self, items: list[dict[str, Any]], runtime: Any, log: Log | None) -> Any:
        from prax import worker

        return worker.do_titles(items, runtime, log_=log)

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        return f"{rep.get('applied', 0)} retitled, {rep.get('skipped', 0)} left"


class Summaries(ModelStep):
    """A summary written in another language than the document field,
    translated by the summaries model. No document is read: the summary
    itself is the whole input, which is why this is seconds of a local
    model and not a re-extraction."""

    name = "summaries"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        if models.resolve("summaries") is None:
            return h.nothing()

        def build(c: tuple[int, str]) -> dict[str, Any] | None:
            doc_id, lang = c
            meta = store.get_meta(h.con, doc_id)
            # always from the summary as first written, where we kept it:
            # translating a translation compounds the first one's mistakes
            held = summaries.native(meta.get("summaries") or {})
            summary = str(
                held[1] if held and held[0] == lang else meta.get("summary") or ""
            )
            if not summary:
                return None
            row = store.get_document(h.con, doc_id, max_chars=0)
            return {
                "doc_id": doc_id,
                "lang": lang,
                "summary": summary,
                "title": (row["title"] if row else "") or "",
            }

        return h.documents(pipeline.summaries_needed(h.con, untried_only=True), build)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        run = t.run()

        def tried(doc_id: int, r: dict[str, Any]) -> None:
            # the model could not translate it: the summary stays as it was
            # written, and the document is not offered again
            with contextlib.suppress(Exception):
                store.summary_tried(t.con, doc_id, run, str(r["tried"]))

        def apply(doc_id: int, r: dict[str, Any]) -> None:
            store.set_summary(
                t.con,
                doc_id,
                str(r["summary"]),
                lang=str(r.get("lang") or "") or None,
                source=str(r.get("source") or t.worker),
                run=run,
            )

        t.each(apply, tried=tried)
        return t.out

    def do(self, items: list[dict[str, Any]], runtime: Any, log: Log | None) -> Any:
        from prax import worker

        return worker.do_summaries(items, runtime, log_=log)

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        return f"{rep.get('applied', 0)} translated, {rep.get('skipped', 0)} left"


class Sections(ModelStep):
    """A long document's chapters, read one at a time. The whole document
    never goes to the model: a section does, and only the sections long
    enough to be chapters."""

    name = "sections"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        if models.resolve("sections") is None:
            return h.nothing()
        limit = min(h.limit, SECTIONS_BATCH)

        def build(doc_id: int) -> dict[str, Any]:
            row = store.get_document(h.con, doc_id, max_chars=0)
            return {
                "doc_id": doc_id,
                "title": (row["title"] if row else "") or "",
                "sections": store.document_sections(h.con, doc_id),
            }

        return h.documents(
            store.sections_needed(h.con, limit=limit * 4), build, limit=limit
        )

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        run = t.run()

        def apply(doc_id: int, r: dict[str, Any]) -> None:
            store.set_sections(
                t.con,
                doc_id,
                list(r.get("sections") or []),
                source=str(r.get("source") or t.worker),
                run=run,
            )

        t.each(apply)
        return t.out

    def do(self, items: list[dict[str, Any]], runtime: Any, log: Log | None) -> Any:
        from prax import worker

        return worker.do_sections(items, runtime, log_=log)

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        wrote = sum(len(r.get("sections") or []) for r in results)
        return f"{rep.get('applied', 0)} documents, {wrote} sections"


REGISTERED = {s.name: s for s in (Titles(), Summaries(), Sections())}
