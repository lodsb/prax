"""AL step 9: the client's second page (doc 13470 in the owner's library,
checked from a laptop on 2026-10-04; items named as there)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import work


def test_a_worker_not_yet_back_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    """G4: after a door restart the client saw ``alive: false`` and nothing
    more; the door forgets its workers when it restarts, and one in a long
    batch asks for nothing until it is through."""
    monkeypatch.setattr(work, "_asked", {})
    monkeypatch.setattr(work, "leases", dict)

    def supervised(state: str) -> Any:
        return lambda: {"state": state, "pid": 7, "since": "2026-10-05T10:53:09Z"}

    monkeypatch.setattr(work, "_worker_process", supervised("up"))
    got = work.worker_state()
    assert got["alive"] is False and got["process"]["pid"] == 7
    assert "restarted" in got["note"] and "reattaches" in got["note"]
    monkeypatch.setattr(work, "_worker_process", supervised("stopped"))
    assert "prax up --start worker" in work.worker_state()["note"]
    monkeypatch.setattr(work, "_worker_process", lambda: None)
    assert "prax work --watch" in work.worker_state()["note"]


ADMIN = "admin-secret"
REMOTE = "github.com/someone/synth"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_TOKEN", ADMIN)
    monkeypatch.setenv("PRAX_EMBED", "hash")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def _body(text: str, **kw: Any) -> dict[str, Any]:
    raw = text.encode("utf-8")
    f = {
        "path": "notes.md",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "modified": "2026-10-03T12:00:00Z",
        "text": text,
    }
    return {"remote": REMOTE, "prefix": "", "root_name": "synth", "files": [f], **kw}


def test_a_named_token_plans_any_project_and_syncs_a_registered_one(
    client: TestClient,
) -> None:
    """N1: the client's named token could not sync at all, so it fell back
    to a scratch script and the old keys."""
    admin = _bearer(ADMIN)
    me = _bearer(
        client.post("/tokens", json={"name": "laptop"}, headers=admin).json()["secret"]
    )
    notes = "# Notes\n\nThe voice board talks I2C.\n"
    plan = client.post("/projects/sync", json=_body(notes), headers=me)
    assert plan.status_code == 200 and plan.json()["counts"]["add"] == 1
    refused = client.post(
        "/projects/sync", json=_body(notes, dry_run=False), headers=me
    )
    assert refused.status_code == 403 and "registered" in refused.json()["detail"]
    # the administrator registers it; the token then keeps it in step
    got = client.post(
        "/projects/sync",
        json=_body(notes, dry_run=False, domains=["workshop"]),
        headers=admin,
    )
    assert got.status_code == 200
    again = _body(notes + "And SPI.\n", dry_run=False)
    synced = client.post("/projects/sync", json=again, headers=me)
    assert synced.status_code == 200 and synced.json()["counts"]["refresh"] == 1
    # but not its settings
    moved = _body(notes, dry_run=False, domains=["kitchen"])
    assert client.post("/projects/sync", json=moved, headers=me).status_code == 403
    # a project whose notes the token does not see is as if absent
    doc = synced.json()["plan"][0]["doc_id"]
    client.put(f"/doc/{doc}/sensitivity", json={"state": "personal"}, headers=admin)
    hidden = client.post("/projects/sync", json=_body(notes), headers=me)
    assert hidden.status_code == 404


def test_a_citation_is_taken_where_the_passage_answers(con: Any) -> None:
    """N2: the citation words were "permission to make digital", the ACM
    notice on a paper's first page, and elsewhere "in order to test"."""
    from urllib.parse import unquote_plus

    from prax import store

    first = (
        "Permission to make digital or hard copies of all or part of this work"
        " for personal or classroom use is granted without fee provided that"
        " copies are not made or distributed for profit. "
        "We detect note onsets with adaptive whitening of the spectral flux,"
        " which keeps quiet passages from vanishing under loud ones."
    )
    other = "A second section about tempo estimation and beat tracking. " * 3
    doc = int(store.ingest_text(con, first + "\n\n" + other, title="Onsets")["doc_id"])
    chunk = int(
        con.execute(
            "SELECT id FROM chunks WHERE doc_id = ? ORDER BY id LIMIT 1", (doc,)
        ).fetchone()[0]
    )
    link = store.cite_link(con, doc, chunk, near="adaptive whitening")
    words = unquote_plus(link.split("find=", 1)[1])
    assert "permission" not in words and "copies" not in words
    assert "whitening" in words or "adaptive" in words


YIN = (
    "[1] A. de Cheveigné and H. Kawahara, “YIN, a fundamental frequency estimator"
    " for speech and music,” J. Acoust. Soc. Am., vol. 111, no. 4,"
    " pp. 1917–1930, 2002."
)
ADAM = (
    "[2] D. P. Kingma and J. Ba, “Adam: a method for stochastic optimization,”"
    " in Proc. ICLR, 2015."
)
FULL_NAMES = (
    "Florian Eyben, Sebastian Böck, Björn Schuller, and Alex Graves. 2010."
    " Universal onset detection with bidirectional long short-term memory"
    " neural networks. In Proc. ISMIR."
)


