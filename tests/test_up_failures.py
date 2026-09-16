"""``prax up`` when things go wrong: the supervisor ended by the operating
system, a command that cannot start, backoff and its reset, a role that
waits in vain, a stale pid file, a child that will not stop."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from test_up import CHILD, PY, child, runs_of, wait_for

from prax import up


def pids_of(mark: Path) -> list[int]:
    return [int(x) for x in mark.read_text().split()] if mark.exists() else []


def test_the_supervisor_ended_by_the_os_takes_its_children_with_it(
    data_dir: Path, tmp_path: Path
) -> None:
    """Windows: a hard TerminateProcess on the supervisor closes its job
    object, which ends the children. Elsewhere: SIGTERM, and the
    supervisor stops them in order before it goes."""
    data_dir.mkdir(parents=True, exist_ok=True)
    mark, argv = child(tmp_path, "kid", 120)
    script = tmp_path / "sup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path
            from prax import up
            up.Supervisor(
                [up.Role("kid", {argv!r})], data_dir=Path({str(data_dir)!r}), tick=0.05
            ).run()
            """
        ),
        encoding="utf-8",
    )
    env = dict(os.environ, PRAX_DATA_DIR=str(data_dir))
    sup = subprocess.Popen([PY, str(script)], env=env, **up._spawn_kwargs())
    try:
        wait_for(lambda: runs_of(mark) == 1)
        kid = pids_of(mark)[0]
        assert up._alive(kid)
        if sys.platform == "win32":
            sup.kill()  # no signal, no handler: the job object is what is left
        else:
            sup.send_signal(signal.SIGTERM)
        sup.wait(timeout=20)
        wait_for(lambda: not up._alive(kid), seconds=15)
    finally:
        if sup.poll() is None:
            sup.kill()
        for pid in pids_of(mark):
            if up._alive(pid):
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)]
                    if sys.platform == "win32"
                    else ["kill", "-9", str(pid)],
                    check=False,
                    capture_output=True,
                )
    if sys.platform != "win32":  # an orderly stop leaves nothing behind
        assert not (up.run_dir(data_dir) / up.PIDFILE).exists()


