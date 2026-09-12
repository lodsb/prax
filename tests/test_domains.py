"""Per-document domain sets: setting them, assigning them by rule,
extracting against the document's own subset of the ontology, re-running
per domain, and the doors."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import config, extraction, ontology, store

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
    assert set(ext._prompts) == {"core1+family1", "core1+research5"}
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
    assert store.get_meta(con, fam)["extraction"]["ontology_version"] == "core1+family1"


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
    assert [tuple(r) for r in live()] == [("local", "core1+family1")]
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
        "domains": ["family"]
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
        "domains": None
    }
    hits = client.get("/search", params={"q": "alpha", "domain": "family"}).json()
    assert hits and hits[0]["doc_id"] == a  # no set: in every module
