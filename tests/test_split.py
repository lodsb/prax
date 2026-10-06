"""AK's split: two chat servers in one card group, the 27B answering
``ask`` and the 35B doing the bulk passes. A further chat server is the
role ``llama-server-<name>``, a reading waits for the role that serves its
step's model, and an ask takes the card for its own role."""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from prax import config, models, work
from prax.host import plan, up
from tests.test_up import child

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def split_host(data_dir: Path, tmp_path: Path) -> Path:
    """The 35B for the bulk steps and the vision readings, the 27B for
    ask, one card between them."""
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("big.gguf", "dense.gguf"):
        (tmp_path / name).write_bytes(b"GGUF")
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"MZ")
    (data_dir / config.CONFIG_NAME).write_text(
        textwrap.dedent(
            f"""
            models:
              big:
                kind: openai
                base_url: http://127.0.0.1:8085/v1
                model: q35
                serve: {{path: {(tmp_path / "big.gguf").as_posix()}}}
              dense:
                kind: openai
                base_url: http://127.0.0.1:8086/v1
                model: q27
                serve: {{path: {(tmp_path / "dense.gguf").as_posix()}}}
            paths:
              llama_server: {binary.as_posix()}
            steps:
              extract: {{model: big}}
              vision: {{model: big}}
              formulas: {{model: dense}}
              ask: {{model: dense}}
            run:
              llama-server: {{model: big, group: card}}
              llama-server-ask: {{model: dense, group: card, on_demand: true}}
              door: {{port: 8000}}
            """
        ),
        encoding="utf-8",
    )
    return data_dir


def test_a_further_chat_server_is_a_role_of_its_own(split_host: Path) -> None:
    roles = up.roles()
    assert [r.name for r in roles] == ["llama-server", "llama-server-ask", "door"]
    ask = roles[1]
    assert ask.group == "card" and ask.on_demand
    assert ask.health == "http://127.0.0.1:8086/health"
    assert "--port" in ask.argv and ask.argv[ask.argv.index("--port") + 1] == "8086"
    with pytest.raises(up.UpError, match="llama-server-<name>"):
        up.roles({"llama-servers": {"model": "big"}})
    with pytest.raises(up.UpError, match="unknown setting"):
        up.roles({"llama-server-ask": {"model": "dense", "venv": "x"}})


def test_each_step_waits_for_the_server_of_its_model(split_host: Path) -> None:
    assert work.role_of_step("extract") == "llama-server"
    assert work.role_of_step("ask") == "llama-server-ask"
    assert work.ask_role() == "llama-server-ask"
    by_role = work.role_work()
    # a figure is the vision step's, on the 35B; a formula reading the 27B's
    assert "figures" in by_role["llama-server"]
    assert "formulas" in by_role["llama-server-ask"]
    assert by_role["marker"] == ("marker",)


def test_an_ask_that_found_no_server_holds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(work, "_asks", {"running": 0, "ended": float("-inf")})
    with pytest.raises(models.ServerNotReady), work.asking():
        raise models.ServerNotReady("loading")
    assert not work.ask_holds()  # its role must be free to take the card
    with work.asking():
        pass
    assert work.ask_holds()


STATUS = {
    "roles": {
        "llama-server": {"state": "up", "load_s": 180.0},
        "llama-server-ask": {"state": "paused", "load_s": 45.0},
    },
    "groups": {"card": {"members": ["llama-server", "llama-server-ask"]}},
}


def _group(role: str, action: str, waiting: int) -> dict[str, Any]:
    return {
        "role": role,
        "action": action,
        "waiting": waiting,
        "rate": None,
        "hours_left": None,
        "now": action == "ask",
        "oldest": None,
        "asked_by": "human" if action == "ask" else "door",
    }


