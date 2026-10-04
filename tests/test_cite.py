"""A passage's link that survives a re-chunk (``store.cite_link``; the
first client's O2): the chunk id and words of the passage no other
passage of the document holds, which the UI's ``chunkTarget`` trusts over
the id."""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from prax import store

HEADER = "Proceedings of the Bed Conference 2026, Somewhere"


def _fold(text: str) -> str:
    """The UI's norm: lowercase, letters and digits, single spaces."""
    return " ".join(re.findall(r"[^\W_]+", text.lower()))


def _ui_finds(con: sqlite3.Connection, doc_id: int, find: str) -> int | None:
    """``locateChunk``'s exact path: the first passage, in order, whose
    folded text holds the folded words."""
    probe = _fold(find)[:80]
    for cid, text in con.execute(
        "SELECT id, text FROM chunks WHERE doc_id = ? ORDER BY seq", (doc_id,)
    ):
        if probe in _fold(text):
            return int(cid)
    return None


def _paper(con: sqlite3.Connection) -> int:
    parts = []
    for i, topic in enumerate(["onsets", "tempo", "beats", "meter"]):
        parts.append(f"## {topic.title()}\n\n{HEADER}\n\n")
        parts.append(
            f"The {topic} estimator number {i} uses a spectral flux with a"
            f" median threshold tuned on the {topic} corpus, unlike the others. "
            * 3
            + "\n\n"
        )
    return int(store.ingest_text(con, "# A paper\n\n" + "".join(parts))["doc_id"])


def test_a_passage_gets_words_only_it_holds(con: sqlite3.Connection) -> None:
    doc = _paper(con)
    rows = con.execute(
        "SELECT id, text FROM chunks WHERE doc_id = ? AND kind = 'text' ORDER BY seq",
        (doc,),
    ).fetchall()
    found = 0
    for cid, _text in rows:
        link = store.cite_link(con, doc, int(cid))
        assert link.startswith(f"#doc/{doc}?chunk={cid}")
        if "find=" not in link:
            continue
        found += 1
        words = parse_qs(urlparse(link.replace("#", "/", 1)).query)["find"][0]
        assert _ui_finds(con, doc, words) == int(cid)  # the UI lands on it
    assert found >= 4


def test_a_passage_the_document_repeats_gets_the_id_alone(
    con: sqlite3.Connection,
) -> None:
    """A running header, a boilerplate block: words every copy holds would
    land on the first copy, so the link is the id alone."""
    same = "This block repeats word for word in every section of the manual. " * 3
    doc = store.ingest_text(con, f"# Manual\n\n## One\n\n{same}\n\n## Two\n\n{same}\n")[
        "doc_id"
    ]
    rows = con.execute(
        "SELECT id FROM chunks WHERE doc_id = ? AND text LIKE 'This block%'", (doc,)
    ).fetchall()
    assert len(rows) == 2
    for (cid,) in rows:
        assert store.cite_link(con, doc, int(cid)) == f"#doc/{doc}?chunk={cid}"


def test_the_agents_search_carries_the_link(con: sqlite3.Connection) -> None:
    from prax.api import app

    with TestClient(app) as client:
        _paper(client.app.state.con)
        brief = client.get(
            "/search", params={"q": "tempo estimator", "brief": True, "mode": "fts"}
        ).json()
        hit = next(h for h in brief if h.get("chunk_id"))
        assert hit["cite"].startswith(f"#doc/{hit['doc_id']}?chunk={hit['chunk_id']}")
        plain = client.get(
            "/search", params={"q": "tempo estimator", "mode": "fts"}
        ).json()
        assert all("cite" not in h for h in plain)  # the UI builds its own links
