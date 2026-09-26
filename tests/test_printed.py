"""The word a document printed, kept beside the name the graph writes.

From 2026-09-24 to -26 the extraction prompt wrote a common noun in the
library's language, and a new entity got one label, its own name — so a
German recipe's "Äpfel" became `apple` with no German label anywhere, and
a German query had nothing to cross to the English documents on
(``docs/eval/apfelkuchen-2026-09-26.md``).

The main route back is the prompt writing names as printed and the watched
vocabulary pass translating them, which keeps the word by construction. A
local model asked to write both names on one line wrote neither (0 of
145) or, forced, wrote junk. What is tested here is the fallback: a model
that translates anyway can still hand over the printed word, and it is
kept.
"""

from __future__ import annotations

import sqlite3

from prax import extraction, lineformat, ontology, store

T = extraction.Triple
SEP = lineformat.SEP

GERMAN = (
    "# Apfelkuchen vom Blech\n\n"
    "Dieser Kuchen ist schnell gemacht und schmeckt am besten, wenn die Äpfel"
    " noch etwas Biss haben. Die Butter wird geschmolzen und mit dem Zucker"
    " verrührt, dann kommen die Eier und das Mehl dazu. Der Teig wird auf dem"
    " Blech verteilt und mit den Apfelscheiben belegt. Nach vierzig Minuten im"
    " Ofen ist der Kuchen fertig und kann abkühlen, bevor er geschnitten wird.\n"
)


def _german(con: sqlite3.Connection) -> int:
    return int(store.ingest_text(con, GERMAN, title="Apfelkuchen vom Blech")["doc_id"])


def _apple(said: str = "Äpfel") -> extraction.Extraction:
    return extraction.Extraction(
        triples=[
            T(
                "Apfelkuchen vom Blech",
                "recipe",
                "calls_for",
                "apple",
                "ingredient",
                "EXTRACTED",
                "750 g Äpfel",
                dst_as=said,
            )
        ],
        summary="An apple sheet cake.",
    )


def _labels(con: sqlite3.Connection, name: str) -> list[tuple]:
    return [
        tuple(r)
        for r in con.execute(
            "SELECT l.label, l.lang, l.kind, l.source_doc FROM entity_labels l"
            " JOIN entities e ON e.id = l.entity_id WHERE e.name = ?"
            " ORDER BY l.kind, l.label",
            (name,),
        )
    ]


def test_the_line_format_carries_the_printed_names_either_or_both() -> None:
    for src_as, dst_as in (
        ("", "Äpfel"),
        ("Blechkuchen", ""),
        ("Blechkuchen", "Äpfel"),
        ("", ""),
    ):
        ex = extraction.Extraction(
            triples=[
                T(
                    "sheet cake",
                    "dish",
                    "calls_for",
                    "apple",
                    "ingredient",
                    "EXTRACTED",
                    "q",
                    src_as=src_as,
                    dst_as=dst_as,
                )
            ],
            summary="s",
        )
        back = lineformat.parse(lineformat.render(ex))
        assert back.triples == ex.triples
    # an answer from before the change, eight fields, still reads
    old = (
        f"triple{SEP}src=a{SEP}src_type=dish{SEP}rel=calls_for{SEP}dst=b"
        f"{SEP}dst_type=ingredient{SEP}confidence=EXTRACTED{SEP}evidence=q"
    )
    t = lineformat.parse(old).triples[0]
    assert (t.src_as, t.dst_as) == ("", "")


def test_the_grammar_allows_the_printed_names_and_does_not_require_them() -> None:
    g = lineformat.grammar(ontology.current())
    assert '"\\tevidence=" text srcas? dstas? "\\n"' in g
    assert 'dstas ::= "\\tdst_as=" name' in g


def test_the_json_answer_carries_it_as_the_entity_s_as() -> None:
    got = extraction.parse_output(
        {
            "triples": [
                {
                    "src": {"name": "sheet cake", "type": "dish"},
                    "rel": "calls_for",
                    "dst": {"name": "apple", "type": "ingredient", "as": "Äpfel"},
                    "confidence": "EXTRACTED",
                    "evidence": "750 g Äpfel",
                }
            ]
        }
    )
    assert (got.triples[0].src_as, got.triples[0].dst_as) == ("", "Äpfel")
    schema = extraction.output_schema(ontology.current())
    entity = schema["properties"]["triples"]["items"]["properties"]["dst"]
    assert "as" in entity["properties"] and entity["required"] == ["name", "type"]


def test_the_prompt_translates_no_name_and_the_format_keeps_a_fallback() -> None:
    prompt = extraction.system_prompt(ontology.current())
    assert "never translate a name" in prompt
    # the line format still offers the fields, for a model that does
    assert "src_as=<name as printed>" in lineformat.prompt_section()


def test_apply_keeps_the_printed_word_as_a_label_in_the_document_s_language(
    con: sqlite3.Connection,
) -> None:
    doc = _german(con)
    assert store.get_meta(con, doc).get("lang") == "de"
    report = extraction.apply(con, doc, _apple(), extractor="stub", run="r1")
    assert report.linked == 1 and report.printed == 1
    assert ("Äpfel", "de", "alt", doc) in _labels(con, "apple")


def test_an_existing_edge_still_keeps_the_word(con: sqlite3.Connection) -> None:
    """The label is about the thing, not about this fact."""
    doc = _german(con)
    extraction.apply(con, doc, _apple("Äpfel"), extractor="stub")
    again = extraction.apply(con, doc, _apple("Apfel"), extractor="stub")
    assert again.existing == 1 and again.printed == 1
    said = {label for label, *_ in _labels(con, "apple")}
    assert {"Äpfel", "Apfel"} <= said


def test_a_word_printed_as_the_name_is_not_kept_twice(con: sqlite3.Connection) -> None:
    doc = _german(con)
    report = extraction.apply(con, doc, _apple("Apple"), extractor="stub")
    assert report.printed == 0
    assert [label for label, *_ in _labels(con, "apple")] == ["apple"]


def test_the_word_is_what_a_german_query_crosses_on(con: sqlite3.Connection) -> None:
    """The bridge the prevention had dropped: a German word reaches the
    English name through the label."""
    doc = _german(con)
    extraction.apply(con, doc, _apple("Apfel"), extractor="stub")
    terms = store.expand_query(con, "Apfel")
    assert "apple" in terms[0]
