"""Refuse a commit that would put the owner's private data into git.

Run by the pre-commit hook (``python scripts/check_private.py --install``
writes it into ``.git/hooks``). It reads, at commit time and read-only,
the host's ``prax.yaml`` (``private.paths``) and the text of every
document marked ``personal`` or ``suspected``, and collects what in them
identifies a person: IBANs, email addresses, phone numbers, street
addresses, postcodes with a town. A staged line that contains one of
them stops the commit. The report names the file, the line and the kind,
never the value, so the refusal itself leaks nothing.

The owner's name is not checked: an author's name in attribution is
theirs to publish (the user, 2026-10-01). Nothing private lives in this
file; it holds only patterns. Without a data directory it says so and
lets the commit through: there is nothing to compare against.

    python scripts/check_private.py            # check what is staged
    python scripts/check_private.py --all      # check every tracked file
    python scripts/check_private.py --install  # write the pre-commit hook
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

KINDS = {
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?:\+49|0049|\b0)[\d /-]{8,16}\d"),
    "street": re.compile(
        r"\b[A-ZÄÖÜ][\wäöüß.-]+(?:straße|strasse|str\.|weg|platz|allee|gasse|ring)"
        r"\s+\d{1,4}[a-z]?\b",
        re.IGNORECASE,
    ),
    "postcode and town": re.compile(
        r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]{2,}(?:\s[A-ZÄÖÜ][a-zäöüß]+)?\b"
    ),
}
MIN_CHARS = 8  # shorter matches are too common to mean a person
# files allowed to hold a value of the owner's, by path, one a line: the
# open-access paper of the Zotero fixture prints its author's address,
# which is attribution (the user, 2026-10-01). It holds paths, not values
ALLOW = Path(__file__).with_name("check_private.allow")


def _allowed() -> set[str]:
    if not ALLOW.exists():
        return set()
    return {
        line.strip()
        for line in ALLOW.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def _plain(kind: str, value: str) -> str | None:
    """The value as it is compared, or None when it is not worth one."""
    value = " ".join(value.split())
    if kind == "iban":
        value = value.replace(" ", "")
        if not (15 <= len(value) <= 34 and sum(c.isdigit() for c in value) >= 12):
            return None
    return value if len(value) >= MIN_CHARS else None


def private_values(data: Path) -> dict[str, set[str]]:
    """What identifies the owner, by kind: the private paths of prax.yaml
    and what the personal documents' texts hold."""
    out: dict[str, set[str]] = {k: set() for k in KINDS}
    cfg = yaml.safe_load((data / "prax.yaml").read_text(encoding="utf-8")) or {}
    out["private path"] = {
        str(p).strip("/\\")
        for p in (cfg.get("private") or {}).get("paths") or []
        if len(str(p).strip("/\\")) >= 4
    }
    db = data / "prax.db"
    if not db.exists():
        return out
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT text_hash FROM documents WHERE sensitivity IN ('personal', 'suspected')"
        " AND text_hash IS NOT NULL"
    ).fetchall()
    con.close()
    for (h,) in rows:
        path = data / "archive" / h[:2] / h
        try:
            text = path.read_bytes().decode("utf-8", "ignore")
        except OSError:
            continue
        for kind, pattern in KINDS.items():
            for m in pattern.findall(text):
                value = _plain(kind, m)
                if value:
                    out[kind].add(value)
    return out


def _staged_lines() -> list[tuple[str, int, str]]:
    """Each added line of the staged diff: file, line number, text."""
    diff = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--no-color"],  # binaries: no lines
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "ignore")
    out: list[tuple[str, int, str]] = []
    name, line = "", 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            name = row[6:] if row.startswith("+++ b/") else row[4:]
        elif row.startswith("@@"):
            m = re.search(r"\+(\d+)", row)
            line = int(m.group(1)) if m else 0
        elif row.startswith("+"):
            out.append((name, line, row[1:]))
            line += 1
    return out


def _tracked_lines() -> list[tuple[str, int, str]]:
    names = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    out: list[tuple[str, int, str]] = []
    for name in names:
        try:
            raw = Path(name).read_bytes()
        except OSError:
            continue
        if b"\x00" in raw:  # a binary (a PDF, an image): its digit runs are not numbers
            continue
        text = raw.decode("utf-8", "ignore")
        out += [(name, i, t) for i, t in enumerate(text.splitlines(), 1)]
    return out


def findings(
    lines: list[tuple[str, int, str]], values: dict[str, set[str]]
) -> list[tuple[str, int, str]]:
    """Each line that holds a private value: file, line, kind."""
    found = []
    allowed = _allowed()
    for name, number, text in lines:
        if name in allowed:
            continue
        flat = text.replace(" ", "")
        lowered = text.lower()
        for kind, vals in values.items():
            for v in vals:
                hit = (
                    v in flat
                    if kind == "iban"
                    else v.lower() in lowered
                    if kind == "private path"
                    else v in text
                )
                if hit:
                    found.append((name, number, kind))
                    break
    return found


HOOK = """#!/bin/sh
# prax: refuse private data in a commit (scripts/check_private.py)
exec "{python}" scripts/check_private.py
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--all", action="store_true", help="every tracked file")
    ap.add_argument("--install", action="store_true", help="write the hook")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if a.install:
        hooks = Path(
            subprocess.run(
                ["git", "rev-parse", "--git-path", "hooks"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        hook = hooks / "pre-commit"
        hook.write_text(
            HOOK.format(python=Path(sys.executable).as_posix()),
            encoding="utf-8",
            newline="\n",
        )
        hook.chmod(0o755)
        print(f"wrote {hook}")
        return 0
    data = Path(os.environ.get("PRAX_DATA_DIR") or "C:/prax-data")
    if not (data / "prax.yaml").exists():
        print(f"check_private: no prax.yaml in {data}; nothing to compare against")
        return 0
    values = private_values(data)
    found = findings(_tracked_lines() if a.all else _staged_lines(), values)
    for name, number, kind in found:
        print(f"{name}:{number}: {kind} of the owner's (the value is not shown)")
    if found:
        print(
            f"check_private: {len(found)} line(s) hold private data; commit refused."
            " Replace the value with an invented one."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
