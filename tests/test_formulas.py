"""Formula readings: a model's words under a display equation, on the
figure pattern — the extractor, again mode, the selection and the
ailment, the reading request through the door."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import chunking, config, models, parsers, store
from prax.parsers import formulas

PAPER = """\
# An Improved Diode Clipper Model

## 2.1 Single Diode

The Shockley equation gives the current through a diode as

$$i = I_s \\left( e^{v/V_T} - 1 \\right), \\quad (1)$$

where i is the current through and v the voltage across the diode, I_s
the reverse-bias saturation current and V_T the thermal voltage. In the
wave domain this becomes

$$\\frac{a-b}{2R} = I_s \\left( e^{\\frac{a+b}{2V_T}} - 1 \\right). \\quad (4)$$

which has no closed form without the Lambert W function.
"""


class Reader:
    """A runtime that answers with the equation's number, so a test can
    tell which reading landed where."""

    name = "reader@test"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, int]]:
        self.calls.append({"system": system, "user": user, **kw})
        number = user.split("The equation (")[1].split(")")[0] if "(" in user else "?"
        return f"Equation {number} in words.", {"input_tokens": 1, "output_tokens": 1}


def test_refs_find_the_display_equations_and_who_read_them() -> None:
    found = formulas.refs(PAPER)
    assert [r["number"] for r in found] == ["1", "4"]
    assert found[0]["latex"].startswith("i = I_s") and found[0]["read_by"] == []
    read = PAPER.replace(
        "\\quad (1)$$\n", "\\quad (1)$$\n*Formula, as read by m:* the diode law\n"
    )
    assert formulas.refs(read)[0]["read_by"] == ["m"]


def test_a_reading_goes_under_each_equation_with_the_text_around_it(
    data_dir: Path,
) -> None:
    reader = Reader()
    out = formulas.describe(PAPER, runtime=reader)
    assert len(reader.calls) == 2
    first = reader.calls[-1]  # read from the last equation up: (1) is last
    assert 'From "An Improved Diode Clipper Model"' in first["user"]
    assert "The equation (1):\n$$i = I_s" in first["user"]
    assert "thermal voltage" in first["user"]  # the text around it
    assert "$$\\frac" not in first["user"]  # not the other equation
    assert first["max_tokens"] == formulas.MAX_TOKENS
    assert "*Formula, as read by reader@test:* Equation 1 in words." in out
    assert "*Formula, as read by reader@test:* Equation 4 in words." in out
    # right under the equation line, inside its element: the chunker sees it
    chunks = chunking.chunk(out)
    f = [c for c in chunks if c.kind == "formula"]
    assert len(f) == 2
    parsed = chunking.parse_formula(f[0].text)
    assert parsed and parsed["readings"] == [
        {"model": "reader@test", "text": "Equation 1 in words."}
    ]
    # a second pass reads nothing new; `again` replaces this model's, keeps another's
    assert formulas.describe(out, runtime=reader) == out
    other = out.replace(
        "*Formula, as read by reader@test:* Equation 4 in words.",
        "*Formula, as read by other:* someone else's words\n"
        "*Formula, as read by reader@test:* Equation 4 in words.",
    )
    reader.calls.clear()
    import os

    os.environ["PRAX_FORMULA_READINGS"] = "again"
    try:
        twice = formulas.describe(other, runtime=reader)
    finally:
        del os.environ["PRAX_FORMULA_READINGS"]
    assert len(reader.calls) == 2
    assert twice.count("as read by reader@test") == 2
    assert "*Formula, as read by other:* someone else's words" in twice
    assert twice.count("Equation 4 in words.") == 1


def test_the_step_names_the_model_and_none_is_an_error(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models.reset()
    monkeypatch.setenv("PRAX_FORMULAS_MODEL", "")  # the legacy id: not used
    monkeypatch.setenv("PRAX_FORMULAS", "none")
    with pytest.raises(parsers.ExtractionError, match="no formulas model"):
        formulas.describe(PAPER)
    ext = parsers.by_name("formulas")
    assert ext.explicit_only and ext.annotates and ext.previous
    assert ext.stamp == "formulas/1+none"
    monkeypatch.setenv("PRAX_FORMULAS", "stub")
    models.reset()
    assert parsers.by_name("formulas").stamp == "formulas/1+stub"
    out = ext(b"", filename=None, previous=PAPER)
    assert out.count("*Formula, as read by stub:*") == 2
    with pytest.raises(parsers.ExtractionError, match="no text"):
        ext(b"", filename=None, previous="")
    monkeypatch.setenv("PRAX_FORMULA_READINGS", "sideways")
    with pytest.raises(parsers.ExtractionError, match="must be one of"):
        formulas.describe(PAPER, runtime=Reader())


def _paper(con: sqlite3.Connection, text: str, title: str) -> int:
    return store.ingest_text(con, text, title=title)["doc_id"]


def test_the_unread_ones_are_found_and_selected(con: sqlite3.Connection) -> None:
    unread = _paper(con, PAPER, "Diode clipper")
    read = _paper(con, formulas.describe(PAPER, runtime=Reader()), "Read clipper")
    prose = _paper(con, "# Notes\n\nNo maths here. " * 20, "Notes")
    assert store.select_for_reading(con, unread_formulas=True) == [unread]
    assert store.select_for_reading(con, read_formulas=True) == [read]
    assert store.select_for_reading(con, ids=[prose], unread_formulas=True) == []
    # a document with an unread figure and no formula is not an unread-formula
    # one, nor the other way round (the loose OR that once said so)
    figured = _paper(
        con,
        "# Pictures\n\n"
        + "Prose here. " * 10
        + "\n\n![Fig. 1. A rig.](figure:"
        + "d" * 64
        + ")\n",
        "Pictures",
    )
    assert figured not in store.select_for_reading(con, unread_formulas=True)
    assert store.select_for_reading(con, unread_figures=True) == [figured]
    assert unread not in store.select_for_reading(con, unread_figures=True)
    ailment = next(a for a in store.AILMENTS if a.name == "unread-formulas")
    found = ailment.find(con)
    assert [f["id"] for f in found] == [unread] and found[0]["unread"] == 2
    assert ailment.offers[0]["extractor"] == "formulas"


def test_a_reading_request_through_the_door(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        "steps: {formulas: {model: stub}}\n", encoding="utf-8"
    )
    models.reset()
    from prax.api import app

    with TestClient(app) as client:
        doc = client.post("/ingest", json={"text": PAPER, "title": "Clipper"}).json()
        r = client.post(
            "/readings/bulk",
            json={"extractor": "formulas", "unread_formulas": True, "dry_run": True},
        )
        assert r.status_code == 200 and r.json()["selected"] == 1
        r = client.post(
            "/readings/bulk",
            json={"extractor": "formulas", "unread_formulas": True, "mode": "again"},
        )
        assert r.status_code == 200 and r.json()["requested"] == 1
        r = client.post(
            "/readings/bulk",
            json={"extractor": "formulas", "mode": "sideways", "ids": [1]},
        )
        assert r.status_code == 400
        # the worker does it as any reading: through the work protocol
        from prax import worker

        class Door:
            name = "test"

            def get_json(self, path: str, params: Any = None) -> Any:
                return client.get(path, params=params).json()

            def post_json(self, path: str, body: Any = None) -> Any:
                return client.post(path, json=body).json()

            def get_bytes(self, path: str) -> bytes:
                return client.get(path).content

        done = worker.run_once(Door(), steps=("parse",))  # type: ignore[arg-type]
        assert done.get("parse")
        text = client.get(f"/doc/{doc['doc_id']}/text").text
        assert text.count("*Formula, as read by stub:*") == 2
        chunks = client.get(f"/doc/{doc['doc_id']}/chunks").json()
        rows = chunks if isinstance(chunks, list) else chunks["chunks"]
        assert sum(1 for c in rows if c["kind"] == "formula") == 2
        assert store.select_for_reading(store.connect(), unread_formulas=True) == []
