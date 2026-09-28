"""Plain text, and telling code from prose in it: a source file becomes
one fenced block, code regions inside prose are fenced, and
``code_language`` names the language.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import re
from typing import Any


def _plain(data: bytes, *, filename: str | None = None) -> str:
    text = data.decode("utf-8", errors="replace")
    if "```" in text:
        return text  # already fenced (Markdown)
    lang = code_language(text, filename)
    if lang is not None:
        return f"```{lang}\n{text.strip()}\n```"
    return fence_code_regions(text)


# ---------------------------------------------------- mixed prose and code
# A forum thread pasted into a note, a chat log with a function in the
# middle: the file as a whole is prose, so Magika says "text" and the
# chunker windows straight through the code. Lines are scored for code
# signals, runs of code lines become fenced blocks (one chunk each), and the
# prose around them stays prose.

_CODE_LINE_START = re.compile(
    r"^\s*(//|/\*|\*/|\*\s|#include|#define|#pragma|#!|import\s|from\s+\S+\s+import"
    r"|def\s|class\s|return\b|if\s*\(|else\b|for\s*\(|while\s*\(|do\b|switch\s*\("
    r"|case\b|inline\b|static\b|const\b|void\b|int\b|double\b|float\b|bool\b|char\b"
    r"|auto\b|var\b|let\b|function\b|end\b|endfunction\b|elif\b|try:|except\b"
    r"|\}|\{|\)|\]|[A-Za-z_][\w:.]*\s*=\s*[^=]|[A-Za-z_][\w:.]*\s*\+=|self\.|this->)"
)
_CODE_LINE_END = re.compile(r"[;{}]\s*$|\)\s*$|\]\s*$|,\s*$")
_CODE_OPERATORS = re.compile(
    r"==|!=|<=|>=|&&|\|\||->|::|\+\+|--|\+=|-=|\*=|/=|<<|>>|\(\)"
)
_PROSE_END = re.compile(r"[.!?:]\s*$")
_WORD = re.compile(r"[A-Za-z]{2,}")
CODE_MIN_LINES = 4  # shorter regions stay prose: an inline snippet


def code_line_score(line: str) -> float:
    """0 for prose, 1 for code, from cheap surface signals; blank lines are
    neutral (0.5) so they neither start nor end a region."""
    s = line.strip()
    if not s:
        return 0.5
    score = 0.0
    if _CODE_LINE_START.match(line):
        score += 0.5
    if _CODE_LINE_END.search(s):
        score += 0.3
    if _CODE_OPERATORS.search(s):
        score += 0.3
    if line.startswith(("    ", "\t")):
        score += 0.15
    words = _WORD.findall(s)
    symbols = sum(1 for ch in s if ch in "{}()[];=<>+*/&|^%!#")
    if symbols >= 3:
        score += 0.2
    if len(words) >= 8 and symbols <= 1 and _PROSE_END.search(s):
        score -= 0.6
    elif len(words) >= 12 and symbols <= 2:
        score -= 0.3
    if (
        " " in s
        and len(words) >= 6
        and not _CODE_LINE_START.match(line)
        and symbols == 0
    ):
        score -= 0.3
    return max(0.0, min(1.0, score))


def code_regions(lines: list[str], *, threshold: float = 0.5) -> list[tuple[int, int]]:
    """(start, end) line ranges that look like code: runs of scoring lines,
    allowing single prose-looking lines inside a run (a comment written as
    a sentence), trimmed to code lines at both ends, at least
    ``CODE_MIN_LINES`` long."""
    scores = [code_line_score(ln) for ln in lines]
    # everything inside a /* ... */ block comment is code, whatever it says
    inside = False
    for k, ln in enumerate(lines):
        stripped = ln.strip()
        if inside or stripped.startswith("/*"):
            scores[k] = 1.0
            inside = not ("*/" in stripped and not stripped.endswith("/*"))
        if not inside and stripped.startswith("/*") and "*/" not in stripped:
            inside = True
    regions: list[tuple[int, int]] = []
    i = 0
    n = len(lines)
    while i < n:
        if scores[i] < threshold or not lines[i].strip():
            i += 1
            continue
        j = i
        miss = 0
        last_code = i
        while j < n:
            sc = scores[j]
            if not lines[j].strip():
                j += 1
                continue
            if sc >= threshold:
                last_code = j
                miss = 0
            else:
                miss += 1
                if miss > 1:
                    break
            j += 1
        start, end = i, last_code + 1
        code_lines = sum(1 for k in range(start, end) if lines[k].strip())
        if code_lines >= CODE_MIN_LINES:
            regions.append((start, end))
        i = max(end, i + 1)
    return regions


def fence_code_regions(text: str) -> str:
    """Wrap code regions of a mixed prose/code text in Markdown fences, with
    the language Magika gives the region when it is confident."""
    lines = text.split("\n")
    regions = code_regions(lines)
    if not regions:
        return text
    out: list[str] = []
    pos = 0
    for start, end in regions:
        out.extend(lines[pos:start])
        block = "\n".join(lines[start:end])
        lang = code_language(block, None) or ""
        out.append(f"```{lang}")
        out.append(block)
        out.append("```")
        pos = end
    out.extend(lines[pos:])
    return "\n".join(out)


# Source files a Zotero attachment or a drop folder may hold, by extension.
CODE_EXTENSIONS = {
    "py": "python", "c": "c", "h": "c", "cpp": "cpp", "cc": "cpp", "hpp": "cpp",
    "js": "javascript", "ts": "typescript", "java": "java", "m": "matlab",
    "scd": "supercollider", "sc": "supercollider", "lua": "lua", "rs": "rust",
    "go": "go", "jl": "julia", "r": "r", "sh": "bash", "ps1": "powershell",
    "pd": "puredata", "dsp": "faust", "lib": "faust", "cs": "csharp",
    "swift": "swift", "kt": "kotlin", "sql": "sql", "json": "json", "yaml": "yaml",
    "yml": "yaml", "toml": "toml", "css": "css", "max": "max",
}  # fmt: skip
MAGIKA_MIN_SCORE = 0.75
MAGIKA_SAMPLE = 64_000  # bytes; the classifier looks at the head and tail
# Content-based detection accepts programming languages only: a bibliographic
# note full of "Key: value" lines scores as YAML, and data formats are prose
# to the reader anyway. An extension still wins for these.
MAGIKA_DATA_LABELS = frozenset(
    {"yaml", "json", "toml", "xml", "ini", "csv", "tsv", "markdown", "txt", "html"}
)


def code_language(text: str, filename: str | None = None) -> str | None:
    """The language when ``text`` is source code, else None. The filename's
    extension decides when it is telling; otherwise Magika (Google's small
    content-type model, optional) classifies the bytes and its language
    label is used when it is confident the content is code."""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[1].lower()
        if ext in CODE_EXTENSIONS:
            return CODE_EXTENSIONS[ext]
        if ext in ("txt", "md", "markdown", "rst", "html", "htm", "csv", "tsv"):
            return None
    if not text.strip():
        return None
    try:
        magika = importlib.import_module("magika")
    except ImportError:
        return None
    result = _magika(magika).identify_bytes(text[:MAGIKA_SAMPLE].encode("utf-8"))
    out = result.output
    if (
        out.group == "code"
        and result.score >= MAGIKA_MIN_SCORE
        and out.label not in MAGIKA_DATA_LABELS
    ):
        return str(out.label)
    return None


_MAGIKA: Any = None


def _magika(module: Any) -> Any:
    global _MAGIKA
    if _MAGIKA is None:
        _MAGIKA = module.Magika()
    return _MAGIKA
