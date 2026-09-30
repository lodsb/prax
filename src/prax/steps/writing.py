"""The steps that write something about a document with a local model:
its title, its summary in the library's language, and what each of its
chapters is about."""

from __future__ import annotations

import contextlib
from typing import Any

from prax import models, store
from prax.capture import pipeline
from prax.graph import ontology
from prax.text import language
from prax.writing import genres, sections, summaries, titles

from . import leases
from .base import HandOut, Log, ModelStep, TakeIn, say

# one item is a whole book, and a book is up to forty model calls: a batch
# of thirty was 1,200 of them before anything was posted, and the worker's
# heartbeat went stale inside it (2026-09-24)
TITLE_REASONS = ("empty", "filename", "identifier", "zotero-auto", "caps")

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
            pipeline.mark_tried(t.con, doc_id, run, str(r["tried"]))

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

        return do_titles(items, runtime, log_=log)

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

        return do_summaries(items, runtime, log_=log)

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

        return do_sections(items, runtime, log_=log)

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        wrote = sum(len(r.get("sections") or []) for r in results)
        return f"{rep.get('applied', 0)} documents, {wrote} sections"


class Communities(ModelStep):
    """The regions of the library named and described: each community of
    the nightly partition without a summary, or with one its members have
    moved away from (``prax.graph.communities``). Over the graph, not over
    documents, so scope does not apply."""

    name = "communities"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        from prax.graph import communities

        if models.resolve("communities") is None:
            return h.nothing()
        leased = leases.leased(self.name)
        wanted = [
            c
            for c in store.communities_to_summarize(h.con, limit=h.limit * 2)
            if c not in leased
        ][: h.limit]
        items = []
        for cid in wanted:
            got = store.community(
                h.con,
                cid,
                members=communities.SHOW_MEMBERS,
                documents=communities.SHOW_DOCUMENTS,
            )
            if got is None:
                continue
            region = ""
            if got["parent"] is not None:
                # a part is described inside its region, so it waits until
                # the region has a name (the next pass, as they go first)
                parent = store.community(h.con, int(got["parent"]), members=1)
                region = (parent or {}).get("label") or ""
                if not region:
                    continue
            items.append(
                {
                    "id": cid,
                    "region": region,
                    "members": got["members"],
                    "documents": got["documents"],
                }
            )
        h.lease([i["id"] for i in items])
        return h.batch(items)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        run = t.run()

        def apply(cid: int, r: dict[str, Any]) -> None:
            store.set_community_summary(
                t.con,
                cid,
                label=str(r.get("label") or ""),
                summary=str(r.get("summary") or ""),
                source=str(r.get("source") or t.worker),
                run=run,
            )

        t.each(apply, key="id")
        return t.out

    def do(self, items: list[dict[str, Any]], runtime: Any, log: Log | None) -> Any:
        from prax import models
        from prax.graph import communities

        results = []
        for it in items:
            if runtime is None:
                break
            try:
                got = communities.summarize(runtime, it)
            except models.ServerNotReady as exc:
                say(log, f"community {it['id']}: not yet - {exc}")
                results.append({"id": it["id"], "defer": True})
                continue
            if got is None:
                say(log, f"community {it['id']}: no usable answer")
                continue
            label, summary = got
            say(log, f"community {it['id']}: {label}")
            results.append(
                {
                    "id": it["id"],
                    "label": label,
                    "summary": summary,
                    "source": runtime.name,
                }
            )
        return results

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        return f"{rep.get('applied', 0)} named"


