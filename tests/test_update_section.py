"""A page's section replaced in place (``store.update_section``,
``PUT /page/{slug}/section``; AL step 6): an agent's status block it
rewrites, never a person's text, never an ask block."""

from __future__ import annotations

import sqlite3

import pytest

from prax import store
from prax.text import markup

PAGE = """# Synth

What the person wrote about the synth.

## Status

Old status, written by the agent.

### Detail

A sub-point of the status.

## Decisions

The person's decisions.
"""


def test_the_span_of_a_section() -> None:
    start, end, level = markup.section_span(PAGE, "status")  # type: ignore[misc]
    assert level == 2
    assert PAGE[start:end].strip().startswith("Old status")
    assert "### Detail" in PAGE[start:end] and "## Decisions" not in PAGE[start:end]
    assert markup.section_span(PAGE, "Nothing") is None
    fenced = "```\n## Status\n```\n## Status\nreal\n"
    s, e, _ = markup.section_span(fenced, "Status")  # type: ignore[misc]
    assert fenced[s:e] == "real\n"


def _page(con: sqlite3.Connection) -> None:
    # the person leaves the Status heading empty for the agent to keep
    human = (
        "# Synth\n\nWhat the person wrote about the synth.\n\n## Status\n\n"
        "## Decisions\n\nThe person's decisions.\n"
    )
    store.write_page(con, "synth", human, author="human")
    store.update_section(con, "synth", "Status", "Old status, written by the agent.")


def test_an_agent_rewrites_its_own_section_in_place(con: sqlite3.Connection) -> None:
    _page(con)
    out = store.update_section(con, "synth", "status", "New status: all green.")
    assert out["section"] == "replaced"
    text = store.get_page(con, "synth")["text"]
    assert "New status: all green." in text and "Old status" not in text
    assert text.count("## Status") == 1
    assert "The person's decisions." in text and "What the person wrote" in text
    # a heading the page lacks is added at the end
    added = store.update_section(con, "synth", "Open questions", "None yet.")
    assert added["section"] == "added"
    assert store.get_page(con, "synth")["text"].rstrip().endswith("None yet.")


def test_a_persons_section_is_refused_unless_forced(con: sqlite3.Connection) -> None:
    _page(con)
    with pytest.raises(PermissionError):
        store.update_section(con, "synth", "Decisions", "The agent's decisions.")
    out = store.update_section(con, "synth", "Decisions", "Agreed.", force=True)
    assert out["section"] == "replaced"


def test_an_ask_block_is_the_questions_pass(con: sqlite3.Connection) -> None:
    store.write_page(
        con,
        "q",
        '# Q\n\n## Asked\n\n<!-- prax:ask id=q1 "why" -->\n<!-- /prax:ask id=q1 -->\n',
        author="agent",
    )
    with pytest.raises(ValueError):
        store.update_section(con, "q", "Asked", "gone")


def test_the_route(con: sqlite3.Connection) -> None:
    from fastapi.testclient import TestClient

    from prax.api import app

    with TestClient(app) as client:
        c = client.app.state.con
        _page(c)
        r = client.put(
            "/page/synth/section", json={"heading": "Status", "text": "Fine."}
        )
        assert r.status_code == 200 and r.json()["section"] == "replaced"
        r = client.put(
            "/page/synth/section", json={"heading": "Decisions", "text": "x"}
        )
        assert r.status_code == 409
        r = client.put("/page/nope/section", json={"heading": "A", "text": "x"})
        assert r.status_code == 404
