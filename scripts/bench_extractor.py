#!/usr/bin/env python3
"""Compare an extractor against the edges already in the store, per document.

    python scripts/bench_extractor.py --ids 12 34 56                 # the extract step
    PRAX_EXTRACT=server-32b python scripts/bench_extractor.py --ids 12 34 56
    python scripts/bench_extractor.py --ids 12 34 56 --out docs/eval/x.md

Runs the current extractor (``prax.yaml`` extract step, ``PRAX_EXTRACT``
for one run) over the documents and applies nothing. For each document it
reports seconds, tokens, triples, how many would pass the ontology, and the
overlap with the live edges another producer wrote for the same document
(same relation and same target name, case-insensitive): a cheap proxy for
"does this model find what the reference model found". The triples are
printed so the quality can be read, not only counted.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import extraction, ontology, store


def reference(con: store.sqlite3.Connection, doc_id: int) -> set[tuple[str, str]]:
    return {
        (r["rel"], r["name"].lower())
        for r in con.execute(
            "SELECT x.rel, t.name FROM edges x JOIN entities t ON t.id = x.dst"
            " WHERE x.source_doc = ? AND x.valid_to IS NULL AND x.rel != 'cites'",
            (doc_id,),
        )
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ids", type=int, nargs="+", required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    con = store.connect()
    store.init_db(con)
    ext = extraction.current()
    onto = ontology.current()
    lines = [f"# Extractor bench: {ext.name}", ""]
    lines += [
        "| doc | seconds | in | out | triples | valid | ref edges | overlap |",
        "|---|---|---|---|---|---|---|---|",
    ]
    detail: list[str] = []
    for doc_id in a.ids:
        doc = extraction.build_input(con, doc_id)
        t0 = time.monotonic()
        result = ext.extract(doc)
        dt = time.monotonic() - t0
        valid = 0
        for t in result.triples:
            try:
                onto.check_edge(t.src_type, t.rel, t.dst_type)
                valid += 1
            except ValueError:
                pass
        ref = reference(con, doc_id)
        got = {(t.rel, t.dst.lower()) for t in result.triples}
        overlap = len(ref & got)
        u = result.usage
        lines.append(
            f"| {doc_id} | {dt:.0f} | {u.get('input_tokens', 0)}"
            f" | {u.get('output_tokens', 0)} | {len(result.triples)} | {valid}"
            f" | {len(ref)} | {overlap} |"
        )
        detail += [
            "",
            f"## {doc_id}: {doc.title}",
            "",
            f"Summary: {result.summary}",
            "",
        ]
        detail += [
            f"- {t.src} ({t.src_type}) --{t.rel}--> {t.dst} ({t.dst_type})"
            f" [{t.confidence}]"
            for t in result.triples
        ]
        if result.unmapped:
            detail += ["", "unmapped:"] + [
                f"- {m.get('src')} --{m.get('rel')}--> {m.get('dst')}:"
                f" {m.get('reason')}"
                for m in result.unmapped
            ]
        print(lines[-1], file=sys.stderr, flush=True)
    text = "\n".join(lines + detail) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
