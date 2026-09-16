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
``<data dir>/run/``, and a command file the ``prax up --stop`` and
``--restart`` commands write, which is what works the same on every
platform. Children get no console on Windows and a session of their own
elsewhere, so no terminal window can end them; on Windows they are also
in a job object that ends them if the supervisor itself is killed.

The timed passes are not here: ``maintain`` and ``backup`` run on the
door's own clock (``schedule:``, ``prax.schedule``) and the nightly
backlog pass is the worker's (``nightly:`` above). What is left for an
operating system is one login entry, which ``prax.autostart`` writes.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from prax import config, models

# the start order; the stop order is the reverse. A reranker is a second
# llama-server with a cross-encoder (serve: {reranker: true} on its model)
ROLES = ("llama-server", "reranker", "door", "worker")
KEEP_LOGS = 10
BACKOFF = (1, 2, 4, 8, 16, 32, 60)  # seconds before a restart, per failure in a row
STABLE_SECONDS = 300  # a process that lived this long starts the backoff over
STOP_GRACE = 10.0  # seconds a process gets to end on its own
DOOR_PATIENCE = 90.0  # how long the worker waits for the door
SERVER_PATIENCE = 900.0  # a 20 GB model takes a while to load
TICK = 1.0
PIDFILE = "up.pid"  # the files under <data dir>/run/
STATUS = "up.json"
COMMAND = "up.cmd"
SERVE_KEYS = (
    "path",  # the GGUF file; else repo and file of the models: entry
    "slots",
    "mmproj",  # the projector beside the model, for images
    "projector_on_cpu",
    "image_max_tokens",
    "cpu_moe",  # expert weights of the first N layers stay in RAM
    "ubatch",
    "threads",
    "thinking",  # off by default: extraction under a grammar must not think
    "metrics",
    "reranker",  # a cross-encoder server instead of a chat one
    "extra",  # more llama-server arguments, as a list
)
ROLE_KEYS = {
    "llama-server": ("model",),
    "reranker": ("model",),
    "door": ("host", "port", "ssl_certfile", "ssl_keyfile"),
    "worker": (
        "door",
        "interval",
        "steps",
        "scope",
        "limit",
        "workers",
        "nightly",
        "nightly_limit",
    ),
}


def _s(items: list[Any]) -> str:
    return "s" if len(items) > 1 else ""


class UpError(ValueError):
    """``run:`` asks for something this host cannot do."""


@dataclass
class Role:
    name: str
    argv: list[str]
    health: str | None = None  # answers 200 once the process is ready
    after: str | None = None  # the role that must be up first
    patience: float = DOOR_PATIENCE  # how long to wait for it
    env: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------- the model


