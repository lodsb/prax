"""The vocabulary step: one name per thing, whatever language the
document was in, and the word the other way (``prax.vocabulary``)."""

from __future__ import annotations

from typing import Any

from prax import models, store
from prax import vocabulary as words

from .base import HandOut, Log, ModelStep, TakeIn


def named_in(con: Any, entity_id: int) -> str:
    """The title of a document that named this entity: context for a
    model asked what English calls the thing, since a bare word can be
    two things and the document says which."""
    return store.entity_named_in(con, entity_id)


class Vocabulary(ModelStep):
    """Entities named in the language of the document that named them,
    where English calls the thing something else; then the other way, the
    word a reader of another language would search for. Over the graph,
    not over documents, so scope does not apply."""

    name = "vocabulary"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        from prax import work

        if models.resolve("vocabulary") is None:
            return h.nothing()
        leased = tuple(i for (s, i) in work._leases if s == self.name and not h.free(i))
        found = store.foreign_names(h.con, limit=h.limit, skip=leased)
        if len(found) < h.limit:
            found += store.unlabelled_names(
                h.con,
                words.label_languages(h.con),
                limit=h.limit - len(found),
                skip=leased + tuple(r["id"] for r in found),
            )
        items = [{**r, "context": named_in(h.con, r["id"])} for r in found]
        h.lease([i["id"] for i in items])
        return h.batch(items)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        run = t.run()

        def apply(entity_id: int, r: dict[str, Any]) -> str:
            if r.get("into"):
                return store.label_in_language(
                    t.con,
                    entity_id,
                    str(r["name"]),
                    lang=str(r["into"]),
                    producer="vocabulary",
                    run=run,
                )
            if r.get("changed"):
                got = store.name_in_english(
                    t.con,
                    entity_id,
                    str(r["name"]),
                    producer="vocabulary",
                    run=run,
                    confidence=str(r.get("confidence") or "INFERRED"),
                )
                return str(got.get("action") or "?")
            # already the word English uses: the pass says so with a label,
            # which is also what keeps it from being asked twice
            store.add_label(
                t.con,
                entity_id,
                str(r["name"]),
                lang="en",
                kind="pref",
                producer="vocabulary",
                run=run,
            )
            return "kept"

        t.out["actions"] = t.each(apply, key="id")
        return t.out

    def do(self, items: list[dict[str, Any]], runtime: Any, log: Log | None) -> Any:
        from prax import worker

        return worker.do_vocabulary(items, runtime, log_=log)

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        errors = rep.get("errors") or []
        return f"{rep.get('applied', 0)} named {rep.get('actions') or ''}" + (
            f" · {len(errors)} refused: {errors[0]['error'][:60]}" if errors else ""
        )


REGISTERED = {"vocabulary": Vocabulary()}
