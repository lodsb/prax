"""Lists of links: what a reading service or a browser exports, each
link fetched by the door as a capture.

Read here, without a library:

* **Browser bookmarks**: the Netscape bookmark file every browser exports
  (``<DT><A HREF="…" ADD_DATE="…" TAGS="…">``); folder names become tags
  (``folder:Reading/DSP``).
* **CSV**: any file with a ``url`` column — Pocket's export (``title,
  url, time_added, tags, status``), Raindrop's (``title, note, excerpt,
  url, folder, tags, created``); ``tags``, ``folder``, ``note`` and
  ``excerpt`` are used when present.
* **Text**: one link per line, the rest of the line as the note.
* **Medium's export** (Settings → Security and apps → Download your
  information): a zip whose ``bookmarks/``, ``lists/`` and ``highlights/``
  are HTML lists of links; ``medium:bookmarks``, ``medium:list:<name>``
  and ``medium:highlights`` become tags and a highlight's text the note.
  Medium serves a public story to the door's fetch; a member-only one
  comes back as its preview, and the browser extension is the way to
  capture that with your own session.
* Any other HTML: every ``<a href="http…">``.

Every link is an ``Item`` without text; ``feed.run`` asks the door
whether it already holds the URL before fetching it.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from .feed import Item

SOURCE = "links"
_URL = re.compile(r"https?://[^\s<>()\[\]\"']+")
_MEDIUM_FOLDERS = ("bookmarks", "lists", "highlights")
_MEDIUM_SKIP = (
    "/me/",
    "/m/signin",
    "help.medium.com",
    "policy.medium.com",
    "medium.com/about",
)


def read(path: Path) -> Iterator[Item]:
    """The links in an export file, whichever shape it has."""
    suffix = path.suffix.lower()
    if suffix == ".zip":
        yield from read_medium_zip(path)
    elif suffix in (".html", ".htm"):
        yield from read_html(path.read_text(encoding="utf-8", errors="replace"))
    elif suffix == ".csv":
        yield from read_csv(path.read_text(encoding="utf-8-sig", errors="replace"))
    elif suffix in (".txt", ".md", ".text", ".list", ""):
        yield from read_text(path.read_text(encoding="utf-8-sig", errors="replace"))
    else:
        raise ValueError(f"{path.name}: expected .html, .csv, .txt or Medium's .zip")


# ------------------------------------------------------------------ HTML


class _Links(HTMLParser):
    """Anchors with their text; in a Netscape bookmark file also the folder
    path (``<H3>`` headings, nested ``<DL>``), the date and the tags."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[tuple[str, str, list[str], str | None, str | None]] = []
        self.folders: list[str] = []
        self._heading: list[str] | None = None
        self._anchor: dict[str, str] | None = None
        self._text: list[str] = []
        self._li: list[str] | None = None
        self._li_anchor: tuple[str, str] | None = None
        self._li_tags: list[str] = []
        self._li_when: str | None = None
        self.netscape = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "h3":
            self._heading = []
        elif tag == "a" and a.get("href", "").startswith(("http://", "https://")):
            self._anchor = a
            self._text = []
        elif tag == "li":
            self._li = []
            self._li_anchor = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._heading is not None:
            self.folders.append(" ".join("".join(self._heading).split()))
            self._heading = None
        elif tag == "dl" and self.folders and self.netscape:
            self.folders.pop()
        elif tag == "a" and self._anchor is not None:
            text = " ".join("".join(self._text).split())
            a = self._anchor
            self._anchor = None
            tags = [t.strip() for t in a.get("tags", "").split(",") if t.strip()]
            if self.folders:
                tags.append("folder:" + "/".join(self.folders))
            when = _from_epoch(a.get("add_date"))
            if self._li is not None:
                self._li_anchor = (a["href"], text)
                self._li_tags = tags
                self._li_when = when
            else:
                self.items.append((a["href"], text, tags, when, None))
        elif tag == "li" and self._li is not None:
            note = " ".join("".join(self._li).split())
            if self._li_anchor:
                href, text = self._li_anchor
                if text and note.startswith(text):
                    note = note[len(text) :].strip(" -–—·:")
                self.items.append(
                    (href, text, self._li_tags, self._li_when, note or None)
                )
            self._li = None
            self._li_anchor = None

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)
        if self._anchor is not None:
            self._text.append(data)
        if self._li is not None:
            self._li.append(data)

    def handle_decl(self, decl: str) -> None:
        if "NETSCAPE-Bookmark-file" in decl:
            self.netscape = True


