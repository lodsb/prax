"""AJ's reader contract: a reader declares its processes and what they
hold, a companion moves with its role in a swap, the venv is pinned to a
lock, and the reader's own logs are trimmed (``prax.host.readers``)."""

from __future__ import annotations

import json
import textwrap
import threading
from pathlib import Path

import pytest

from prax import config, work
from prax.host import plan, readers, up
from tests.test_up import _marker_venv, child, runs_of, wait_for

# ---------------------------------------------------------- the manifest


def test_a_reader_says_which_role_its_readings_wait_for() -> None:
    assert work.role_work()["marker"] == ("marker",)
    assert "figures" in work.role_work()["llama-server"]
    assert readers.of_role("marker") is readers.MARKER
    found = readers.companion_of("ocr-server")
    assert found is not None and found[0] is readers.MARKER
    # alone, marker starts its OCR server itself and holds its share too
    alone = readers.MARKER.alone()
    own, ocr = readers.MARKER.processes
    assert alone.vram_mb == (own.vram_mb or 0) + (ocr.vram_mb or 0)
    assert alone.vram_mb == up.MARKER_VRAM_MB


def _venv(tmp_path: Path, packages: dict[str, str]) -> Path:
    venv = tmp_path / "venv"
    site = venv / "Lib" / "site-packages"
    site.mkdir(parents=True)
    for name, version in packages.items():
        (site / f"{name}-{version}.dist-info").mkdir()
    return venv


