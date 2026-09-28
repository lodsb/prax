"""The vocabulary step: one name per thing, whatever language the
document was in, and the word the other way (``prax.graph.vocabulary``)."""

from __future__ import annotations

from typing import Any

from prax import models, store
from prax.graph import vocabulary
from prax.graph import vocabulary as words

from .base import HandOut, Log, ModelStep, TakeIn, say


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

        return do_vocabulary(items, runtime, log_=log)

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        errors = rep.get("errors") or []
        return f"{rep.get('applied', 0)} named {rep.get('actions') or ''}" + (
            f" · {len(errors)} refused: {errors[0]['error'][:60]}" if errors else ""
        )


REGISTERED = {"vocabulary": Vocabulary()}


# ------------------------------------------------ the worker's half


def do_vocabulary(
    items: list[dict[str, Any]], runtime: Any | None, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    """Each name asked of the model: what does English call this thing?

    The commonest answer is the name it was given — a rare English term
    nobody else in the library wrote down looks foreign to the candidate
    net and is handed back unchanged, which costs the call and nothing
    else.
    """
    results = []
    for it in items:
        entity_id, name = it["id"], str(it.get("name") or "")
        into = it.get("into")
        if runtime is None or not name:
            if not into:  # no model, no word in another language
                results.append({"id": entity_id, "name": name, "changed": False})
            continue
        try:
            got = vocabulary.rename(
                runtime,
                name,
                str(it.get("type") or "concept"),
                context=str(it.get("context") or ""),
                into=into,
            )
        except models.ServerNotReady as exc:
            say(log_, f"name {entity_id}: not yet — {exc}")
            results.append({"id": entity_id, "defer": True})
            continue
        if into:
            # a refused answer writes the name as it is: the pass has asked,
            # and a label in the language is what says so
            said = got.name if got is not None else name
            results.append({"id": entity_id, "name": said, "into": into})
            if said != name:
                say(log_, f"name {entity_id}: {name!r} in {into} is {said!r}")
            continue
        if got is None:
            results.append({"id": entity_id, "name": name, "changed": False})
            continue
        results.append({"id": entity_id, "name": got.name, "changed": got.changed})
        if got.changed:
            say(log_, f"name {entity_id}: {name!r} -> {got.name!r}")
    return results
