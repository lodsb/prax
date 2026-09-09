#!/usr/bin/env python3
"""Citation network from OpenAlex or Crossref: ``cites`` edges and citation counts.

    python scripts/import_citations.py --dry-run                   # selection
    python scripts/import_citations.py --commit --limit 50         # trial
    python scripts/import_citations.py --commit --source crossref  # other source
    python scripts/import_citations.py --commit --resolve-titles   # DOI-less too
    python scripts/import_citations.py --commit --refresh          # re-fetch all

Documents with a DOI are looked up first; each referenced work with a title
becomes a ``paper --cites--> paper`` edge and the document gets
``meta.citations`` with its citation count. Re-runs skip fetched documents.
``PRAX_CITATIONS_MAILTO`` (or ``--mailto``) puts requests in the sources'
polite pools. Details: ``prax.importers.citations``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, store
from prax.importers import citations


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", action="store_true")
    ap.add_argument("--source", choices=("openalex", "crossref"), default="openalex")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", type=int, nargs="+")
    ap.add_argument(
        "--resolve-titles",
        action="store_true",
        help="also look up documents without a DOI by exact title",
    )
    ap.add_argument("--refresh", action="store_true", help="re-fetch fetched documents")
    ap.add_argument("--mailto", help="polite-pool contact (PRAX_CITATIONS_MAILTO)")
    ap.add_argument("--timeout", type=float, default=30, help="seconds per request")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    con = store.connect()
    store.init_db(con)
    ids = a.ids or citations.candidates(
        con, limit=a.limit, refresh=a.refresh, doi_only=not a.resolve_titles
    )
    print(
        f"store: {config.db_path()}; source {a.source}; {len(ids)} documents selected",
        file=sys.stderr,
    )
    if a.dry_run:
        return 0
    fetch = citations.HttpFetcher(a.mailto, timeout=a.timeout)
    source = citations.source_named(a.source, fetch)
    rep = citations.Report()
    t0 = time.monotonic()
    step = 25
    for start in range(0, len(ids), step):
        citations.import_citations(
            con,
            ids[start : start + step],
            source=source,
            resolve_titles=a.resolve_titles,
            report=rep,
        )
        if not a.quiet:
            print(
                f"  {min(start + step, len(ids))}/{len(ids)}: {rep.documents} resolved,"
                f" {rep.unresolved} unresolved, {rep.linked} edges,"
                f" {len(rep.errors)} errors, {fetch.calls} requests",
                file=sys.stderr,
                flush=True,
            )
    for e in rep.errors[:20]:
        print("  " + e, file=sys.stderr)
    print(
        f"{a.source}: {rep.documents} documents resolved, {rep.unresolved} unresolved,"
        f" {rep.skipped} skipped; {rep.linked} cites edges added, {rep.existing}"
        f" existing, {rep.untitled} references without a title,"
        f" {rep.library_refs} references to library documents;"
        f" {len(rep.errors)} errors; {fetch.calls} requests in"
        f" {time.monotonic() - t0:.0f} s"
    )
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
