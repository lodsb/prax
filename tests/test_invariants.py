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


def test_the_parsers_parts_import_only_the_ones_before_them() -> None:
    """``parsers/__init__.py`` was 1,706 lines of parsing until the
    engineering pass of 2026-09-28 cut it into parts by what they read; the
    ``ORDER`` it declares is the one they keep, so there is no cycle."""
    parsers = SRC / "parsers"
    order = _declared_order(parsers / "__init__.py")
    assert order, "parsers/__init__.py declares no ORDER"
    broken = []
    for k, part in enumerate(order):
        for level, module, line in _relative(parsers / f"{part}.py"):
            head = module.split(".")[0]
            if level == 1 and head in order and order.index(head) >= k:
                broken.append(f"{part}.py:{line} imports {head}")
    assert not broken, "the parsers' order is broken: " + "; ".join(broken)


def _top_level_parsers_imports(path: Path) -> set[str]:
    """What a parsers module imports of ``prax.parsers`` at its top level
    (not inside a function): ``parsers`` for the package itself, else the
    module's name."""
    out: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom):
            if node.level == 1:
                out |= {node.module or a.name for a in node.names}
            elif node.module == "prax.parsers":
                out |= {a.name for a in node.names}
            elif node.module == "prax":
                out |= {a.name for a in node.names if a.name == "parsers"}
    return out


def test_a_parsers_helper_does_not_reach_back_into_the_parts() -> None:
    """The modules outside the ``ORDER`` (figures, formulas, vision, …) are
    helpers the parts call. One a part imports at its top may not import the
    package or a part at its own top, or the two wait on each other."""
    parsers = SRC / "parsers"
    order = _declared_order(parsers / "__init__.py")
    helpers = {p.stem for p in parsers.glob("*.py")} - set(order) - {"__init__"}
    used = set()
    for part in order:
        used |= _top_level_parsers_imports(parsers / f"{part}.py") & helpers
    assert used, "no part imports a helper at its top: the test reads nothing"
    broken = [
        f"{h}.py imports {x}"
        for h in sorted(used)
        for x in sorted(_top_level_parsers_imports(parsers / f"{h}.py"))
        if x in order or x == "parsers" or x not in helpers
    ]
    assert not broken, "a parsers helper reaches back: " + "; ".join(broken)


def test_the_mcp_server_is_a_thin_proxy() -> None:
    """Invariant 5. It makes one HTTP call per tool and must not reach
    into the store or the app — `tests/test_mcp.py` checks what a live
    process loads; this checks what the source asks for, which is what a
    reader changes."""
    asked = {m for m, _ in _imports(SRC / "mcp_server.py")}
    assert asked <= {"prax.client"}, f"the proxy imports more than the client: {asked}"


STORE_MAY_STAND_ON = (
    "prax.config",
    "prax.packs",  # the manifests: data (docs/packs.md)
    "prax.text",
    "prax.ml",
    "prax.graph.ontology",
)


def test_the_store_stands_only_on_what_is_below_it() -> None:
    """The store is the door (invariant 3), not the bottom layer: a pass
    that must touch the tables and needs the graph's or a parser's logic
    (replaying the review queue, healing a document's type) lives in it and
    calls up, inside a function. At the top of a module it may import only
    what is below it: the configuration, text, the models' files and
    indexes, and the ontology. Anything else there would be an import
    cycle waiting for the day both ends load first (the engineering pass
    of 2026-09-28 counted a dozen lazy calls up, and no top-level one)."""
    reaching: list[str] = []
    for path in sorted((SRC / "store").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == "prax":
                    names = [f"prax.{a.name}" for a in node.names]
                elif node.module.startswith("prax."):
                    names = [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names if a.name.startswith("prax.")]
            for name in names:
                if not name.startswith(STORE_MAY_STAND_ON) and not name.startswith(
                    "prax.store"
                ):
                    reaching.append(f"{path.relative_to(SRC)}:{node.lineno} {name}")
    assert not reaching, "the store imports upward at the top: " + "; ".join(reaching)


def test_an_import_inside_a_store_function_is_a_call_up() -> None:
    """The other half: an import placed inside a function is for reaching
    up (a parser, the graph's passes, the wall). One that reaches down, to
    what the top may import anyway, only hides the module's dependencies
    (36 of the 60 on 2026-09-30, one module importing its sibling twelve
    times)."""
    hiding: list[str] = []
    for path in sorted((SRC / "store").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level:
                    names = [f"prax.store.{node.module or ''}"]
                elif node.module == "prax":
                    names = [f"prax.{a.name}" for a in node.names]
                else:
                    names = [f"{node.module}.{a.name}" for a in node.names]
                for name in names:
                    if name.startswith((*STORE_MAY_STAND_ON, "prax.store")):
                        hiding.append(f"{path.relative_to(SRC)}:{node.lineno} {name}")
    assert not hiding, "an import below the store, inside a function: " + "; ".join(
        hiding
    )


def test_the_text_package_stands_on_nothing_of_prax() -> None:
    """``prax.text`` holds the shapes of text: markup, chunks, what a region
    of a page is. The engineering pass of 2026-09-28 gathered them because
    they import nothing of prax at the top of a module, which is what lets
    the store, the parsers and a client all read them. A module that needs
    the store or the models belongs elsewhere; a function may still reach
    for one lazily (compounds asks the store which forms a word takes)."""
    reaching: list[str] = []
    for path in sorted((SRC / "text").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module == "prax" or (
                    module.startswith("prax.") and not module.startswith("prax.text")
                ):
                    reaching.append(f"{path.name}:{node.lineno} {module}")
    assert not reaching, "prax.text imports prax at the top: " + "; ".join(reaching)


# every table a migration creates: derived, so a new one is covered the day
# it is made (a hand-kept list had missed three by 2026-09-30)
PRAX_TABLES = "|".join(
    sorted(
        {
            m.group(1)
            for sql in (SRC / "migrations").glob("*.sql")
            for m in re.finditer(
                r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
                sql.read_text(encoding="utf-8"),
                re.IGNORECASE,
            )
        }
    )
)


def test_the_table_list_is_read_from_the_migrations() -> None:
    names = PRAX_TABLES.split("|")
    assert {"documents", "edges", "tokens", "communities"} <= set(names)


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
        [git, "ls-files", "-s", "--", "*.py", "*.sh"],  # a shell script runs as one too
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


def test_no_source_file_holds_a_control_character() -> None:
    """A backslash lost in a shell edit writes \f, \t or \b into the code
    (a form feed for \frac, a backspace for \b) and the file still parses;
    it happened four times on 2026-10-01/02. Tab, newline and carriage
    return are the only ones a source file may hold."""
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("no git")
    root = Path(__file__).resolve().parents[1]
    listed = subprocess.run(
        [git, "ls-files", "--", "*.py", "*.js", "*.yaml", "*.md", "*.css"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        pytest.skip("not a git checkout")
    allowed = {9, 10, 13}
    wrong = []
    for path in listed.stdout.splitlines():
        f = root / path
        if "/vendor/" in f"/{path}":
            continue  # third-party code, as its authors ship it
        if f.is_file() and any(c < 32 and c not in allowed for c in f.read_bytes()):
            wrong.append(path)
    assert wrong == []
