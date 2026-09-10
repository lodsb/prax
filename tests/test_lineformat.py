"""The line format for local models: grammar from the ontology, parse and
render as inverses, the local extractor with a fake runtime, apply()."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import pytest

from prax import extraction, lineformat, local_llm, ontology, store

T = extraction.Triple
SEP = lineformat.SEP


def test_grammar_follows_the_ontology_and_bounds_the_output() -> None:
    onto = ontology.current()
    g = lineformat.grammar(onto, max_triples=7, max_unmapped=2)
    assert "root ::= summary triple{1,7} unmapped{0,2}" in g
    for name in onto.entity_types:
        assert f'"{name}"' in g
    for rel in onto.relations:
        assert f'"{rel}"' in g
    assert 'conf ::= "AMBIGUOUS" | "EXTRACTED" | "INFERRED"' in g
    assert f"text ::= char{{1,{lineformat.TEXT_CHARS}}}" in g
    assert "char ::= [^\\t\\n\\r]" in g


def test_grammar_compiles_in_llama_cpp() -> None:
    try:
        local_llm.llama_class()  # puts the runtime DLLs on PATH first
    except RuntimeError as e:
        pytest.skip(str(e))
    from llama_cpp import LlamaGrammar

    g = lineformat.grammar(ontology.current())
    assert LlamaGrammar.from_string(g, verbose=False) is not None


def _sample() -> extraction.Extraction:
    return extraction.Extraction(
        triples=[
            T(
                "Paper X",
                "paper",
                "authored_by",
                "Ada Lovelace",
                "author",
                "EXTRACTED",
                "Authors: Ada Lovelace",
            ),
            T(
                "Paper X",
                "paper",
                "about",
                "granular synthesis",
                "concept",
                "INFERRED",
                "we study grains",
            ),
        ],
        unmapped=[
            {"src": "Paper X", "rel": "funded_by", "dst": "EU", "reason": "no relation"}
        ],
        summary="A paper about grains.",
    )


def test_render_and_parse_are_inverses() -> None:
    ex = _sample()
    text = lineformat.render(ex)
    lines = text.splitlines()
    assert lines[0] == f"summary{SEP}A paper about grains."
    assert lines[1].split(SEP) == [
        "triple",
        "src=Paper X",
        "src_type=paper",
        "rel=authored_by",
        "dst=Ada Lovelace",
        "dst_type=author",
        "confidence=EXTRACTED",
        "evidence=Authors: Ada Lovelace",
    ]
    assert lines[3].startswith(f"unmapped{SEP}src=Paper X{SEP}rel=funded_by{SEP}")
    back = lineformat.parse(text)
    assert back.triples == ex.triples
    assert back.unmapped == ex.unmapped and back.summary == ex.summary
    assert back.usage == {}


def test_parse_drops_malformed_lines_and_strips() -> None:
    def row(*fields: str) -> str:
        return SEP.join(fields)

    text = "\n".join(
        [
            row("summary", " Spaces around . "),
            row("triple", "A", "paper", "cites", "B", "paper", "EXTRACTED"),  # 7 fields
            row(
                "triple", "A", "paper", "cites", "", "paper", "EXTRACTED", "q"
            ),  # no dst
            "garbage line",
            row(
                "triple", "A ", "paper", "cites", " B", "paper", "EXTRACTED", "x" * 400
            ),
            row("summary", "second summary is dropped"),
            "",
        ]
    )
    ex = lineformat.parse(text)  # bare fields, without keys, parse too
    assert ex.summary == "Spaces around ."
    assert (
        len(ex.triples) == 1 and ex.triples[0].src == "A" and ex.triples[0].dst == "B"
    )
    assert len(ex.triples[0].evidence) == lineformat.TEXT_CHARS
    assert ex.usage == {"dropped_lines": 4}


def test_parse_drops_repeats() -> None:
    ex = _sample()
    ex.triples.append(ex.triples[0])  # exact repeat
    twin = T(*[f.upper() for f in ex.triples[1].__dict__.values()])
    ex.triples.append(twin)  # same triple, different case and evidence
    ex.unmapped.append(dict(ex.unmapped[0], reason="again"))
    back = lineformat.parse(lineformat.render(ex))
    assert len(back.triples) == 2 and len(back.unmapped) == 1
    assert back.usage == {"repeats": 3}


def test_render_removes_tabs_and_breaks_from_fields() -> None:
    ex = extraction.Extraction(
        triples=[T("A\tB", "paper", "cites", "C\nD", "paper", "EXTRACTED", "e\r\nf")],
        summary="s\tt",
    )
    back = lineformat.parse(lineformat.render(ex))
    assert back.triples[0].src == "A B" and back.triples[0].dst == "C D"
    assert back.triples[0].evidence == "e f" and back.summary == "s t"


@dataclass
class FakeRuntime:
    reply: str
    name: str = "local:fake-7b"
    calls: list[dict[str, object]] = field(default_factory=list)

    def chat(
        self,
        system,
        user,
        *,
        grammar=None,
        max_tokens=2000,
        temperature=0.0,
        repeat_penalty=1.0,
    ):
        self.calls.append(
            {
                "system": system,
                "user": user,
                "grammar": grammar,
                "max_tokens": max_tokens,
            }
        )
        return self.reply, {"input_tokens": 100, "output_tokens": 20}


def test_local_extractor_prompts_with_lines_and_applies(
    con: sqlite3.Connection,
) -> None:
    doc_id = store.ingest_text(
        con,
        "we study grains",
        title="Paper X",
        meta={"creators": [{"name": "Ada Lovelace"}]},
    )["doc_id"]
    runtime = FakeRuntime(lineformat.render(_sample()))
    ext = extraction.LocalExtractor(runtime, max_triples=9)
    assert ext.name == "local:fake-7b"
    result = ext.extract(extraction.build_input(con, doc_id))
    call = runtime.calls[0]
    assert "Answer in the line format" in str(call["system"])
    assert "Return JSON" not in str(call["system"])
    assert "Emit at most 9 triples" in str(call["system"])
    assert f"summary{SEP}two or three sentences" in str(call["system"])
    assert f"triple{SEP}src=source name{SEP}src_type=" in str(call["system"])
    assert "triple{1,9}" in str(call["grammar"])
    assert "Title: Paper X" in str(call["user"])
    assert result.triples == _sample().triples
    assert result.usage == {"input_tokens": 100, "output_tokens": 20}
    report = extraction.apply(con, doc_id, result, extractor=ext.name)
    assert (report.linked, report.queued, report.rejected) == (2, 1, 0)
    meta = store.get_meta(con, doc_id)
    assert meta["extraction"]["extractor"] == "local:fake-7b"
    assert meta["summary"] == "A paper about grains."
    assert extraction.cost_usd(ext.name, result.usage) == 0.0


def test_local_extractor_names_the_document_and_apply_drops_reference_numbers(
    con: sqlite3.Connection,
) -> None:
    doc_id = store.ingest_text(con, "text", title="Paper X")["doc_id"]
    reply = lineformat.render(
        extraction.Extraction(
            triples=[
                T("paper", "paper", "cites", "[12]", "paper", "EXTRACTED", "see [12]"),
                T("this paper", "paper", "cites", "Paper Y", "paper", "EXTRACTED", "q"),
                T("Paper Z", "paper", "cites", "Paper W", "paper", "EXTRACTED", "q"),
                T("paper", "concept", "about", "1990", "concept", "INFERRED", "q"),
            ],
            summary="s",
        )
    )
    ext = extraction.LocalExtractor(FakeRuntime(reply))
    result = ext.extract(extraction.build_input(con, doc_id))
    assert [t.src for t in result.triples] == ["Paper X", "Paper X", "Paper Z", "paper"]
    assert result.triples[2].dst == "Paper W"  # only self-references are renamed
    report = extraction.apply(con, doc_id, result, extractor=ext.name)
    assert (report.linked, report.rejected, report.queued) == (2, 2, 0)
    names = {e["dst"] for e in store.traverse(con, "Paper X", hops=1)}
    assert names == {"Paper Y"}


def test_json_prompt_is_unchanged_by_the_lines_option() -> None:
    onto = ontology.current()
    default = extraction.system_prompt(onto)
    assert default == extraction.system_prompt(onto, output="json")
    assert "line format" not in default and default.endswith(".")
    lines = extraction.system_prompt(onto, output="lines")
    assert lines.startswith(default.split("Return JSON")[0])
    with pytest.raises(ValueError):
        extraction.system_prompt(onto, output="xml")


def test_current_local_extractor_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAX_EXTRACT", "local")
    monkeypatch.delenv("PRAX_LOCAL_MODEL", raising=False)
    with pytest.raises(ValueError, match="PRAX_LOCAL_MODEL"):
        extraction.current()
    monkeypatch.setenv("PRAX_LOCAL_MODEL", "C:/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf")
    monkeypatch.setenv("PRAX_LOCAL_CTX", "4096")
    ext = extraction.current()
    assert isinstance(ext, extraction.LocalExtractor)
    assert ext.name == "local:Qwen2.5-7B-Instruct-Q4_K_M"
    assert ext.runtime.n_ctx == 4096  # nothing loaded yet
    assert extraction.price(ext.name) == (0.0, 0.0)


def test_identifier_names_become_printed_names() -> None:
    text = (
        "summary\tx\n"
        "triple\tsrc=Paper A\tsrc_type=paper\trel=uses\tdst=rwc_pop_dataset"
        "\tdst_type=dataset\tconfidence=EXTRACTED\tevidence=q\n"
        "unmapped\tsrc=Paper A\trel=plans\tdst=some_thing\treason=r\n"
    )
    ex = lineformat.parse(text)
    assert ex.triples[0].dst == "rwc pop dataset"
    assert ex.unmapped[0]["dst"] == "some thing"
    assert lineformat._name("snake_case_only") == "snake case only"
    assert lineformat._name("Mixed_Case") == "Mixed_Case"
    assert lineformat._name("a name") == "a name"
