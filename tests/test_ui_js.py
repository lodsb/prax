"""The UI script must at least parse: a syntax error blanks every view, and
nothing else in the suite executes JavaScript."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "src" / "prax" / "ui" / "app.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_app_js_parses() -> None:
    proc = subprocess.run(
        [shutil.which("node"), "--check", str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
