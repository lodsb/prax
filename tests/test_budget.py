"""The ledger and the budget: what a paid call costs, what it may cost."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import budget, config, models, store, work


def _paid_host(data_dir: Path, *, daily: float = 0.0, monthly: float = 0.0) -> None:
    """A host whose extract and ask steps are a priced API model."""
    lines = [
        "models:",
        "  api:",
        "    kind: openai",
        "    base_url: https://api.example.com/v1",
        "    model: gpt-x",
        "    price: [2, 8]",
        "steps:",
        "  extract: {model: api}",
        "  ask: {model: api}",
    ]
    if daily or monthly:
        lines += ["budget:"]
        if daily:
            lines.append(f"  daily_usd: {daily}")
        if monthly:
            lines.append(f"  monthly_usd: {monthly}")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    models.reset()


def test_a_priced_api_model_is_paid_and_a_local_one_is_not(data_dir: Path) -> None:
    """What counts as money: a Claude model, or any model the file
    prices. An OpenAI-shaped endpoint at somebody's API used to slip the
    worker's gate because only `kind: claude` was paid."""
    _paid_host(data_dir)
    spec = models.resolve("extract")
    assert spec is not None and spec.paid
    local = models._spec_from(
        "l", {"kind": "openai", "base_url": "http://127.0.0.1:8080/v1", "model": "q"}
    )
    assert not local.paid
    priced_local = models._spec_from(
        "l2",
        {
            "kind": "openai",
            "base_url": "http://127.0.0.1:8080/v1",
            "model": "q",
            "price": [1, 1],
            "paid": False,  # priced for the ledger's sake, but free
        },
    )
    assert not priced_local.paid


def test_the_ledger_records_what_a_call_cost(
    con: sqlite3.Connection, data_dir: Path
) -> None:
    _paid_host(data_dir)
    usd = budget.note(
        con, "extract", {"input_tokens": 1_000_000, "output_tokens": 100_000}, doc_id=7
    )
    assert usd == pytest.approx(2 + 0.8)  # 2 per million in, 8 per million out
    assert store.spent_usd(con, budget.day_start()) == pytest.approx(2.8)
    led = store.spending(con)
    assert led["calls"] == 1 and led["by_step"][0]["step"] == "extract"
    assert led["by_model"][0]["model"] == "gpt-x@api.example.com"
    assert led["recent"][0]["doc_id"] == 7
    # a local model is not in the ledger at all
    (data_dir / config.CONFIG_NAME).write_text(
        "models:\n  l: {kind: openai, base_url: 'http://127.0.0.1:8080/v1', model: q}\n"
        "steps:\n  titles: {model: l}\n",
        encoding="utf-8",
    )
    models.reset()
    assert budget.note(con, "titles", {"input_tokens": 9_000_000}) == 0.0
    assert store.spending(con)["calls"] == 1


def test_the_budget_holds_the_paid_work_once_it_is_spent(
    con: sqlite3.Connection, data_dir: Path
) -> None:
    """The gate is the money already spent: under the limit the work is
    handed out, over it the batch comes back empty with a reason. A step
    with no model of its own (parse) is never gated."""
    _paid_host(data_dir, daily=1.0)
    assert budget.allows(con, "extract") == (True, "")
    assert budget.allows(con, "parse") == (True, "")
    doc = store.ingest_text(con, "a paper about diodes " * 40, title="D")["doc_id"]
    out = work.hand_out(con, "extract", limit=5, scope="all")
    assert [it["doc_id"] for it in out["items"]] == [doc]
    budget.note(con, "extract", {"input_tokens": 400_000, "output_tokens": 25_000})
    may, why = budget.allows(con, "extract")
    assert may is False and "today's 1.00 USD is spent" in why
    held = work.hand_out(con, "extract", limit=5, scope="all")
    assert held["items"] == [] and "costs money" in held["held"]
    state = budget.state(con)
    assert state["ok"] is False and state["left"]["day"] <= 0
    # the month's limit bites the same way, and a host with no limits never does
    _paid_host(data_dir, monthly=0.5)
    assert budget.allows(con, "extract")[0] is False
    _paid_host(data_dir)
    assert budget.allows(con, "extract") == (True, "")
    work._release("extract", [doc])  # the first hand-out leased it
    again = work.hand_out(con, "extract", limit=5, scope="all")
    assert [it["doc_id"] for it in again["items"]] == [doc]


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_door_reports_the_spending_and_refuses_a_paid_ask(
    client: TestClient, data_dir: Path
) -> None:
    _paid_host(data_dir, daily=0.01)
    con = client.app.state.con
    client.post("/ingest", json={"text": "granular synthesis " * 30, "title": "g"})
    view = client.get("/spending").json()
    assert view["budget"]["limits"]["daily_usd"] == 0.01
    assert view["ledger"]["calls"] == 0 and view["budget"]["ok"] is True
    budget.note(con, "extract", {"input_tokens": 100_000, "output_tokens": 10_000})
    view = client.get("/spending").json()
    assert view["budget"]["ok"] is False and view["ledger"]["calls"] == 1
    assert view["ledger"]["by_step"][0]["usd"] > 0
    # the bundle still comes back; the paid backend does not answer
    out = client.post("/ask", json={"question": "granular", "steps": 0}).json()
    assert out["answer"] is None and out["passages"]
    asked = client.post(
        "/ask", json={"question": "granular", "backend": "api", "steps": 0}
    )
    assert asked.status_code == 402 and "is spent" in asked.json()["detail"]
