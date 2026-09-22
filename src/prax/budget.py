"""What the paid steps may spend, and what they have spent.

A host whose models are somebody's API has no card to run out of; it has
a bill. The budget is two numbers in ``prax.yaml``, money per calendar
day and per calendar month in UTC::

    budget:
      daily_usd: 5        # 0 or absent: no limit, which is what a host
      monthly_usd: 50     # with only local models wants

They are read against the ledger (``store.record_spend``, one row per
paid call with the money at the price of that moment). When a limit is
reached the door stops handing paid work out and refuses a paid ask,
until the day or the month turns. Nothing local is affected: a free
model is never counted and never stopped.

The budget is the host's ceiling, not consent for one run: a worker
still needs ``--spend`` to run a paid step at all (``prax.worker``).
Money already spent is what counts, so a pass may pass the limit by its
last call and stop after it, rather than not starting a call it cannot
price in advance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prax import config, models, store


def limits() -> dict[str, float]:
    """``daily_usd`` and ``monthly_usd`` from ``prax.yaml``; 0 is no limit."""
    return {
        "daily_usd": float(config.number("budget.daily_usd", "PRAX_DAILY_USD", 0.0)),
        "monthly_usd": float(
            config.number("budget.monthly_usd", "PRAX_MONTHLY_USD", 0.0)
        ),
    }


def day_start(now: datetime | None = None) -> str:
    at = now or datetime.now(UTC)
    return at.strftime("%Y-%m-%dT00:00:00Z")


def month_start(now: datetime | None = None) -> str:
    at = now or datetime.now(UTC)
    return at.strftime("%Y-%m-01T00:00:00Z")


def state(con: Any) -> dict[str, Any]:
    """What is left of the budget: the two limits, what has been spent
    against each, and whether paid work may run. ``ok`` is true when no
    limit is set at all, which is a host that never spends or one that
    has not said a number."""
    lim = limits()
    day = store.spent_usd(con, day_start())
    month = store.spent_usd(con, month_start())
    over = []
    if lim["daily_usd"] and day >= lim["daily_usd"]:
        over.append(f"today's {lim['daily_usd']:.2f} USD is spent ({day:.2f})")
    if lim["monthly_usd"] and month >= lim["monthly_usd"]:
        over.append(f"this month's {lim['monthly_usd']:.2f} USD is spent ({month:.2f})")
    return {
        "limits": lim,
        "spent": {"day": day, "month": month},
        "left": {
            "day": round(lim["daily_usd"] - day, 4) if lim["daily_usd"] else None,
            "month": round(lim["monthly_usd"] - month, 4)
            if lim["monthly_usd"]
            else None,
        },
        "ok": not over,
        "why": "; ".join(over),
    }


def allows(con: Any, step: str) -> tuple[bool, str]:
    """Whether a paid step may run now: (True, "") when the step costs
    nothing on this host, or when the budget has room; (False, why) when
    a limit is reached. The caller says what to do about it — the work
    hand-out offers nothing, the ask answers without a model."""
    if step not in models.STEPS:
        return True, ""  # parse, embed, resolve: work with no model of its own
    try:
        spec = models.resolve(step)
    except config.ConfigError:
        return True, ""  # a step nobody configured is not a step that spends
    if spec is None or not spec.paid:
        return True, ""
    now = state(con)
    if now["ok"]:
        return True, ""
    return False, f"{step} is {spec.name}, which costs money, and {now['why']}"


def price(spec: models.ModelSpec | None, usage: dict[str, Any] | None) -> float:
    """What a call cost: ``extraction.cost_usd`` over the model's runtime
    name, which reads ``price:`` from ``prax.yaml`` first and the Claude
    table after, and charges a cache read at a tenth and a cache write at
    a quarter more. One rule for the ledger and for what an ask reports."""
    from prax import extraction

    if spec is None or not usage:
        return 0.0
    return round(float(extraction.cost_usd(spec.runtime_name, dict(usage))), 6)


def note(
    con: Any,
    step: str,
    usage: dict[str, Any] | None,
    *,
    doc_id: int | None = None,
    run: str | None = None,
    spec: models.ModelSpec | None = None,
) -> float:
    """Write what a step's call cost to the ledger, if it cost anything.
    Every paid call the door makes or takes in goes through here."""
    if spec is None:
        if step not in models.STEPS:
            return 0.0
        try:
            spec = models.resolve(step)
        except config.ConfigError:
            return 0.0
    if spec is None or not spec.paid:
        return 0.0
    return store.record_spend(
        con,
        step=step,
        model=spec.runtime_name,
        usage=usage,
        usd=price(spec, usage),
        doc_id=doc_id,
        run=run,
    )
