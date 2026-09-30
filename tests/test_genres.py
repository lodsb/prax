"""What a document is: the genre vocabulary beside the modules, a person's
genres as the gold sample, and the sample to label (stage Z)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.graph import ontology


def test_the_genres_load_beside_the_modules() -> None:
    g = ontology.genres()
    assert g.version != "0"
    labels = g.labels()
    assert labels[0] == "informational" and "paper" in labels
    assert g.level_of("datasheet") == "instructional"
    assert g.level_of("opinion") == "opinion"  # a level is a label of its own
    assert g.level_of("symphony") is None
    assert all(lv.description for lv in g.levels)
    assert all(d for lv in g.levels for _, d in lv.genres)


def test_the_genres_are_not_a_module() -> None:
    onto = ontology.current()
    for beside in ("genres", "subjects"):
        assert beside not in onto.modules and beside not in onto.version


def test_the_subjects_load_and_share_no_label_with_the_genres() -> None:
    """A label says one thing: "opinion" is a genre, "politics" a subject,
    and a model or a rule never has to ask which file a word came from."""
    subj = ontology.subjects()
    assert subj.version != "0"
    assert subj.level_of("politics") == "society"
    assert {"philosophy", "sociology", "music", "electronics"} <= set(subj.labels())
    assert not set(subj.labels()) & set(ontology.genres().labels())


def test_a_label_is_named_once() -> None:
    with pytest.raises(ValueError, match="genres.yaml names .essay. twice"):
        ontology.parse_facet(
            "levels:\n  opinion: {genres: {essay: x}}\n  other: {genres: {essay: y}}"
        )


def test_check_keeps_the_vocabulary_order_and_refuses_the_unknown() -> None:
    g = ontology.genres()
    assert g.check(["review", "tutorial", "review"]) == ["tutorial", "review"]
    with pytest.raises(ValueError, match="unknown label"):
        g.check(["pamphlet"])


def _doc(con: sqlite3.Connection, text: str, **meta: object) -> int:
    doc_id = int(store.ingest_text(con, text, title=text[:20])["doc_id"])
    if meta:
        m = store.get_meta(con, doc_id)
        m.update(meta)
        store.set_meta(con, doc_id, m)
    return doc_id


def test_a_persons_genres_are_the_gold(con: sqlite3.Connection) -> None:
    d = _doc(con, "How to solder a TL072 onto a board, step by step.")
    got = store.set_genres(con, d, ["tutorial", "instructional"])
    assert got == {
        "doc_id": d,
        "genres": ["instructional", "tutorial"],
        "subjects": [],
        "skipped": False,
    }
    m = store.get_meta(con, d)
    assert m["genres"] == [
        {"genre": "instructional", "p": 1.0},
        {"genre": "tutorial", "p": 1.0},
    ]
    assert m["genres_by"] == "human" and m["genres_at"]
    with pytest.raises(ValueError, match="unknown label"):
        store.set_genres(con, d, ["pamphlet"])
    with pytest.raises(ValueError, match="skip"):
        store.set_genres(con, d, [])
    assert store.set_genres(con, d, None, skip=True)["skipped"]
    m = store.get_meta(con, d)
    assert "genres" not in m and m["genres_skip"]
    store.set_genres(con, d, None)  # taken back: open again
    assert not {"genres", "genres_skip"} & set(store.get_meta(con, d))
    with pytest.raises(KeyError):
        store.set_genres(con, 99999, ["paper"])


def test_subjects_go_with_the_genres(con: sqlite3.Connection) -> None:
    d = _doc(con, "Why the welfare state was built, and why it is dismantled.")
    got = store.set_genres(con, d, ["essay"], subjects=["sociology", "politics"])
    # a subject brings its group
    assert got["subjects"] == ["society", "politics", "sociology"]
    assert store.get_meta(con, d)["subjects"] == [
        {"subject": "society", "p": 1.0},
        {"subject": "politics", "p": 1.0},
        {"subject": "sociology", "p": 1.0},
    ]
    assert store.set_genres(con, d, ["invoice"])["subjects"] == []  # may be none
    assert "subjects" not in store.get_meta(con, d)
    with pytest.raises(ValueError, match="unknown label"):
        store.set_genres(
            con, d, ["essay"], subjects=["essay"]
        )  # a genre, not a subject
    store.set_genres(con, d, ["essay"], subjects=["philosophy"])
    item = store.genre_sample(con, state="labelled")["items"][0]
    assert item["subjects"] == ["society", "philosophy"]


def test_the_sample_takes_each_source_in_turn(con: sqlite3.Connection) -> None:
    """The NAS dump must not fill the gold sample: the sources alternate,
    and the order is the same on every visit."""
    nas = [
        _doc(con, f"nas file {i}", origin={"path": f"/nas/{i}.pdf"}) for i in range(6)
    ]
    zot = [_doc(con, f"zotero paper {i}", source="zotero") for i in range(2)]
    ext = _doc(con, "a page sent from the browser", capture={"by": "extension"})
    first = store.genre_sample(con, limit=3)
    assert first["total"] == 9 and first["labelled"] == 0
    assert {it["source"] for it in first["items"]} == {"nas", "zotero", "extension"}
    assert store.genre_sample(con, limit=3) == first
    item = first["items"][0]
    assert item["opening"] and "summary" in item and item["genres"] == []
    store.set_genres(con, zot[0], ["paper"])
    store.set_genres(con, nas[0], None, skip=True)
    after = store.genre_sample(con, limit=50)
    assert after["total"] == 7 and after["labelled"] == 1 and after["skipped"] == 1
    assert zot[0] not in {it["id"] for it in after["items"]}
    done = store.genre_sample(con, state="labelled")
    assert [it["id"] for it in done["items"]] == [zot[0]]
    assert done["items"][0]["genres"] == ["informational", "paper"]
    assert ext in {it["id"] for it in after["items"]}
    with pytest.raises(ValueError):
        store.genre_sample(con, state="everything")


@pytest.fixture()
def client(data_dir: Path) -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_door_serves_the_genre_tab(client: TestClient) -> None:
    vocab = client.get("/genres").json()
    assert vocab["levels"][0]["name"] == "informational"
    assert vocab["subjects"]["levels"][0]["name"] == "society"
    d = client.post(
        "/ingest", json={"text": "An essay on collapse.", "title": "e"}
    ).json()
    doc_id = d["doc_id"]
    sample = client.get("/documents/genre-sample", params={"limit": 5}).json()
    assert [it["id"] for it in sample["items"]] == [doc_id]
    r = client.put(
        f"/doc/{doc_id}/genres", json={"genres": ["essay"], "subjects": ["ecology"]}
    )
    assert r.status_code == 200 and r.json()["genres"] == ["opinion", "essay"]
    assert r.json()["subjects"] == ["society", "ecology"]
    r = client.put(f"/doc/{doc_id}/genres", json={"genres": ["essay"]})
    assert r.status_code == 200 and r.json()["subjects"] == []
    assert (
        client.put(f"/doc/{doc_id}/genres", json={"genres": ["x"]}).status_code == 400
    )
    assert (
        client.put("/doc/99999/genres", json={"genres": ["essay"]}).status_code == 404
    )
    labelled = client.get(
        "/documents/genre-sample", params={"state": "labelled"}
    ).json()
    assert labelled["labelled"] == 1
    assert labelled["items"][0]["genres"] == ["opinion", "essay"]
    meta = client.get(f"/get/{doc_id}", params={"max_chars": 0}).json()["meta"]
    assert json.dumps(meta["genres"]) == (
        '[{"genre": "opinion", "p": 1.0}, {"genre": "essay", "p": 1.0}]'
    )


def test_a_yaml_file_that_is_not_a_module_is_never_composed(tmp_path: Path) -> None:
    """A file beside the modules that the running code has no name for
    (as genres.yaml was to a door started before it) is not read as a
    module: it would join the version string (2026-09-29, 883 edges)."""
    for f in Path(ontology.path()).glob("*.yaml"):
        (tmp_path / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "tomorrow.yaml").write_text(
        "version: 1\nlevels:\n  x: {description: y}\n", encoding="utf-8"
    )
    onto = ontology.load_dir(tmp_path)
    assert "tomorrow" not in onto.modules and "tomorrow" not in onto.version
    assert onto.version == ontology.current().version


def test_heal_takes_a_stray_module_out_of_a_version(con: sqlite3.Connection) -> None:
    """The live facts and open review items a door stamped with "+genres1"
    are restamped; a retired fact and a resolved item keep their history."""
    d = _doc(con, "A paper about reverberation and its delay networks.")
    right = ontology.current().version
    wrong = right + "+genres1"
    for name in ("a", "b"):
        con.execute("INSERT INTO entities (name, type) VALUES (?, 'concept')", (name,))
    a, b = [r[0] for r in con.execute("SELECT id FROM entities ORDER BY id")]
    for dst, valid_to in ((b, None), (a, "2026-09-29T00:00:00Z")):
        con.execute(
            "INSERT INTO edges (src, dst, rel, confidence, source_doc,"
            " ontology_version, producer, valid_to)"
            " VALUES (?, ?, 'mentions', 'EXTRACTED', ?, ?, 't', ?)",
            (a, dst, d, wrong, valid_to),
        )
    for resolved in (None, "2026-09-29T00:00:00Z"):
        con.execute(
            "INSERT INTO review_queue (source_doc, src, rel, dst, reason,"
            " ontology_version, resolved_at) VALUES (?, 'x', 'r', 'y', 'misfit', ?, ?)",
            (d, wrong, resolved),
        )
    con.commit()
    found = store.health(con, only=["stray-version-modules"])["ailments"][0]
    assert found["count"] == 2 and found["examples"][0]["right"] == right
    out = store.heal(con, only=["stray-version-modules"])
    assert out["stray-version-modules"] == {"found": 2, "repaired": 2}
    versions = [
        r[0] for r in con.execute("SELECT ontology_version FROM edges ORDER BY id")
    ]
    assert versions == [right, wrong]
    reviews = [
        r[0]
        for r in con.execute("SELECT ontology_version FROM review_queue ORDER BY id")
    ]
    assert reviews == [right, wrong]
    assert not store.health(con, only=["stray-version-modules"])["ailments"][0]["count"]


def test_a_models_labels_wait_for_a_person(con: sqlite3.Connection) -> None:
    """A model labels first (``by: claude``, a probability each); the "to
    check" list shows the least sure first; a person's save keeps the
    model's labels beside it, and a model never writes over a person."""
    sure = _doc(con, "TL072 datasheet: pinout, electrical characteristics.")
    unsure = _doc(con, "Notes on the collapse of complex societies, part two.")
    store.set_genres(
        con,
        sure,
        ["datasheet"],
        subjects=["electronics"],
        by="claude",
        p={"datasheet": 0.97, "electronics": 0.95},
        note="a part's datasheet",
    )
    store.set_genres(
        con,
        unsure,
        ["essay", "review"],
        subjects=["ecology"],
        by="claude",
        p={"essay": 0.55, "review": 0.4, "ecology": 0.9},
        note="essay or review",
    )
    check = store.genre_sample(con, state="check")
    assert [it["id"] for it in check["items"]] == [unsure, sure]
    assert check["to_check"] == 2 and check["labelled"] == 0
    first = check["items"][0]
    # a level a model did not name takes its surest label's probability
    assert first["p"] == {
        "opinion": 0.55,
        "essay": 0.55,
        "review": 0.4,
        "society": 0.9,
        "ecology": 0.9,
    }
    assert first["note"] == "essay or review" and first["genres_by"] == "claude"
    assert store.genre_sample(con)["total"] == 0  # labelled by someone: not open
    store.set_genres(con, unsure, ["review"], subjects=["ecology", "politics"])
    m = store.get_meta(con, unsure)
    assert m["genres_by"] == "human"
    assert m["genres"] == [
        {"genre": "opinion", "p": 1.0},
        {"genre": "review", "p": 1.0},
    ]
    assert m["genres_model"]["by"] == "claude"
    assert [g["genre"] for g in m["genres_model"]["genres"]] == [
        "opinion",
        "essay",
        "review",
    ]
    assert "genres_note" not in m
    with pytest.raises(ValueError, match="person labelled"):
        store.set_genres(con, unsure, ["essay"], by="claude")
    after = store.genre_sample(con, state="check")
    assert [it["id"] for it in after["items"]] == [sure]
    assert after["labelled"] == 1 and after["to_check"] == 1


