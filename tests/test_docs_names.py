"""The names the architecture document gives exist: every `prax.x.y` and
`store.<name>` in docs/architecture.md resolves to a module or an
attribute. The module map had drifted by a whole pass (2026-09-30: a
store of "nine" modules, a do_<step> in a worker that has none, eleven
modules missing); a name that no longer resolves now fails here."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture.md"
NAME = re.compile(r"`((?:prax|store)(?:\.[A-Za-z_][A-Za-z0-9_]*)+)")


def _resolves(dotted: str) -> bool:
    parts = dotted.split(".")
    if parts[0] == "store":
        parts = ["prax", *parts]
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for attr in parts[cut:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def test_every_name_the_architecture_gives_resolves() -> None:
    pytest.importorskip("fastapi")
    files = ("yaml", "toml", "md", "py", "json", "sql")  # prax.yaml is a file
    found = set(NAME.findall(DOC.read_text(encoding="utf-8")))
    names = sorted(n for n in found if n.rsplit(".", 1)[-1] not in files)
    assert len(names) > 50
    missing = [n for n in names if not _resolves(n)]
    assert not missing, "names in docs/architecture.md that do not exist: " + ", ".join(
        missing
    )
