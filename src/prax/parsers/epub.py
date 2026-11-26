"""EPUB books: the XHTML of the spine in reading order, as Markdown.

An EPUB is a zip of XHTML files, a package file (``.opf``) that lists
them in reading order (the spine), and a table of contents (EPUB 3's
``nav`` document, EPUB 2's ``toc.ncx``). The text is read from the XHTML
itself, so a heading is the book's own ``h1``..``h6``. The table of
contents is the book's declared structure, so it decides the chapters:
where an entry points, the heading or the styled paragraph found there
takes the entry's level, and an entry that points at plain text puts its
title in as a heading. A heading the table does not list sits at least one
level under the entry before it. Many EPUBs mark their chapter titles
with a class and no ``h`` tag, and 4 of the 26 in one library carried no
heading tag at all: the table is what gives those their chapters
(2026-11-26).

Read with the standard library's HTML parser, which takes the XHTML of
a careful publisher and the tag soup of a converted scan alike, so
nothing is installed. Pictures are left out: an EPUB's images are members
of its zip, not objects a figure reference serves (``prax.parsers.figures``).
An EPUB of page images holds almost no text and ends ``empty``, as a scan
does.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

from .office import ZIPPED_XML_MAX, _join_blocks, _markdown_table, _zipped_xml

EPUB_MIME = "application/epub+zip"
_CONTAINER = "META-INF/container.xml"
_XHTML = ("application/xhtml+xml", "text/html")
_SKIP = {"script", "style", "head", "title", "svg", "math"}
_BLOCKS = {
    "p",
    "div",
    "li",
    "dt",
    "dd",
    "blockquote",
    "figcaption",
    "caption",
    "section",
    "article",
    "aside",
    "header",
    "footer",
    "body",
    "hr",
}
_HEADING = re.compile(r"^h([1-6])$")


@dataclass
class _Block:
    kind: str  # "h", "p", "li", "pre", "table"
    text: str
    level: int = 0  # a heading's
    toc: bool = False  # its level is the table of contents'


@dataclass
class _Entry:
    level: int
    title: str
    fragment: str | None


class _Reader(HTMLParser):
    """One XHTML file as blocks, and the block index each ``id`` stands
    before (where a table-of-contents entry points)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self.anchors: dict[str, int] = {}
        self._buf: list[str] = []
        self._kind = "p"
        self._level = 0
        self._skip = 0  # inside a script, a style, the head
        self._pre = 0
        self._table = 0  # the depth of tables open
        self._rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def _has_text(self) -> bool:
        return bool("".join(self._buf).strip())

    def _flush(self) -> None:
        raw = "".join(self._buf)
        self._buf = []
        if self._kind == "pre":
            text = raw.strip("\n")
            if text.strip():
                self.blocks.append(_Block("pre", f"```\n{text}\n```"))
            return
        text = re.sub(r"\s+", " ", raw.replace("\xad", "")).strip()
        if not text:
            return
        if self._kind == "h":
            self.blocks.append(_Block("h", text, self._level))
        elif self._kind == "li":
            self.blocks.append(_Block("li", "- " + text))
        else:
            self.blocks.append(_Block("p", text))

    def _start(self, kind: str, level: int = 0) -> None:
        self._flush()
        self._kind, self._level = kind, level

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        # an id on a block stands before it, after the text buffered so far;
        # one inside a paragraph stands before that paragraph
        opens = tag in _BLOCKS or tag in ("li", "pre", "table") or _HEADING.match(tag)
        at = len(self.blocks) + (self._has_text() if opens else 0)
        for key, value in attrs:
            if key in ("id", "name") and value:
                self.anchors.setdefault(value, at)
        if self._table:
            if tag == "table":
                self._table += 1  # a nested table reads as its cells' text
            elif tag == "tr" and self._table == 1:
                self._rows.append([])
            elif tag in ("td", "th") and self._table == 1:
                self._cell = []
                if not self._rows:
                    self._rows.append([])
            elif tag == "br":
                self._data(" ")
            return
        if tag == "table":
            self._flush()
            self._table, self._rows, self._cell = 1, [], None
        elif m := _HEADING.match(tag):
            self._start("h", int(m.group(1)))
        elif tag == "pre":
            self._start("pre")
            self._pre += 1
        elif tag == "li":
            self._start("li")
        elif tag in _BLOCKS:
            if not (self._kind == "li" and not self._has_text()):
                self._start("p")  # a paragraph that opens a list item stays it
        elif tag == "br":
            self._buf.append("\n" if self._pre else " ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in _SKIP:
            self._skip -= 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if self._table:
            if tag in ("td", "th") and self._table == 1 and self._cell is not None:
                cell = re.sub(r"\s+", " ", "".join(self._cell)).strip()
                self._rows[-1].append(cell.replace("|", r"\|"))
                self._cell = None
            elif tag == "table":
                self._table -= 1
                if not self._table:
                    self._end_table()
            return
        if tag == "pre":
            self._flush()
            self._pre = max(0, self._pre - 1)
            self._kind = "p"
        elif _HEADING.match(tag) or tag == "li" or tag in _BLOCKS:
            self._flush()
            self._kind = "p"

    def _data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._table:
            self._data(data)
            return
        self._buf.append(data)

    def _end_table(self) -> None:
        table = _markdown_table(self._rows)
        self._rows = []
        if table:
            self.blocks.append(_Block("table", table))

    def close(self) -> None:
        super().close()
        if self._table:
            self._table = 1
            self.handle_endtag("table")
        self._flush()


def _decode(raw: bytes) -> str:
    head = raw[:200].decode("ascii", "replace")
    m = re.search(r"""encoding=["']([\w.-]+)["']""", head)
    try:
        return raw.decode(m.group(1) if m else "utf-8", "replace")
    except LookupError:  # an encoding Python does not know
        return raw.decode("utf-8", "replace")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el: Any, name: str) -> list[Any]:
    """The descendants of an element by local name, whatever the namespace
    (a package's OPF and NCX namespaces vary by version and by tool)."""
    return [e for e in el.iter() if _local(e.tag) == name]


