"""The client half of a project's sync (``prax.client.project_files``):
which files of a working copy are its documents, read where they are."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from prax import client

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "synth"
    for rel, text in {
        "README.md": "# Synth\n\nA synthesizer.\n",
        "docs/design.md": "# Design\n\nThe voice.\n",
        "fw/notes.txt": "the firmware notes\n",
        "fw/main.c": "int main(void) { return 0; }\n",
        "LICENSE.md": "MIT\n",
        "build/_deps/lib-src/README.md": "# a vendored library\n",
        "build/lib-subbuild/CMakeFiles/x.txt": "cmake's\n",
        "fw/_deps/other/README.md": "# vendored again\n",
        "docs/empty.md": "   \n",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.org")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-f", ".")
    _git(root, "commit", "-qm", "first")
    _git(root, "remote", "add", "origin", "git@example.org:someone/synth.git")
    (root / "docs" / "draft.md").write_text("# not tracked yet\n", encoding="utf-8")
    return root


def test_the_documents_of_a_working_copy(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    got = client.project_files(root)
    assert got["remote"] == "example.org/someone/synth"
    assert got["prefix"] == "" and got["tracked"] is True
    paths = [f["path"] for f in got["files"]]
    # tracked documents only: no source, no licence, no vendored trees,
    # nothing empty, and not the draft git does not know yet
    assert paths == ["README.md", "docs/design.md", "fw/notes.txt"]
    first = got["files"][0]
    assert first["text"].startswith("# Synth") and len(first["sha256"]) == 64
    why = {k: v["count"] for k, v in got["skipped"].items()}
    assert why == {
        "a build or vendored folder": 3,
        "not a document file": 1,
        "a licence or requirements file": 1,
        "empty": 1,
    }
    assert "fw/main.c" in got["skipped"]["not a document file"]["examples"]
    # a dry run reads no text; untracked files count when asked
    dry = client.project_files(root, texts=False, tracked_only=False)
    assert "text" not in dry["files"][0]
    assert "docs/draft.md" in [f["path"] for f in dry["files"]]


def test_a_subdirectory_is_a_project_of_its_own(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    got = client.project_files(root / "fw")
    assert got["prefix"] == "fw" and got["remote"] == "example.org/someone/synth"
    assert [f["path"] for f in got["files"]] == ["notes.txt"]
    picked = client.project_files(root, include=["docs/**/*.md"], exclude=["docs/x*"])
    assert [f["path"] for f in picked["files"]] == ["docs/design.md"]


def test_a_folder_outside_git_is_walked(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("# A\n", encoding="utf-8")
    got = client.project_files(tmp_path / "notes")
    assert got["remote"] is None and got["tracked"] is False
    assert [f["path"] for f in got["files"]] == ["a.md"]
    with pytest.raises(ValueError):
        client.project_files(tmp_path / "missing")


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:someone/synth.git",
        "https://github.com/someone/synth",
        "https://user:secret@GitHub.com/someone/synth.git",
        "ssh://git@github.com:22/someone/synth.git",
        "https://github.com/someone/synth/",
    ],
)
def test_one_remote_however_it_is_written(url: str) -> None:
    assert client.canonical_remote(url) == "github.com/someone/synth"
    assert "secret" not in str(client.canonical_remote(url))


def test_no_remote_is_none() -> None:
    assert client.canonical_remote("") is None and client.canonical_remote(None) is None
