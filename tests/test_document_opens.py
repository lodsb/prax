"""Which documents were opened, and when (migration 66, plan item "Reading
activity as a signal"): one row a document, viewer and way in, at most
once in ``OPEN_EVERY``."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.store.documents import opens


@pytest.fixture(autouse=True)
def _forget_opens() -> Iterator[None]:
    # the memo is the process's; every test's store numbers from 1 again
    opens._opened.clear()
    yield
    opens._opened.clear()


def _rows(con: sqlite3.Connection) -> list[tuple[int, str, str]]:
    return [
        (int(r[0]), str(r[1]), str(r[2]))
        for r in con.execute(
            "SELECT doc_id, viewer, via FROM document_opens ORDER BY id"
        )
    ]


def test_an_opening_is_written_once_a_while(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = int(store.ingest_text(con, "A text to open. " * 20)["doc_id"])
    assert store.note_open(con, doc, "web")
    assert not store.note_open(con, doc, "web")  # the page drawn again
    assert store.note_open(con, doc, "agent")  # another way in counts
    with pytest.raises(ValueError):
        store.note_open(con, doc, "telepathy")
    # a named token's opening is its own
    token = store.VIEWER.set(store.Viewer("laptop"))
    try:
        assert store.note_open(con, doc, "web")
    finally:
        store.VIEWER.reset(token)
    assert _rows(con) == [
        (doc, "admin", "web"),
        (doc, "admin", "agent"),
        (doc, "laptop", "web"),
    ]
    # past its time, the same opening is a new one
    clock = [opens.time.monotonic() + opens.OPEN_EVERY + 1]
    monkeypatch.setattr(opens.time, "monotonic", lambda: clock[0])
    assert store.note_open(con, doc, "web")
    got = store.document_opens(con, [doc, doc + 1])
    assert set(got) == {doc}
    assert (got[doc]["count"], got[doc]["web"], got[doc]["agent"]) == (4, 3, 1)
    assert got[doc]["last"]
    assert store.document_opens(con, [doc], since="9999") == {}


def test_the_door_records_the_page_an_agent_and_a_read(client: TestClient) -> None:
    con = client.app.state.con
    doc = int(store.ingest_text(con, "A text to open. " * 20)["doc_id"])
    assert client.get(f"/get/{doc}", params={"via": "web"}).status_code == 200
    assert client.get(f"/get/{doc}", params={"brief": True}).status_code == 200
    assert client.get(f"/get/{doc}").status_code == 200
    # a later window of the same reading is not another opening
    assert client.get(f"/get/{doc}", params={"offset": 50}).status_code == 200
    assert client.get("/get/999").status_code == 404
    assert [via for _, _, via in _rows(con)] == ["web", "agent", "api"]
