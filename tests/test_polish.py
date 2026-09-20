# ruff: noqa: E501 — the raw transcript is data, its lines are long
"""The polish: an automatic transcript punctuated by the step's model, the
fillers dropped, a paragraph the model rewrote kept raw; asked for by the
door after a video capture, and by the heal for the ones it missed."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_video import META, PAGE

from prax import models, pipeline, store
from prax.parsers import ExtractionError, polish


@pytest.fixture()
def client(data_dir: object) -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


RAW = """# A Talk

## Transcript

![0:00 — thanks so uh title slide](figure:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789)

[0:04] thanks so uh title slide all right yeah i'm andrew kelly i am the uh president and lead software developer of the zig software foundation

[0:27] here pressing the go button not working no we're experiencing technical difficulties okay so i'll press it hard and long there we go

[1:02] this one the model will rewrite
"""


class Fake:
    name = "stub"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, Any]]:
        self.calls.append(user)
        answer = (
            "[0:04] Thanks. So, title slide. All right, yeah, I'm Andrew Kelly. I am"
            " the president and lead software developer of the Zig Software"
            " Foundation.\n"
            "[0:27] Here, pressing the Go button. Not working. No, we're experiencing"
            " technical difficulties. I'll press it hard and long. There we go.\n"
            "[1:02] A summary of what he said instead, in many more words than there"
            " were, which is not a copy-edit at all and must be refused.\n"
        )
        return answer, {}


def test_the_paragraphs_are_punctuated_and_a_rewrite_is_kept_raw() -> None:
    fake = Fake()
    text, counts = polish.polish(RAW, runtime=fake)
    assert counts == {"polished": 2, "kept_raw": 1}
    assert "[0:04] Thanks. So, title slide. All right, yeah, I'm Andrew Kelly." in text
    assert "[0:27] Here, pressing the Go button." in text
    assert "[1:02] this one the model will rewrite" in text  # refused: too many words
    # the frame line and the headings are not touched; the model saw only paragraphs
    assert "![0:00 — thanks so uh title slide](figure:" in text and "# A Talk" in text
    assert len(fake.calls) == 1 and fake.calls[0].startswith("[0:04] thanks so uh")
    assert "figure:" not in fake.calls[0]
    # the guard: the fillers may go, the rest must stay; nothing much added
    raw = (
        "thanks so uh title slide all right yeah i'm andrew kelly i am the uh president"
    )
    assert polish.acceptable(
        raw,
        "Thanks. So, title slide. All right, yeah, I'm Andrew Kelly. I am the president.",
    )
    assert not polish.acceptable(raw, "Andrew Kelly is the president.")
    assert not polish.acceptable(
        raw, raw + " and here are ten extra words that nobody said at all"
    )
    assert not polish.acceptable(raw, "")
    with pytest.raises(ExtractionError):
        polish.polish("# No transcript\n\nprose only\n", runtime=fake)


def test_the_door_asks_for_the_polish_after_a_video_capture_and_the_frames_after_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con: sqlite3.Connection = client.app.state.con
    local = models.ModelSpec(
        name="local", kind="openai", base_url="http://127.0.0.1:1/v1", model="m"
    )
    monkeypatch.setattr(
        models, "resolve", lambda s: local if s in ("polish", "vision") else None
    )
    r = client.post(
        "/ingest/html",
        json={"url": META["url"], "html": PAGE, "mode": "video", "video": META},
    ).json()
    assert r["created"] and r["indexed"]
    reading = store.get_meta(con, r["doc_id"])["reading"]
    assert reading["extractor"] == "polish" and reading["by"] == "door"
    # the polish lands (as a reading, through the store's own apply): next,
    # the frames, since the vision model is free and nobody read them
    from prax.parsers import queue

    monkeypatch.setattr(
        polish,
        "polish",
        lambda previous, runtime=None: (
            previous.replace("[0:32] the", "[0:32] The"),
            {"polished": 1, "kept_raw": 0},
        ),
    )
    action = queue.parse_one(con, r["doc_id"], extractor="polish", force=True)
    assert action == "upgraded"
    store.finish_reading(con, r["doc_id"], outcome=action, stamp="polish/1+local")
    assert (
        pipeline.follow_ups(con, r["doc_id"], stamp="polish/1+local", action=action)
        == "figures"
    )
    assert store.get_meta(con, r["doc_id"])["reading"]["extractor"] == "figures"
    # a video whose track a person wrote gets no polish: straight to the frames
    uploaded = dict(
        META, id="up1", url="https://www.youtube.com/watch?v=up1", captions="uploaded"
    )
    r2 = client.post(
        "/ingest/html",
        json={
            "url": uploaded["url"],
            "html": PAGE.replace("abc123", "up1"),
            "mode": "video",
            "video": uploaded,
        },
    ).json()
    assert store.get_meta(con, r2["doc_id"])["reading"]["extractor"] == "figures"


def test_the_heal_names_the_unpolished_and_offers_the_polish(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con: sqlite3.Connection = client.app.state.con
    monkeypatch.setattr(
        models, "resolve", lambda s: None
    )  # no local model: no follow-up
    r = client.post(
        "/ingest/html",
        json={"url": META["url"], "html": PAGE, "mode": "video", "video": META},
    ).json()
    assert store.get_meta(con, r["doc_id"]).get("reading") is None
    found = {
        a["name"]: a
        for a in store.health(con, only=["unpolished-transcripts"])["ailments"]
    }
    a = found["unpolished-transcripts"]
    assert a["count"] == 1 and a["examples"][0]["id"] == r["doc_id"]
    assert a["offers"][0] == {
        "label": "polish all of them",
        "extractor": "polish",
        "unpolished": True,
    }
    assert store.select_for_reading(con, unpolished=True) == [r["doc_id"]]
    assert store.select_for_reading(con, doctype="video") == [r["doc_id"]]
    assert store.select_for_reading(con, doctype="pdf") == []
    with pytest.raises(ValueError):
        store.select_for_reading(con, doctype="nope")
    dry = client.post(
        "/readings/bulk",
        json={"extractor": "polish", "unpolished": True, "dry_run": True},
    ).json()
    assert dry["selected"] == 1
    assert (
        client.post(
            "/readings/bulk", json={"extractor": "polish", "doctype": "nope"}
        ).status_code
        == 400
    )