def test_backoff_grows_resets_after_a_stable_run_and_never_blocks_stop(
    data_dir: Path, tmp_path: Path
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    mark_q, argv_q = child(tmp_path, "quick", 0.05, 1)  # dies at once, every time
    mark_s, argv_s = child(tmp_path, "steady", 0.6, 1)  # lives past `stable`
    said: list[str] = []
    sup = up.Supervisor(
        [up.Role("quick", argv_q), up.Role("steady", argv_s)],
        data_dir=data_dir,
        say=said.append,
        tick=0.05,
        backoff=(0.2, 0.5, 30),
        stable=0.4,
    )
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    wait_for(lambda: runs_of(mark_q) == 3 and runs_of(mark_s) >= 2, seconds=30)

    def delays(name: str) -> list[str]:
        return [
            line.rsplit("restart in ", 1)[1].split(" s")[0]
            for line in said
            if f" {name}: " in line and "restart in" in line
        ]

    wait_for(lambda: len(delays("quick")) >= 3)
    assert delays("quick")[:3] == ["0.2", "0.5", "30"]  # one failure after another
    assert set(delays("steady")) == {
        "0.2"
    }  # each death after a stable run: the first step
    # quick is now waiting 30 s; stop must not wait with it
    began = time.monotonic()
    assert up.stop(data_dir, wait=20)
    thread.join(timeout=10)
    assert time.monotonic() - began < 5
    assert runs_of(mark_q) == 3


def test_a_command_that_cannot_start_does_not_take_the_supervisor_down(
    data_dir: Path, tmp_path: Path
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    mark, argv = child(tmp_path, "fine", 60)
    said: list[str] = []
    sup = up.Supervisor(
        [up.Role("ghost", [str(tmp_path / "nope.exe")]), up.Role("fine", argv)],
        data_dir=data_dir,
        say=said.append,
        tick=0.05,
        backoff=(0.2, 0.2),
    )
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    wait_for(lambda: runs_of(mark) == 1)
    wait_for(lambda: any("ghost: cannot start" in line for line in said))
    wait_for(lambda: any("ghost: did not start; restart in" in line for line in said))
    assert sup.state["ghost"]["state"] == "down"
    assert sup.state["fine"]["state"] == "up"
    assert up.stop(data_dir, wait=20)
    thread.join(timeout=10)


def test_a_role_whose_patience_runs_out_starts_anyway(
    data_dir: Path, tmp_path: Path
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _, argv_l = child(tmp_path, "late", 60)
    mark_a, argv_a = child(tmp_path, "after", 60)
    said: list[str] = []
    sup = up.Supervisor(
        [
            up.Role("late", argv_l, health="http://127.0.0.1:9/health"),  # never
            up.Role("after", argv_a, after="late", patience=0.4),
        ],
        data_dir=data_dir,
        say=said.append,
        tick=0.05,
    )
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    wait_for(lambda: runs_of(mark_a) == 1)
    assert any("after: waiting for late" in line for line in said)
    assert any(
        "after: starting without waiting longer for late" in line for line in said
    )
    assert sup.state["late"]["state"] == "starting"
    assert up.stop(data_dir, wait=20)
    thread.join(timeout=10)


def test_a_stale_pid_file_is_cleared(data_dir: Path) -> None:
    """A supervisor that was killed leaves its pid file; the next `prax up`
    must not believe it."""
    run = up.run_dir(data_dir)
    run.mkdir(parents=True)
    dead = subprocess.Popen([PY, "-c", "pass"])
    dead.wait()
    (run / up.PIDFILE).write_text(f"{dead.pid} 2026-09-16T00:00:00\n")
    assert up.running_pid(data_dir) is None
    assert not (run / up.PIDFILE).exists()
    (run / up.PIDFILE).write_text("not a pid\n")
    assert up.running_pid(data_dir) is None
    assert up.status(data_dir) is None
    assert up.stop(data_dir) is True  # nothing to stop
    assert up.restart(data_dir, "door") is False


def test_restart_all_and_an_unknown_name(data_dir: Path, tmp_path: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    mark_a, argv_a = child(tmp_path, "a", 60)
    mark_b, argv_b = child(tmp_path, "b", 60)
    said: list[str] = []
    sup = up.Supervisor(
        [up.Role("a", argv_a), up.Role("b", argv_b)],
        data_dir=data_dir,
        say=said.append,
        tick=0.05,
    )
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    wait_for(lambda: runs_of(mark_a) == 1 and runs_of(mark_b) == 1)
    assert up.restart(data_dir, "all")
    wait_for(lambda: runs_of(mark_a) == 2 and runs_of(mark_b) == 2)
    assert all(
        "asked to restart; restart in 0 s" in line
        for line in said
        if "restart in" in line
    )
    assert up.restart(data_dir, "nope")
    wait_for(lambda: any("no role named nope" in line for line in said))
    assert runs_of(mark_a) == 2  # nothing else was touched
    assert up.stop(data_dir, wait=20)
    thread.join(timeout=10)


@pytest.mark.skipif(sys.platform == "win32", reason="terminate is already kill there")
def test_a_child_that_ignores_sigterm_is_killed_after_the_grace(
    data_dir: Path, tmp_path: Path
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n" + CHILD,
        encoding="utf-8",
    )
    mark = tmp_path / "stubborn.ran"
    said: list[str] = []
    sup = up.Supervisor(
        [up.Role("stubborn", [PY, str(script), str(mark), "60", "0"])],
        data_dir=data_dir,
        say=said.append,
        tick=0.05,
        grace=0.5,
    )
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    wait_for(lambda: runs_of(mark) == 1)
    pid = pids_of(mark)[0]
    assert up.stop(data_dir, wait=20)
    thread.join(timeout=10)
    assert any("stubborn: still running after 0.5 s, killed" in line for line in said)
    assert not up._alive(pid)
