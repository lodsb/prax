"""The wall (stage U): a named token sees only what it is given. Every
route such a token may call is walked with a personal document and a
personal page in the library, and none of them may show it; every other
route is refused. A route added to the allowlist without a case here
fails ``test_every_allowed_route_is_walked``."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.wall import auth

ADMIN = "admin-secret"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_TOKEN", ADMIN)
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _as(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def _library(con: sqlite3.Connection, client: TestClient) -> dict[str, Any]:
    """An open paper, a bank statement marked personal (it names a
    landlord no other document names, and the wavelet transform the
    paper uses), and a personal page linking the statement."""
    paper = int(
        store.ingest_text(
            con, "The wavelet transform decomposes signals. " * 30, title="Open paper"
        )["doc_id"]
    )
    bank = int(
        store.ingest_text(
            con,
            "Kontoauszug zebrafinch quarterly statement of the account. " * 30,
            title="Bank statement",
        )["doc_id"]
    )
    for edge, doc in (
        (
            store.Edge("Open paper", "paper", "uses", "wavelet transform", "method"),
            paper,
        ),
        (
            store.Edge(
                "Bank statement", "document", "mentions", "Landlord Smith", "person"
            ),
            bank,
        ),
        (
            store.Edge(
                "Bank statement", "document", "mentions", "wavelet transform", "method"
            ),
            bank,
        ),
    ):
        store.link(
            con, edge, confidence="EXTRACTED", source_doc=doc, producer="t", run="t"
        )
    page = store.write_page(
        con, "private-notes", f"Notes on [Bank statement](#doc/{bank}).", kind="topic"
    )
    admin = _as(ADMIN)
    for doc in (bank, int(page["doc_id"])):
        got = client.put(
            f"/doc/{doc}/sensitivity", json={"state": "personal"}, headers=admin
        )
        assert got.status_code == 200
    secret = client.post("/tokens", json={"name": "mcp"}, headers=admin).json()[
        "secret"
    ]
    chunk = int(
        con.execute(
            "SELECT id FROM chunks WHERE doc_id = ? LIMIT 1", (bank,)
        ).fetchone()[0]
    )
    return {
        "paper": paper,
        "bank": bank,
        "page": int(page["doc_id"]),
        "chunk": chunk,
        "secret": secret,
    }


def _says_text(value: Any, *words: str) -> bool:
    text = json.dumps(value)
    return any(w in text for w in words)


def _says(response: Any, *words: str) -> bool:
    text = json.dumps(response.json()) if response.content else ""
    return any(w in text for w in words)


# every route a named token may call, as a request the test makes; the
# coverage test below keeps this list and auth.RESTRICTED_ROUTES together
WALKED = (
    ("GET", "/search"),
    ("GET", "/get/1"),
    ("GET", "/chunk/1"),
    ("GET", "/traverse"),
    ("GET", "/edge/1/why"),
    ("GET", "/graph/changes"),
    ("GET", "/graph/connect"),
    ("GET", "/documents"),
    ("GET", "/doc/1/context"),
    ("GET", "/page/x"),
    ("PUT", "/page/x"),
    ("POST", "/page/x/append"),
    ("PUT", "/page/x/section"),
    ("POST", "/ask"),
    ("POST", "/link"),
    ("PUT", "/doc/1/domains"),
    ("POST", "/doc/1/promote"),
    ("POST", "/doc/1/reading"),
    ("POST", "/ingest"),
    ("POST", "/ingest/url"),
    ("POST", "/ingest/urls"),
    ("GET", "/doc/1/references"),
    ("GET", "/doc/1/figure/ab12"),
    ("GET", "/doc/1/page/1"),
    ("POST", "/references/missing"),
    ("PUT", "/doc/1/title"),
    ("POST", "/ingest/file"),
    ("POST", "/maths"),
    ("GET", "/work/status"),
    ("POST", "/projects/sync"),
)


def test_every_allowed_route_is_walked() -> None:
    for method, pattern in auth.RESTRICTED_ROUTES:
        assert any(m == method and pattern.fullmatch(p) for m, p in WALKED), (
            f"{method} {pattern.pattern} may be called with a named token but the"
            " wall test does not walk it"
        )


def test_a_named_token_does_not_see_what_is_personal(client: TestClient) -> None:
    con = client.app.state.con
    lib = _library(con, client)
    me = _as(lib["secret"])
    bank, page = lib["bank"], lib["page"]

    # search: the statement's own word finds nothing, the paper's finds it
    hits = client.get("/search", params={"q": "zebrafinch", "mode": "fts"}, headers=me)
    assert hits.status_code == 200 and not _says(hits, "Bank statement")
    hits = client.get("/search", params={"q": "wavelet", "mode": "fts"}, headers=me)
    assert _says(hits, "Open paper")
    # a document, a chunk, a context, a page: as if not there
    assert client.get(f"/get/{bank}", headers=me).status_code == 404
    assert client.get(f"/get/{lib['paper']}", headers=me).status_code == 200
    assert client.get(f"/chunk/{lib['chunk']}", headers=me).status_code == 404
    assert client.get(f"/doc/{bank}/context", headers=me).status_code == 404
    # its pages and figures: as if not there; an open one is seen (a text
    # has no pages, which is a 400, not a 404)
    assert client.get(f"/doc/{bank}/page/1", headers=me).status_code == 404
    assert client.get(f"/doc/{bank}/figure/ab12", headers=me).status_code == 404
    assert client.get(f"/doc/{lib['paper']}/page/1", headers=me).status_code == 400
    context = client.get(f"/doc/{lib['paper']}/context", headers=me)
    assert context.status_code == 200 and not _says(context, "Bank statement")
    assert client.get("/page/private-notes", headers=me).status_code == 404
    # the list: neither the statement nor the page, nor counted
    listed = client.get("/documents", params={"limit": 50}, headers=me).json()
    ids = {d["id"] for d in listed["items"]}
    assert bank not in ids and page not in ids and listed["total"] == len(ids)
    # the graph: what only the statement says is not there, and a shared
    # entity carries only the paper's edge
    walk = client.get(
        "/traverse", params={"entity": "Landlord Smith"}, headers=me
    ).json()
    assert walk["edges"] == [] and "senses" not in walk
    # a path: none crosses the statement's facts for this token
    path = client.get(
        "/graph/connect",
        params={"a": "Open paper", "b": "Landlord Smith", "weak": "true"},
        headers=me,
    ).json()
    assert not path.get("paths")
    full = client.get(
        "/graph/connect",
        params={"a": "Open paper", "b": "Landlord Smith", "weak": "true"},
        headers=_as(ADMIN),
    ).json()
    assert [h["rel"] for h in full["paths"][0]["hops"]] == [
        "uses",
        "mentions",
        "mentions",
    ]
    # what changed: the statement's facts are neither listed nor counted
    changed = client.get("/graph/changes", params={"since": "2000"}, headers=me).json()
    assert not _says_text(changed, "Landlord Smith")
    assert changed["added"]["count"] == len(changed["added"]["facts"])
    walk = client.get(
        "/traverse", params={"entity": "wavelet transform"}, headers=me
    ).json()
    assert {e["source_doc"] for e in walk["edges"]} == {lib["paper"]}
    # ask: the bundle is built from what the token sees
    got = client.post(
        "/ask", json={"question": "zebrafinch statement", "backend": "none"}, headers=me
    )
    assert got.status_code == 200 and not _says(got, "Bank statement", "zebrafinch q")
    # writes to what it cannot see: as if absent
    assert (
        client.put(
            "/page/private-notes", json={"text": "over it"}, headers=me
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/page/private-notes/append", json={"section": "more"}, headers=me
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/doc/{bank}/domains", json={"domains": ["research"]}, headers=me
        ).status_code
        == 404
    )
    assert client.post(f"/doc/{bank}/promote", json={}, headers=me).status_code == 404
    link = {
        "src": "A",
        "src_type": "concept",
        "rel": "mentions",
        "dst": "B",
        "dst_type": "concept",
        "source_doc": bank,
    }
    assert client.post("/link", json=link, headers=me).status_code == 400
    # a hidden document as an end (doc:N) is as if absent
    to_hidden = {
        "src": "A",
        "src_type": "concept",
        "rel": "mentions",
        "dst": f"doc:{bank}",
    }
    assert client.post("/link", json=to_hidden, headers=me).status_code == 404
    # what it may do: add to the library
    assert (
        client.post(
            "/ingest", json={"text": "A new note. " * 20, "title": "Note"}, headers=me
        ).status_code
        == 200
    )
    # every other route is the administrator's
    for method, path in (
        ("GET", "/graph/overview"),
        ("GET", "/tokens"),
        ("PUT", f"/doc/{bank}/sensitivity"),
        ("GET", f"/doc/{bank}/text"),
    ):
        assert client.request(method, path, headers=me).status_code == 403, path
    # a secret nobody made opens nothing
    assert (
        client.get("/search", params={"q": "x"}, headers=_as("prax_nobody")).status_code
        == 401
    )
    # the administrator sees everything
    admin = _as(ADMIN)
    assert client.get(f"/get/{bank}", headers=admin).status_code == 200
    hits = client.get(
        "/search", params={"q": "zebrafinch", "mode": "fts"}, headers=admin
    )
    assert _says(hits, "Bank statement")


def test_a_token_of_one_module_sees_only_that_module(client: TestClient) -> None:
    con = client.app.state.con
    lib = _library(con, client)
    admin = _as(ADMIN)
    recipe = int(store.ingest_text(con, "Apple cake. " * 30, title="Cake")["doc_id"])
    store.set_domains(con, recipe, ["kitchen"])
    store.set_domains(con, lib["paper"], ["research"])
    secret = client.post(
        "/tokens", json={"name": "tablet", "domains": ["craft"]}, headers=admin
    ).json()["secret"]
    me = _as(secret)
    # craft holds the kitchen built on it; research and unassigned are out
    assert client.get(f"/get/{recipe}", headers=me).status_code == 200
    assert client.get(f"/get/{lib['paper']}", headers=me).status_code == 404
    assert (
        client.post(
            "/tokens", json={"name": "x", "domains": ["nope"]}, headers=admin
        ).status_code
        == 400
    )
    listed = client.get("/tokens", headers=admin).json()["tokens"]
    assert {t["name"]: t["domains"] for t in listed} == {
        "mcp": None,
        "tablet": ["craft"],
    }
    assert client.delete("/tokens/tablet", headers=admin).status_code == 200
    assert client.get(f"/get/{recipe}", headers=me).status_code == 401


def test_a_named_token_asks_the_calculator_only_about_what_it_sees(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /maths`` with ``chunk:<id>``: a hidden document's chunk is as
    absent as one that does not exist; an open one that is not a formula
    is said to be none."""
    monkeypatch.setenv("PRAX_PACKS", "maths")
    con = client.app.state.con
    lib = _library(con, client)
    me = _as(lib["secret"])
    hidden = store.list_chunks(con, lib["bank"])[0]["chunk_id"]
    got = client.post("/maths", json={"op": "read", "a": f"chunk:{hidden}"}, headers=me)
    assert got.status_code == 404 and not _says(got, "zebrafinch")
    assert (
        client.post(
            "/maths", json={"op": "read", "a": "chunk:999999"}, headers=me
        ).status_code
        == 404
    )
    shown = store.list_chunks(con, lib["paper"])[0]["chunk_id"]
    got = client.post("/maths", json={"op": "read", "a": f"chunk:{shown}"}, headers=me)
    assert got.status_code == 400 and "not a display formula" in got.text


def test_a_named_token_asks_the_status_only_of_what_it_sees(
    client: TestClient,
) -> None:
    """``GET /work/status``: a hidden document is as absent as one that
    does not exist, and its text length is not said."""
    con = client.app.state.con
    lib = _library(con, client)
    me = _as(lib["secret"])
    got = client.get(
        "/work/status", params={"ids": f"{lib['bank']},{lib['paper']}"}, headers=me
    )
    assert got.status_code == 200
    rows = {r["doc_id"]: r for r in got.json()["documents"]}
    assert rows[lib["bank"]] == {"doc_id": lib["bank"], "state": "unknown"}
    assert rows[lib["paper"]]["state"] == "indexed"
    assert "alive" in got.json()["worker"]
