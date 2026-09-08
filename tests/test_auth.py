"""Bearer token on the door: header or session cookie, loopback-only without
a token, static UI and /health always open."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import auth
from prax.api import app

REMOTE = ("100.64.0.9", 40000)  # a Tailscale-looking address


@pytest.fixture()
def remote(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_TOKEN", "s3cret-token")
    with TestClient(app, client=REMOTE) as c:
        yield c


def test_without_token_configured_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PRAX_TOKEN", raising=False)
    with TestClient(app) as local:  # the test client counts as loopback
        assert local.get("/search", params={"q": "x"}).status_code == 200
        assert local.get("/health").json() == {"ok": True, "auth": "loopback-only"}
        assert local.post("/session", json={"token": ""}).json()["cookie"] is False
    with TestClient(app, client=REMOTE) as far:
        r = far.get("/search", params={"q": "x"})
        assert r.status_code == 401 and "loopback" in r.json()["detail"]
        assert r.headers["www-authenticate"] == "Bearer"
        assert far.get("/ui/").status_code == 200  # static files stay open
        assert far.get("/health").status_code == 200
        assert far.post("/session", json={"token": "anything"}).status_code == 401


def test_header_and_cookie_grant_access(remote: TestClient) -> None:
    assert remote.get("/search", params={"q": "x"}).status_code == 401
    assert remote.get("/documents").status_code == 401
    assert remote.get("/", follow_redirects=False).status_code == 307  # open
    ok = remote.get(
        "/search", params={"q": "x"}, headers={"Authorization": "Bearer s3cret-token"}
    )
    assert ok.status_code == 200
    bad = remote.get(
        "/search", params={"q": "x"}, headers={"Authorization": "Bearer nope"}
    )
    assert bad.status_code == 401
    # the UI's path: exchange the token for the cookie once
    assert remote.post("/session", json={"token": "wrong"}).status_code == 401
    r = remote.post("/session", json={"token": "s3cret-token"})
    assert r.status_code == 200 and r.json()["cookie"] is True
    assert auth.COOKIE in r.cookies
    assert remote.get("/documents").status_code == 200  # cookie jar carries it
    assert remote.get("/doc/1/original").status_code == 404  # authorized, no doc
    remote.delete("/session")
    assert remote.get("/documents").status_code == 401


def test_constant_time_compare_and_presented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAX_TOKEN", "abc")
    assert auth.valid("abc") and not auth.valid("abd") and not auth.valid(None)
    monkeypatch.setenv("PRAX_TOKEN", "  ")
    assert auth.token() is None and not auth.valid("")
