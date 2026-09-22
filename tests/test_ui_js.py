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
EXT = ROOT / "clients" / "browser-extension"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
@pytest.mark.parametrize(
    "script",
    [*sorted(UI.glob("*.js")), *sorted(EXT.glob("*.js"))],
    ids=lambda p: p.name,
)
def test_scripts_parse(script: Path) -> None:
    proc = subprocess.run(
        [NODE, "--check", str(script)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr


@needs_node
@pytest.mark.parametrize("suite", ["lib.test.js", "extension.test.js"])
def test_js_helpers(suite: str) -> None:
    proc = subprocess.run(
        [NODE, "--test", str(ROOT / "tests" / "ui" / suite)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_manifest_is_one_codebase_for_both_browsers() -> None:
    import json

    m = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3
    assert "scripts" in m["background"] and "service_worker" in m["background"]
    assert m["browser_specific_settings"]["gecko"]["id"]
    for name in [*m["background"]["scripts"], m["background"]["service_worker"]]:
        assert (EXT / name).is_file(), name
    for page in (m["action"]["default_popup"], m["options_ui"]["page"]):
        assert (EXT / page).is_file(), page
    for icon in m["icons"].values():
        assert (EXT / icon).is_file(), icon


def test_singlefile_is_vendored_with_its_licence() -> None:
    sf = EXT / "vendor" / "single-file"
    core = (sf / "single-file.js").read_text(encoding="utf-8", errors="replace")
    assert ".singlefile={}" in core[:400]  # the UMD bundle defines the global
    for name in (
        "single-file-frames.js",
        "single-file-hooks-frames.js",
        "LICENSE",
        "NOTICE.md",
    ):
        assert (sf / name).is_file(), name
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (sf / "LICENSE").read_text(
        encoding="utf-8"
    )
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (EXT / "LICENSE").read_text(
        encoding="utf-8"
    )
