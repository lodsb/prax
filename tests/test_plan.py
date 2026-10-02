"""The card's plan (stage AI, step 4): what waits, what a swap costs,
and the order the card would serve it in (``prax.host.plan``)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prax.host import plan

NOW = datetime(2026, 10, 2, 20, 0, 0, tzinfo=UTC)


def group(role: str, action: str, waiting: int, **kw: Any) -> dict[str, Any]:
    return {
        "role": role,
        "action": action,
        "waiting": waiting,
        "rate": kw.get("rate"),
        "hours_left": None,
        "now": False,
        "oldest": kw.get("oldest"),
        "asked_by": kw.get("asked_by", "human"),
    }


STATUS = {
    "roles": {
        "llama-server": {"state": "up", "load_s": 180.0},
        "marker": {"state": "paused", "load_s": 40.0},
    },
    "groups": {"card": {"members": ["llama-server", "marker"]}},
}


def test_the_holder_serves_and_a_swap_waits_until_its_wait_outgrows_it() -> None:
    """The role on the card serves without a swap; a person's marker
    reading waits until it has waited three times the swap (marker's load
    plus llama-server's back), and a door's twenty times."""
    demand = {
        "groups": [
            group("llama-server", "vision-pages", 2, oldest="2026-10-02T19:59:00Z"),
            group("marker", "marker", 2, oldest="2026-10-02T19:55:00Z"),
        ]
    }
    got = plan.plan(demand, STATUS, now=NOW)
    serving, marker = got["groups"]
    assert serving["decision"] == "serving" and serving["swap_s"] == 0
    assert marker["swap_s"] == 220 and not marker["swap_guessed"]
    assert marker["decision"] == "waits"  # 300 s waited, 660 s due
    assert "360 s more" in marker["why"]
    assert got["order"] == ["llama-server"]
    later = datetime(2026, 10, 2, 20, 6, 0, tzinfo=UTC)  # 660 s waited
    got = plan.plan(demand, STATUS, now=later)
    # llama-server still serves: it keeps the card, marker goes after it
    assert got["groups"][1]["decision"] == "waits"
    assert got["groups"][1]["why"] == "after llama-server"
    demand["groups"].pop(0)  # its readings done
    got = plan.plan(demand, STATUS, now=later)
    assert got["groups"][0]["decision"] == "next"
    assert got["order"] == ["marker"]
    # asked by the door, the same wait is not enough, except at night
    demand["groups"][0]["asked_by"] = "door"
    assert plan.plan(demand, STATUS, now=later)["groups"][0]["decision"] == "waits"
    (row,) = plan.plan(demand, STATUS, now=later, night=True)["groups"]
    assert row["decision"] == "next" and row["why"] == "the night window"


def test_the_night_window_starts_at_the_nightly_hour() -> None:
    at = datetime(2026, 10, 2, 3, 30, tzinfo=UTC)
    assert plan.night_now("03:00", at)
    assert plan.night_now("00:00", at)  # the window from midnight reaches 03:30
    assert not plan.night_now("22:00", at)
    assert not plan.night_now("04:00", at)
    assert not plan.night_now(None, at) and not plan.night_now("soon", at)


def test_only_a_person_takes_the_card_from_a_holder_still_serving() -> None:
    demand = {
        "groups": [
            group("llama-server", "vision-pages", 2),
            group("marker", "marker", 1),
        ],
        "now": {"marker": None},
    }
    marker = plan.plan(demand, STATUS, now=NOW)["groups"][1]
    assert marker["decision"] == "next"
    # the other way round, after the card went back: marker does not take
    # it again while llama-server's asked-for work waits
    demand["now"] = {"llama-server": None}
    demand["groups"][1]["oldest"] = "2026-10-02T12:00:00Z"  # long past due
    marker = plan.plan(demand, STATUS, now=NOW)["groups"][1]
    assert marker["decision"] == "waits"
    assert marker["why"] == "after llama-server"


def test_do_it_now_and_a_big_batch_go_next_and_an_ask_holds_them() -> None:
    demand = {
        "groups": [group("marker", "marker", 3, oldest="2026-10-02T19:59:50Z")],
        "now": {"marker": "marker"},
        "ask_holds": True,
    }
    (row,) = plan.plan(demand, STATUS, now=NOW)["groups"]
    assert row["decision"] == "next"
    assert row["why"] == "a person asked for it now, after the ask"
    big = {"groups": [group("marker", "marker", plan.BATCH_ITEMS, asked_by="door")]}
    (row,) = plan.plan(big, STATUS, now=NOW)["groups"]
    assert row["decision"] == "next" and "pay the swap back" in row["why"]


def test_a_card_on_loan_and_load_times_never_measured() -> None:
    """With marker holding the card, llama-server's work needs the swap
    back; a role that never loaded here costs ``LOAD_GUESS_S``, said so.
    Without a supervisor every role is a guess and nothing holds."""
    lent = {
        "roles": {
            "llama-server": {"state": "paused"},
            "marker": {"state": "up", "load_s": 40.0},
        },
        "groups": {"card": {"members": ["llama-server", "marker"], "holder": "marker"}},
    }
    demand = {"groups": [group("llama-server", "vision", 1, rate=60.0)]}
    (row,) = plan.plan(demand, lent, now=NOW)["groups"]
    assert row["holder"] == "marker"
    assert row["swap_s"] == plan.LOAD_GUESS_S + 40 and row["swap_guessed"]
    assert row["work_s"] == 60
    assert row["waited_s"] is None and row["decision"] == "waits"
    (row,) = plan.plan(demand, None, now=NOW)["groups"]
    assert row["holder"] is None and row["swap_s"] == plan.LOAD_GUESS_S
