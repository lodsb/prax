"""The work protocol end to end: the door hands out, a worker that only
speaks HTTP does the work, the door takes the results in and stays the
only writer."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import embeddings, inbox, store, work, worker

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
    assert "reading" not in store.get_meta(con, image.doc_id)
    # a local server: the image is asked for, once, and handed out next
    free = models.ModelSpec(
        name="server-vl", kind="openai", base_url="http://127.0.0.1:1/v1", model="vl"
    )
    monkeypatch.setattr(models, "resolve", lambda s: free if s == "vision" else None)
    client.get("/work/parse")
    reading = store.get_meta(con, image.doc_id)["reading"]
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
    reading = store.get_meta(con, page.doc_id)["reading"]
    assert reading["extractor"] == "figures" and reading["by"] == "door"
    # a parse without figures asks for nothing
    plain = inbox.ingest_upload(con, html + b" ", filename="q.html", mime="text/html")
    prose = "Prose. " * 60
    result = {"doc_id": plain.doc_id, "extractor": "trafilatura/9", "text": prose}
    client.post("/work/parse", json={"results": [result]})
    assert "reading" not in store.get_meta(con, plain.doc_id)


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
    assert "reading" not in store.get_meta(con, scan.doc_id)
    done = client.post(
        "/readings/bulk", json={"extractor": "pymupdf4llm-ocr", "unreadable": True}
    ).json()
    assert done["requested"] == 1
    assert store.get_meta(con, scan.doc_id)["reading"]["extractor"] == "pymupdf4llm-ocr"
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
    assert store.get_meta(con, paper["doc_id"])["reading"]["mode"] == "all"
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
    reading = store.get_meta(con, doc)["reading"]
    assert reading["state"] == "requested"  # still waiting, no error recorded
    assert "parse_history" not in store.get_meta(con, doc)
    # once the lease runs out it goes out again; marker's absent server is the same
    work._leases.clear()
    client.post(f"/doc/{doc}/reading", json={"extractor": "marker"})
    monkeypatch.setenv("PRAX_MARKER_URL", "http://127.0.0.1:9")
    # a text document is not marker's type, so use a fake PDF-typed one
    pdf = store.register(con, b"%PDF-1.4 fake", mime="application/pdf", title="p")[
        "doc_id"
    ]
    store.index_text(con, pdf, "old text " * 40, text_source="pymupdf4llm/1")
    assert (
        client.post(f"/doc/{pdf}/reading", json={"extractor": "marker"}).status_code
        == 200
    )
    work._leases.clear()
    said.clear()
    worker.run_once(_door(client), steps=("parse",), log_=said.append)
    assert any("marker's server" in line and "not yet" in line for line in said)
    assert store.get_meta(con, pdf)["reading"]["state"] == "requested"
    assert isinstance(parsers.NotYet("x"), parsers.ExtractionError)
