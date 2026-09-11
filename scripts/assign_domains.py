#!/usr/bin/env python3
"""Give documents their domain set from the rules in prax.yaml.

    python scripts/assign_domains.py --dry-run     # counts per rule
    python scripts/assign_domains.py --commit      # documents without a set
    python scripts/assign_domains.py --commit --force   # every document not set by hand

Rules (``domains:`` in prax.yaml, first match wins; a rule without ``match``
is the default)::

    domains:
      - match: {collection: Family}
        domains: [family]
      - match: {source: zotero}
        domains: [research]
      - domains: [research]

``match`` keys: ``source`` (meta.source), ``mime`` (prefix), ``path``
(original path prefix), ``collection`` and ``tag`` (Zotero, case-insensitive).
A set written by hand (the document page, the API, the MCP tool) is never
touched. A document without a set is read against every module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, models, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--commit", action="store_true")
    ap.add_argument(
        "--force", action="store_true", help="re-assign rule-set documents too"
    )
    a = ap.parse_args()
    rules = list(models.load().get("domains") or [])
    if not rules:
        print(f"no 'domains' rules in {models.config_path()}", file=sys.stderr)
        return 2
    con = store.connect()
    store.init_db(con)
    counts = store.assign_domains(con, rules, force=a.force, commit=a.commit)
    mode = "would assign" if a.dry_run else "assigned"
    print(f"store: {config.db_path()}; {len(rules)} rules")
    for i, rule in enumerate(rules):
        n = counts.get(f"rule {i}", 0)
        where = rule.get("match") or "everything else"
        print(f"  {n:6d}  {mode} {rule.get('domains')} for {where}")
    print(f"  {counts['unmatched']:6d}  unmatched (every module)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
