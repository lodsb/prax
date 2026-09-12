"""Chat exports, one document per conversation and month.

A chat is where links get sent, ideas get typed out and a friend explains
something once; "note to self" is a notebook. The importer reads what the
apps export — never their databases (invariant 10, and Signal Desktop's
is encrypted for a reason):

* **Telegram Desktop**: Settings → Advanced → Export Telegram data, JSON;
  ``result.json`` of one chat or of everything (``chats.list``).
* **Signal**: `sigtop <https://github.com/tbvdm/sigtop>`_ reads Signal
  Desktop's database and writes one file per conversation with
  ``sigtop export-messages -f json DIR``; the file is named after the
  conversation.
* **WhatsApp**: a chat's "Export chat" ``.txt`` (``dd/mm/yyyy, hh:mm -
  Name: text`` and the bracketed iOS variant; day-first unless the
  dates say otherwise).

Each month of a conversation becomes one Markdown document — a heading
per day, a line per message, attachments named in brackets — titled
``<conversation> · March 2024 (Telegram)``, keyed
``telegram/<conversation>/2024-03``, with the message count as the
version so ``--refresh`` re-reads a month that grew. The links people
sent are listed at the end of the month; ``links=True`` makes them
captures of their own (``by = import:chat``, the message as the note).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .feed import Item

SOURCE = "chat"
_URL = re.compile(r"https?://[^\s<>()\[\]\"']+")
_WHATSAPP = re.compile(
    r"^\[?(?P<d1>\d{1,2})[./](?P<d2>\d{1,2})[./](?P<y>\d{2,4}),?\s+"
    r"(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ampm>[AaPp][Mm])?\]?"
    r"\s*[-–]?\s*(?P<who>[^:]{1,80}?):\s(?P<text>.*)$"
)


@dataclass
class Message:
    at: datetime
    who: str
    text: str
    attachments: list[str] = field(default_factory=list)
    quote: str | None = None


@dataclass
class Conversation:
    app: str  # telegram | signal | whatsapp
    name: str
    messages: list[Message]
    me: str = "me"


# ------------------------------------------------------------------ readers


def read(path: Path) -> list[Conversation]:
    """The conversations in an export file, whichever app wrote it."""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and ("messages" in data or "chats" in data):
            return read_telegram(data)
        if isinstance(data, list):
            return [read_sigtop(data, name=path.stem)]
        raise ValueError(f"{path.name}: not a Telegram or sigtop JSON export")
    if path.suffix.lower() in (".txt", ".text"):
        return [read_whatsapp(path.read_text(encoding="utf-8-sig"), name=path.stem)]
    raise ValueError(
        f"{path.name}: expected a .json (Telegram, sigtop) or .txt (WhatsApp)"
    )


def read_telegram(data: dict[str, Any]) -> list[Conversation]:
    chats = data["chats"]["list"] if "chats" in data else [data]
    out = []
    for chat in chats:
        if chat.get("type") == "saved_messages":
            name = chat.get("name") or "Saved Messages"
        else:
            name = chat.get("name") or f"chat {chat.get('id', '?')}"
        msgs = []
        for m in chat.get("messages") or []:
            if m.get("type") != "message":
                continue
            text = _telegram_text(m.get("text"))
            attachments = [
                str(m[k]).rsplit("/", 1)[-1]
                for k in ("photo", "file", "media_type")
                if m.get(k) and k != "media_type"
            ]
            if m.get("media_type") and not attachments:
                attachments.append(m["media_type"])
            if not text and not attachments:
                continue
            when = m.get("date") or ""
            try:
                at = datetime.fromisoformat(when)
            except ValueError:
                continue
            msgs.append(
                Message(
                    at,
                    m.get("from") or m.get("actor") or "?",
                    text,
                    attachments,
                    _telegram_text(m.get("reply_to_message_text")) or None,
                )
            )
        out.append(Conversation("telegram", str(name), msgs))
    return out


def _telegram_text(text: Any) -> str:
    """Telegram's text is a string or a list of strings and entity dicts."""
    if not text:
        return ""
    if isinstance(text, str):
        return text
    parts = []
    for piece in text:
        if isinstance(piece, str):
            parts.append(piece)
        elif isinstance(piece, dict):
            parts.append(piece.get("text") or "")
    return "".join(parts)


