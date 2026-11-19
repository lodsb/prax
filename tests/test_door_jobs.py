"""The maintenance passes the one-off scripts used to be, through the door:
entity resolution as a plan and a job, the citation import as a job, the
review queue's rule passes and a rechunk inside the maintenance pass."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.store import Edge as E
from tests.conftest import wait_job


@pytest.fixture()
def door(data_dir: object) -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_resolution_is_a_plan_and_then_a_job(door: TestClient) -> None:
    con = door.app.state.con
    store.link(con, E("Paper A", "paper", "authored_by", "Julius O. Smith", "author"))
    store.link(con, E("Paper B", "paper", "authored_by", "J. O. Smith", "author"))
    store.link(con, E("Paper C", "paper", "about", "wave digital filters", "concept"))
    store.link(con, E("Paper D", "paper", "about", "Wave Digital Filter", "concept"))
    plan = door.post("/graph/resolve", json={"embed": False}).json()
    assert plan["applied"] is False and plan["plan"]["sure"]["count"] == 2
    pairs = {(c["drop"], c["keep"]) for c in plan["plan"]["sure"]["examples"]}
    assert ("J. O. Smith", "Julius O. Smith") in pairs
    assert (
        con.execute(
            "SELECT count(*) FROM entities WHERE canonical_id IS NOT NULL"
        ).fetchone()[0]
        == 0
    )
    applied = door.post("/graph/resolve", json={"embed": False, "apply": True}).json()
    assert applied["applied"] is True
    row = wait_job(door, applied["job"])
    assert row["status"] == "done" and row["note"].startswith("done: merged 2 sure")
    assert [e["name"] for e in store.find_entities(con, "smith")] == ["Julius O. Smith"]
    # idempotent: nothing left to merge
    assert (
        door.post("/graph/resolve", json={"embed": False}).json()["plan"]["sure"][
            "count"
        ]
        == 0
    )


def test_the_review_pass_and_a_rechunk_run_inside_maintenance(door: TestClient) -> None:
    con = door.app.state.con
    doc = store.ingest_text(con, "a paper on reverberation " * 40, title="R")["doc_id"]
    # a typed item the current ontology accepts: replay links it
    store.queue_review(
        con,
        src="R",
        src_type="paper",
        rel="about",
        dst="reverberation",
        dst_type="concept",
        source_doc=doc,
        reason="test",
    )
    assert store.count_review(con) == 1
    started = door.post("/maintain", json={"only": ["review", "rechunk"]}).json()
    assert started["passes"] == ["review", "rechunk"]
    row = wait_job(door, started["job"])
    assert row["status"] == "done", row
    assert store.count_review(con) == 0
    assert any(
        e["rel"] == "about" for e in store.traverse(con, "reverberation", hops=1)
    )
    # rechunk is not a nightly pass: only when named
    assert "rechunk" not in store.PASSES and "rechunk" in store.ON_REQUEST
    assert door.post("/maintain", json={"only": ["nope"]}).status_code == 400


def test_the_citation_import_is_a_job(
    door: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax.importers import citations

    con = door.app.state.con
    doc = store.ingest_text(con, "a paper with a doi " * 30, title="With DOI")["doc_id"]
    store.set_meta(con, doc, {**store.get_meta(con, doc), "doi": "10.1000/x"})
    plain = store.ingest_text(con, "a paper without one " * 30, title="No DOI")[
        "doc_id"
    ]
    dry = door.post(
        "/import/citations", json={"source": "crossref", "dry_run": True}
    ).json()
    assert dry == {"selected": 1, "source": "crossref", "dry_run": True}
    assert door.post("/import/citations", json={"source": "nope"}).status_code == 400

    calls: list[list[int]] = []

    def fake_import(
        con: sqlite3.Connection,
        ids: list[int],
        *,
        source: Any,
        resolve_titles: bool,
        report: Any,
    ) -> None:
        calls.append(list(ids))
        report.documents += len(ids)
        report.linked += 3
        for i in ids:
            store.set_meta(
                con, i, {**store.get_meta(con, i), "citations": {"fetched_at": "now"}}
            )

    monkeypatch.setattr(citations, "import_citations", fake_import)
    monkeypatch.setattr(citations, "source_named", lambda name, fetch: object())
    started = door.post("/import/citations", json={"source": "crossref"}).json()
    row = wait_job(door, started["job"])
    assert (
        row["status"] == "done"
        and "1 resolved" in row["note"]
        and "3 cites edges" in row["note"]
    )
    assert calls == [[doc]]
    # fetched: no longer a candidate; the DOI-less one only with resolve_titles
    assert (
        door.post("/import/citations", json={"dry_run": True}).json()["selected"] == 0
    )
    assert (
        door.post(
            "/import/citations", json={"dry_run": True, "resolve_titles": True}
        ).json()["selected"]
        == 1
    )
    assert store.get_meta(con, plain).get("citations") is None


ARXIV_RECORD = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord><record>
<metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>{id}</id>
<title>A paper</title>{licence}</arXiv></metadata></record></GetRecord></OAI-PMH>"""
ARXIV_MISSING = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
<error code="idDoesNotExist">no such id</error></OAI-PMH>"""


def test_arxiv_names_the_licence_of_its_copy(
    door: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax.importers import arxiv

    lic = "<license>http://creativecommons.org/licenses/by/4.0/</license>"
    found, got = arxiv.licence_of(
        ARXIV_RECORD.format(id="2501.00001", licence=lic).encode()
    )
    assert found and got is not None and got.id == "CC-BY-4.0"
    assert arxiv.licence_of(ARXIV_RECORD.format(id="x", licence="").encode()) == (
        True,
        None,
    )
    assert arxiv.licence_of(ARXIV_MISSING.encode()) == (False, None)
    assert "identifier=oai:arXiv.org:hep-th/9901001" in arxiv.record_url(
        "hep-th/9901001"
    )

    con = door.app.state.con

    def paper(name: str, ident: str) -> int:
        doc = store.ingest_text(con, f"{name} words " * 30, title=name)["doc_id"]
        store.set_meta(con, doc, {**store.get_meta(con, doc), "arxiv": ident})
        return int(doc)

    open_ = paper("Open", "2501.00001v2")
    plain = paper("Plain", "2501.00002")
    gone = paper("Gone", "2501.00003")
    printed = paper("Printed", "2501.00004")
    store.set_licence(con, printed, "CC-BY-SA-4.0", by="printed")
    store.set_licence(con, plain, "CC-BY-NC-4.0", by="crossref")
    dry = door.post("/import/citations", json={"source": "arxiv", "dry_run": True})
    assert dry.json() == {"selected": 3, "source": "arxiv", "dry_run": True}

    asked: list[str] = []
    records = {
        "2501.00001": ARXIV_RECORD.format(id="2501.00001", licence=lic),
        "2501.00002": ARXIV_RECORD.format(
            id="2501.00002",
            licence="<license>http://arxiv.org/licenses/nonexclusive-distrib/1.0/</license>",
        ),
        "2501.00003": ARXIV_MISSING,
    }

    class Fake:
        calls = 0

        def __init__(self, **_: Any) -> None:
            pass

        def __call__(self, url: str) -> bytes:
            ident = url.rsplit("oai:arXiv.org:", 1)[1].split("&", 1)[0]
            asked.append(ident)
            return records[ident].encode()

    monkeypatch.setattr(arxiv, "HttpFetcher", Fake)
    started = door.post("/import/citations", json={"source": "arxiv"}).json()
    row = wait_job(door, started["job"])
    assert row["status"] == "done" and "2 with a licence" in row["note"], row
    assert sorted(asked) == ["2501.00001", "2501.00002", "2501.00003"]
    assert store.get_meta(con, open_)["licence"]["by"] == "arxiv"
    # arXiv's record speaks of its copy: over Crossref's, never over the text's
    assert store.get_meta(con, plain)["licence"]["id"] == arxiv.licences.ARXIV
    assert store.get_meta(con, printed)["licence"]["by"] == "printed"
    assert store.get_meta(con, gone)["arxiv_record"]["found"] is False
    assert "licence" not in store.get_meta(con, gone)
    # read once: none is due again, unless refreshed
    again = door.post("/import/citations", json={"source": "arxiv", "dry_run": True})
    assert again.json()["selected"] == 0
