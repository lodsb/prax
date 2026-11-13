"""Pointers, ``prax:<slug>`` (plan item AU, step 3): a name for a page or a
project's file or journal entry that outlives a sync, which retires the
document a changed file was."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.text import slugs


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_EMBED", "hash")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _file(path: str, text: str) -> dict[str, Any]:
    raw = text.encode("utf-8")
    return {
        "path": path,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "text": text,
    }


def _sync(client: TestClient, files: list[dict[str, Any]]) -> dict[str, Any]:
    body = {
        "remote": "example.org/someone/motor",
        "prefix": "",
        "name": "Motor Rig",
        "files": files,
        "dry_run": False,
    }
    r = client.post("/projects/sync", json=body)
    assert r.status_code == 200, r.text
    return r.json()


LOG = (
    "# Log\n\n## 2026-11-08\n\nZero.\n\n## 2026-11-09\n\nOne.\n\n"
    "## 2026-09-16 (late)\n\nTwo.\n\n"
)


def test_the_grammar() -> None:
    p = slugs.parse_pointer
    assert p("prax:onset-notes") == slugs.Pointer(page="onset-notes")
    assert p("prax:Motor Rig/docs/a.md") == slugs.Pointer(
        project="motor-rig", path="docs/a.md"
    )
    assert p("prax:motor-rig/log.md#2026-09-16 (late)") == slugs.Pointer(
        project="motor-rig", path="log.md", entry="2026-09-16-late"
    )
    assert p("prax%3Amotor-rig%2Flog.md") == slugs.Pointer(
        project="motor-rig", path="log.md"
    )
    assert p("onset-notes") is None and p("prax:") is None
    assert slugs.pointer("Motor Rig", "log.md", "2026-09-16 (late)") == (
        "prax:motor-rig/log.md#2026-09-16-late"
    )


def test_a_pointer_names_the_current_text_across_syncs(client: TestClient) -> None:
    con = client.app.state.con
    out = _sync(client, [_file("docs/a.md", "# A\n\nFirst.\n"), _file("log.md", LOG)])
    first = next(p["doc_id"] for p in out["plan"] if p["path"] == "docs/a.md")
    got = client.get("/resolve", params={"ref": "prax:motor-rig/docs/a.md"}).json()
    assert got == {
        "pointer": "prax:motor-rig/docs/a.md",
        "doc_id": first,
        "title": "A (Motor Rig)",
        "kind": "file",
    }
    assert client.get(f"/get/{first}").json()["pointer"] == "prax:motor-rig/docs/a.md"

    edited = LOG.replace("One.", "One, and a correction.")
    out = _sync(
        client, [_file("docs/a.md", "# A\n\nSecond.\n"), _file("log.md", edited)]
    )
    second = next(p["doc_id"] for p in out["plan"] if p["path"] == "docs/a.md")
    assert second != first
    got = client.get("/resolve", params={"ref": "prax:motor-rig/docs/a.md"}).json()
    assert got["doc_id"] == second
    # the retired text keeps its pointer, which finds the new one
    assert store.pointer_of(con, first) == "prax:motor-rig/docs/a.md"

    newest = next(p["doc_id"] for p in out["plan"] if p.get("entry") == "2026-11-09")
    got = client.get(
        "/resolve", params={"ref": "prax:motor-rig/log.md#2026-11-09"}
    ).json()
    assert got["doc_id"] == newest and got["kind"] == "entry"
    late = client.get(
        "/resolve", params={"ref": "prax:motor-rig/log.md#2026-09-16-late"}
    )
    assert late.status_code == 200
    assert late.json()["pointer"] == "prax:motor-rig/log.md#2026-09-16-late"

    assert (
        client.get("/resolve", params={"ref": "prax:motor-rig/nope.md"}).status_code
        == 404
    )
    assert client.get("/resolve", params={"ref": "nope"}).status_code == 400


def test_a_page_links_by_pointer(client: TestClient) -> None:
    """A page's ``[title](prax:…)`` is a link like ``#doc/N``: its
    ``annotates`` edge goes to what the pointer names now."""
    con = client.app.state.con
    out = _sync(client, [_file("docs/a.md", "# A\n\nFirst.\n")])
    doc = out["plan"][0]["doc_id"]
    page = store.write_page(
        con, "claims", "# Claims\n\nSee [A](prax:motor-rig/docs/a.md).\n"
    )
    got = client.get("/resolve", params={"ref": "prax:claims"}).json()
    assert got["doc_id"] == page["doc_id"] and got["kind"] == "page"
    assert store.page_links(con, "[A](prax:motor-rig/docs/a.md) [x](prax:none)") == [
        doc
    ]
    annotated = con.execute(
        "SELECT count(*) FROM edges e JOIN entities t ON t.id = e.dst"
        " WHERE e.source_doc = ? AND e.rel = 'annotates' AND e.valid_to IS NULL"
        " AND t.name = 'A (Motor Rig)'",
        (page["doc_id"],),
    ).fetchone()[0]
    assert annotated == 1
