#!/usr/bin/env python
"""What a library's face settings should be (plan item AT steps 4, 5 and
5b): ``faces.match.line``, ``faces.match.margin`` and
``faces.match.neighbours``, and how the ``facegroups`` pass did.

    python scripts/eval_faces.py [--db PATH] [--neighbours 5]

Four tables, each measured on the owner's own answers, never on a model:

- the match: every face a person named is matched against the other
  named faces (never one of its own picture), as the door matches an open
  face (``prax.ml.matching``). A match is right when it names the face's
  own person. For each line and margin: how many would be offered, how
  many of those wrongly; the faces answered "nobody" offered (a stranger
  given a name); the open faces offered.
- the offers: the offers a person answered (a face named under a run of
  kind ``match``, or refused), by the similarity of the person offered:
  what step 5b, a tag without asking past a line, would have got wrong.
- the sizes: the named faces matched right and wrong by their size in
  pixels, for whether a small face should be offered at all.
- the groups: how many groups the last ``facegroups`` pass made, and how
  many hold the named faces of more than one person.

The numbers of 2026-10-27 are in ``docs/log.md``. Another library, another
camera or other faces than the owner's put SFace's similarities elsewhere:
this is how that library sets its own line in prax.yaml.

Opens the database read-only (``mode=ro``) and the face index as a view,
like the other measurement scripts; writes nothing. Prints counts only:
no name, no picture, no face.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from prax import config
from prax.ml import faces as faces_ml
from prax.ml import matching

LINES = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
MARGINS = (0.0, 0.05, 0.10)
BANDS = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.80)
SIZES = (40, 80)


def _vectors(data: Path, model: str) -> dict[int, np.ndarray]:
    """Every vector of the face index of ``model``, its delta's last."""
    from usearch.index import Index

    out: dict[int, np.ndarray] = {}
    for name in (
        f"vectors-face-{model}.usearch",
        f"vectors-face-{model}.delta.usearch",
    ):
        path = data / name
        if not path.exists():
            continue
        idx = Index.restore(str(path), view=True)
        keys = np.asarray(idx.keys)
        for k, v in zip(keys.tolist(), idx.get(keys), strict=True):
            out[int(k)] = np.asarray(v, dtype=np.float32)
    return out


