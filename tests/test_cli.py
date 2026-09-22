"""The `prax` command: a client like the extension and the MCP proxy. It
talks to a door over HTTP, opens no database, and says something useful
when the door is not there."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prax_cli import main as cli

from prax.client import Door


@pytest.fixture()
def door(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A door on this test's data directory, with the command pointed at it."""
    from prax.api import app

    with TestClient(app) as client:
        monkeypatch.setattr(
            cli,
            "_door_of",
            lambda a: Door("http://testserver", client=client, name="test"),
        )
        yield client


def run(*args: str) -> int:
    return cli.main(list(args))


def test_status_says_what_the_store_holds(
    door: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    door.post("/ingest", json={"text": "granular synthesis " * 30, "title": "G"})
    assert run("status") == 0
    printed = capsys.readouterr().out
    assert "1 documents" in printed or "1 document" in printed
    assert "Ontology" in printed and "core3" in printed
    assert "Store" in printed


def test_search_shows_hits_with_their_document_ids(
    door: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = door.post(
        "/ingest", json={"text": "feedback delay networks " * 40, "title": "FDN"}
    ).json()["doc_id"]
    assert run("search", "feedback", "delay") == 0
    printed = capsys.readouterr().out
    assert "FDN" in printed and f"doc {doc}" in printed
    assert f"prax show {doc}" in printed  # what to type next
    assert run("search", "nothing-like-this-exists") == 1
    assert "Nothing found" in capsys.readouterr().out


def test_search_json_is_machine_readable(
    door: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    door.post("/ingest", json={"text": "wave digital filters " * 40, "title": "WDF"})
    assert run("search", "wave", "digital", "--json") == 0
    hits = json.loads(capsys.readouterr().out)
    assert hits and hits[0]["title"] == "WDF"


def test_ask_shows_the_trail_then_the_answer(
    door: TestClient,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With --answer the trail streams line by line before the answer;
    --steps 0 asks in one shot and prints no trail."""
    monkeypatch.setenv("PRAX_ASK", "stub")
    door.post(
        "/ingest", json={"text": "granular synthesis of clouds " * 40, "title": "G"}
    )
    assert run("ask", "--answer", "--steps", "3", "granular", "synthesis") == 0
    printed = capsys.readouterr().out
    assert "0. searched granular synthesis" in printed
    assert "1. enough read" in printed and "writing the answer from" in printed
    assert "Stub answer to 'granular synthesis' [1]." in printed
    assert "1 steps" in printed or "1 step" in printed
    assert run("ask", "--answer", "--steps", "0", "granular", "synthesis") == 0
    printed = capsys.readouterr().out
    assert "searched" not in printed and "Stub answer" in printed


def test_add_a_file_a_url_and_a_pipe(
    door: TestClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("a note worth keeping " * 20, encoding="utf-8")
    assert run("add", str(note), "--domain", "research") == 0
    printed = capsys.readouterr().out
    assert "note.md" in printed and "doc " in printed and "1 added" in printed
    doc_id = int(printed.split("doc ")[1].split()[0])
    assert door.get(f"/doc/{doc_id}/domains").json()["domains"] == ["research"]

    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("piped thought " * 20))
    assert run("add", "-", "--title", "from the pipe") == 0
    assert "doc " in capsys.readouterr().out

    assert run("add", str(tmp_path / "nope.pdf")) == 1
    assert "no such file" in capsys.readouterr().err


def test_show_prints_the_document_and_offers_the_rest(
    door: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = door.post(
        "/ingest", json={"text": "alpha " * 500, "title": "Long one"}
    ).json()["doc_id"]
    assert run("show", str(doc), "--chars", "200") == 0
    printed = capsys.readouterr().out
    assert "Long one" in printed and f"doc {doc}" in printed
    assert "alpha" in printed and "--offset 200" in printed
    assert run("show", "999999") == 1
    assert "no such document" in capsys.readouterr().err


def test_jobs_inbox_pages_and_doctor_run(
    door: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    for command, expected in (
        ("jobs", "Nothing running"),
        ("inbox", "Drop folder"),
        ("pages", "No pages yet"),
        ("doctor", "the door"),
    ):
        assert run(command) == 0, command
        assert expected in capsys.readouterr().out


def test_graph_keeps_self_edges_out_of_the_way(
    door: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    for src, dst in (("A", "B"), ("A", "C")):
        door.post(
            "/link",
            json={
                "src": src,
                "src_type": "concept",
                "rel": "extends",
                "dst": dst,
                "dst_type": "concept",
            },
        )
    assert run("graph", "A") == 0
    printed = capsys.readouterr().out
    assert "2 edges" in printed and "B" in printed and "C" in printed
    assert run("graph", "nobody-knows-this") == 1


def test_the_overview_is_what_a_bare_prax_prints(
    door: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    door.post("/ingest", json={"text": "one document " * 30, "title": "One"})
    assert cli.main([]) == 0  # the bare command talks to the same door
    printed = capsys.readouterr().out
    assert "prax search" in printed and "prax work --watch" in printed
    assert "1 documents" in printed or "1 document" in printed


def test_a_door_that_is_not_there_is_said_plainly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("status", "--door", "http://127.0.0.1:9") == 2
    said = capsys.readouterr().err
    assert "no door at http://127.0.0.1:9" in said
    assert "prax serve" in said


def test_the_command_opens_no_database() -> None:
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, prax_cli.main;"
                " print(sorted(m for m in sys.modules if m.startswith('prax.')))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env={
            **__import__("os").environ,
            "PYTHONPATH": f"src{__import__('os').pathsep}clients/cli",
        },
    )
    loaded = json.loads(out.stdout.replace("'", '"'))
    assert "prax.store" not in loaded and "prax.api" not in loaded
    # the door's HTTP client, and the names of the worker's steps (a module
    # of tuples, so that the --steps default is not a second list)
    assert loaded == ["prax.client", "prax.steps"]


def test_help_lists_the_everyday_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_code:
        cli.main(["--help"])
    assert exit_code.value.code == 0
    printed = capsys.readouterr().out
    for command in ("search", "ask", "add", "show", "work", "serve", "doctor"):
        assert command in printed


def test_models_needs_a_name_to_fetch(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("models", "fetch") == 2
    assert "which model" in capsys.readouterr().err


def test_every_command_has_help_and_an_example() -> None:
    parser = cli.build_parser()
    actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    commands: dict[str, Any] = dict(actions[0].choices)  # the subparsers
    assert len(commands) >= 14
    for name, sub in commands.items():
        assert sub.description, name


def test_backup_follows_the_job_to_the_end(
    door: TestClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    door.post("/ingest", json={"text": "granular synthesis " * 30, "title": "G"})
    assert run("backup", str(tmp_path / "copy")) == 0
    printed = capsys.readouterr().out
    assert "Backup" in printed and "new archive files" in printed
    assert (tmp_path / "copy" / "prax.db").is_file()
    assert run("backup", "not/absolute") == 1
    assert "absolute" in capsys.readouterr().err


def test_import_links_dry_run_then_for_real(
    door: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from prax import inbox

    page = "<html><head><title>WDF</title></head><body><p>{}</p></body></html>"

    def fake_fetch(url: str, *, timeout: float = 0) -> tuple[bytes, str, str]:
        return page.format("wave digital filters " * 40).encode(), "text/html", url

    monkeypatch.setattr(inbox, "fetch_url", fake_fetch)
    reading = tmp_path / "reading.txt"
    reading.write_text("https://example.org/wdf the classic\n", encoding="utf-8")
    assert run("import", "links", str(reading), "--dry-run") == 0
    printed = capsys.readouterr().out
    assert "Would add 1" in printed and "Nothing was sent" in printed
    assert run("import", "links", str(reading), "--domain", "research") == 0
    printed = capsys.readouterr().out
    assert "links: 1 added" in printed
    assert run("import", "links", str(reading)) == 0
    assert "1 already there" in capsys.readouterr().out
    assert run("import", "chat") == 2  # which files?
    monkeypatch.delenv("PRAX_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("PRAX_GITHUB_USER", raising=False)
    assert run("import", "github") == 2  # whose stars?


def test_import_project_reads_the_directory_quietly(
    door: TestClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "gadget"
    root.mkdir()
    (root / "NOTES.md").write_text("# Gadget\n\nDecisions.\n", encoding="utf-8")
    (root / ".prax-project").write_text("domains: [workshop]\n", encoding="utf-8")
    assert run("import", "project", str(root), "--quiet") == 0
    assert capsys.readouterr().out == ""
    assert run("import", "project", str(root)) == 0
    printed = capsys.readouterr().out
    assert "Project" in printed and "gadget" in printed and "1 already there" in printed
    listing = door.get("/documents", params={"tag": "project:gadget"}).json()
    assert listing["total"] == 1 and listing["items"][0]["meta"]["domains"] == [
        "workshop"
    ]
    assert run("import", "project", str(tmp_path / "missing")) == 2


def test_readings_shows_the_queue_and_wait_blocks_until_it_drains(
    door: TestClient,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line between the steps of a marker evening — swap the card,
    ask, wait until nothing waits, swap back — on any platform: the
    counts per extractor, and --wait polling until they are gone."""
    from prax_cli import running

    from prax import store

    con = door.app.state.con
    assert run("readings") == 0
    assert "Nothing waiting" in capsys.readouterr().out
    pdfs = []
    for i in range(3):
        pdf = store.register(
            con, b"%PDF-1.4 " + str(i).encode(), mime="application/pdf", title=f"p{i}"
        )["doc_id"]
        store.index_text(con, pdf, f"old text {i} " * 40, text_source="pymupdf4llm/1")
        pdfs.append(pdf)
    assert run("reread", "--extractor", "marker", "--ids", *map(str, pdfs)) == 0
    text = store.ingest_text(con, "# M\n\n$$x = 1 \\quad (1)$$\n\ntext " * 3)["doc_id"]
    assert (
        door.post(f"/doc/{text}/reading", json={"extractor": "formulas"}).status_code
        == 200
    )
    assert run("readings") == 0
    printed = capsys.readouterr().out
    assert (
        "Waiting (4)" in printed and "3  marker" in printed and "1  formulas" in printed
    )
    assert run("readings", "--extractor", "marker") == 0
    assert "Waiting (3)" in capsys.readouterr().out
    # --wait: the marker requests finish one a look; the formulas one stays
    looks = {"n": 0}

    def a_look(seconds: float) -> None:
        looks["n"] += 1
        if pdfs:
            store.finish_reading(con, pdfs.pop(), outcome="done", stamp="marker/2")

    monkeypatch.setattr(running.time, "sleep", a_look)
    assert run("readings", "--wait", "--extractor", "marker", "--every", "0") == 0
    printed = capsys.readouterr().out
    assert "3 marker waiting" in printed and "marker: nothing waiting" in printed
    assert looks["n"] == 3
    # the finished ones show under Lately although the waiting one is newer
    assert run("readings", "-n", "1") == 0
    printed = capsys.readouterr().out
    assert "Lately" in printed and "marker" in printed.split("Lately")[1]
    # the formulas request still waits: a wait on everything times out
    monkeypatch.setattr(running.time, "sleep", lambda s: None)
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(running.time, "monotonic", lambda: float(next(clock)))
    assert run("readings", "--wait", "--timeout", "1") == 2
    assert "still waiting after 1 min" in capsys.readouterr().err


def test_following_a_job_survives_a_dropped_poll(
    door: TestClient,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A poll the door drops (a keep-alive connection closed under load)
    is not the end of the job: the CLI waits and asks again."""
    import httpx
    from prax_cli import running

    started = door.post("/maintain", json={"only": ["fields"]}).json()
    real = running.Door.get_json
    dropped = {"n": 0}

    def flaky(self: Any, path: str, params: Any = None) -> Any:
        if path.startswith("/jobs/") and dropped["n"] < 2:
            dropped["n"] += 1
            raise httpx.RemoteProtocolError("Server disconnected")
        return real(self, path, params)

    monkeypatch.setattr(running.Door, "get_json", flaky)
    monkeypatch.setattr(running.time, "sleep", lambda s: None)
    client = Door("http://testserver", client=door, name="test")
    code = running.follow_job(client, started["job"], quiet=False, what="the pass")
    assert code == 0 and dropped["n"] == 2
    assert "did not answer a poll" in capsys.readouterr().out


def test_questions_are_listed_and_asked_again(
    door: TestClient,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRAX_ASK", "stub")
    door.post(
        "/ingest",
        json={"text": "feedback delay networks build reverb " * 30, "title": "FDN"},
    )
    assert run("questions") == 0
    assert "No standing questions" in capsys.readouterr().out
    assert (
        run(
            "ask",
            "--answer",
            "--steps",
            "0",
            "--stand",
            "how",
            "do",
            "FDNs",
            "build",
            "reverb",
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert "Standing question q-how-do-fdns-build-reverb" in printed
    assert run("questions") == 0
    printed = capsys.readouterr().out
    assert "1, 0 with something new" in printed and "settled" in printed
    door.post(
        "/ingest",
        json={
            "text": "feedback delay networks build reverb densely " * 30,
            "title": "FDN 2",
        },
    )
    assert run("questions") == 0
    printed = capsys.readouterr().out
    assert "1 with something new" in printed and "FDN 2" in printed
    assert run("questions", "--ask", "--briefing") == 0
    printed = capsys.readouterr().out
    assert "1 of 1 asked again" in printed and "briefing" in printed
    assert run("questions", "--json") == 0
    assert json.loads(capsys.readouterr().out)[0]["revision"] == 2
    # an ask block in a page of one's own is listed beside it, as slug#id;
    # saved through the door it is answered at once; a hand in it is shown
    text = (
        "# My notes\n\nMine.\n\n"
        '<!-- prax:ask id=q1 steps=0 "how do FDNs build reverb" -->\n'
        "<!-- /prax:ask id=q1 -->\n"
    )
    job = door.put("/page/my-notes", json={"text": text, "title": "My notes"}).json()
    for _ in range(100):
        if door.get(f"/jobs/{job['job']}").json()["status"] != "running":
            break
        time.sleep(0.1)
    assert run("questions") == 0
    printed = capsys.readouterr().out
    assert "my-notes#q1" in printed and "in My notes" in printed
    page = door.get("/page/my-notes").json()
    assert page["blocks"][0]["filled"]
    edited = page["text"].replace("Stub answer", "My own answer")
    door.put("/page/my-notes", json={"text": edited})
    assert run("questions") == 0
    printed = capsys.readouterr().out
    assert "held" in printed and "--release" in printed
    assert run("questions", "--ask", "my-notes#q1", "--release") == 0
    assert "1 of 1 asked again" in capsys.readouterr().out
    assert "Stub answer" in door.get("/page/my-notes").json()["text"]
