"""``prax up``: the processes prax keeps alive on this host.

prax owns its process model; the operating system's only job is to start
one process at login and start it again if it vanishes. ``run:`` in
``prax.yaml`` says which of prax's roles this host runs and with what::

    run:
      llama-server: {model: server-35b}    # the models: entry, with its serve: block
      reranker:     {model: ranker}        # optional: a second server, a cross-encoder
      door:         {host: 0.0.0.0, port: 8000}
      worker:       {interval: 20, nightly: "03:00"}

The supervisor starts them in that order with real health gates (the
worker once the door answers, with patience for a model server loading
for a minute), restarts what dies with a capped backoff, stops them in
reverse order, and writes each one's output to ``<data dir>/logs/<name>.log``
(rotated on every start, ten kept) and its own lines to ``up.log``. It
has no port and no state: a pid file and a status file under
``<data dir>/run/``, and a command queue (one file per command,
``run/commands/``) the ``prax up --stop`` and ``--restart`` commands
write, which is what works the same on every platform. Children get no
console on Windows and a session of their own
elsewhere, so no terminal window can end them; on Windows they are also
in a job object that ends them if the supervisor itself is killed.

The timed passes are not here: ``maintain`` and ``backup`` run on the
door's own clock (``schedule:``, ``prax.host.schedule``) and the nightly
backlog pass is the worker's (``nightly:`` above). What is left for an
operating system is one login entry, which ``prax.host.autostart`` writes.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prax import config
from prax.host import hostinfo

# the roles and the processes, from where they are defined: a caller may
# still write ``up.<name>`` for any of them
from .process import (  # noqa: F401
    COMMANDS,
    KEEP_LOGS,
    LOADS,
    PIDFILE,
    STATUS,
    _alive,
    _await_state,
    _clear_commands,
    _commands_sent,
    _JobObject,
    _now,
    _spawn_kwargs,
    command,
    healthy,
    logs_dir,
    restart,
    rotate,
    run_dir,
    running_pid,
    start,
    status,
    stop,
    swap,
    unswap,
)
from .roles import (  # noqa: F401
    DOOR_PATIENCE,
    GROUP_KEYS,
    MARKER_PORT,
    MARKER_VRAM_MB,
    ROLE_KEYS,
    ROLES,
    SERVE_KEYS,
    SERVER_KEYS,
    SERVER_PATIENCE,
    SWAP_WHEN,
    Role,
    UpError,
    _grouped,
    _health_of,
    _python,
    _s,
    _swap_setting,
    environment,
    llama_argv,
    llama_binary,
    marker_role,
    model_path,
    prax_command,
    roles,
)

BACKOFF = (1, 2, 4, 8, 16, 32, 60)  # seconds before a restart, per failure in a row
STABLE_SECONDS = 300  # a process that lived this long starts the backoff over
STOP_GRACE = 10.0  # seconds a process gets to end on its own
TICK = 1.0
BACK_WHEN = ("idle", "never")  # when the group goes back to what held it
GROUP_QUIET = 90.0  # seconds of no work for the borrower before it goes back
DEMAND_SECONDS = 20.0  # how often the supervisor asks the door what waits
LOADS_KEPT = 5  # load times kept per role; the status says their median
IDLE_POLL = 30.0  # how often an idle-watched server's /metrics is read


class Supervisor:
    """Keeps the roles alive until told to stop. ``run()`` blocks in the
    calling thread (the one that gets the signals); ``say`` gets each
    event as a line, the log file gets them all."""

    def __init__(
        self,
        roles_: list[Role],
        *,
        data_dir: Path,
        say: Callable[[str], None] | None = None,
        tick: float = TICK,
        backoff: tuple[int, ...] = BACKOFF,
        stable: float = STABLE_SECONDS,
        grace: float = STOP_GRACE,
        cwd: Path | None = None,
    ) -> None:
        self.roles = roles_
        self.data_dir = data_dir
        self.logs = logs_dir(data_dir)
        self.run_dir = run_dir(data_dir)
        self.say = say
        self.tick = tick
        self.backoff = backoff
        self.stable = stable
        self.grace = grace
        self.cwd = cwd or config.REPO_ROOT
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self.state: dict[str, dict[str, Any]] = {
            r.name: {
                "state": "waiting",
                "pid": None,
                "since": None,
                "restarts": 0,
                "exit": None,
                "next": None,
            }
            for r in roles_
        }
        self.restart_now: set[str] = set()
        self.paused: set[str] = set()  # roles told to stop until told to start
        # a group's resource on loan: who has it, who had it, when it goes
        # back (``_groups``). Empty until a swap is asked for
        self.groups: dict[str, dict[str, Any]] = {}
        self.demand: dict[str, Any] = {}  # what the door says waits, per role
        self.now: dict[str, Any] = {}  # the roles a person said do it now for
        self.ask_holds = False  # an ask keeps the card where it is
        self.plan: list[dict[str, Any]] = []  # the door's plan: what goes next
        self._demand_at = 0.0
        # servers stopped for being quiet (``idle_minutes``), and per
        # watched server the last activity count, when it last moved and
        # when /metrics was last read
        self.idled: set[str] = set()
        self._activity: dict[str, dict[str, Any]] = {}
        # how long each role took from start to ready, the last few: what a
        # swap costs (stage AI), kept across runs of the supervisor
        self.loads: dict[str, list[float]] = self._read_loads()
        for name, fields in self.state.items():
            fields["load_s"] = self.load_seconds(name)
        self.started = _now()
        self.jobs: dict[str, _JobObject] = {}  # Windows: one per role, its tree
        self.paused.update(r.name for r in roles_ if r.on_demand)
        self.log = logging.getLogger("prax.up")
        self._threads: list[threading.Thread] = []

    # -- words

    def _say(self, text: str) -> None:
        self.log.info(text)
        if self.say:
            self.say(f"{time.strftime('%H:%M:%S')} {text}")

    def _set(self, name: str, **fields: Any) -> None:
        with self.lock:
            self.state[name].update(fields)
        self._write_status()

    def _write_status(self) -> None:
        with self.lock:
            snapshot = {
                "pid": os.getpid(),
                "started": self.started,
                "updated": _now(),
                "data_dir": str(self.data_dir),
                "roles": {r.name: dict(self.state[r.name]) for r in self.roles},
                "groups": self._group_status(),
                "waiting": dict(self.demand),
            }
        path = self.run_dir / STATUS
        tmp = path.with_suffix(".tmp")
        with contextlib.suppress(OSError):
            tmp.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
            tmp.replace(path)

    # -- one role

    def _gate(self, role: Role) -> None:
        """Wait for the role this one comes after, up to its patience."""
        if not role.after:
            return
        deadline = time.monotonic() + role.patience
        said = False
        while not self.stopping.is_set():
            with self.lock:
                up = self.state.get(role.after, {}).get("state") == "up"
            if up:
                return
            if time.monotonic() > deadline:
                self._say(
                    f"{role.name}: starting without waiting longer for {role.after}"
                )
                return
            if not said:
                self._say(f"{role.name}: waiting for {role.after}")
                said = True
            self.stopping.wait(self.tick)

    def _start(self, role: Role) -> subprocess.Popen[bytes] | None:
        log_path = rotate(self.logs, role.name)
        env = environment(self.data_dir)
        env.update(role.env)
        try:
            log = open(log_path, "ab")  # noqa: SIM115 - the child owns it
        except OSError as exc:
            self._say(f"{role.name}: cannot open {log_path}: {exc}")
            return None
        try:
            proc = subprocess.Popen(
                role.argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(self.run_dir if role.scratch else self.cwd),
                env=env,
                **_spawn_kwargs(),
            )
        except OSError as exc:
            self._say(f"{role.name}: cannot start {role.argv[0]}: {exc}")
            return None
        finally:
            log.close()
        job = self.jobs.get(role.name) or _JobObject()
        self.jobs[role.name] = job
        if not job.assign(proc) and sys.platform == "win32":
            self._say(
                f"{role.name}: not in the job object (survives a killed supervisor)"
            )
        with self.lock:
            self.procs[role.name] = proc
        self._set(
            role.name,
            state="starting" if role.health else "up",
            pid=proc.pid,
            since=_now(),
            next=None,
        )  # `exit` stays: the last one, until the next
        self._say(f"{role.name}: started (pid {proc.pid})")
        return proc

    def _keep(self, role: Role) -> None:
        failures = 0
        while not self.stopping.is_set():
            if role.name in self.paused:
                state = "idle" if role.name in self.idled else "paused"
                self._set(role.name, state=state, pid=None, next=None)
                while role.name in self.paused and not self.stopping.wait(self.tick):
                    pass
                failures = 0
                continue
            self._gate(role)
            if self.stopping.is_set():
                break
            proc = self._start(role)
            began = time.monotonic()
            if proc is not None:
                ready = role.health is None
                while proc.poll() is None:
                    if not ready and healthy(role.health or ""):
                        ready = True
                        took = time.monotonic() - began
                        self._set(role.name, state="up")
                        self._say(f"{role.name}: up ({took:.0f} s from start)")
                        self._loaded(role, proc, took)
                    if self.stopping.wait(self.tick):
                        break
                if self.stopping.is_set():
                    break
                code = proc.returncode
                lived = time.monotonic() - began
            else:
                code, lived = None, 0.0
            immediate = role.name in self.restart_now
            self.restart_now.discard(role.name)
            if immediate:
                failures, delay = 0, 0
            else:
                failures = 1 if lived >= self.stable else failures + 1
                delay = self.backoff[min(failures, len(self.backoff)) - 1]
            with self.lock:
                self.state[role.name]["restarts"] += 1
            self._set(
                role.name,
                state="down",
                pid=None,
                exit=code,
                next=_now() if not delay else None,
            )
            what = (
                "asked to restart"
                if immediate
                else f"exited with {code} after {lived:.0f} s"
                if proc
                else "did not start"
            )
            self._say(f"{role.name}: {what}; restart in {delay} s")
            if self.stopping.wait(delay):
                break

    # -- all of them

    def _control(self) -> None:
        """Every command queued since the last tick, in order: the files
        of the commands directory by name (a timestamp, the sender's
        pid, a counter), each read and removed; a file still under its
        temporary name is not there yet."""
        queue = self.run_dir / COMMANDS
        try:
            names = sorted(n for n in os.listdir(queue) if n.endswith(".json"))
        except OSError:
            return
        for name in names:
            path = queue / name
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            with contextlib.suppress(OSError):
                path.unlink()
            try:
                what = json.loads(text)
            except ValueError:
                continue
            self._command(what)

    def _command(self, what: dict[str, Any]) -> None:
        cmd = what.get("cmd")
        name = what.get("name")
        if cmd == "stop" and name not in (None, "", "all"):
            if name not in self.state:
                self._say(f"no role named {name}")
                return
            self._say(f"{name}: asked to stop until started again")
            self.paused.add(str(name))
            proc = self.procs.get(str(name))
            if proc is not None and proc.poll() is None:
                self._end(str(name), proc)
        elif cmd == "stop":
            self._say("asked to stop")
            self.stopping.set()
        elif cmd == "start":
            names = list(self.paused) if name in (None, "", "all") else [str(name)]
            for one in names:
                if one not in self.state:
                    self._say(f"no role named {one}")
                elif one in self.paused:
                    self._say(f"{one}: asked to start")
                    self.idled.discard(one)
                    self.paused.discard(one)
                else:
                    self._say(f"{one}: not stopped")
        elif cmd == "swap":
            self._swap(str(what.get("to") or ""), str(what.get("back_when") or "idle"))
        elif cmd == "unswap":
            groups = (
                list(self.groups)
                if what.get("group") in (None, "", "all")
                else [str(what["group"])]
            )
            for group in groups:
                self._unswap(group, "asked for")
        elif cmd == "restart":
            names = (
                [r.name for r in self.roles]
                if what.get("name") in (None, "", "all")
                else [str(what["name"])]
            )
            for name in names:
                if name not in self.state:
                    self._say(f"no role named {name}")
                    continue
                self.restart_now.add(name)
                self.idled.discard(name)
                self.paused.discard(name)  # a restart of a paused role starts it
                proc = self.procs.get(name)
                if proc is not None and proc.poll() is None:
                    self._end(name, proc)

    # -- a model server that has loaded, and one that has been quiet

    def _read_loads(self) -> dict[str, list[float]]:
        with contextlib.suppress(OSError, ValueError, AttributeError):
            got = json.loads((self.run_dir / LOADS).read_text(encoding="utf-8"))
            return {
                str(name): [float(x) for x in seconds][-LOADS_KEPT:]
                for name, seconds in got.items()
            }
        return {}

    def load_seconds(self, name: str) -> float | None:
        """The role's typical load time, start to ready: the median of the
        last few, or None before the first."""
        seconds = sorted(self.loads.get(name) or [])
        return seconds[len(seconds) // 2] if seconds else None

    def _note_load(self, name: str, seconds: float) -> None:
        kept = [*self.loads.get(name, []), round(seconds, 1)][-LOADS_KEPT:]
        self.loads[name] = kept
        with contextlib.suppress(OSError):
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / LOADS).write_text(json.dumps(self.loads), encoding="utf-8")
        self._set(name, load_s=self.load_seconds(name))

    def _loaded(
        self, role: Role, proc: subprocess.Popen[bytes], took: float | None = None
    ) -> None:
        """A server has answered its first health check: its load time is
        noted (``took``, start to ready), its quiet is counted from now,
        and its working set is trimmed (``trim``)."""
        if took is not None:
            self._note_load(role.name, took)
        self._activity[role.name] = {
            "count": None,
            "moved": time.monotonic(),
            "read": 0.0,
        }
        if not role.trim:
            return
        held = hostinfo.trim_working_set(proc.pid)
        if held is not None:
            self._say(f"{role.name}: working set trimmed ({held} MB before)")

    def _server_activity(self, role: Role) -> float | None:
        """How much the server has done so far (decode calls, and one for
        each request in flight), from its /metrics; None when it does not
        answer."""
        try:
            with urllib.request.urlopen(role.metrics or "", timeout=3) as r:
                text = r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError):
            return None
        done = busy = 0.0
        for line in text.splitlines():
            name, _, value = line.partition(" ")
            with contextlib.suppress(ValueError):
                if name == "llamacpp:n_decode_total":
                    done = float(value)
                elif name == "llamacpp:requests_processing":
                    busy = float(value)
        return done + busy * 1e12 if busy else done  # in flight: always moved

    def _idle(self) -> None:
        """Every tick: a server with ``idle_minutes`` that has done nothing
        for that long is stopped and marked idle; an idle one whose work
        the door says waits (a worker's "not yet", an ask that found it
        gone) is started again. Loading takes minutes, so the quiet time
        is the hysteresis: the server stays until it has been unused for
        as long again."""
        watched = [r for r in self.roles if r.idle_minutes > 0]
        if not watched:
            return
        self._ask_demand()
        now = time.monotonic()
        for role in watched:
            name = role.name
            if name in self.idled:
                if int(self.demand.get(name, 0) or 0) > 0:
                    loan = self._lent_away(role)
                    if loan is not None:
                        # its card is on loan: it waits for it and comes back
                        # with it, rather than loading beside the borrower
                        # (2026-10-02: llama-server reloaded three minutes into
                        # marker's turn, both models on the card and in RAM)
                        if name not in loan["was_up"]:
                            loan["was_up"].append(name)
                            self._say(
                                f"{name}: work waits for it; after {loan['holder']}"
                            )
                        continue
                    self._say(f"{name}: work waits for it; loading again")
                    self.idled.discard(name)
                    self.paused.discard(name)
                continue
            with self.lock:
                up = self.state[name]["state"] == "up"
            seen = self._activity.get(name)
            if not up or name in self.paused or seen is None:
                continue
            if any(name in loan.get("was_up", ()) for loan in self.groups.values()):
                continue
            if now - seen["read"] < IDLE_POLL:
                continue
            seen["read"] = now
            count = self._server_activity(role)
            if count is None:
                continue
            if count != seen["count"]:
                seen["count"], seen["moved"] = count, now
                continue
            quiet = now - seen["moved"]
            if quiet >= role.idle_minutes * 60:
                self._say(
                    f"{name}: nothing for {quiet / 60:.0f} min; stopped until"
                    " work asks for it"
                )
                self.idled.add(name)
                self.paused.add(name)
                proc = self.procs.get(name)
                if proc is not None and proc.poll() is None:
                    self._end(name, proc)

    # -- the groups: roles that compete for one resource (the card)

    def _group_status(self) -> dict[str, Any]:
        """Every group ``run:`` declares, whether or not its resource is on
        loan: the members and what each wants of it, and the loan when
        there is one. A group only listed while borrowed would leave the
        Jobs view with nothing to offer until the first swap."""
        out: dict[str, Any] = {}
        for role in self.roles:
            if not role.group:
                continue
            group = out.setdefault(
                role.group,
                {
                    "members": [],
                    "needs_vram_mb": {},
                    "holder": None,
                    "was_up": [],
                    "back_when": None,
                    "fits": None,
                    "since": None,
                },
            )
            group["members"].append(role.name)
            group["needs_vram_mb"][role.name] = role.needs_vram_mb
        for name, loan in self.groups.items():
            group = out.setdefault(name, {"members": []})
            group.update({k: v for k, v in loan.items() if k != "quiet_since"})
        return out

    def _members(self, group: str) -> list[Role]:
        return [r for r in self.roles if r.group == group]

    def _fits(self, role: Role) -> bool:
        """Whether the role can have what it needs of the card beside what
        is on it now. Without a number for either, no: the swap is what
        the group is for, and a wrong yes wedges two servers on one card."""
        want = role.needs_vram_mb
        free = hostinfo.vram_free_mb()
        return bool(want and free is not None and free >= want)

    def _swap(self, to: str, back_when: str = "idle") -> None:
        """Give the group's resource to ``to``: the members that hold it
        stop (unless it fits beside them), and go back when the door says
        no work is left for the borrower (``idle``) or never."""
        role = next((r for r in self.roles if r.name == to), None)
        if role is None or not role.group:
            self._say(f"{to}: not a role of a group (run.{to}.group)")
            return
        if back_when not in BACK_WHEN:
            self._say(f"swap: back_when must be one of {', '.join(BACK_WHEN)}")
            return
        group = role.group
        members = self._members(group)
        was_up = [r.name for r in members if r.name != to and r.name not in self.paused]
        fits = self._fits(role)
        if not fits:
            for name in was_up:
                self.paused.add(name)
                proc = self.procs.get(name)
                if proc is not None and proc.poll() is None:
                    self._end(name, proc)
        self.groups[group] = {
            "holder": to,
            "was_up": was_up,
            "back_when": back_when,
            "since": _now(),
            "fits": fits,
            "quiet_since": None,
        }
        self.paused.discard(to)
        self.restart_now.discard(to)
        how = "beside" if fits else "instead of"
        others = ", ".join(was_up) or "nothing"
        self._say(f"{group}: {to} takes it {how} {others} (back when {back_when})")
        self._write_status()

    def _lent_away(self, role: Role) -> dict[str, Any] | None:
        """The loan of the role's group to another role, if there is one."""
        if not role.group:
            return None
        loan = self.groups.get(role.group)
        if loan is None or loan.get("holder") == role.name or loan.get("fits"):
            return None
        return loan

    def _unswap(self, group: str, why: str) -> None:
        """The group's resource back to what held it: the borrower stops
        if it was not up before, and the others start again."""
        loan = self.groups.pop(group, None)
        if loan is None:
            return
        holder = str(loan["holder"])
        role = next((r for r in self.roles if r.name == holder), None)
        if role is not None and (role.on_demand or holder not in loan["was_up"]):
            self.paused.add(holder)
            proc = self.procs.get(holder)
            if proc is not None and proc.poll() is None:
                self._end(holder, proc)
        for name in loan["was_up"]:
            self.paused.discard(name)
            self.idled.discard(name)  # one that waited, idle, for the card
        back = ", ".join(loan["was_up"]) or "nothing"
        self._say(f"{group}: {holder} gives it back to {back} ({why})")
        self._write_status()

    def _door_url(self) -> str | None:
        role = next((r for r in self.roles if r.name == "door"), None)
        if role is None or not role.health:
            return None
        return role.health.rsplit("/health", 1)[0]

    def _ask_demand(self) -> None:
        """What the door says waits, per role (``GET /work/demand``): one
        ask every ``DEMAND_SECONDS``. A door that is down or refuses says
        nothing, and a group on loan then falls back to its quiet time."""
        now = time.monotonic()
        if now - self._demand_at < DEMAND_SECONDS:
            return
        self._demand_at = now
        url = self._door_url()
        if not url:
            return
        token = environment(self.data_dir).get("PRAX_TOKEN", "")
        req = urllib.request.Request(f"{url}/work/demand?plan=true")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                said = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            said = {}
        self.demand = said.get("roles") or {}
        self.now = said.get("now") or {}
        self.ask_holds = bool(said.get("ask_holds"))
        self.plan = [
            row
            for row in (said.get("plan") or {}).get("groups") or []
            if row.get("decision") == "next"
        ]

    def _groups(self) -> None:
        """Every tick: the door's plan is followed (``_follow_plan``), and a
        group whose borrower has nothing left to do gives the resource
        back."""
        if not any(r.group for r in self.roles):
            return
        self._ask_demand()
        self._follow_plan()
        for group, loan in list(self.groups.items()):
            if loan["back_when"] != "idle":
                continue
            waiting = int(self.demand.get(str(loan["holder"]), 0) or 0)
            if waiting:
                loan["quiet_since"] = None
                continue
            since = loan["quiet_since"]
            if since is None:
                loan["quiet_since"] = time.monotonic()
            elif time.monotonic() - since >= GROUP_QUIET:
                self._unswap(group, "nothing left waiting")

    def _follow_plan(self) -> None:
        """The door's plan (``GET /work/plan``, in its demand): the first
        group it says goes next gets its role the card, by a swap, or by
        the loan ending when the card was lent away from that role. A role
        with ``swap: auto`` follows the plan by itself; any other only for
        a person's "do it now". Never while an ask holds the card; the
        door keeps a "do it now" standing, so the next look does it."""
        if self.ask_holds:
            return
        for row in self.plan:
            name = str(row.get("role") or "")
            role = next((r for r in self.roles if r.name == name), None)
            if role is None or not role.group:
                continue
            if role.swap != "auto" and name not in self.now:
                continue
            loan = self.groups.get(role.group)
            if loan is None:
                if name in self.paused:
                    self._swap(name, "idle")
                    return
            elif loan["holder"] != name and name in loan["was_up"]:
                self._unswap(role.group, f"the plan: {row.get('why') or 'next'}")
                return

    def _end(self, name: str, proc: subprocess.Popen[bytes]) -> None:
        """End a role's process and whatever it started: the job on
        Windows, the session elsewhere (children were started in one of
        their own), then the process itself for good measure."""
        job = self.jobs.get(name)
        if job is not None and job.terminate():
            pass
        elif sys.platform != "win32":
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(OSError):
            proc.terminate()
        try:
            proc.wait(timeout=self.grace)
        except subprocess.TimeoutExpired:
            self._say(f"{name}: still running after {self.grace:g} s, killed")
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)

    def _stop_all(self) -> None:
        for role in reversed(self.roles):
            proc = self.procs.get(role.name)
            if proc is not None and proc.poll() is None:
                self._say(f"{role.name}: stopping (pid {proc.pid})")
                self._end(role.name, proc)
            self._set(role.name, state="stopped", pid=None)
        for t in self._threads:
            t.join(timeout=5)

    def run(self) -> int:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        (self.run_dir / PIDFILE).write_text(
            f"{os.getpid()} {self.started}\n", encoding="utf-8"
        )
        _clear_commands(self.run_dir)

        def on_signal(signum: int, _frame: Any) -> None:
            self._say(f"signal {signum}")
            self.stopping.set()

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                with contextlib.suppress(ValueError, OSError):  # not the main thread
                    signal.signal(getattr(signal, name), on_signal)
        names = ", ".join(r.name for r in self.roles)
        self._say(f"prax up: {names} (pid {os.getpid()}, store {self.data_dir})")
        self._threads = [
            threading.Thread(
                target=self._keep, args=(r,), name=f"up-{r.name}", daemon=True
            )
            for r in self.roles
        ]
        for t in self._threads:
            t.start()
        try:
            ticks = 0
            while not self.stopping.is_set():
                self._control()
                self._groups()
                self._idle()
                ticks += 1
                if ticks % 10 == 0:  # a heartbeat; every change writes it anyway
                    self._write_status()
                self.stopping.wait(self.tick)
        except KeyboardInterrupt:
            self.stopping.set()
        finally:
            self._stop_all()
            self._write_status()
            with contextlib.suppress(OSError):
                (self.run_dir / PIDFILE).unlink()
            _clear_commands(self.run_dir)
            self._say("prax up: stopped")
        return 0


def attach_log(data_dir: Path) -> None:
    """The supervisor's own lines into ``logs/up.log``."""
    logs = logs_dir(data_dir)
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(logs / "up.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    logger = logging.getLogger("prax.up")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def detach(data_dir: Path, args: list[str]) -> int:
    """Start ``prax up <args>`` as a process of its own — no console on
    Windows, a session of its own elsewhere, its output in ``up.log`` —
    and return its pid."""
    logs = logs_dir(data_dir)
    logs.mkdir(parents=True, exist_ok=True)
    run_dir(data_dir).mkdir(parents=True, exist_ok=True)
    argv = prax_command("up", "--data-dir", str(data_dir), *args)
    env = environment(data_dir)
    with open(logs / "up.log", "ab") as log:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "cwd": str(config.REPO_ROOT),
            "env": env,
        }
        try:
            proc = subprocess.Popen(argv, **kwargs, **_spawn_kwargs(detached=True))
        except OSError:
            if sys.platform != "win32":
                raise
            # a terminal that forbids leaving its job: stay in it, still no console
            proc = subprocess.Popen(argv, **kwargs, **_spawn_kwargs())
    return proc.pid
