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


def _with_figures(con: sqlite3.Connection) -> tuple[int, int]:
    """One document whose figure a model has read, one whose figure
    nobody has read."""
    ref, other = "a" * 64, "b" * 64
    read = store.ingest_text(
        con,
        "# A paper that was read\n\nProse about the plot below.\n\n"
        f"![Figure 1. The plot.](figure:{ref})\n"
        "*Figure, as read by a-vision-model:* a plot of two curves.\n",
        title="A paper that was read",
    )["doc_id"]
    unread = store.ingest_text(
        con,
        "# A paper nobody read\n\nProse about the plot below.\n\n"
        f"![Figure 1. The other plot.](figure:{other})\n",
        title="A paper nobody read",
    )["doc_id"]
    return read, unread


def test_a_figure_nobody_has_read_is_named_with_the_way_on(
    con: sqlite3.Connection,
) -> None:
    """A report: the picture is in the text and nothing says what it
    shows, so a search cannot find it — the offer asks the vision model."""
    read, unread = _with_figures(con)
    found = store.health(con, only=["unread-figures"])["ailments"][0]
    assert found["count"] == 1 and not found["repairable"]
    assert found["examples"][0]["id"] == unread
    assert found["examples"][0]["unread"] == 1
    assert [o["label"] for o in found["offers"]] == [
        "read the captioned ones",
        "read every image",
    ]
    assert found["offers"][0] == {
        "label": "read the captioned ones",
        "extractor": "figures",
        "unread_figures": True,
    }
    # and the two selections are the two halves of the library's figures
    assert store.select_for_reading(con, unread_figures=True) == [unread]
    assert store.select_for_reading(con, read_figures=True) == [read]
    done = store.heal(con, only=["unread-figures"])
    assert "ask" in done["unread-figures"]


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


def test_an_extraction_of_a_replaced_text_is_found_and_unstamped(
    con: sqlite3.Connection,
) -> None:
    """A text replaced after its extraction (a reader from before the rule
    that unstamps on the way in): the ailment names the document, the
    repair moves the stamp aside so the extract step selects it again; an
    annotating read after the extraction is not damage."""
    from prax import extraction, ontology
    from prax.parsers import queue

    body = "# A paper on reverb\n\n" + "Feedback delay networks make reverb. " * 40
    replaced = store.ingest_text(con, body, title="Replaced")["doc_id"]
    annotated = store.ingest_text(con, body + " Twice.", title="Annotated")["doc_id"]
    for doc_id in (replaced, annotated):
        result = extraction.StubExtractor().extract(extraction.build_input(con, doc_id))
        extraction.apply(con, doc_id, result, extractor="stub", run="r1")
    # the history entries are what the ailment reads: written as the queue
    # writes them, but around the rule, as a reader from before it did
    queue._record(
        con,
        replaced,
        {
            "extractor": "marker/2.0.0",
            "outcome": "upgraded",
            "at": "2099-01-01T00:00:00Z",
        },
    )
    queue._record(
        con,
        annotated,
        {
            "extractor": "figures/2+m",
            "outcome": "upgraded",
            "at": "2099-01-01T00:00:00Z",
        },
    )
    ailment = next(a for a in store.AILMENTS if a.name == "stale-extractions")
    found = ailment.find(con)
    assert [f["id"] for f in found] == [replaced]
    assert found[0]["read_by"] == "marker/2.0.0" and found[0]["extractor"] == "stub"
    version = ontology.current().version
    assert replaced not in store.select_for_extraction(con, ontology_version=version)
    assert ailment.repair is not None
    assert ailment.repair(con, found) == 1
    assert replaced in store.select_for_extraction(con, ontology_version=version)
    meta = store.get_meta(con, replaced)
    assert meta["extraction_stale"]["run"] == "r1"
    assert meta["extraction_history"][-1]["superseded_by"] == "marker/2.0.0"
    assert ailment.find(con) == []  # repaired: nothing left to find
    assert store.get_meta(con, annotated).get("extraction")