def read_sigtop(rows: list[dict[str, Any]], *, name: str) -> Conversation:
    """sigtop's JSON: Signal Desktop's own message records, one list per
    conversation; ``type`` says who spoke, ``sent_at`` is in milliseconds."""
    msgs = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        text = str(m.get("body") or m.get("text") or "").strip()
        attachments = [
            a.get("fileName") or a.get("contentType") or "attachment"
            for a in m.get("attachments") or []
            if isinstance(a, dict)
        ]
        if not text and not attachments:
            continue
        stamp = m.get("sent_at") or m.get("timestamp") or m.get("received_at")
        at = _when(stamp)
        if at is None:
            continue
        kind = m.get("type") or ""
        who = "me" if kind == "outgoing" else (m.get("source") or name)
        quote = None
        if isinstance(m.get("quote"), dict):
            quote = str(m["quote"].get("text") or "") or None
        msgs.append(Message(at, str(who), text, attachments, quote))
    return Conversation("signal", name, msgs)


def _when(stamp: Any) -> datetime | None:
    """Wall-clock time, naive: what the export says, the way the chat
    showed it (a Signal stamp is milliseconds since the epoch, in UTC)."""
    if stamp is None:
        return None
    if isinstance(stamp, int | float):
        seconds = stamp / 1000 if stamp > 1e11 else stamp
        return datetime.fromtimestamp(seconds, tz=UTC).replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(stamp)).replace(tzinfo=None)
    except ValueError:
        return None


