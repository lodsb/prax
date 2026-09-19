# ruff: noqa: E501 — the fixture page is data, its lines are long
"""A video capture: the extension's transcript-with-frames HTML through the
door — parsed by the parser it names, timed chunks, frames as figures
the door serves, a video to the doctype filter."""

from __future__ import annotations

import base64
import io
import json
import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import chunking, parsers, store, work
from prax.parsers import video


@pytest.fixture()
def client(data_dir: object) -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _jpeg(color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    im = Image.new("RGB", (64, 36), color)
    px = im.load()
    for x in range(0, 64, 2):
        px[x, 0] = ((x * 5) % 256, 0, 0)  # not a flat image: the bytes differ
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


FRAME_A = _jpeg((200, 30, 30))
FRAME_B = _jpeg((30, 30, 200))
META = {
    "provider": "youtube",
    "id": "abc123",
    "url": "https://www.youtube.com/watch?v=abc123",
    "channel": "A Channel",
    "duration": 3754,
    "published": "2024-01-01",
    "captions": "asr",
    "chapters": [{"t": 0, "title": "Intro"}, {"t": 60, "title": "The envelope"}],
}


def _data(blob: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(blob).decode()


PAGE = f"""<!doctype html><html><head><meta charset="utf-8"><title>A Talk</title>
<meta name="prax-video" content='{json.dumps(META)}'></head><body>
<article class="prax-video">
<h1>A Talk on Grains</h1>
<section class="description"><p>Granular synthesis, explained.</p></section>
<section class="transcript">
<figure data-t="0"><img src="{_data(FRAME_A)}" alt=""><figcaption>0:00 — welcome everyone</figcaption></figure>
<p data-t="0"><a href="https://www.youtube.com/watch?v=abc123&amp;t=0s">0:00</a> welcome everyone to this talk about granular synthesis and the ways grains of sound can be scattered.</p>
<p data-t="32"><a href="https://www.youtube.com/watch?v=abc123&amp;t=32s">0:32</a> the first thing to understand is the grain envelope, which shapes each tiny burst of sound.</p>
<figure data-t="65"><img src="{_data(FRAME_B)}" alt=""><figcaption>1:05 — here is the envelope on the slide</figcaption></figure>
<p data-t="65"><a href="https://www.youtube.com/watch?v=abc123&amp;t=65s">1:05</a> here is the envelope on the slide, a Hann window, the smooth rise and fall of amplitude.</p>
<p data-t="3700"><a href="https://www.youtube.com/watch?v=abc123&amp;t=3700s">1:01:40</a> much later we look at the density of grains per second and the fusion threshold.</p>
</section></article></body></html>"""


def test_the_transcript_parses_to_marked_markdown_with_chapters_and_frames() -> None:
    text = video.parse(PAGE.encode())
    assert text.startswith(
        "# A Talk on Grains\n\nA Channel · 2024-01-01 · 1:02:34 · https://"
    )
    assert "## Description\n\nGranular synthesis, explained.\n" in text
    assert "### Intro\n" in text and "### The envelope\n" in text
    assert "[0:32] the first thing to understand" in text
    assert "[1:01:40] much later" in text
    lines = text.splitlines()
    figs = [ln for ln in lines if ln.startswith("![")]
    assert len(figs) == 2 and figs[0].startswith("![0:00 — welcome everyone](figure:")
    assert figs[1].startswith("![1:05 — here is the envelope on the slide](figure:")
    # the chapter heading comes before the first element at or after its time
    assert lines.index("### The envelope") < lines.index(figs[1])
    with pytest.raises(video.NotATranscript):
        video.parse(b"<html><body><p>an ordinary page</p></body></html>")
    assert not video.is_transcript(b"<html><body>no</body></html>")


def test_timed_chunks_carry_their_moment() -> None:
    chunks = chunking.chunk(video.parse(PAGE.encode()))
    timed = [
        (c.kind, c.time, c.time_end, c.heading) for c in chunks if c.time is not None
    ]
    assert timed[0] == ("figure", 0, 65, ["A Talk on Grains", "Transcript", "Intro"])
    assert timed[1][:3] == ("text", 0, 65)
    # the two paragraphs after the second frame are one text chunk (short
    # paragraphs merge), so nothing timed follows the frame: no end known
    assert timed[2] == (
        "figure",
        65,
        None,
        ["A Talk on Grains", "Transcript", "The envelope"],
    )
    last = [c for c in chunks if c.time is not None][-1]
    assert last.kind == "text" and last.time == 65 and last.time_end is None
    assert "[1:01:40]" in last.text
    for c in chunks:
        loc = c.locator()
        assert ("time" in loc) == (c.time is not None)
    assert (
        chunking.parse_time("1:01:40") == 3700
        and chunking.format_time(3700) == "1:01:40"
    )


def test_a_video_capture_through_the_door(client: TestClient) -> None:
    con: sqlite3.Connection = client.app.state.con
    r = client.post(
        "/ingest/html",
        json={
            "url": META["url"],
            "html": PAGE,
            "domains": ["research"],
            "mode": "video",
            "note": "transcript, 2 frames",
            "video": META,
        },
    ).json()
    assert r["created"] and r["indexed"], r
    doc = client.get(f"/get/{r['doc_id']}").json()
    assert doc["meta"]["video"]["id"] == "abc123" and doc["meta"]["parser"] == "video"
    assert doc["meta"]["text_source"].startswith("video/")
    assert "[0:32] the first thing" in doc["text"]
    # the frames are figures the door serves out of the original
    chunks = store.list_chunks(con, r["doc_id"])
    figs = [c for c in chunks if c["kind"] == "figure"]
    assert len(figs) == 2 and figs[0]["locator"]["time"] == 0
    ref = figs[1]["data"]["ref"]
    served = client.get(f"/doc/{r['doc_id']}/figure/{ref}")
    assert served.status_code == 200 and served.content == FRAME_B
    # a video to the doctype filter, and not a web page
    hits = client.get("/search", params={"q": "grain envelope", "doctype": "video"})
    assert [h["doc_id"] for h in hits.json()] == [r["doc_id"]]
    assert (
        client.get("/search", params={"q": "grain envelope", "doctype": "web"}).json()
        == []
    )
    # the hand-out names the parser for a document that names it
    con.execute(
        "UPDATE documents SET text_hash = NULL, parsed_at = NULL WHERE id = ?",
        (r["doc_id"],),
    )
    con.commit()
    work._leases.clear()
    batch = client.get("/work/parse", params={"scope": "captures"}).json()
    item = next(i for i in batch["items"] if i["doc_id"] == r["doc_id"])
    assert item["extractor"] == "video"
    assert [e.name for e in parsers.candidates("text/html", "video")] == ["video"]