def _match(
    named: list[tuple[int, int, int]],
    vecs: dict[int, np.ndarray],
    asked: list[tuple[int, int]],
    k: int,
) -> dict[int, matching.Match]:
    """Each asked ``(face, picture)``'s match against the named faces of
    other pictures."""
    by_doc: dict[int, list[int]] = defaultdict(list)
    for face, doc in asked:
        by_doc[doc].append(face)
    rows = matching.unit(np.stack([vecs[f] for f, _, _ in named]))
    docs = np.asarray([d for _, d, _ in named])
    people = [p for _, _, p in named]
    out: dict[int, matching.Match] = {}
    for doc, ask in by_doc.items():
        keep = docs != doc
        if not keep.any():
            continue
        got = matching.nearest_people(
            rows[keep],
            [p for p, kept in zip(people, keep, strict=True) if kept],
            matching.unit(np.stack([vecs[f] for f in ask])),
            k=k,
        )
        out.update({f: m for f, m in zip(ask, got, strict=True) if m is not None})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--neighbours", type=int, default=None)
    args = ap.parse_args()
    db = args.db or config.data_dir() / "prax.db"
    model = faces_ml.recognizer().name
    k = args.neighbours or config.whole("faces.match.neighbours", default=5)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    vecs = _vectors(db.parent, model)
    faces = {
        int(r[0]): (int(r[1]), int(r[2] or 0))
        for r in con.execute(
            "SELECT id, doc_id, px FROM faces"
            " WHERE gone_at IS NULL AND vector_model = ?",
            (model,),
        )
        if int(r[0]) in vecs
    }
    answers = {
        int(r[0]): (str(r[1]), r[2], str(r[3] or ""))
        for r in con.execute(
            "SELECT n.face_id, n.verdict, COALESCE(e.canonical_id, e.id), n.run"
            " FROM face_names n LEFT JOIN entities e ON e.id = n.entity_id"
            " WHERE n.ended_at IS NULL"
        )
    }
    named = [
        (f, faces[f][0], int(a[1]))
        for f, a in answers.items()
        if f in faces and a[0] == "person" and a[1] is not None
    ]
    person = {f: p for f, _, p in named}
    print(
        f"{model}: {len(faces)} faces with a vector, {len(named)} named,"
        f" {len(set(person.values()))} people, k = {k}"
    )
    if len(set(person.values())) < 2:
        print("fewer than two people named: nothing to measure yet")
        return

    # ------------------------------------------------------------ the match
    named_m = _match(named, vecs, [(f, d) for f, d, _ in named], k)
    nobody = [
        (f, faces[f][0]) for f, a in answers.items() if f in faces and a[0] == "nobody"
    ]
    open_ = [(f, d) for f, (d, _) in faces.items() if f not in answers]
    nobody_m = _match(named, vecs, nobody, k)
    open_m = _match(named, vecs, open_, k)
    print(
        f"\nthe match: named offered (wrong) | nobody offered, of {len(nobody)}"
        f" | open offered, of {len(open_)}"
    )
    for line in LINES:
        for margin in MARGINS:

            def past(m: matching.Match) -> bool:
                return m.similarity >= line and m.margin >= margin  # noqa: B023

            offered = [f for f, m in named_m.items() if past(m)]
            wrong = sum(named_m[f].person != person[f] for f in offered)
            share = 100 * wrong / max(len(offered), 1)
            print(
                f"  line {line:.2f} margin {margin:.2f}: {len(offered):6d}"
                f" ({wrong:4d}, {share:4.1f}%) | {sum(map(past, nobody_m.values())):5d}"
                f" | {sum(map(past, open_m.values())):6d}"
            )

    # ----------------------------------------------------------- the offers
    refused = [
        (int(r[0]), int(r[1]))
        for r in con.execute(
            "SELECT x.face_id, COALESCE(e.canonical_id, e.id) FROM face_refusals x"
            " JOIN entities e ON e.id = x.entity_id WHERE x.ended_at IS NULL"
        )
        if int(r[0]) in faces
    ]
    taken = [
        (f, person[f])
        for f, a in answers.items()
        if f in person and a[2].startswith("match-")
    ]
    rows = matching.unit(np.stack([vecs[f] for f, _, _ in named]))
    docs = np.asarray([d for _, d, _ in named])
    who = np.asarray([p for _, _, p in named])
    ids = np.asarray([f for f, _, _ in named])
    table: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    for yes, (face, offered_to) in [(True, t) for t in taken] + [
        (False, r) for r in refused
    ]:
        sims = rows @ matching.unit(vecs[face][None])[0]
        sims[(docs == faces[face][0]) | (ids == face)] = -np.inf
        mine = sims[who == offered_to]
        sim = float(mine.max()) if len(mine) else -1.0
        band = max((b for b in BANDS if sim >= b), default=0.0)
        table[band][0 if yes else 1] += 1
    print(
        f"\nthe offers answered: {len(taken)} taken, {len(refused)} refused,"
        " by similarity"
    )
    for band in (0.0, *BANDS):
        if sum(table[band]):
            yes, no = table[band]
            print(f"  >= {band:.2f}: {yes:5d} taken / {no:5d} refused")

    # ------------------------------------------------------------ the sizes
    line, margin = (
        config.number("faces.match.line", default=0.45),
        config.number("faces.match.margin", default=0.05),
    )
    sizes: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f, m in named_m.items():
        if m.similarity < line or m.margin < margin:
            continue
        px = faces[f][1]
        band_name = (
            f"under {SIZES[0]} px"
            if px < SIZES[0]
            else (
                f"{SIZES[0]} to {SIZES[1]} px"
                if px < SIZES[1]
                else f"{SIZES[1]} px and over"
            )
        )
        sizes[band_name][0 if m.person == person[f] else 1] += 1
    print(f"\nthe sizes, offered at line {line:.2f} margin {margin:.2f}: right / wrong")
    for band_name in (
        f"under {SIZES[0]} px",
        f"{SIZES[0]} to {SIZES[1]} px",
        f"{SIZES[1]} px and over",
    ):
        right, wrong = sizes[band_name]
        print(f"  {band_name:>16}: {right:5d} / {wrong:4d}")

    # ----------------------------------------------------------- the groups
    members: dict[int, list[int]] = defaultdict(list)
    for r in con.execute("SELECT group_id, face_id FROM face_group_members"):
        members[int(r[0])].append(int(r[1]))
    mixed = sum(
        len({person[f] for f in fs if f in person}) > 1 for fs in members.values()
    )
    grouped = {f for fs in members.values() for f in fs}
    sized = Counter(min(len(fs), 50) // 10 * 10 for fs in members.values())
    print(
        f"\nthe groups: {len(members)}, holding {len(grouped)} faces;"
        f" {mixed} hold more than one named person;"
        f" {sum(f not in grouped for f in person)} named faces in no group"
    )
    for size in sorted(sized):
        label = "50 and more" if size == 50 else f"{max(size, 1)} to {size + 9}"
        print(f"  {label:>12} faces: {sized[size]} groups")


if __name__ == "__main__":
    main()
