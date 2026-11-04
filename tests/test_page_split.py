"""A long page split by the door into an index page and a child page a
section (``store.split_page``, R3a): not a word changed or lost, the old
citations still landing, each child where its page was and as closed."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.store import pages

LOG = (
    "# Log\n\nWhat this log is for.\n\n"
    "## Monday\n\nThe wavelet transform was measured on the bench today.\n\n"
    "## Tuesday\n\nThe spectrogram of the second recording looked noisier.\n"
)


def _chunks(con: sqlite3.Connection, doc_id: int) -> dict[str, int]:
    return {
        str(r[1]): int(r[0])
        for r in con.execute("SELECT id, text FROM chunks WHERE doc_id = ?", (doc_id,))
    }


def _text(con: sqlite3.Connection, doc_id: int) -> str:
    h = con.execute("SELECT text_hash FROM documents WHERE id = ?", (doc_id,))
    return store.base._read_archive(h.fetchone()[0]).decode("utf-8")


def _live(con: sqlite3.Connection, source: int, rel: str) -> list[str]:
    return sorted(
        str(r[0])
        for r in con.execute(
            "SELECT t.name FROM edges e JOIN entities t ON t.id = e.dst"
            " WHERE e.source_doc = ? AND e.rel = ? AND e.valid_to IS NULL",
            (source, rel),
        )
    )


def test_a_split_adds_up_and_keeps_whose_the_words_are(
    con: sqlite3.Connection,
) -> None:
    store.write_page(con, "project-bench", "# Bench\n\nThe bench.", kind="project")
    log = store.write_page(con, "log", LOG, part_of="project-bench", author="human")
    doc_id = int(log["doc_id"])

    plan = store.split_page(con, "log", dry_run=True)
    assert plan["level"] == 2 and [c["slug"] for c in plan["children"]] == [
        "log-monday",
        "log-tuesday",
    ]
    assert _text(con, doc_id) == LOG  # a dry run writes nothing

    got = store.split_page(con, "log", author="agent")
    kids = [int(c["doc_id"]) for c in got["children"]]
    head = "# Log\n\nWhat this log is for.\n\n"
    assert head + "".join(_text(con, k) for k in kids) == LOG
    index = _text(con, doc_id)
    assert index.startswith(head) and f"](#doc/{kids[0]})" in index

    page = store.get_page(con, "log")
    assert [c["slug"] for c in page["children"]] == ["log-monday", "log-tuesday"]
    assert page["meta"]["page"]["split"][0]["children"] == kids
    last = page["revisions"][-1]
    # the words are still the person's, whoever split them
    assert last["author"] == "human" and "split by agent" in last["note"]
    with pytest.raises(PermissionError):
        store.write_page(con, "log-monday", "Over it.", author="agent")
    monday = store.get_page(con, "log-monday")
    assert monday["title"] == "Log: Monday"
    assert [p["slug"] for p in monday["projects"]] == ["project-bench"]
    assert [a["slug"] for a in monday["ancestors"]][-1] == "log"


def test_the_chunks_move_with_their_words_and_old_citations_land(
    con: sqlite3.Connection,
) -> None:
    doc_id = int(store.write_page(con, "log", LOG)["doc_id"])
    before = _chunks(con, doc_id)
    monday_chunk = next(i for t, i in before.items() if "wavelet" in t)

    got = store.split_page(con, "log")
    monday, tuesday = (int(c["doc_id"]) for c in got["children"])
    # the chunk is the same row, now the child's: its id and vector kept
    assert monday_chunk in _chunks(con, monday).values()
    assert store.cited_place(con, doc_id, chunk=monday_chunk) == {
        "doc_id": monday,
        "chunk_id": monday_chunk,
    }
    # a citation whose chunk is gone is found by its words
    found = store.cited_place(con, doc_id, chunk=999_999, find="second recording")
    assert found is not None and found["doc_id"] == tuesday
    assert store.cited_place(con, doc_id, find="no such words anywhere") is None
    # only the page's own children are followed
    other = int(store.write_page(con, "other", "# Other\n\nElse.")["doc_id"])
    assert store.cited_place(con, other, chunk=monday_chunk) is None


def test_a_split_child_is_split_again_and_still_found(
    con: sqlite3.Connection,
) -> None:
    text = LOG.replace(
        "## Tuesday\n",
        "## Tuesday\n\n### Morning\n\nCoffee and a capacitor.\n\n### Evening\n\n",
    )
    doc_id = int(store.write_page(con, "log", text)["doc_id"])
    store.split_page(con, "log", level=2)
    store.split_page(con, "log-tuesday", level=3)
    found = store.cited_place(con, doc_id, find="coffee and a capacitor")
    assert found is not None
    assert store.get_page(con, "log-tuesday-morning")["doc_id"] == found["doc_id"]


def test_the_links_go_with_the_words(con: sqlite3.Connection) -> None:
    paper = int(
        store.ingest_text(con, "A paper on wavelets. " * 10, title="Wavelets")["doc_id"]
    )
    aside = int(
        store.ingest_text(con, "A paper on filters. " * 10, title="Filters")["doc_id"]
    )
    text = LOG.replace("today.", f"today ([the paper](#doc/{paper})).")
    doc_id = int(store.write_page(con, "log", text, annotates=[paper, aside])["doc_id"])
    assert _live(con, doc_id, "annotates") == ["Filters", "Wavelets"]

    got = store.split_page(con, "log")
    monday = int(got["children"][0]["doc_id"])
    assert _live(con, monday, "annotates") == ["Wavelets"]
    # the given edge a child links ends on the index; the other stays
    assert "Wavelets" not in _live(con, doc_id, "annotates")
    assert "Filters" in _live(con, doc_id, "annotates")


def test_refused_splits_write_nothing(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.write_page(con, "short", "# Short\n\n## Only\n\nOne section.")
    with pytest.raises(ValueError, match="two"):
        store.split_page(con, "short")
    store.write_page(con, "project-x", "# X\n\n## A\n\na\n\n## B\n\nb", kind="project")
    with pytest.raises(ValueError, match="project"):
        store.split_page(con, "project-x")
    asked = (
        '# Q\n\n## A\n\n<!-- prax:ask id=q1 "what?" -->\n\n## B\n\nan answer\n'
        "<!-- /prax:ask id=q1 sha=abc -->\n"
    )
    store.write_page(con, "asks", asked)
    with pytest.raises(ValueError, match="ask block"):
        store.split_page(con, "asks")
    log = store.write_page(con, "log", LOG)
    with pytest.raises(PermissionError):
        store.split_page(con, "log", expected_text_hash="0" * 64)
    with pytest.raises(KeyError):
        store.split_page(con, "nope")

    # a failure after the children are written takes them back with it
    def lose(*_: object) -> None:
        raise ValueError("the split pages do not add up")

    monkeypatch.setattr(pages.splits, "_refuse_lost_words", lose)
    pages_before = con.execute("SELECT count(*) FROM pages").fetchone()[0]
    with pytest.raises(ValueError):
        store.split_page(con, "log")
    assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == pages_before
    assert _text(con, int(log["doc_id"])) == LOG


def test_a_personal_page_splits_into_personal_pages(con: sqlite3.Connection) -> None:
    doc_id = int(store.write_page(con, "log", LOG, sensitivity="personal")["doc_id"])
    store.set_domains(con, doc_id, ["research"])
    got = store.split_page(con, "log")
    for child in got["children"]:
        row = con.execute(
            "SELECT sensitivity, json_extract(meta, '$.domains[0]') FROM documents"
            " WHERE id = ?",
            (child["doc_id"],),
        ).fetchone()
        assert tuple(row) == ("personal", "research")
    token = store.VIEWER.set(store.Viewer("guest", role="contributor"))
    try:
        assert store.cited_place(con, doc_id, find="second recording") is None
        with pytest.raises(KeyError):
            store.split_page(con, "log-monday")
    finally:
        store.VIEWER.reset(token)


def test_the_door_and_the_tool_shapes(client: TestClient) -> None:
    con = client.app.state.con
    doc_id = int(store.write_page(con, "log", LOG)["doc_id"])
    plan = client.post("/page/log/split", json={"dry_run": True}).json()
    assert plan["dry_run"] and len(plan["children"]) == 2
    got = client.post("/page/log/split", json={"level": 2}).json()
    tuesday = int(got["children"][1]["doc_id"])
    assert client.post("/page/nope/split", json={}).status_code == 404
    assert client.post("/page/log-monday/split", json={}).status_code == 409
    landed = client.get(
        f"/doc/{doc_id}/cited", params={"find": "second recording"}
    ).json()
    assert landed["doc_id"] == tuesday
    assert (
        client.get(f"/doc/{doc_id}/cited", params={"find": "nothing"}).status_code
        == 404
    )
