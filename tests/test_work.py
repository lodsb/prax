"""The work protocol end to end: the door hands out, a worker that only
speaks HTTP does the work, the door takes the results in and stays the
only writer."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import embeddings, inbox, models, steps, store, work, worker


def pending_one(con: Any, doc_id: int) -> dict[str, Any]:
    """The reading a document is waiting for, or {} — a pending request
    is a row of the queue since migration 21, not a field on the
    document."""
    rows = store.pending_readings(con, doc_id)
    return rows[0] if rows else {}


needs_usearch = pytest.mark.skipif(
    not store.vectors_available(), reason="usearch not installed"
)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_EXTRACT", "stub")
    monkeypatch.setenv("PRAX_TITLES", "none")
    monkeypatch.setenv("PRAX_EMBED", "hash")
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _door(client: TestClient) -> worker.Door:
    return worker.Door("http://testserver", client=client, name="test-worker")


def test_hand_out_and_take_in_extract(client: TestClient) -> None:
    con = client.app.state.con
    a = client.post(
        "/ingest",
        json={"text": "reverb design by feedback delay networks " * 30, "title": "A"},
    ).json()["doc_id"]
    inbox.ingest_upload(
        con, ("a note on granular synthesis " * 30).encode(), filename="b.txt"
    )
    # captures only: the plain ingest is not a capture
    batch = client.get("/work/extract", params={"limit": 5}).json()
    ids = [i["doc_id"] for i in batch["items"]]
    assert a not in ids and len(ids) == 1
    item = batch["items"][0]
    assert item["header"].startswith("Title:") and item["ontology_version"]
    # leased: a second worker gets nothing
    assert client.get("/work/extract", params={"limit": 5}).json()["items"] == []
    assert (
        client.get("/work/extract", params={"limit": 5, "scope": "all"}).json()[
            "items"
        ][0]["doc_id"]
        == a
    )
    # the worker does it with the stub extractor and posts
    d = _door(client)
    work._leases.clear()  # the hand-outs above went to this test, not the worker
    out = worker.run_once(d, steps=("extract",), scope="all", log_=lambda t: None)
    assert "extract" in out
    for doc_id in ids + [a]:
        assert store.get_meta(con, doc_id)["extraction"]["extractor"] == "stub"
    assert (
        client.get("/work/extract", params={"limit": 5, "scope": "all"}).json()["items"]
        == []
    )
    assert client.get("/work/nope").status_code == 400


def test_parse_and_titles_through_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    # a pdf the door only registered: the worker fetches the original and parses
    pdf = Path("tests/fixtures/zotero/storage").rglob("*.pdf")
    src = next(iter(pdf), None)
    if src is None:
        pytest.skip("no fixture PDF")
    cap = inbox.ingest_upload(
        con, src.read_bytes(), filename="paper.pdf", mime="application/pdf"
    )
    assert not cap.indexed
    batch = client.get("/work/parse").json()
    assert [i["doc_id"] for i in batch["items"]] == [cap.doc_id]
    assert batch["items"][0]["original"] == f"/doc/{cap.doc_id}/original"
    d = _door(client)
    work._leases.clear()
    out = worker.run_once(d, steps=("parse",), log_=lambda t: None)
    assert out["parse"].startswith("1 parsed")
    assert store.get_document(con, cap.doc_id, max_chars=0)["text_len"] > 0
    assert client.get("/work/parse").json()["items"] == []
    # the worker counted the pages on the way and the door kept the count
    assert store.get_meta(con, cap.doc_id)["pages"] >= 1
    # titles: the file name is not a title; with no titles model the worker
    # says so and the door remembers the try
    batch = client.get("/work/titles").json()
    assert [i["doc_id"] for i in batch["items"]] == [cap.doc_id] and batch["items"][0][
        "why"
    ] == "filename"
    work._leases.clear()
    out = worker.run_once(d, steps=("titles",), log_=lambda t: None)
    assert out["titles"].endswith("1 left")
    assert store.get_meta(con, cap.doc_id)["titles_tried"]["why"] == "no titles model"
    assert client.get("/work/titles").json()["items"] == []
    # a retitle through the door
    rep = client.post(
        "/work/titles",
        json={
            "results": [
                {
                    "doc_id": cap.doc_id,
                    "title": "A Real Title",
                    "source": "test",
                    "confidence": "printed",
                }
            ]
        },
    ).json()
    assert rep["applied"] == 1
    assert store.get_document(con, cap.doc_id, max_chars=0)["title"] == "A Real Title"


@needs_usearch
def test_embed_through_the_door(client: TestClient) -> None:
    con = client.app.state.con
    emb = embeddings.current()
    assert emb is not None
    doc = client.post(
        "/ingest",
        json={"text": "wave digital filters for diode clippers " * 30, "title": "W"},
    ).json()["doc_id"]
    batch = client.get("/work/embed", params={"limit": 50}).json()
    assert batch["model"] == emb.name and batch["chunks"] and batch["fields"]
    assert (
        client.get("/work/embed", params={"limit": 50}).json()["chunks"] == []
    )  # leased
    d = _door(client)
    work._leases.clear()
    out = worker.run_once(d, steps=("embed",), log_=lambda t: None)
    assert out["embed"].endswith("vectors")
    assert store.count_pending_embeddings(con, emb.name) == 0
    assert store.count_pending_document_embeddings(con, emb.name) == 0
    hits = client.get(
        "/search", params={"q": "diode clipper wave digital", "mode": "vec"}
    ).json()
    assert hits and hits[0]["doc_id"] == doc
    merged = client.post("/vectors/merge").json()
    assert merged["chunks"]["delta"] == 0
    # a wrong model is refused
    assert (
        client.post(
            "/work/embed", json={"model": "other", "chunks": [], "fields": []}
        ).status_code
        == 400
    )


def test_a_beat_renews_the_leases_its_worker_holds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    inbox.ingest_upload(con, ("a note " * 60).encode(), filename="n.txt")
    work._leases.clear()
    items = client.get("/work/extract", headers={"X-Prax-Worker": "w1"}).json()["items"]
    doc = items[0]["doc_id"]
    held_until = work._leases[("extract", doc)][1]
    job = client.post("/work/session", json={"name": "worker"}).json()["job_id"]
    # another worker's beat does not touch w1's lease
    r = client.post(
        f"/work/session/{job}",
        json={"renew": {"step": "extract", "items": [doc]}},
        headers={"X-Prax-Worker": "w2"},
    ).json()
    assert r["renewed"] == 0 and work._leases[("extract", doc)][1] == held_until
    # the holder's does, for a whole lease again
    monkeypatch.setattr(work, "LEASE_SECONDS", 5000)
    r = client.post(
        f"/work/session/{job}",
        json={"done": 0, "renew": {"step": "extract", "items": [doc]}},
        headers={"X-Prax-Worker": "w1"},
    ).json()
    assert r["renewed"] == 1 and work._leases[("extract", doc)][1] > held_until + 4000
    # a free item is not leased by a renewal
    assert work.renew("extract", [doc + 1], "w1") == 0
    assert ("extract", doc + 1) not in work._leases


def test_parse_results_land_one_document_at_a_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch of two: the first is posted (and its lease released) before
    the second is read, and the session is beaten with the count."""
    con = client.app.state.con
    a = inbox.ingest_upload(
        con, b"first note " * 50, filename="a.txt", mime="text/plain"
    )
    b = inbox.ingest_upload(
        con, b"second note " * 50, filename="b.txt", mime="text/plain"
    )
    for cap in (a, b):  # unindexed, so the parse step wants them
        con.execute(
            "UPDATE documents SET text_hash = NULL, parsed_at = NULL WHERE id = ?",
            (cap.doc_id,),
        )
    con.commit()
    work._leases.clear()
    d = _door(client)
    seen: list[tuple[int, int]] = []  # (doc posted, how many were still leased)
    real_post = d.post_json

    def spying_post(path: str, body: dict | None = None):  # type: ignore[no-untyped-def]
        if path == "/work/parse":
            leased = sum(1 for (step, _), _v in work._leases.items() if step == "parse")
            seen.append((int(body["results"][0]["doc_id"]), leased))
        return real_post(path, body)

    monkeypatch.setattr(d, "post_json", spying_post)
    out = worker.run_once(d, steps=("parse",), log_=lambda t: None, scope="all")
    assert out["parse"].startswith("2 parsed")
    # two posts, one per document; the second document was still leased
    # when the first landed, nothing was when the second did
    assert [doc for doc, _ in seen] == [a.doc_id, b.doc_id]
    assert [leased for _, leased in seen] == [2, 1]
    assert store.get_document(con, a.doc_id, max_chars=0)["text_len"] > 0
    assert store.get_document(con, b.doc_id, max_chars=0)["text_len"] > 0


