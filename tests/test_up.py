"""``prax up``: the process model from prax.yaml, the supervisor, the
detached start. The children here are small Python scripts, so the same
tests run on Windows, Linux and macOS."""

from __future__ import annotations

import json
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from prax_cli import main as cli

from prax import config, models
from prax.host import up

PY = sys.executable


def wait_for(check: Any, *, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("did not happen in time")


# ---------------------------------------------------------------- roles


@pytest.fixture()
def gpu_host(data_dir: Path, tmp_path: Path) -> Path:
    """A prax.yaml with a served model, whose file and binary exist."""
    data_dir.mkdir(parents=True, exist_ok=True)
    gguf = tmp_path / "models" / "q.gguf"
    gguf.parent.mkdir()
    gguf.write_bytes(b"GGUF")
    (tmp_path / "models" / "mmproj.gguf").write_bytes(b"GGUF")
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"MZ")
    (data_dir / config.CONFIG_NAME).write_text(
        textwrap.dedent(
            f"""
            models:
              big:
                kind: openai
                base_url: http://127.0.0.1:8085/v1
                model: q4
                n_ctx: 16384
                serve:
                  path: {gguf.as_posix()}
                  slots: 2
                  mmproj: mmproj.gguf
                  cpu_moe: 2
                  ubatch: 256
                  image_max_tokens: 1024
            paths:
              llama_server: {binary.as_posix()}
            run:
              llama-server: {{model: big}}
              door: {{host: 0.0.0.0, port: 8005}}
              worker: {{interval: 7, nightly: "03:00", nightly_limit: 50}}
            """
        ),
        encoding="utf-8",
    )
    return data_dir


def test_the_roles_come_from_run_in_prax_yaml(gpu_host: Path, tmp_path: Path) -> None:
    roles = up.roles()
    assert [r.name for r in roles] == ["llama-server", "door", "worker"]
    server, door, worker = roles
    assert server.argv[0].endswith("llama-server.exe")
    assert server.health == "http://127.0.0.1:8085/health"
    assert "--ctx-size" in server.argv
    assert server.argv[server.argv.index("--ctx-size") + 1] == "32768"  # 2 × 16 K
    assert server.argv[server.argv.index("--mmproj") + 1].endswith("mmproj.gguf")
    assert "--n-cpu-moe" in server.argv and "--reasoning" in server.argv
    assert door.argv[1:4] == ["-m", "prax_cli.main", "serve"]
    assert "--port" in door.argv and door.argv[-1] == "8005"
    assert door.health == "http://127.0.0.1:8005/health"
    assert worker.after == "door"
    assert worker.env["PRAX_DOOR"] == "http://127.0.0.1:8005"
    assert "--nightly" in worker.argv and "03:00" in worker.argv
    assert worker.argv[worker.argv.index("--nightly-limit") + 1] == "50"


def test_a_worker_for_another_door_waits_for_nothing(data_dir: Path) -> None:
    roles = up.roles({"worker": {"door": "http://board:8000"}})
    assert roles[0].after is None
    assert roles[0].env["PRAX_DOOR"] == "http://board:8000"


def test_the_worker_role_spends_only_when_the_config_says(data_dir: Path) -> None:
    roles = up.roles({"door": {"port": 8000}, "worker": {"interval": 20}})
    worker_ = next(r for r in roles if r.name == "worker")
    assert "--spend" not in worker_.argv
    roles = up.roles({"door": {"port": 8000}, "worker": {"spend": True}})
    worker_ = next(r for r in roles if r.name == "worker")
    assert "--spend" in worker_.argv


def test_what_run_cannot_say(data_dir: Path) -> None:
    with pytest.raises(up.UpError, match="no run: section"):
        up.roles({})
    with pytest.raises(up.UpError, match="unknown role"):
        up.roles({"dooor": {}})
    with pytest.raises(up.UpError, match="unknown setting"):
        up.roles({"door": {"prot": 1}})
    with pytest.raises(up.UpError, match="which model"):
        up.roles({"llama-server": {}})


