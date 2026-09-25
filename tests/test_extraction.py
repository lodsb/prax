"""Graph extraction: prompt and schema from the ontology, the stub extractor,
apply() writing edges / review items / stamps, selection, provenance."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from typing import Any

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
    assert "authored_by (document -> organization, person)" in prompt
    assert f"ontology {onto.version}" in prompt
    assert "(a kind of person)" in prompt  # subtypes name their parent
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
    # the stub's one unmapped item has "?" for a target: the rules that run
    # after every extraction drop it, so nothing is left queued
    assert report.linked == len(result.triples) and report.queued == 0
    edges = store.traverse(con, result.triples[0].src, hops=1)
    assert len(edges) == len(result.triples)
    assert all(e["source_doc"] == doc_id and e["evidence"] for e in edges)
    assert all(e["ontology_version"] == ontology.current().version for e in edges)
    meta = store.get_meta(con, doc_id)
    assert meta["summary"].startswith("Stub summary")
    assert meta["extraction"]["linked"] == report.linked
    assert meta["extraction"]["ontology_version"] == ontology.current().version
    queue = store.list_review(con)
    assert queue == []  # the "?" item was dropped by the rules after the extraction
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


class _TooBig:
    """A served model whose slot is too small for the first prompt: the
    server's own refusal, with the numbers, then an answer once the text
    is cut to fit."""

    name = "fake@test"

    def __init__(self) -> None:
        self.calls: list[int] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, int]]:
        self.calls.append(len(user))
        if len(self.calls) == 1:
            raise RuntimeError(
                "http://127.0.0.1:8080/v1/chat/completions: HTTP 400:"
                ' {"error":{"code":400,"message":"request (9816 tokens) exceeds the'
                ' available context size (8192 tokens), try increasing it",'
                '"type":"exceed_context_size_error","n_prompt_tokens":9816,'
                '"n_ctx":8192}}'
            )
        reply = (
            "summary\tA short one.\n"
            "triple\tPaper X\tpaper\tabout\tgrains\tconcept\tEXTRACTED\tgrains\n"
        )
        return reply, {"input_tokens": 7000, "output_tokens": 20}


def test_a_prompt_the_slot_cannot_hold_is_cut_to_fit_and_asked_again(
    con: sqlite3.Connection,
) -> None:
    doc_id = store.ingest_text(con, "we study grains " * 800, title="Paper X")["doc_id"]
    runtime = _TooBig()
    ext = extraction.LocalExtractor(runtime)
    result = ext.extract(extraction.build_input(con, doc_id))
    assert [t.dst for t in result.triples] == ["grains"]
    first, second = runtime.calls
    # 9816 tokens did not fit 8192: the second prompt is shorter by at least
    # that ratio, with room to spare
    assert second < first * (8192 / 9816)
    assert result.usage.get("cut") == 1


def test_a_failed_extraction_is_remembered_and_not_retried_every_pass(
    con: sqlite3.Connection,
) -> None:
    """The door stamps an error result (meta.extraction_error) and the
    selection skips the document under that ontology version; a bump
    re-selects it, and the ailment lists it."""
    doc_id = store.ingest_text(con, "a long text " * 200, title="Stuck")["doc_id"]
    version = ontology.current().version
    assert store.select_for_extraction(con, ontology_version=version) == [doc_id]
    extraction.note_failure(
        con, doc_id, "RuntimeError: HTTP 400: exceeds", extractor="fake"
    )
    assert store.select_for_extraction(con, ontology_version=version) == []
    assert store.select_for_extraction(con, ontology_version="v-next") == [doc_id]
    failed = store.get_meta(con, doc_id)["extraction_error"]
    assert failed["extractor"] == "fake" and failed["ontology_version"] == version
    found = store.health(con, only=["extraction-failed"])["ailments"][0]
    assert found["count"] == 1 and found["examples"][0]["id"] == doc_id
    # a success clears the note
    extraction.apply(con, doc_id, extraction.Extraction(summary="s"), extractor="fake")
    assert "extraction_error" not in store.get_meta(con, doc_id)
    assert store.health(con, only=["extraction-failed"])["ailments"][0]["count"] == 0


def test_a_replacing_reread_unstamps_the_extraction_and_the_next_one_supersedes_it(
    con: sqlite3.Connection,
) -> None:
    """A parser that reads the mathematics replaces the text an extraction
    was made from: the stamp goes to the history, the extract step selects
    the document again, and the new reading retires the old one's edges.
    An annotating read (a figure's reading) adds to the text and leaves
    the stamp: every figure pass would re-extract the library otherwise."""
    from prax.parsers import queue

    doc_id = _doc(con)
    result = extraction.StubExtractor().extract(extraction.build_input(con, doc_id))
    extraction.apply(con, doc_id, result, extractor="stub", run="first")
    version = ontology.current().version
    assert doc_id not in store.select_for_extraction(con, ontology_version=version)
    old_edges = [
        e
        for e in store.traverse(con, result.triples[0].src, hops=1)
        if e["source_doc"] == doc_id
    ]
    assert old_edges and all(e["run"] == "first" for e in old_edges)

    def live(run: str) -> int:
        return con.execute(
            "SELECT count(*) FROM edges WHERE source_doc = ? AND run = ?"
            " AND valid_to IS NULL",
            (doc_id, run),
        ).fetchone()[0]

    assert live("first") == len(old_edges)

    # an annotating read: the stamp stays
    text = store.get_document(con, doc_id, max_chars=0)
    grown = (
        store.get_document(con, doc_id)["text"]
        + "\n\n*Figure, as read by m:* a chart.\n"
    )
    assert (
        queue.apply_parse(con, doc_id, stamp="figures/2", text=grown, keep_source=True)
        == "upgraded"
    )
    assert store.get_meta(con, doc_id).get("extraction")
    assert doc_id not in store.select_for_extraction(con, ontology_version=version)

    # a replacing read: the stamp goes, the document is selected again
    rewritten = (
        store.get_document(con, doc_id)["text"].replace("paper", "papyrus")
        + "\n\n$$E = mc^2 \\quad (1)$$\n"
    )
    assert (
        queue.apply_parse(con, doc_id, stamp="marker/2.0.0", text=rewritten)
        == "upgraded"
    )
    meta = store.get_meta(con, doc_id)
    assert "extraction" not in meta
    assert meta["extraction_history"][-1]["superseded_by"] == "marker/2.0.0"
    assert meta["extraction_history"][-1]["run"] == "first"
    assert meta["extraction_stale"] == {
        "extractor": "stub",
        "run": "first",
        "ontology_version": version,
        "text_read_by": "marker/2.0.0",
    }
    assert doc_id in store.select_for_extraction(con, ontology_version=version)
    assert text is not None
    # and before the never-extracted backlog, whatever its id
    fresh = store.ingest_text(con, "# Fresh\n\n" + "Never extracted. " * 40, title="F")
    order = store.select_for_extraction(con, ontology_version=version)
    assert order.index(doc_id) < order.index(fresh["doc_id"])

    # the next extraction by the same producer supersedes the old reading
    again = extraction.StubExtractor().extract(extraction.build_input(con, doc_id))
    report = extraction.apply(con, doc_id, again, extractor="stub", run="second")
    assert report.retired >= len(old_edges)
    assert report.linked == len(again.triples) and report.existing == 0
    now = [
        e
        for e in store.traverse(con, again.triples[0].src, hops=1)
        if e["source_doc"] == doc_id
    ]
    assert now and all(e["run"] == "second" for e in now)
    assert live("first") == 0 and live("second") == len(again.triples)
    meta = store.get_meta(con, doc_id)
    assert meta["extraction"]["run"] == "second" and "extraction_stale" not in meta
    assert doc_id not in store.select_for_extraction(con, ontology_version=version)
    # another producer's reading is not what a text upgrade supersedes
    assert (
        extraction.apply(con, doc_id, again, extractor="other", run="o1").retired == 0
    )


def test_the_input_is_budgeted_in_bytes_as_well_as_characters(
    con: sqlite3.Connection,
) -> None:
    """Extraction had the same character-only cap the sections pass had:
    12,000 characters of Arabic is 23,834 tokens against a 16,384-token
    slot (2026-09-25)."""
    arabic = "هذا نص عربي طويل جدا للاختبار وفيه كلمات كثيرة. " * 600
    doc = store.ingest_text(con, arabic, title="مقال")
    got = extraction.build_input(con, doc["doc_id"])
    assert len(got.text.encode("utf-8")) <= extraction.INPUT_BYTES + 200
    assert got.text.strip()

    latin = "The quick brown fox jumps over the lazy dog. " * 600
    doc = store.ingest_text(con, latin, title="A paper")
    got = extraction.build_input(con, doc["doc_id"])
    # a Latin document is unaffected: it reaches the character cap first
    assert len(got.text) > extraction.INPUT_CHARS / 2
