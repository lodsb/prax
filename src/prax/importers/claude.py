"""Claude Code sessions, one document each: what was said, not what was run.

A session with an agent is a conversation, and the record of it is the
raw side of what ``/prax:remember`` distils: later, "what did we say
about the filter's tuning" is a search over these, "what did we decide"
a read of the project's page. Claude Code keeps every session as a
JSON-lines transcript under ``~/.claude/projects/<project>/``; this
reader keeps the person's turns and the assistant's prose, and drops
tool calls, tool results, thinking, sub-agent side chains, injected
notifications and the summaries a compaction writes — the noise that
would swamp a library built on what was read and decided.

Keyed ``claude/<project>/<session id>`` and versioned by the transcript's
length, so a session still running is refreshed by the next run; titled
by the session's own title; tagged ``claude`` and ``project:<name>``.
Transcripts can carry what was pasted into them; ``--dry-run`` lists the
sessions before anything is sent, and the plugin's hook archives only a
project whose ``.prax-project`` asks for it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .chats import Conversation, Message, links, render_conversation
from .feed import Item

SOURCE = "claude"
APP = "claude-code"
ME = "me"
ASSISTANT = "claude"
_INJECTED = (
    "<task-notification",
    "<local-command",
    "<command-name",
    "<system-reminder",
)
_CONTINUED = "This session is being continued"
_MANGLE = re.compile(r"[^A-Za-z0-9]")


def _last_part(cwd: str) -> str:
    r"""The directory's name from a transcript's ``cwd`` — written by the
    machine the session ran on, so a Windows path is read as one wherever
    this runs (``I:\work\gadget`` is ``gadget`` on Linux too)."""
    windows = "\\" in cwd or re.match(r"^[A-Za-z]:", cwd) is not None
    return (PureWindowsPath(cwd) if windows else PurePosixPath(cwd)).name


def projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def transcripts_for(project: Path) -> Path:
    """Where Claude Code keeps a project's sessions: the project's path
    with every character that is not a letter or digit turned into a
    hyphen (``I:\\proj\\prax`` → ``I--proj-prax``)."""
    return projects_dir() / _MANGLE.sub("-", str(project.resolve()))


def memory_dir(project: Path) -> Path:
    return transcripts_for(project) / "memory"


@dataclass
class Session:
    id: str
    path: Path
    project: str  # the project's directory name
    cwd: str
    title: str | None
    branch: str | None
    lines: int
    messages: list[Message]
    started: datetime | None = None
    ended: datetime | None = None
    tools: int = 0
    dropped: dict[str, int] = field(default_factory=dict)


def read(path: Path) -> Session:
    """One transcript file → the session, its prose only."""
    messages: list[Message] = []
    dropped: dict[str, int] = {}
    title = None
    cwd = ""
    branch = None
    session_id = path.stem
    lines = 0
    tools = 0
    last_request: str | None = None

    def drop(why: str) -> None:
        dropped[why] = dropped.get(why, 0) + 1

    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            lines += 1
            try:
                o = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = o.get("type")
            if kind == "ai-title" and o.get("aiTitle"):
                title = str(o["aiTitle"]).strip() or title
                continue
            if kind not in ("user", "assistant"):
                continue
            session_id = o.get("sessionId") or session_id
            cwd = o.get("cwd") or cwd
            branch = o.get("gitBranch") or branch
            if o.get("isSidechain"):
                drop("side chain")
                continue
            at = _when(o.get("timestamp"))
            if at is None:
                continue
            content = (o.get("message") or {}).get("content")
            if kind == "user":
                if o.get("isMeta") or o.get("isCompactSummary"):
                    drop("meta")
                    continue
                text = _user_text(content)
                if text is None:
                    drop("tool result")
                    continue
                if text.startswith(_INJECTED) or text.startswith(_CONTINUED):
                    drop("injected")
                    continue
                messages.append(Message(at, ME, text))
                last_request = None
                continue
            text, used = _assistant_text(content)
            tools += used
            if not text:
                continue
            request = o.get("requestId")
            if (
                messages
                and messages[-1].who == ASSISTANT
                and request
                and request == last_request
            ):
                messages[-1].text += "\n\n" + text
            else:
                messages.append(Message(at, ASSISTANT, text))
            last_request = request
    project = _last_part(cwd) if cwd else path.parent.name
    return Session(
        id=session_id,
        path=path,
        project=project,
        cwd=cwd,
        title=title,
        branch=branch,
        lines=lines,
        messages=messages,
        started=messages[0].at if messages else None,
        ended=messages[-1].at if messages else None,
        tools=tools,
        dropped=dropped,
    )


def _when(stamp: Any) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(str(stamp)).replace(tzinfo=None)
    except ValueError:
        return None


def _user_text(content: Any) -> str | None:
    """The person's words, or None when the line is only tool results."""
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = [
            str(b.get("text") or "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        text = "\n".join(p for p in parts if p.strip()).strip()
        return text or None
    return None


def _assistant_text(content: Any) -> tuple[str, int]:
    """The assistant's prose and how many tools it called on the line."""
    if isinstance(content, str):
        return content.strip(), 0
    if not isinstance(content, list):
        return "", 0
    parts = []
    tools = 0
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            parts.append(str(b.get("text") or ""))
        elif b.get("type") == "tool_use":
            tools += 1
    return "\n".join(p for p in parts if p.strip()).strip(), tools


def sessions(paths: list[Path], *, since: datetime | None = None) -> Iterator[Session]:
    """Transcripts from files, project directories (their sessions) or
    directories of transcripts, newest first; ``since`` keeps sessions
    that ended after it."""
    files: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix == ".jsonl":
            files.append(p)
        elif p.is_dir() and any(p.glob("*.jsonl")):
            files += sorted(p.glob("*.jsonl"))
        elif p.is_dir():
            files += sorted(transcripts_for(p).glob("*.jsonl"))
    seen: set[Path] = set()
    for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
        if f in seen:
            continue
        seen.add(f)
        s = read(f)
        if not s.messages:
            continue
        if since and s.ended and s.ended < since:
            continue
        yield s


def items(found: Iterator[Session] | list[Session]) -> Iterator[Item]:
    for s in found:
        conv = Conversation(APP, s.title or s.project, s.messages, me=ME)
        day = s.started.strftime("%Y-%m-%d") if s.started else "undated"
        span = day
        if s.ended and s.started and s.ended.date() != s.started.date():
            span = f"{day} to {s.ended.strftime('%Y-%m-%d')}"
        first = next((m.text for m in s.messages if m.who == ME), "")
        head = s.title or " ".join(first.split())[:80] or s.id[:8]
        text = render_conversation(
            conv,
            s.messages,
            heading=f"# {head} — Claude Code session in {s.project}, {span}",
            preface=(
                f"_session {s.id} · {s.cwd}"
                + (f" · branch {s.branch}" if s.branch else "")
                + f" · {len(s.messages)} turns · {s.tools} tool calls left out_"
            ),
            style="turns",
        )
        yield Item(
            key=f"{s.project}/{s.id}",
            title=f"{head} ({s.project}, {day})",
            text=text,
            tags=["claude", f"project:{s.project}"],
            meta={
                "session": s.id,
                "project": s.project,
                "cwd": s.cwd,
                "branch": s.branch,
                "started": s.started.isoformat(timespec="minutes")
                if s.started
                else None,
                "ended": s.ended.isoformat(timespec="minutes") if s.ended else None,
                "turns": len(s.messages),
                "tools": s.tools,
                "links": len(links(s.messages)),
                "transcript": str(s.path),
            },
            version=str(s.lines),
        )