def test_a_served_model_needs_its_file_and_the_binary(gpu_host: Path) -> None:
    spec = models.spec("big")
    assert spec is not None
    missing = models.ModelSpec(
        name="x", kind="openai", base_url="http://127.0.0.1:1/v1", model="x"
    )
    with pytest.raises(up.UpError, match="nothing to serve"):
        up.llama_argv(missing)
    with pytest.raises(up.UpError, match="unknown serve setting"):
        up.llama_argv(
            models.ModelSpec(
                name="x",
                kind="openai",
                base_url="http://127.0.0.1:1/v1",
                model="x",
                serve=(("path", "a"), ("slot", 1)),
            )
        )
    argv = up.llama_argv(spec)
    assert argv[argv.index("--alias") + 1] == "q4"
    assert argv[argv.index("--port") + 1] == "8085"
    # llama.cpp's 8 GB prompt cache in RAM, bounded (docs/PLAN.md, AJ)
    assert argv[argv.index("--cache-ram") + 1] == str(up.CACHE_RAM_MB)
    capped = models.ModelSpec(
        name="c",
        kind="openai",
        base_url="http://127.0.0.1:8086/v1",
        model="c",
        serve=(("path", str(up.model_path(spec))), ("cache_ram_mb", 0)),
    )
    capped_argv = up.llama_argv(capped)
    assert capped_argv[capped_argv.index("--cache-ram") + 1] == "0"
    ranker = models.ModelSpec(
        name="r",
        kind="openai",
        base_url="http://127.0.0.1:8081/v1",
        model="bge",
        serve=(("path", str(up.model_path(spec))), ("reranker", True)),
    )
    ranked = up.llama_argv(ranker)
    assert "--reranking" in ranked and "--parallel" in ranked
    assert ranked[ranked.index("--cache-ram") + 1] == "0"
    with pytest.raises(up.UpError, match="not a reranker"):
        up.roles({"reranker": {"model": "big"}})


