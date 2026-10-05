"""What the card does next (stage AI, step 4): the door's plan over the
work that waits for a role of ``prax up``, from what it costs.

The input is the door's demand (``work.demand``: the groups by role and
action, ``now``, ``ask_holds``) and the supervisor's status (``up.json``:
each role's state and ``load_s``, the group on loan). The output is each
group with its costs and a decision, and the order the card would serve
them in. Nothing here acts: the door shows the plan (``GET /work/plan``),
and ``prax up`` is the one that swaps.

The costs, in seconds:

- the swap: the load time of the role that would take the card and of
  the one that gets it back (``load_s``, the median of the last five),
  each with the companions that start with it (marker's OCR server);
  where a role has never loaded under this supervisor, its reader's
  manifest's number (``load_guess_s``) or else ``LOAD_GUESS_S``, marked
  as a guess;
- the work: the items over the group's rate, when one was measured;
- the wait: how long the oldest item has waited.

A group is served when its role already holds the card (no swap), when a
person said "do it now", or when its wait has outgrown its swap: a
person's request after ``WAIT_FACTOR["human"]`` times the swap, one the
door or a rule asked for after ``WAIT_FACTOR["other"]`` times, and any
group of ``BATCH_ITEMS`` or more at once, which pays its swap back. The
factor is the hysteresis: a swap costs minutes, so a reading that waited
seconds is not worth one. In the night window (``NIGHT_HOURS`` from the
worker's ``nightly`` hour, ``night_now``) every group goes. Otherwise it
waits for its wait to grow or for the night.

Three things hold a "next" back. A role on the card that is still
serving keeps it: a swap would end marker in the middle of a book. Only
a person's "do it now" takes the card from it. A role a person asked for
goes before the others, so the card does not flip straight back to the
one it came from. And an ask in flight holds the card for the role that
answers it (``ask_role``): the plan then says "after the ask" for every
swap to another role. An ask that waits for its server is a person's
"do it now" for that role (stage AK's split: the 27B answers, the 35B
does the bulk passes, one card between them).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

LOAD_GUESS_S = 120.0  # a role that has never loaded here
WAIT_FACTOR = {"human": 3.0, "other": 20.0}
BATCH_ITEMS = 50
NIGHT_HOURS = 4  # the night window, from the worker's nightly hour on


def night_now(nightly: str | None, now: datetime | None = None) -> bool:
    """Whether the local clock is in the night window that starts at
    ``nightly`` (``"03:00"``, ``run.worker.nightly``); never without one."""
    from prax import config
    from prax.host import schedule

    if not nightly:
        return False
    try:
        at = schedule.parse_hour(str(nightly))
    except config.ConfigError:
        return False
    now = now or datetime.now().astimezone()  # the person's clock
    start = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if start > now:
        start -= timedelta(days=1)
    return now - start < timedelta(hours=NIGHT_HOURS)


def _age_s(at: str | None, now: datetime) -> float | None:
    if not at:
        return None
    try:
        then = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return max(0.0, (now - then).total_seconds())


def _load(roles: dict[str, Any], name: str | None) -> tuple[float, bool]:
    """A role's load time with its companions', which start with it (the
    slower of them, since they load side by side), and whether every one
    was measured."""
    if not name:
        return 0.0, True
    seconds, measured = [], True
    for one in [name, *((roles.get(name) or {}).get("with") or [])]:
        state = roles.get(one) or {}
        got = state.get("load_s")
        if got is None:
            measured = False
            got = state.get("load_guess_s")
        seconds.append(float(got) if got is not None else LOAD_GUESS_S)
    return max(seconds), measured


def _holder(status: dict[str, Any], role: str) -> tuple[str | None, bool]:
    """Who holds the card the role would need, and whether the role holds
    it already (it is up, or the group is on loan to it)."""
    roles = status.get("roles") or {}
    for group in (status.get("groups") or {}).values():
        members = group.get("members") or []
        if role not in members:
            continue
        holder = group.get("holder")
        if holder:
            return str(holder), holder == role
        up = [m for m in members if (roles.get(m) or {}).get("state") == "up"]
        return (up[0] if up else None), role in up
    return None, (roles.get(role) or {}).get("state") == "up"


def plan(
    demand: dict[str, Any],
    status: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    night: bool = False,
) -> dict[str, Any]:
    """Each waiting group with its costs and a decision (``serving``,
    ``next``, ``waits``), and the order the card would take them in."""
    now = now or datetime.now(UTC)
    status = status or {}
    roles = status.get("roles") or {}
    asked_now = dict(demand.get("now") or {})
    ask_holds = bool(demand.get("ask_holds"))
    ask_role = demand.get("ask_role")
    for g in demand.get("groups") or []:
        if g.get("action") == "ask":  # a person waits for the answer
            asked_now.setdefault(str(g["role"]), None)
    out = []
    for g in demand.get("groups") or []:
        role = str(g["role"])
        holder, holds = _holder(status, role)
        take, measured = _load(roles, None if holds else role)
        back, back_measured = _load(roles, None if holds else holder)
        swap_s = 0.0 if holds else take + back
        rate = g.get("rate")
        work_s = round(3600.0 * g["waiting"] / rate) if rate else None
        waited_s = _age_s(g.get("oldest"), now)
        asker = "human" if g.get("asked_by") == "human" else "other"
        due_s = WAIT_FACTOR[asker] * swap_s
        row = {
            **g,
            "holder": holder,
            "swap_s": round(swap_s),
            "swap_guessed": not (measured and back_measured),
            "work_s": work_s,
            "waited_s": None if waited_s is None else round(waited_s),
        }
        if holds:
            row.update(decision="serving", why=f"{role} holds the card")
        elif g.get("action") == "ask":
            row.update(decision="next", why="an ask waits for it")
        elif role in asked_now and asked_now[role] in (None, g["action"]):
            row.update(decision="next", why="a person asked for it now")
        elif g["waiting"] >= BATCH_ITEMS:
            row.update(decision="next", why=f"{g['waiting']} items pay the swap back")
        elif waited_s is not None and waited_s >= due_s:
            row.update(decision="next", why=f"waited past {round(due_s)} s")
        elif night:
            row.update(decision="next", why="the night window")
        else:
            left = None if waited_s is None else round(due_s - waited_s)
            row.update(
                decision="waits",
                why="until the night window"
                + ("" if left is None else f", or {left} s more"),
            )
        out.append(row)
    # a holder still serving keeps the card, and a role a person asked
    # for goes before the rest, unless a person asked for this one
    busy = {str(r["role"]) for r in out if r["decision"] == "serving"}
    for r in out:
        if r["decision"] != "next":
            continue
        first = sorted((busy | set(asked_now)) - {r["role"]})
        if first and r["role"] not in asked_now:
            r.update(decision="waits", why=f"after {', '.join(first)}")
        elif ask_holds and r["role"] != ask_role:
            r["why"] += ", after the ask"
    rank = {"serving": 0, "next": 1, "waits": 2}
    out.sort(key=lambda r: (rank[r["decision"]], -(r["waited_s"] or 0)))
    order: list[str] = []
    for r in out:
        if r["decision"] != "waits" and r["role"] not in order:
            order.append(str(r["role"]))
    return {
        "groups": out,
        "order": order,
        "ask_holds": ask_holds,
        "ask_role": ask_role,
    }


def for_host(demand: dict[str, Any], status: dict[str, Any] | None) -> dict[str, Any]:
    """The plan on this host: its night window from the worker's
    ``nightly`` hour in ``run:`` (``prax.yaml``)."""
    import contextlib

    from prax import models

    nightly = None
    with contextlib.suppress(Exception):  # an unreadable file plans without a night
        nightly = ((models.load().get("run") or {}).get("worker") or {}).get("nightly")
    return plan(demand, status, night=night_now(nightly))
