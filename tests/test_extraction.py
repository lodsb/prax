"""Graph extraction: prompt and schema from the ontology, the stub extractor,
apply() writing edges / review items / stamps, selection, provenance."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from prax import extraction, ontology, store

PAPER = (
    "# Correlated Tensor Factorization for Audio Source Separation\n\n"
    "This paper presents an extension of nonnegative matrix factorization for"
    " audio source separation based on full covariance modeling. Nonnegative"
    " matrix factorization is widely used; our correlated tensor factorization"
    " models correlations across time-frequency bins.\n\n"
    "**Figure 1:** a schematic.\n\n"
    "```\n"
    + "\n".join(f"code listing line {i} that must not be sent" for i in range(8))
    + "\n```\n"
)
META = {
    "creators": [{"name": "Kazuyoshi Yoshii"}],
    "date": "2018-04",
    "doi": "10.1109/ICASSP.2018.8461434",
    "abstract": "An ultimate extension of NMF.",
    "fields": {"proceedingsTitle": "ICASSP 2018"},
}


def _doc(con: sqlite3.Connection) -> int:
    return store.ingest_text(
        con,
        PAPER,
        title="Correlated Tensor Factorization for Audio Source Separation",
        meta=META,
    )["doc_id"]


# ------------------------------------------------------------ prompt/schema


def test_prompt_and_schema_follow_the_ontology() -> None:
    onto = ontology.current()
    prompt = extraction.system_prompt(onto)
    for name in onto.entity_types:
        assert f"- {name}:" in prompt
    assert "authored_by (paper -> author)" in prompt
    assert f"ontology version {onto.version}" in prompt
    schema = extraction.output_schema(onto)
    triple = schema["properties"]["triples"]["items"]
    assert set(triple["properties"]["src"]["properties"]["type"]["enum"]) == set(
        onto.entity_types
    )
    assert set(triple["properties"]["rel"]["enum"]) == set(onto.relations)
    assert schema["additionalProperties"] is False


def test_build_input_has_header_and_skips_figures_and_code(
    con: sqlite3.Connection,
) -> None:
    doc_id = _doc(con)
    inp = extraction.build_input(con, doc_id, max_chars=10_000)
    assert inp.header.startswith("Title: Correlated Tensor Factorization")
    assert "Authors: Kazuyoshi Yoshii" in inp.header
    assert "Venue: ICASSP 2018" in inp.header and "DOI: 10.1109" in inp.header
    assert "Abstract: An ultimate extension" in inp.header
    assert "full covariance modeling" in inp.text
    assert "Figure 1" not in inp.text and "code listing" not in inp.text
    short = extraction.build_input(con, doc_id, max_chars=80)
    assert len(short.text) <= 80
    with pytest.raises(KeyError):
        extraction.build_input(con, 999)


def test_build_input_appends_the_closing_sections(con: sqlite3.Connection) -> None:
    body = "\n\n".join(
        [
            "# Intro",
            "Opening paragraph. " * 30,
            "## Method",
            "Method detail. " * 60,
            "## Experiments",
            "Numbers. " * 60,
            "## Conclusion",
            "We showed that the thing works. " * 5,
            "## References",
            "[1] Someone, somewhere. " * 10,
        ]
    )
    doc_id = store.ingest_text(con, body, title="Long paper")["doc_id"]
    inp = extraction.build_input(con, doc_id, max_chars=700, tail_chars=200)
    head, _, tail = inp.text.partition(extraction.TAIL_MARK)
    assert len(head) <= 700 and "Opening paragraph" in head
    assert "We showed that the thing works" in tail and "Numbers." not in tail
    assert len(tail) <= 200 and "Someone, somewhere" not in inp.text
    # a short document has no tail: everything fits in the head
    whole = extraction.build_input(con, doc_id, max_chars=100_000)
    assert extraction.TAIL_MARK not in whole.text and "Someone" in whole.text


# ------------------------------------------------------------------ apply


def test_stub_extract_and_apply(con: sqlite3.Connection) -> None:
    doc_id = _doc(con)
    ext = extraction.StubExtractor()
    result = ext.extract(extraction.build_input(con, doc_id))
    rels = {(t.rel, t.dst_type) for t in result.triples}
    assert ("authored_by", "author") in rels and ("published_in", "venue") in rels
    assert ("about", "concept") in rels and result.unmapped
    report = extraction.apply(con, doc_id, result, extractor="stub")
    assert report.linked == len(result.triples) and report.queued == 1
    edges = store.traverse(con, result.triples[0].src, hops=1)
    assert len(edges) == len(result.triples)
    assert all(e["source_doc"] == doc_id and e["evidence"] for e in edges)
    assert all(e["ontology_version"] == ontology.current().version for e in edges)
    meta = store.get_meta(con, doc_id)
    assert meta["summary"].startswith("Stub summary")
    assert meta["extraction"]["linked"] == report.linked
    assert meta["extraction"]["ontology_version"] == ontology.current().version
    queue = store.list_review(con)
    assert len(queue) == 1 and queue[0]["reason"].startswith("unmapped")
    # a second apply adds nothing (edges exist), stamps again
    again = extraction.apply(con, doc_id, result, extractor="stub")
    assert again.linked == 0 and again.existing == len(result.triples)


def test_misfit_triples_go_to_review_not_graph(con: sqlite3.Connection) -> None:
    doc_id = _doc(con)
    bad = extraction.Extraction(
        triples=[
            extraction.Triple(
                "x", "paper", "authored_by", "y", "concept", "EXTRACTED", "q"
            ),
            extraction.Triple("x", "planet", "orbits", "y", "planet", "EXTRACTED", "q"),
            extraction.Triple("", "paper", "about", "y", "concept", "EXTRACTED", "q"),
            extraction.Triple("x", "paper", "about", "y", "concept", "MAYBE", "q"),
        ]
    )
    report = extraction.apply(con, doc_id, bad, extractor="stub")
    assert report.linked == 0 and report.queued == 2 and report.rejected == 2
    assert con.execute("SELECT count(*) FROM edges").fetchone()[0] == 0
    items = store.list_review(con)
    assert {i["reason"].split(" ")[0] for i in items} <= {"'authored_by'", "unknown"}
    store.resolve_review(con, items[0]["id"], "dropped")
    assert len(store.list_review(con)) == 1
    assert len(store.list_review(con, open_only=False)) == 2
    with pytest.raises(ValueError):
        store.resolve_review(con, items[1]["id"], "whatever")


def test_selection_follows_the_ontology_version(con: sqlite3.Connection) -> None:
    a = _doc(con)
    b = store.ingest_text(con, "another text", title="b")["doc_id"]
    store.register(con, b"%PDF", mime="application/pdf")  # not indexed: excluded
    version = ontology.current().version
    assert store.select_for_extraction(con, ontology_version=version) == [a, b]
    extraction.apply(con, a, extraction.Extraction(summary="s"), extractor="stub")
    assert store.select_for_extraction(con, ontology_version=version) == [b]
    assert store.select_for_extraction(con, ontology_version="v-next") == [a, b]
    assert store.select_for_extraction(con, ontology_version=version, limit=1) == [b]
    # "another text" is 12 characters: nothing to extract
    assert store.select_for_extraction(
        con, ontology_version="v-next", min_chars=100
    ) == [a]


# ------------------------------------------------------------ claude path


def test_claude_extractor_params_and_response_parsing(con: sqlite3.Connection) -> None:
    doc_id = _doc(con)
    inp = extraction.build_input(con, doc_id)
    ext = extraction.ClaudeExtractor(
        model="claude-opus-5", effort="low", client=object()
    )
    params = ext.params(inp)
    assert params["model"] == "claude-opus-5"
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["output_config"]["effort"] == "low"
    assert params["messages"][0]["content"].startswith("Title:")

    payload = {
        "summary": "A paper.",
        "triples": [
            {
                "src": {"name": inp.title, "type": "paper"},
                "rel": "about",
                "dst": {"name": "source separation", "type": "concept"},
                "confidence": "EXTRACTED",
                "evidence": "audio source separation",
            }
        ],
        "unmapped": [],
    }
    message = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        usage=SimpleNamespace(
            input_tokens=1200,
            output_tokens=90,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=0,
        ),
    )
    out = extraction.ClaudeExtractor.from_message(message)
    assert out.triples[0].dst == "source separation" and out.summary == "A paper."
    assert out.usage["cache_read_input_tokens"] == 800
    assert 0 < extraction.cost_usd("claude-opus-5", out.usage) < 0.01
    refused = SimpleNamespace(
        stop_reason="refusal", content=[], usage=None, stop_details="x"
    )
    with pytest.raises(RuntimeError, match="refused"):
        extraction.ClaudeExtractor.from_message(refused)


def test_effort_only_where_supported(con: sqlite3.Connection) -> None:
    doc = extraction.build_input(con, _doc(con))
    opus = extraction.ClaudeExtractor(model="claude-opus-5", client=object())
    assert opus.params(doc)["output_config"]["effort"] == "medium"
    haiku = extraction.ClaudeExtractor(model="claude-haiku-4-5", client=object())
    assert "effort" not in haiku.params(doc)["output_config"]
    assert "format" in haiku.params(doc)["output_config"]


def test_current_extractor_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAX_EXTRACT", "stub")
    assert isinstance(extraction.current(), extraction.StubExtractor)
    monkeypatch.delenv("PRAX_EXTRACT")
    monkeypatch.setenv("PRAX_EXTRACT_MODEL", "claude-sonnet-5")
    ext = extraction.current()
    assert (
        isinstance(ext, extraction.ClaudeExtractor) and ext.model == "claude-sonnet-5"
    )


def test_apply_drops_author_year_citations_from_unmapped(
    con: sqlite3.Connection,
) -> None:
    doc_id = store.ingest_text(con, "text " * 50, title="A paper")["doc_id"]
    ex = extraction.Extraction(
        summary="s",
        unmapped=[
            {
                "src": "A paper",
                "rel": "cites",
                "dst": "Turner & Sahani, 2014",
                "reason": "r",
            },
            {"src": "A paper", "rel": "cites", "dst": "Solin et al.", "reason": "r"},
            {"src": "A paper", "rel": "cites", "dst": "Smith and Goto", "reason": "r"},
            {"src": "A paper", "rel": "plans", "dst": "a roadmap item", "reason": "r"},
        ],
    )
    rep = extraction.apply(con, doc_id, ex, extractor="stub")
    assert rep.queued == 1 and rep.rejected == 3
    assert store.count_review(con) == 1


def test_unmapped_noise_rules() -> None:
    noise = extraction.unmapped_is_noise
    assert noise({"rel": "cites", "dst": "[5]", "reason": "no title"})
    assert noise({"rel": "cites", "dst": "Solin et al., 2018", "reason": "x"})
    assert noise(
        {"rel": "uses", "dst": "MUSDB18", "reason": "The document does not mention"}
    )
    assert noise({"rel": "published_in", "dst": "J", "reason": "likely published in"})
    assert not noise({"rel": "plans", "dst": "a roadmap item", "reason": "stated"})
    assert not noise(
        {"rel": "cites", "dst": "A Dictionary of Musical Themes", "reason": "r"}
    )