def test_a_server_beyond_loopback_asks_for_its_key(
    gpu_host: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Bound to a LAN address, llama-server gets --api-key from the model's
    api_key_env; without one the supervisor says so. On loopback, neither."""
    spec = models.spec("big")
    assert spec is not None
    lan = models.ModelSpec(
        **{
            **spec.__dict__,
            "base_url": "http://192.168.1.20:8085/v1",
            "api_key_env": "PRAX_TEST_LLAMA_KEY",
        }
    )
    monkeypatch.setenv("PRAX_TEST_LLAMA_KEY", "s3cret")
    argv = up.llama_argv(lan)
    assert argv[argv.index("--api-key") + 1] == "s3cret"
    assert argv[argv.index("--host") + 1] == "192.168.1.20"
    monkeypatch.delenv("PRAX_TEST_LLAMA_KEY")
    with caplog.at_level("WARNING", logger="prax.host.up"):
        argv = up.llama_argv(lan)
    assert "--api-key" not in argv and "without an api key" in caplog.text
    assert "--api-key" not in up.llama_argv(spec)  # loopback: nothing to ask


def test_the_serve_block_is_only_for_a_server(data_dir: Path) -> None:
    with pytest.raises(models.ConfigError, match="only an openai model"):
        models._spec_from(
            "c", {"kind": "claude", "model": "claude-x", "serve": {"slots": 1}}
        )


# ----------------------------------------------------------- supervisor

CHILD = textwrap.dedent(
    """
    import os, sys, time
    # a child that says it ran, then lives as long as it is told (seconds),
    # exiting with the code it is given
    mark, life, code = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
    with open(mark, "a") as f:
        f.write(f"{os.getpid()}\\n")
    print("alive", flush=True)  # something for the log
    time.sleep(life)
    sys.exit(code)
    """
)


def child(
    tmp_path: Path, name: str, life: float, code: int = 0
) -> tuple[Path, list[str]]:
    script = tmp_path / "child.py"
    if not script.exists():
        # written once per test: rewriting it while a child of an earlier
        # role is reading it is a sharing violation on Windows, which the
        # suite saw twice as a PermissionError out of pytest's own cleanup
        script.write_text(CHILD, encoding="utf-8")
    mark = tmp_path / f"{name}.ran"
    return mark, [PY, str(script), str(mark), str(life), str(code)]


def runs_of(mark: Path) -> int:
    return len(mark.read_text().split()) if mark.exists() else 0


def test_a_dying_process_is_restarted_with_backoff_and_all_stop_in_order(
    data_dir: Path, tmp_path: Path
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    mark_a, argv_a = child(tmp_path, "a", 0.2, 3)  # dies at once, again and again
    mark_b, argv_b = child(tmp_path, "b", 60)  # lives
    roles = [up.Role("a", argv_a), up.Role("b", argv_b)]
    said: list[str] = []
    sup = up.Supervisor(
        roles, data_dir=data_dir, say=said.append, tick=0.05, backoff=(0, 0, 0)
    )
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    wait_for(lambda: runs_of(mark_a) >= 3)
    assert runs_of(mark_b) == 1
    status = json.loads((up.run_dir(data_dir) / up.STATUS).read_text())
    assert status["roles"]["b"]["state"] == "up"
    assert status["roles"]["a"]["restarts"] >= 2
    assert status["roles"]["a"]["exit"] == 3
    assert up.running_pid(data_dir) == status["pid"]
    # the logs: one per role, rotated on every start, the supervisor's own
    assert (data_dir / "logs" / "a.log").exists()
    assert list((data_dir / "logs").glob("a.20*.log"))
    # stop through the command file, as `prax up --stop` does
    assert up.stop(data_dir, wait=20)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert up.running_pid(data_dir) is None
    # stopped in reverse order: b, then a if it was alive at that moment
    stops = [line.split()[1].rstrip(":") for line in said if ": stopping" in line]
    assert stops in (["b", "a"], ["b"])
    assert any("a: exited with 3" in line for line in said)


def test_a_role_waits_for_the_one_before_it_to_answer(
    data_dir: Path, tmp_path: Path
) -> None:
    """The worker starts once the door answers /health; here a small server
    stands in for the door and answers 503 until told otherwise."""
    data_dir.mkdir(parents=True, exist_ok=True)
    ready = threading.Event()

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200 if ready.is_set() else 503)
            self.end_headers()

        def log_message(self, *_: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    mark_d, argv_d = child(tmp_path, "door", 60)
    mark_w, argv_w = child(tmp_path, "worker", 60)
    roles = [
        up.Role("door", argv_d, health=f"http://127.0.0.1:{port}/health"),
        up.Role("worker", argv_w, after="door", patience=30),
    ]
    sup = up.Supervisor(roles, data_dir=data_dir, tick=0.05)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    wait_for(lambda: runs_of(mark_d) == 1)
    time.sleep(0.5)
    assert runs_of(mark_w) == 0  # still waiting
    assert sup.state["door"]["state"] == "starting"
    ready.set()
    wait_for(lambda: runs_of(mark_w) == 1)
    assert sup.state["door"]["state"] == "up"
    # a restart on request comes at once, without backoff
    first = sup.procs["worker"].pid
    up.restart(data_dir, "worker")
    wait_for(lambda: runs_of(mark_w) == 2)
    assert sup.procs["worker"].pid != first
    up.stop(data_dir, wait=20)
    thread.join(timeout=10)
    server.shutdown()


def test_the_command_says_what_is_running(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["up", "--status"]) == 1
    assert "not running" in capsys.readouterr().out
    assert cli.main(["up", "--stop"]) == 0  # nothing to stop is fine
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text("run: {door: {port: 8006}}\n")
    mark, argv = child(tmp_path, "x", 60)
    sup = up.Supervisor([up.Role("door", argv)], data_dir=data_dir, tick=0.05)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    wait_for(lambda: up.running_pid(data_dir) is not None)
    wait_for(lambda: runs_of(mark) == 1)
    assert cli.main(["up", "--status"]) == 0
    printed = capsys.readouterr().out
    assert "door" in printed and "up" in printed
    assert cli.main(["up"]) == 1  # a second one refuses
    assert "already running" in capsys.readouterr().err
    assert cli.main(["up", "--stop"]) == 0
    thread.join(timeout=10)
    assert "stopped" in capsys.readouterr().out


def test_bad_configuration_is_said_before_anything_starts(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text("run: {llama-server: {model: nope}}\n")
    assert cli.main(["up"]) == 2
    assert "no model named 'nope'" in capsys.readouterr().err


def test_detached_start_survives_and_stops(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real ``prax up -d``: a process of its own, no console, found again
    by the pid file, stopped by the command."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        "run: {door: {host: 127.0.0.1, port: 8007}}\n"
    )
    assert cli.main(["up", "-d", "--data-dir", str(data_dir)]) == 0
    printed = capsys.readouterr().out
    assert "prax up started" in printed
    try:
        wait_for(lambda: up.status(data_dir) is not None, seconds=30)
        wait_for(
            lambda: (
                (up.status(data_dir) or {}).get("roles", {}).get("door", {}).get("pid")
            ),
            seconds=30,
        )
        snap = up.status(data_dir)
        assert snap and snap["roles"]["door"]["state"] in ("starting", "up")
        assert (data_dir / "logs" / "up.log").exists()
    finally:
        assert cli.main(["up", "--stop", "--data-dir", str(data_dir)]) == 0
    assert up.running_pid(data_dir) is None
    log = (data_dir / "logs" / "up.log").read_text(encoding="utf-8")
    assert "door: started" in log and "prax up: stopped" in log


# ----------------------------------------------------------- environment


def test_children_get_the_token_file_and_up_env(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("PRAX_TOKEN", raising=False)
    (data_dir / "door.token").write_text("s3cret\n")
    (data_dir / "up.env").write_text(
        '# the key\nANTHROPIC_API_KEY=sk-test\nQUOTED="a b"\n'
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = up.environment(data_dir)
    assert env["PRAX_TOKEN"] == "s3cret"
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert env["QUOTED"] == "a b"
    assert env["PRAX_DATA_DIR"] == str(data_dir)
    assert env["PYTHONUNBUFFERED"] == "1"
    monkeypatch.setenv("PRAX_TOKEN", "fromenv")
    assert up.environment(data_dir)["PRAX_TOKEN"] == "fromenv"


def test_log_rotation_keeps_ten(data_dir: Path) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True)
    for i in range(13):
        current = up.rotate(logs, "door")
        current.write_text(f"run {i}\n")
        time.sleep(0.01)
    rotated = sorted(logs.glob("door.20*.log"))
    assert len(rotated) == up.KEEP_LOGS
    assert (logs / "door.log").read_text() == "run 12\n"


def _marker_venv(tmp_path: Path) -> Path:
    """A venv with a marker_server binary in it, as the role wants."""
    venv = tmp_path / "marker-venv"
    exe = venv / ("Scripts" if up.sys.platform == "win32" else "bin")
    exe.mkdir(parents=True, exist_ok=True)
    name = "marker_server.exe" if up.sys.platform == "win32" else "marker_server"
    (exe / name).write_bytes(b"MZ")
    return venv


def test_a_group_is_read_off_run(gpu_host: Path, tmp_path: Path) -> None:
    """``group``, ``needs_vram_mb`` and ``swap`` come off ``run:``; a
    served model needs what its file takes on the card; a bad ``swap``
    is refused."""
    roles = up.roles(
        {
            "llama-server": {"model": "big", "group": "card"},
            "marker": {
                "venv": str(_marker_venv(tmp_path)),
                "group": "card",
                "on_demand": True,
                "swap": "auto",
            },
            "door": {"port": 8000},
        }
    )
    by = {r.name: r for r in roles}
    assert by["llama-server"].group == "card" and by["marker"].group == "card"
    assert by["door"].group is None
    assert by["llama-server"].needs_vram_mb and by["llama-server"].needs_vram_mb > 0
    assert by["marker"].needs_vram_mb == up.MARKER_VRAM_MB
    assert by["marker"].swap == "auto" and by["llama-server"].swap == "ask"
    # on the CPU, marker wants nothing of the card
    cpu = up.roles(
        {"marker": {"venv": str(_marker_venv(tmp_path)), "ngl": 0, "group": "card"}}
    )
    assert cpu[0].needs_vram_mb is None
    with pytest.raises(up.UpError, match="swap: must be one of"):
        up.roles({"marker": {"venv": str(_marker_venv(tmp_path)), "swap": "sometimes"}})


def test_a_swap_hands_the_card_over_and_takes_it_back(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The holder stops, the borrower starts, and the group goes back
    when the door says nothing waits for the borrower."""
    data_dir.mkdir(parents=True, exist_ok=True)
    mark_a, argv_a = child(tmp_path, "holder", 60)
    mark_b, argv_b = child(tmp_path, "borrower", 60)
    holder = up.Role("llama-server", argv_a, group="card", needs_vram_mb=20000)
    borrower = up.Role(
        "marker", argv_b, group="card", needs_vram_mb=5000, on_demand=True
    )
    said: list[str] = []
    sup = up.Supervisor(
        [holder, borrower], data_dir=data_dir, say=said.append, tick=0.05
    )
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 100)  # they do not fit
    waiting = {"marker": 1}
    monkeypatch.setattr(
        up.Supervisor, "_ask_demand", lambda self: setattr(self, "demand", waiting)
    )
    monkeypatch.setattr(up, "GROUP_QUIET", 0.0)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        wait_for(lambda: runs_of(mark_a) >= 1)
        assert runs_of(mark_b) == 0  # on demand: not started with the rest
        # the group is in the status file before any swap: the Jobs view
        # has something to offer from the start
        before = json.loads((up.run_dir(data_dir) / up.STATUS).read_text())
        assert before["groups"]["card"]["holder"] is None
        up.swap(data_dir, "marker")
        wait_for(lambda: runs_of(mark_b) >= 1)
        status = json.loads((up.run_dir(data_dir) / up.STATUS).read_text())
        group = status["groups"]["card"]
        assert group["holder"] == "marker" and group["was_up"] == ["llama-server"]
        assert group["fits"] is False
        assert status["roles"]["llama-server"]["state"] in ("paused", "stopped", "down")
        # nothing waits any more: the card goes back on its own
        waiting.clear()
        wait_for(lambda: runs_of(mark_a) >= 2)
        status = json.loads((up.run_dir(data_dir) / up.STATUS).read_text())
        card = status["groups"]["card"]  # still declared, nothing on loan
        assert card["holder"] is None and card["members"] == [
            "llama-server",
            "marker",
        ]
        assert card["needs_vram_mb"] == {"llama-server": 20000, "marker": 5000}
        assert any("gives it back" in line for line in said)
    finally:
        up.stop(data_dir, wait=20)
        thread.join(timeout=10)


