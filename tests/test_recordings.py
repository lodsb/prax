"""A recording's text (plan item AQ stage 4, ``prax.ml.speech``): where
it is speech, singing or music, the words of the first two, music written
as music, and the transcribe step through the door. The models are a
stand-in here: what is tested is what is made of their answers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from prax import store, worker
from prax.ml import speech
from prax.steps import base, recordings
from prax.text import chunking, markup

SR = speech.SR
STEP = int(speech.WINDOW_S * SR)
# a window's level says what the stand-in tagger hears in it
LEVELS = {"speech": 0.1, "music": 0.2, "sung": 0.3, "other": 0.4}


def _sound(*kinds: str) -> Any:
    """Five-second windows, each of one kind; ``silence`` is zeros."""
    return np.concatenate(
        [np.full(STEP, LEVELS.get(k, 0.0), np.float32) for k in kinds]
    )


class Stand:
    """The tagger by a window's level; Whisper says which kind it heard,
    with a doubtful score for singing."""

    def __init__(self) -> None:
        self.read: list[tuple[float, str]] = []
        self.released = False

    def tag(self, windows: list[Any]) -> list[dict[str, float]]:
        out = []
        for w in windows:
            level = round(float(np.max(np.abs(w))), 1)
            kind = next((k for k, v in LEVELS.items() if v == level), "other")
            names = speech.LABELS.get(kind, ())
            out.append({names[0]: 0.9} if names else {"Wind": 0.8})
        return out

    def words(self, audio: Any, kind: str) -> list[speech.Segment]:
        seconds = len(audio) / SR
        self.read.append((seconds, kind))
        score = -1.8 if kind == "sung" else -0.2
        return [speech.Segment(0.5, seconds - 0.5, f"the {kind} words", kind, score)]

    def release(self) -> None:
        self.released = True


def test_each_window_gets_one_kind() -> None:
    assert speech.kind_of(None) == "silence"
    assert speech.kind_of({"Speech": 0.7, "Music": 0.6}) == "speech"
    # a song's words are heard as speech too: singing goes first
    assert speech.kind_of({"Singing": 0.5, "Speech": 0.8, "Music": 0.9}) == "sung"
    assert speech.kind_of({"Music": 0.5, "Speech": 0.1}) == "music"
    assert speech.kind_of({"Speech": 0.1, "Music": 0.1}) == "other"


def test_neighbours_of_one_kind_are_one_span() -> None:
    w = speech.WINDOW_S
    kinds = ["speech", "speech", "silence", "speech", "music", "music", "music"]
    got = speech.spans(kinds, 7 * w)
    # a breath in a sentence joins it; the music is its own span
    assert [(s.start, s.end, s.kind) for s in got] == [
        (0.0, 4 * w, "speech"),
        (4 * w, 7 * w, "music"),
    ]
    # a short phrase between two songs is still read, never folded in
    got = speech.spans(["music", "music", "speech", "music", "music"], 5 * w)
    assert [s.kind for s in got] == ["music", "speech", "music"]
    # the last window ends with the sound, not on the grid
    assert speech.spans(["speech"], 3.2)[0].end == 3.2


def test_music_is_written_as_music_and_singing_as_sung() -> None:
    models = Stand()
    sound = _sound("speech", "speech", "music", "music", "music", "sung", "sung")
    got = speech.read(sound, models)
    # Whisper reads the speech and the singing, each span alone; never the music
    assert models.read == [(10.0, "speech"), (10.0, "sung")]
    text = speech.render(got)
    lines = text.rstrip("\n").split("\n\n")
    assert lines[0] == "## Transcript"
    assert lines[1] == "[0:00] the speech words"
    assert lines[2] == "[0:10] (music, 0:15)"
    # sung, and the model was unsure of it
    assert lines[3] == "[0:25] (sung) the sung words (?)"
    assert markup.MUSIC.match(lines[2])
    facts = got.facts()
    assert facts["duration"] == 35.0
    assert (facts["speech_s"], facts["music_s"], facts["sung_s"]) == (10.0, 15.0, 10.0)
    assert (facts["segments"], facts["doubtful"]) == (2, 1)
    # every line is a timed paragraph, so a chunk is found at its moment
    assert all(markup.TIME_PARA.match(line) for line in lines[1:])
    chunks = chunking.chunk(text)
    assert chunks[0].locator().get("time") == 0


def test_a_recording_with_nothing_in_it_has_no_text() -> None:
    models = Stand()
    got = speech.read(_sound("silence", "other", "silence"), models)
    assert models.read == [] and speech.render(got) == ""
    assert got.facts()["segments"] == 0


def _pass(client: TestClient) -> base.Pass:
    return base.Pass(
        worker.Door("http://testserver", client=client, name="test-worker")
    )


@pytest.fixture
def stand(monkeypatch: pytest.MonkeyPatch) -> Stand:
    """The step's models and decoder replaced: no ffmpeg, no card."""
    models = Stand()
    monkeypatch.setattr(speech, "available", lambda: True)
    monkeypatch.setattr(speech, "device", lambda setting: "cpu")
    sound = _sound("speech", "speech", "music", "music")
    monkeypatch.setattr(speech, "decode", lambda path: sound)

    def read_files(paths: list[Path], where: str) -> list[dict[str, Any]]:
        assert all(p.is_file() for p in paths)  # fetched before the read
        return [speech.read_file(p, models) for p in paths]

    monkeypatch.setattr(speech, "read_files", read_files)
    return models


