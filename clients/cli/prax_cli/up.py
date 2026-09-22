"""``prax up``: keep this host's processes running (``prax.up``)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from . import out


def _data_dir(a: Any) -> Path:
    """``--data-dir`` sets the store for this command and every child."""
    from prax import config

    if a.data_dir:
        os.environ["PRAX_DATA_DIR"] = str(Path(a.data_dir).resolve())
    return config.data_dir()


def _since(stamp: str | None) -> str:
    return out.when(stamp, "%m-%d %H:%M") if stamp else ""


def _groups_lines(state: dict) -> list[str]:
    """One line per group whose resource is on loan."""
    out_lines = []
    for name, g in (state.get("groups") or {}).items():
        members = ", ".join(g.get("members") or [])
        holder = g.get("holder")
        if not holder:  # nothing borrowed: who shares it, and how to ask
            out_lines.append(f"  {name}: shared by {members} · prax up --swap <role>")
            continue
        others = ", ".join(g.get("was_up") or []) or "nothing"
        how = "beside" if g.get("fits") else "instead of"
        back = g.get("back_when") or "idle"
        out_lines.append(
            f"  {name}: {holder} has it {how} {others}"
            f" (back when {back}; prax up --unswap {name})"
        )
    return out_lines


def show_status(data_dir: Path) -> int:
    from prax import up

    snap = up.status(data_dir)
    if snap is None:
        out.say("prax up is not running")
        out.hint("  prax up -d starts it; prax up --install makes it start at login")
        return 1
    since = _since(snap.get("started"))
    out.say(f"prax up (pid {snap['pid']}) since {since} · {snap['data_dir']}")
    rows = []
    for name, r in snap.get("roles", {}).items():
        state = r.get("state", "?")
        colour = {
            "up": "green",
            "starting": "yellow",
            "down": "red",
            "waiting": "yellow",
            "paused": "yellow",
        }.get(state)
        shown = out.paint(state, colour) if colour else state
        note = ""
        if state == "down":
            note = f"exit {r.get('exit')}"
        elif state == "paused":
            note = f"until prax up --start {name}"
        elif r.get("since"):
            note = f"since {_since(r['since'])}"
        if r.get("restarts"):
            note += f" · {out.plural(r['restarts'], 'restart')}"
        rows.append([name, shown, str(r.get("pid") or ""), note.strip(" ·")])
    out.table(rows, headers=["role", "state", "pid", ""])
    for line in _groups_lines(snap):
        out.hint(line)
    waiting = {k: v for k, v in (snap.get("waiting") or {}).items() if v}
    if waiting:
        out.hint(
            "  waiting: "
            + ", ".join(f"{n} for {role}" for role, n in sorted(waiting.items()))
        )
    out.hint(f"  logs: {up.logs_dir(data_dir)}")
    return 0


def up(a: Any) -> int:
    from prax import config, up

    data_dir = _data_dir(a)
    try:
        if a.status:
            return show_status(data_dir)
        if a.stop:
            if up.running_pid(data_dir) is None:
                out.say("prax up is not running")
                return 0
            if a.stop != "all":
                out.say(f"stopping {a.stop}…")
                if up.stop(data_dir, name=a.stop):
                    out.say(
                        f"{a.stop} stopped; prax up --start {a.stop} brings it back"
                    )
                    return 0
                out.fail(f"{a.stop} did not stop", "is that a role of run:?")
                return 1
            out.say("stopping…")
            if up.stop(data_dir):
                out.say("stopped")
                return 0
            out.fail(
                "prax up did not stop in time",
                f"see {up.logs_dir(data_dir) / 'up.log'}",
            )
            return 1
        if a.start:
            if up.running_pid(data_dir) is None:
                out.fail("prax up is not running", "prax up -d starts everything")
                return 1
            if up.start(data_dir, a.start):
                out.say(f"{a.start} started")
                return 0
            out.fail(
                f"{a.start} did not start", "prax up --status; is it a role of run:?"
            )
            return 1
        if getattr(a, "swap", None):
            if not up.swap(data_dir, a.swap):
                out.fail("prax up is not running", "prax up -d starts it")
                return 1
            out.say(f"asked prax up for the card for {a.swap}")
            out.hint(
                "  prax up --status shows who holds it; it goes back when"
                " nothing waits. A worker that just met a server that was"
                " down may hold its request for up to ten minutes"
            )
            return 0
        if getattr(a, "unswap", None):
            if not up.unswap(data_dir, a.unswap):
                out.fail("prax up is not running", "prax up -d starts it")
                return 1
            out.say("asked prax up to give it back")
            return 0
        if a.restart:
            if not up.restart(data_dir, a.restart):
                out.fail("prax up is not running", "prax up -d starts it")
                return 1
            out.say(f"asked prax up to restart {a.restart}")
            return 0
        if a.install or a.uninstall:
            from prax import autostart

            if a.uninstall:
                lines = autostart.uninstall()
            else:
                up.roles()  # a bad run: section is found here, not at login
                lines = autostart.install(data_dir)
            for line in lines:
                out.say(line)
            return 0
        roles = up.roles()
        pid = up.running_pid(data_dir)
        if pid is not None:
            out.fail(
                f"prax up is already running (pid {pid})", "prax up --status shows it"
            )
            return 1
        if a.detach:
            pid = up.detach(data_dir, ["--tray"] if getattr(a, "tray", False) else [])
            out.say(f"prax up started (pid {pid}): {', '.join(r.name for r in roles)}")
            out.hint(
                f"  prax up --status · prax up --stop · logs in {up.logs_dir(data_dir)}"
            )
            return 0
        if getattr(a, "tray", False):
            from prax import tray as tray_mod

            return tray_mod.run(data_dir, supervise=True)
        up.attach_log(data_dir)
        say = out.say if sys.stdout is not None else None
        if say:
            out.hint(f"store {data_dir} · ctrl-c stops everything in order")
        return up.Supervisor(roles, data_dir=data_dir, say=say).run()
    except (up.UpError, config.ConfigError) as exc:
        out.fail(str(exc))
        return 2


def tray(a: Any) -> int:
    """``prax tray``: an icon beside the supervisor running here."""
    from prax import config, up
    from prax import tray as tray_mod

    data_dir = _data_dir(a)
    try:
        if up.running_pid(data_dir) is None:
            out.hint("prax up is not running; the tray's menu can start it")
        return tray_mod.run(data_dir, supervise=False)
    except (up.UpError, config.ConfigError) as exc:
        out.fail(str(exc))
        return 2