def test_the_venv_is_held_to_its_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = tmp_path / "locks"
    locks.mkdir()
    (locks / "marker.txt").write_text(
        "# a comment\nMarker-PDF==2.0.0\nsurya_ocr==0.22.1\ntorch==2.14.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(readers, "LOCKS", locks)
    venv = _venv(
        tmp_path, {"marker_pdf": "2.1.0", "surya_ocr": "0.22.1", "pip": "26.2"}
    )
    lines = readers.drift(readers.MARKER, venv)
    assert lines[0] == "marker-pdf 2.1.0, locked 2.0.0"  # the reader's own first
    assert set(lines[1:]) == {"pip 26.2, not locked", "torch missing, locked 2.14.0"}
    # an upgrade made on purpose becomes the lock
    readers.write_lock(readers.MARKER, venv)
    assert readers.drift(readers.MARKER, venv) == []
    assert readers.locked(readers.MARKER)["marker-pdf"] == "2.1.0"
    assert readers.drift(readers.MARKER, tmp_path / "nowhere") == [
        f"no packages found in {tmp_path / 'nowhere'}"
    ]


def test_the_lock_in_the_repository_reads() -> None:
    pinned = readers.locked(readers.MARKER)
    assert "marker-pdf" in pinned and "surya-ocr" in pinned


def test_a_readers_own_log_is_cut_to_its_last_lines(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    line = b"making room for prompt cache entry\n"
    log.write_bytes(line * 100_000)  # 3.4 MB
    gave = readers.trim_log(log, keep_mb=1)
    kept = log.read_bytes()
    assert gave > 0 and len(kept) + gave == len(line) * 100_000
    assert len(kept) <= 1 << 20 and kept.startswith(line)  # from a line start
    assert readers.trim_log(log, keep_mb=1) == 0  # small now
    assert readers.trim_log(tmp_path / "absent.log") == 0


# ---------------------------------------------------------- the roles


@pytest.fixture()
def ocr_host(data_dir: Path, tmp_path: Path) -> Path:
    """A prax.yaml with marker and its OCR server's model."""
    data_dir.mkdir(parents=True, exist_ok=True)
    gguf = tmp_path / "models" / "surya-2.gguf"
    gguf.parent.mkdir()
    gguf.write_bytes(b"GGUF")
    (tmp_path / "models" / "surya-2-mmproj.gguf").write_bytes(b"GGUF")
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"MZ")
    (data_dir / config.CONFIG_NAME).write_text(
        textwrap.dedent(
            f"""
            models:
              surya:
                kind: openai
                base_url: http://127.0.0.1:8090/v1
                model: datalab-to/surya-ocr-2
                n_ctx: 12288
                serve:
                  path: {gguf.as_posix()}
                  mmproj: surya-2-mmproj.gguf
                  slots: 4
                  cache_ram_mb: 0
                  kv_type: f16
              other:
                kind: openai
                base_url: http://127.0.0.1:8091/v1
                model: something-else
                serve: {{path: {gguf.as_posix()}}}
            paths:
              llama_server: {binary.as_posix()}
            """
        ),
        encoding="utf-8",
    )
    return data_dir


def test_marker_is_told_where_its_ocr_server_is(ocr_host: Path, tmp_path: Path) -> None:
    venv = str(_marker_venv(tmp_path))
    roles = up.roles(
        {
            "marker": {"venv": venv, "group": "card", "on_demand": True},
            "ocr-server": {"model": "surya"},
        }
    )
    assert [r.name for r in roles] == ["ocr-server", "marker"]  # the start order
    ocr, marker = roles
    assert marker.env["SURYA_INFERENCE_URL"] == "http://127.0.0.1:8090/v1"
    assert "LLAMA_CPP_EXTRA_ARGS" not in marker.env  # surya spawns nothing
    assert marker.after == "ocr-server" and marker.companions == ("ocr-server",)
    # the companion has marker's group and starts, and the manifest's numbers
    assert ocr.companion_of == "marker" and ocr.group == "card" and ocr.on_demand
    assert ocr.needs_vram_mb == readers.MARKER.processes[1].vram_mb
    assert marker.needs_vram_mb == readers.MARKER.processes[0].vram_mb
    assert marker.needs_ram_mb == readers.MARKER.processes[0].ram_mb
    assert marker.own_logs == readers.MARKER.logs
    argv = ocr.argv
    assert argv[argv.index("--alias") + 1] == "datalab-to/surya-ocr-2"
    assert argv[argv.index("--cache-ram") + 1] == "0"
    assert argv[argv.index("--cache-type-k") + 1] == "f16"
    assert argv[argv.index("--ctx-size") + 1] == str(4 * 12288)
    # without the companion, marker starts surya's server itself, capped
    alone = up.roles({"marker": {"venv": venv}})[0]
    assert alone.env["LLAMA_CPP_EXTRA_ARGS"] == "--cache-ram 0"
    assert alone.needs_vram_mb == up.MARKER_VRAM_MB and not alone.companions


def test_what_an_ocr_server_cannot_be(ocr_host: Path, tmp_path: Path) -> None:
    venv = str(_marker_venv(tmp_path))
    with pytest.raises(up.UpError, match="companion; run.marker is missing"):
        up.roles({"ocr-server": {"model": "surya"}})
    with pytest.raises(up.UpError, match="the name surya checks"):
        up.roles({"marker": {"venv": venv}, "ocr-server": {"model": "other"}})
    with pytest.raises(up.UpError, match="ocr_parallel: with an ocr-server role"):
        up.roles(
            {
                "marker": {"venv": venv, "ocr_parallel": 4},
                "ocr-server": {"model": "surya"},
            }
        )
    with pytest.raises(up.UpError, match="unknown setting"):
        up.roles({"marker": {"venv": venv}, "ocr-server": {"model": "surya", "x": 1}})


# ---------------------------------------------------------- the supervisor


def _status(data_dir: Path) -> dict:
    return json.loads((up.run_dir(data_dir) / up.STATUS).read_text())


def test_a_companion_moves_with_its_role_in_a_swap(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The swap to marker starts its OCR server too, and the give-back
    stops both; the holder is never counted as one of the party."""
    data_dir.mkdir(parents=True, exist_ok=True)
    mark_a, argv_a = child(tmp_path, "holder", 60)
    mark_m, argv_m = child(tmp_path, "marker", 60)
    mark_o, argv_o = child(tmp_path, "ocr", 60)
    roles = [
        up.Role("llama-server", argv_a, group="card", needs_vram_mb=20000),
        up.Role(
            "ocr-server",
            argv_o,
            group="card",
            needs_vram_mb=3400,
            on_demand=True,
            companion_of="marker",
        ),
        up.Role(
            "marker",
            argv_m,
            group="card",
            needs_vram_mb=1600,
            on_demand=True,
            companions=("ocr-server",),
            after="ocr-server",
        ),
    ]
    sup = up.Supervisor(roles, data_dir=data_dir, say=lambda _l: None, tick=0.05)
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 100)
    waiting = {"marker": 1}
    monkeypatch.setattr(
        up.Supervisor, "_ask_demand", lambda self: setattr(self, "demand", waiting)
    )
    monkeypatch.setattr(up, "GROUP_QUIET", 0.0)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        wait_for(lambda: runs_of(mark_a) >= 1)
        assert runs_of(mark_o) == 0 and runs_of(mark_m) == 0
        status = _status(data_dir)
        assert status["roles"]["marker"]["with"] == ["ocr-server"]
        assert status["groups"]["card"]["with"] == {"marker": ["ocr-server"]}
        up.swap(data_dir, "marker")
        wait_for(lambda: runs_of(mark_o) >= 1 and runs_of(mark_m) >= 1)
        group = _status(data_dir)["groups"]["card"]
        assert group["holder"] == "marker" and group["was_up"] == ["llama-server"]
        waiting.clear()  # nothing left: both go, the holder comes back
        wait_for(lambda: runs_of(mark_a) >= 2)
        wait_for(
            lambda: (
                _status(data_dir)["roles"]["ocr-server"]["state"] == "paused"
                and _status(data_dir)["roles"]["marker"]["state"] == "paused"
            )
        )
    finally:
        up.stop(data_dir, wait=20)
        thread.join(timeout=10)


def test_starting_and_stopping_a_role_takes_its_companion_along(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    mark_m, argv_m = child(tmp_path, "marker", 60)
    mark_o, argv_o = child(tmp_path, "ocr", 60)
    roles = [
        up.Role("ocr-server", argv_o, on_demand=True, companion_of="marker"),
        up.Role("marker", argv_m, on_demand=True, companions=("ocr-server",)),
    ]
    sup = up.Supervisor(roles, data_dir=data_dir, say=lambda _l: None, tick=0.05)
    monkeypatch.setattr(up.Supervisor, "_ask_demand", lambda self: None)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        # the supervisor clears the command queue as it starts
        wait_for(lambda: (up.run_dir(data_dir) / up.STATUS).exists())
        up.start(data_dir, "marker", wait=10)
        wait_for(lambda: runs_of(mark_o) >= 1 and runs_of(mark_m) >= 1)
        up.stop(data_dir, name="marker", wait=10)
        wait_for(
            lambda: (
                _status(data_dir)["roles"]["ocr-server"]["state"] == "paused"
                and _status(data_dir)["roles"]["marker"]["state"] == "paused"
            )
        )
    finally:
        up.stop(data_dir, wait=20)
        thread.join(timeout=10)


def test_a_party_fits_only_when_all_of_it_does(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roles = [
        up.Role("ocr-server", ["x"], needs_vram_mb=3400, needs_ram_mb=2000),
        up.Role(
            "marker",
            ["x"],
            needs_vram_mb=1600,
            needs_ram_mb=5000,
            companions=("ocr-server",),
        ),
    ]
    sup = up.Supervisor(roles, data_dir=data_dir, say=lambda _l: None)
    marker = roles[1]
    monkeypatch.setattr(up.hostinfo, "memory", lambda: {"ram_free_mb": 16000})
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 4000)
    assert not sup._fits(marker)  # marker's 1.6 GB fits; with its server not
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 6000)
    assert sup._fits(marker)
    monkeypatch.setattr(up.hostinfo, "memory", lambda: {"ram_free_mb": 5000})
    assert not sup._fits(marker)  # the card has room, the machine does not
    monkeypatch.setattr(up.hostinfo, "memory", lambda: {"ram_free_mb": None})
    assert sup._fits(marker)  # a host that cannot say does not refuse


def test_a_readers_own_logs_are_trimmed_when_it_starts(
    data_dir: Path, tmp_path: Path
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "surya.log"
    log.write_bytes(b"x" * 100 + b"\n" + b"line\n" * 1_000_000)  # 5 MB
    mark, argv = child(tmp_path, "marker", 0.2)
    said: list[str] = []
    role = up.Role("marker", argv, own_logs=(str(log),), drift=("marker-pdf 2.1.0",))
    sup = up.Supervisor([role], data_dir=data_dir, say=said.append, tick=0.05)
    proc = sup._start(role)
    assert proc is not None
    proc.wait(timeout=20)
    assert log.stat().st_size <= readers.LOG_KEEP_MB << 20
    assert any("surya.log trimmed by" in line for line in said)
    assert any("differs from its lock: marker-pdf 2.1.0" in line for line in said)
    assert runs_of(mark) == 1


# ---------------------------------------------------------- the plan


def test_a_swap_costs_the_slower_of_a_role_and_its_companions() -> None:
    roles = {
        "marker": {"load_s": 18.0, "with": ["ocr-server"]},
        "ocr-server": {"load_s": None, "load_guess_s": 40.0},
        "llama-server": {"load_s": 44.0},
    }
    assert plan._load(roles, "marker") == (40.0, False)  # a guess in it
    roles["ocr-server"]["load_s"] = 12.0
    assert plan._load(roles, "marker") == (18.0, True)
    assert plan._load(roles, "nobody") == (plan.LOAD_GUESS_S, False)


def test_a_swap_to_a_companion_is_a_swap_to_its_role(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roles = [
        up.Role("llama-server", ["x"], group="card", needs_vram_mb=20000),
        up.Role("ocr-server", ["x"], group="card", companion_of="marker"),
        up.Role("marker", ["x"], group="card", companions=("ocr-server",)),
    ]
    sup = up.Supervisor(roles, data_dir=data_dir, say=lambda _l: None)
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 100)
    monkeypatch.setattr(up.Supervisor, "_record_swap", lambda *a, **k: None)
    monkeypatch.setattr(up.Supervisor, "_write_status", lambda self: None)
    sup._swap("ocr-server")
    assert sup.groups["card"]["holder"] == "marker"
    assert sup.groups["card"]["was_up"] == ["llama-server"]
    assert "llama-server" in sup.paused and not {"marker", "ocr-server"} & sup.paused


def test_a_load_that_would_leave_no_commit_does_not_fit(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows a server's VRAM is charged to commit: the 27B fit beside
    marker on the card and left 628 MB (2026-10-06). The commit a role
    takes is measured off its job object and kept; the check leaves
    ``COMMIT_RESERVE_MB`` free."""
    monkeypatch.setattr(up.sys, "platform", "win32")
    big = up.Role("llama-server", ["x"], group="card", needs_vram_mb=14900)
    sup = up.Supervisor([big], data_dir=data_dir, say=lambda _l: None)
    monkeypatch.setattr(up.hostinfo, "vram_free_mb", lambda: 20000)
    free = {"ram_free_mb": 16000, "commit_free_mb": 20000}
    monkeypatch.setattr(up.hostinfo, "memory", lambda: free)
    assert sup._fits(big)  # its card's worth (14.9 GB) leaves 5 GB of commit

    class Job:
        def peak_commit_mb(self) -> int:
            return 18379  # what the 27B committed: weights, KV cache, buffers

    sup.jobs["llama-server"] = Job()  # type: ignore[assignment]
    sup._note_commits()
    assert sup.commits == {"llama-server": 18379}
    assert not sup._fits(big)  # 1.6 GB would be left
    again = up.Supervisor([big], data_dir=data_dir, say=lambda _l: None)
    assert again.commits == {"llama-server": 18379}  # kept across runs
    free["commit_free_mb"] = 30000
    assert sup._fits(big)
    monkeypatch.setattr(up.sys, "platform", "linux")  # overcommitted: no wall
    free["commit_free_mb"] = 0
    assert sup._fits(big)
