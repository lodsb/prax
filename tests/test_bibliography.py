"""A paper's reference list and what a set of papers cites that the
library lacks (AL step 7): read from the reference entries, never from
``cites`` edges."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import store


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_EMBED", "hash")
    from prax.api import app

    with TestClient(app) as c:
        yield c


HELD = "Spectral flux revisited for onset detection"


def _paper(title: str, refs: list[str]) -> str:
    body = (
        f"# {title}\n\n"
        + "Onsets are where notes begin. " * 30
        + "\n\n## References\n\n"
    )
    return body + "\n\n".join(f"[{i}] {r}" for i, r in enumerate(refs, 1)) + "\n"


def _library(client: TestClient) -> tuple[int, int, int]:
    con = client.app.state.con
    held = store.ingest_text(con, f"# {HELD}\n\n" + "flux " * 80, title=HELD)["doc_id"]
    shared = (
        'J. P. Bello, L. Daudet, "A tutorial on onset detection in music signals,"'
        " IEEE Trans. Speech Audio Process., 2005, doi:10.1109/TSA.2005.851998."
    )
    a = store.ingest_text(
        con,
        _paper(
            "Onsets A",
            [
                shared,
                f'S. Bock, G. Widmer, "{HELD}," in Proc. DAFx, 2013.',
                (
                    'A. Klapuri, "Sound onset detection by applying psychoacoustic'
                    ' knowledge," in Proc. ICASSP, 1999.'
                ),
            ],
        ),
        title="Onsets A",
    )["doc_id"]
    b = store.ingest_text(
        con,
        _paper(
            "Onsets B",
            [
                shared,
                (
                    'N. Collins, "Using a pitch detector for onset detection," in'
                    " Proc. ISMIR, 2005. arXiv:2101.00001."
                ),
            ],
        ),
        title="Onsets B",
    )["doc_id"]
    store.maintain(con, only=["references"])
    return held, a, b


def test_a_papers_reference_list(client: TestClient) -> None:
    held, a, _b = _library(client)
    out = client.get(f"/doc/{a}/references").json()
    assert out["entries"] == 3 and out["in_library"] == 1
    first, second, _third = out["references"]
    assert first["n"] == 1 and first["year"] == 2005
    assert first["links"]["doi"] == "https://doi.org/10.1109/tsa.2005.851998"
    assert second["in_library"]["doc_id"] == held and "links" not in second
    assert client.get("/doc/999999/references").status_code == 404


def test_what_a_set_cites_that_the_library_lacks(client: TestClient) -> None:
    _held, a, b = _library(client)
    out = client.post("/references/missing", json={"doc_ids": [a, b]}).json()
    assert (out["looked_at"], out["documents"]) == (2, 2)
    top = out["missing"][0]
    assert top["count"] == 2 and sorted(top["cited_by"]) == sorted([a, b])
    assert top["doi"] == "10.1109/tsa.2005.851998"
    titles = [w.get("title") or "" for w in out["missing"]]
    assert not any(HELD.lower() in t.lower() for t in titles)  # held: not missing
    collins = next(
        w for w in out["missing"] if "pitch detector" in (w.get("title") or "")
    )
    assert collins["links"]["arxiv"] == "https://arxiv.org/abs/2101.00001"
    # a page's links are a set too
    store.write_page(
        client.app.state.con, "onsets", f"# Onsets\n\n[A](#doc/{a}), [B](#doc/{b})\n"
    )
    by_page = client.post("/references/missing", json={"page": "onsets"}).json()
    assert by_page["missing"][0]["count"] == 2
    assert client.post("/references/missing", json={}).status_code == 400
