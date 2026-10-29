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
    # a contributor, so the walk reaches the writes too (a reader's are
    # test_a_reader_reads_and_changes_nothing)
    secret = client.post(
        "/tokens", json={"name": "mcp", "role": "contributor"}, headers=admin
    ).json()["secret"]
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
    # the web page's reads (test_the_web_page_with_a_named_token)
    ("GET", "/changes"),
    ("GET", "/ontology"),
    ("GET", "/genres"),
    ("GET", "/ask/config"),
    ("GET", "/domains/counts"),
    ("GET", "/doc/1/original"),
    ("GET", "/doc/1/text"),
    ("GET", "/doc/1/chunks"),
    ("GET", "/doc/1/thumb"),
    ("GET", "/doc/1/preview"),
    ("GET", "/pages"),
    ("GET", "/entities"),
    ("GET", "/graph/overview"),
    ("GET", "/graph/documents"),
    ("GET", "/graph/document/1"),
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
        ("GET", "/communities"),
        ("GET", "/people"),
        ("GET", "/pictures"),
        ("GET", "/faces/groups"),
        ("GET", "/tokens"),
        ("PUT", f"/doc/{bank}/sensitivity"),
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


def test_the_web_page_with_a_named_token(client: TestClient) -> None:
    """Signed in on the web page with a named token: its session cookie
    carries it, the page learns who it is, and the reads the page makes
    show nothing of the personal statement or page."""
    con = client.app.state.con
    lib = _library(con, client)
    bank, page, paper = lib["bank"], lib["page"], lib["paper"]
    # the statement has copies, so a 404 for them can only be the guard's
    copies = {"thumb": b"thumb of it", "preview": b"preview of it"}
    for doc in (bank, paper):
        store.apply_photo(con, doc, facts={}, copies=copies, version=1, by="t")
    assert client.post("/session", json={"token": "prax_nobody"}).status_code == 401
    signed = client.post("/session", json={"token": lib["secret"]})
    assert signed.status_code == 200
    assert signed.json()["admin"] is False and signed.json()["name"] == "mcp"
    # from here on the cookie alone, as a browser sends it
    assert client.get("/session").json()["name"] == "mcp"
    for path in (
        f"/doc/{bank}/text",
        f"/doc/{bank}/chunks",
        f"/doc/{bank}/original",
        f"/doc/{bank}/thumb",
        f"/doc/{bank}/preview",
    ):
        assert client.get(path).status_code == 404, path
    assert client.get(f"/doc/{paper}/text").status_code == 200
    assert client.get(f"/doc/{paper}/chunks").json()
    assert client.get(f"/doc/{paper}/thumb").content == b"thumb of it"
    assert client.get(f"/graph/document/{bank}").json()["edges"] == []
    assert client.get(f"/graph/document/{paper}").json()["edges"]
    pages = client.get("/pages").json()
    assert page not in {p["doc_id"] for p in pages}
    found = client.get("/entities", params={"q": "Landlord"}).json()
    assert not _says_text(found, "Landlord Smith")
    titles = client.get("/graph/documents", params={"ids": f"{bank},{paper}"})
    assert not _says(titles, "Bank statement") and _says(titles, "Open paper")
    overview = client.get("/graph/overview", params={"min_shared": 1})
    assert overview.status_code == 200
    # a hub counts only what the token sees: the paper, not the statement
    assert [(n["name"], n["docs"]) for n in overview.json()["nodes"]] == [
        ("wavelet transform", 1)
    ]
    assert not _says(overview, "Landlord Smith", "Bank statement")
    changes = client.get("/changes", params={"doc": bank}).json()
    assert changes["doc"] is None and "stamp" in changes
    for path in ("/ontology", "/genres", "/ask/config"):
        assert client.get(path).status_code == 200, path
    # the domain menu counts what the token sees: the paper, not the
    # statement or the page (all three in no module)
    unassigned = client.get("/domains/counts").json()["unassigned"]
    # the administrator's views stay refused
    assert client.get("/people").status_code == 403
    assert client.get("/communities").status_code == 403
    # the administrator, signed in the same way, is the administrator
    assert client.post("/session", json={"token": ADMIN}).json()["admin"] is True
    assert client.get("/session").json() == {"admin": True}
    assert client.get(f"/doc/{bank}/text").status_code == 200
    assert client.get(f"/doc/{bank}/preview").content == b"preview of it"
    assert client.get("/domains/counts").json()["unassigned"] == unassigned + 2
    overview = client.get("/graph/overview", params={"min_shared": 1})
    assert overview.json()["nodes"][0]["docs"] == 2  # the statement's too


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


