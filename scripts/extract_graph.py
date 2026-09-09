#!/usr/bin/env python3
"""Enrich the graph: extract typed triples from documents with Claude.

    python scripts/extract_graph.py --dry-run          # selection and a cost guess
    python scripts/extract_graph.py --limit 20         # a trial, synchronous
    python scripts/extract_graph.py --budget-usd 5     # stop past the estimate
    python scripts/extract_graph.py --submit-batch --limit 2000   # half price, async
    python scripts/extract_graph.py --collect-batch <batch id>    # apply results

Selection: indexed documents whose ``meta.extraction.ontology_version`` is
not the current one, oldest first. Model and effort come from
PRAX_EXTRACT_MODEL / PRAX_EXTRACT_EFFORT (defaults claude-opus-5, medium).
Every applied document is stamped, so reruns continue where they stopped.
Batch runs write ``<data dir>/batches/<id>.json`` with the document map.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, extraction, ontology, store

# Output tokens per document measured in the Sonnet 5 trial (20-triple cap).
EST_OUTPUT_TOKENS = 2500


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", type=int, nargs="+")
    ap.add_argument("--mime", help="only this MIME prefix")
    ap.add_argument(
        "--min-chars",
        type=int,
        default=500,
        help="skip documents with less text than this (notes, empty scans)",
    )
    ap.add_argument(
        "--budget-usd", type=float, help="stop once the running estimate passes this"
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--submit-batch", action="store_true", help="use the Message Batches API"
    )
    ap.add_argument(
        "--collect-batch", metavar="BATCH_ID", help="apply a finished batch"
    )
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    con = store.connect()
    store.init_db(con)
    version = ontology.current().version
    ext = extraction.current()

    if a.collect_batch:
        return collect(con, ext, a.collect_batch, quiet=a.quiet)

    ids = a.ids or store.select_for_extraction(
        con,
        ontology_version=version,
        limit=a.limit,
        mime_prefix=a.mime,
        min_chars=a.min_chars,
    )
    print(
        f"store: {config.db_path()}; ontology v{version}; extractor {ext.name};"
        f" {len(ids)} documents selected",
        file=sys.stderr,
    )
    if a.dry_run:
        chars = sum(len(extraction.build_input(con, i).as_message()) for i in ids[:200])
        per_doc = chars / max(1, min(len(ids), 200))
        est_in = per_doc / 4 + 300
        price_in, price_out = extraction.price(ext.name)
        per_call = (est_in * price_in + EST_OUTPUT_TOKENS * price_out) / 1e6
        print(
            f"about {per_doc:.0f} chars of input per document;"
            f" rough cost {per_call:.4f} USD per document,"
            f" {per_call * len(ids):.2f} USD for the selection"
            f" ({ext.name}, synchronous; batch is half)"
        )
        return 0
    if not ids:
        print("nothing to extract")
        return 0
    if a.submit_batch:
        return submit(con, ext, ids)

    spent = 0.0
    t0 = time.monotonic()
    totals = extraction.ApplyReport()
    for n, doc_id in enumerate(ids, 1):
        if a.budget_usd is not None and spent >= a.budget_usd:
            print(f"budget reached after {n - 1} documents", file=sys.stderr)
            break
        try:
            doc = extraction.build_input(con, doc_id)
            result = ext.extract(doc)
            rep = extraction.apply(con, doc_id, result, extractor=ext.name)
        except Exception as exc:  # noqa: BLE001 - one document must not stop the run
            print(f"[{n}] doc {doc_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        spent += extraction.cost_usd(ext.name, result.usage)
        for k in ("linked", "existing", "queued", "rejected"):
            setattr(totals, k, getattr(totals, k) + getattr(rep, k))
        if not a.quiet:
            print(
                f"[{n}/{len(ids)}] doc {doc_id}: +{rep.linked} edges,"
                f" {rep.queued} queued, {len(result.triples)} triples;"
                f" ~{spent:.2f} USD so far",
                file=sys.stderr,
                flush=True,
            )
    print(
        f"extracted with {ext.name}: {totals.linked} edges added, {totals.existing}"
        f" existing, {totals.queued} queued for review, {totals.rejected} rejected;"
        f" ~{spent:.2f} USD in {time.monotonic() - t0:.0f} s"
    )
    return 0


def submit(
    con: store.sqlite3.Connection, ext: extraction.Extractor, ids: list[int]
) -> int:
    if not isinstance(ext, extraction.ClaudeExtractor):
        print("batch mode needs the Claude extractor", file=sys.stderr)
        return 2
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    ext._ensure()
    requests = [
        Request(
            custom_id=f"doc-{doc_id}",
            params=MessageCreateParamsNonStreaming(
                **ext.params(extraction.build_input(con, doc_id))
            ),
        )
        for doc_id in ids
    ]
    batch = ext.client.messages.batches.create(requests=requests)
    out_dir = config.data_dir() / "batches"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{batch.id}.json").write_text(
        json.dumps({"batch": batch.id, "model": ext.name, "doc_ids": ids}),
        encoding="utf-8",
    )
    print(
        f"submitted batch {batch.id} with {len(ids)} documents;"
        f" status {batch.processing_status}"
    )
    print(f"collect later with: scripts/extract_graph.py --collect-batch {batch.id}")
    return 0


def collect(
    con: store.sqlite3.Connection,
    ext: extraction.Extractor,
    batch_id: str,
    *,
    quiet: bool,
) -> int:
    if not isinstance(ext, extraction.ClaudeExtractor):
        print("batch mode needs the Claude extractor", file=sys.stderr)
        return 2
    ext._ensure()
    batch = ext.client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print(f"batch {batch_id} is {batch.processing_status}; try again later")
        return 1
    totals = extraction.ApplyReport()
    spent = 0.0
    n = 0
    skipped = 0
    version = ontology.current().version
    # the results stream can outlast the extractor's call timeout; and a
    # re-collection after an interruption must not apply a document twice
    client = ext.client.with_options(timeout=900.0)
    for result in client.messages.batches.results(batch_id):
        doc_id = int(result.custom_id.split("-", 1)[1])
        if result.result.type != "succeeded":
            print(f"doc {doc_id}: {result.result.type}", file=sys.stderr)
            continue
        stamp = store.get_meta(con, doc_id).get("extraction") or {}
        if (
            stamp.get("ontology_version") == version
            and stamp.get("extractor") == ext.name
        ):
            skipped += 1
            continue
        try:
            parsed = extraction.ClaudeExtractor.from_message(result.result.message)
            rep = extraction.apply(con, doc_id, parsed, extractor=ext.name)
        except Exception as exc:  # noqa: BLE001
            print(f"doc {doc_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        n += 1
        spent += extraction.cost_usd(ext.name, parsed.usage) / 2  # batch pricing
        for k in ("linked", "existing", "queued", "rejected"):
            setattr(totals, k, getattr(totals, k) + getattr(rep, k))
        if not quiet and n % 100 == 0:
            print(f"applied {n} documents", file=sys.stderr, flush=True)
    print(
        f"batch {batch_id}: {n} documents applied, {skipped} already applied,"
        f" {totals.linked} edges added, {totals.queued} queued for review;"
        f" ~{spent:.2f} USD"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
