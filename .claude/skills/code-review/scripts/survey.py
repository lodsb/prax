#!/usr/bin/env python
"""The facts a code review starts from (the code-review skill).

    python .claude/skills/code-review/scripts/survey.py <base> [scope]

For the Python files changed in ``scope`` (default ``src``) since commit
``base``: lines added and removed; every module of the scope past 1,500
lines (CLAUDE.md's rule splits at 2,000); functions past 80 lines in the
changed files; module-level numeric constants that are new or changed
since ``base`` (tuned numbers: does something re-measure them?); and
changed lines that hold one of the owner's private names from
``prax.yaml`` (``private:``), by file and line only, never the name.

Reads the repository and git only; prints, writes nothing.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

MODULE_WARN, MODULE_LIMIT, FUNCTION_LONG = 1500, 2000, 80
SHOWN = 40  # changed files listed

Changed = list[list[str]]  # (added, removed, path) per changed file


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=False
    ).stdout


def _numeric(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Constant)
        and isinstance(n.value, int | float)
        and not isinstance(n.value, bool)
        for n in ast.walk(node)
    )


def constants(source: str) -> dict[str, str]:
    """Module-level UPPER_CASE names bound to a number, or to a tuple,
    list, set or dict holding numbers, as source text; a long multi-line
    value (a table of records) is left out."""
    out: dict[str, str] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = node.value
        if value is None or not _numeric(value):
            continue
        text = ast.get_source_segment(source, value) or ""
        if "\n" in text and len(text) > 120:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id.isupper():
                out[t.id] = text
    return out


def private_names() -> list[str]:
    """The owner's private names from prax.yaml, if this host has one."""
    try:
        sys.path.insert(0, str(Path("src").resolve()))
        from prax import config

        return [str(n) for n in config.words("private.names") if str(n).strip()]
    except Exception:  # noqa: BLE001 - a survey without the names is still a survey
        return []


def report_changed(changed: Changed, base: str, scope: str) -> None:
    print(f"== changed Python files in {scope} since {base}: {len(changed)}")
    by_size = sorted(changed, key=lambda x: -int(x[0] if x[0] != "-" else 0))
    for added, removed, path in by_size[:SHOWN]:
        print(f"  +{added:>5} -{removed:>5}  {path}")
    if len(changed) > SHOWN:
        print(f"  ... {len(changed) - SHOWN} more")


def report_modules(scope: str) -> None:
    print()
    print(f"== modules past {MODULE_WARN} lines (the rule splits at {MODULE_LIMIT})")
    for p in sorted(Path(scope).rglob("*.py")):
        n = sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
        if n > MODULE_WARN:
            past = "  PAST THE RULE" if n > MODULE_LIMIT else ""
            print(f"  {n:>5}  {p.as_posix()}{past}")


def report_functions(changed: Changed) -> None:
    print()
    print(f"== functions past {FUNCTION_LONG} lines in the changed files")
    for _, _, path in changed:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                if length > FUNCTION_LONG:
                    print(f"  {length:>4}  {path}:{node.lineno} {node.name}")


def report_constants(changed: Changed, base: str) -> None:
    print()
    print(f"== numeric constants new or changed since {base} (tuned? re-measured?)")
    for _, _, path in changed:
        if not Path(path).exists():
            continue
        now = constants(Path(path).read_text(encoding="utf-8"))
        then = constants(git("show", f"{base}:{path}"))
        for name, value in sorted(now.items()):
            if then.get(name) != value:
                was = f" (was {then[name][:40]})" if name in then else ""
                print(f"  {path}: {name} = {value[:60]}{was}")


def report_private(base: str) -> None:
    names = [n.lower() for n in private_names()]
    print()
    print(f"== changed lines holding one of the owner's {len(names)} private names")
    if not names:
        print("  (no prax.yaml with private names on this host; not checked)")
        return
    path, line_no, hits = "", 0, 0
    for line in git("diff", "-U0", base, "HEAD", "--", ".").splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@"):
            line_no = int(line.split("+")[1].split(",")[0].split(" ")[0])
        elif line.startswith("+") and not line.startswith("+++"):
            if any(n in line.lower() for n in names):
                print(f"  {path}:{line_no}")
                hits += 1
            line_no += 1
    if not hits:
        print("  none")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    base, scope = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "src")
    changed = [
        line.split("\t")
        for line in git("diff", "--numstat", base, "HEAD", "--", scope).splitlines()
        if line.endswith(".py")
    ]
    report_changed(changed, base, scope)
    report_modules(scope)
    report_functions(changed)
    report_constants(changed, base)
    report_private(base)


if __name__ == "__main__":
    main()