def test_a_reader_reads_and_changes_nothing(client: TestClient) -> None:
    """A reader's token (the default, migration 58) may call every reader's
    route and none of the contributor's: each write is refused before its
    handler, and the library is as it was."""
    con = client.app.state.con
    lib = _library(con, client)
    admin = _as(ADMIN)
    made = client.post("/tokens", json={"name": "friend"}, headers=admin).json()
    reader = _as(made["secret"])
    listed = client.get("/tokens", headers=admin).json()
    roles = {t["name"]: t["role"] for t in listed["tokens"]}
    assert roles == {"mcp": "contributor", "friend": "reader"}
    assert listed["roles"] == ["reader", "contributor"]
    assert client.get("/search", params={"q": "wavelet"}, headers=reader).is_success
    assert client.get(f"/get/{lib['paper']}", headers=reader).is_success
    reads = {(m, p.pattern) for m, p in auth.READER_ROUTES}
    writes = [
        (m, p)
        for m, p in WALKED
        if not any(m == mm and pat.fullmatch(p) for mm, pat in auth.READER_ROUTES)
    ]
    assert len(writes) == len(auth.CONTRIBUTOR_ROUTES) - len(reads)
    for method, path in writes:
        path = path.replace("/1", f"/{lib['paper']}")
        got = client.request(method, path, json={}, headers=reader)
        assert got.status_code == 403, (method, path)
        assert "reader" in got.json()["detail"]
    assert client.get("/page/x", headers=reader).status_code == 404  # as for anyone
    # who it is, on the web page
    signed = client.post("/session", json={"token": made["secret"]}).json()
    assert signed["role"] == "reader" and signed["admin"] is False
    # made a contributor, the same secret writes
    assert client.put(
        "/tokens/friend", json={"role": "contributor"}, headers=admin
    ).json() == {"name": "friend", "role": "contributor"}
    renamed = client.put(
        f"/doc/{lib['paper']}/title", json={"title": "Paper"}, headers=reader
    )
    assert renamed.status_code != 403
    assert (
        client.put("/tokens/friend", json={"role": "boss"}, headers=admin).status_code
        == 400
    )
    assert (
        client.put("/tokens/nobody", json={"role": "reader"}, headers=admin).status_code
        == 404
    )
    # and the route that changes a role is the administrator's
    assert (
        client.put(
            "/tokens/friend", json={"role": "reader"}, headers=reader
        ).status_code
        == 403
    )


def test_a_token_made_before_roles_is_a_contributor(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO tokens (name, hash) VALUES ('old', 'abc')")
    con.commit()
    (row,) = [t for t in store.list_tokens(con) if t["name"] == "old"]
    assert row["role"] == "contributor"
    with pytest.raises(ValueError):
        store.add_token(con, "new", role="boss")
    assert store.set_token_role(con, "old", "reader") is True
    assert store.set_token_role(con, "gone", "reader") is False


def test_a_readers_ask_spends_nothing(client: TestClient) -> None:
    """On a host whose ask is a priced API model, a reader's question comes
    back with its passages and no answer, and asking for the paid model
    by name is refused; nothing calls the API."""
    from prax import config, models

    (config.data_dir() / config.CONFIG_NAME).write_text(
        "models:\n  api:\n    kind: openai\n    base_url: https://api.example.com/v1\n"
        "    model: gpt-x\n    price: [2, 8]\nsteps:\n  ask: {model: api}\n",
        encoding="utf-8",
    )
    models.reset()
    try:
        con = client.app.state.con
        _library(con, client)
        made = client.post("/tokens", json={"name": "friend"}, headers=_as(ADMIN))
        reader = _as(made.json()["secret"])
        out = client.post(
            "/ask", json={"question": "wavelet", "steps": 0}, headers=reader
        )
        assert out.status_code == 200
        assert out.json()["answer"] is None and out.json()["passages"]
        named = client.post(
            "/ask",
            json={"question": "wavelet", "backend": "api", "steps": 0},
            headers=reader,
        )
        assert named.status_code == 403 and "paid" in named.json()["detail"]
    finally:
        (config.data_dir() / config.CONFIG_NAME).unlink()
        models.reset()


def test_an_export_for_a_token_holds_only_what_it_sees(client: TestClient) -> None:
    """``prax export --as`` (``docs/global-ids.md``, "The wall on an
    export"): the administrator's file for a named token's holder."""
    con = client.app.state.con
    lib = _library(con, client)
    bank_hash = con.execute(
        "SELECT hash FROM documents WHERE id = ?", (lib["bank"],)
    ).fetchone()[0]
    # a name of the method that only the statement gave
    wavelet = store.entity_answering(con, "wavelet transform", "method")
    assert wavelet is not None
    store.add_label(con, wavelet, "zebrafinch transform", source_doc=lib["bank"])
    seed = {"entity": "wavelet transform", "type": "method"}
    whole = client.get("/graph/export", params=seed, headers=_as(ADMIN))
    assert whole.status_code == 200
    assert _says_text(whole.text, "Landlord Smith", bank_hash, "zebrafinch transform")
    got = client.get("/graph/export", params={**seed, "as": "mcp"}, headers=_as(ADMIN))
    assert got.status_code == 200
    assert "Open paper" in got.text
    assert "Bank statement" not in got.text and bank_hash not in got.text
    assert "Landlord Smith" not in got.text
    assert "zebrafinch" not in got.text
    nobody = client.get(
        "/graph/export", params={**seed, "as": "nobody"}, headers=_as(ADMIN)
    )
    assert nobody.status_code == 400
    # a named token may not export at all, as itself or as another
    assert client.get(
        "/graph/export", params=seed, headers=_as(lib["secret"])
    ).status_code in (401, 403)


def test_no_export_holds_who_a_picture_shows(client: TestClient) -> None:
    con = client.app.state.con
    photo = int(store.ingest_text(con, "A photo. " * 30, title="Holiday")["doc_id"])
    meta = store.get_meta(con, photo)
    store.set_meta(con, photo, {**meta, "tags": ["project:trip"]})
    for rel, dst, dtype in (
        ("depicts", "Ada", "person"),
        ("mentions", "Rome", "place"),
    ):
        store.link(
            con,
            store.Edge("Holiday", "document", rel, dst, dtype),
            source_doc=photo,
            producer="human",
        )
    got = client.get("/graph/export", params={"project": "trip"}, headers=_as(ADMIN))
    assert got.status_code == 200
    # past the header, which carries the ontology's text
    body = "".join(got.text.splitlines()[1:])
    assert "Rome" in body and "Ada" not in body and '"depicts"' not in body
