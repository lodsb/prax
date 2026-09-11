#!/usr/bin/env python3
"""Enrich the graph: extract typed triples from documents with Claude.

    python scripts/extract_graph.py --dry-run          # selection and a cost guess
    python scripts/extract_graph.py --limit 20         # a trial, synchronous
    python scripts/extract_graph.py --budget-usd 5     # stop past the estimate
    python scripts/extract_graph.py --submit-batch --limit 2000   # half price, async
    python scripts/extract_graph.py --collect-batch <batch id>    # apply results
    python scripts/extract_graph.py --promoted [--submit-batch]  # flagged documents
    python scripts/extract_graph.py --domain family               # a re-run per domain
    PRAX_EXTRACT=server-32b python scripts/extract_graph.py --workers 2  # served

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

from prax import config, extraction, ontology, pipeline, store

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
    ap.add_argument(
        "--never-extracted",
        action="store_true",
        help="only documents no model has read yet (an ontology bump re-selects"
        " the others; skip them when the pass is a cheaper model's)",
    )
    ap.add_argument(
        "--domain",
        help="only documents assigned to this ontology module (a re-run per domain)",
    )
    ap.add_argument(
        "--promoted",
        action="store_true",
        help="the flagged documents (store.promote) through the promote step's model",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--submit-batch", action="store_true", help="use the Message Batches API"
    )
    ap.add_argument(
        "--collect-batch", metavar="BATCH_ID", help="apply a finished batch"
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel requests to a served model (its --parallel slots)",
    )
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    con = store.connect()
    store.init_db(con)
    onto = ontology.current()
    version = onto.version
    ext = extraction.current("promote" if a.promoted else "extract")

    if a.collect_batch:
        return collect(con, ext, a.collect_batch, quiet=a.quiet)

    if a.promoted and not a.ids:
        ids = [
            d["doc_id"]
            for d in store.promoted_documents(con, producer=ext.name)
            if not d["done"] and (not a.domain or a.domain in (d["domains"] or []))
        ]
        if a.limit:
            ids = ids[: a.limit]
    else:
        ids = a.ids or store.select_for_extraction(
            con,
            ontology_version=version,
            limit=None if a.never_extracted else a.limit,
            mime_prefix=a.mime,
            min_chars=a.min_chars,
            domain=a.domain,
            onto=onto,
        )
    if a.never_extracted and not a.ids:
        ids = [i for i in ids if not store.get_meta(con, i).get("extraction")]
        if a.limit:
            ids = ids[: a.limit]
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

    run = ("promote-" if a.promoted else "sync-") + time.strftime("%Y%m%dT%H%M%S")
    workers = max(1, a.workers)
    if workers > 1 and not isinstance(ext, extraction.LocalExtractor):
        print(
            "--workers applies to a served model; running one at a time",
            file=sys.stderr,
        )
        workers = 1
    say = None if a.quiet else (lambda t: print(t, file=sys.stderr, flush=True))
    with store.Job(
        con, "promote" if a.promoted else "extract", total=len(ids), note=ext.name
    ) as job:
        rep = pipeline.extract_documents(
            con,
            ids,
            ext,
            workers=workers,
            run=run,
            budget_usd=a.budget_usd,
            log=say,
            job=job,
        )
        job.note(str(rep))
    if rep.stopped:
        print(rep.stopped, file=sys.stderr)
    print(f"extracted with {ext.name}: {rep}")
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
    onto = ontology.current()
    # the results stream can outlast the extractor's call timeout; and a
    # re-collection after an interruption must not apply a document twice
    client = ext.client.with_options(timeout=900.0)
    for result in client.messages.batches.results(batch_id):
        doc_id = int(result.custom_id.split("-", 1)[1])
        if result.result.type != "succeeded":
            print(f"doc {doc_id}: {result.result.type}", file=sys.stderr)
            continue
        meta = store.get_meta(con, doc_id)
        stamp = meta.get("extraction") or {}
        if (
            stamp.get("ontology_version") == store.expected_version(meta, onto)
            and stamp.get("extractor") == ext.name
        ):
            skipped += 1
            continue
        try:
            parsed = extraction.ClaudeExtractor.from_message(result.result.message)
            rep = extraction.apply(
                con, doc_id, parsed, extractor=ext.name, run=batch_id
            )
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
