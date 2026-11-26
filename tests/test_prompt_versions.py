"""Provenance a downstream reader needs (AV, the BWS survey of 2026-11-17):
every ontology stamp names its modules (migration 72), and an extraction
edge's producer names the version of the prompt its model was given."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from prax import store
from prax.graph import extraction, ontology
from prax.store.base import schema

E = store.Edge

# The fingerprint of each prompt version: the instructions for a fixed
# one-module ontology, both output formats, the optional rules off. A change
# to what the prompt says fails here; bump ``extraction.PROMPT_VERSION`` and
# add its fingerprint (the failure prints it).
PROMPTS = {1: "2953e69f602c", 2: "fd47f7b8f7d0"}

_FIXED = """
module: main
version: 1
entity_types:
  paper:
  tool:
relation_types:
  uses: {domain: [paper], range: [tool], description: what it uses}
"""


def _fingerprint(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("PRAX_EXTRACT_STANDARD", "0")
    monkeypatch.setenv("PRAX_EXTRACT_DATES", "0")
    monkeypatch.setenv("PRAX_GRAPH_LANGUAGE", "en")
    onto = ontology.compose([ontology.parse_module(_FIXED)])
    # from version 2 also what a document is asked by its genre (genres.yaml's
    # reading) and a part of a reading in full; version 1 had neither
    later = extraction.PROMPT_VERSION >= 2
    asks: list[str | None] = [None]
    if later:
        asks += [name for name, _ in ontology.genres().asks]
    text = "\n\n".join(
        extraction.system_prompt(onto, output=o, asks=a, part=part)
        for o in ("json", "lines")
        for a in asks
        for part in ((False, True) if later else (False,))
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def test_a_change_to_the_prompt_comes_with_a_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    got = _fingerprint(monkeypatch)
    assert PROMPTS.get(extraction.PROMPT_VERSION) == got, (
        f"the extraction prompt changed: bump extraction.PROMPT_VERSION and"
        f" add {{{extraction.PROMPT_VERSION + 1}: {got!r}}} to PROMPTS"
    )


def test_a_producer_names_its_prompt_and_gives_it_back() -> None:
    assert store.prompted("qwen@127.0.0.1:8080", 3) == "qwen@127.0.0.1:8080/prompt3"
    assert store.prompted("claude-opus-5", None) == "claude-opus-5"
    assert store.reader_of("qwen@127.0.0.1:8080/prompt12") == (
        "qwen@127.0.0.1:8080",
        12,
    )
    assert store.reader_of("typing-rules/located_in") == (
        "typing-rules/located_in",
        None,
    )


def _reading(rel_dst: str) -> extraction.Extraction:
    return extraction.Extraction(
        summary="s",
        triples=[
            extraction.Triple(
                "Synth Manual", "paper", "uses", rel_dst, "tool", "EXTRACTED", "e"
            )
        ],
    )


def _live(con: sqlite3.Connection, doc: int) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT producer FROM edges WHERE source_doc = ? AND valid_to IS NULL"
            " ORDER BY id",
            (doc,),
        )
    ]


def test_an_extraction_edge_names_its_prompt_and_a_rereading_supersedes_it(
    con: sqlite3.Connection,
) -> None:
    doc = store.ingest_text(con, "a synth manual " * 30, title="Synth Manual")["doc_id"]
    extraction.apply(con, doc, _reading("Max"), extractor="local@h", prompt=1)
    assert _live(con, doc) == ["local@h/prompt1"]
    assert store.get_meta(con, doc)["extraction"]["prompt"] == 1
    # a person asks for a new reading; the model is the same, the prompt newer
    store.request_extraction(con, doc, by="test")
    rep = extraction.apply(con, doc, _reading("Pd"), extractor="local@h", prompt=2)
    assert rep.retired == 1
    assert _live(con, doc) == ["local@h/prompt2"]
    # retiring the model retires its readings under any prompt
    extraction.apply(
        con, doc, _reading("Csound"), extractor="other@h", prompt=2
    )  # another model's stays
    assert store.retire_run(con, producer="local@h") == 1
    assert _live(con, doc) == ["other@h/prompt2"]
    # a worker that posts no prompt writes the bare name, as before
    store.request_extraction(con, doc, by="test")
    extraction.apply(con, doc, _reading("Max"), extractor="other@h")
    assert _live(con, doc) == ["other@h"]
    assert "prompt" not in store.get_meta(con, doc)["extraction"]


def test_the_plain_ontology_versions_take_their_modules_name(
    con: sqlite3.Connection,
) -> None:
    doc = store.ingest_text(con, "x " * 40, title="Old")["doc_id"]
    for i, version in enumerate(("0", "3", "5", "core3", "core8+research9")):
        store.link(
            con,
            E("Old", "paper", "uses", f"tool {i}", "tool"),
            source_doc=doc,
            ontology_version=version,
        )
    store.update_meta(
        con, doc, keys={"extraction": {"extractor": "m", "ontology_version": "4"}}
    )
    sql = next(p for n, p in schema.migrations() if n == 72).read_text("utf-8")
    con.executescript(f"BEGIN;\n{sql}\nCOMMIT;")
    got = [
        r[0]
        for r in con.execute(
            "SELECT ontology_version FROM edges WHERE source_doc = ? ORDER BY id",
            (doc,),
        )
    ]
    assert got == [
        "research0",
        "research3",
        "core1+research5",
        "core3",
        "core8+research9",
    ]
    assert store.get_meta(con, doc)["extraction"]["ontology_version"] == "research4"