def read_whatsapp(text: str, *, name: str) -> Conversation:
    lines = text.splitlines()
    matches = [(_WHATSAPP.match(line), line) for line in lines]
    # day-first unless some first field is a month at most and some second
    # field cannot be one
    firsts = [int(m.group("d1")) for m, _ in matches if m]
    seconds = [int(m.group("d2")) for m, _ in matches if m]
    day_first = not (any(s > 12 for s in seconds) and all(f <= 12 for f in firsts))
    msgs: list[Message] = []
    for m, line in matches:
        if m is None:
            if msgs and line.strip():  # a continuation line of the last message
                msgs[-1].text += "\n" + line.rstrip()
            continue
        d1, d2, y = int(m.group("d1")), int(m.group("d2")), int(m.group("y"))
        day, month = (d1, d2) if day_first else (d2, d1)
        if y < 100:
            y += 2000
        hour = int(m.group("h"))
        ampm = (m.group("ampm") or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        try:  # the chat's own wall clock, naive like every stamp here
            at = datetime(  # noqa: DTZ001
                y, month, day, hour, int(m.group("m")), int(m.group("s") or 0)
            )
        except ValueError:
            continue
        body = m.group("text").strip()
        attachments = []
        att = re.match(r"^(.+?)\s*\((file attached)\)$|^<attached:\s*(.+?)>$", body)
        if att:
            attachments.append(att.group(1) or att.group(3) or "attachment")
            body = ""
        elif body in ("<Media omitted>", "image omitted", "video omitted"):
            attachments.append(body.strip("<>"))
            body = ""
        msgs.append(Message(at, m.group("who").strip(), body, attachments))
    return Conversation("whatsapp", name, msgs)


# ------------------------------------------------------------------ documents


def months(conv: Conversation) -> Iterator[tuple[str, list[Message]]]:
    by_month: dict[str, list[Message]] = {}
    for m in sorted(conv.messages, key=lambda x: x.at):
        by_month.setdefault(m.at.strftime("%Y-%m"), []).append(m)
    yield from by_month.items()


def _pretty(month: str) -> str:
    return datetime.strptime(month, "%Y-%m").strftime("%B %Y")  # noqa: DTZ007


def render(conv: Conversation, month: str, msgs: list[Message]) -> str:
    app = conv.app.capitalize()
    return render_conversation(
        conv, msgs, heading=f"# {conv.name} — {app}, {_pretty(month)}"
    )


_MD_HEADING = re.compile(r"^(#{1,6})(\s)", re.MULTILINE)


def render_conversation(
    conv: Conversation,
    msgs: list[Message],
    *,
    heading: str,
    preface: str | None = None,
    style: str = "lines",
) -> str:
    """Messages as Markdown under day headings. ``lines``: one line per
    message (chats); ``turns``: a heading per message with the text
    verbatim beneath, its own headings demoted (a session with an agent,
    whose answers are long and structured)."""
    lines = [heading, ""]
    if preface:
        lines += [preface, ""]
    day = None
    for m in msgs:
        d = m.at.strftime("%Y-%m-%d")
        if d != day:
            lines += [f"## {d}", ""]
            day = d
        if style == "turns":
            lines += [f"### {m.who} · {m.at.strftime('%H:%M')}", ""]
            if m.quote:
                lines += [f"> {' '.join(m.quote.split())[:300]}", ""]
            lines += [_MD_HEADING.sub(r"###\1\2", m.text.strip()), ""]
            continue
        head = f"**{m.who}** {m.at.strftime('%H:%M')} —"
        if m.quote:
            lines.append(f"> {' '.join(m.quote.split())[:300]}")
        body = m.text.strip()
        att = " ".join(f"[attachment: {a}]" for a in m.attachments)
        first, _, rest = body.partition("\n")
        lines.append(" ".join(x for x in (head, first, att) if x))
        for extra in rest.splitlines():
            lines.append(f"  {extra}")
        lines.append("")
    urls = links(msgs)
    if urls:
        lines += ["## Links", ""]
        lines += [f"- {u}" for u in urls]
        lines.append("")
    return "\n".join(lines)


def links(msgs: list[Message]) -> list[str]:
    seen: dict[str, None] = {}
    for m in msgs:
        for u in _URL.findall(m.text):
            seen.setdefault(u.rstrip(".,;:!?"), None)
    return list(seen)


def items(
    conversations: list[Conversation], *, with_links: bool = False
) -> Iterator[Item]:
    """A document per conversation and month; with ``with_links``, every
    link sent that month as a capture of its own, the message as its note."""
    for conv in conversations:
        for month, msgs in months(conv):
            key = f"{conv.app}/{conv.name}/{month}"
            pretty = _pretty(month)
            urls = links(msgs)
            yield Item(
                key=key,
                title=f"{conv.name} · {pretty} ({conv.app.capitalize()})",
                text=render(conv, month, msgs),
                tags=[f"chat:{conv.name}"],
                meta={
                    "app": conv.app,
                    "conversation": conv.name,
                    "month": month,
                    "messages": len(msgs),
                    "first": msgs[0].at.isoformat(timespec="minutes"),
                    "last": msgs[-1].at.isoformat(timespec="minutes"),
                    "links": len(urls),
                },
                version=f"{len(msgs)}@{msgs[-1].at.isoformat(timespec='minutes')}",
            )
            if with_links:
                for u in urls:
                    said = next((m for m in msgs if u in m.text), None)
                    note = None
                    if said is not None:
                        when = said.at.strftime("%Y-%m-%d %H:%M")
                        note = (
                            f"{said.who}, {when} in {conv.name}: "
                            + " ".join(said.text.split())[:500]
                        )
                    yield Item(
                        key=u,
                        kind="link",
                        url=u,
                        tags=[f"chat:{conv.name}"],
                        note=note,
                    )
