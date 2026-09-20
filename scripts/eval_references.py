#!/usr/bin/env python
"""How well the library's own reference lists resolve to its documents.

Reads only. For every document with a bibliography (chunks under a
References/Bibliography heading), the entries are split and parsed
(``prax.references``), each one matched against the library: an id
(DOI, arXiv) when printed, else the document-field index's candidates
(``documents_fts``: title, creators, venue) scored by ``similarity``.

The labelled set is what Crossref already found: the ``cites`` edges
whose target entity is named like a library document. For the citing
documents Crossref resolved, the local resolver's sure matches are
compared with those — precision and recall on the overlap — and for the
rest of the library (no DOI, never resolved) the numbers say how many
new links the pass would write.

    python scripts/eval_references.py                 # the whole library
    python scripts/eval_references.py --limit 300     # a sample
    python scripts/eval_references.py --show 12       # print some matches
    python scripts/eval_references.py --threshold 0.85 --margin 0.05

Writes nothing; the report goes to stdout (docs/eval keeps the dated one).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import references as refs
from prax import store

HEADINGS = ("references", "bibliography", "literatur", "works cited", "literature")
_TOKEN = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
STOP = refs._NOISE | {
    "for",
    "with",
    "from",
    "using",
    "based",
    "analysis",
    "approach",
    "method",
    "methods",
    "model",
    "models",
    "system",
    "systems",
    "study",
    "new",
    "via",
    "towards",
    "toward",
}


def bibliographies(con: sqlite3.Connection, limit: int | None) -> dict[int, str]:
    """doc_id -> the text of its bibliography chunks, in order."""
    rows = con.execute(
        "SELECT doc_id, id, text, json_extract(heading, '$[#-1]') AS h FROM chunks"
        " WHERE kind = 'text' AND heading IS NOT NULL ORDER BY doc_id, id"
    )
    out: dict[int, list[str]] = {}
    for r in rows:
        h = (r["h"] or "").strip().lower()
        if not any(h.startswith(x) or h.endswith(x) for x in HEADINGS):
            continue
        out.setdefault(r["doc_id"], []).append(r["text"])
        if limit and len(out) > limit:
            out.pop(r["doc_id"])
            break
    return {k: "\n\n".join(v) for k, v in out.items()}


def library(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """id -> title, creators, year, doi, arxiv of every live document."""
    lib: dict[int, dict[str, Any]] = {}
    for r in con.execute(
        "SELECT id, title, meta FROM documents WHERE title IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    ):
        meta = json.loads(r["meta"] or "{}")
        year = None
        for key in ("year", "date", "published"):
            m = re.search(r"(19|20)\d{2}", str(meta.get(key) or ""))
            if m:
                year = int(m.group(0))
                break
        lib[r["id"]] = {
            "title": r["title"],
            "creators": [c.get("name", "") for c in meta.get("creators") or []],
            "year": year,
            "doi": (meta.get("doi") or "").lower().strip() or None,
            "arxiv": (meta.get("arxiv") or "").lower().strip() or None,
        }
    return lib


def labels(con: sqlite3.Connection) -> dict[int, set[int]]:
    """citing doc -> the library documents Crossref says it cites (an edge
    whose target entity is named like a document title)."""
    titles: dict[str, int] = {}
    for r in con.execute(
        "SELECT id, title FROM documents WHERE title IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    ):
        titles.setdefault(refs.normalize_title(r["title"]), r["id"])
    out: dict[int, set[int]] = {}
    for r in con.execute(
        "SELECT e.source_doc AS src, n.name FROM edges e"
        " JOIN entities n ON n.id = e.dst"
        " WHERE e.rel = 'cites' AND e.valid_to IS NULL AND e.producer IN"
        " ('crossref', 'openalex') AND e.source_doc IS NOT NULL"
    ):
        target = titles.get(refs.normalize_title(r["name"]))
        if target and target != r["src"]:
            out.setdefault(r["src"], set()).add(target)
    return out


def candidates(con: sqlite3.Connection, ref: refs.Reference, k: int = 10) -> list[int]:
    """Library documents whose field shares the reference's title words:
    the rarer half of the title's tokens OR-ed, BM25's top k."""
    toks = [t.lower() for t in _TOKEN.findall(ref.title)]
    toks = [t for t in toks if t not in STOP]
    if len(toks) < 2:
        return []
    expr = " OR ".join(f'"{t}"' for t in toks[:12])
    try:
        rows = con.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?"
            " ORDER BY bm25(documents_fts) LIMIT ?",
            (expr, k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r[0] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, default=None, help="documents to read")
    ap.add_argument("--show", type=int, default=0, help="print this many matches")
    ap.add_argument("--threshold", type=float, default=refs.THRESHOLD)
    ap.add_argument("--margin", type=float, default=refs.MARGIN)
    args = ap.parse_args()
    con = store.connect()
    con.execute("PRAGMA query_only = ON")
    t0 = time.perf_counter()
    lib = library(con)
    by_doi = {v["doi"]: k for k, v in lib.items() if v["doi"]}
    by_arxiv = {v["arxiv"]: k for k, v in lib.items() if v["arxiv"]}
    gold = labels(con)
    bibs = bibliographies(con, args.limit)
    print(
        f"{len(lib):,} documents; {len(bibs):,} with a bibliography;"
        f" {sum(len(v) for v in gold.values()):,} Crossref links into the library"
        f" from {len(gold):,} documents"
    )
    tally: Counter[str] = Counter()
    found: dict[int, dict[int, tuple[float, str]]] = {}
    shown = 0
    for doc_id, text in bibs.items():
        ents = refs.entries(text)
        tally["entries"] += len(ents)
        for e in ents:
            ref = refs.parse(e)
            if not ref.title:
                tally["untitled"] += 1
                continue
            tally["titled"] += 1
            if ref.year:
                tally["with year"] += 1
            if ref.surnames:
                tally["with surnames"] += 1
            targets: list[int] = []
            how, scores = "none", {}
            if ref.doi and ref.doi in by_doi:
                targets, how = [by_doi[ref.doi]], "doi"
            elif ref.arxiv and ref.arxiv in by_arxiv:
                targets, how = [by_arxiv[ref.arxiv]], "arxiv"
            else:
                scored = []
                for cid in candidates(con, ref):
                    d = lib.get(cid)
                    if not d or cid == doc_id:
                        continue
                    s = refs.similarity(
                        ref, title=d["title"], creators=d["creators"], year=d["year"]
                    )
                    scored.append((cid, s))
                targets, how = refs.match(
                    scored, threshold=args.threshold, margin=args.margin
                )
                scores = dict(scored)
            tally[f"matched:{how}"] += 1
            for target in targets:
                if target == doc_id:
                    continue
                score = scores.get(target, 1.0)
                found.setdefault(doc_id, {})[target] = (score, how)
                if shown < args.show:
                    shown += 1
                    print(
                        f"  doc {doc_id} → {target} [{how} {score:.2f}]"
                        f" {ref.title[:70]!r} ~ {lib[target]['title'][:70]!r}"
                    )
    # against the labels: the documents Crossref resolved and we read
    tp = fp = fn = 0
    for doc_id, truth in gold.items():
        mine = set(
            found.get(doc_id, {})
        )  # the ambiguous ones too: twins in the library
        if doc_id not in bibs:
            continue
        tp += len(mine & truth)
        fp += len(mine - truth)
        fn += len(truth - mine)
    sure_links = sum(
        1 for d in found.values() for s, how in d.values() if how != "ambiguous"
    )
    amb_links = sum(
        1 for d in found.values() for s, how in d.values() if how == "ambiguous"
    )
    new_docs = sum(1 for d in found if d not in gold)
    new_links = sum(
        1
        for d, m in found.items()
        if d not in gold
        for s, how in m.values()
        if how != "ambiguous"
    )
    print(
        f"\nentries {tally['entries']:,}: titled {tally['titled']:,},"
        f" untitled {tally['untitled']:,}; with a year {tally['with year']:,},"
        f" with surnames {tally['with surnames']:,}"
    )
    print(
        "matched:",
        {
            k.split(":")[1]: v
            for k, v in sorted(tally.items())
            if k.startswith("matched:")
        },
    )
    print(
        f"library links: {sure_links:,} sure + {amb_links:,} ambiguous,"
        f" from {len(found):,} citing documents"
    )
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    print(
        f"against Crossref's links (the citing documents read here):"
        f" tp {tp:,} fp {fp:,} fn {fn:,} → precision {prec:.2f}, recall {rec:.2f}"
    )
    print(
        f"new: {new_links:,} sure links from {new_docs:,} documents Crossref never"
        f" resolved"
    )
    print(f"({time.perf_counter() - t0:.0f} s)")


if __name__ == "__main__":
    main()
