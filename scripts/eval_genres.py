#!/usr/bin/env python
"""How well the local model says what a document is and what it is about,
measured against a person's labels (stage Z, step 2 of docs/PLAN.md).

    python scripts/eval_genres.py [--limit N] [--out FILE.jsonl] [--claude A.json ...]

The gold is every document a person labelled on the Review page's
"genre" tab (``meta.genres_by: human``), in two parts: **blind**, labelled
without a model's labels in front of them, which is the measure, and
**checked**, a model's labels a person confirmed or changed, which lean
towards what was ticked already and are reported apart.

Two methods, each on two views of a document (``writing.genres.view``):
the summary alone, and the summary with the opening of the text.

- ``yesno``: one question a label, P(yes) from the answer token, levels
  first and the labels under a likely level after (``probabilities``).
  Reported raw at 0.5 and calibrated: a Platt map per facet and kind
  (level or label), fitted on four fifths of the documents and applied to
  the fifth, in turn.
- ``list``: the labels as one list under a grammar (``listed``).
- ``hybrid``: the list proposes, the calibrated yes/no disposes: a listed
  label is kept when its probability is at least a threshold (or was not
  asked), and a label the list left out when it is 0.8 or more. What the
  genres step would cost: one list and the questions of its labels.

``--decision URL`` adds a decision model that speaks Jev's request format
(Jeff, Kev; library docs 10307, 13341, 13342): the document as the
``state``, every label a ``noul`` question in one request, calibrated like
the yes/no answers. ``--decision-name`` names it in the tables.

``--reuse FILE`` takes the yes/no answers of an earlier ``--out`` instead
of asking again.

Scored per label, micro-averaged: precision, recall and F1 for the genres
and the subjects (the labels under a level), and for the levels. A
``--claude`` file of labels given blind (``{id: {"g": [...], "s": [...]}}``)
is scored the same way on the documents it covers, as the reference a
second reader sets.

Opens the database read-only (``mode=ro``), like the other measurement
scripts; asks llama-server (the ``extract`` step's model until the
genres step has one of its own); writes nothing to the store.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, models
from prax.graph import calibration, ontology
from prax.writing import genres as gw

FOLDS = 5
VIEWS = {"summary": 0, "summary+opening": gw.OPENING}


def gold(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT id, title, text_hash, source_url, original_path, meta FROM documents"
        " WHERE json_extract(meta, '$.genres_by') = 'human' ORDER BY id"
    ).fetchall()
    out = []
    for i, title, th, url, path, meta in rows:
        m = json.loads(meta)
        text = ""
        if th:
            f = config.archive_dir() / th[:2] / th
            try:
                with f.open(encoding="utf-8", errors="replace") as fh:
                    text = fh.read(gw.OPENING + 200)
            except OSError:
                pass
        out.append(
            {
                "id": i,
                "title": title,
                "meta": m,
                "text": text,
                "where": (m.get("origin") or {}).get("path") or url or path,
                "blind": not m.get("genres_model"),
                "g": {g["genre"] for g in m.get("genres") or []},
                "s": {x["subject"] for x in m.get("subjects") or []},
            }
        )
    return out


def split(facet: ontology.Facet, labels: set[str]) -> tuple[set[str], set[str]]:
    """(levels, labels under a level)."""
    levels = {x for x in labels if facet.level_of(x) == x}
    return levels, labels - levels


def prf(pairs: list[tuple[set[str], set[str]]]) -> tuple[float, float, float]:
    tp = fp = fn = 0
    for said, truth in pairs:
        tp += len(said & truth)
        fp += len(said - truth)
        fn += len(truth - said)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def score(
    docs: list[dict[str, Any]],
    said: dict[int, dict[str, set[str]]],
    G: ontology.Facet,
    S: ontology.Facet,
) -> dict[str, tuple[float, float, float]]:
    out = {}
    for name, key, facet, part in (
        ("genres", "g", G, 1),
        ("levels", "g", G, 0),
        ("subjects", "s", S, 1),
        ("groups", "s", S, 0),
    ):
        pairs = []
        for d in docs:
            if d["id"] not in said or (key == "s" and not d["s"]):
                continue
            pairs.append(
                (
                    split(facet, said[d["id"]][key])[part],
                    split(facet, d[key])[part],
                )
            )
        out[name] = prf(pairs)
    return out


def calibrated(
    docs: list[dict[str, Any]],
    probs: dict[int, dict[str, dict[str, float]]],
    G: ontology.Facet,
    S: ontology.Facet,
) -> tuple[
    dict[int, dict[str, set[str]]],
    dict[str, Any],
    dict[int, dict[str, dict[str, float]]],
]:
    """Platt per facet and kind, fitted out of fold; the labels at 0.5,
    and every calibrated probability."""
    ids = [d["id"] for d in docs if d["id"] in probs]
    rng = random.Random(7)
    rng.shuffle(ids)
    fold = {i: k % FOLDS for k, i in enumerate(ids)}
    truth = {d["id"]: d for d in docs}
    said: dict[int, dict[str, set[str]]] = {i: {"g": set(), "s": set()} for i in ids}
    cal: dict[int, dict[str, dict[str, float]]] = {i: {"g": {}, "s": {}} for i in ids}
    raw_all: list[tuple[float, int]] = []
    cal_all: list[tuple[float, int]] = []
    for key, facet in (("g", G), ("s", S)):
        labels = facet.labels()
        for kind in (0, 1):
            wanted = [x for x in labels if (facet.level_of(x) == x) == (kind == 0)]

            def points(
                which: list[int], key: str = key, wanted: list[str] = wanted
            ) -> list[tuple[float, int, int, str]]:
                pts = []
                for i in which:
                    if key == "s" and not truth[i]["s"]:
                        continue
                    for x in wanted:
                        p = probs[i][key].get(x)
                        if p is None:
                            continue  # not asked: its level was unlikely
                        pts.append((p, int(x in truth[i][key]), i, x))
                return pts

            for k in range(FOLDS):
                train = points([i for i in ids if fold[i] != k])
                test = points([i for i in ids if fold[i] == k])
                if len({y for _, y, _, _ in train}) < 2:
                    platt = calibration.Platt()
                else:
                    platt = calibration.fit_platt(
                        [p for p, _, _, _ in train], [y for _, y, _, _ in train]
                    )
                for p, y, i, x in test:
                    q = platt(p)
                    cal[i][key][x] = q
                    raw_all.append((p, y))
                    cal_all.append((q, y))
                    if q >= 0.5:
                        said[i][key].add(x)
    rp, ry = [p for p, _ in raw_all], [y for _, y in raw_all]
    cp, cy = [p for p, _ in cal_all], [y for _, y in cal_all]
    return (
        said,
        {
            "ece raw": round(calibration.ece(rp, ry), 3),
            "ece calibrated": round(calibration.ece(cp, cy), 3),
            "brier raw": round(calibration.brier(rp, ry), 3),
            "brier calibrated": round(calibration.brier(cp, cy), 3),
            "questions": len(rp),
        },
        cal,
    )


def platt_all(
    docs: list[dict[str, Any]],
    probs: dict[int, dict[str, dict[str, float]]],
    G: ontology.Facet,
    S: ontology.Facet,
) -> dict[str, list[float]]:
    """The Platt maps fitted on every labelled document: what the genres
    step reads from prax.yaml (``steps.genres.platt``)."""
    truth = {d["id"]: d for d in docs}
    out = {}
    for key, facet, name in (("g", G, "genres"), ("s", S, "subjects")):
        for kind, part in ((0, "levels"), (1, "labels")):
            pts = [
                (p, int(x in truth[i][key]))
                for i, pr in probs.items()
                if i in truth and not (key == "s" and not truth[i]["s"])
                for x, p in pr[key].items()
                if (facet.level_of(x) == x) == (kind == 0)
            ]
            if len({y for _, y in pts}) < 2:
                continue
            m = calibration.fit_platt([p for p, _ in pts], [y for _, y in pts])
            out[f"{name} {part}"] = [round(m.a, 4), round(m.b, 4)]
    return out


def decide(
    url: str, document: str, facet: ontology.Facet, *, about: bool, model: str
) -> dict[str, float]:
    """Every label of a facet as one yes/no question to a Jev-format
    decision model, in one request over one reading of the document."""
    import urllib.request

    said = {
        x: d for lv in facet.levels for x, d in ((lv.name, lv.description), *lv.genres)
    }
    questions = {
        x: {"type": "noul", "instructions": gw.question(facet, x, said[x], about=about)}
        for x in facet.labels()
    }
    body = {"model": model, "state": document, "questions": questions}
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/systemone",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    got = json.loads(urllib.request.urlopen(req, timeout=600).read())
    return {x: float(v["noul"]) for x, v in got["answers"].items() if "noul" in v}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=Path, help="every answer, as JSON lines")
    ap.add_argument("--claude", type=Path, nargs="*", default=[])
    ap.add_argument("--methods", default="yesno,list")
    ap.add_argument("--reuse", type=Path, help="yes/no answers of an earlier --out")
    ap.add_argument("--decision", help="a Jev-format decision model's URL")
    ap.add_argument("--decision-name", default="decision model")
    ap.add_argument("--decision-model", default="jeff-latest")
    ap.add_argument("--views", default=",".join(VIEWS), help="which views to read")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    spec = models.resolve("extract")  # the genres step's own model once it exists
    if spec is None or not spec.base_url:
        raise SystemExit("no local model for the genres step")
    runtime = models.runtime(spec)
    G, S = ontology.genres(), ontology.subjects()
    con = sqlite3.connect(f"file:{config.db_path()}?mode=ro", uri=True)
    docs = gold(con)
    if a.limit:
        docs = docs[: a.limit]
    blind = [d for d in docs if d["blind"]]
    print(f"{len(docs)} labelled documents, {len(blind)} of them blind\n")

    reused: dict[tuple[int, str], dict[str, dict[str, float]]] = {}
    if a.reuse:
        for line in a.reuse.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if "yesno" in row:
                reused[(int(row["id"]), row["view"])] = row["yesno"]
    results: dict[str, dict[int, dict[str, set[str]]]] = {}
    extra: dict[str, dict[str, Any]] = {}
    seconds: dict[str, float] = defaultdict(float)
    fitted: dict[str, dict[str, list[float]]] = {}
    for vname, opening in VIEWS.items():
        if vname not in a.views.split(","):
            continue
        views = {
            d["id"]: gw.view(
                d["title"], d["meta"], d["text"], opening=opening, where=d["where"]
            )
            for d in docs
        }
        if "yesno" in a.methods:
            probs: dict[int, dict[str, dict[str, float]]] = {}
            for n, d in enumerate(docs, 1):
                if (d["id"], vname) in reused:
                    probs[d["id"]] = reused[(d["id"], vname)]
                    continue
                t0 = time.monotonic()
                probs[d["id"]] = {
                    "g": gw.probabilities(
                        spec.base_url, spec.model, views[d["id"]], G, about=False
                    ),
                    "s": gw.probabilities(
                        spec.base_url, spec.model, views[d["id"]], S, about=True
                    ),
                }
                seconds[f"yesno, {vname}"] += time.monotonic() - t0
                if a.out:
                    with a.out.open("a", encoding="utf-8") as f:
                        f.write(
                            json.dumps(
                                {"id": d["id"], "view": vname, "yesno": probs[d["id"]]}
                            )
                            + "\n"
                        )
                print(f"\r yesno {vname}: {n}/{len(docs)}", end="", flush=True)
            print()
            results[f"yesno raw, {vname}"] = {
                i: {k: {x for x, p in v.items() if p >= 0.5} for k, v in pr.items()}
                for i, pr in probs.items()
            }
            said, cal_stats, cal = calibrated(docs, probs, G, S)
            results[f"yesno calibrated, {vname}"] = said
            extra[f"yesno, {vname}"] = cal_stats
        if a.decision:
            dprobs: dict[int, dict[str, dict[str, float]]] = {}
            for n, d in enumerate(docs, 1):
                t0 = time.monotonic()
                dprobs[d["id"]] = {
                    "g": decide(
                        a.decision,
                        views[d["id"]],
                        G,
                        about=False,
                        model=a.decision_model,
                    ),
                    "s": decide(
                        a.decision,
                        views[d["id"]],
                        S,
                        about=True,
                        model=a.decision_model,
                    ),
                }
                seconds[f"{a.decision_name}, {vname}"] += time.monotonic() - t0
                if a.out:
                    with a.out.open("a", encoding="utf-8") as f:
                        f.write(
                            json.dumps(
                                {
                                    "id": d["id"],
                                    "view": vname,
                                    a.decision_name: dprobs[d["id"]],
                                }
                            )
                            + "\n"
                        )
                print(
                    f"\r {a.decision_name} {vname}: {n}/{len(docs)}", end="", flush=True
                )
            print()
            results[f"{a.decision_name} raw, {vname}"] = {
                i: {k: {x for x, p in v.items() if p >= 0.5} for k, v in pr.items()}
                for i, pr in dprobs.items()
            }
            dsaid, dstats, _ = calibrated(docs, dprobs, G, S)
            results[f"{a.decision_name} calibrated, {vname}"] = dsaid
            extra[f"{a.decision_name}, {vname}"] = dstats
        if "list" in a.methods:
            lists: dict[int, dict[str, set[str]]] = {}
            for n, d in enumerate(docs, 1):
                t0 = time.monotonic()
                lists[d["id"]] = {
                    "g": set(
                        G.implied(gw.listed(runtime, views[d["id"]], G, about=False))
                    ),
                    "s": set(
                        S.implied(gw.listed(runtime, views[d["id"]], S, about=True))
                    ),
                }
                seconds[f"list, {vname}"] += time.monotonic() - t0
                if a.out:
                    with a.out.open("a", encoding="utf-8") as f:
                        row = {k: sorted(v) for k, v in lists[d["id"]].items()}
                        f.write(
                            json.dumps({"id": d["id"], "view": vname, "list": row})
                            + "\n"
                        )
                print(f"\r list {vname}: {n}/{len(docs)}", end="", flush=True)
            print()
            results[f"list, {vname}"] = lists
            if "yesno" in a.methods:
                for keep in (0.3, 0.5):
                    hybrid: dict[int, dict[str, set[str]]] = {}
                    for i, listed in lists.items():
                        hybrid[i] = {}
                        for key, facet in (("g", G), ("s", S)):
                            q = cal.get(i, {}).get(key, {})
                            chosen = {x for x in listed[key] if q.get(x, 1.0) >= keep}
                            chosen |= {x for x, v in q.items() if v >= 0.8}
                            hybrid[i][key] = set(facet.implied(chosen))
                    results[f"hybrid {keep:g}, {vname}"] = hybrid
                # what the genres step does: only the listed labels asked
                lean: dict[int, dict[str, set[str]]] = {}
                for i, listed in lists.items():
                    lean[i] = {}
                    for key, facet in (("g", G), ("s", S)):
                        q = cal.get(i, {}).get(key, {})
                        chosen = {x for x in listed[key] if q.get(x, 1.0) >= 0.3}
                        lean[i][key] = set(facet.implied(chosen))
                results[f"listed, then asked, 0.3, {vname}"] = lean
                fitted[vname] = platt_all(docs, probs, G, S)

    for path in a.claude:
        for k, v in json.loads(path.read_text(encoding="utf-8")).items():
            results.setdefault("Claude, blind", {})[int(k)] = {
                "g": set(G.implied(x for x in v["g"] if x in G.labels())),
                "s": set(S.implied(x for x in v["s"] if x in S.labels())),
            }

    for part, subset in (
        ("blind", blind),
        ("checked", [d for d in docs if not d["blind"]]),
    ):
        if not subset:
            continue
        print(f"\n### {part} ({len(subset)} documents): precision / recall / F1\n")
        print("| method | genres | levels | subjects | groups |\n|---|---|---|---|---|")
        for name, said in results.items():
            got = score([d for d in subset if d["id"] in said], said, G, S)
            if not any(v[2] for v in got.values()):
                continue
            cells = " | ".join(
                f"{p:.2f} / {r:.2f} / **{f:.2f}**" for p, r, f in got.values()
            )
            print(f"| {name} | {cells} |")
    print("\n### calibration of the yes/no answers (out of fold)\n")
    for name, stats in extra.items():
        print(f"- {name}: {stats}")
    print("\n### Platt maps fitted on every labelled document (a, b)\n")
    for vname, maps in fitted.items():
        print(f"- {vname}: {maps}")
    print("\n### seconds a document\n")
    for name, s in seconds.items():
        print(f"- {name}: {s / max(1, len(docs)):.1f}")


if __name__ == "__main__":
    main()
