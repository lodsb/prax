"""Ask blocks: a region of a person's page the door keeps answered.

Written by hand, in any page::

    <!-- prax:ask id=q1 "how do feedback delay networks stay lossless" -->
    <!-- /prax:ask id=q1 -->

The door fills what is between the two markers — the answer and its
sources — and closes the block with the interior's hash and the pass in
the tail: ``<!-- /prax:ask id=q1 sha=1f3a… asked=2026-09-21 run=… -->``.
HTML comments render as nothing in any Markdown viewer and survive every
editor; the query lives in the head, the result inside, the pass in the
tail, never in the text (rationale R17). The head may carry the options
a question takes (``steps=``, ``doctype=``, ``limit=``). A tail without
a hash (or no tail at all) is a block not yet filled: the door writes
the tail with the first fill.

Two rules keep hands safe. Everything outside the markers is the
person's and is never touched: ``fill`` replaces interiors by id and
nothing else. Inside, a block whose interior no longer matches the
tail's hash was edited by hand and is *held* — ``fill`` refuses it (the
caller records why and asks the person) — and a ``<!-- prax:keep -->``
… ``<!-- /prax:keep -->`` region inside the block is carried over
verbatim, so an annotation to an answer does not fork it.

No store, no model: this module is the grammar and the rewrite, used by
the chunker (an ``ask`` chunk per block), the page store and the
questions pass.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

HEAD = re.compile(
    r"^[ \t]*<!--\s*prax:ask\s+(?P<attrs>[^>]*?)\s*-->[ \t]*$", re.MULTILINE
)
TAIL = re.compile(
    r"^[ \t]*<!--\s*/prax:ask\s+(?P<attrs>[^>]*?)\s*-->[ \t]*$", re.MULTILINE
)
KEEP = re.compile(
    r"<!--\s*prax:keep\s*-->(?P<body>.*?)<!--\s*/prax:keep\s*-->", re.DOTALL
)
OPTIONS = ("steps", "doctype", "limit", "domain")
SHA_CHARS = 12


@dataclass
class Block:
    id: str
    question: str
    options: dict[str, Any] = field(default_factory=dict)
    head: tuple[int, int] = (0, 0)  # the head line, [start, end) with its newline
    interior: tuple[int, int] = (0, 0)  # between the markers
    tail: tuple[int, int] | None = None  # None: not yet filled, no tail line
    tail_attrs: dict[str, str] = field(default_factory=dict)

    @property
    def sha(self) -> str | None:
        return self.tail_attrs.get("sha")

    @property
    def filled(self) -> bool:
        """Has the door written the interior? A person writes both
        markers; the tail carries a hash once the door has filled it."""
        return self.tail is not None and bool(self.sha)

    @property
    def asked(self) -> str | None:
        return self.tail_attrs.get("asked")


def _attrs(text: str) -> tuple[dict[str, str], str]:
    """``id=q1 steps=4 "the question"`` -> the pairs and the quoted rest
    (the question). Quoting as a shell would."""
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    pairs: dict[str, str] = {}
    words: list[str] = []
    for p in parts:
        if "=" in p and not p.startswith("="):
            k, _, v = p.partition("=")
            if k.isidentifier():
                pairs[k] = v
                continue
        words.append(p)
    return pairs, " ".join(words).strip()


def _line_end(text: str, pos: int) -> int:
    nl = text.find("\n", pos)
    return len(text) if nl < 0 else nl + 1


def blocks(text: str) -> list[Block]:
    """The ask blocks of a page's text, in order. A head with no tail of
    its id before the next head (or the end) has an empty interior right
    after the head line, and is filled like any."""
    text = text or ""
    heads = list(HEAD.finditer(text))
    tails = {}
    for m in TAIL.finditer(text):
        attrs, _ = _attrs(m.group("attrs"))
        if attrs.get("id"):
            tails.setdefault(attrs["id"], []).append((m, attrs))
    out: list[Block] = []
    for i, m in enumerate(heads):
        attrs, question = _attrs(m.group("attrs"))
        block_id = attrs.get("id")
        if not block_id or not question:
            continue
        head_end = _line_end(text, m.end())
        limit = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        options = {k: v for k, v in attrs.items() if k in OPTIONS}
        block = Block(
            id=block_id,
            question=question,
            options=_typed(options),
            head=(m.start(), head_end),
            interior=(head_end, head_end),
        )
        for tm, tattrs in tails.get(block_id, []):
            if head_end <= tm.start() < limit:
                block.interior = (head_end, tm.start())
                block.tail = (tm.start(), _line_end(text, tm.end()))
                block.tail_attrs = tattrs
                break
        out.append(block)
    return out


def _typed(options: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in options.items():
        if k in ("steps", "limit"):
            try:
                out[k] = int(v)
            except ValueError:
                continue
        elif v:
            out[k] = v
    return out


def sha_of(interior: str) -> str:
    """What the tail records of the interior: the hash of the door's own
    text — the keep regions left out, since they are the person's by
    design, and the whitespace at either end, so an editor that adds a
    newline does not count as a hand."""
    own = KEEP.sub("", interior).strip()
    return hashlib.sha256(own.encode("utf-8")).hexdigest()[:SHA_CHARS]


def held(block: Block, text: str) -> bool:
    """Was the interior edited by hand since the door wrote it? True when
    the tail carries a hash and the interior no longer matches it; an
    unfilled block, or one the door never hashed, is not held."""
    if block.tail is None or not block.sha:
        return False
    return sha_of(text[block.interior[0] : block.interior[1]]) != block.sha


def keeps(interior: str) -> list[str]:
    """The keep regions of an interior, markers included, in order."""
    return [m.group(0) for m in KEEP.finditer(interior)]


def answer_of(interior: str) -> str:
    """The answer alone out of an interior: before the source list, the
    keep regions left out."""
    body = KEEP.sub("", interior)
    cut = body.find("\n\nSources:\n")
    if cut >= 0:
        body = body[:cut]
    return body.strip()


def fill(
    text: str,
    fills: dict[str, str],
    *,
    asked: str,
    run: str,
    release: set[str] | None = None,
) -> tuple[str, dict[str, str]]:
    """The text with the interiors of the blocks named in ``fills``
    replaced — the keep regions of the old interior carried over after
    the new one — and their tails written with the hash, the day and the
    run. Everything else is untouched. A block edited by hand (``held``)
    is left as it is and reported, unless its id is in ``release``; an
    id with no block is reported too. Returns the new text and
    ``{id: "filled" | "held" | "missing"}``."""
    text = text or ""
    report: dict[str, str] = {}
    found = {b.id: b for b in blocks(text)}
    edits: list[tuple[int, int, str]] = []
    for block_id, interior in fills.items():
        block = found.get(block_id)
        if block is None:
            report[block_id] = "missing"
            continue
        if held(block, text) and block_id not in (release or set()):
            report[block_id] = "held"
            continue
        old = text[block.interior[0] : block.interior[1]]
        kept = keeps(old)
        body = interior.strip("\n")
        if kept:
            body += "\n\n" + "\n\n".join(kept)
        new_interior = "\n" + body + "\n\n" if body else "\n"
        tail = (
            f"<!-- /prax:ask id={block_id} sha={sha_of(new_interior)}"
            f" asked={asked} run={run} -->\n"
        )
        start = block.interior[0]
        end = block.tail[1] if block.tail is not None else block.interior[1]
        edits.append((start, end, new_interior + tail))
        report[block_id] = "filled"
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, report


def head_line(block_id: str, question: str, **options: Any) -> str:
    """A block's markers as a person would write them, for a form or a
    tool that adds one to a page."""
    opts = " ".join(f"{k}={v}" for k, v in options.items() if v not in (None, ""))
    q = question.replace('"', "'")
    return (
        f'<!-- prax:ask id={block_id}{" " + opts if opts else ""} "{q}" -->\n'
        f"<!-- /prax:ask id={block_id} -->\n"
    )