def test_session_job_and_watch_once(client: TestClient, tmp_path: Path) -> None:
    con = client.app.state.con
    drop = tmp_path / "prax-inbox"
    drop.mkdir()
    f = drop / "note.txt"
    f.write_text("a dropped note about reverb " * 20, encoding="utf-8")
    (drop / "note.txt.json").write_text(
        json.dumps({"title": "Dropped", "tags": ["t"], "domains": ["research"]}),
        encoding="utf-8",
    )
    import os
    import time

    for p in drop.iterdir():
        os.utime(p, (time.time() - 10, time.time() - 10))
    d = _door(client)
    worker.watch(d, folders=[drop], once=True, log_=lambda t: None)
    assert not f.exists()
    recent = inbox.recent(con)
    assert recent and recent[0]["title"] == "Dropped" and recent[0]["tags"] == ["t"]
    jobs = store.list_jobs(con)
    assert (
        jobs["recent"]
        and jobs["recent"][0]["name"] == "worker"
        and jobs["recent"][0]["host"] == "test-worker"
    )
    assert jobs["recent"][0]["status"] == "done"


def test_leases_expire(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    con = client.app.state.con
    inbox.ingest_upload(con, ("a note " * 60).encode(), filename="n.txt")
    assert len(client.get("/work/extract").json()["items"]) == 1
    assert client.get("/work/extract").json()["items"] == []
    monkeypatch.setattr(work, "LEASE_SECONDS", 0)
    # a fresh hand-out after the lease ran out
    work._leases.clear()
    assert len(client.get("/work/extract").json()["items"]) == 1


def test_the_resolve_step_computes_the_likely_pairs_off_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The likely tier used to embed every entity name inside the door
    (and crashed it at 138,000 of them): now the door hands a type's
    names to a worker, the worker embeds them and posts the close pairs,
    and the plan reads those. A type is due once a week."""
    from prax import resolution

    con = client.app.state.con
    for paper, concept in (
        ("P", "granular synthesis"),
        ("Q", "granular synthesis method"),
        ("R", "room acoustics"),
    ):
        store.link(
            con,
            store.Edge(paper, "paper", "about", concept, "concept"),
            source_doc=None,
            producer="test",
        )
    monkeypatch.setattr(resolution, "LIKELY_THRESHOLD", 0.8)  # the hash embedder
    batch = client.get("/work/resolve").json()
    assert batch["type"] == "concept" and len(batch["names"]) == 3
    assert batch["threshold"] == 0.8 and batch["model"] == "hash-test"
    # leased: nothing else of that type goes out; the next type may
    again = client.get("/work/resolve").json()
    assert again["type"] != "concept"
    work._leases.clear()
    said: list[str] = []
    out = worker.run_once(_door(client), steps=("resolve",), log_=said.append)
    assert out["resolve"].startswith("concept: 1 likely pairs among 3 names")
    rows = store.entity_candidates(con, "concept")
    assert len(rows) == 1 and rows[0]["producer"] == "hash-test via test-worker"
    assert {rows[0]["a_name"], rows[0]["b_name"]} == {
        "granular synthesis",
        "granular synthesis method",
    }
    plan = client.post("/graph/resolve", json={}).json()["plan"]
    assert plan["likely"]["count"] == 1
    assert plan["likely"]["computed"].keys() == {"concept"}
    # computed this week: the type is not handed out again, the others are
    work._leases.clear()
    types = set()
    for _ in range(6):
        b = client.get("/work/resolve").json()
        if b["type"]:
            types.add(b["type"])
    assert "concept" not in types
    # a wrong model is refused, as for embed
    r = client.post(
        "/work/resolve", json={"type": "concept", "model": "other", "pairs": []}
    )
    assert r.status_code == 400
    assert (
        client.post(
            "/work/resolve", json={"type": "paper", "model": "hash"}
        ).status_code
        == 400
    )


def test_the_adjudicate_step_spends_only_when_told_and_records_its_decisions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The likely pairs go to the adjudicate step's model through the
    worker (paid: --spend), the decisions come back as merges and
    recorded declines, and the script that opened the database file for
    this is no longer the way."""
    from prax import models, resolution

    con = client.app.state.con
    for paper, concept in (
        ("P", "granular synthesis"),
        ("Q", "granular synthesis method"),
        ("R", "spatial audio"),
        ("S", "spatial audio coding"),
    ):
        store.link(
            con,
            store.Edge(paper, "paper", "about", concept, "concept"),
            source_doc=None,
            producer="test",
        )
    monkeypatch.setattr(resolution, "LIKELY_THRESHOLD", 0.7)
    work._leases.clear()
    worker.run_once(_door(client), steps=("resolve",), log_=lambda t: None)
    assert len(resolution.plan(con).likely) == 2
    # no adjudicate model: the step is silent
    monkeypatch.setattr(models, "resolve", lambda s: None)
    out = worker.run_once(_door(client), steps=("adjudicate",), log_=lambda t: None)
    assert "adjudicate" not in out
    assert client.get("/work/adjudicate").json()["items"] == []
    # a paid model without --spend: skipped, nothing fetched
    spec = models.ModelSpec(name="opus", kind="claude", model="claude-opus-5")
    monkeypatch.setattr(
        models, "resolve", lambda s: spec if s == "adjudicate" else None
    )
    out = worker.run_once(_door(client), steps=("adjudicate",), log_=lambda t: None)
    assert out["adjudicate"].startswith("skipped: the adjudicate step is opus (paid)")

    # with --spend the model decides: the granular pair merges, the audio pair is a no
    class Judge:
        name = "claude-opus-5"
        cost = 0.0055

        def __init__(self, model=""):
            self.usage = {"input_tokens": 900, "output_tokens": 40}

        def decide(self, cands):
            return ["granular" in c.keep_name for c in cands]

    monkeypatch.setattr(resolution, "ClaudeAdjudicator", Judge)
    said: list[str] = []
    out = worker.run_once(
        _door(client), steps=("adjudicate",), spend=True, log_=said.append
    )
    assert out["adjudicate"] == "1 merged, 1 kept apart of 2 pairs"
    assert resolution.plan(con).likely == []
    merged = con.execute(
        "SELECT count(*) FROM entities WHERE canonical_id IS NOT NULL"
    ).fetchone()[0]
    assert merged == 1
    assert (
        con.execute(
            "SELECT decided_by FROM entity_candidates WHERE decided = 'different'"
        ).fetchone()["decided_by"]
        == "claude-opus-5"
    )
    assert client.get("/work/adjudicate").json()["items"] == []  # nothing left


def test_a_title_whose_server_is_down_is_deferred_not_the_whole_pass(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The titles model's server paused for marker failed the whole titles
    pass ("pass failed: ServerNotReady") every cycle; now the item is
    deferred like a reading, no try recorded against it."""
    from prax import models

    con = client.app.state.con
    cap = inbox.ingest_upload(
        con, ("a paper on granular synthesis " * 40).encode(), filename="scan_01.txt"
    )
    spec = models.ModelSpec(
        name="srv", kind="openai", base_url="http://127.0.0.1:1/v1", model="q"
    )
    monkeypatch.setattr(models, "resolve", lambda s: spec if s == "titles" else None)

    class Down:
        name = "q@127.0.0.1:1"

        def chat(self, system, user, **kw):
            raise models.ServerNotReady("http://127.0.0.1:1/v1: not answering")

    monkeypatch.setattr(models, "runtime", lambda s: Down())
    said: list[str] = []
    out = worker.run_once(_door(client), steps=("titles",), log_=said.append)
    assert "pass failed" not in " ".join(said)
    assert any("not yet" in line for line in said)
    assert "titles" in out
    assert "titles_tried" not in store.get_meta(con, cap.doc_id)
    held = work._leases[("titles", cap.doc_id)]
    assert held[1] > time.monotonic() + work.DEFER_SECONDS - 60
    assert client.get("/work/titles").json()["items"] == []  # deferred, not out again


def test_a_requested_reading_goes_out_first_and_comes_back_with_its_outcome(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person asks for the vision model over a scanned PDF from the
    document page; the door hands the request out before the pending
    captures, the worker runs the named extractor in the asked mode with
    force, and the outcome lands on the document."""
    import pymupdf

    from prax import models

    con = client.app.state.con
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "A printed page.")
    doc.new_page()  # a scan
    pdf = doc.tobytes()
    doc.close()
    scan = store.register(con, pdf, mime="application/pdf", title="scan.pdf")["doc_id"]
    store.index_text(con, scan, "old OCR text " * 40, text_source="pymupdf4llm-ocr/1")
    # the request: validated against the type
    r = client.post(f"/doc/{scan}/reading", json={"extractor": "vision", "mode": None})
    assert r.status_code == 400 and "does not read" in r.json()["detail"]
    ask = {"extractor": "vision-pages", "mode": "all"}
    r = client.post(f"/doc/{scan}/reading", json=ask)
    assert r.status_code == 200 and r.json()["state"] == "requested"
    assert client.get("/readings").json()["requested"][0]["doc_id"] == scan
    # the door hands it out first, named, forced, in its mode
    batch = client.get("/work/parse").json()
    item = batch["items"][0]
    assert item["doc_id"] == scan and item["extractor"] == "vision-pages"
    assert item["mode"] == "all" and item["force"] is True and "previous" not in item
    work._leases.clear()
    # the worker: a fake vision model; every page rendered, since mode is all
    seen: list[str] = []
    spec = models.ModelSpec(
        name="server-vl", kind="openai", base_url="http://127.0.0.1:1/v1", model="vl"
    )
    monkeypatch.setattr(models, "resolve", lambda s: spec if s == "vision" else None)

    class FakeRuntime:
        def chat(self, system, user, **kw):
            seen.append(os.environ.get("PRAX_VISION_PAGES") or "")
            return "(handwritten) a transcribed page " + "word " * 100, {}

    monkeypatch.setattr(models, "runtime", lambda s: FakeRuntime())
    monkeypatch.delenv("PRAX_VISION_PAGES", raising=False)
    out = worker.run_once(_door(client), steps=("parse",), log_=lambda t: None)
    assert out["parse"].startswith("1 parsed")
    assert seen == ["all", "all"] and os.environ.get("PRAX_VISION_PAGES") is None
    text = store.get_document(con, scan)["text"]
    assert "(handwritten) a transcribed page" in text and "old OCR" not in text
    reading = store.get_meta(con, scan)["reading"]
    assert reading["state"] == "done" and reading["outcome"] == "upgraded"
    assert reading["stamp"].startswith("vision-pages/")
    assert reading["stamp"].endswith("+vl@127.0.0.1:1+all")
    assert client.get("/work/parse").json()["items"] == []  # done, not handed out again
    assert client.get("/readings").json()["recent"][0]["state"] == "done"
    # a paid vision model is refused, and the refusal is the outcome
    client.post(f"/doc/{scan}/reading", json={"extractor": "vision-pages"})
    work._leases.clear()
    paid = models.ModelSpec(name="sonnet", kind="claude", model="claude-sonnet-5")
    monkeypatch.setattr(models, "resolve", lambda s: paid if s == "vision" else None)
    worker.run_once(_door(client), steps=("parse",), log_=lambda t: None)
    # the refusal is the reading's outcome: it is finished, not waiting
    assert not store.pending_readings(con, scan)
    reading = store.get_meta(con, scan)["reading"]
    assert reading["state"] == "error" and "(paid)" in reading["error"]
    assert client.delete(f"/doc/{scan}/reading").json()["removed"] is True


def test_the_door_asks_for_readings_of_captures_when_the_vision_model_is_free(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An uploaded image, and a captured page whose parse found figures,
    get a reading request from the door itself — only when the vision
    model is a local server; with Claude set, nothing is asked."""
    from prax import models

    con = client.app.state.con
    png = b"\x89PNG\r\n\x1a\n" + bytes(40)
    image = inbox.ingest_upload(con, png, filename="panel.png", mime="image/png")
    # Claude as the vision model: the door leaves the image alone
    paid = models.ModelSpec(name="sonnet", kind="claude", model="claude-sonnet-5")
    monkeypatch.setattr(models, "resolve", lambda s: paid if s == "vision" else None)
    client.get("/work/parse")
    assert not store.pending_readings(con, image.doc_id)
    # a local server: the image is asked for, once, and handed out next
    free = models.ModelSpec(
        name="server-vl", kind="openai", base_url="http://127.0.0.1:1/v1", model="vl"
    )
    monkeypatch.setattr(models, "resolve", lambda s: free if s == "vision" else None)
    client.get("/work/parse")
    reading = pending_one(con, image.doc_id)
    assert reading["extractor"] == "vision" and reading["by"] == "door"
    work._leases.clear()
    items = client.get("/work/parse").json()["items"]
    assert [i["extractor"] for i in items if i["doc_id"] == image.doc_id] == ["vision"]
    # a captured page whose parse found a figure: its figures are asked for
    html = b"<html><body>x</body></html>"
    page = inbox.ingest_upload(con, html, filename="p.html", mime="text/html")
    text = "# A page\n\nProse.\n\n![A plot](figure:" + "ab" * 32 + ")\n"
    result = {"doc_id": page.doc_id, "extractor": "trafilatura/9", "text": text * 20}
    rep = client.post("/work/parse", json={"results": [result]}).json()
    assert rep["applied"] == 1
    reading = pending_one(con, page.doc_id)
    assert reading["extractor"] == "figures" and reading["by"] == "door"
    # a parse without figures asks for nothing
    plain = inbox.ingest_upload(con, html + b" ", filename="q.html", mime="text/html")
    prose = "Prose. " * 60
    result = {"doc_id": plain.doc_id, "extractor": "trafilatura/9", "text": prose}
    client.post("/work/parse", json={"results": [result]})
    assert not store.pending_readings(con, plain.doc_id)


def test_a_backlog_pass_re_reads_what_a_revised_extractor_would_read_differently(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document whose text came from an earlier revision of its extractor
    is stale; scope 'all' hands it out after the captures, the re-read moves
    the stamp (and costs only the parse when the words are the words), and
    the record keeps every artifact's hash."""
    from prax import parsers
    from prax.parsers import queue

    con = client.app.state.con
    doc_id = store.ingest_text(con, "Plain words. " * 40, title="old")["doc_id"]
    # as if read by revision 1 of the plain extractor (the current one is later)
    store.set_text_source(con, doc_id, "plain/1")
    assert parsers.behind("plain/1") is not None
    assert queue.stale(con) == [doc_id]
    assert client.get("/work/parse").json()["items"] == []  # captures only
    work._leases.clear()
    items = client.get("/work/parse", params={"scope": "all"}).json()["items"]
    assert [i["doc_id"] for i in items] == [doc_id]
    assert "extractor" not in items[0]
    work._leases.clear()
    out = worker.run_once(
        _door(client), steps=("parse",), scope="all", log_=lambda t: None
    )
    assert "same" in out["parse"] or "upgraded" in out["parse"]
    meta = store.get_meta(con, doc_id)
    assert meta["text_source"] == parsers.by_name("plain").stamp
    assert queue.stale(con) == []
    # the health panel lists what a nightly pass would still have to do;
    # a repair moves nothing here, since no annotation covers the revision
    store.set_text_source(con, doc_id, "plain/1")
    found = client.get("/heal", params={"check": "stale-parses"}).json()
    ailment = found["ailments"][0]
    assert ailment["count"] == 1 and ailment["examples"][0]["covered"] is None
    assert client.post("/heal", json={"checks": ["stale-parses"]}).json()[
        "stale-parses"
    ] == {"found": 1, "repaired": 0, "left alone": 1}
    # an annotation that is what a revision added moves the stamp itself
    pdf = b"%PDF-1.4 fake"  # figure-refs covers pymupdf4llm r2, the current one
    page = inbox.ingest_upload(con, pdf, filename="p.pdf", mime="application/pdf")
    queue.apply_parse(con, page.doc_id, stamp="pymupdf4llm/1.28.2", text="Prose. " * 60)
    assert queue.stale(con) == [doc_id, page.doc_id]
    refs = "Prose. " * 60 + "\n\n![A plot](figure:" + "ab" * 32 + ")\n"
    queue.apply_parse(
        con, page.doc_id, stamp="figure-refs/1", text=refs, keep_source=True
    )
    assert store.get_meta(con, page.doc_id)["text_source"] == "pymupdf4llm/1.28.2-r2"
    assert queue.stale(con) == [doc_id]
    # ...and where it did so before stamps moved, the history says so and
    # the repair moves the stamp instead of reading the document again
    store.set_text_source(con, page.doc_id, "pymupdf4llm/1.28.2")
    assert queue.covered_by_history(store.get_meta(con, page.doc_id)) == (
        "pymupdf4llm/1.28.2-r2"
    )
    assert client.post("/heal", json={"checks": ["stale-parses"]}).json()[
        "stale-parses"
    ] == {"found": 2, "repaired": 1, "left alone": 1}
    meta = store.get_meta(con, page.doc_id)
    assert meta["text_source"] == "pymupdf4llm/1.28.2-r2"
    assert meta["parse_history"][-1]["outcome"] == "stamped"
    assert queue.stale(con) == [doc_id]
    # a text that did change keeps the earlier artifact addressable
    other = store.ingest_text(con, "First words. " * 40, title="changed")["doc_id"]
    queue.apply_parse(
        con, other, stamp="plain/1-r3", text="Second words. " * 40, force=True
    )
    hist = store.get_meta(con, other)["parse_history"]
    assert hist[-1]["outcome"] == "upgraded" and len(hist[-1]["text_hash"]) == 64
    old_text = store.documents._read_archive(hist[-1]["text_hash"])
    assert old_text.decode("utf-8").startswith("Second words.")


def _blank_pdf() -> bytes:
    fitz = pytest.importorskip("pymupdf")
    doc = fitz.open()
    doc.new_page()
    return doc.tobytes()


def test_a_capture_nothing_could_read_is_tried_once_and_then_left_alone(
    client: TestClient,
) -> None:
    """A scan without a text layer: the first extractor refuses it, the
    fallback finds nothing. The worker reports both attempts, so the door
    sees the chain was run, stops handing the document out (it used to
    come back every cycle, and ten of them filled every batch) and the
    inbox and the health panel say what happened."""
    from prax.parsers import queue

    con = client.app.state.con
    scan = inbox.ingest_upload(con, _blank_pdf(), filename="scan.pdf")
    items = client.get("/work/parse").json()["items"]
    assert [i["doc_id"] for i in items] == [scan.doc_id]
    work._leases.clear()
    out = worker.run_once(_door(client), steps=("parse",), log_=lambda t: None)
    assert "empty" in out["parse"]
    hist = store.get_meta(con, scan.doc_id)["parse_history"]
    assert [h["extractor"].split("/")[0] for h in hist] == ["pymupdf4llm", "pymupdf"]
    assert "error" in hist[0] and hist[1]["outcome"] == "empty"
    assert queue._seen(store.get_meta(con, scan.doc_id), hist[0]["extractor"])
    work._leases.clear()
    assert client.get("/work/parse").json()["items"] == []
    # the inbox says so, and a reading asked for on its page goes out
    row = next(r for r in inbox.recent(con) if r["doc_id"] == scan.doc_id)
    assert row["indexed"] is False and row["tried"] == "empty"
    store.request_reading(con, scan.doc_id, "vision-pages", by="human")
    work._leases.clear()
    items = client.get("/work/parse").json()["items"]
    assert [i["extractor"] for i in items] == ["vision-pages"]
    store.cancel_reading(con, scan.doc_id)
    found = client.get("/heal", params={"check": "unreadable-documents"}).json()
    ailment = found["ailments"][0]
    assert ailment["count"] == 1 and not ailment["repairable"]
    assert ailment["examples"][0]["id"] == scan.doc_id


def test_a_figures_request_may_ask_for_every_image(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The figures reading takes a mode like vision-pages does: the
    captioned figures (the default) or every image the text references;
    the worker runs the extractor with that setting for the one call, and
    a mode an extractor does not take is refused."""
    from prax import models
    from prax.parsers import figures

    con = client.app.state.con
    html = b"<html><body><p>Prose.</p></body></html>"
    page = inbox.ingest_upload(con, html, filename="p.html", mime="text/html")
    text = "# A page\n\nProse.\n\n![A plot](figure:" + "ab" * 32 + ")\n"
    store.index_text(con, page.doc_id, text * 5, text_source="trafilatura/2.2.0-r3")
    r = client.post(
        f"/doc/{page.doc_id}/reading", json={"extractor": "figures", "mode": "scans"}
    )
    assert r.status_code == 400 and "mode" in r.json()["detail"]
    r = client.post(
        f"/doc/{page.doc_id}/reading", json={"extractor": "trafilatura", "mode": "all"}
    )
    assert r.status_code == 400 and "takes no mode" in r.json()["detail"]
    r = client.post(
        f"/doc/{page.doc_id}/reading", json={"extractor": "figures", "mode": "all"}
    )
    assert r.status_code == 200
    item = client.get("/work/parse").json()["items"][0]
    assert item["extractor"] == "figures" and item["mode"] == "all"
    assert item["previous"].startswith("# A page")  # the text the readings go into
    work._leases.clear()
    # the worker: what the extractor sees as its setting during the call
    seen: list[str | None] = []
    spec = models.ModelSpec(
        name="server-vl", kind="openai", base_url="http://127.0.0.1:1/v1", model="vl"
    )
    monkeypatch.setattr(models, "resolve", lambda s: spec if s == "vision" else None)

    def fake_describe(data: bytes, previous: str) -> str:
        seen.append(os.environ.get("PRAX_FIGURES"))
        return previous

    monkeypatch.setattr(figures, "describe", fake_describe)
    worker.run_once(_door(client), steps=("parse",), log_=lambda t: None)
    assert seen == ["all"] and "PRAX_FIGURES" not in os.environ
    reading = store.get_meta(con, page.doc_id)["reading"]
    assert reading["state"] == "done" and reading["error"] is None


def test_the_promote_step_reads_flagged_documents_and_spends_only_when_told(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expensive pass as a work step: the door hands out the flagged
    documents the promote model has not read; the worker refuses the
    step while the model is paid and --spend was not given, and does it
    otherwise; the stamp says the promote model read it."""
    from prax import models

    con = client.app.state.con
    doc_id = client.post(
        "/ingest",
        json={"text": "a paper on wave digital filters " * 40, "title": "P"},
    ).json()["doc_id"]
    assert client.get("/work/promote").json()["items"] == []  # nothing flagged
    store.promote(con, doc_id, by="test", reason="a test")
    monkeypatch.setenv("PRAX_PROMOTE", "stub")
    items = client.get("/work/promote").json()["items"]
    assert [i["doc_id"] for i in items] == [doc_id] and "header" in items[0]
    work._leases.clear()
    # a paid promote model and no --spend: refused before anything is fetched
    paid = models.ModelSpec(name="sonnet", kind="claude", model="claude-sonnet-5")
    real = models.resolve
    monkeypatch.setattr(
        models, "resolve", lambda s: paid if s == "promote" else real(s)
    )
    out = worker.run_once(_door(client), steps=("promote",), log_=lambda t: None)
    assert out["promote"].startswith("skipped") and "--spend" in out["promote"]
    assert client.get("/work/promote").json()["items"], "still waiting, not leased"
    work._leases.clear()
    # the stub is free: the step runs, with or without --spend
    monkeypatch.setattr(models, "resolve", real)
    out = worker.run_once(
        _door(client), steps=("promote",), spend=True, log_=lambda t: None
    )
    assert out["promote"].startswith("1 documents")
    stamp = store.get_meta(con, doc_id)["extraction"]
    assert stamp["extractor"] == "stub" and stamp["run"].startswith("promote-")
    assert store.promoted_documents(con, producer="stub")[0]["done"] is True
    assert client.get("/work/promote").json()["items"] == []
    assert client.get("/promote").json()["promoted"][0]["done"] is True


def test_readings_asked_for_a_selection_at_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A named extractor over a selection — every scan nothing could read,
    every PDF still stamped by an old extractor, a list of ids — is one
    request that places a reading on each; the worker drains them like
    any other. What the extractor does not read is skipped and counted;
    a dry run only counts."""
    con = client.app.state.con
    scan = inbox.ingest_upload(con, _blank_pdf(), filename="scan.pdf")
    work._leases.clear()
    worker.run_once(
        _door(client), steps=("parse",), log_=lambda t: None
    )  # tried, empty
    html = b"<html><body>x</body></html>"
    page = inbox.ingest_upload(con, html, filename="p.html", mime="text/html")
    store.index_text(con, page.doc_id, "Prose. " * 60, text_source="trafilatura/2.2.0")
    paper = store.register(con, b"%PDF-1.4 fake", mime="application/pdf", title="paper")
    store.index_text(
        con, paper["doc_id"], "Words. " * 60, text_source="pymupdf4llm/1.28.2"
    )
    assert store.unreadable_documents(con) == [scan.doc_id]
    # the unreadable ones, OCR: the page is not among them, the scan is
    dry = client.post(
        "/readings/bulk",
        json={"extractor": "pymupdf4llm-ocr", "unreadable": True, "dry_run": True},
    ).json()
    assert dry == {"selected": 1, "requested": 0, "skipped": 0, "dry_run": True}
    assert not store.pending_readings(con, scan.doc_id)
    done = client.post(
        "/readings/bulk", json={"extractor": "pymupdf4llm-ocr", "unreadable": True}
    ).json()
    assert done["requested"] == 1
    assert pending_one(con, scan.doc_id)["extractor"] == "pymupdf4llm-ocr"
    # by stamp prefix, with a mode; a document the extractor cannot read is skipped
    r = client.post(
        "/readings/bulk",
        json={
            "extractor": "figures",
            "mode": "all",
            "ids": [page.doc_id, paper["doc_id"], scan.doc_id],
            "text_source": "pymupdf4llm/",
        },
    ).json()
    assert (
        r["selected"] == 1 and r["requested"] == 1
    )  # only the paper matches the prefix
    assert pending_one(con, paper["doc_id"])["mode"] == "all"
    r = client.post(
        "/readings/bulk",
        json={"extractor": "trafilatura", "ids": [page.doc_id, paper["doc_id"]]},
    ).json()
    assert r == {"selected": 2, "requested": 1, "skipped": 1, "dry_run": False}
    # an unknown extractor is refused; the requests are what the worker sees
    assert (
        client.post(
            "/readings/bulk", json={"extractor": "nope", "unreadable": True}
        ).status_code
        == 400
    )
    waiting = {r["doc_id"] for r in client.get("/readings").json()["requested"]}
    assert waiting == {scan.doc_id, paper["doc_id"], page.doc_id}
    # the health panel offers the two readings for the unreadable ones
    found = client.get("/heal", params={"check": "unreadable-documents"}).json()
    offers = found["ailments"][0]["offers"]
    assert [o["extractor"] for o in offers] == ["pymupdf4llm-ocr", "vision-pages"]


def test_the_typing_step_puts_untyped_review_items_to_the_model_through_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model typing pass as a work step: the door hands out batches of
    untyped review items with their documents' titles and domains, the
    worker asks the typing model, the door applies the answers — an edge
    for a fit, dropped for 'none', open for a misfit. A paid model is
    refused; no model, nothing handed out."""
    from prax import models

    con = client.app.state.con
    doc = client.post(
        "/ingest",
        json={"text": "a paper at a symposium " * 30, "title": "Cuckoo Hashing"},
    ).json()["doc_id"]
    for dst, rel in (
        ("European Symposium on Algorithms", "published_in"),
        ("nothing", "about"),
        ("a claim", "cites"),
    ):
        store.queue_review(
            con,
            src="Cuckoo Hashing",
            src_type=None,
            rel=rel,
            dst=dst,
            dst_type=None,
            source_doc=doc,
            reason="untyped",
            evidence="…",
        )
    assert store.count_review(con) == 3
    # no typing model: nothing to hand out, the worker says nothing
    monkeypatch.setattr(models, "resolve", lambda s: None)
    assert client.get("/work/typing").json()["items"] == []
    out = worker.run_once(_door(client), steps=("typing",), log_=lambda t: None)
    assert "typing" not in out
    # a paid model: refused before anything is fetched
    paid = models.ModelSpec(name="sonnet", kind="claude", model="claude-sonnet-5")
    monkeypatch.setattr(models, "resolve", lambda s: paid if s == "typing" else None)
    out = worker.run_once(_door(client), steps=("typing",), log_=lambda t: None)
    assert out["typing"].startswith("skipped") and "paid" in out["typing"]
    # a local server: one batch of three, answered
    local = models.ModelSpec(
        name="server", kind="openai", base_url="http://127.0.0.1:1/v1", model="m"
    )
    monkeypatch.setattr(models, "resolve", lambda s: local if s == "typing" else None)
    items = client.get("/work/typing").json()["items"]
    assert len(items) == 1 and len(items[0]["items"]) == 3
    assert items[0]["docs"][str(doc)][0] == "Cuckoo Hashing"
    work._leases.clear()
    prompts: list[str] = []

    class FakeRuntime:
        name = "m"

        def chat(self, system, user, **kw):
            prompts.append(user)
            return "1: paper, venue\n2: paper, none\n3: venue, author\n", {
                "input_tokens": 5
            }

    monkeypatch.setattr(models, "runtime", lambda s: FakeRuntime())
    out = worker.run_once(_door(client), steps=("typing",), log_=lambda t: None)
    assert "1 requests" in out["typing"] and "linked 1" in out["typing"]
    assert "misfit 1" in out["typing"]
    assert (
        "European Symposium" in prompts[0] and "Document: Cuckoo Hashing" in prompts[0]
    )
    # the misfit (a venue cites an author) stays open, typed as the model
    # said: the rules' and a later ontology's business, not asked again
    left = store.list_review(con, limit=10)
    assert [(it["src_type"], it["dst_type"]) for it in left] == [("venue", "author")]
    edges = store.traverse(con, "European Symposium on Algorithms", hops=1)
    assert (
        edges
        and edges[0]["producer"] == "typing:m@127.0.0.1:1"  # the runtime's name
        and edges[0]["confidence"] == "INFERRED"
    )
    assert client.get("/work/typing").json()["items"] == []  # nothing untyped left


def test_a_reading_whose_server_is_loading_waits_instead_of_failing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model server answers 503 for a minute or three after a start (or
    marker's server is not up): the reading is not the document's fault,
    so the worker posts nothing, the lease runs out and the request is
    handed out again; the outcome the person sees is still 'requested'."""
    from prax import models, parsers

    con = client.app.state.con
    doc = store.ingest_text(
        con, "# Maths\n\nSee\n\n$$E = mc^2 \\quad (1)$$\n\nwhich holds. " * 3, title="M"
    )["doc_id"]
    r = client.post(f"/doc/{doc}/reading", json={"extractor": "formulas"})
    assert r.status_code == 200
    spec = models.ModelSpec(
        name="srv", kind="openai", base_url="http://127.0.0.1:1/v1", model="q"
    )
    monkeypatch.setattr(models, "resolve", lambda s: spec if s == "formulas" else None)

    class Loading:
        name = "q@127.0.0.1:1"

        def chat(self, system, user, **kw):
            raise models.ServerNotReady("HTTP 503: Loading model")

    monkeypatch.setattr(models, "runtime", lambda s: Loading())
    said: list[str] = []
    out = worker.run_once(_door(client), steps=("parse",), log_=said.append)
    assert out.get("parse", "0 parsed").startswith("0 parsed")
    assert any("not yet" in line and "Loading model" in line for line in said)
    reading = pending_one(con, doc)
    assert reading["state"] == "requested"  # still waiting, no error recorded
    assert "parse_history" not in store.get_meta(con, doc)
    # and deferred: leased well past a batch, so the next hand-outs hold
    # other work — the follow-ups of the first papers marker read filled
    # every batch of ten with "not yet" and starved the 228 requests behind
    held = work._leases[("parse", doc)]
    assert held[1] > time.monotonic() + work.DEFER_SECONDS - 60
    # a text document is not marker's type, so use a fake PDF-typed one
    pdf = store.register(con, b"%PDF-1.4 fake", mime="application/pdf", title="p")[
        "doc_id"
    ]
    store.index_text(con, pdf, "old text " * 40, text_source="pymupdf4llm/1")
    assert (
        client.post(f"/doc/{pdf}/reading", json={"extractor": "marker"}).status_code
        == 200
    )
    handed = client.get("/work/parse").json()["items"]
    assert [i["doc_id"] for i in handed] == [pdf]  # the deferred one waits
    # once the lease runs out it goes out again; marker's absent server is the same
    work._leases.clear()
    monkeypatch.setenv("PRAX_MARKER_URL", "http://127.0.0.1:9")
    said.clear()
    worker.run_once(_door(client), steps=("parse",), log_=said.append)
    assert any("marker's server" in line and "not yet" in line for line in said)
    assert pending_one(con, pdf)["state"] == "requested"
    assert isinstance(parsers.NotYet("x"), parsers.ExtractionError)


def test_the_hand_out_sees_the_whole_reading_queue_oldest_first(
    client: TestClient,
) -> None:
    """The status view lists the newest fifty requests; the hand-out used
    the same list, so sixty older marker requests behind ten newer
    follow-ups were never handed out once those ten were leased. The
    hand-out reads the whole queue, oldest first."""
    con = client.app.state.con
    older = []
    for i in range(60):
        pdf = store.register(
            con, b"%PDF-1.4 " + str(i).encode(), mime="application/pdf", title=f"p{i}"
        )["doc_id"]
        store.index_text(con, pdf, f"old text {i} " * 40, text_source="pymupdf4llm/1")
        assert (
            client.post(f"/doc/{pdf}/reading", json={"extractor": "marker"}).status_code
            == 200
        )
        older.append(pdf)
    newer = []
    for i in range(10):
        maths = f"# M{i}\n\n$$x_{i} = 1 \\quad (1)$$\n\ntext " * 3
        doc = store.ingest_text(con, maths)["doc_id"]
        assert (
            client.post(
                f"/doc/{doc}/reading", json={"extractor": "formulas"}
            ).status_code
            == 200
        )
        newer.append(doc)
    # the newest ten are leased (say deferred: their server is down)
    work._lease("parse", newer, "w", seconds=work.DEFER_SECONDS)
    handed = [i["doc_id"] for i in client.get("/work/parse").json()["items"]]
    assert handed == older[:10]  # oldest first, none of the leased ones
    handed = [i["doc_id"] for i in client.get("/work/parse").json()["items"]]
    assert handed == older[10:20]
    # the status view keeps its newest-first window
    listed = client.get("/readings").json()["requested"]
    assert len(listed) == 50 and set(newer) <= {r["doc_id"] for r in listed}


def test_an_extraction_whose_server_is_down_is_not_a_failure_of_the_document(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pass that ran while llama-server was paused recorded 'connection
    refused' as the document's extraction error, which kept it out of the
    selection under this ontology. The worker posts nothing for a server
    that is not ready; and the errors already recorded that way are
    forgotten by the extraction-failed repair — the others kept."""
    from prax import extraction, models

    con = client.app.state.con
    a = client.post(
        "/ingest",
        json={"text": "reverb design by feedback delay networks " * 30, "title": "A"},
    ).json()["doc_id"]

    class Down(extraction.StubExtractor):
        def extract(self, doc):  # type: ignore[override]
            raise models.ServerNotReady("http://127.0.0.1:1/v1: not answering")

    monkeypatch.setattr(extraction, "current", lambda step="extract": Down())
    said: list[str] = []
    out = worker.run_once(
        _door(client), steps=("extract",), scope="all", log_=said.append
    )
    assert any("not yet" in line for line in said)
    assert "extraction_error" not in store.get_meta(con, a)
    assert "extraction" not in store.get_meta(con, a)
    assert out.get("extract", "").startswith("0 ") or "extract" not in out
    assert work._leases[("extract", a)][1] > time.monotonic() + work.DEFER_SECONDS - 60
    # what an earlier pass wrote is the ailment's business
    extraction.note_failure(con, a, "URLError: actively refused it", extractor="x")
    b = client.post(
        "/ingest", json={"text": "a second paper " * 60, "title": "B"}
    ).json()["doc_id"]
    extraction.note_failure(
        con, b, "prompt of 40,000 tokens exceeds the slot", extractor="x"
    )
    ailment = next(x for x in store.AILMENTS if x.name == "extraction-failed")
    found = ailment.find(con)
    assert {f["id"] for f in found} == {a, b}
    assert ailment.repair is not None and ailment.repair(con, found) == 1
    assert "extraction_error" not in store.get_meta(con, a)
    assert store.get_meta(con, b)["extraction_error"]["error"].startswith("prompt")


def test_who_runs_says_why_a_queue_sits_still(
    con: store.sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why a flagged document sits at "pending" with nothing broken: no
    worker asks for the promote step unless the run names it, and its
    model costs money. The door says both, and the command that moves
    it. A watched step only waits for a worker to turn up."""
    for var in ("PRAX_PROMOTE", "PRAX_PROMOTE_MODEL", "PRAX_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    models.reset()
    work._asked.clear()
    it = work.who_runs(con, "promote")
    assert it["model"] == "claude-sonnet-5" and it["paid"] is True
    assert it["watched"] is False and it["asked"] is None
    assert "unless the run names it" in it["why"] and "costs money" in it["why"]
    assert it["how"] == "prax work --steps promote --spend"
    parse = work.who_runs(con, "parse")
    assert parse["watched"] is True and parse["paid"] is False
    assert parse["why"] == "no worker has asked this door for parse work"
    work.hand_out(con, "parse", limit=1)
    after = work.who_runs(con, "parse")
    assert after["asked"] and after["why"] == ""
    assert work.asked_at("parse") == after["asked"]


def test_the_steps_are_named_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One list of steps for the door, the worker and the command line."""
    from prax_cli.main import build_parser

    assert work.STEPS is steps.STEPS and worker.STEPS is steps.STEPS
    assert set(steps.WATCHED_STEPS) < set(steps.STEPS)
    assert steps.NAMED_ONLY == ("vocabulary", "promote", "typing", "adjudicate")
    a = build_parser().parse_args(["work"])
    assert tuple(a.steps.split(",")) == steps.WATCHED_STEPS
    assert all(hasattr(a, f"no_{s}") for s in steps.STEPS)


def test_no_reading_spends_without_being_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every reading that runs a step's model is refused while the model
    costs money and --spend was not given — by the step it runs, never by
    what the reading is called. The guard used to key on the name, so
    `figures` and `formulas` walked past it and read 30 figures with
    claude-sonnet-5 on 2026-09-23.
    """
    from prax import models, worker

    monkeypatch.setenv("PRAX_VISION", "claude-sonnet-5")
    monkeypatch.setenv("PRAX_FORMULAS", "claude-sonnet-5")
    models.reset()
    for name in ("vision", "vision-pages", "figures", "formulas"):
        exts, refused = worker._requested({"extractor": name, "mime": "image/png"})
        assert exts == [], f"{name} ran a paid model unasked"
        assert refused and "(paid)" in refused and "--spend" in refused
    # with --spend the same request goes through
    exts, refused = worker._requested(
        {"extractor": "figures", "mime": "application/pdf"}, spend=True
    )
    assert refused is None and exts
    # and a local model needs no asking at all
    monkeypatch.setenv("PRAX_VISION", "none")
    models.reset()
    exts, refused = worker._requested({"extractor": "figures", "mime": "image/png"})
    assert refused is None or "(paid)" not in refused


def test_every_reading_that_runs_a_model_is_named(monkeypatch) -> None:
    """A reading missing from READING_STEPS is a reading nothing guards,
    which is how this got out. The readings that run a step's model are
    the vision ones, the formulas and the polish."""
    from prax import steps as steps_mod
    from prax import store

    assert set(steps_mod.READING_STEPS) <= set(store.READINGS)
    assert set(steps_mod.READING_STEPS) == {
        "vision",
        "vision-pages",
        "figures",
        "formulas",
        "polish",
    }


def test_a_figures_request_with_nothing_to_read_is_dropped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half the live queue (1,818 of 3,477 on 2026-09-23) asked the
    vision model to read documents whose every figure it had read
    already: a fetch, a parse and a round trip each, all coming back
    "same". The door drops such a request instead of handing it out.
    """
    from prax import models, store

    con = client.app.state.con
    monkeypatch.setenv("PRAX_VISION", "claude-sonnet-5")
    models.reset()
    who = models.resolve("vision").runtime_name
    html = b"<html><body><p>Prose.</p></body></html>"
    page = inbox.ingest_upload(con, html, filename="p.html", mime="text/html")
    ref = "ab" * 32
    store.index_text(
        con,
        page.doc_id,
        "# A page\n\nProse about it.\n\n![A plot](figure:" + ref + ")\n",
        text_source="trafilatura/2.2.0-r3",
    )
    assert store.figures_to_read(con, page.doc_id, model=who) == 1

    # the reading is handed out while the figure is unread
    client.post(f"/doc/{page.doc_id}/reading", json={"extractor": "figures"})
    assert [it["doc_id"] for it in client.get("/work/parse").json()["items"]] == [
        page.doc_id
    ]
    work._leases.clear()

    # once that model has read it, the same request has nothing to do
    chunk = next(
        c for c in store.list_chunks(con, page.doc_id) if c["kind"] == "figure"
    )
    data = dict(chunk["data"] or {})
    data["readings"] = [{"model": who, "text": "a plot of two lines"}]
    con.execute(
        "UPDATE chunks SET data = ? WHERE id = ?",
        (json.dumps(data), chunk["chunk_id"]),
    )
    con.commit()
    assert store.figures_to_read(con, page.doc_id, model=who) == 0
    client.post(f"/doc/{page.doc_id}/reading", json={"extractor": "figures"})
    assert client.get("/work/parse").json()["items"] == []
    # and the request is gone, not left waiting for ever
    assert store.reading_requests(con) == []

    # "every image" is a different question: an uncaptioned figure is
    # left alone by the captioned pass and read by this one
    data["caption"] = "Figure on page 3"
    data["readings"] = []
    con.execute(
        "UPDATE chunks SET data = ? WHERE id = ?",
        (json.dumps(data), chunk["chunk_id"]),
    )
    con.commit()
    assert store.figures_to_read(con, page.doc_id, model=who) == 0
    assert store.figures_to_read(con, page.doc_id, model=who, every=True) == 1

    # and a caption with no picture behind it is nothing to read at all:
    # a PDF whose images no extractor could pull out leaves those, and
    # 55,607 of the live store's 108,781 figure chunks were counted as a
    # backlog because of it (2026-09-24)
    con.execute(
        "UPDATE chunks SET data = ? WHERE id = ?",
        (json.dumps({"caption": "Figure 1: the signal path"}), chunk["chunk_id"]),
    )
    con.commit()
    assert store.figures_to_read(con, page.doc_id, model=who) == 0
    assert store.figures_to_read(con, page.doc_id, model=who, every=True) == 0


def test_a_document_waits_for_several_readings(con: store.sqlite3.Connection) -> None:
    """A reading queues beside the others instead of replacing them.

    Until migration 21 the request was one field on the document, so a
    figures request wiped the formulas the door had queued behind a
    marker read, and a bulk re-read wiped every pending reading it
    touched. The plan carried it from 2026-09-20 as "worth a queue some
    day"; the crop pass, which every PDF wants alongside its other
    readings, made it due.
    """
    doc = store.ingest_text(con, "a paper about reverb " * 40, title="P")["doc_id"]
    store.request_reading(con, doc, "vision-pages", mode="scans", by="door")
    store.request_reading(con, doc, "formulas", by="human")
    store.request_reading(con, doc, "vision-pages", mode="scans")  # again: nothing new
    waiting = store.pending_readings(con, doc)
    assert [r["extractor"] for r in waiting] == ["vision-pages", "formulas"]
    assert waiting[0]["by"] == "door"  # the first asking stands
    assert store.count_reading_requests(con) == 2

    # the hand-out serves them oldest first, one document at a time
    first = work.hand_out(con, "parse", limit=10, scope="all")["items"]
    assert [it["extractor"] for it in first] == ["vision-pages"]
    work._leases.clear()

    # except that a reading running no model goes ahead of one that does,
    # however long the queue: it costs seconds and it makes the pictures
    # the expensive readings then read
    other = store.register(con, b"%PDF-1.4 x", mime="application/pdf")["doc_id"]
    store.index_text(
        con,
        other,
        "# A paper\n\nProse.\n\nFigure 1: A plot of two lines.\n",
        text_source="pymupdf4llm/1.28.2-r2",
    )
    store.request_reading(con, other, "figure-crops")
    out = work.hand_out(con, "parse", limit=1, scope="all")["items"]
    assert [it["doc_id"] for it in out] == [other]
    work._leases.clear()

    # finishing one leaves the other waiting, and the document remembers
    # the one that finished
    store.finish_reading(
        con,
        doc,
        outcome="upgraded",
        stamp="vision-pages/1+local",
        extractor="vision-pages",
    )
    assert [r["extractor"] for r in store.pending_readings(con, doc)] == ["formulas"]
    last = store.get_meta(con, doc)["reading"]
    assert last["extractor"] == "vision-pages" and last["state"] == "done"

    # and one can be withdrawn by name
    assert store.cancel_reading(con, doc, extractor="formulas") is True
    assert store.pending_readings(con, doc) == []
    assert store.cancel_reading(con, doc, extractor="formulas") is False


def test_the_door_asks_for_the_crops_of_a_pdf_itself(
    con: store.sqlite3.Connection,
) -> None:
    """A caption with no picture behind it is a figure drawn in vector
    paths. Rendering it runs no model, so the door asks for it after any
    PDF parse, beside whatever else is waiting."""
    from prax import pipeline

    doc = store.register(con, b"%PDF-1.4 fake", mime="application/pdf")["doc_id"]
    store.index_text(
        con,
        doc,
        "# A paper\n\nProse about it.\n\nFigure 1: A plot of two lines.\n",
        text_source="pymupdf4llm/1.28.2-r2",
    )
    store.request_reading(con, doc, "formulas", by="human")
    assert (
        pipeline.follow_ups(con, doc, stamp="pymupdf4llm/1.28.2-r2", action="upgraded")
        == "figure-crops"
    )
    # queued beside the formulas, not over it
    assert [r["extractor"] for r in store.pending_readings(con, doc)] == [
        "formulas",
        "figure-crops",
    ]
    # and not twice
    pipeline.follow_ups(con, doc, stamp="pymupdf4llm/1.28.2-r2", action="upgraded")
    assert len(store.pending_readings(con, doc)) == 2
    # a document whose captions all have pictures is left alone
    other = store.register(con, b"%PDF-1.4 other", mime="application/pdf")["doc_id"]
    store.index_text(
        con,
        other,
        "# Another\n\n![Figure 1: A plot.](figure:" + "ab" * 32 + ")\n",
        text_source="pymupdf4llm/1.28.2-r2",
    )
    assert (
        pipeline.follow_ups(
            con, other, stamp="pymupdf4llm/1.28.2-r2", action="upgraded"
        )
        != "figure-crops"
    )
