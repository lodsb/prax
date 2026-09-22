"""FastAPI door: every endpoint through TestClient against a tmp data dir."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import ontology
from prax.api import app


def ontology_version_now() -> str:
    from prax import ontology

    return ontology.current().version


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:  # runs the lifespan: opens the store
        yield c


def test_ingest_search_get(client: TestClient) -> None:
    r = client.post("/ingest", json={"text": "alpha beta gamma", "title": "abc"})
    assert r.status_code == 200 and r.json()["created"]
    doc_id = r.json()["doc_id"]

    hits = client.get("/search", params={"q": "beta"}).json()
    assert hits[0]["doc_id"] == doc_id and hits[0]["title"] == "abc"

    doc = client.get(f"/get/{doc_id}").json()
    assert doc["text"] == "alpha beta gamma" and doc["meta"] == {}

    window = client.get(f"/get/{doc_id}", params={"offset": 6, "max_chars": 4}).json()
    assert window["text"] == "beta" and window["truncated"]


def test_get_unknown_is_404(client: TestClient) -> None:
    assert client.get("/get/12345").status_code == 404


def test_ingest_file_text_and_binary(client: TestClient) -> None:
    r = client.post(
        "/ingest/file",
        files={"file": ("notes.md", b"granular synthesis notes", "text/markdown")},
        data={"title": "notes"},
    )
    assert r.status_code == 200 and r.json()["created"]
    assert client.get("/search", params={"q": "granular"}).json()[0]["title"] == "notes"

    r = client.post(
        "/ingest/file",
        files={"file": ("paper.pdf", b"%PDF-1.4 binary", "application/pdf")},
    )
    doc = client.get(f"/get/{r.json()['doc_id']}").json()
    assert doc["mime"] == "application/pdf" and doc["parsed_at"] is None
    assert doc["title"] == "paper.pdf"


def test_ingest_file_guesses_mime_from_name(client: TestClient) -> None:
    r = client.post(
        "/ingest/file",
        files={"file": ("x.txt", b"plain words", "application/octet-stream")},
    )
    doc = client.get(f"/get/{r.json()['doc_id']}").json()
    assert doc["mime"] == "text/plain" and doc["text"] == "plain words"


def test_search_with_punctuation_is_200(client: TestClient) -> None:
    client.post("/ingest", json={"text": "STFT-based analysis"})
    r = client.get("/search", params={"q": "STFT-based: what's (this)?"})
    assert r.status_code == 200 and r.json()
    assert client.get("/search", params={"q": "STFT", "doctype": "text"}).json()
    assert (
        client.get("/search", params={"q": "STFT", "doctype": "nope"}).status_code
        == 400
    )


def test_link_and_traverse(client: TestClient) -> None:
    body = {
        "src": "A",
        "src_type": "concept",
        "rel": "extends",
        "dst": "B",
        "dst_type": "concept",
    }
    assert client.post("/link", json=body).json()["edge_id"] == 1
    body.update(src="B", dst="C")
    client.post("/link", json=body)
    body.update(src="C", dst="D")
    client.post("/link", json=body)

    one = client.get("/traverse", params={"entity": "A", "hops": 1}).json()
    assert {(e["src"], e["dst"]) for e in one} == {("A", "B")}
    capped = client.get("/traverse", params={"entity": "A", "hops": 9}).json()
    assert {(e["src"], e["dst"]) for e in capped} == {("A", "B"), ("B", "C")}
    assert capped[0]["hop"] == 1 and capped[-1]["hop"] == 2


def test_document_context(client: TestClient) -> None:
    from prax import store

    con = client.app.state.con
    E = store.Edge
    a = client.post(
        "/ingest",
        json={
            "text": "a",
            "title": "Paper A",
            "meta": {
                "summary": "A is about grains.",
                "collections": ["Synthesis"],
                "tags": ["granular"],
                "zotero": {"kind": "attachment", "keys": ["ATT1"], "items": ["ITEM1"]},
            },
        },
    ).json()["doc_id"]
    b = client.post("/ingest", json={"text": "b", "title": "Paper B"}).json()["doc_id"]
    c = client.post("/ingest", json={"text": "c", "title": "Paper C"}).json()["doc_id"]
    note = client.post(
        "/ingest",
        json={
            "text": "a note",
            "title": "Note on A",
            "meta": {"zotero": {"kind": "note", "keys": ["N1"], "parent": "ITEM1"}},
        },
    ).json()["doc_id"]
    store.link(con, E("Paper A", "paper", "authored_by", "Ada", "author"), source_doc=a)
    store.link(con, E("Paper B", "paper", "authored_by", "Ada", "author"), source_doc=b)
    store.link(
        con,
        E("Paper A", "paper", "about", "granular synthesis", "concept"),
        source_doc=a,
    )
    store.link(
        con, E("Paper A", "paper", "uses", "phase vocoder", "method"), source_doc=a
    )
    store.link(
        con,
        E("Paper C", "paper", "about", "granular synthesis", "concept"),
        source_doc=c,
    )
    store.link(
        con, E("Paper C", "paper", "uses", "phase vocoder", "method"), source_doc=c
    )
    store.link(con, E("Paper A", "paper", "cites", "Paper B", "paper"), source_doc=a)
    store.link(
        con, E("Paper A", "paper", "cites", "Outside Work", "paper"), source_doc=a
    )
    store.link(
        con,
        E("Paper C", "paper", "cites", "Paper A", "paper"),
        source_doc=c,
        confidence="INFERRED",  # matched by title from C's reference list
    )
    ctx = client.get(f"/doc/{a}/context").json()
    assert ctx["summary"] == "A is about grains."
    assert [(e["name"], e["rel"]) for e in ctx["entities"]] == [
        ("granular synthesis", "about"),
        ("phase vocoder", "uses"),
    ]
    assert ctx["cites"] == [
        {"title": "Paper B", "doc_id": b, "confidence": "EXTRACTED"},
        {"title": "Outside Work", "doc_id": None, "confidence": "EXTRACTED"},
    ]
    assert ctx["cited_by"] == [
        {"doc_id": c, "title": "Paper C", "confidence": "INFERRED"}
    ]
    assert ctx["shared"][0]["doc_id"] == c and ctx["shared"][0]["count"] == 2
    assert ctx["same_authors"] == [
        {"doc_id": b, "title": "Paper B", "authors": ["Ada"]}
    ]
    assert ctx["similar"] == []  # no vectors in this fixture
    z = ctx["zotero"]
    assert z["collections"] == ["Synthesis"] and z["tags"] == ["granular"]
    assert [s["doc_id"] for s in z["siblings"]] == [note]  # the note names the item
    nctx = client.get(f"/doc/{note}/context").json()
    assert nctx["zotero"]["parent"] == {"key": "ITEM1", "doc_id": a, "title": "Paper A"}
    assert nctx["entities"] == [] and nctx["cites"] == []
    assert client.get("/doc/999/context").status_code == 404


def test_page_endpoints(client: TestClient) -> None:
    paper = client.post(
        "/ingest", json={"text": "grains", "title": "Grain Paper"}
    ).json()["doc_id"]
    r = client.put(
        "/page/granular-study", json={"text": "# Study\n\nOpen.", "kind": "project"}
    )
    assert r.status_code == 200 and r.json()["created"] and r.json()["revision"] == 1
    r = client.put(
        "/page/note-1",
        json={
            "text": "Figure 3 matters.",
            "kind": "addendum",
            "annotates": [paper],
            "part_of": "granular-study",
            "title": "Note on grains",
        },
    )
    assert r.status_code == 200
    note_id = r.json()["doc_id"]
    page = client.get("/page/note-1").json()
    assert page["kind"] == "addendum" and page["text"] == "Figure 3 matters."
    # agent may not overwrite; append works; a person may
    r = client.put("/page/note-1", json={"text": "x", "author": "agent"})
    assert r.status_code == 409
    r = client.post(
        "/page/note-1/append", json={"section": "Also this.", "heading": "Agent"}
    )
    assert r.status_code == 200 and r.json()["revision"] == 2
    assert client.get("/page/note-1/revision/1").json()["text"] == "Figure 3 matters."
    assert client.get("/page/note-1/revision/9").status_code == 404
    assert (
        client.put("/page/note-1", json={"text": "mine", "author": "human"}).json()[
            "revision"
        ]
        == 3
    )
    listed = client.get("/pages").json()
    assert {pg["slug"] for pg in listed} == {"granular-study", "note-1"}
    assert [
        pg["slug"] for pg in client.get("/pages", params={"kind": "project"}).json()
    ] == ["granular-study"]
    r = client.post("/project/granular-study/members", json={"doc_id": paper})
    assert r.status_code == 200 and r.json()["edge_id"]
    assert client.post(
        "/project/granular-study/members", json={"doc_id": paper}
    ).json()["existing"]
    assert (
        client.post("/project/nope/members", json={"doc_id": paper}).status_code == 404
    )
    ctx = client.get(f"/doc/{paper}/context").json()
    assert [n["doc_id"] for n in ctx["notes"]] == [note_id]
    proj = client.get("/page/granular-study").json()
    pctx = client.get(f"/doc/{proj['doc_id']}/context").json()
    assert {m["title"] for m in pctx["members"]} == {"Grain Paper", "Note on grains"}
    assert client.get("/page/missing").status_code == 404
    assert (
        client.put("/page/bad", json={"text": "t", "kind": "diary"}).status_code == 400
    )
    assert (
        client.put("/page/n9", json={"text": "t", "annotates": [999]}).status_code
        == 404
    )
    assert client.post("/page/missing/append", json={"section": "s"}).status_code == 404


def test_graph_overview(client: TestClient) -> None:
    from prax import store

    con = client.app.state.con
    E = store.Edge
    store.link(con, E("P1", "paper", "uses", "granular synthesis", "method"))
    store.link(con, E("P2", "paper", "uses", "granular synthesis", "method"))
    store.link(con, E("P1", "paper", "uses", "phase vocoder", "method"))
    store.link(
        con, E("phase vocoder", "method", "uses", "granular synthesis", "method")
    )
    store.link(con, E("P3", "paper", "about", "reverb", "concept"))
    g = client.get("/graph/overview", params={"limit": 2}).json()
    assert [(n["name"], n["degree"]) for n in g["nodes"]] == [
        ("granular synthesis", 3),
        ("phase vocoder", 2),
    ]
    assert [(e["src"], e["rel"], e["dst"]) for e in g["edges"]] == [
        ("phase vocoder", "uses", "granular synthesis")
    ]
    # co-occurrence: hubs that share source documents
    d1 = client.post("/ingest", json={"text": "a", "title": "D1"}).json()["doc_id"]
    d2 = client.post("/ingest", json={"text": "b", "title": "D2"}).json()["doc_id"]
    for d in (d1, d2):
        store.link(
            con, E("D", "paper", "uses", "granular synthesis", "method"), source_doc=d
        )
        store.link(con, E("D", "paper", "about", "reverb", "concept"), source_doc=d)
    g = client.get("/graph/overview", params={"limit": 5, "min_shared": 2}).json()
    assert [(l["a"], l["b"], l["weight"]) for l in g["links"]] == [
        ("granular synthesis", "reverb", 2)
    ]
    assert (
        client.get("/graph/overview", params={"limit": 5, "min_shared": 3}).json()[
            "links"
        ]
        == []
    )
    assert "P1" not in {n["name"] for n in g["nodes"]}  # papers are not hubs


def test_review_queue_endpoints(client: TestClient) -> None:
    from prax import store

    con = client.app.state.con
    doc = client.post("/ingest", json={"text": "t", "title": "P"}).json()["doc_id"]
    a = store.queue_review(
        con,
        src="P",
        src_type="paper",
        rel="about",
        dst="STFT",
        dst_type="method",
        reason="'about' does not accept dst type 'method'",
        source_doc=doc,
        evidence="q",
    )
    b = store.queue_review(
        con,
        src="P",
        src_type=None,
        rel="funded_by",
        dst="EU",
        dst_type=None,
        reason="unmapped: no relation",
        source_doc=doc,
    )
    c = store.queue_review(
        con, src="x", src_type=None, rel="r", dst="y", dst_type=None, reason="z"
    )
    page = client.get("/review", params={"limit": 2}).json()
    assert page["total"] == 3 and [i["id"] for i in page["items"]] == [a, b]
    assert (
        client.get("/review", params={"limit": 2, "offset": 2}).json()["items"][0]["id"]
        == c
    )
    onto = client.get("/ontology").json()
    assert "paper" in onto["entity_types"] and "about" in onto["relations"]
    # link with a corrected target type: the edge is written with the item's evidence
    r = client.post(
        f"/review/{a}", json={"resolution": "linked", "dst_type": "concept"}
    )
    assert r.status_code == 200 and r.json()["edge_id"]
    edge = client.get("/traverse", params={"entity": "P"}).json()[0]
    assert (edge["dst"], edge["dst_type"], edge["evidence"], edge["source_doc"]) == (
        "STFT",
        "concept",
        "q",
        doc,
    )
    # an invalid link leaves the item open
    r = client.post(
        f"/review/{b}",
        json={"resolution": "linked", "src_type": "paper", "dst_type": "concept"},
    )  # a venue is an organization now, so funded_by would take it; a concept not
    assert r.status_code == 400 and "funded_by" in r.json()["detail"]
    assert (
        client.post(f"/review/{b}", json={"resolution": "ontology"}).status_code == 200
    )
    assert (
        client.post(f"/review/{c}", json={"resolution": "dropped"}).status_code == 200
    )
    assert client.get("/review").json()["total"] == 0
    assert client.get("/review", params={"open": False}).json()["total"] == 3
    assert client.post(f"/review/{c}", json={"resolution": "bogus"}).status_code == 400
    assert client.post("/review/999", json={"resolution": "dropped"}).status_code == 404


def test_review_filters_bulk_and_replay(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax import store

    con = client.app.state.con
    doc = client.post("/ingest", json={"text": "t", "title": "P"}).json()["doc_id"]
    for i in range(3):
        store.queue_review(
            con,
            src="P",
            src_type=None,
            rel="cites",
            dst=f"[{i}]",
            dst_type=None,
            reason="unmapped: reference number",
            source_doc=doc,
        )
    typed = store.queue_review(
        con,
        src="P",
        src_type="paper",
        rel="uses",
        dst="Fourier",
        dst_type="concept",
        reason="'uses' does not accept dst type 'concept'",
        source_doc=doc,
        evidence="q",
    )
    store.queue_review(
        con,
        src="P",
        src_type="paper",
        rel="cites",
        dst="Matlab",
        dst_type="tool",
        reason="'cites' does not accept dst type 'tool'",
        source_doc=doc,
    )
    assert client.get("/review", params={"rel": "cites"}).json()["total"] == 4
    assert (
        client.get("/review", params={"rel": "cites", "unmapped": True}).json()["total"]
        == 3
    )
    assert client.get("/review", params={"unmapped": False}).json()["total"] == 2
    assert (
        client.post("/review/bulk", json={"resolution": "dropped"}).status_code == 400
    )
    assert (
        client.post(
            "/review/bulk", json={"resolution": "linked", "rel": "x"}
        ).status_code
        == 400
    )
    r = client.post(
        "/review/bulk", json={"resolution": "dropped", "rel": "cites", "unmapped": True}
    )
    assert r.json() == {"resolved": 3} and client.get("/review").json()["total"] == 2
    # replay: v2 accepts "paper uses concept" and links it with its evidence;
    # "paper cites tool" stays open until an ontology allows it
    rep = client.post("/review/replay").json()
    assert (rep["linked"], rep["still_open"]) == (1, 1)
    edge = client.get("/traverse", params={"entity": "Fourier"}).json()[0]
    assert (edge["rel"], edge["evidence"]) == ("uses", "q")
    assert edge["ontology_version"] == ontology_version_now()
    assert client.get("/review").json()["total"] == 1
    # a bumped research module in a copy of the ontology directory: cites
    # may now target a tool, and the replay links the last item under it
    from prax import config

    onto_dir = tmp_path / "onto"
    onto_dir.mkdir()
    for f in Path(config.ONTOLOGY_PATH).glob("*.yaml"):
        (onto_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    research = (onto_dir / "research.yaml").read_text(encoding="utf-8")
    research = research.replace("version: 8", "version: 99", 1).replace(
        "    domain: [paper, page, project]\n    range: [paper]\n",
        "    domain: [paper, page, project]\n    range: [paper, tool]\n",
        1,
    )
    assert "version: 99" in research and "range: [paper, tool]" in research
    (onto_dir / "research.yaml").write_text(research, encoding="utf-8")
    monkeypatch.setenv("PRAX_ONTOLOGY", str(onto_dir))
    later = ontology.current().version  # every module, research at 99 now
    assert "research99" in later
    rep = client.post("/review/replay").json()
    assert (rep["ontology_version"], rep["linked"], rep["still_open"]) == (later, 1, 0)
    assert client.get("/review").json()["total"] == 0
    assert store.get_review(con, typed)["resolution"] == "linked"


def test_link_bad_confidence_is_400(client: TestClient) -> None:
    body = {
        "src": "A",
        "src_type": "x",
        "rel": "r",
        "dst": "B",
        "dst_type": "x",
        "confidence": "GUESS",
    }
    assert client.post("/link", json=body).status_code == 400


def test_search_kind_filter_and_chunk_route(client: TestClient) -> None:
    table = "Table 1: sizes\n\n| part | mm |\n|---|---|\n| bolt | 12 |\n"
    r = client.post("/ingest", json={"text": table, "title": "t"})
    hits = client.get("/search", params={"q": "bolt", "kind": "table"}).json()
    assert hits and hits[0]["kind"] == "table"
    assert hits[0]["doc_id"] == r.json()["doc_id"]
    chunk = client.get(f"/chunk/{hits[0]['chunk_id']}").json()
    assert chunk["data"]["rows"] == [["bolt", "12"]]
    assert chunk["locator"]["char_start"] == 0
    assert client.get("/chunk/999999").status_code == 404
    bad = client.get("/search", params={"q": "bolt", "kind": "audio"})
    assert bad.status_code == 400


def test_browsing_endpoints_and_ui(client: TestClient) -> None:
    pdf = client.post(
        "/ingest/file",
        files={"file": ("paper.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"title": "A fake paper"},
    ).json()
    note = client.post(
        "/ingest",
        json={
            "text": "# Intro\n\nGranular clouds.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
            "title": "note",
            "meta": {"source": "zotero"},
        },
    ).json()
    listing = client.get("/documents").json()
    assert listing["total"] == 2
    js = client.get("/ui/app.js")
    assert js.status_code == 200 and js.headers["cache-control"] == "no-cache"
    assert [d["id"] for d in listing["items"]] == [note["doc_id"], pdf["doc_id"]]
    assert listing["items"][0]["n_chunks"] >= 1 and listing["items"][0]["meta"] == {
        "source": "zotero"
    }
    assert client.get("/documents", params={"title": "FAKE"}).json()["total"] == 1
    assert client.get("/documents", params={"source": "zotero"}).json()["total"] == 1
    assert (
        client.get("/documents", params={"mime": "application/pdf"}).json()["total"]
        == 1
    )

    orig = client.get(f"/doc/{pdf['doc_id']}/original")
    assert orig.status_code == 200 and orig.content == b"%PDF-1.4 fake"
    assert orig.headers["content-type"].startswith("application/pdf")
    assert "inline" in orig.headers["content-disposition"]
    assert orig.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in orig.headers  # the viewer runs nothing
    assert client.get("/doc/999/original").status_code == 404
    # anything that is not a PDF or a raster image is sandboxed: an SVG
    # with a script would otherwise run on the door's origin
    svg = client.post(
        "/ingest/file",
        files={"file": ("d.svg", b"<svg onload='x()'/>", "image/svg+xml")},
    ).json()
    orig = client.get(f"/doc/{svg['doc_id']}/original")
    assert orig.headers["content-security-policy"].startswith("sandbox")

    text = client.get(f"/doc/{note['doc_id']}/text")
    assert text.status_code == 200 and text.text.startswith("# Intro")
    assert text.headers["x-content-type-options"] == "nosniff"
    assert text.headers["content-type"].startswith("text/markdown")

    chunks = client.get(f"/doc/{note['doc_id']}/chunks").json()
    assert [c["kind"] for c in chunks] == ["text", "table"]
    assert chunks[1]["data"]["rows"] == [["1", "2"]] and chunks[1]["heading"] == [
        "Intro"
    ]
    assert client.get("/doc/999/chunks").status_code == 404

    client.post(
        "/link",
        json={
            "src": "A fake paper",
            "src_type": "paper",
            "rel": "authored_by",
            "dst": "Ada Lovelace",
            "dst_type": "author",
        },
    )
    ents = client.get("/entities", params={"q": "lovelace"}).json()
    assert ents == [
        {"id": ents[0]["id"], "name": "Ada Lovelace", "type": "author", "degree": 1}
    ]

    assert client.get("/", follow_redirects=False).status_code == 307
    page = client.get("/ui/")
    assert page.status_code == 200 and "<title>prax</title>" in page.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/vendor/marked.min.js").status_code == 200
    # the maths: KaTeX and one of its fonts, served by the door itself (the
    # policy allows no other origin for scripts, styles or fonts)
    assert client.get("/ui/vendor/katex/katex.min.js").status_code == 200
    assert client.get("/ui/vendor/katex/katex.min.css").status_code == 200
    font = client.get("/ui/vendor/katex/fonts/KaTeX_Main-Regular.woff2")
    assert font.status_code == 200
    # the rendered Markdown of strangers (captured pages, a model's answer)
    # cannot run script in the UI: no inline script, only the UI's own files
    policy = page.headers["content-security-policy"]
    scripts = [d for d in policy.split(";") if d.strip().startswith("script-src")]
    assert scripts == [" script-src 'self'"]
    assert "frame-ancestors 'none'" in policy and "form-action 'self'" in policy
    assert "<script src=" in page.text and "<script>" not in page.text
    assert client.get("/ui/theme.js").status_code == 200


def test_ui_error_is_logged(client: TestClient, caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="prax.ui"):
        r = client.post(
            "/ui/error",
            json={"message": "boom", "hash": "#doc/1", "stack": "at x", "agent": "t"},
        )
    assert r.status_code == 200 and r.json() == {"logged": True}
    assert "boom" in caplog.text and "#doc/1" in caplog.text


def test_extension_origins_get_cors_and_private_network_consent(
    client: TestClient,
) -> None:
    """An extension's preflight is answered without a token, for any
    extension origin, and with Chrome's private-network consent."""
    r = client.options(
        "/inbox",
        headers={
            "Origin": "moz-extension://1234-abcd",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "moz-extension://1234-abcd"
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
    assert r.headers["access-control-allow-private-network"] == "true"
    # a web origin is not answered unless configured
    r = client.options(
        "/inbox",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in r.headers


def test_a_search_reports_where_its_time_went(client: TestClient) -> None:
    from prax import store

    client.post("/ingest", json={"text": "granular synthesis scatters grains " * 20})
    timing: dict[str, float] = {}
    hits = store.search(client.app.state.con, "granular grains", timing=timing)
    assert hits and "fts" in timing and timing["fts"] >= 0
    assert all(v >= 0 for v in timing.values())


def test_a_body_over_the_cap_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The door reads an upload into memory on a host with a gigabyte:
    ``door.max_upload_mb`` bounds it, by the declared length before the
    body is read, and by the bytes read when nothing was declared."""
    monkeypatch.setenv("PRAX_MAX_UPLOAD_MB", "1")
    big = b"x" * (1024 * 1024 + 1)
    r = client.post("/ingest/file", files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 413 and "max_upload_mb" in r.json()["detail"]
    r = client.post("/ingest", json={"text": "x" * (1024 * 1024 + 100)})
    assert r.status_code == 413
    small = client.post(
        "/ingest/file", files={"file": ("ok.txt", b"fine " * 100, "text/plain")}
    )
    assert small.status_code == 200
