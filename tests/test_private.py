"""Stage V, what is personal: the rules suspect, a person decides, and the
rules never overrule a person. The owner's name in these tests is an
invented one; the real one lives in the host's prax.yaml only."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import auth, private, store

RULES = private.Rules(names=("Jane Example",), paths=("/volume1/admin/",))


def test_a_strong_cue_suspects_alone_and_a_weak_one_needs_another() -> None:
    assert RULES.suspect(RULES.cues("Kontoauszug_2019.pdf", [], ""))
    bill = RULES.cues("Rechnung Mai", [], "An Jane Example, Musterstr. 1")
    assert bill == ["weak: rechnung", "name"] and RULES.suspect(bill)
    calculus = RULES.cues("Rechnung", [], "Differential- und Integralrechnung")
    assert not RULES.suspect(calculus)


def test_synonyms_are_one_cue() -> None:
    """ "Rechnung" and "invoice" on one bill, or two identity documents on
    an exam leaflet, are one thing said twice."""
    leaflet = RULES.cues("Merkblatt zur Klausur", [], "Personalausweis oder Reisepass")
    assert leaflet == ["weak: personalausweis"] and not RULES.suspect(leaflet)
    assert RULES.cues("Rechnung / Invoice", [], "") == ["weak: rechnung"]


def test_an_iban_counts_only_when_its_check_digits_add_up() -> None:
    assert private.iban_ok("DE89370400440532013000")
    assert not private.iban_ok("DE89370400440532013001")
    donation = RULES.cues("An article", [], "Spenden: DE89 3704 0044 0532 0130 00")
    assert donation == ["weak: iban"] and not RULES.suspect(donation)
    assert RULES.suspect(RULES.cues("Jane Example", [], "IBAN DE89370400440532013000"))


def test_a_name_is_found_glued_into_a_file_name_and_never_spelled_out() -> None:
    cues = RULES.cues("2013_RE1300JaneExample.pdf", [], "")
    assert cues == ["name"]  # the cue is kept and shown: it never says whose


def test_a_path_suspects_everything_under_it() -> None:
    cues = RULES.cues("Einspruch.odt", ["/volume1/admin/finanzamt/Einspruch.odt"], "")
    assert "path: /volume1/admin/" in cues and RULES.suspect(cues)


def test_the_stamp_changes_with_the_rules() -> None:
    assert (
        RULES.stamp()
        == private.Rules(names=("Jane Example",), paths=("/volume1/admin/",)).stamp()
    )
    assert RULES.stamp() != private.Rules(names=("Jane Example",)).stamp()


def test_prax_yaml_adds_to_the_rules(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "prax.yaml"
    cfg.write_text(
        "private:\n  names: [Jane Example]\n  weak: [Kaution]\n  weak_needed: 3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRAX_CONFIG", str(cfg))
    rules = private.rules()
    assert rules.names == ("Jane Example",) and rules.weak_needed == 3
    assert ("kaution",) in {tuple(w.lower() for w in g) for g in rules.weak}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _meta(con: sqlite3.Connection, doc_id: int) -> dict:
    return json.loads(
        con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()[0]
    )


def paper_of(con: sqlite3.Connection) -> int:
    """The one document of the test that shows no cue."""
    return int(
        con.execute("SELECT id FROM documents WHERE title = 'Notes only'").fetchone()[0]
    )


def _state(con: sqlite3.Connection, doc_id: int) -> str | None:
    return con.execute(
        "SELECT sensitivity FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()[0]


def test_a_statement_is_suspected_as_it_is_indexed(client: TestClient) -> None:
    con = client.app.state.con
    bank = int(
        store.ingest_text(con, "Kontoauszug Nr. 6 vom 24.08.2009 " * 5, title="Auszug")[
            "doc_id"
        ]
    )
    paper = int(
        store.ingest_text(con, "The wavelet transform. " * 20, title="Paper")["doc_id"]
    )
    assert _state(con, bank) == "suspected" and _state(con, paper) is None
    assert _meta(con, bank)["private"]["cues"] == ["strong: kontoauszug"]
    assert _meta(con, bank)["sensitivity"]["by"] == "rules"
    page = client.get("/documents/suspected").json()
    assert page["total"] == 1 and page["items"][0]["cues"] == ["strong: kontoauszug"]
    # a restricted token may not see the list at all
    assert "/documents/suspected" not in auth.RESTRICTED_ROUTES


def test_a_person_decides_and_the_rules_never_overrule_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    bank = int(store.ingest_text(con, "Kontoauszug " * 20, title="Auszug")["doc_id"])
    store.ingest_text(con, "Wavelets " * 20, title="Notes only")
    # released by a person: open, and it stays open whatever the rules say
    got = client.put(f"/doc/{bank}/sensitivity", json={"state": None})
    assert got.status_code == 200
    assert _state(con, bank) is None
    assert store.suspect(con, bank) is None
    store.maintain(con, only=["private"])
    assert _state(con, bank) is None
    assert client.get("/documents/suspected").json()["total"] == 0
    # a new rule reads the library again, and still leaves the person's word
    other = int(store.ingest_text(con, "Notes " * 20, title="Einspruch")["doc_id"])
    con.execute(
        "UPDATE documents SET original_path = '/admin/Einspruch.odt' WHERE id = ?",
        (other,),
    )
    con.commit()
    assert _state(con, other) is None
    monkeypatch.setattr(private, "rules", lambda: private.Rules(paths=("/admin/",)))
    out = store.maintain(con, only=["private"])["private"]
    assert out["suspected"] == 1
    assert _state(con, other) == "suspected" and _state(con, bank) is None
    # confirmed: personal, and no longer on the list
    client.put(f"/doc/{other}/sensitivity", json={"state": "personal"})
    assert _state(con, other) == "personal"
    assert client.get("/documents/suspected").json()["total"] == 0
    # an unchanged rule reads again only what showed no cue, and marks nothing
    assert store.maintain(con, only=["private"])["private"]["suspected"] == 0
    assert "private" not in _meta(con, paper_of(con))
