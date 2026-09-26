"""The invariants that a test can catch.

CLAUDE.md marks each of the ten with what kind of thing it is. The ones
marked *checked* are here, mechanically, because until 2026-09-26 three of
them lived in a scratch audit script that ran when somebody thought to
write it again — and a structural change is exactly when a silent breach
gets introduced.

The *enforced* ones are held by a code path and have their own tests
beside that path. The *measured* ones cannot be tested at all, which is
the point of the distinction: invariant 6 was breached for months because
"keep responses small" is a property of an answer, not of the code
(`store.attachment` reports those numbers instead).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "prax"

# invariant 3: the store is a package of ten modules in this order, and a
# module imports only from the ones before it
STORE_ORDER = (
    "base",
    "documents",
    "retrieval",
    "graph",
    "pages",
    "jobs",
    "summary",
    "repair",
    "maintain",
    "backup",
)


def _imports(path: Path) -> list[tuple[str, int]]:
    """The prax modules a file imports, with the line, including the ones
    imported inside a function."""
    out: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module:
                out.append((node.module, node.lineno))
            elif (node.module or "").startswith("prax"):
                out.append((node.module or "", node.lineno))
        elif isinstance(node, ast.Import):
            out += [
                (a.name, node.lineno) for a in node.names if a.name.startswith("prax")
            ]
    return out


def test_the_store_is_the_ten_modules_in_that_order() -> None:
    """Invariant 3. A module imports only from the ones before it, so the
    package has no cycle and the order in CLAUDE.md is the real one."""
    missing = [n for n in STORE_ORDER if not (SRC / "store" / f"{n}.py").exists()]
    assert not missing, f"the store is missing {missing}"

    broken = []
    for i, name in enumerate(STORE_ORDER):
        for dep, line in _imports(SRC / "store" / f"{name}.py"):
            head = dep.split(".")[0]
            if head in STORE_ORDER and STORE_ORDER.index(head) >= i:
                broken.append(f"{name}.py:{line} imports {head}")
    assert not broken, "the store's module order is broken: " + "; ".join(broken)


def test_the_mcp_server_is_a_thin_proxy() -> None:
    """Invariant 5. It makes one HTTP call per tool and must not reach
    into the store or the app — `tests/test_mcp.py` checks what a live
    process loads; this checks what the source asks for, which is what a
    reader changes."""
    asked = {m for m, _ in _imports(SRC / "mcp_server.py")}
    assert asked <= {"prax.client"}, f"the proxy imports more than the client: {asked}"


def test_no_module_outside_the_store_writes_to_sqlite() -> None:
    """Invariant 3, the other half. Reads outside the store are allowed
    and counted in `docs/audit/`; writes are not."""
    writes = re.compile(
        r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", re.IGNORECASE
    )
    guilty: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "store" in path.parts:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if writes.search(line) and not line.lstrip().startswith("#"):
                guilty.append(f"{path.relative_to(SRC)}:{i}")
    assert not guilty, "SQL writes outside prax.store: " + "; ".join(guilty)


def test_the_zotero_importer_opens_its_source_read_only() -> None:
    """Invariant 10. Nothing in prax modifies a Zotero library, so the
    copy is opened with `mode=ro` and never without it."""
    text = (SRC / "importers" / "zotero.py").read_text(encoding="utf-8")
    # the whole statement, not up to the first bracket: the path is built
    # with an f-string that closes one of its own
    opens = [
        line.strip()
        for line in text.splitlines()
        if "sqlite3.connect(" in line and not line.lstrip().startswith("#")
    ]
    assert opens, "the importer no longer opens a database; check this test"
    for call in opens:
        assert "mode=ro" in call, f"the importer opens its source writable: {call[:90]}"
