"""The plugin's session-end hook: a project that opted in (a
``.prax-project`` file in its root) has its documentation files sent to
the library, keyed by path and versioned by content, so a note rewritten
during the session replaces its earlier self.

Runs with whatever ``python`` the hook finds; when that interpreter has
no prax, and ``PRAX_PYTHON`` names one that does, it hands over to it.
Quiet on success; a project without the file is left alone. Never
fails the session: a door that is down is a warning on stderr."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SETTINGS_FILE = ".prax-project"


def main() -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if not (root / SETTINGS_FILE).is_file():
        return 0
    try:
        from prax_cli.main import main as prax
    except ImportError:
        other = os.environ.get("PRAX_PYTHON")
        if other and Path(other).resolve() != Path(sys.executable).resolve():
            return subprocess.call([other, __file__])
        print(
            "prax sync: no prax in this interpreter; set PRAX_PYTHON to the"
            " one that has it",
            file=sys.stderr,
        )
        return 0
    try:
        return prax(["import", "project", str(root), "--refresh", "--quiet"])
    except Exception as exc:  # noqa: BLE001 - the session must end regardless
        print(f"prax sync: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
