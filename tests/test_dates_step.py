"""When a document was published, read from its first page by a model
(the ``dates`` step; ``prax.writing.dates``)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import models, store, work, worker
from prax.writing import dates as reading

PAGE = (
    "Proceedings of the 12th International Conference on Digital Audio Effects"
    " (DAFx-09), Como, Italy, September 1-4, 2009\n\nONSET DETECTION REVISITED\n\n"
    "We compare onset detection functions, building on Bello et al. (2005)."
)


def test_a_date_is_believed_only_when_the_page_says_it() -> None:
    got = reading.checked("2009-09 | Como, Italy, September 1-4, 2009", PAGE)
    assert got is not None and got.date == "2009-09" and got.confidence == "medium"
    got = reading.checked(
        "2009 | Conference on Digital Audio Effects (DAFx-09), Como, Italy,"
        " September 1-4, 2009",
        PAGE,
    )
    assert got is not None and got.confidence == "high"
    assert reading.checked("none", PAGE) is None
    assert (
        reading.checked("2005 | Bello et al. (2005)", PAGE) is not None
    )  # on the page
    assert reading.checked("2011 | Proceedings of DAFx-11", PAGE) is None  # not on it
    assert (
        reading.checked("2010 | September 1-4, 2009", PAGE) is None
    )  # words, other year
    assert reading.checked("soon | Proceedings", PAGE) is None
    assert reading.checked("", PAGE) is None


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_step_dates_what_nothing_else_did(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handed out: documents with text and no date, newest first; the
    model's checked answer is kept below every other source, a document it
    could not date is marked tried and not asked again."""
    con = client.app.state.con
    paper = store.ingest_text(con, PAGE, title="Onset detection revisited")["doc_id"]
    vague = store.ingest_text(con, "A note without a date. " * 10, title="note")[
        "doc_id"
    ]
    dated = store.ingest_text(
        con, PAGE + " Another paper.", title="dated", meta={"date": "2010"}
    )["doc_id"]
    store.maintain(con, only=["published"])  # the record's date for `dated`
    spec = models.ModelSpec(
        name="local", kind="openai", base_url="http://127.0.0.1:1/v1", model="m"
    )
    monkeypatch.setattr(models, "resolve", lambda s: spec if s == "dates" else None)

    class FakeRuntime:
        def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, Any]]:
            if "ONSET DETECTION" in user:
                return "2009 | Conference on Digital Audio Effects (DAFx-09)", {}
            return "none", {}

    monkeypatch.setattr(models, "runtime", lambda s: FakeRuntime())
    handed = {i["doc_id"] for i in client.get("/work/dates").json()["items"]}
    assert handed == {paper, vague}  # `dated` has its record's date
    work._leases.clear()
    door = worker.Door("http://testserver", client=client, name="test-worker")
    out = worker.run_once(door, steps=("dates",), log_=lambda t: None)
    assert out["dates"].startswith("1 dated, 1 without")
    published = store.get_meta(con, paper)["published"]
    assert published["date"] == "2009" and published["by"] == "first-page"
    assert published["confidence"] == "high" and "DAFx-09" in published["words"]
    assert "published_tried" in store.get_meta(con, vague)
    assert client.get("/work/dates").json()["items"] == []  # nothing asked again
    # a model's date never replaces a more trusted one; a person's does
    store.set_published(con, dated, "2003", by="first-page")
    assert store.get_meta(con, dated)["published"]["date"] == "2010"
    store.set_published(con, paper, "2009-09-01")
    assert store.get_meta(con, paper)["published"]["by"] == "human"
