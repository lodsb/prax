"""The UI's JavaScript: the script must parse (a syntax error blanks every
view) and the pure helpers in lib.js pass their node tests. Both run through
node when it is installed and are skipped otherwise; nothing else in the
suite executes JavaScript."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "prax" / "ui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
@pytest.mark.parametrize("script", ["app.js", "lib.js"])
def test_scripts_parse(script: str) -> None:
    proc = subprocess.run(
        [NODE, "--check", str(UI / script)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr


@needs_node
def test_lib_helpers() -> None:
    proc = subprocess.run(
        [NODE, "--test", str(ROOT / "tests" / "ui" / "lib.test.js")],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