def test_an_entry_reads_its_year_and_its_surnames() -> None:
    """N4: YIN was dated 1917 (its pages), and an author run of whole names
    gave every first name as a surname."""
    from prax.text import references

    assert references.parse(YIN).year == 2002
    got = references.parse(FULL_NAMES)
    assert got.surnames == ["Eyben", "Böck", "Schuller", "Graves"]
    assert got.year == 2010


def test_what_every_field_cites_comes_after_the_topics_own(con: Any) -> None:
    """N4: generic references ranked beside the topic's papers, and most
    works came back with no link."""
    from prax import store

    def paper(title: str, *refs: str) -> int:
        text = f"# {title}\n\n" + "Onset detection body text. " * 20
        text += "\n\n## References\n\n" + "\n\n".join(refs) + "\n"
        return int(store.ingest_text(con, text, title=title)["doc_id"])

    a = paper("Onsets one", YIN, ADAM)
    b = paper("Onsets two", ADAM, YIN)
    paper("A vision paper", ADAM)  # the rest of the library cites Adam too
    got = store.cited_but_missing(con, [a, b])
    titles = [w["title"] for w in got["missing"]]
    assert titles[0].startswith("YIN") and titles[1].startswith("Adam")
    yin, adam = got["missing"]
    assert (yin["cited_in_library"], adam["cited_in_library"]) == (2, 3)
    assert yin["year"] == 2002 and "search" in yin["links"]
    assert store.cited_but_missing(con, [a, b], min_count=3)["missing"] == []


def test_a_sure_resolved_citation_costs_as_a_stated_one(con: Any) -> None:
    """G1: three clean chains of resolved citations (score 1.00) were
    classed weak, at cost 6.45 past the line of 6."""
    from prax import store
    from prax.store.graph import paths as part

    E = store.Edge
    store.link(
        con,
        E("Paper A", "paper", "cites", "Paper B", "paper"),
        producer="references",
        confidence="INFERRED",
        evidence="references: [3] 'Paper B' (2010), score 0.98",
    )
    store.link(
        con,
        E("Paper A", "paper", "cites", "Paper C", "paper"),
        producer="references",
        confidence="INFERRED",
        evidence="references: [4] 'Paper C' (2011), score 0.81",
    )
    conf = {r[0]: r[4] for r in part._rows(con, None)}
    by_dst = dict(
        con.execute(
            "SELECT e.id, t.name FROM edges e JOIN entities t ON t.id = e.dst"
        ).fetchall()
    )
    assert {by_dst[i]: c for i, c in conf.items()} == {
        "Paper B": "EXTRACTED",
        "Paper C": "INFERRED",
    }


def test_one_fact_from_one_reader_twice_is_ended_once(con: Any) -> None:
    """G2: the same fact live as edges 7402 and 47184, one extraction under
    two ontology versions; a fact two readers state stays twice."""
    from prax import store
    from prax.store import repair

    E = store.Edge
    doc = int(store.ingest_text(con, "onsets " * 40, title="A paper")["doc_id"])
    fact = E("A paper", "paper", "about", "onset detection", "concept")
    first = store.link(con, fact, source_doc=doc, producer="model-a", run="v1")
    store.link(con, fact, source_doc=doc, producer="model-a", run="v3")
    store.link(con, fact, source_doc=doc, producer="zotero", run="import")
    found = repair._duplicate_facts(con)
    assert [(f["id"], len(f["later"])) for f in found] == [(first, 1)]
    assert repair._repair_duplicate_facts(con, found) == 1
    assert repair._duplicate_facts(con) == []
    live = con.execute(
        "SELECT producer FROM edges WHERE source_doc = ? AND valid_to IS NULL", (doc,)
    ).fetchall()
    assert sorted(r[0] for r in live) == ["model-a", "zotero"]
    # recorded, so the run can be undone (a fact live by its oldest edge is
    # not stated a second time by the undoing)
    assert (
        con.execute(
            "SELECT count(*) FROM edge_endings WHERE run = ?",
            (repair.DUPLICATES_PRODUCER,),
        ).fetchone()[0]
        == 1
    )


def test_a_repairs_corrections_are_counted_not_listed(con: Any) -> None:
    """G3: a day of ``changes`` was 2,609 facts, most of them part_of
    turned the right way round by a repair."""
    from prax import store
    from prax.store.repair.common import _invalidate

    E = store.Edge
    learned = store.link(con, E("Paper A", "paper", "cites", "Paper B", "paper"))
    wrong = store.link(
        con, E("TU Munich", "organization", "part_of", "A chair", "organization")
    )
    _invalidate(con, [wrong], run="heal:part_of-direction")
    store.link(
        con,
        E("A chair", "organization", "part_of", "TU Munich", "organization"),
        producer="heal:part_of-direction",
        run="heal:part_of-direction",
    )
    got = store.changes(con, "2000-01-01")
    assert [f["edge_id"] for f in got["added"]["facts"]] == [learned]
    assert got["added"]["corrected"] == 2 and got["ended"]["corrected"] == 1
    every = store.changes(con, "2000-01-01", corrections=True)
    assert every["added"]["count"] == 3 and "corrected" not in every["added"]