def _children(el: Any, name: str) -> list[Any]:
    return [e for e in el if _local(e.tag) == name]


def _href(base: str, href: str) -> tuple[str, str | None]:
    """A link of the package as (member, fragment), resolved against the
    directory of the file it is written in."""
    path, _, frag = unquote(href).partition("#")
    member = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
    return member, (frag or None)


@dataclass
class _Package:
    spine: list[str]
    nav: str | None  # the EPUB 3 nav document, left out of the text
    toc: dict[str, list[_Entry]]  # member -> its entries, in the table's order


def _package(archive: zipfile.ZipFile) -> _Package:
    try:
        container = ElementTree.fromstring(_zipped_xml(archive, _CONTAINER))
        opf_path = next(e.get("full-path", "") for e in _find(container, "rootfile"))
    except (KeyError, StopIteration, ElementTree.ParseError):
        opfs = [n for n in archive.namelist() if n.lower().endswith(".opf")]
        if not opfs:
            raise ValueError("not an EPUB (no package file)") from None
        opf_path = opfs[0]
    try:
        opf = ElementTree.fromstring(_zipped_xml(archive, opf_path))
    except (KeyError, ElementTree.ParseError) as exc:
        raise ValueError(f"not an EPUB (its package {opf_path} is unreadable)") from exc
    manifest: dict[str, tuple[str, str, str]] = {}
    for item in _find(opf, "item"):
        href = item.get("href")
        if href:
            member, _ = _href(opf_path, href)
            manifest[item.get("id", "")] = (
                member,
                item.get("media-type", ""),
                item.get("properties", ""),
            )
    spine_el = next(iter(_find(opf, "spine")), None)
    spine = [
        manifest[ref.get("idref", "")][0]
        for ref in (_children(spine_el, "itemref") if spine_el is not None else [])
        if ref.get("idref", "") in manifest
    ]
    if not spine:  # no spine: the XHTML of the manifest, in its order
        spine = [m for m, t, _ in manifest.values() if t in _XHTML]
    nav = next((m for m, _, p in manifest.values() if "nav" in p.split()), None)
    entries: list[tuple[str, _Entry]] = []
    if nav is not None and nav in archive.namelist():
        entries = _nav_entries(archive, nav)
    if not entries and spine_el is not None:
        ncx = manifest.get(spine_el.get("toc", ""), ("", "", ""))[0]
        if not ncx:
            ncx = next(
                (m for m, t, _ in manifest.values() if t == "application/x-dtbncx+xml"),
                "",
            )
        if ncx and ncx in archive.namelist():
            entries = _ncx_entries(archive, ncx)
    toc: dict[str, list[_Entry]] = {}
    for member, entry in entries:
        toc.setdefault(member, []).append(entry)
    return _Package(spine=spine, nav=nav, toc=toc)


def _ncx_entries(archive: zipfile.ZipFile, ncx: str) -> list[tuple[str, _Entry]]:
    try:
        root = ElementTree.fromstring(_zipped_xml(archive, ncx))
    except ElementTree.ParseError:
        return []
    out: list[tuple[str, _Entry]] = []

    def walk(parent: Any, level: int) -> None:
        for point in _children(parent, "navPoint"):
            label = " ".join(
                (t.text or "").strip() for t in _find(point, "text")[:1]
            ).strip()
            src = next((c.get("src") for c in _children(point, "content")), None)
            if label and src:
                member, frag = _href(ncx, src)
                out.append((member, _Entry(min(level, 6), label, frag)))
            walk(point, level + 1)

    for nav_map in _find(root, "navMap")[:1]:
        walk(nav_map, 1)
    return out


