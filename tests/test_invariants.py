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

import pytest

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


def _relative(path: Path) -> list[tuple[int, str, int]]:
    """A file's relative imports: how many levels up, from what, the line."""
    return [
        (node.level, node.module or "", node.lineno)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level
    ]


def _declared_order(init: Path) -> tuple[str, ...]:
    """A store subpackage's ``ORDER``, read without importing it."""
    for node in ast.parse(init.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "ORDER" for t in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    return ()


def test_the_store_is_the_ten_modules_in_that_order() -> None:
    """Invariant 3. A module imports only from the ones before it, so the
    package has no cycle and the order in CLAUDE.md is the real one.

    A module that grew past two thousand lines is a package of parts
    (`documents`, `graph`) with an ``ORDER`` of its own: a part imports
    only from the parts before it, and from the store's modules before its
    package."""
    store = SRC / "store"
    missing = [
        n
        for n in STORE_ORDER
        if not ((store / f"{n}.py").exists() or (store / n / "__init__.py").exists())
    ]
    assert not missing, f"the store is missing {missing}"

    broken = []
    for i, name in enumerate(STORE_ORDER):
        if (store / f"{name}.py").exists():
            files: list[tuple[Path, int, str | None]] = [
                (store / f"{name}.py", 1, None)
            ]
            order: tuple[str, ...] = ()
        else:
            order = _declared_order(store / name / "__init__.py")
            on_disk = {p.stem for p in (store / name).glob("*.py")} - {"__init__"}
            assert set(order) == on_disk, f"{name}: ORDER {order} but files {on_disk}"
            files = [(store / name / f"{p}.py", 2, p) for p in order]
            files.append((store / name / "__init__.py", 2, None))
        for path, depth, part in files:
            where = f"{path.relative_to(store)}"
            for level, module, line in _relative(path):
                head = module.split(".")[0]
                if level == depth and head in STORE_ORDER:
                    if STORE_ORDER.index(head) >= i:
                        broken.append(f"{where}:{line} imports {head}")
                elif (
                    level == 1
                    and depth == 2
                    and part is not None
                    and (head not in order or order.index(head) >= order.index(part))
                ):
                    broken.append(f"{where}:{line} imports {name}.{head}")
    assert not broken, "the store's module order is broken: " + "; ".join(broken)


def test_the_mcp_server_is_a_thin_proxy() -> None:
    """Invariant 5. It makes one HTTP call per tool and must not reach
    into the store or the app — `tests/test_mcp.py` checks what a live
    process loads; this checks what the source asks for, which is what a
    reader changes."""
    asked = {m for m, _ in _imports(SRC / "mcp_server.py")}
    assert asked <= {"prax.client"}, f"the proxy imports more than the client: {asked}"


PRAX_TABLES = (
    "acronyms|chunk_embeddings|chunks|chunks_fts|document_embeddings|documents"
    "|documents_fts|edges|entities|entity_candidates|entity_labels|jobs"
    "|page_revisions|pages|readings|review_queue|spend"
)


def test_no_module_outside_the_store_reads_prax_s_tables() -> None:
    """Invariant 3, reads too: a question about the library is a store
    read. They were allowed and counted, and the count went 26, 38, 41
    (2026-09-22 to -27): a query written where it is needed is the easy
    way, so only a test holds the line. The Zotero importer's own queries
    read Zotero's tables, not these, and pass."""
    # a keyword-index query names its table in an f-string as often as not;
    # its MATCH does not change
    reads = re.compile(rf"\b(FROM|JOIN)\s+({PRAX_TABLES})\b|\bMATCH\s+\?")
    guilty: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "store" in path.parts:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if reads.search(line) and not line.lstrip().startswith("#"):
                guilty.append(f"{path.relative_to(SRC)}:{i}")
    assert not guilty, "SQL reads outside prax.store: " + "; ".join(guilty)


def test_no_module_outside_the_store_writes_to_sqlite() -> None:
    """Invariant 3, the other half: no writes outside the store."""
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


def test_a_file_with_a_shebang_is_executable_in_git() -> None:
    """Ruff's EXE001 fails CI on Linux for a script with a shebang that git
    does not mark executable, and Windows has no bit for ruff to see here
    (2026-09-28). Git keeps the mode on every system, so ask git."""
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("no git")
    root = Path(__file__).resolve().parents[1]
    listed = subprocess.run(
        [git, "ls-files", "-s", "--", "*.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        pytest.skip("not a git checkout")
    wrong = []
    for line in listed.stdout.splitlines():
        mode, _, rest = line.partition(" ")
        path = rest.split("\t", 1)[-1]
        f = root / path
        if f.is_file() and f.read_bytes()[:2] == b"#!" and mode != "100755":
            wrong.append(path)
    assert wrong == [], f"git update-index --chmod=+x {' '.join(wrong)}"
