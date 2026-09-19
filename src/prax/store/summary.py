"""What the store holds, counted in one place.

``prax status`` and the README's state table are this function; everything
it counts belongs to another module, which is why it sits on top of them.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from prax import config, ontology

from .base import _reading
from .documents import DOCTYPES
from .jobs import running_jobs
from .retrieval import vec_status


@_reading
def stats(con: sqlite3.Connection) -> dict[str, Any]:
    """What the store holds, in one place: documents by type and source,
    chunks and vectors, the graph by producer, entities by type, the review
    queue, pages, acronyms, the ontology and the domains documents are read
    against. What `prax status` prints and the README's state table is made
    of. Counting queries only; the JSON paths grouped by are indexed
    (migration 0010)."""
    row = con.execute(
        "SELECT count(*) AS total,"
        " sum(text_hash IS NOT NULL) AS with_text,"
        " sum(json_extract(meta, '$.retired') IS NOT NULL) AS retired"
        " FROM documents"
    ).fetchone()
    docs: dict[str, Any] = {
        "total": row["total"] or 0,
        "with_text": row["with_text"] or 0,
        "retired": row["retired"] or 0,
    }
    kinds = ", ".join(
        f"sum(CASE WHEN {expr} THEN 1 ELSE 0 END) AS {name}"
        for name, expr in DOCTYPES.items()
    )
    krow = con.execute(
        f"SELECT {kinds} FROM documents d"
        " WHERE json_extract(d.meta, '$.retired') IS NULL"
    ).fetchone()
    docs["by_type"] = {k: krow[k] or 0 for k in DOCTYPES if krow[k]}
    docs["by_source"] = {
        (r[0] or "—"): r[1]
        for r in con.execute(
            "SELECT json_extract(meta, '$.source'), count(*) FROM documents"
            " WHERE json_extract(meta, '$.retired') IS NULL GROUP BY 1"
            " ORDER BY 2 DESC"
        )
    }
    domains: dict[str, int] = {}
    unset = 0
    for raw, n in con.execute(
        "SELECT json_extract(meta, '$.domains'), count(*) FROM documents"
        " WHERE json_extract(meta, '$.retired') IS NULL GROUP BY 1"
    ):
        names = json.loads(raw) if raw else None
        if not names:
            unset += n
            continue
        for name in names:
            domains[name] = domains.get(name, 0) + n
    onto = ontology.current()

    chunks = {
        r[0]: r[1] for r in con.execute("SELECT kind, count(*) FROM chunks GROUP BY 1")
    }
    edges = {
        r[0] or "—": r[1]
        for r in con.execute(
            "SELECT producer, count(*) FROM edges WHERE valid_to IS NULL"
            " GROUP BY 1 ORDER BY 2 DESC"
        )
    }
    erow = con.execute(
        "SELECT count(*) AS total, sum(canonical_id IS NOT NULL) AS merged"
        " FROM entities"
    ).fetchone()
    types = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT type, count(*) FROM entities WHERE canonical_id IS NULL"
            " GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
        )
    }
    one = lambda sql: int(con.execute(sql).fetchone()[0])
    db = config.db_path()
    return {
        "documents": docs,
        "domains": {"documents": domains, "unset": unset},
        "chunks": {"total": sum(chunks.values()), "by_kind": chunks},
        "vectors": vec_status(con),
        "graph": {
            "edges": sum(edges.values()),
            "by_producer": edges,
            "entities": erow["total"] or 0,
            "merged": erow["merged"] or 0,
            "by_type": types,
        },
        "review": {
            "open": one("SELECT count(*) FROM review_queue WHERE resolution IS NULL")
        },
        "pages": one("SELECT count(*) FROM pages"),
        "acronyms": one("SELECT count(*) FROM acronyms"),
        "jobs": {"running": running_jobs(con)},
        "ontology": {
            "version": onto.version,
            "modules": sorted(m for m in onto.modules if m != ontology.CORE),
        },
        "store": {
            "path": str(db),
            "db_bytes": db.stat().st_size if db.exists() else 0,
        },
    }