class Genres(ModelStep):
    """What a document is and what it is about (stage Z of docs/PLAN.md):
    the genres step's local model lists the labels that fit, is asked
    about each and its level, and the answers are calibrated by the Platt
    maps fitted on a person's labels (``steps.genres.platt``;
    ``writing.genres.label``). The door stores what is kept at
    ``steps.genres.keep`` as the model's labels, with the run, beside and
    never over a person's. Off until prax.yaml names a model for it.

    ``steps.genres.method: small`` labels with the small trained model
    instead (`prax.ml.labeller`, the run ``models/labeller/CURRENT``
    names): milliseconds a document on the CPU, no llama-server, and on
    only once a run has been trained and made current."""

    name = "genres"

    def available(self, spec: models.ModelSpec | None) -> bool:
        return genres_on()  # the small labeller needs no model spec

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        if not genres_on():
            return h.nothing()

        def build(doc_id: int) -> dict[str, Any] | None:
            doc = store.get_document(h.con, doc_id, max_chars=genres.OPENING + 200)
            if doc is None:
                return None
            meta = doc.get("meta") or {}
            where = (
                (meta.get("origin") or {}).get("path")
                or doc.get("source_url")
                or doc.get("original_path")
            )
            return {
                "doc_id": doc_id,
                "view": genres.view(
                    doc.get("title"), meta, doc.get("text") or "", where=where
                ),
            }

        return h.documents(store.genres_needed(h.con, limit=500), build)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        run = t.run()

        def tried(doc_id: int, r: dict[str, Any]) -> None:
            store.genres_tried(t.con, doc_id, run, str(r["tried"]))

        def apply(doc_id: int, r: dict[str, Any]) -> str:
            g, s = dict(r.get("g") or {}), dict(r.get("s") or {})
            try:
                store.set_genres(
                    t.con,
                    doc_id,
                    list(g),
                    subjects=list(s),
                    by=str(r.get("by") or t.worker),
                    p={**g, **s},
                    run=run,
                )
            except ValueError:
                return "left to a person"  # a person labelled it meanwhile
            return "labelled"

        t.out["actions"] = t.each(apply, tried=tried)
        return t.out

    def do(self, items: list[dict[str, Any]], runtime: Any, log: Log | None) -> Any:
        return do_genres(items, runtime, log_=log)

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        return f"{rep.get('applied', 0)} labelled, {rep.get('skipped', 0)} left"


REGISTERED = {
    s.name: s for s in (Titles(), Summaries(), Sections(), Communities(), Genres())
}


# ------------------------------------------------ the worker's half


