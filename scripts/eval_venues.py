#!/usr/bin/env python
"""Whether the venue tier (``prax.graph.venues``, the ``venue`` tier of
``prax resolve``) merges and links the names it should, measured against
the Zotero records of the papers that name them.

    python scripts/eval_venues.py [--db PATH] [--save PLAN.json]
    python scripts/eval_venues.py --diff OLD.json   # what a change moved
    python scripts/eval_venues.py --sample 30 --seed 5

The tier runs over the live venue names as ``prax resolve`` would (the
library's acronyms table for the expansions) and is scored against an
independent reading: a paper that says it was "published in" a venue
name and has a Zotero record with its venue (``proceedingsTitle``,
``conferenceName``, ``publicationTitle``, ``bookTitle``) says which
venue that name stood for there. A merge of two names agrees when the
record behind the dropped name reads as the kept name's series and
edition, and disagrees when it reads as another edition or another
series. An edition linked ``part_of`` a series agrees when its record
names that series with an edition. A name with no record is unknown.

``--save`` keeps the plan, ``--diff`` prints what moved since a saved
plan: the measurement for a change to the venue words (the research
pack's ``lexicon.yaml``) or to the reader, before it merges at night.
Names that only documents behind the wall name are counted, never
printed.

Opens the database read-only (``mode=ro``), like the other measurement
scripts; writes nothing to the store.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config
from prax.graph import venues

RECORD_FIELDS = ("proceedingsTitle", "conferenceName", "publicationTitle", "bookTitle")


def _entities(con: sqlite3.Connection) -> list[tuple[int, str, int]]:
    return [
        (int(r[0]), str(r[1]), int(r[2]))
        for r in con.execute(
            """
            SELECT v.id, v.name, count(e.id)
            FROM entities v
            LEFT JOIN edges e ON (e.src = v.id OR e.dst = v.id) AND e.valid_to IS NULL
            WHERE v.type = 'venue' AND v.canonical_id IS NULL
            GROUP BY v.id
            """
        )
    ]


def _shown(con: sqlite3.Connection) -> set[int]:
    """The venues some open document names: the rest are counted only."""
    return {
        int(r[0])
        for r in con.execute(
            """
            SELECT DISTINCT e.dst FROM edges e
            JOIN documents d ON d.id = e.source_doc
            WHERE e.rel = 'published_in' AND e.valid_to IS NULL
              AND d.sensitivity IS NULL
            """
        )
    }


def _records(con: sqlite3.Connection) -> dict[int, list[str]]:
    """Each venue's Zotero venues: what the records of the papers that
    were "published in" it say."""
    out: dict[int, list[str]] = defaultdict(list)
    for dst, meta in con.execute(
        """
        SELECT e.dst, d.meta FROM edges e JOIN documents d ON d.id = e.source_doc
        WHERE e.rel = 'published_in' AND e.valid_to IS NULL
          AND json_extract(d.meta, '$.zotero') IS NOT NULL
        """
    ):
        fields = json.loads(meta).get("fields") or {}
        said = next((fields[k] for k in RECORD_FIELDS if fields.get(k)), None)
        if said:
            out[int(dst)].append(str(said))
    return out


def _same(record: str, name: str, expansions: dict[str, set[str]]) -> str:
    """``agree`` when a record reads as the name's series and edition,
    ``edition`` when as its series in another edition, ``series`` when as
    another series."""
    r, v = venues.read(record), venues.read(name)
    one = bool(venues.series_names(r, expansions) & venues.series_names(v, expansions))
    # a record that carries the name's acronym is of its series: "Proceedings
    # of the 28th international conference on Human factors… - CHI '10"
    one = one or (v.acronym is not None and v.acronym == r.acronym)
    if not one:
        return "series"
    if v.edition is not None and r.edition is not None and v.edition != r.edition:
        return "edition"
    return "agree"


def _score(
    pairs: list[tuple[int, int]],
    names: dict[int, str],
    records: dict[int, list[str]],
    expansions: dict[str, set[str]],
    *,
    edition_link: bool,
) -> tuple[dict[str, int], list[tuple[str, int, int, str]]]:
    counts: dict[str, int] = defaultdict(int)
    wrong: list[tuple[str, int, int, str]] = []
    for kept, other in pairs:
        said = records.get(other) or []
        if not said:
            counts["unknown"] += 1
            continue
        verdicts = [_same(s, names[kept], expansions) for s in said]
        if edition_link:
            # an edition is part of its series: its record names the series
            # (any edition of it)
            verdicts = ["agree" if v == "edition" else v for v in verdicts]
        verdict = max(set(verdicts), key=verdicts.count)
        counts[verdict] += 1
        if verdict != "agree":
            wrong.append((verdict, kept, other, said[0]))
    return dict(counts), wrong


def _merged(con: sqlite3.Connection, run: str | None) -> tuple[str | None, list]:
    """The venue merges a resolve run made, (kept, folded): the last run
    that made any unless ``run`` names one."""
    if run is None:
        row = con.execute(
            "SELECT max(merged_run) FROM entities WHERE type = 'venue'"
            " AND merged_by = 'resolution' AND merged_run LIKE 'resolve-%'"
        ).fetchone()
        run = row[0] if row else None
    pairs = [
        (int(r[0]), int(r[1]))
        for r in con.execute(
            "SELECT canonical_id, id FROM entities WHERE type = 'venue'"
            " AND merged_run = ? AND canonical_id IS NOT NULL",
            (run,),
        )
    ]
    return run, pairs


def _plan(con: sqlite3.Connection) -> tuple[venues.Plan, dict[int, str], dict]:
    rows = _entities(con)
    names = {i: n for i, n, _ in rows}
    acronyms = {v.acronym for _, n, _ in rows if (v := venues.read(n)).acronym}
    expansions = {
        a: {
            str(r[0])
            for r in con.execute(
                "SELECT expansion FROM acronyms WHERE acronym = ? AND docs >= 2"
                " ORDER BY docs DESC, expansion LIMIT 5",
                (a.lower(),),
            )
        }
        for a in acronyms
    }
    return venues.plan(rows, expansions), names, expansions


def _print_pairs(
    title: str, pairs: list[tuple[int, int]], names: dict[int, str], shown: set[int]
) -> None:
    hidden = sum(1 for a, b in pairs if a not in shown or b not in shown)
    print(f"{title}: {len(pairs)}" + (f" ({hidden} behind the wall)" if hidden else ""))
    for a, b in pairs:
        if a in shown and b in shown:
            print(f"  {a} {names.get(a, '?')!r} <- {b} {names.get(b, '?')!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(config.data_dir() / "prax.db"))
    ap.add_argument("--save", metavar="PLAN.json", help="keep the plan in a file")
    ap.add_argument("--diff", metavar="PLAN.json", help="what moved since a saved plan")
    ap.add_argument("--sample", type=int, default=0, help="print N merges to read")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument(
        "--run", help="the resolve run whose merges to score (default: the last)"
    )
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{Path(args.db).as_posix()}?mode=ro", uri=True)
    plan, names, expansions = _plan(con)
    shown = _shown(con)
    records = _records(con)
    print(
        f"venues: {len(names)} names, {len(records)} with a Zotero record;"
        f" plan: {len(plan.merges)} merges, {len(plan.editions)} editions"
    )
    run, done = _merged(con, args.run)
    # the names the run folded, which the plan no longer reads
    every = dict(con.execute("SELECT id, name FROM entities WHERE type = 'venue'"))
    names = {**every, **names}
    for title, pairs, link in (
        (f"merged by {run}", done, False),
        ("merges", plan.merges, False),
        ("editions", plan.editions, True),
    ):
        ordered = [(b, a) for a, b in pairs] if link else pairs
        counts, wrong = _score(ordered, names, records, expansions, edition_link=link)
        known = sum(v for k, v in counts.items() if k != "unknown")
        share = f"{counts.get('agree', 0) / known:.2f}" if known else "n/a"
        print(f"{title}: {counts} (agree {share} of those with a record)")
        for verdict, kept, other, said in wrong[:15]:
            if kept in shown and other in shown:
                print(
                    f"  {verdict:<8} {kept} {names[kept]!r} <- {other}"
                    f" {names[other]!r}  (record: {said!r})"
                )
    if args.sample:
        rnd = random.Random(args.seed)
        visible = [p for p in plan.merges if p[0] in shown and p[1] in shown]
        _print_pairs(
            "sample of merges",
            rnd.sample(visible, min(args.sample, len(visible))),
            names,
            shown,
        )
    if args.diff:
        old = json.loads(Path(args.diff).read_text(encoding="utf-8"))
        old_names = {int(k): v for k, v in old["names"].items()}
        for key, now in (("merges", plan.merges), ("editions", plan.editions)):
            before = {tuple(p) for p in old[key]}
            after = set(now)
            both = {**old_names, **names}
            _print_pairs(f"{key} added", sorted(after - before), both, shown)
            _print_pairs(f"{key} gone", sorted(before - after), both, shown)
    if args.save:
        Path(args.save).write_text(
            json.dumps(
                {
                    "merges": plan.merges,
                    "editions": plan.editions,
                    "names": {str(k): v for k, v in names.items()},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"plan kept in {args.save}")


if __name__ == "__main__":
    main()
