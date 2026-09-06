#!/usr/bin/env python3
"""Run several extractors over a sample of PDFs and write a comparison.

    python scripts/compare_extractors.py --ids 12 34 56 --out docs/eval/extractors
    python scripts/compare_extractors.py --sample 20 --out C:/tmp/cmp

Reads originals through ``prax.store`` and writes nothing to the store; each
extractor's text goes to ``<out>/<doc_id>.<extractor>.md`` next to a
``summary.md`` with per-document metrics (seconds, characters, Markdown table
rows, headings, formula-ish lines, replacement characters). The numbers say
which extractor to trust on *this* library; read a few of the texts too.

``--sample N`` picks the N indexed PDFs whose current text mentions tables
most often (a cheap proxy for "table-heavy"), which is where the extractors
differ most.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import parsers, store

TABLE_ROW = re.compile(r"^\|.*\|\s*$", re.MULTILINE)
HEADING = re.compile(r"^#{1,6} ", re.MULTILINE)
FORMULA = re.compile(r"[=∑∫√α-ωΑ-Ω]|\\frac|\^\{|_\{")


def metrics(text: str) -> dict[str, int]:
    return {
        "chars": len(text),
        "table_rows": len(TABLE_ROW.findall(text)),
        "headings": len(HEADING.findall(text)),
        "formula_hits": len(FORMULA.findall(text)),
        "bad_chars": text.count("\ufffd"),
    }


def page_count(data: bytes) -> int:
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


def sample_ids(con: store.sqlite3.Connection, n: int, max_pages: int) -> list[int]:
    """The ``n`` indexed PDFs of at most ``max_pages`` pages that mention
    tables most often per chunk. Books and proceedings volumes are excluded
    by the page bound, papers and datasheets are what remains."""
    rows = con.execute(
        """
        SELECT c.doc_id,
               sum(c.text LIKE '%Table %' OR c.text LIKE '%TABLE %') * 1.0 / count(*)
                   AS density,
               sum(c.text LIKE '%Table %' OR c.text LIKE '%TABLE %') AS mentions
        FROM chunks c JOIN documents d ON d.id = c.doc_id
        WHERE d.mime = 'application/pdf'
        GROUP BY c.doc_id HAVING mentions >= 5
        ORDER BY density DESC LIMIT ?
        """,
        (n * 10,),
    ).fetchall()
    picked: list[int] = []
    for r in rows:
        if page_count(store.get_original(con, r["doc_id"])) <= max_pages:
            picked.append(r["doc_id"])
        if len(picked) == n:
            break
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ids", type=int, nargs="+")
    ap.add_argument("--sample", type=int, help="pick N table-heavy indexed PDFs")
    ap.add_argument("--max-pages", type=int, default=30, help="page bound for --sample")
    ap.add_argument(
        "--extractors", nargs="+", default=["pymupdf", "pymupdf4llm", "docling"]
    )
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    con = store.connect()
    ids = list(a.ids or [])
    if a.sample:
        ids += sample_ids(con, a.sample, a.max_pages)
    ids = list(dict.fromkeys(ids))
    if not ids:
        ap.error("nothing selected: --ids or --sample")
    exts = [parsers.by_name(n) for n in a.extractors]
    missing = [e.name for e in exts if not e.available()]
    if missing:
        ap.error(f"not installed: {missing}")
    a.out.mkdir(parents=True, exist_ok=True)

    lines = ["# Extractor comparison", ""]
    lines.append("| doc | title | extractor | s | chars | rows | h | formula | bad |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    totals: dict[str, dict[str, float]] = {e.name: {"s": 0.0, "chars": 0} for e in exts}
    for doc_id in ids:
        doc = store.get_document(con, doc_id, max_chars=0)
        if doc is None:
            continue
        title = (doc["title"] or "")[:50].replace("|", "/")
        data = store.get_original(con, doc_id)
        print(f"doc {doc_id}: {title}", file=sys.stderr)
        for e in exts:
            t0 = time.monotonic()
            try:
                text = e(data)
                err = ""
            except Exception as exc:  # noqa: BLE001 - reported in the table
                text, err = "", f"{type(exc).__name__}"
            dt = time.monotonic() - t0
            (a.out / f"{doc_id}.{e.name}.md").write_text(text, encoding="utf-8")
            m = metrics(text)
            totals[e.name]["s"] += dt
            totals[e.name]["chars"] += m["chars"]
            lines.append(
                f"| {doc_id} | {title} | {e.name}{' ' + err if err else ''} | {dt:.1f}"
                f" | {m['chars']} | {m['table_rows']} | {m['headings']}"
                f" | {m['formula_hits']} | {m['bad_chars']} |"
            )
            print(f"   {e.name:12} {dt:6.1f}s {m}", file=sys.stderr)
    lines += ["", "| extractor | total s | total chars |", "|---|---|---|"]
    for name, t in totals.items():
        lines.append(f"| {name} | {t['s']:.0f} | {int(t['chars'])} |")
    (a.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {a.out / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