def do_titles(
    items: list[dict[str, Any]], runtime: Any | None, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    results = []
    for it in items:
        doc_id, why, old = it["doc_id"], it["why"], it.get("title") or ""
        if why == "caps":
            results.append(
                {"doc_id": doc_id, "title": titles.recase(old), "source": "recase"}
            )
            continue
        if runtime is None or not (it.get("text") or "").strip():
            results.append(
                {
                    "doc_id": doc_id,
                    "tried": "no titles model" if runtime is None else "no text",
                }
            )
            continue
        try:
            guess = titles.guess_title(
                runtime,
                it["text"],
                filename=it.get("filename"),
                heading=titles.first_heading(it["text"]),
                pdf_title=None,
                # many documents carry this one: the model is told it is not it
                not_title=old if why == "shared" else None,
            )
        except models.ServerNotReady as exc:
            # the titles model's server is loading or down: deferred, like
            # a reading; the pass goes on with the rest and posts them
            say(log_, f"title doc {doc_id}: not yet — {exc}")
            results.append({"doc_id": doc_id, "defer": True})
            continue
        if guess is None:
            results.append({"doc_id": doc_id, "tried": "no usable guess"})
        elif guess.confidence == "low":
            results.append(
                {"doc_id": doc_id, "tried": f"unconfirmed: {guess.title[:80]}"}
            )
        else:
            results.append(
                {
                    "doc_id": doc_id,
                    "title": guess.title,
                    "source": runtime.name,
                    "confidence": guess.confidence,
                    "why": why,
                }
            )
            say(log_, f"title doc {doc_id}: {guess.title[:60]!r}")
    return results


def do_summaries(
    items: list[dict[str, Any]], runtime: Any | None, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    """Each summary translated into the language the document field is
    written in. Nothing reads the document: the summary is the input."""
    results = []
    for it in items:
        doc_id, lang = it["doc_id"], it.get("lang")
        if runtime is None:
            results.append({"doc_id": doc_id, "tried": "no summaries model"})
            continue
        try:
            got = summaries.translate(
                runtime,
                str(it.get("summary") or ""),
                lang=lang,
                title=str(it.get("title") or ""),
            )
        except models.ServerNotReady as exc:
            say(log_, f"summary doc {doc_id}: not yet — {exc}")
            results.append({"doc_id": doc_id, "defer": True})
            continue
        if got is None:
            results.append({"doc_id": doc_id, "tried": f"no usable {lang} translation"})
            continue
        results.append(
            {
                "doc_id": doc_id,
                "summary": got.text,
                "lang": language.canonical(),
                "source": runtime.name,
            }
        )
        say(log_, f"summary doc {doc_id} ({lang}): {got.text[:60]!r}")
    return results


def do_sections(
    items: list[dict[str, Any]], runtime: Any | None, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    """Each of a document's chapters read in turn.

    A document with no section long enough to be a chapter comes back
    with an empty list, which is an answer: the door records it against
    the text it was read from, and the document is not offered again
    until that text changes.
    """
    results = []
    for it in items:
        doc_id = it["doc_id"]
        done: list[dict[str, Any]] = []
        deferred = False
        for part in it.get("sections") or []:
            if runtime is None:
                break
            try:
                got = sections.summarize(
                    runtime,
                    str(part.get("heading") or ""),
                    str(part.get("text") or ""),
                    title=str(it.get("title") or ""),
                )
            except models.ServerNotReady as exc:
                say(log_, f"sections doc {doc_id}: not yet - {exc}")
                deferred = True
                break
            if got is not None:
                done.append(got.as_meta())
        if deferred:
            results.append({"doc_id": doc_id, "defer": True})
            continue
        results.append(
            {
                "doc_id": doc_id,
                "sections": done,
                "source": runtime.name if runtime else "none",
            }
        )
        if done:
            say(log_, f"sections doc {doc_id}: {len(done)} sections")
    return results


def genres_method() -> str:
    """``llm`` (the local model, list then ask) or ``small`` (the trained
    labeller); ``steps.genres.method``."""
    method = str(models.settings("genres").get("method") or "llm")
    if method not in ("llm", "small"):
        raise ValueError(f"steps.genres.method is llm or small, not {method!r}")
    return method


def genres_on() -> bool:
    """Whether the genres step has something to label with."""
    if genres_method() == "small":
        from prax.ml import labeller

        return labeller.current_run() is not None
    return models.resolve("genres") is not None


def do_small_genres(
    items: list[dict[str, Any]], *, log_: Log | None = None
) -> list[dict[str, Any]]:
    """Each document's genres and subjects from the small labeller, kept at
    its threshold (or ``steps.genres.keep``)."""
    from prax.ml import labeller

    model = labeller.current()
    if model is None:  # this worker's host, not the document: defer
        say(log_, "genres: no labeller (models/labeller/CURRENT)")
        return [{"doc_id": it["doc_id"], "defer": True} for it in items]
    keep = float(models.settings("genres").get("keep") or model.threshold)
    G, S = ontology.genres(), ontology.subjects()
    probs = model.predict([it["view"] for it in items])
    results: list[dict[str, Any]] = []
    for it, p in zip(items, probs, strict=True):
        g = {x: v for x, v in p.items() if v >= keep and x in G.labels()}
        s = {x: v for x, v in p.items() if v >= keep and x in S.labels()}
        if not g:
            results.append({"doc_id": it["doc_id"], "tried": "no genre kept"})
            continue
        results.append({"doc_id": it["doc_id"], "by": model.name, "g": g, "s": s})
    say(log_, f"genres: {len(items)} documents by {model.name}")
    return results


def do_genres(
    items: list[dict[str, Any]], runtime: Any | None, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    """Each document's genres and subjects from the genres step's local
    model (``writing.genres.label``), calibrated by ``steps.genres.platt``,
    or from the small labeller (``steps.genres.method: small``). A document
    with no genre kept is tried and not handed out again; a server that is
    not answering defers the rest of the batch."""
    if genres_method() == "small":
        return do_small_genres(items, log_=log_)
    base_url = getattr(runtime, "base_url", None)
    model = getattr(runtime, "model", None)
    if runtime is None or not base_url or not model:
        # this worker's configuration, not the document: defer, never mark
        # it tried (a worker without prax.yaml once marked 174 so)
        why = "no genres model" if runtime is None else "wants a local model"
        say(log_, f"genres: {why}")
        return [{"doc_id": it["doc_id"], "defer": True} for it in items]
    opts = models.settings("genres")
    platt = opts.get("platt") or None
    keep = float(opts.get("keep") or genres.KEEP)
    G, S = ontology.genres(), ontology.subjects()
    results: list[dict[str, Any]] = []
    for n, it in enumerate(items):
        try:
            got = genres.label(
                runtime,
                base_url,
                model,
                it["view"],
                G,
                S,
                platt=platt,
                keep=keep,
                slots=int(opts.get("slots") or 2),
            )
        except models.ServerNotReady as exc:
            say(log_, f"genres: not yet — {exc}")
            results += [{"doc_id": x["doc_id"], "defer": True} for x in items[n:]]
            break
        except Exception as exc:  # noqa: BLE001 - one document must not stop the rest
            results.append(
                {"doc_id": it["doc_id"], "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        if not got["g"]:
            results.append({"doc_id": it["doc_id"], "tried": "no genre kept"})
            continue
        results.append({"doc_id": it["doc_id"], "by": runtime.name, **got})
        say(log_, f"genres doc {it['doc_id']}: {sorted(got['g'])} / {sorted(got['s'])}")
    return results
