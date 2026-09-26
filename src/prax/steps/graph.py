"""The steps that work on the graph rather than on a document: typing the
review queue's untyped items, the likely tier of entity resolution, and
the adjudication of the likely pairs."""

from __future__ import annotations

import time
from typing import Any

from prax import embeddings, models, store
from prax.work import LEASE_SECONDS

from .base import HandOut, Pass, Step, TakeIn, paid_refusal


class Typing(Step):
    """Untyped review items to the typing model, a batch of items each;
    nothing without a model for the step."""

    name = "typing"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        from prax import typing_pass

        if models.resolve("typing") is None:
            return h.nothing()
        items = [
            b
            for b in typing_pass.hand_out(h.con, limit=h.limit * 2)
            if all(h.free(it["id"]) for it in b["items"])
        ][: h.limit]
        h.lease([it["id"] for b in items for it in b["items"]])
        return h.batch(items)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        from prax import typing_pass, work

        model = str(t.payload.get("model") or t.worker)
        run = t.run("typing-model")
        for r in t.results:
            t.release([it["id"] for it in r.get("items") or []])
            work._note_spend(t.con, self.name, r.get("usage"), run=run)
        rep = typing_pass.take_in(t.con, t.results, model=model, run=run)
        t.out["applied"] = rep.requests
        t.out["report"] = {
            "checked": rep.checked,
            "linked": rep.linked,
            "existing": rep.existing,
            "dropped": rep.dropped,
            "misfit": rep.misfit,
            "unanswered": rep.unanswered,
        }
        return t.out

    def run(self, p: Pass) -> str | None:
        from prax import worker

        spec = models.resolve("typing")
        if spec is None:
            return None  # the step is off on this host: nothing to say
        refused = paid_refusal(self.name, spec)
        if refused:
            return refused
        items = p.fetch(self.name).get("items") or []
        if not items:
            return None
        p.note(f"{self.name}: {len(items)} items", total=len(items), done=0)
        results = worker.do_typing(items, models.runtime(spec), log_=p.log)
        rep = p.post(
            self.name,
            {
                "results": results,
                "model": spec.runtime_name,
                "run": f"typing-model-{time.strftime('%Y%m%dT%H%M%S')}",
            },
        )
        r = rep.get("report") or {}
        return (
            f"{rep.get('applied', 0)} requests: linked {r.get('linked', 0)},"
            f" dropped {r.get('dropped', 0)}, misfit {r.get('misfit', 0)},"
            f" unanswered {r.get('unanswered', 0)}"
        )


class Resolve(Step):
    """The likely tier of entity resolution: one type's names to a
    worker, which embeds them and posts the close pairs; a type is due
    when its pairs are older than LIKELY_DAYS or were never computed (the
    door never embeds: invariant 7). Leased by the type's place in the
    sorted list."""

    name = "resolve"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        from prax import resolution

        emb = embeddings.current()
        if emb is None:
            return {"step": self.name, "type": None, "names": [], "lease_seconds": 0}
        runs = store.candidate_runs(h.con)
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() - resolution.LIKELY_DAYS * 86400),
        )
        for i, t in enumerate(sorted(resolution.LIKELY_TYPES)):
            if runs.get(t, "") >= cutoff or not h.free(i):
                continue
            names = store.entity_names(h.con, t)
            if len(names) < 2:
                continue
            h.lease([i])
            return {
                "step": self.name,
                "type": t,
                "names": names,
                "model": emb.name,
                "threshold": resolution.LIKELY_THRESHOLD,
                "lease_seconds": LEASE_SECONDS,
            }
        return {"step": self.name, "type": None, "names": [], "lease_seconds": 0}

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        from prax import resolution

        etype = str(t.payload.get("type") or "")
        if etype not in resolution.LIKELY_TYPES:
            raise ValueError(
                f"resolve takes a type among {sorted(resolution.LIKELY_TYPES)}"
            )
        emb = embeddings.current()
        model = str(t.payload.get("model") or "")
        if emb is None or model != emb.name:
            theirs = emb.name if emb else None
            raise ValueError(f"the door's names embed with {theirs}, not {model!r}")
        pairs = [
            (int(a), int(b), float(s)) for a, b, s in (t.payload.get("pairs") or [])
        ]
        t.release([sorted(resolution.LIKELY_TYPES).index(etype)])
        t.out["applied"] = store.replace_entity_candidates(
            t.con, etype, pairs, producer=f"{model} via {t.worker}"
        )
        t.out["type"] = etype
        return t.out

    def run(self, p: Pass) -> str | None:
        from prax import worker

        emb = embeddings.current()
        if emb is None:
            return None
        batch = p.fetch(self.name, limit=1)
        if not batch.get("type"):
            return None
        if batch.get("model") != emb.name:
            return (
                f"skipped: the door's names embed with {batch.get('model')},"
                f" this worker with {emb.name}"
            )
        p.note(f"resolving {len(batch['names'])} {batch['type']} names")
        rep = p.post(self.name, worker.do_resolve(batch, emb))
        return (
            f"{batch['type']}: {rep.get('applied', 0)} likely pairs"
            f" among {len(batch['names'])} names"
        )


class Adjudicate(Step):
    """The likely pairs nobody has decided, to a worker with the
    adjudicate step's model (a paid one: the worker spends only when told
    to); leased by the entity that would be dropped."""

    name = "adjudicate"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        from prax import resolution

        if models.resolve("adjudicate") is None:
            return h.nothing()
        items = []
        for c in resolution.plan(h.con).likely:
            if len(items) >= h.limit or not h.free(c.drop):
                continue
            items.append(
                {
                    "keep": c.keep,
                    "drop": c.drop,
                    "keep_name": c.keep_name,
                    "drop_name": c.drop_name,
                    "type": c.type,
                    "score": round(c.score, 4),
                }
            )
        h.lease([i["drop"] for i in items])
        return h.batch(items)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        from prax import resolution, work

        model = str(t.payload.get("model") or t.worker)
        items = list(t.payload.get("items") or [])
        same = [bool(x) for x in (t.payload.get("same") or [])]
        if len(same) != len(items):
            raise ValueError("adjudicate takes one decision per item")
        t.release([int(it["drop"]) for it in items])
        work._note_spend(t.con, self.name, t.payload.get("usage"))
        rep = resolution.decide(
            t.con, items, resolution.DecidedAdjudicator(model, same)
        )
        t.out["applied"] = rep.merged_likely
        t.out["declined"] = rep.declined
        return t.out

    def run(self, p: Pass) -> str | None:
        from prax import worker

        spec = models.resolve("adjudicate")
        if spec is None:
            return None  # the step is off on this host: nothing to say
        if spec.paid and not p.spend:
            return (
                f"skipped: the adjudicate step is {spec.name} (paid); --spend runs it"
            )
        items = p.fetch(self.name, limit=p.limit * 20).get("items") or []
        if not items:
            return None
        p.note(f"adjudicating {len(items)} likely pairs")
        rep = p.post(self.name, worker.do_adjudicate(items, spec))
        return (
            f"{rep.get('applied', 0)} merged, {rep.get('declined', 0)} kept apart"
            f" of {len(items)} pairs"
        )


REGISTERED = {s.name: s for s in (Typing(), Resolve(), Adjudicate())}
