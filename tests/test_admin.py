"""Stage W, the administrative side: the tokens with when each was last
used, the modules a new one may be given, and the personal-document rules
in force, with the owner's names counted and never shown."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.wall import auth, private

ADMIN = "admin-secret"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_TOKEN", ADMIN)
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _as(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def test_a_tokens_last_use_is_kept_once_an_hour(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = client.get("/tokens", headers=_as(ADMIN)).json()
    assert listed["tokens"] == [] and "research" in listed["modules"]
    assert store.UNASSIGNED in listed["modules"]
    made = client.post(
        "/tokens", json={"name": "laptop", "domains": ["research"]}, headers=_as(ADMIN)
    ).json()
    before = client.get("/tokens", headers=_as(ADMIN)).json()["tokens"][0]
    assert before["name"] == "laptop" and before["last_used"] is None
    store.jobs._token_used.clear()
    assert (
        client.get(
            "/search", params={"q": "x"}, headers=_as(made["secret"])
        ).status_code
        == 200
    )
    first = client.get("/tokens", headers=_as(ADMIN)).json()["tokens"][0]["last_used"]
    assert first
    # within the hour a use writes nothing
    con = store.thread_connection()
    assert store.note_token_use(con, "laptop") is False
    monkeypatch.setattr(store.jobs, "TOKEN_USE_EVERY", 0.0)
    assert store.note_token_use(con, "laptop") is True


def test_the_rules_in_force_count_the_names_and_never_show_them(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        private,
        "rules",
        lambda: private.Rules(names=("Jane Example",), paths=("/admin/",)),
    )
    got = client.get("/private/rules", headers=_as(ADMIN))
    assert got.status_code == 200
    assert "Jane Example" not in got.text
    r = got.json()
    assert r["added"]["names"] == 1 and r["added"]["paths"] == ["/admin/"]
    assert (
        "kontoauszug" in r["strong"]
        and ["rechnung", "invoice", "quittung", "receipt"] in r["weak"]
    )
    assert r["documents"] == {"suspected": 0, "personal": 0, "released": 0}


def test_a_named_token_reaches_no_admin_route(client: TestClient) -> None:
    made = client.post("/tokens", json={"name": "reader"}, headers=_as(ADMIN)).json()
    for method, path in (
        ("GET", "/tokens"),
        ("POST", "/tokens"),
        ("DELETE", "/tokens/reader"),
        ("GET", "/private/rules"),
        ("GET", "/documents/suspected"),
        ("GET", "/cleanup"),
    ):
        assert not any(
            m == method and pattern.fullmatch(path)
            for m, pattern in auth.RESTRICTED_ROUTES
        ), path
        got = client.request(method, path, headers=_as(made["secret"]))
        assert got.status_code == 403, (method, path, got.status_code)
