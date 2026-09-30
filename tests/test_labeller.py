"""The small labeller (`prax.ml.labeller`): a trained run read from its
directory, the probabilities from the encoder's pooled output and the
output layer, the genres step using it, and the door's training data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from prax.graph import ontology
from prax.ml import labeller


class Enc:
    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        self.attention_mask = [1] * len(ids)


class Tok:
    def encode_batch(self, texts: list[str]) -> list[Enc]:
        return [Enc([1, 2]) if "datasheet" in t else Enc([3, 4]) for t in texts]


class Session:
    """Two tokens of hidden state, 2 wide: [1, 0] for a datasheet's
    tokens and [0, 1] for anything else."""

    def get_inputs(self) -> list[Any]:
        return [type("I", (), {"name": n})() for n in ("input_ids", "attention_mask")]

    def run(
        self, _outs: Any, feed: dict[str, np.ndarray], _opts: Any = None
    ) -> list[Any]:
        ids = feed["input_ids"]
        hidden = np.where(
            (ids[..., None] <= 2), np.array([1.0, 0.0]), np.array([0.0, 1.0])
        ).astype(np.float32)
        return [hidden]


def a_run(tmp_path: Path, labels: list[str]) -> labeller.Labeller:
    (tmp_path / "meta.json").write_text(
        json.dumps({"labels": labels, "threshold": 0.6}), encoding="utf-8"
    )
    m = labeller.Labeller(tmp_path)
    m._session, m._tokenizer = Session(), Tok()
    # the first label fires on the first hidden direction, the second on
    # the second: a datasheet and anything else
    m._w = np.array([[8.0, -8.0], [-8.0, 8.0]], dtype=np.float32)
    m._b = np.zeros(2, dtype=np.float32)
    return m


def test_the_labeller_scores_every_label(tmp_path: Path) -> None:
    m = a_run(tmp_path, ["datasheet", "essay"])
    got = m.predict(["a TL072 datasheet", "an essay on collapse"])
    assert got[0]["datasheet"] > 0.99 and got[0]["essay"] < 0.01
    assert got[1]["essay"] > 0.99
    assert m.threshold == 0.6 and m.name == f"labeller:{tmp_path.name}"


def test_the_current_run_is_named_by_a_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_MODELS_DIR", str(tmp_path))
    assert labeller.current_run() is None and labeller.current() is None
    run = tmp_path / "labeller" / "labeller-1"
    run.mkdir(parents=True)
    (tmp_path / "labeller" / "CURRENT").write_text("labeller-1", encoding="utf-8")
    assert labeller.current_run() is None  # a run without its meta is not one
    (run / "meta.json").write_text('{"labels": []}', encoding="utf-8")
    assert labeller.current_run() == run


def test_the_step_labels_with_the_small_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``steps.genres.method: small``: the step hands out without an LLM,
    the labeller answers, the door stores its labels as the labeller's."""
    from fastapi.testclient import TestClient

    from prax import models, store, work
    from prax.api import app
    from prax.steps import writing as step_writing

    fake = a_run(tmp_path, ["datasheet", "instructional", "essay"])
    fake._w = np.array([[8.0, -8.0], [8.0, -8.0], [-8.0, 8.0]], dtype=np.float32)
    fake._b = np.zeros(3, dtype=np.float32)
    monkeypatch.setattr(labeller, "current", lambda: fake)
    monkeypatch.setattr(labeller, "current_run", lambda: tmp_path)
    monkeypatch.setattr(
        models, "settings", lambda step: {"method": "small"} if step == "genres" else {}
    )
    work._leases.clear()
    with TestClient(app) as client:
        con = client.app.state.con
        sheet = client.post(
            "/ingest",
            json={"text": "TL072 datasheet pinout " * 20, "title": "TL072 datasheet"},
        ).json()["doc_id"]
        batch = client.get("/work/genres", params={"scope": "all"}).json()
        assert [i["doc_id"] for i in batch["items"]] == [sheet]
        results = step_writing.do_genres(batch["items"], None)
        assert results[0]["by"] == fake.name
        rep = client.post("/work/genres", json={"results": results}).json()
        assert rep["applied"] == 1
        m = store.get_meta(con, sheet)
        assert m["genres_by"] == fake.name
        assert [g["genre"] for g in m["genres"]] == ["instructional", "datasheet"]


def test_the_method_is_llm_or_small(monkeypatch: pytest.MonkeyPatch) -> None:
    from prax import models
    from prax.steps import writing as step_writing

    monkeypatch.setattr(models, "settings", lambda step: {"method": "big"})
    with pytest.raises(ValueError, match="llm or small"):
        step_writing.genres_method()


def test_the_door_hands_out_the_training_data(data_dir: Path) -> None:
    """Every labelled document with its view; the person's blind labels
    marked, so a training run can leave them out."""
    from fastapi.testclient import TestClient

    from prax import store
    from prax.api import app

    with TestClient(app) as client:
        con = client.app.state.con
        a = client.post("/ingest", json={"text": "A paper.", "title": "P"}).json()[
            "doc_id"
        ]
        b = client.post("/ingest", json={"text": "An essay.", "title": "E"}).json()[
            "doc_id"
        ]
        client.post("/ingest", json={"text": "Nothing.", "title": "N"})
        store.set_genres(con, a, ["paper"])  # blind: nothing was ticked
        store.set_genres(con, b, ["essay"], by="claude", p={"essay": 0.9})
        got = client.get("/genres/training").json()
        assert got["genres"] == ontology.genres().labels()
        by_id = {it["id"]: it for it in got["items"]}
        assert set(by_id) == {a, b}
        assert by_id[a]["blind"] and not by_id[b]["blind"]
        assert by_id[a]["g"] == ["informational", "paper"]
        assert by_id[b]["view"].startswith("Title: E")