def _from_epoch(value: str | None) -> str | None:
    if not value:
        return None
    try:
        seconds = int(value)
    except ValueError:
        return None
    if seconds > 1e11:  # milliseconds, or Chrome's microseconds since 1601
        seconds = seconds / 1000 if seconds < 1e14 else seconds / 1e6 - 11644473600
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%d")


def read_html(html: str, *, tags: list[str] | None = None) -> Iterator[Item]:
    parser = _Links()
    parser.feed(html)
    seen: set[str] = set()
    for href, text, own, when, note in parser.items:
        if href in seen:
            continue
        seen.add(href)
        yield Item(
            key=href,
            kind="link",
            url=href,
            title=text or None,
            tags=[*(tags or []), *own],
            note=(f"{note}" if note else None),
            meta={"added": when} if when else {},
        )


# ------------------------------------------------------------------ CSV, text


def read_csv(text: str) -> Iterator[Item]:
    reader = csv.DictReader(io.StringIO(text))
    fields = {(f or "").strip().lower(): f for f in reader.fieldnames or []}
    url_col = next((fields[k] for k in ("url", "link", "href") if k in fields), None)
    if url_col is None:
        raise ValueError("the CSV has no url column")

    def cell(row: dict[str, str], name: str) -> str:
        column = fields.get(name)
        return (row.get(column) or "").strip() if column else ""

    seen: set[str] = set()
    for row in reader:
        href = (row.get(url_col) or "").strip()
        if not href.startswith(("http://", "https://")) or href in seen:
            continue
        seen.add(href)
        tags = [
            t.strip()
            for name in ("tags", "tag")
            for t in re.split(r"[|,;]", cell(row, name))
            if t.strip()
        ]
        if cell(row, "folder"):
            tags.append("folder:" + cell(row, "folder"))
        note = " · ".join(
            cell(row, n) for n in ("note", "excerpt", "description") if cell(row, n)
        )
        when = None
        for name in ("time_added", "created", "added", "date"):
            if cell(row, name):
                when = _from_epoch(cell(row, name)) or cell(row, name)[:10]
                break
        title = cell(row, "title")
        yield Item(
            key=href,
            kind="link",
            url=href,
            title=title or None,
            tags=tags,
            note=note or None,
            meta={"added": when} if when else {},
        )


def read_text(text: str) -> Iterator[Item]:
    seen: set[str] = set()
    for line in text.splitlines():
        for m in _URL.finditer(line):
            href = m.group(0).rstrip(".,;:!?")
            if href in seen:
                continue
            seen.add(href)
            note = (line[: m.start()] + line[m.end() :]).strip(" -–—·:\t#*")
            yield Item(key=href, kind="link", url=href, note=note or None)


# ------------------------------------------------------------------ Medium


def read_medium_zip(path: Path) -> Iterator[Item]:
    seen: set[str] = set()
    with zipfile.ZipFile(path) as zf:
        for name in sorted(zf.namelist()):
            parts = name.replace("\\", "/").split("/")
            if len(parts) < 2 or not name.lower().endswith((".html", ".htm")):
                continue
            folder = parts[-2].lower() if len(parts) >= 2 else ""
            top = next((p.lower() for p in parts if p.lower() in _MEDIUM_FOLDERS), None)
            if top is None:
                continue
            tags = [f"medium:{top}"]
            if top == "lists":
                list_name = re.sub(r"[-_]?\d+$", "", Path(parts[-1]).stem) or folder
                tags = [f"medium:list:{list_name}"]
            html = zf.read(name).decode("utf-8", errors="replace")
            for item in read_html(html, tags=tags):
                url = item.url or ""
                if any(s in url for s in _MEDIUM_SKIP) or url in seen:
                    continue
                seen.add(url)
                if top == "highlights" and item.note:
                    item.note = "highlight: " + item.note
                yield item
