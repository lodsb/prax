"""The model typing pass: untyped review items go to a model in batches,
its answers become edges when they fit the ontology, `none` drops, the
rest stays open; nothing is written on a dry run."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax import store, typing_pass


class FakeModel:
    """Answers by name: people are authors, venues are venues, 'junk' is none."""

    name = "fake@test"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, int]]:
        self.calls.append(user)
        assert "Types:" in system and "published_in:" in system
        lines = []
        n = 0
        for line in user.splitlines():
            if not line[:1].isdigit():
                continue
            n += 1
            body = line.split(". ", 1)[1]
            _src, rest = body.split(" --", 1)
            rel, dst = rest.split("--> ", 1)
            dst = dst.split("   (evidence")[0]
            if "junk" in dst:
                lines.append(f"{n}: paper -> none")
            elif rel == "published_in":
                lines.append(f"{n}: paper -> organization")  # retyped to a venue
            elif rel == "authored_by":
                lines.append(f"{n}: paper -> author")
            elif rel == "uses":
                lines.append(f"{n}: paper -> tool")
            elif rel == "about":
                lines.append(f"{n}: venue -> author")  # a misfit: a venue is not about
            elif rel == "used_in":
                lines.append(f"{n}: tool -> paper")  # the tool is used in the paper
        return "\n".join(lines), {"input_tokens": 10, "output_tokens": 5}


def _queue(con: sqlite3.Connection, doc: int, src: str, rel: str, dst: str) -> None:
    store.queue_review(
        con,
        src=src,
        src_type=None,
        rel=rel,
        dst=dst,
        dst_type=None,
        reason="unmapped",
        source_doc=doc,
        evidence=f"{src} {rel} {dst}",
    )


def test_the_model_pass_types_drops_and_leaves(con: sqlite3.Connection) -> None:
    title = "Cuckoo Hashing for Undergraduates"
    doc = store.ingest_text(con, "hashing " * 50, title=title)["doc_id"]
    store.set_domains(con, doc, ["research"])
    _queue(con, doc, title, "published_in", "European Symposium on Algorithms")
    _queue(con, doc, title, "authored_by", "Rasmus Pagh")
    _queue(con, doc, title, "uses", "junk value")
    _queue(con, doc, title, "about", "Some Venue")
    _queue(
        con, doc, "source name", "cites", "target name"
    )  # a placeholder: never asked
    model = FakeModel()
    dry = typing_pass.run(con, commit=False, runtime=model)
    assert (dry.checked, dry.requests) == (4, 1)
    assert (dry.linked, dry.dropped, dry.misfit, dry.unanswered) == (2, 1, 1, 0)
    assert dry.by_shape == {
        "paper -published_in-> venue": 1,
        "paper -authored_by-> author": 1,
        "drop (paper -> none)": 1,
        "misfit venue -about-> author": 1,
    }
    assert (
        "Document: Cuckoo Hashing for Undergraduates (its own type: paper)"
        in model.calls[0]
    )
    assert len(store.list_review(con, limit=100)) == 5  # a dry run writes nothing
    assert store.stats(con)["graph"]["edges"] == 0
    real = typing_pass.run(con, commit=True, runtime=model)
    assert (real.linked, real.dropped, real.misfit) == (2, 1, 1)
    edges = store.traverse(con, title, hops=1)
    assert {(e["rel"], e["dst"], e["dst_type"]) for e in edges} == {
        ("published_in", "European Symposium on Algorithms", "venue"),
        ("authored_by", "Rasmus Pagh", "author"),
    }
    assert {e["producer"] for e in edges} == {"typing:fake@test"}
    assert {e["confidence"] for e in edges} == {"INFERRED"}
    assert {e["run"] for e in edges} == {real.run}
    left = store.list_review(con, limit=100)
    assert {it["rel"] for it in left} == {
        "about",
        "cites",
    }  # the misfit and the placeholder


def test_answers_are_parsed_leniently() -> None:
    lines = ["1: paper -> venue", "2. author, organization", "3) tool | concept"]
    lines += ["noise", "9: x -> y"]
    text = chr(10).join(lines)
    assert typing_pass.parse_answer(text, 3) == {
        1: ("paper", "venue"),
        2: ("author", "organization"),
        3: ("tool", "concept"),
    }


def test_a_reversed_alias_is_typed_the_other_way_round(
    con: sqlite3.Connection,
) -> None:
    """ "NumPy used_in <paper>" becomes the paper uses NumPy: the ends and
    their types swap with the canonical name (research v8)."""
    title = "A Study of Arrays"
    doc = store.ingest_text(con, "arrays " * 50, title=title)["doc_id"]
    store.set_domains(con, doc, ["research"])
    _queue(con, doc, "NumPy", "used_in", title)
    model = FakeModel()
    real = typing_pass.run(con, commit=True, runtime=model)
    assert (real.linked, real.misfit) == (1, 0)
    edges = store.traverse(con, title, hops=1)
    assert {(e["src"], e["rel"], e["dst"], e["dst_type"]) for e in edges} == {
        (title, "uses", "NumPy", "tool")
    }