def _python() -> str:
    """The interpreter running prax; ``pythonw`` on Windows is the same
    interpreter without a console, and a child wants the one with."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        exe = exe.with_name("python.exe")
    return str(exe)


def prax_command(*args: str) -> list[str]:
    """``prax <args>`` as the interpreter running the module: one process
    to signal, where the ``prax.exe`` launcher on Windows would be two."""
    return [_python(), "-m", "prax_cli.main", *args]


def llama_binary() -> str:
    """Where llama-server is: ``paths.llama_server`` (``PRAX_LLAMA_SERVER``),
    else where ``docs/howto.md`` 3h puts it, else the PATH."""
    chosen = config.setting("paths.llama_server", "PRAX_LLAMA_SERVER")
    if chosen:
        return str(chosen)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        return str(Path(local) / "prax" / "llama.cpp" / "llama-server.exe")
    found = shutil.which("llama-server")
    if found:
        return found
    share = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return str(share / "prax" / "llama.cpp" / "llama-server")


def model_path(spec: models.ModelSpec) -> Path:
    """The GGUF a server loads: ``serve.path``, else where ``prax models
    fetch`` put ``repo``/``file``."""
    serve = dict(spec.serve)
    if serve.get("path"):
        return Path(str(serve["path"])).expanduser()
    if spec.repo and spec.file:
        from prax import fetch

        return fetch.models_dir() / spec.repo / spec.file
    raise UpError(
        f"model {spec.name!r}: nothing to serve — give serve.path, or repo and"
        f" file (then: prax models fetch {spec.name})"
    )


def llama_argv(spec: models.ModelSpec, *, binary: str | None = None) -> list[str]:
    """llama-server's command line for an ``openai`` model served here:
    the port from its ``base_url``, the context per slot from ``n_ctx``,
    the rest from ``serve:``. What the numbers cost on a card that also
    drives a display: ``docs/howto.md`` 3h."""
    if spec.kind != "openai" or not spec.base_url:
        raise UpError(
            f"model {spec.name!r}: only an openai model with a base_url is served"
        )
    serve = dict(spec.serve)
    unknown = sorted(k for k in serve if k not in SERVE_KEYS)
    if unknown:
        raise UpError(
            f"model {spec.name!r}: unknown serve setting{_s(unknown)}"
            f" {', '.join(unknown)}; known: {', '.join(SERVE_KEYS)}"
        )
    path = model_path(spec)
    if not path.is_file():
        raise UpError(
            f"model {spec.name!r}: no file at {path} (prax models fetch {spec.name},"
            " or serve.path)"
        )
    exe = binary or llama_binary()
    if not Path(exe).is_file():
        raise UpError(
            f"llama-server not found at {exe} (paths.llama_server; docs/howto.md 3h)"
        )
    url = urlparse(spec.base_url)
    host, port = url.hostname or "127.0.0.1", url.port or 8080
    slots = int(serve.get("slots", 2))
    argv = [exe]
    if not serve.get("thinking", False):
        argv += ["--reasoning", "off", "--reasoning-budget", "0"]
    if serve.get("mmproj"):
        mmproj = Path(str(serve["mmproj"])).expanduser()
        if not mmproj.is_absolute():  # a name is looked for beside the model
            mmproj = path.parent / mmproj
        argv += ["--mmproj", str(mmproj)]
        if serve.get("projector_on_cpu"):
            argv.append("--no-mmproj-offload")
        if serve.get("image_max_tokens"):
            argv += ["--image-max-tokens", str(int(serve["image_max_tokens"]))]
    if serve.get("cpu_moe"):
        argv += ["--n-cpu-moe", str(int(serve["cpu_moe"]))]
    if serve.get("metrics", True):
        argv.append("--metrics")
    argv += [str(x) for x in (serve.get("extra") or [])]
    threads = str(int(serve.get("threads", 8)))
    common = ["--model", str(path), "--alias", spec.model or spec.name, "--host", host]
    common += ["--port", str(port), "--n-gpu-layers", "999"]
    if serve.get("reranker"):
        # a cross-encoder: the query and a candidate in one sequence, read in
        # one batch, one score out; so the batch is the context, and 4096
        # tokens covers any chunk prax sends
        return argv + common + [
            "--reranking",
            "--ctx-size", "4096", "--parallel", "1",
            "--batch-size", "4096", "--ubatch-size", "4096",
            "--threads", threads, "--no-webui",
        ]  # fmt: skip
    return argv + common + [
        "--load-mode", "mmap",
        "--ctx-size", str(slots * spec.n_ctx), "--parallel", str(slots),
        "--flash-attn", "on", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        "--batch-size", "2048", "--ubatch-size", str(int(serve.get("ubatch", 512))),
        "--threads", threads, "--no-webui",
    ]  # fmt: skip


def _health_of(base_url: str) -> str:
    url = urlparse(base_url)
    host, port = url.hostname or "127.0.0.1", url.port or 8080
    return f"{url.scheme or 'http'}://{host}:{port}/health"


def roles(section: dict[str, Any] | None = None) -> list[Role]:
    """The roles ``run:`` names, in start order, each with its command."""
    raw = config.setting("run") if section is None else section
    if not raw:
        raise UpError(
            f"no run: section in {config.config_path()} — which of the door, the"
            " worker and llama-server this host runs (prax.example.yaml shows it)"
        )
    if not isinstance(raw, dict):
        raise UpError("run: must be a mapping of roles")
    unknown = sorted(k for k in raw if k not in ROLES)
    if unknown:
        raise UpError(
            f"run: unknown role{_s(unknown)} {', '.join(unknown)};"
            f" roles are {', '.join(ROLES)}"
        )
    for name, opts in raw.items():
        opts = opts or {}
        if not isinstance(opts, dict):
            raise UpError(f"run.{name}: must be a mapping (or empty)")
        bad = sorted(k for k in opts if k not in ROLE_KEYS[name])
        if bad:
            raise UpError(
                f"run.{name}: unknown setting{_s(bad)} {', '.join(bad)};"
                f" known: {', '.join(ROLE_KEYS[name])}"
            )
    out: list[Role] = []
    door_url = None
    if "door" in raw:
        port = int((raw["door"] or {}).get("port", 8000))
        door_url = f"http://127.0.0.1:{port}"
    for name in ROLES:
        if name not in raw:
            continue
        opts = raw[name] or {}
        if name in ("llama-server", "reranker"):
            model = opts.get("model")
            if not model:
                raise UpError(
                    f"run.{name}: which model? (a models: entry with a serve: block)"
                )
            spec = models.spec(str(model))
            if spec is None:
                raise UpError(f"run.{name}: no model named {model!r} in prax.yaml")
            if name == "reranker" and not dict(spec.serve).get("reranker"):
                raise UpError(
                    f"run.reranker: {model!r} is not a reranker (serve: reranker: true)"
                )
            out.append(
                Role(
                    name,
                    llama_argv(spec),
                    health=_health_of(spec.base_url or ""),
                    patience=SERVER_PATIENCE,
                )
            )
        elif name == "door":
            argv = prax_command(
                "serve",
                "--host",
                str(opts.get("host", "127.0.0.1")),
                "--port",
                str(int(opts.get("port", 8000))),
            )
            if opts.get("ssl_certfile") or opts.get("ssl_keyfile"):
                argv += [
                    "--ssl-certfile",
                    str(opts.get("ssl_certfile", "")),
                    "--ssl-keyfile",
                    str(opts.get("ssl_keyfile", "")),
                ]
            out.append(Role(name, argv, health=f"{door_url}/health"))
        elif name == "worker":
            target = str(opts.get("door") or door_url or "http://127.0.0.1:8000")
            argv = prax_command(
                "work",
                "--watch",
                "--door",
                target,
                "--interval",
                str(float(opts.get("interval", 20))),
            )
            if opts.get("steps"):
                steps = opts["steps"]
                argv += [
                    "--steps",
                    ",".join(steps) if isinstance(steps, list) else str(steps),
                ]
            if opts.get("scope"):
                argv += ["--scope", str(opts["scope"])]
            if opts.get("limit"):
                argv += ["-n", str(int(opts["limit"]))]
            if opts.get("workers"):
                argv += ["--workers", str(int(opts["workers"]))]
            if opts.get("nightly"):
                argv += ["--nightly", str(opts["nightly"])]
                if opts.get("nightly_limit"):
                    argv += ["--nightly-limit", str(int(opts["nightly_limit"]))]
            after = "door" if ("door" in raw and not opts.get("door")) else None
            out.append(Role(name, argv, after=after, env={"PRAX_DOOR": target}))
    return out


def environment(data_dir: Path) -> dict[str, str]:
    """What every child sees: this environment, the data directory, an
    unbuffered stdout (the logs stream), the token from ``door.token`` when
    the environment has none, and ``up.env`` (``KEY=value`` lines) for what
    a login entry cannot carry — the Anthropic key on a host without a
    user environment."""
    env = dict(os.environ)
    env["PRAX_DATA_DIR"] = str(data_dir)
    env["PYTHONUNBUFFERED"] = "1"
    extra = data_dir / "up.env"
    if extra.is_file():
        for line in extra.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip('"'))
    token = data_dir / "door.token"
    if not env.get("PRAX_TOKEN") and token.is_file():
        env["PRAX_TOKEN"] = token.read_text(encoding="utf-8").strip()
    return env


# ------------------------------------------------------------- the files


def run_dir(data_dir: Path) -> Path:
    return data_dir / "run"


def logs_dir(data_dir: Path) -> Path:
    return data_dir / "logs"


def rotate(logs: Path, name: str) -> Path:
    """``<name>.log``, the last one moved aside with its time, ten kept."""
    logs.mkdir(parents=True, exist_ok=True)
    current = logs / f"{name}.log"
    if current.exists() and current.stat().st_size > 0:
        stamp = datetime.fromtimestamp(current.stat().st_mtime, tz=UTC).strftime(
            "%Y%m%d-%H%M%S"
        )
        target = logs / f"{name}.{stamp}.log"
        n = 1
        while target.exists():  # two starts within a second
            n += 1
            target = logs / f"{name}.{stamp}-{n}.log"
        with contextlib.suppress(OSError):
            current.replace(target)
    old = sorted(
        logs.glob(f"{name}.20*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for stale in old[KEEP_LOGS:]:
        with contextlib.suppress(OSError):
            stale.unlink()
    return current


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        still = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(still) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_pid(data_dir: Path) -> int | None:
    """The supervisor's pid when one is alive; a stale file is removed."""
    path = run_dir(data_dir) / PIDFILE
    try:
        pid = int(path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if _alive(pid):
        return pid
    with contextlib.suppress(OSError):
        path.unlink()
    return None


def status(data_dir: Path) -> dict[str, Any] | None:
    """The supervisor's last status, or None when none is running."""
    if running_pid(data_dir) is None:
        return None
    try:
        return json.loads((run_dir(data_dir) / STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def command(data_dir: Path, what: dict[str, Any]) -> None:
    path = run_dir(data_dir) / COMMAND
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(what), encoding="utf-8")
    tmp.replace(path)


def stop(data_dir: Path, *, wait: float = 45.0) -> bool:
    """Ask the running supervisor to stop; True once it is gone."""
    pid = running_pid(data_dir)
    if pid is None:
        return True
    command(data_dir, {"cmd": "stop"})
    deadline = time.monotonic() + wait
    pidfile = run_dir(data_dir) / PIDFILE
    while time.monotonic() < deadline:
        # the supervisor removes its pid file as it leaves; a killed one
        # cannot, so a dead pid counts too
        if not pidfile.exists() or not _alive(pid):
            with contextlib.suppress(OSError):
                pidfile.unlink()
            return True
        time.sleep(0.2)
    return False


def restart(data_dir: Path, name: str) -> bool:
    if running_pid(data_dir) is None:
        return False
    command(data_dir, {"cmd": "restart", "name": name})
    return True


# ------------------------------------------------------ the process tree


def _spawn_kwargs(*, detached: bool = False) -> dict[str, Any]:
    """No console on Windows, a session of its own elsewhere: nothing a
    terminal does reaches the process."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        if detached:
            flags |= subprocess.CREATE_BREAKAWAY_FROM_JOB
        return {"creationflags": flags}
    return {"start_new_session": True}


class _JobObject:
    """A Windows job object with kill-on-close: every child assigned to it
    ends when the supervisor's handle goes, which is when the supervisor
    goes, however it went. A no-op elsewhere."""

    def __init__(self) -> None:
        self.handle: Any = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_ulonglong)
                for n in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = (
            0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if k32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            self.handle = handle
            self._k32 = k32
        else:
            k32.CloseHandle(handle)

    def assign(self, proc: subprocess.Popen[bytes]) -> bool:
        if self.handle is None:
            return False
        return bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))  # type: ignore[attr-defined]


def healthy(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
        self.started = _now()
        self.job = _JobObject()
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
                cwd=str(self.cwd),
                env=env,
                **_spawn_kwargs(),
            )
        except OSError as exc:
            self._say(f"{role.name}: cannot start {role.argv[0]}: {exc}")
            return None
        finally:
            log.close()
        if not self.job.assign(proc) and sys.platform == "win32":
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
                        self._set(role.name, state="up")
                        self._say(f"{role.name}: up")
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
        path = self.run_dir / COMMAND
        if not path.exists():
            return
        try:
            what = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            what = {}
        with contextlib.suppress(OSError):
            path.unlink()
        cmd = what.get("cmd")
        if cmd == "stop":
            self._say("asked to stop")
            self.stopping.set()
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
                proc = self.procs.get(name)
                if proc is not None and proc.poll() is None:
                    self._end(name, proc)

    def _end(self, name: str, proc: subprocess.Popen[bytes]) -> None:
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
        with contextlib.suppress(OSError):
            (self.run_dir / COMMAND).unlink()

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
                ticks += 1
                if ticks % 10 == 0:  # a heartbeat; every change writes it anyway
                    self._write_status()
                self.stopping.wait(self.tick)
        except KeyboardInterrupt:
            self.stopping.set()
        finally:
            self._stop_all()
            self._write_status()
            for name in (PIDFILE, COMMAND):
                with contextlib.suppress(OSError):
                    (self.run_dir / name).unlink()
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