def test_a_borrower_that_fits_runs_beside_the_holder(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A card with room for both is not a swap: nothing is stopped."""
    data_dir.mkdir(parents=True, exist_ok=True)
    mark_a, argv_a = child(tmp_path, "holder", 60)
    mark_b, argv_b = child(tmp_path, "borrower", 60)
    roles = [
        up.Role("llama-server", argv_a, group="card", needs_vram_mb=2000),
        up.Role("marker", argv_b, group="card", needs_vram_mb=5000, on_demand=True),
    ]
    sup = up.Supervisor(roles, data_dir=data_dir, say=lambda _line: None, tick=0.05)
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 40000)  # room for both
    monkeypatch.setattr(
        up.hostinfo, "memory", lambda: {"ram_free_mb": 64000, "commit_free_mb": 64000}
    )
    monkeypatch.setattr(up.Supervisor, "_ask_demand", lambda self: None)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        wait_for(lambda: runs_of(mark_a) >= 1)
        up.swap(data_dir, "marker", back_when="never")
        wait_for(lambda: runs_of(mark_b) >= 1)
        status = json.loads((up.run_dir(data_dir) / up.STATUS).read_text())
        group = status["groups"]["card"]
        assert group["fits"] is True and group["was_up"] == ["llama-server"]
        assert status["roles"]["llama-server"]["state"] == "up"  # never stopped
        assert runs_of(mark_a) == 1  # and never restarted
        # each swap is a line to measure the card plan by (finding 20)
        lines = (up.run_dir(data_dir) / up.SWAPS).read_text().splitlines()
        swap = json.loads(lines[-1])
        assert (swap["kind"], swap["role"], swap["why"], swap["fits"]) == (
            "swap",
            "marker",
            "asked for",
            True,
        )
    finally:
        up.stop(data_dir, wait=20)
        thread.join(timeout=10)


def test_swap_says_when_a_role_has_no_group(data_dir: Path, tmp_path: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _mark, argv = child(tmp_path, "lonely", 60)
    said: list[str] = []
    sup = up.Supervisor(
        [up.Role("door", argv)], data_dir=data_dir, say=said.append, tick=0.05
    )
    sup._swap("door")
    assert any("not a role of a group" in line for line in said)
    sup._swap("nobody")
    assert sum("not a role of a group" in line for line in said) == 2


def test_idle_and_trim_are_read_off_run(gpu_host: Path) -> None:
    """A model server may give the card back after ``idle_minutes`` (its
    /metrics says how quiet it is) and be trimmed once loaded."""
    roles = up.roles({"llama-server": {"model": "big", "idle_minutes": 30}})
    server = roles[0]
    assert server.idle_minutes == 30
    assert server.metrics == "http://127.0.0.1:8085/metrics"
    assert server.trim is (sys.platform == "win32")
    off = up.roles({"llama-server": {"model": "big", "trim": False}})[0]
    assert off.idle_minutes == 0 and off.trim is False
    with pytest.raises(up.UpError, match="unknown setting"):
        up.roles({"door": {"idle_minutes": 5}})


def test_a_quiet_server_stops_and_comes_back_when_work_asks(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing done for ``idle_minutes``: the server is stopped and shown
    idle. The door saying work waits for it starts it again."""
    data_dir.mkdir(parents=True, exist_ok=True)
    mark, argv = child(tmp_path, "server", 60)
    server = up.Role(
        "llama-server",
        argv,
        health="http://127.0.0.1:1/health",
        metrics="http://127.0.0.1:1/metrics",
        idle_minutes=0.002,  # a tenth of a second
    )
    said: list[str] = []
    sup = up.Supervisor([server], data_dir=data_dir, say=said.append, tick=0.05)
    monkeypatch.setattr(up, "healthy", lambda url, timeout=3.0: True)
    monkeypatch.setattr(up, "IDLE_POLL", 0.0)
    monkeypatch.setattr(up.Supervisor, "_server_activity", lambda self, role: 7.0)
    waiting: dict[str, int] = {}
    monkeypatch.setattr(
        up.Supervisor, "_ask_demand", lambda self: setattr(self, "demand", waiting)
    )
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        wait_for(lambda: runs_of(mark) >= 1)
        wait_for(lambda: "llama-server" in sup.idled)
        wait_for(
            lambda: (
                json.loads((up.run_dir(data_dir) / up.STATUS).read_text())["roles"][
                    "llama-server"
                ]["state"]
                == "idle"
            )
        )
        assert runs_of(mark) == 1  # stopped, not restarted
        assert any("stopped until work asks" in line for line in said)
        waiting["llama-server"] = 3
        wait_for(lambda: runs_of(mark) >= 2)
        assert any("work waits for it" in line for line in said)
    finally:
        up.stop(data_dir, wait=20)
        thread.join(timeout=10)


@pytest.mark.parametrize("free_mb", [100, 40000])
def test_an_idle_server_waits_for_a_lent_card(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, free_mb: int
) -> None:
    """A server stopped for being quiet, whose card marker borrowed, does
    not load beside it when its work asks; it comes back with the card
    (2026-10-02: llama-server reloaded during marker's turn, and both
    models on the card ran the machine out of memory). Also when marker
    took the card beside it, the card free while the server was idle
    (2026-10-06: the 27B loaded beside marker and left 1.7 GB of commit)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    mark_a, argv_a = child(tmp_path, "holder", 60)
    mark_b, argv_b = child(tmp_path, "borrower", 60)
    holder = up.Role(
        "llama-server",
        argv_a,
        group="card",
        needs_vram_mb=20000,
        health="http://127.0.0.1:1/health",
        metrics="http://127.0.0.1:1/metrics",
        idle_minutes=0.002,
    )
    borrower = up.Role(
        "marker", argv_b, group="card", needs_vram_mb=5000, on_demand=True
    )
    said: list[str] = []
    sup = up.Supervisor(
        [holder, borrower], data_dir=data_dir, say=said.append, tick=0.05
    )
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: free_mb)
    monkeypatch.setattr(
        up.hostinfo, "memory", lambda: {"ram_free_mb": 64000, "commit_free_mb": 64000}
    )
    monkeypatch.setattr(up, "healthy", lambda url, timeout=3.0: True)
    monkeypatch.setattr(up, "IDLE_POLL", 0.0)
    monkeypatch.setattr(up, "GROUP_QUIET", 0.0)
    monkeypatch.setattr(up.Supervisor, "_server_activity", lambda self, role: 7.0)
    waiting: dict[str, int] = {}
    monkeypatch.setattr(
        up.Supervisor, "_ask_demand", lambda self: setattr(self, "demand", waiting)
    )
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        wait_for(lambda: "llama-server" in sup.idled)  # quiet, stopped
        waiting["marker"] = 2
        up.swap(data_dir, "marker")
        wait_for(lambda: runs_of(mark_b) >= 1)
        waiting["llama-server"] = 5  # its work asks while marker holds the card
        wait_for(lambda: any("after marker" in line for line in said))
        assert runs_of(mark_a) == 1  # not loaded beside marker
        waiting.pop("marker")  # marker is done: the card goes back
        wait_for(lambda: runs_of(mark_a) >= 2)
    finally:
        up.stop(data_dir, wait=20)
        thread.join(timeout=10)


def test_prax_up_follows_the_plan(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The door's plan (in its demand) says which group goes next. A role
    with ``swap: auto`` takes the card for it by itself, any other only
    for a person's "do it now"; never while an ask holds the card. Next
    for the role the card was lent away from ends the loan (stage AI)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    up.run_dir(data_dir).mkdir(parents=True, exist_ok=True)
    _, argv_a = child(tmp_path, "holder", 60)
    _, argv_b = child(tmp_path, "borrower", 60)
    holder = up.Role("llama-server", argv_a, group="card", needs_vram_mb=20000)
    auto = up.Role(
        "marker", argv_b, group="card", needs_vram_mb=5000, on_demand=True, swap="auto"
    )
    said: list[str] = []
    sup = up.Supervisor([holder, auto], data_dir=data_dir, say=said.append)
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 100)  # they do not fit
    door: dict[str, Any] = {"roles": {}, "now": {}, "ask_holds": False, "plan": []}

    def ask_demand(self: up.Supervisor) -> None:
        self.demand, self.now = door["roles"], door["now"]
        self.ask_holds, self.plan = door["ask_holds"], door["plan"]

    monkeypatch.setattr(up.Supervisor, "_ask_demand", ask_demand)
    marker_next = [{"role": "marker", "action": "marker", "why": "waited past 660 s"}]
    door.update(roles={"marker": 2}, plan=marker_next, ask_holds=True)
    sup._groups()
    assert "card" not in sup.groups  # an ask holds the card: it waits
    door["ask_holds"] = False
    sup._groups()
    assert sup.groups["card"]["holder"] == "marker"
    assert "llama-server" in sup.paused
    sup._groups()  # the plan says the same, and it is done already
    assert sup.groups["card"]["holder"] == "marker"
    # a person asked for llama-server: the plan puts it next, the loan ends
    door.update(
        now={"llama-server": None},
        plan=[{"role": "llama-server", "action": "vision", "why": "asked for now"}],
    )
    sup._groups()
    assert "card" not in sup.groups
    assert "marker" in sup.paused and "llama-server" not in sup.paused
    assert any("the plan: asked for now" in line for line in said)
    # a role without swap: auto follows the plan only for a person
    asks = up.Role("marker", argv_b, group="card", needs_vram_mb=5000, on_demand=True)
    other = up.Supervisor([holder, asks], data_dir=data_dir)
    door.update(now={}, plan=marker_next)
    other._groups()
    assert "card" not in other.groups
    door["now"] = {"marker": "marker"}
    other._groups()
    assert other.groups["card"]["holder"] == "marker"


def test_a_load_time_is_noted_and_kept(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A server's load time, start to its first health answer, goes into
    the status as the median of the last few and is kept across runs of
    the supervisor (``run/loads.json``): what a swap costs (stage AI)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    mark, argv = child(tmp_path, "server", 60)
    server = up.Role("llama-server", argv, health="http://127.0.0.1:1/health")
    said: list[str] = []
    sup = up.Supervisor([server], data_dir=data_dir, say=said.append, tick=0.05)
    assert sup.state["llama-server"]["load_s"] is None
    monkeypatch.setattr(up, "healthy", lambda url, timeout=3.0: runs_of(mark) >= 1)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        wait_for(lambda: sup.state["llama-server"]["state"] == "up")
        wait_for(lambda: sup.state["llama-server"]["load_s"] is not None)
        assert any("s from start" in line for line in said)
        assert cli.main(["up", "--status"]) == 0  # the command line says it
        assert "loads in" in capsys.readouterr().out
    finally:
        up.stop(data_dir, wait=20)
        thread.join(timeout=10)
    (up.run_dir(data_dir) / up.LOADS).write_text(
        json.dumps({"llama-server": [200.0, 1.0, 180.0, 190.0, 170.0, 175.0]})
    )
    again = up.Supervisor([server], data_dir=data_dir)
    assert again.loads["llama-server"] == [1.0, 180.0, 190.0, 170.0, 175.0]
    assert again.state["llama-server"]["load_s"] == 175.0
    status = json.loads((up.run_dir(data_dir) / up.STATUS).read_text())
    assert status["roles"]["llama-server"]["load_s"] is not None