def test_a_label_brings_its_level() -> None:
    g = ontology.genres()
    assert g.implied(["paper", "datasheet"]) == [
        "informational",
        "paper",
        "instructional",
        "datasheet",
    ]
    assert g.implied(["opinion"]) == ["opinion"]  # a level alone stays alone
    assert ontology.subjects().implied(["religion"]) == ["society", "religion"]
    assert {"article", "notes", "coursework", "lyrics", "score"} <= set(g.labels())


def test_browse_filters_by_genre_and_subject(con: sqlite3.Connection) -> None:
    a = _doc(con, "A paper on reverberation.")
    b = _doc(con, "An essay on collapse.")
    store.set_genres(con, a, ["paper"], subjects=["audio"])
    store.set_genres(
        con, b, ["essay"], subjects=["ecology"], by="claude", p={"essay": 0.8}
    )
    ids = lambda **kw: [d["id"] for d in store.list_documents(con, **kw)["items"]]
    assert ids(genre="paper") == [a]
    assert ids(genre="opinion") == [b]  # a level names what is under it
    assert ids(subject="technology") == [a]
    assert ids(genre="paper", subject="ecology") == []


def _facts(con: sqlite3.Connection, doc: int, typed: list[tuple[str, str]]) -> None:
    ids = []
    for name, etype in typed:
        cur = con.execute(
            "INSERT INTO entities (name, type) VALUES (?, ?)", (f"{name} {doc}", etype)
        )
        ids.append(cur.lastrowid)
    for dst in ids[1:]:
        con.execute(
            "INSERT INTO edges (src, dst, rel, confidence, source_doc,"
            " ontology_version, producer) VALUES (?, ?, 'mentions', 'EXTRACTED', ?,"
            " 'v', 't')",
            (ids[0], dst, doc),
        )
    con.commit()