class _NavReader(HTMLParser):
    """The links of an EPUB 3 nav document's table of contents, each with
    its depth in the nested lists."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[int, str, str]] = []
        self._in_toc = 0  # depth of <nav> once the toc's has opened
        self._depth = 0
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "nav":
            kind = a.get("epub:type") or a.get("role") or ""
            if self._in_toc:
                self._in_toc += 1
            elif "toc" in kind and not self.links:
                self._in_toc = 1
        if not self._in_toc:
            return
        if tag == "ol":
            self._depth += 1
        elif tag == "a" and a.get("href"):
            self._href, self._text = a["href"], []

    def handle_endtag(self, tag: str) -> None:
        if not self._in_toc:
            return
        if tag == "ol":
            self._depth -= 1
        elif tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            if text:
                self.links.append((max(self._depth, 1), text, self._href))
            self._href = None
        elif tag == "nav":
            self._in_toc -= 1

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)


def _nav_entries(archive: zipfile.ZipFile, nav: str) -> list[tuple[str, _Entry]]:
    reader = _NavReader()
    reader.feed(_decode(_zipped_xml(archive, nav)))
    reader.close()
    out = []
    for level, title, href in reader.links:
        member, frag = _href(nav, href)
        out.append((member, _Entry(min(level, 6), title, frag)))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.lower()).strip()


def _matches(block: _Block, title: str) -> bool:
    """Whether the block at an entry's place is that entry's title as the
    page prints it (a heading, or a paragraph styled as one)."""
    if block.kind not in ("h", "p"):
        return False
    have, want = _norm(block.text), _norm(title)
    if not have or not want or len(have) > 3 * len(want) + 20:
        return False
    return have == want or have.startswith(want) or want.startswith(have)


def _place_entries(reader: _Reader, entries: list[_Entry]) -> list[_Block]:
    """The file's blocks with its table-of-contents entries placed: the
    block an entry points at takes its level when it is its title, and is
    preceded by the title as a heading when it is not."""
    blocks = list(reader.blocks)
    at = [reader.anchors.get(e.fragment, 0) if e.fragment else 0 for e in entries]
    shift = 0  # the headings put in so far move what follows
    cursor = 0
    for entry, index in sorted(zip(entries, at, strict=True), key=lambda p: p[1]):
        pos = max(index + shift, cursor)
        if pos < len(blocks) and _matches(blocks[pos], entry.title):
            blocks[pos] = _Block("h", blocks[pos].text, entry.level, toc=True)
        else:
            blocks.insert(pos, _Block("h", entry.title, entry.level, toc=True))
            shift += 1
        cursor = pos + 1
    return blocks


def _levels(blocks: list[_Block], has_toc: bool) -> list[tuple[str, str]]:
    """Blocks as ``office._join_blocks`` takes them: a heading the table
    does not list sits at least one level under the last entry before it."""
    out: list[tuple[str, str]] = []
    under = 0  # the level of the last entry of the table
    for b in blocks:
        if b.kind != "h":
            out.append((b.kind, b.text))
            continue
        level = b.level
        if b.toc:
            under = b.level
        elif has_toc and under:
            level = min(max(level, under + 1), 6)
        out.append(("h", "#" * level + " " + b.text))
    return out


def _epub(data: bytes) -> str:
    """An EPUB as Markdown: the spine's XHTML in reading order, headings
    placed by its table of contents (the module's docstring)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("not an EPUB (not a zip)") from exc
    with archive:
        pkg = _package(archive)
        names = set(archive.namelist())
        spine = [m for m in pkg.spine if m != pkg.nav and m in names]
        # each member is capped by _zipped_xml, and the book as a whole
        # by the same bound: a spine may name a member many times
        total = sum(archive.getinfo(m).file_size for m in spine)
        if total > ZIPPED_XML_MAX:
            raise ValueError(
                f"the book's XHTML inflates to {total >> 20} MB, over the"
                f" {ZIPPED_XML_MAX >> 20} MB the parser reads"
            )
        blocks: list[_Block] = []
        for member in spine:
            reader = _Reader()
            reader.feed(_decode(_zipped_xml(archive, member)))
            reader.close()
            blocks += _place_entries(reader, pkg.toc.get(member, []))
    return _join_blocks(_levels(blocks, bool(pkg.toc)))
