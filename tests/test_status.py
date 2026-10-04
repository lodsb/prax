"""A document's own status (``prax.text.status``): front matter and an
explicit status line near the top, never a word in passing."""

from __future__ import annotations

from prax.text import status

HERE = "fw/docs/plan.md"


def test_a_status_line_with_a_date_and_a_replacement() -> None:
    text = (
        "# The re-baseline plan\n\n"
        "> **Status:** superseded 2026-10-02 by [the new plan](plan-v2.md)\n\n"
        "The numbers below no longer hold.\n"
    )
    got = status.read(text, HERE, "fw")
    assert got is not None and got.state == "superseded" and got.stale
    assert got.since == "2026-10-02"
    assert got.refs and got.refs[0][0] == ["fw/docs/plan-v2.md"]
    assert "superseded 2026-10-02" in got.words


def test_a_bare_state_word_at_a_line_start() -> None:
    got = status.read("# Notes\n\nRETIRED 2026-09 — see `docs/new.md`.\n", HERE, "fw")
    assert got is not None and (got.state, got.since) == ("retired", "2026-09")
    assert ["docs/new.md", "fw/docs/new.md", "fw/docs/docs/new.md"] in [
        c for c, _ in got.refs
    ]
    assert status.read("**Deprecated** since 2026-08-01.\n", HERE).state == "deprecated"
    assert (
        status.read("> [!WARNING] Invalid: the table is wrong.\n", HERE).state
        == "invalid"
    )


def test_front_matter() -> None:
    text = "---\ntitle: Plan\nstatus: retired\nretired: 2026-10-02\n---\n# Plan\n"
    got = status.read(text, HERE, "fw")
    assert got is not None and (got.state, got.since) == ("retired", "2026-10-02")
    swapped = "---\nsuperseded-by: docs/plan-v2.md\n---\nbody\n"
    got = status.read(swapped, HERE, "fw")
    assert got is not None and got.state == "superseded"
    assert "fw/docs/plan-v2.md" in got.refs[0][0]
    assert status.read("---\nstatus: accepted\n---\n", HERE).state == "current"


def test_what_says_nothing_about_the_document() -> None:
    assert (
        status.read("# The retired voice board\n\nIt was replaced in May.\n", HERE)
        is None
    )
    assert status.read("We retired the old board last year.\n", HERE) is None
    assert (
        status.read("Current draft of the plan.\n", HERE) is None
    )  # prose, not a status
    deep = "# Notes\n" + "text\n" * 40 + "Retired 2026-10-02\n"
    assert status.read(deep, HERE) is None  # past the head of the document
    assert status.read("Status: current\n", HERE).state == "current"