def test_domain_rules_name_genres_subjects_and_facts(con: sqlite3.Connection) -> None:
    """Stage Z, step 5: a rule may name a genre or a subject (at a
    probability) or the module a document's facts mostly belong to; the
    dry run counts what the rules would assign and writes nothing."""
    sheet = _doc(con, "TL072 datasheet.")
    cake = _doc(con, "Apfelkuchen: Mehl, Äpfel, Butter.")
    essay = _doc(con, "An essay on collapse.")
    store.set_genres(con, sheet, ["datasheet"], subjects=["electronics"])
    store.set_genres(
        con, essay, ["essay"], subjects=["ecology"], by="claude", p={"essay": 0.4}
    )
    _facts(
        con,
        cake,
        [
            ("Apfelkuchen", "recipe"),
            ("Äpfel", "ingredient"),
            ("Mehl", "ingredient"),
            ("Oma", "person"),
        ],
    )
    counts = store.fact_modules(con, cake)
    assert counts["kitchen"] == 3 and counts["*"] == 4
    few = {"match": {"facts": "kitchen"}, "domains": ["kitchen"]}
    assert store.domains_dry_run(con, [few])["rules"][0]["count"] == 0  # 4 < min 5
    rules = [
        {"match": {"genre": "datasheet"}, "domains": ["electronics"]},
        {"match": {"facts": "kitchen", "min": 3}, "domains": ["kitchen"]},
        {"match": {"genre": "essay"}, "domains": ["research"]},  # 0.4 < 0.5
        {"match": {"genre": "essay", "p": 0.3}, "domains": ["research"]},
    ]
    before = {d: store.get_meta(con, d).get("domains") for d in (sheet, cake, essay)}
    dry = store.domains_dry_run(con, rules)
    assert [r["count"] for r in dry["rules"]] == [1, 1, 0, 1]
    assert dry["rules"][0]["examples"][0]["id"] == sheet
    assert {
        d: store.get_meta(con, d).get("domains") for d in (sheet, cake, essay)
    } == before
    with pytest.raises(ValueError):
        store.domains_dry_run(con, [{"match": {"genre": "x"}, "domains": ["nope"]}])
    store.assign_domains(con, rules)
    assert store.get_meta(con, sheet)["domains"] == ["electronics"]
    assert store.get_meta(con, cake)["domains"] == ["kitchen"]
    assert store.get_meta(con, essay)["domains"] == ["research"]