def test_an_ask_takes_the_card_from_a_serving_holder() -> None:
    """The 35B serves its readings; an ask for the 27B goes next anyway,
    a person waits for it, and the ask's hold does not stand in its way.
    The 35B's own work after it waits for the ask."""
    demand = {
        "groups": [
            _group("llama-server", "figures", 3),
            _group("llama-server-ask", "ask", 1),
        ],
        "ask_holds": True,
        "ask_role": "llama-server-ask",
    }
    got = plan.plan(demand, STATUS, now=NOW)
    rows = {r["role"]: r for r in got["groups"]}
    assert rows["llama-server"]["decision"] == "serving"
    ask = rows["llama-server-ask"]
    assert ask["decision"] == "next" and ask["why"] == "an ask waits for it"
    assert ask["swap_s"] == 45 + 180
    assert got["ask_role"] == "llama-server-ask"


def test_prax_up_gives_the_card_to_the_ask_and_keeps_it_there(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    up.run_dir(data_dir).mkdir(parents=True, exist_ok=True)
    _, argv_a = child(tmp_path, "bulk", 60)
    _, argv_b = child(tmp_path, "ask", 60)
    bulk = up.Role("llama-server", argv_a, group="card", needs_vram_mb=20000)
    # ``swap: ask``: only a person moves it, and an ask is one
    asks = up.Role(
        "llama-server-ask", argv_b, group="card", needs_vram_mb=17000, on_demand=True
    )
    sup = up.Supervisor([bulk, asks], data_dir=data_dir, say=lambda _l: None)
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 100)
    door: dict[str, Any] = {
        "roles": {"llama-server-ask": 1},
        "now": {},
        "ask_holds": True,  # an ask that answered a moment ago
        "plan": [{"role": "llama-server-ask", "action": "ask", "why": "an ask"}],
    }

    def ask_demand(self: up.Supervisor) -> None:
        self.demand, self.now = door["roles"], door["now"]
        self.ask_holds, self.plan = door["ask_holds"], door["plan"]
        self.ask_role = "llama-server-ask"

    monkeypatch.setattr(up.Supervisor, "_ask_demand", ask_demand)
    monkeypatch.setattr(up, "GROUP_QUIET", 0.0)
    sup._groups()
    assert sup.groups["card"]["holder"] == "llama-server-ask"
    assert "llama-server" in sup.paused
    # nothing waits now, but the ask's hold keeps the card with it
    door.update(roles={}, plan=[])
    sup._groups()
    sup._groups()
    assert sup.groups["card"]["holder"] == "llama-server-ask"
    # the hold over and nothing asked: the card goes back to the 35B
    door["ask_holds"] = False
    sup._groups()
    sup._groups()
    assert "card" not in sup.groups and "llama-server" not in sup.paused
    assert "llama-server-ask" in sup.paused


def test_an_idle_server_does_not_load_beside_another_member(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Work for an idled chat server while the other holds the card is the
    plan's swap, never a second model loading beside the first."""
    bulk = up.Role("llama-server", ["x"], group="card", needs_vram_mb=20000)
    asks = up.Role(
        "llama-server-ask",
        ["x"],
        group="card",
        needs_vram_mb=17000,
        idle_minutes=30,
        metrics="http://127.0.0.1:1/metrics",
    )
    sup = up.Supervisor([bulk, asks], data_dir=data_dir, say=lambda _l: None)
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 100)
    monkeypatch.setattr(up.Supervisor, "_ask_demand", lambda self: None)
    sup.state["llama-server"]["state"] = "up"
    sup.idled.add("llama-server-ask")
    sup.paused.add("llama-server-ask")
    sup.demand = {"llama-server-ask": 1}
    sup._idle()
    assert "llama-server-ask" in sup.paused  # left for the plan
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 40000)
    monkeypatch.setattr(
        up.hostinfo, "memory", lambda: {"ram_free_mb": 64000, "commit_free_mb": 64000}
    )
    sup._idle()
    assert "llama-server-ask" not in sup.paused  # room for both: it loads
