"""The plugin's session-end hook: a project that opted in (a
``.prax-project`` file in its root) has its documentation files sent to
the library, keyed by path and versioned by content, so a note rewritten
during the session replaces its earlier self. With ``archive:`` in that
file, the session's transcript (what was said, not what was run) and
the agent's memory files go too:

    archive: [transcripts, memory]

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


def archive_choices(root: Path) -> list[str]:
    """The ``archive:`` list of ``.prax-project``, read without YAML so the
    hook needs nothing installed to decide."""
    text = (root / SETTINGS_FILE).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "archive":
            return [
                w.strip().strip("'\"")
                for w in value.strip().strip("[]").split(",")
                if w.strip().strip("'\"")
            ]
    return []


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
    runs = [["import", "project", str(root), "--refresh", "--quiet"]]
    choices = archive_choices(root)
    if "transcripts" in choices:
        runs.append(["import", "claude", str(root), "--refresh", "--quiet"])
    if "memory" in choices:
        from prax.importers import claude

        memory = claude.memory_dir(root)
        if memory.is_dir():
            runs.append(
                [
                    "import",
                    "project",
                    str(memory),
                    "--name",
                    f"{root.resolve().name}-memory",
                    "--refresh",
                    "--quiet",
                ]
            )
    worst = 0
    for argv in runs:
        try:
            worst = max(worst, int(prax(argv) or 0))
        except Exception as exc:  # noqa: BLE001 - the session must end regardless
            print(f"prax sync: {' '.join(argv[:2])}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