def test_a_rule_may_name_a_piece_of_the_documents_origin(
    con: sqlite3.Connection,
) -> None:
    """``origin`` matches the path a document had where it came from (the
    sender's ``meta.origin.path``), any piece of it, ignoring case."""
    help_file = _doc(
        con,
        "SinOsc.ar(freq, phase) a sine oscillator.",
        origin={"host": "nas", "path": "/home/me/SuperCollider/Help/SinOsc.help.rtf"},
    )
    other = _doc(
        con, "A letter.", origin={"host": "nas", "path": "/home/me/letters/a.doc"}
    )
    bare = _doc(con, "No origin at all.")
    rule = {"match": {"origin": [".HELP.rtf", "/quarks/"]}, "domains": ["studio"]}
    assert store.domains_dry_run(con, [rule])["rules"][0]["count"] == 1
    store.assign_domains(con, [rule])
    assert store.get_meta(con, help_file)["domains"] == ["studio"]
    assert "domains" not in store.get_meta(con, other)
    assert "domains" not in store.get_meta(con, bare)


def test_a_rule_with_a_key_the_door_does_not_know_is_refused(
    con: sqlite3.Connection,
) -> None:
    """An unknown ``match`` key is refused, never ignored: ignored, it made
    the rule match every document (2026-09-30)."""
    d = _doc(con, "Anything at all.")
    rule = {"match": {"orgin": ".help.rtf"}, "domains": ["studio"]}
    with pytest.raises(ValueError, match="orgin"):
        store.domains_dry_run(con, [rule])
    with pytest.raises(ValueError, match="orgin"):
        store.assign_domains(con, [rule])
    assert "domains" not in store.get_meta(con, d)
