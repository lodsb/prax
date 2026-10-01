"""Per-document domain sets: setting them, assigning them by rule,
extracting against the document's own subset of the ontology, re-running
per domain, and the doors."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import config, store
from prax.graph import extraction, ontology

FAMILY = """
module: family
version: 1
requires: [core]
entity_types:
  relative: {parent: person, description: a family member as named}
relation_types:
  parent_of: {domain: [person], range: [person], description: parent and child}
  depicts:
    domain: [document]
    range: [person, place, event]
    description: shown in the picture
"""


@pytest.fixture()
def three_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "onto"
    d.mkdir()
    # the three the domain tests reason about, plus one of their own: the
    # repo's other modules are tested where they belong and would only make
    # these assertions move whenever one is added
    for name in ("core.yaml", "research.yaml", "studio.yaml"):
        f = Path(config.ONTOLOGY_PATH) / name
        (d / name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (d / "family.yaml").write_text(FAMILY, encoding="utf-8")
    monkeypatch.setenv("PRAX_ONTOLOGY", str(d))
    assert set(ontology.current().modules) == {"core", "research", "studio", "family"}
    return d


def test_set_add_remove(con: sqlite3.Connection, three_modules: Path) -> None:
    a = store.ingest_text(con, "text " * 40, title="A photo of grandma")["doc_id"]
    assert store.document_domains(con, a) is None
    assert store.set_domains(con, a, ["family"]) == ["family"]
    assert store.get_meta(con, a)["domains_by"] == "human"
    assert store.add_domain(con, a, "research") == ["family", "research"]
    assert store.add_domain(con, a, "research") == ["family", "research"]  # once
    assert store.remove_domain(con, a, "family") == ["research"]
    assert store.remove_domain(con, a, "research") is None  # back to every module
    with pytest.raises(ValueError, match="unknown domain"):
        store.set_domains(con, a, ["core"])
    with pytest.raises(ValueError, match="unknown domain"):
        store.add_domain(con, a, "cooking")
    with pytest.raises(KeyError):
        store.document_domains(con, 999)


def test_assign_by_rules(con: sqlite3.Connection, three_modules: Path) -> None:
    fam = store.ingest_text(con, "x " * 40, title="Grandma 1962")["doc_id"]
    meta = store.get_meta(con, fam)
    meta.update(source="zotero", collections=["Family", "Photos"])
    store.set_meta(con, fam, meta)
    paper = store.ingest_text(con, "y " * 40, title="A paper")["doc_id"]
    meta = store.get_meta(con, paper)
    meta.update(source="zotero", collections=["analysis"])
    store.set_meta(con, paper, meta)
    byhand = store.ingest_text(con, "z " * 40, title="Mine")["doc_id"]
    store.set_domains(con, byhand, ["research"])  # a person's choice stays
    rules = [
        {"match": {"collection": "family"}, "domains": ["family", "research"]},
        {"match": {"source": "zotero"}, "domains": ["research"]},
    ]
    dry = store.assign_domains(con, rules, commit=False)
    assert dry == {"unmatched": 0, "rule 0": 1, "rule 1": 1}
    assert store.document_domains(con, fam) is None
    store.assign_domains(con, rules)
    assert store.document_domains(con, fam) == ["family", "research"]
    assert store.get_meta(con, fam)["domains_by"] == "rule"
    assert store.document_domains(con, paper) == ["research"]
    assert store.document_domains(con, byhand) == ["research"]
    # a second pass touches nothing unless forced; force still spares hands
    assert store.assign_domains(con, [{"domains": ["family"]}]) == {"unmatched": 0}
    store.assign_domains(con, [{"domains": ["family"]}], force=True)
    assert store.document_domains(con, paper) == ["family"]
    assert store.document_domains(con, byhand) == ["research"]
    with pytest.raises(ValueError, match="unknown domain"):
        store.assign_domains(con, [{"domains": ["cooking"]}])
    assert store.documents_in_domain(con, "family") == [fam, paper]


def test_extraction_uses_the_documents_subset(
    con: sqlite3.Connection, three_modules: Path
) -> None:
    fam = store.ingest_text(
        con, "A picture of grandma in Lisbon. " * 20, title="Grandma 1962"
    )["doc_id"]
    store.set_domains(con, fam, ["family"])
    inp = extraction.build_input(con, fam)
    assert inp.domains == ["family"] and "Domains: family" in inp.header
    sub = inp.ontology()
    assert set(sub.modules) == {"core", "family"} and "paper" not in sub.types
    assert extraction.self_types(sub) == ("document",)
    # the Claude extractor builds the prompt and schema for that subset
    ext = extraction.ClaudeExtractor(model="claude-test", client=object())
    params = ext.params(inp)
    system = params["system"][0]["text"]
    assert "The document itself is a document entity" in system
    assert "relative" in system and "parent_of" in system and "advised_by" not in system
    enum = params["output_config"]["format"]["schema"]["properties"]["triples"][
        "items"
    ]["properties"]["src"]["properties"]["type"]["enum"]
    assert "relative" in enum and "paper" not in enum
    # a research document gets the research prompt from the same extractor
    paper = store.ingest_text(con, "A paper about NMF. " * 20, title="NMF paper")[
        "doc_id"
    ]
    store.set_domains(con, paper, ["research"])
    system2 = ext.params(extraction.build_input(con, paper))["system"][0]["text"]
    assert "paper entity" in system2 and "parent_of" not in system2
    assert set(ext._prompts) == {"core3+family1", "core3+research9"}
    # apply validates against the subset and stamps its version
    ex = extraction.Extraction(
        summary="s",
        triples=[
            extraction.Triple(
                "Grandma 1962",
                "document",
                "depicts",
                "Grandma",
                "relative",
                "EXTRACTED",
                "e",
            ),
            extraction.Triple(
                "Grandma 1962", "paper", "about", "nmf", "concept", "EXTRACTED", "e"
            ),
        ],
    )
    rep = extraction.apply(con, fam, ex, extractor="stub")
    assert rep.linked == 1 and rep.queued == 1  # "paper" is not in the family subset
    assert store.get_meta(con, fam)["extraction"]["ontology_version"] == "core3+family1"


def test_rereading_under_another_subset_retires_the_earlier_reading(
    con: sqlite3.Connection, three_modules: Path
) -> None:
    doc = store.ingest_text(con, "a synth manual " * 30, title="Synth Manual")["doc_id"]
    first = extraction.Extraction(
        summary="s",
        triples=[
            extraction.Triple(
                "Synth Manual",
                "paper",
                "about",
                "synthesis",
                "concept",
                "EXTRACTED",
                "e",
            ),
        ],
    )
    extraction.apply(con, doc, first, extractor="local")
    other = extraction.Extraction(
        summary="s",
        triples=[
            extraction.Triple(
                "Synth Manual",
                "paper",
                "about",
                "synthesis",
                "concept",
                "EXTRACTED",
                "e",
            ),
        ],
    )
    extraction.apply(con, doc, other, extractor="sonnet")  # another producer
    live = lambda: con.execute(
        "SELECT producer, ontology_version FROM edges WHERE source_doc = ?"
        " AND valid_to IS NULL ORDER BY id",
        (doc,),
    ).fetchall()
    whole = ontology.current().version  # no domain set: the whole ontology
    assert [tuple(r) for r in live()] == [("local", whole)]  # sonnet's: existing
    store.set_domains(con, doc, ["family"])
    second = extraction.Extraction(
        summary="s",
        triples=[
            extraction.Triple(
                "Synth Manual",
                "document",
                "depicts",
                "Grandma",
                "relative",
                "EXTRACTED",
                "e",
            ),
        ],
    )
    rep = extraction.apply(con, doc, second, extractor="local")
    assert rep.retired == 1 and rep.linked == 1
    assert [tuple(r) for r in live()] == [("local", "core3+family1")]
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NOT NULL",
            (doc,),
        ).fetchone()[0]
        == 1
    )  # history kept


def test_select_for_extraction_knows_subset_versions(
    con: sqlite3.Connection, three_modules: Path
) -> None:
    fam = store.ingest_text(con, "f " * 300, title="F")["doc_id"]
    store.set_domains(con, fam, ["family"])
    res = store.ingest_text(con, "r " * 300, title="R")["doc_id"]
    onto = ontology.current()
    extraction.apply(con, fam, extraction.Extraction(summary="s"), extractor="stub")
    # stamped core1+family1: not the whole ontology's version, but done for its domains
    assert store.select_for_extraction(con, ontology_version=onto.version) == [fam, res]
    assert store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto
    ) == [res]
    assert (
        store.select_for_extraction(
            con, ontology_version=onto.version, onto=onto, domain="family"
        )
        == []
    )
    store.add_domain(con, res, "family")
    assert store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto, domain="family"
    ) == [res]


def test_a_domain_change_makes_the_extraction_stale(
    con: sqlite3.Connection, three_modules: Path
) -> None:
    """The lens changed under a reading: the stamp goes to the history,
    the document goes first in the extract queue, the new reading
    retires the old; the same set again, or one of the same version,
    changes nothing."""
    later = store.ingest_text(con, "later " * 300, title="Later")["doc_id"]
    doc = store.ingest_text(con, "f " * 300, title="F")["doc_id"]
    store.set_domains(con, doc, ["family"])
    onto = ontology.current()
    triple = extraction.Triple(
        "Grandma", "person", "parent_of", "Mother", "person", "EXTRACTED", "F"
    )
    first = extraction.apply(
        con,
        doc,
        extraction.Extraction(summary="s", triples=[triple]),
        extractor="stub",
        run="r1",
    )
    assert first.linked == 1
    assert store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto
    ) == [later]
    store.set_domains(con, doc, ["family"])  # the same set: nothing
    assert "extraction_stale" not in store.get_meta(con, doc)
    store.set_domains(con, doc, ["research"])
    meta = store.get_meta(con, doc)
    assert "extraction" not in meta
    assert meta["extraction_stale"]["domains_changed"] is True
    assert meta["extraction_history"][-1]["superseded_by"] == "domains"
    # first in the queue, before the never-extracted document
    assert store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto
    ) == [
        doc,
        later,
    ]
    # a person's change is a reading asked for: a worker scoped to the
    # captures takes this upload too
    assert meta["extraction_stale"]["requested"]["by"] == "human"
    assert store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto, sources=("capture",)
    ) == [doc]
    # the next reading retires the old edges (history kept) and clears the mark
    extraction.apply(
        con, doc, extraction.Extraction(summary="t"), extractor="stub", run="r2"
    )
    meta = store.get_meta(con, doc)
    assert "extraction_stale" not in meta
    live = con.execute(
        "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NULL", (doc,)
    ).fetchone()[0]
    kept = con.execute(
        "SELECT count(*) FROM edges WHERE source_doc = ?", (doc,)
    ).fetchone()[0]
    assert (live, kept) == (0, 1)


def test_search_domain_filter(con: sqlite3.Connection, three_modules: Path) -> None:
    fam = store.ingest_text(con, "reverb at the family party " * 20, title="Party")[
        "doc_id"
    ]
    store.set_domains(con, fam, ["family"])
    res = store.ingest_text(con, "reverb in the concert hall " * 20, title="Hall")[
        "doc_id"
    ]
    store.set_domains(con, res, ["research"])
    both = store.ingest_text(con, "reverb everywhere " * 20, title="Both")["doc_id"]
    ids = lambda hits: {h["doc_id"] for h in hits}
    assert ids(store.search(con, "reverb")) == {fam, res, both}
    assert ids(store.search(con, "reverb", domain="family")) == {fam, both}
    assert ids(store.search(con, "reverb", domain="research")) == {res, both}
    with pytest.raises(ValueError, match="unknown domain"):
        store.search(con, "reverb", domain="cooking")


@pytest.fixture()
def client(three_modules: Path) -> TestClient:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_api_domains(client: TestClient) -> None:
    a = client.post("/ingest", json={"text": "alpha " * 50, "title": "A"}).json()[
        "doc_id"
    ]
    r = client.get(f"/doc/{a}/domains").json()
    assert r["domains"] is None and r["modules"] == ["family", "research", "studio"]
    assert client.put(f"/doc/{a}/domains", json={"domains": ["family"]}).json() == {
        "domains": ["family"],
        "reread": False,  # nothing extracted yet
    }
    assert client.post(f"/doc/{a}/domains/research").json() == {
        "domains": ["family", "research"]
    }
    assert client.delete(f"/doc/{a}/domains/family").json() == {"domains": ["research"]}
    assert (
        client.put(f"/doc/{a}/domains", json={"domains": ["cooking"]}).status_code
        == 400
    )
    assert client.get("/doc/999/domains").status_code == 404
    assert client.put(f"/doc/{a}/domains", json={"domains": None}).json() == {
        "domains": None,
        "reread": False,
    }
    hits = client.get("/search", params={"q": "alpha", "domain": "family"}).json()
    assert hits and hits[0]["doc_id"] == a  # no set: in every module


def test_browse_and_similar_take_a_domain(
    con: sqlite3.Connection, three_modules: Path
) -> None:
    fam = store.ingest_text(con, "reverb at the family party " * 20, title="Party")[
        "doc_id"
    ]
    res = store.ingest_text(con, "reverb in a concert hall " * 20, title="Hall")[
        "doc_id"
    ]
    free = store.ingest_text(con, "reverb everywhere " * 20, title="Free")["doc_id"]
    store.set_domains(con, fam, ["family"])
    store.set_domains(con, res, ["research"])
    listed = store.list_documents(con, domain="family")
    assert {d["id"] for d in listed["items"]} == {fam, free}  # unset is everywhere
    assert listed["total"] == 2
    assert {d["id"] for d in store.list_documents(con)["items"]} == {fam, res, free}


def test_the_door_browses_and_places_within_a_domain(three_modules: Path) -> None:
    from fastapi.testclient import TestClient

    from prax.api import app

    with TestClient(app) as client:
        a = client.post("/ingest", json={"text": "x " * 40, "title": "A"}).json()
        b = client.post("/ingest", json={"text": "y " * 40, "title": "B"}).json()
        client.put(f"/doc/{a['doc_id']}/domains", json={"domains": ["family"]})
        client.put(f"/doc/{b['doc_id']}/domains", json={"domains": ["research"]})
        ids = {
            d["id"]
            for d in client.get("/documents", params={"domain": "family"}).json()[
                "items"
            ]
        }
        assert ids == {a["doc_id"]}
        ctx = client.get(
            f"/doc/{a['doc_id']}/context", params={"domain": "family"}
        ).json()
        assert "similar" in ctx  # the filter is accepted; vectors decide the rest


def test_the_door_says_when_a_domain_change_means_a_reread(client: TestClient) -> None:
    a = client.post("/ingest", json={"text": "alpha " * 50, "title": "A"}).json()[
        "doc_id"
    ]
    con = store.connect()
    extraction.apply(con, a, extraction.Extraction(summary="s"), extractor="stub")
    assert client.put(f"/doc/{a}/domains", json={"domains": ["family"]}).json() == {
        "domains": ["family"],
        "reread": True,
    }
    assert client.put(f"/doc/{a}/domains", json={"domains": ["family"]}).json() == {
        "domains": ["family"],
        "reread": True,  # still waiting for the pass
    }


def test_which_documents_belong_to_a_domain_is_said_once(
    con: sqlite3.Connection,
) -> None:
    """``holds_domain`` and ``domain_clause`` agree: a set holding one of
    the names belongs, no set (or an empty one) belongs only when asked,
    and an empty set is stored as no set."""
    assert store.holds_domain('["studio"]', {"studio", "computing"}, unset=False)
    assert not store.holds_domain('["kitchen"]', {"studio"}, unset=True)
    assert store.holds_domain(None, {"studio"}, unset=True)
    assert not store.holds_domain("[]", {"studio"}, unset=False)
    assert store.decode_domains("[]") is None
    sheet = int(store.ingest_text(con, "A datasheet.", title="sheet")["doc_id"])
    loose = int(store.ingest_text(con, "Nothing set.", title="loose")["doc_id"])
    both = int(store.ingest_text(con, "A synth recipe.", title="both")["doc_id"])
    store.set_domains(con, sheet, ["studio"])
    store.set_domains(con, loose, [])
    store.set_domains(con, both, ["kitchen", "studio"])
    assert "domains" not in store.get_meta(con, loose)

    def members(names: list[str], unset: bool) -> set[int]:
        clause, args = store.domain_clause(con, names, unset=unset)
        rows = con.execute(f"SELECT d.id FROM documents d WHERE 1 = 1{clause}", args)
        return {int(r[0]) for r in rows} & {sheet, loose, both}

    assert members(["studio"], False) == {sheet, both}
    assert members(["kitchen"], False) == {both}
    assert members(["studio"], True) == {sheet, loose, both}
    assert members(["research"], False) == set()
    assert members([], True) == {loose}
    assert members([], False) == set()


def test_the_rules_are_applied_again_to_named_documents(
    client: TestClient, three_modules: Path
) -> None:
    """``POST /domains/assign`` runs prax.yaml's rules over the named
    documents, over a set a rule gave them, and leaves a person's set."""

    def note(i: int) -> int:
        body = {"text": f"note {i} " * 40, "title": f"N{i}"}
        return int(client.post("/ingest", json=body).json()["doc_id"])

    ids = [note(i) for i in range(3)]
    client.put(f"/doc/{ids[2]}/domains", json={"domains": ["research"]})  # a person's
    (config.data_dir() / "prax.yaml").write_text(
        "domains:\n  - domains: [family]\n", encoding="utf-8"
    )
    got = client.post("/domains/assign", json={"ids": ids[1:]}).json()
    assert got == {"rule 0": 1, "unmatched": 0}
    domains = [client.get(f"/doc/{i}/domains").json()["domains"] for i in ids]
    assert domains == [None, ["family"], ["research"]]
