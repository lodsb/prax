"""What ``run:`` in prax.yaml asks this host to keep alive, as roles: the
command line of each (llama-server with its model and flags, marker, the
door, the worker), the environment it starts with, and which roles share
the card. Nothing here starts anything; ``prax.host.process`` does, and
``prax.host.up`` watches."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from prax import config, models

# the start order; the stop order is the reverse. A reranker is a second
# llama-server with a cross-encoder (serve: {reranker: true} on its model);
# marker is marker's own server, the PDF-to-LaTeX reading (howto 3h)
ROLES = ("llama-server", "reranker", "marker", "door", "worker")
MARKER_VRAM_MB = 5000  # what marker's models want of the card (howto 3h)
MARKER_PORT = 8765
DOOR_PATIENCE = 90.0  # how long the worker waits for the door
SERVER_PATIENCE = 900.0  # a 20 GB model takes a while to load
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
# every role may say which resource it competes for and what it needs of
# it; the supervisor keeps one holder of a group when they do not fit
GROUP_KEYS = ("group", "needs_vram_mb", "swap")
# a model server may give the card back after a quiet while and come back
# when work asks for it (``idle_minutes``), and have its working set
# trimmed once it has loaded (``trim``, Windows; on by default)
SERVER_KEYS = ("model", "idle_minutes", "trim", *GROUP_KEYS)
ROLE_KEYS = {
    "llama-server": SERVER_KEYS,
    "reranker": SERVER_KEYS,
    "marker": ("venv", "port", "ngl", "on_demand", *GROUP_KEYS),
    "door": ("host", "port", "ssl_certfile", "ssl_keyfile", *GROUP_KEYS),
    "worker": (
        "door",
        "interval",
        "steps",
        "scope",
        "limit",
        "workers",
        "nightly",
        "nightly_limit",
        "spend",
        *GROUP_KEYS,
    ),
}
SWAP_WHEN = ("ask", "auto")  # who starts a swap: a person, or the supervisor


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
    on_demand: bool = False  # declared, started only by `prax up --start`
    scratch: bool = False  # runs in the data directory's run/: it writes where it is
    # the resource this role competes for (``group: card``), what it needs
    # of it, and whether the supervisor may take it on its own (``swap``)
    group: str | None = None
    needs_vram_mb: int | None = None
    swap: str = "ask"
    # a model server's /metrics (activity), the minutes of none after which
    # it is stopped until work asks for it (0: never), and whether its
    # working set is trimmed once it has loaded
    metrics: str | None = None
    idle_minutes: float = 0.0
    trim: bool = False


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
        from prax.ml import fetch

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
    if host not in ("127.0.0.1", "localhost", "::1"):
        # a server on a LAN or tailnet address answers anyone who reaches
        # it: the model's api_key_env is what the worker sends (prax.models),
        # so the server is told to ask for it (security audit, item 10)
        key = os.environ.get(spec.api_key_env or "", "") if spec.api_key_env else ""
        if key:
            common += ["--api-key", key]
        else:
            logging.getLogger("prax.up").warning(
                "llama-server for %s binds %s without an api key: set api_key_env"
                " on the model and the variable, or bind 127.0.0.1",
                spec.name,
                host,
            )
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


def marker_role(opts: dict[str, Any]) -> Role:
    """marker's server (``marker_server``) from its own venv (``venv``: it
    is a heavy install and never one of prax's dependencies), reading
    through llama.cpp's server with ``ngl`` layers on the card (99: all;
    0: the CPU, 33 s a page against 2). It wants about 5 GB of the card,
    which beside a 20 GB model does not fit a 24 GB one: ``on_demand``
    declares it without starting it, for a `--stop llama-server`,
    `--start marker` evening."""
    venv = opts.get("venv")
    if not venv:
        raise UpError("run.marker: which venv? (where marker-pdf is installed)")
    root = Path(str(venv)).expanduser()
    exe = root / ("Scripts" if sys.platform == "win32" else "bin")
    exe = exe / ("marker_server.exe" if sys.platform == "win32" else "marker_server")
    if not exe.is_file():
        raise UpError(
            f"run.marker: no marker_server at {exe} (pip install marker-pdf fastapi"
            " uvicorn python-multipart there)"
        )
    port = int(opts.get("port", MARKER_PORT))
    # what marker's own models want of the card when they are on it
    needs = opts.get("needs_vram_mb")
    if needs is None and int(opts.get("ngl", 99)) > 0:
        needs = MARKER_VRAM_MB
    env = {
        "SURYA_INFERENCE_BACKEND": "llamacpp",
        "LLAMA_CPP_BINARY": llama_binary(),
        "LLAMA_CPP_NGL": str(int(opts.get("ngl", 99))),
    }
    return Role(
        "marker",
        [str(exe), "--host", "127.0.0.1", "--port", str(port)],
        health=f"http://127.0.0.1:{port}/",
        patience=SERVER_PATIENCE,
        env=env,
        on_demand=bool(opts.get("on_demand", False)),
        scratch=True,  # its server keeps the last upload as ./uploads/document.pdf
        group=str(opts["group"]) if opts.get("group") else None,
        needs_vram_mb=int(needs) if needs else None,
        swap=_swap_setting(opts),
    )


def _swap_setting(opts: dict[str, Any]) -> str:
    """``swap: ask`` (a person asks for the resource; the default) or
    ``auto`` (the supervisor takes it when work waits for this role)."""
    value = str(opts.get("swap", "ask")).strip().lower()
    if value not in SWAP_WHEN:
        raise UpError(f"swap: must be one of {', '.join(SWAP_WHEN)}, not {value!r}")
    return value


def _grouped(role: Role, opts: dict[str, Any]) -> Role:
    """A role with its group settings read off ``run:``."""
    role.group = str(opts["group"]) if opts.get("group") else None
    if opts.get("needs_vram_mb"):
        role.needs_vram_mb = int(opts["needs_vram_mb"])
    role.swap = _swap_setting(opts)
    return role


def _health_of(base_url: str) -> str:
    url = urlparse(base_url)
    host, port = url.hostname or "127.0.0.1", url.port or 8080
    return f"{url.scheme or 'http'}://{host}:{port}/health"


def _checked_run(raw: Any) -> dict[str, Any]:
    """``run:`` as a mapping of known roles, each a mapping of its known
    settings; anything else is refused with what would have been right."""
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
    return raw


def _served_role(name: str, opts: dict[str, Any]) -> Role:
    """llama-server or the reranker: the model's server, its health and
    metrics, the card it needs, and when it may idle."""
    model = opts.get("model")
    if not model:
        raise UpError(f"run.{name}: which model? (a models: entry with a serve: block)")
    spec = models.spec(str(model))
    if spec is None:
        raise UpError(f"run.{name}: no model named {model!r} in prax.yaml")
    if name == "reranker" and not dict(spec.serve).get("reranker"):
        raise UpError(
            f"run.reranker: {model!r} is not a reranker (serve: reranker: true)"
        )
    served = Role(
        name,
        llama_argv(spec),
        health=_health_of(spec.base_url or ""),
        patience=SERVER_PATIENCE,
    )
    with contextlib.suppress(OSError):  # the model file is what it maps
        size = model_path(spec).stat().st_size
        served.needs_vram_mb = max(1, int(size / (1 << 20)))
    if dict(spec.serve).get("metrics", True) and served.health:
        served.metrics = served.health.rsplit("/health", 1)[0] + "/metrics"
    served.idle_minutes = max(0.0, float(opts.get("idle_minutes") or 0))
    if served.idle_minutes and not served.metrics:
        raise UpError(
            f"run.{name}.idle_minutes: the server's /metrics is how quiet"
            " is told (serve: metrics: false turns it off)"
        )
    served.trim = bool(opts.get("trim", sys.platform == "win32"))
    return _grouped(served, opts)


def _door_role(opts: dict[str, Any], door_url: str | None) -> Role:
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
    return _grouped(Role("door", argv, health=f"{door_url}/health"), opts)


# the worker's settings that are a flag of ``prax work`` each: setting,
# flag, and how its value is written (a list of steps joined by commas)
_WORKER_FLAGS: tuple[tuple[str, str, Any], ...] = (
    ("steps", "--steps", lambda v: ",".join(v) if isinstance(v, list) else str(v)),
    ("scope", "--scope", str),
    ("limit", "-n", lambda v: str(int(v))),
    ("workers", "--workers", lambda v: str(int(v))),
)


def _worker_role(
    opts: dict[str, Any], door_url: str | None, *, door_here: bool
) -> Role:
    target = str(opts.get("door") or door_url or "http://127.0.0.1:8000")
    argv = prax_command(
        "work",
        "--watch",
        "--door",
        target,
        "--interval",
        str(float(opts.get("interval", 20))),
    )
    for key, flag, written in _WORKER_FLAGS:
        if opts.get(key):
            argv += [flag, written(opts[key])]
    if opts.get("nightly"):
        argv += ["--nightly", str(opts["nightly"])]
        if opts.get("nightly_limit"):
            argv += ["--nightly-limit", str(int(opts["nightly_limit"]))]
    if opts.get("spend"):
        argv.append("--spend")  # the paid steps run: money is spent
    after = "door" if (door_here and not opts.get("door")) else None
    return _grouped(Role("worker", argv, after=after, env={"PRAX_DOOR": target}), opts)


def roles(section: dict[str, Any] | None = None) -> list[Role]:
    """The roles ``run:`` names, in start order, each with its command."""
    raw = _checked_run(config.setting("run") if section is None else section)
    door_url = None
    if "door" in raw:
        port = int((raw["door"] or {}).get("port", 8000))
        door_url = f"http://127.0.0.1:{port}"
    out: list[Role] = []
    for name in ROLES:
        if name not in raw:
            continue
        opts = raw[name] or {}
        if name in ("llama-server", "reranker"):
            out.append(_served_role(name, opts))
        elif name == "marker":
            out.append(marker_role(opts))
        elif name == "door":
            out.append(_door_role(opts, door_url))
        elif name == "worker":
            out.append(_worker_role(opts, door_url, door_here="door" in raw))
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