def test_the_step_reads_a_recording_and_a_search_finds_its_words(
    con: sqlite3.Connection, client: TestClient, stand: Stand
) -> None:
    doc = store.register(con, b"ID3 a song idea", mime="audio/mpeg", title="idea.mp3")
    doc_id = doc["doc_id"]
    assert store.transcripts_due(con, speech.VERSION, limit=5) == [doc_id]
    line = recordings.Transcribe().run(_pass(client))
    assert line is not None and "1 read" in line
    meta = json.loads(
        con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()[0]
    )
    t = meta["transcript"]
    assert t["v"] == speech.VERSION and t["by"] == speech.stamp()
    assert (t["speech_s"], t["music_s"], t["duration"]) == (10.0, 10.0, 20.0)
    assert meta["text_source"] == f"transcribe/{speech.stamp()}"
    hits = store.search(con, "speech words", 5)
    assert any(h["doc_id"] == doc_id for h in hits)
    # read once: nothing more is handed out
    assert client.get("/work/transcribe").json()["items"] == []
    assert store.transcript_counts(con, speech.VERSION) == {
        "read": 1,
        "failed": 0,
        "waiting": 0,
    }


def test_what_the_step_does_not_read_and_what_it_marks(
    con: sqlite3.Connection, client: TestClient
) -> None:
    # a recording with a text of its own, from elsewhere, is not read
    sent = store.register(con, b"RIFF a talk", mime="audio/wav", title="talk.wav")
    store.index_text(con, sent["doc_id"], "The words came with it.")
    assert store.transcripts_due(con, speech.VERSION, limit=5) == []
    # a file ffmpeg cannot read is marked at this version
    bad = store.register(con, b"not a video", mime="video/mp4", title="broken.mp4")
    item = client.get("/work/transcribe").json()["items"][0]
    assert item == {
        "doc_id": bad["doc_id"],
        "original": f"/doc/{bad['doc_id']}/original",
    }
    rep = client.post(
        "/work/transcribe",
        json={
            "results": [
                {
                    "doc_id": bad["doc_id"],
                    "v": speech.VERSION,
                    "unreadable": "ffmpeg: no",
                }
            ]
        },
    ).json()
    assert rep["actions"] == {"failed": 1}
    assert store.transcripts_due(con, speech.VERSION, limit=5) == []
    # a fetch that failed is released and handed out again
    later = store.register(
        con, b"\x00\x00 a clip", mime="video/quicktime", title="c.mov"
    )
    client.get("/work/transcribe")
    client.post(
        "/work/transcribe",
        json={"results": [{"doc_id": later["doc_id"], "error": "fetch: offline"}]},
    )
    items = client.get("/work/transcribe").json()["items"]
    assert [i["doc_id"] for i in items] == [later["doc_id"]]


def test_a_full_card_makes_the_pass_wait(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, con: sqlite3.Connection
) -> None:
    store.register(con, b"ID3 waits", mime="audio/mpeg", title="waits.mp3")
    monkeypatch.setattr(speech, "available", lambda: True)
    monkeypatch.setattr(speech, "device", lambda setting: None)
    assert recordings.Transcribe().run(_pass(client)) is None
    assert store.transcripts_due(con, speech.VERSION, limit=5)


def test_the_door_streams_an_original_to_a_file(
    con: sqlite3.Connection, client: TestClient, tmp_path: Path
) -> None:
    data = b"RIFF" + bytes(range(256)) * 4096  # a megabyte
    doc = store.register(con, data, mime="audio/wav", title="long.wav")
    door = worker.Door("http://testserver", client=client, name="test-worker")
    dest = tmp_path / "copy"
    assert door.download(f"/doc/{doc['doc_id']}/original", dest) == len(data)
    assert dest.read_bytes() == data


def test_a_reading_process_that_fails_leaves_the_batch_for_later(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, con: sqlite3.Connection
) -> None:
    doc = store.register(con, b"ID3 again", mime="audio/mpeg", title="again.mp3")
    monkeypatch.setattr(speech, "available", lambda: True)
    monkeypatch.setattr(speech, "device", lambda setting: "cuda")

    def broken(paths: list[Path], where: str) -> list[dict[str, Any]]:
        raise RuntimeError("the reading process ended with 1: out of memory")

    monkeypatch.setattr(speech, "read_files", broken)
    line = recordings.Transcribe().run(_pass(client))
    assert line is not None and "1 not fetched" in line and "out of memory" in line
    # nothing marked: handed out again on the next pass
    assert store.transcripts_due(con, speech.VERSION, limit=5) == [doc["doc_id"]]
