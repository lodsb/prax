"""The heal pass: the damage that recurs is named, found and repaired
through the store's own rules — an edge is ended, never deleted."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store


@pytest.fixture()
def client(data_dir: object) -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _link(con: sqlite3.Connection, src: str, dst: str, rel: str = "extends") -> int:
    doc = store.ingest_text(con, f"{src} and {dst} " * 30, title=f"{src}-{dst}")
    return store.link(
        con,
        store.Edge(src, "concept", rel, dst, "concept"),
        source_doc=doc["doc_id"],
        producer="test",
    )


def test_every_ailment_says_what_it_is(con: sqlite3.Connection) -> None:
    seen = store.health(con)
    assert seen["found"] == 0
    names = [a["name"] for a in seen["ailments"]]
    assert "placeholder-entities" in names and "self-edges" in names
    for ailment in seen["ailments"]:
        assert ailment["what"] and ailment["fix"] and ailment["count"] == 0
        assert isinstance(ailment["repairable"], bool)


def test_placeholder_entities_are_found_and_their_edges_ended(
    con: sqlite3.Connection,
) -> None:
    junk = _link(con, "source name", "granular synthesis")
    good = _link(con, "wave digital filter", "granular synthesis")
    found = store.health(con, only=["placeholder-entities"])["ailments"][0]
    assert found["count"] == 1
    assert found["examples"][0]["name"] == "source name"
    assert found["examples"][0]["edges"] == 1

    done = store.heal(con, only=["placeholder-entities"])
    assert done["placeholder-entities"] == {"found": 1, "repaired": 1}
    assert store.health(con, only=["placeholder-entities"])["ailments"][0]["count"] == 0
    # ended, not deleted: the row is still there with a valid_to (invariant 8)
    row = con.execute("SELECT valid_to FROM edges WHERE id = ?", (junk,)).fetchone()
    assert row["valid_to"] is not None
    assert (
        con.execute("SELECT valid_to FROM edges WHERE id = ?", (good,)).fetchone()[
            "valid_to"
        ]
        is None
    )


def test_reference_numbers_and_unnamed_entities(con: sqlite3.Connection) -> None:
    _link(con, "[12]", "room acoustics")
    _link(con, "a whole citation as a name " * 20, "room acoustics")
    _link(con, "MIDI", "room acoustics")  # short, but a name
    assert (
        store.health(con, only=["reference-number-entities"])["ailments"][0]["count"]
        == 1
    )
    assert store.health(con, only=["unnamed-entities"])["ailments"][0]["count"] == 1
    done = store.heal(con, only=["reference-number-entities", "unnamed-entities"])
    assert done["reference-number-entities"]["repaired"] == 1
    assert done["unnamed-entities"]["repaired"] == 1
    assert [e["src"] for e in store.traverse(con, "room acoustics")] == ["MIDI"]


def test_self_edges_are_what_a_merge_leaves_behind(con: sqlite3.Connection) -> None:
    _link(con, "nonnegative matrix factorization", "NMF", rel="extends")
    duplicate = store._entity_id(con, "NMF", "concept")
    survivor = store._entity_id(con, "nonnegative matrix factorization", "concept")
    store.merge_entities(con, duplicate, survivor)
    found = store.health(con, only=["self-edges"])["ailments"][0]
    assert found["count"] == 1
    assert store.heal(con, only=["self-edges"])["self-edges"]["repaired"] == 1
    assert store.health(con, only=["self-edges"])["ailments"][0]["count"] == 0


def test_what_is_written_after_a_retirement_is_found(
    con: sqlite3.Connection,
) -> None:
    """Retiring a document ends its edges and drops its queue. A pass that
    was already reading it writes afterwards, and that is what these two
    ailments are for."""
    doc = store.ingest_text(con, "a captured page " * 40, title="Page")["doc_id"]
    store.retire_document(con, doc, reason="a duplicate capture")
    store.link(
        con,
        store.Edge("a", "concept", "extends", "b", "concept"),
        source_doc=doc,
        producer="a pass that was already running",
    )
    store.queue_review(
        con,
        src="a",
        src_type="concept",
        rel="invented_relation",
        dst="b",
        dst_type="concept",
        reason="unmapped: invented_relation",
        source_doc=doc,
    )
    assert (
        store.health(con, only=["edges-of-retired-documents"])["ailments"][0]["count"]
        == 1
    )
    assert (
        store.health(con, only=["review-of-retired-documents"])["ailments"][0]["count"]
        == 1
    )
    done = store.heal(
        con, only=["edges-of-retired-documents", "review-of-retired-documents"]
    )
    assert done["edges-of-retired-documents"]["repaired"] == 1
    assert done["review-of-retired-documents"]["repaired"] == 1
    assert store.count_review(con) == 0


def test_a_job_whose_heartbeat_stopped_a_day_ago_is_closed(
    con: sqlite3.Connection,
) -> None:
    job = store.job_start(con, "extract", host="another-machine", pid=0)
    assert store.health(con, only=["stale-jobs"])["ailments"][0]["count"] == 0
    con.execute(
        "UPDATE jobs SET updated_at = datetime('now', '-2 days') WHERE id = ?", (job,)
    )
    con.commit()
    assert store.health(con, only=["stale-jobs"])["ailments"][0]["count"] == 1
    assert store.heal(con, only=["stale-jobs"])["stale-jobs"]["repaired"] == 1
    assert store.running_jobs(con) == 0


def test_the_reports_say_what_to_do_and_change_nothing(
    con: sqlite3.Connection,
) -> None:
    store.register(con, b"PK\x03\x04 a zip", mime="application/zip", title="x.zip")
    found = store.health(con, only=["documents-without-an-extractor"])["ailments"][0]
    assert found["count"] == 1 and not found["repairable"]
    assert found["examples"][0]["mime"] == "application/zip"
    done = store.heal(con, only=["documents-without-an-extractor"])
    assert "look at" in done["documents-without-an-extractor"]


def test_an_unknown_ailment_is_refused(con: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="no such ailment"):
        store.health(con, only=["hypochondria"])


def test_the_pass_is_a_job_and_the_door_offers_both_halves(
    client: TestClient, con: sqlite3.Connection
) -> None:
    _link(con, "source name", "granular synthesis")
    looked: dict[str, Any] = client.get("/heal").json()
    by_name = {a["name"]: a for a in looked["ailments"]}
    assert by_name["placeholder-entities"]["count"] == 1
    # the chunks of that document have no vectors yet, and the pass says so
    assert by_name["chunks-without-vectors"]["count"] == 1
    assert client.get("/heal", params={"check": "self-edges"}).json()["found"] == 0
    assert client.get("/heal", params={"check": "nonsense"}).status_code == 400
    done = client.post("/heal", json={"checks": ["placeholder-entities"]}).json()
    assert done["placeholder-entities"]["repaired"] == 1
    after = client.get("/heal", params={"check": "placeholder-entities"}).json()
    assert after["found"] == 0
    recent = client.get("/jobs").json()["recent"]
    assert recent[0]["name"] == "heal" and recent[0]["status"] == "done"


def test_a_name_with_markup_is_cleaned_rather_than_thrown_away(
    con: sqlite3.Connection,
) -> None:
    """What a citation importer leaves behind is repaired by mending the
    name, because the edge under it is real."""
    _link(con, "<i>The Origins of Music</i>", "music cognition")
    _link(con, "TOPOI" + chr(10) + "        and rhetorical competence", "rhetoric")
    found = store.health(con, only=["mangled-names"])["ailments"][0]
    assert found["count"] == 2
    assert {e["cleaned"] for e in found["examples"]} == {
        "The Origins of Music",
        "TOPOI and rhetorical competence",
    }
    assert store.heal(con, only=["mangled-names"])["mangled-names"]["repaired"] == 2
    assert {e["src"] for e in store.traverse(con, "music cognition")} == {
        "The Origins of Music"
    }
    assert store.health(con, only=["mangled-names"])["ailments"][0]["count"] == 0


def test_cleaning_a_name_merges_into_the_one_already_clean(
    con: sqlite3.Connection,
) -> None:
    _link(con, "The Origins of Music", "music cognition")
    _link(con, "<i>The Origins of Music</i>", "rhetoric")
    assert store.heal(con, only=["mangled-names"])["mangled-names"]["repaired"] == 1
    seen = {e["src"] for e in store.traverse(con, "music cognition")}
    seen |= {e["src"] for e in store.traverse(con, "rhetoric")}
    assert seen == {"The Origins of Music"}  # one entity, both edges on it


def test_a_long_claim_is_left_alone(con: sqlite3.Connection) -> None:
    """A claim is a sentence by definition; only other types are flagged for
    carrying a whole paragraph as a name."""
    long_name = ("a claim that goes on and on " * 20)[:320]
    for etype in ("claim", "paper"):
        store.link(
            con,
            store.Edge(long_name, etype, "contrasts", "reverberation", "concept"),
            producer="test",
        )
    found = store.health(con, only=["unnamed-entities"])["ailments"][0]
    assert found["count"] == 1 and found["examples"][0]["type"] == "paper"
