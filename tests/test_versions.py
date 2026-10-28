"""Versions of a work (plan item AS): the pairs found, a person's answer,
and what a read makes of the answer. The papers are invented."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.text import shingles

TITLE = "Learning to Rank Invented Things"
WORDS = [
    "the",
    "method",
    "ranks",
    "each",
    "invented",
    "thing",
    "by",
    "how",
    "often",
    "the",
    "other",
    "things",
    "point",
    "to",
    "it",
    "and",
    "the",
    "graph",
    "of",
    "those",
    "pointers",
    "converges",
    "after",
    "a",
    "few",
    "rounds",
    "which",
    "the",
    "experiments",
    "below",
    "measure",
    "on",
    "three",
    "collections",
]


def _text(seed: int, words: int = 400, changed: float = 0.0) -> str:
    """A text of ``words`` words; ``changed`` of its sentences differ by seed."""
    out = []
    for i in range(words // len(WORDS)):
        sentence = list(WORDS)
        if (i * 7919 % 100) / 100 < changed:
            sentence[i % len(sentence)] = f"edited{seed}x{i}"
            sentence[(i * 3) % len(sentence)] = f"also{seed}y{i}"
        out.append(" ".join(sentence) + f" number {i}.")
    return "\n\n".join(out)


def _doc(con: sqlite3.Connection, text: str, title: str = TITLE) -> int:
    return int(store.ingest_text(con, text, title=title)["doc_id"])


def test_shingles_survive_an_edit_that_changes_every_chunk() -> None:
    a = shingles.shingles([_text(1)])
    b = shingles.shingles([_text(2, changed=0.3)])
    assert 0.3 < shingles.overlap(a, b) < 0.9
    assert shingles.overlap(a, a) == 1.0
    assert shingles.overlap(a, frozenset()) == 0.0
    # the same numbers in every process: blake2b, not hash()
    assert shingles.shingles(["one two"]) == shingles.shingles(["One, two!"])


def test_pairs_of_one_title_are_found_by_how_much_text_they_share(
    con: sqlite3.Connection,
) -> None:
    preprint = _doc(con, _text(1))
    journal = _doc(con, _text(2, changed=0.3))
    copy = _doc(con, _text(1) + " A second download.")
    other = _doc(con, " ".join(f"unrelated{i} words here" for i in range(200)))
    got = store.find_versions(con)
    assert got["new"] == 3 and got["open"] == 3
    page = store.version_candidates(con)
    pairs = {(i["a"]["doc_id"], i["b"]["doc_id"]): i for i in page["items"]}
    assert set(pairs) == {(preprint, journal), (preprint, copy), (journal, copy)}
    assert other not in {d for p in pairs for d in p}
    assert pairs[(preprint, copy)]["reasons"]["suggest"] == "copy"
    assert pairs[(preprint, journal)]["reasons"]["suggest"] == "version"
    assert page["items"][0]["overlap"] > 0.9  # the most alike first
    # found again with no text changed: nothing new, nothing dropped
    assert store.find_versions(con) == {"open": 3, "new": 0, "dropped": 0}


def test_a_version_writes_the_work_and_the_order(con: sqlite3.Connection) -> None:
    preprint = _doc(con, _text(1))
    journal = _doc(con, _text(2, changed=0.3))
    store.find_versions(con)
    (pair,) = store.version_candidates(con)["items"]
    got = store.decide_version(con, pair["id"], "version")
    assert got["work"]["name"] == TITLE
    versions = store.versions_of(con, preprint)
    assert versions is not None
    assert [v["doc_id"] for v in versions["versions"]] == [preprint, journal]
    assert versions["latest"] == journal
    assert store.later_versions(con, [preprint, journal]) == {preprint: [journal]}
    with pytest.raises(ValueError, match="answered"):
        store.decide_version(con, pair["id"], "different")
    # a pass keeps the answer; the undo ends the facts and opens the pair
    store.find_versions(con)
    assert store.version_candidates(con, verdict="version")["total"] == 1
    undone = store.undo_version(con, pair["id"])
    assert undone["ended"] == 2
    assert store.versions_of(con, preprint) is None
    assert store.version_candidates(con)["total"] == 1


def test_a_third_version_joins_the_work_of_the_first_two(
    con: sqlite3.Connection,
) -> None:
    a = _doc(con, _text(1))
    b = _doc(con, _text(2, changed=0.3))
    c = _doc(con, _text(3, changed=0.3))
    store.find_versions(con)
    items = {
        (i["a"]["doc_id"], i["b"]["doc_id"]): i["id"]
        for i in store.version_candidates(con)["items"]
    }
    first = store.decide_version(con, items[(a, b)], "version")
    second = store.decide_version(con, items[(b, c)], "version")
    assert first["work"] == second["work"]
    got = store.versions_of(con, a)
    assert got is not None and [v["doc_id"] for v in got["versions"]] == [a, b, c]


def test_a_copy_is_retired_into_its_keeper_and_back(con: sqlite3.Connection) -> None:
    keep = _doc(con, _text(1))
    twin = _doc(con, _text(1) + " A second download.")
    store.find_versions(con)
    (pair,) = store.version_candidates(con)["items"]
    got = store.decide_version(con, pair["id"], "copy")
    assert (got["kept"], got["retired"]) == (keep, twin)
    assert store.get_meta(con, twin)["retired"]["of"] == keep
    # a retired document pairs no more, but the answer stays
    assert store.find_versions(con)["open"] == 0
    assert store.version_candidates(con, verdict="copy")["total"] == 1
    back = store.undo_version(con, pair["id"])
    assert back["unretired"] == twin
    assert "retired" not in store.get_meta(con, twin)


def test_different_is_kept_and_not_asked_again(con: sqlite3.Connection) -> None:
    _doc(con, _text(1))
    _doc(con, _text(2, changed=0.3))
    store.find_versions(con)
    (pair,) = store.version_candidates(con)["items"]
    store.decide_version(con, pair["id"], "different")
    assert store.find_versions(con)["open"] == 0
    assert store.version_candidates(con)["counts"]["different"] == 1


def test_a_common_title_held_by_many_documents_pairs_nothing(
    con: sqlite3.Connection,
) -> None:
    for i in range(store.VERSION_GROUP_MAX + 1):
        _doc(con, _text(i, changed=0.2), title="Introduction to the Course")
    assert store.find_versions(con)["open"] == 0


def test_heal_reads_the_same_title_key() -> None:
    assert store.title_key("Learning: to Rank!") == "learning to rank"
    assert store.title_key("Notes") == ""


def test_a_search_folds_an_older_version_under_the_latest(
    con: sqlite3.Connection, client: TestClient
) -> None:
    preprint = _doc(con, _text(1))
    journal = _doc(con, _text(2, changed=0.3))
    store.find_versions(con)
    (pair,) = store.version_candidates(con)["items"]
    store.decide_version(con, pair["id"], "version")
    hits = store.search(con, "invented thing pointers converges", 10)
    by_doc = {h["doc_id"]: h for h in hits}
    assert preprint not in by_doc and by_doc[journal]["versions"] == [preprint]
    # asked for the plain order, both come, the older marked stale
    plain = {
        h["doc_id"]: h
        for h in store.search(
            con, "invented thing pointers converges", 10, include_stale=True
        )
    }
    assert plain[preprint]["stale"]["by"] == "versions"
    assert plain[preprint]["stale"]["replaced_by"][0]["doc_id"] == journal
    got = client.get(f"/get/{preprint}").json()
    assert got["versions"]["latest"] == journal
    assert got["stale"]["state"] == "superseded"
    # the door's routes: the page, an answer and its undo
    assert client.get("/graph/versions?verdict=version").json()["total"] == 1
    assert client.post(f"/graph/versions/{pair['id']}/undo").status_code == 200
    answer = client.post(f"/graph/versions/{pair['id']}", json={"verdict": "copy"})
    assert answer.status_code == 200 and answer.json()["retired"] == journal
    bad = client.post(f"/graph/versions/{pair['id']}", json={"verdict": "copy"})
    assert bad.status_code == 400
