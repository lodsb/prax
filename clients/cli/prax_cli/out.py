"""How the prax command talks: colour when the terminal takes it, plain
text when it is piped, numbers and sizes a person reads without counting
digits. Nothing here knows about prax; it is the terminal half."""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from datetime import UTC, datetime
from typing import Any


def _colour_ready() -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if sys.stdout is None or not sys.stdout.isatty():  # pythonw has no stdout
        return False
    if sys.platform == "win32":  # ask the console for ANSI, old consoles refuse
        try:
            import ctypes

            kernel32: Any = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:  # noqa: BLE001 - no colour is not an error
            return False
    return True


COLOUR = _colour_ready()
# A console that cannot encode them turns the nice separators into question
# marks or mojibake, so every line goes through _plain() on the way out.
_ENCODING = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
UNICODE = _ENCODING in ("utf8", "cp65001")
_PLAIN = {"·": "|", "→": "->", "…": "...", "—": "--"}


def _plain(text: str) -> str:
    if UNICODE:
        return text
    for fancy, flat in _PLAIN.items():
        text = text.replace(fancy, flat)
    return text


def emit(text: str = "", stream: Any = None) -> None:
    # flushed: `prax work --watch` and `prax serve` are usually redirected
    # into a log a person reads while they run
    print(_plain(text), file=stream or sys.stdout, flush=True)


_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}


def paint(text: str, *styles: str) -> str:
    if not COLOUR or not styles:
        return text
    codes = ";".join(_CODES[s] for s in styles if s in _CODES)
    return f"\033[{codes}m{text}\033[0m" if codes else text


def bold(t: str) -> str:
    return paint(t, "bold")


def dim(t: str) -> str:
    return paint(t, "dim")


def width(default: int = 80) -> int:
    return min(shutil.get_terminal_size((default, 24)).columns, 110)


def say(text: str = "") -> None:
    emit(text)


def warn(text: str) -> None:
    emit(paint("! ", "yellow") + text, sys.stderr)


def fail(text: str, hint: str = "") -> None:
    emit(paint("prax: ", "red") + text, sys.stderr)
    if hint:
        emit(dim("  " + hint), sys.stderr)


def num(n: Any) -> str:
    """12345 -> '12,345'; anything that is not a number comes back as text."""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def size(n: float | None) -> str:
    if not n:
        return "0 B"
    step = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{step:.0f} {unit}" if unit == "B" else f"{step:.1f} {unit}"
        step /= 1024
    return f"{step:.1f} TB"


def counts(
    pairs: dict[str, Any],
    sep: str = " · ",
    limit: int = 0,
    names: dict[str, tuple[str, str]] | None = None,
) -> str:
    """{'pdf': 9221, 'web': 267} -> '9,221 PDFs · 267 web pages'. ``names``
    gives a key its singular and plural words."""
    items = list(pairs.items())
    if limit:
        items = items[:limit]
    out = []
    for key, n in items:
        one, many = (names or {}).get(key, (key, key))
        out.append(f"{num(n)} {one if n == 1 else many}")
    return sep.join(out)


def plural(n: Any, one: str, many: str = "") -> str:
    """1 -> '1 page', 4 -> '4 pages'."""
    try:
        count = int(n)
    except (TypeError, ValueError):
        count = 0
    return f"{num(n)} {one if count == 1 else (many or one + 's')}"


def when(stamp: str | None, fmt: str = "%H:%M") -> str:
    """A UTC timestamp from the store, in this machine's time."""
    if not stamp:
        return ""
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp[:16]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone().strftime(fmt)


def field(label: str, text: str, label_width: int = 12) -> None:
    """A label in the margin and text that wraps under itself."""
    pad = " " * label_width
    body = textwrap.wrap(text, max(30, width() - label_width)) or [""]
    emit(f"{bold(label.ljust(label_width))}{body[0]}")
    for line in body[1:]:
        emit(pad + line)


def lines(label: str, rows: list[str], label_width: int = 12) -> None:
    pad = " " * label_width
    for i, row in enumerate(rows):
        emit((bold(label.ljust(label_width)) if i == 0 else pad) + row)


def table(rows: list[list[str]], headers: list[str] | None = None) -> None:
    """Columns aligned to their widest cell; the last column may run on."""
    if not rows:
        return
    body = ([headers] if headers else []) + rows
    n = max(len(r) for r in body)
    widths = [max(len(str(r[i])) for r in body if len(r) > i) for i in range(n)]

    def render(cells: list[str], style: bool = False) -> str:
        out = []
        for i, cell in enumerate(cells):
            text = str(cell)
            out.append(text.ljust(widths[i]) if i < len(cells) - 1 else text)
        line = "  ".join(out).rstrip()
        return bold(line) if style else line

    if headers:
        emit(render(headers, style=True))
    for row in rows:
        emit(render(row))


def snippet(text: str, indent: str = "   ") -> None:
    """A search snippet: the [marked] terms stand out, the rest is quiet."""
    flat = " ".join(text.split())
    for line in textwrap.wrap(flat, max(30, width() - len(indent))):
        if COLOUR:
            out, rest = "", line
            while "[" in rest and "]" in rest[rest.index("[") :]:
                before, _, after = rest.partition("[")
                term, _, rest = after.partition("]")
                out += dim(before) + paint(term, "cyan")
            line = out + dim(rest)
        emit(indent + line)


def hint(text: str) -> None:
    emit(dim(text))
