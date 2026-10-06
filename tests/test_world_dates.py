"""When a fact holds in the world, read from the sentences that date it
(the ``worlddates`` step; ``prax.graph.worlddates``, AL step 5)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import models, store, work, worker
from prax.graph import ontology, worlddates
from prax.graph.extraction import Triple

TEXT = (
    "Pianola Works was founded in 1977 in Boston. It was bought by Organa"
    " Instruments in 1985. The Delay Engine was released by Pianola Works in"
    " 1979. Our method extends the filter of Moorer (1979) and [12]. We"
    " measured it in 2019 hours of audio."
)


def _t(src: str, st: str, rel: str, dst: str, dt: str, ev: str, **kw: str) -> Triple:
    return Triple(src, st, rel, dst, dt, "EXTRACTED", ev, **kw)


def test_the_sentences_read_hold_a_year_and_a_word_of_time() -> None:
    said = worlddates.sentences([TEXT])
    assert said == [
        "Pianola Works was founded in 1977 in Boston.",
        "It was bought by Organa Instruments in 1985.",
        "The Delay Engine was released by Pianola Works in 1979.",
    ]  # a cited year is not a fact's, nor a year with no word of time
    assert worlddates.sentences([TEXT], limit=1) == said[:1]
    assert worlddates.sentences(["Since 1990 the lab has been in Graz. " * 2]) == [
        "Since 1990 the lab has been in Graz."
    ]  # none twice


def test_only_what_the_sentences_bear_out_is_kept() -> None:
    onto = ontology.current()
    said = worlddates.sentences([TEXT])
    good = _t(
        "Delay Engine",
        "device",
        "developed_by",
        "Pianola Works",
        "organization",
        "The Delay Engine was released by Pianola Works in 1979",
        world_from="1979",
    )
    kept = worlddates.checked(
        [
            good,
            # the quote is not in a sentence it was shown
            _t(
                "Delay Engine",
                "device",
                "developed_by",
                "Pianola Works",
                "organization",
                "released in 1979 to acclaim",
                world_from="1979",
            ),
            # the year is not in the quote
            _t(
                "Pianola Works",
                "organization",
                "located_in",
                "Boston",
                "place",
                "founded in 1977 in Boston",
                world_from="1980",
            ),
            # a date for a name, and a stand-in for one
            _t(
                "Pianola Works",
                "organization",
                "part_of",
                "1985",
                "organization",
                "It was bought by Organa Instruments in 1985",
                world_from="1985",
            ),
            _t(
                "the company",
                "organization",
                "part_of",
                "Organa Instruments",
                "organization",
                "It was bought by Organa Instruments in 1985",
                world_from="1985",
            ),
            # "ended, date unknown" is not this reading's to say
            _t(
                "Pianola Works",
                "organization",
                "located_in",
                "Boston",
                "place",
                "Pianola Works was founded in 1977 in Boston",
                world_to="unknown",
            ),
            # a relation no date fits (the year a document appeared)
            _t(
                "A paper",
                "paper",
                "authored_by",
                "Moorer",
                "person",
                "The Delay Engine was released by Pianola Works in 1979",
                world_from="1979",
            ),
        ],
        said,
        onto,
    )
    assert kept == [good]
    names = {r.name for r in worlddates.dated_relations(onto)}
    assert {"affiliated_with", "part_of", "published_in", "developed_by"} <= names
    assert not names & {"authored_by", "cites", "about", "written_in"}


def test_the_judge_reads_the_fact_in_words() -> None:
    onto = ontology.current()
    ev = "x"
    assert (
        worlddates.statement(
            _t(
                "Cronin",
                "person",
                "affiliated_with",
                "Cooper",
                "organization",
                ev,
                world_from="1999",
            ),
            onto,
        )
        == "Cronin affiliated with Cooper, since 1999."
    )
    assert (
        worlddates.statement(
            _t(
                "NIME",
                "event",
                "located_in",
                "Oslo",
                "place",
                ev,
                world_from="2011-05-30",
                world_to="2011-06-01",
            ),
            onto,
        )
        == "NIME located in Oslo, from May 30, 2011 until June 1, 2011."
    )
    assert (
        worlddates.statement(
            _t(
                "Lecture",
                "event",
                "located_in",
                "New York",
                "place",
                ev,
                world_from="2003-10-11",
                world_to="2003-10-11",
            ),
            onto,
        )
        == "Lecture located in New York, on October 11, 2003."
    )
    assert (
        worlddates.statement(
            _t(
                "Live",
                "program",
                "developed_by",
                "Ableton",
                "organization",
                ev,
                world_from="2001",
            ),
            onto,
        )
        == "Live developed by Ableton, in 2001."
    )


def test_a_dated_fact_replaces_its_undated_one_and_a_run_comes_back_whole(
    con: sqlite3.Connection,
) -> None:
    doc = store.ingest_text(con, " ".join([TEXT] * 3), title="Pianola Works")["doc_id"]
    said = worlddates.sentences(store.text_chunks(con, doc))
    org = "organization"
    old = store.link(
        con,
        store.Edge("Pianola Works", org, "part_of", "Organa Instruments", org),
        source_doc=doc,
        evidence="bought by Organa Instruments",
        producer="extractor",
        run="x1",
    )
    facts = [
        {
            "src": "Pianola Works",
            "src_type": org,
            "rel": "part_of",
            "dst": "Organa Instruments",
            "dst_type": org,
            "confidence": "EXTRACTED",
            "evidence": "It was bought by Organa Instruments in 1985",
            "from": "1985",
        },
        {
            "src": "Delay Engine",
            "src_type": "device",
            "rel": "developed_by",
            "dst": "Pianola Works",
            "dst_type": org,
            "evidence": "The Delay Engine was released by Pianola Works in 1979",
            "from": "1979",
        },
        {  # the door checks again: a worker is a client
            "src": "Delay Engine",
            "src_type": "device",
            "rel": "developed_by",
            "dst": "Pianola Works",
            "dst_type": org,
            "evidence": "made up words in 1979",
            "from": "1979",
        },
    ]
    got = worlddates.apply(con, doc, facts, said, model="m", run="wd-1")
    assert (got.restated, got.linked, got.refused) == (1, 1, 1)
    row = con.execute(
        "SELECT valid_to IS NULL, world_from FROM edges WHERE id = ?", (old,)
    ).fetchone()
    assert tuple(row) == (0, None)  # ended, never changed
    new = store.corrections_of(con, old)["corrected_by"]["edge_id"]
    row = con.execute(
        "SELECT world_from, world_from_precision, producer, evidence_start IS NOT NULL"
        " FROM edges WHERE id = ?",
        (new,),
    ).fetchone()
    assert tuple(row) == ("1985", "year", "world-dates:m", 1)
    stamp = store.get_meta(con, doc)["world_dates"]
    assert stamp["restated"] == 1 and stamp["sentences"] == len(said)
    assert doc not in store.world_dates_needed(con)  # read, until the text changes
    again = worlddates.apply(con, doc, facts[:1], said, model="m", run="wd-2")
    assert again.existing == 1  # dated already: nothing doubled
    out = store.restore_run(con, "wd-1")
    assert out == {"retired": 2, "restated": 1}
    live = con.execute(
        "SELECT producer, world_from FROM edges WHERE rel = 'part_of'"
        " AND valid_to IS NULL"
    ).fetchall()
    assert [tuple(r) for r in live] == [("extractor", None)]


@pytest.fixture()
def client() -> Iterator[TestClient]:
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_step_hands_out_sentences_and_keeps_what_the_judge_believes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    dated = store.ingest_text(con, " ".join([TEXT] * 3), title="Pianola Works")[
        "doc_id"
    ]
    plain = store.ingest_text(con, "A note without any year. " * 20, title="n")[
        "doc_id"
    ]
    spec = models.ModelSpec(
        name="local", kind="openai", base_url="http://127.0.0.1:1/v1", model="m"
    )
    monkeypatch.setattr(
        models, "resolve", lambda s: spec if s == "worlddates" else None
    )
    answer = "\n".join(
        "\t".join(f)
        for f in (
            [
                "triple",
                "src=Delay Engine",
                "src_type=device",
                "rel=developed_by",
                "dst=Pianola Works",
                "dst_type=organization",
                "confidence=EXTRACTED",
                "evidence=The Delay Engine was released by Pianola Works in 1979",
                "from=1979",
            ],
            [
                "triple",
                "src=Pianola Works",
                "src_type=organization",
                "rel=located_in",
                "dst=Boston",
                "dst_type=place",
                "confidence=EXTRACTED",
                "evidence=Pianola Works was founded in 1977 in Boston",
                "from=1977",
            ],
        )
    )

    class FakeRuntime:
        base_url, model = spec.base_url, spec.model

        def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, Any]]:
            return answer, {}

    def judge(base_url: str, model: str, messages: Any, **kw: Any) -> Any:
        # yes to the release, no to "located in Boston since 1977"
        sure = "Delay Engine" in messages[0]["content"]
        return [("yes", -0.01), ("no", -5.0)] if sure else [("no", -0.01)]

    monkeypatch.setattr(models, "runtime", lambda s: FakeRuntime())
    monkeypatch.setattr(models, "top_logprobs", judge)
    items = {i["doc_id"]: i for i in client.get("/work/worlddates").json()["items"]}
    assert set(items) >= {dated, plain}
    assert items[plain]["sentences"] == [] and len(items[dated]["sentences"]) == 3
    work._leases.clear()
    door = worker.Door("http://testserver", client=client, name="test-worker")
    out = worker.run_once(door, steps=("worlddates",), log_=lambda t: None)
    assert out["worlddates"].startswith("0 facts dated, 1 new")
    rows = con.execute(
        "SELECT e.rel, e.world_from FROM edges e WHERE e.source_doc = ?"
        " AND e.producer LIKE 'world-dates:%'",
        (dated,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [("developed_by", "1979")]
    assert store.get_meta(con, plain)["world_dates"]["sentences"] == 0
    assert client.get("/work/worlddates").json()["items"] == []


def test_a_restored_edge_keeps_where_its_quote_stood(con: sqlite3.Connection) -> None:
    """``restore_run`` states an ended edge again with its quote's place
    (migration 48), which is part of its evidence."""
    doc = store.ingest_text(con, TEXT, title="t")["doc_id"]
    h = store.get_document(con, doc)["text_hash"]
    org = "organization"
    old = store.link(
        con,
        store.Edge("Pianola Works", org, "part_of", "Organa Instruments", org),
        source_doc=doc,
        evidence="bought by Organa Instruments",
        place=(60, 88, h),
    )
    assert store.end_edge(con, old, run="r1")
    assert not store.end_edge(con, old, run="r1")  # not live: nothing to end
    assert store.restore_run(con, "r1")["restated"] == 1
    row = con.execute(
        "SELECT evidence_start, evidence_end, evidence_text_hash FROM edges"
        " WHERE rel = 'part_of' AND valid_to IS NULL"
    ).fetchone()
    assert tuple(row) == (60, 88, h)
