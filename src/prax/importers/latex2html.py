"""Books on the web written with latex2html and MathJax: each page a
document, its mathematics as the LaTeX it was written in
(``prax import latex2html``).

Julius O. Smith's online books at CCRMA are the case this was built
for: hundreds of short pages a book, each a topic ("Euler's Identity"),
their formulas in the page as MathJax LaTeX, ``\\(…\\)`` inline and a
``MATHDISPLAY`` block for a display, their figures as images under a
caption. The generic HTML parser would keep the LaTeX as text and lose
what is a formula; this reads the page the way latex2html wrote it.

A book is walked from its index page, the pages in the order its table
of contents lists them. A page's content is what lies between its two
navigation panels; the list of its subsections is left out, being
navigation too. An inline formula becomes ``$…$``, a display one
``$$…$$`` line (a ``formula`` chunk), a figure the picture inlined with
its caption, which the door files as a figure for the vision pass. The
page's HTML is the original, the Markdown its text, stamped
``latex2html/<revision>`` (``POST /ingest/file`` with ``text``).

The fetcher keeps every page and picture it fetched on disk and asks the
site's ``robots.txt``: its ``Crawl-delay`` is the pause between two
requests (CCRMA asks for ten seconds), and a disallowed path is not
fetched. A second run reads from the cache, so a crawl can be stopped
and taken up again. Nothing here opens the store.
"""

from __future__ import annotations

import base64
import re
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

SOURCE = "latex2html"
REVISION = 1
USER_AGENT = "prax/latex2html (a personal research library; one request at a time)"
MIN_DELAY = 2.0  # seconds between two requests where robots.txt asks for none
TIMEOUT = 60.0
# what a book's table of contents links that is not a page of the book
NOT_PAGES = re.compile(
    r"(?:^|/)(?:Index_this_Document|footnode|.*-citation|.*-hardcopy|"
    r"GlobalJOSIndex|index)\.html$",
    re.IGNORECASE,
)


def stamp() -> str:
    """The text's stamp: ``latex2html/1``."""
    return f"{SOURCE}/{REVISION}"


# ------------------------------------------------------------------ fetching


@dataclass
class Fetcher:
    """Pages and pictures off one site, one request at a time, kept on
    disk under ``cache`` so a crawl can be taken up again."""

    cache: Path
    delay: float | None = None  # None: the site's Crawl-delay, else MIN_DELAY
    fetched: int = 0
    _last: float = 0.0
    _robots: dict[str, urllib.robotparser.RobotFileParser] = field(default_factory=dict)
    _client: Any = None

    def _path(self, url: str) -> Path:
        u = urlparse(url)
        rel = unquote(u.path).lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        return self.cache / u.netloc / rel

    def _robot(self, url: str) -> urllib.robotparser.RobotFileParser:
        u = urlparse(url)
        root = f"{u.scheme}://{u.netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            text = self._get(root + "/robots.txt", cached=False)
            rp.parse((text or b"").decode("utf-8", "replace").splitlines())
            self._robots[root] = rp
        return self._robots[root]

    def pause(self, url: str) -> float:
        """The seconds between two requests to the site of ``url``."""
        if self.delay is not None:
            return self.delay
        asked = self._robot(url).crawl_delay(USER_AGENT)
        return max(float(asked or 0), MIN_DELAY)

    def get(self, url: str) -> bytes | None:
        """The bytes at ``url``: from the cache, else fetched politely;
        None for what robots.txt disallows or the site does not serve."""
        path = self._path(url)
        if path.is_file():
            return path.read_bytes()
        if not self._robot(url).can_fetch(USER_AGENT, url):
            return None
        wait = self.pause(url) - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        data = self._get(url, cached=True)
        return data

    def _get(self, url: str, *, cached: bool) -> bytes | None:
        import httpx

        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=TIMEOUT,
            )
        self._last = time.monotonic()
        try:
            r = self._client.get(url)
        except httpx.HTTPError:
            return None
        self.fetched += 1
        if r.status_code != 200:
            return None
        if cached:
            path = self._path(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        return bytes(r.content)


# ------------------------------------------------------------------ the book

_TITLE = re.compile(r"<TITLE>(.*?)</TITLE>", re.IGNORECASE | re.DOTALL)
_HREF = re.compile(r"""HREF\s*=\s*["']([^"'#]+\.html)["']""", re.IGNORECASE)


@dataclass(frozen=True)
class Book:
    url: str  # its index page
    title: str  # as its index page names it
    short: str  # what a page's title carries to tell it from another book's
    pages: tuple[str, ...]  # in the table of contents' order

    @property
    def slug(self) -> str:
        return urlparse(self.url).path.rstrip("/").rsplit("/", 1)[-1] or "book"


def short_title(title: str) -> str:
    """A book's name without its subtitle: "Mathematics of the Discrete
    Fourier Transform (DFT), with Audio Applications -- Second Edition"
    is "Mathematics of the Discrete Fourier Transform (DFT)"."""
    cut = re.split(r"\s+--+\s+|,\s+with\s+|\s+with\s+[A-Z]", title)[0]
    return " ".join(cut.split()).strip(" ,") or title


def page_title(html: str) -> str:
    m = _TITLE.search(html)
    return " ".join(_unescape(m.group(1)).split()) if m else ""


def book_pages(index_url: str, html: str) -> tuple[str, ...]:
    """The pages a book's index links, in its order: same folder, not the
    index or the citation, hardcopy, footnote and index-of-terms pages."""
    base = index_url if index_url.endswith("/") else index_url.rsplit("/", 1)[0] + "/"
    out: list[str] = []
    for href in _HREF.findall(html):
        url = urljoin(base, href)
        if not url.startswith(base) or "/" in url[len(base) :]:
            continue
        if NOT_PAGES.search(url) or url in out:
            continue
        out.append(url)
    return tuple(out)


_TOP = re.compile(r"""escape\('((?:\\.|[^'\\])*)'\)"?\s*>\s*Top""")


def book_title(html: str) -> str:
    """The book's name as its pages' "Top" link says it, in its own case
    (the index page's title is in capitals)."""
    m = _TOP.search(html)
    if not m:
        return ""
    return " ".join(_unescape(re.sub(r"\\(.)", r"\1", m.group(1))).split())


def book(fetcher: Fetcher, url: str) -> Book | None:
    """A book from its index page, named as its first page's "Top" link
    names it; None where the index is not served."""
    data = fetcher.get(url)
    if data is None:
        return None
    html = data.decode("utf-8", "replace")
    pages = book_pages(url, html)
    title = page_title(html)
    first = fetcher.get(pages[0]) if pages else None
    if first:
        title = book_title(first.decode("utf-8", "replace")) or title
    return Book(url, title, short_title(title), pages)


def book_macros(fetcher: Fetcher, book: Book) -> Macros:
    """The macros of a book's ``mathjax-config.js``, empty without one."""
    data = fetcher.get(urljoin(book.url, "mathjax-config.js"))
    return macros(data.decode("utf-8", "replace")) if data else {}


# ------------------------------------------------------------------ the page

_CONTENT_START = "<!--End of Navigation Panel-->"
_CONTENT_END = re.compile(r"<!--Navigation Panel-->|<ADDRESS>", re.IGNORECASE)
_CHILD_LINKS = re.compile(
    r"<!--Table of Child-Links-->.*?<!--End of Table of Child-Links-->",
    re.IGNORECASE | re.DOTALL,
)


def content(html: str) -> str:
    """The part of a latex2html page that is the page: after the first
    navigation panel, before the next one or the address, without the
    list of its subsections."""
    start = html.find(_CONTENT_START)
    body = html[start + len(_CONTENT_START) :] if start >= 0 else html
    end = _CONTENT_END.search(body)
    if end:
        body = body[: end.start()]
    return _CHILD_LINKS.sub("", body)


def _unescape(text: str) -> str:
    import html

    return html.unescape(text)


_INLINE_MATH = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_DISPLAY_WRAP = re.compile(
    r"^\\\[(.*)\\\]$|^\\begin\{(?:equation|displaymath)\*?\}(.*)"
    r"\\end\{(?:equation|displaymath)\*?\}$",
    re.DOTALL,
)
_LABEL = re.compile(r"\\label\{[^}]*\}")
_FBOX = re.compile(r"\\fbox\{\$\\displaystyle\s*(.*?)\$\}", re.DOTALL)


Macros = dict[str, tuple[str, int]]
_MACRO = re.compile(
    r"^\s*(\w+)\s*:\s*(?:'((?:\\.|[^'\\])*)'|\[\s*'((?:\\.|[^'\\])*)'\s*,\s*(\d+))",
    re.MULTILINE,
)


def macros(config: str) -> Macros:
    """The ``macros`` of a book's ``mathjax-config.js``: each name with
    its LaTeX and its number of arguments (``abs: ['\\\\left|#1\\\\right|',
    1]``), the JavaScript string's escapes undone."""
    start = config.find("macros")
    out: Macros = {}
    for m in _MACRO.finditer(config[start:] if start >= 0 else ""):
        body = m.group(2) if m.group(2) is not None else m.group(3)
        if body is None:
            continue
        body = re.sub(r"\\(.)", r"\1", body)
        out[m.group(1)] = (body, int(m.group(4) or 0))
    return out


def _argument(latex: str, at: int) -> tuple[str, int] | None:
    """The argument at ``at``: a braced group (without its braces) or one
    token; and where it ends."""
    while at < len(latex) and latex[at] == " ":
        at += 1
    if at >= len(latex):
        return None
    if latex[at] == "{":
        depth = 0
        for j in range(at, len(latex)):
            depth += {"{": 1, "}": -1}.get(latex[j], 0)
            if depth == 0:
                return latex[at + 1 : j], j + 1
        return None
    if latex[at] == "\\":
        m = re.match(r"\\[A-Za-z]+|\\.", latex[at:])
        if m:
            return m.group(0), at + len(m.group(0))
    return latex[at], at + 1


_COMMAND = re.compile(r"\\([A-Za-z]+)")
EXPAND_ROUNDS = 8  # a macro defined through another, and that through a third


def expand(latex: str, defined: Macros) -> str:
    """The LaTeX with a book's own macros written out, round by round,
    until none is left or ``EXPAND_ROUNDS`` is reached: what KaTeX can
    render and a search can find."""
    if not defined:
        return latex
    for _ in range(EXPAND_ROUNDS):
        out: list[str] = []
        at = changed = 0
        for m in _COMMAND.finditer(latex):
            if m.start() < at or m.group(1) not in defined:
                continue
            body, count = defined[m.group(1)]
            args: list[str] = []
            end = m.end()
            for _n in range(count):
                got = _argument(latex, end)
                if got is None:
                    break
                args.append(got[0])
                end = got[1]
            if len(args) < count:
                continue
            for k, arg in enumerate(args, 1):
                body = body.replace(f"#{k}", arg)
            # a command glued to the next letter would read as one name
            tail = " " if body[-1:].isalpha() and latex[end : end + 1].isalpha() else ""
            out.append(latex[at : m.start()] + body + tail)
            at, changed = end, changed + 1
        if not changed:
            break
        latex = "".join(out) + latex[at:]
    return latex


def display(latex: str, defined: Macros | None = None) -> str:
    """A display's LaTeX as one ``$$…$$`` line: its ``\\[…\\]`` or
    equation wrapper and labels gone, a boxed formula ``\\boxed``, the
    book's macros written out."""
    latex = " ".join(latex.split())
    m = _DISPLAY_WRAP.match(latex)
    if m:
        latex = (m.group(1) or m.group(2) or "").strip()
    latex = _FBOX.sub(r"\\boxed{\1}", _LABEL.sub("", latex)).strip()
    return f"$${expand(latex, defined or {})}$$"


class _Render:
    """latex2html's HTML as prax's Markdown."""

    def __init__(
        self, base: str, picture: Callable[[str], str | None], defined: Macros
    ) -> None:
        self.base = base
        self.picture = picture
        self.defined = defined
        self.blocks: list[str] = []
        self.line: list[str] = []

    def math(self, text: str) -> str:
        """Inline ``\\(…\\)`` as ``$…$``, the book's macros written out."""
        return _INLINE_MATH.sub(
            lambda m: "$" + expand(m.group(1).strip(), self.defined) + "$", text
        )

    def flush(self) -> None:
        text = " ".join("".join(self.line).split())
        if text:
            self.blocks.append(self.math(text))
        self.line = []

    def inline(self, el: Any) -> str:
        """An element's text with its children's inline marks."""
        parts = [el.text or ""]
        for child in el:
            parts.append(self.marked(child))
            parts.append(child.tail or "")
        return "".join(parts)

    def marked(self, el: Any) -> str:
        """One inline element as Markdown: emphasis kept, a picture by its
        alternative text."""
        tag = str(el.tag).lower() if isinstance(el.tag, str) else ""
        inner = self.inline(el)
        if tag in ("i", "em") and inner.strip():
            return f"*{inner.strip()}*"
        if tag in ("b", "strong") and inner.strip():
            return f"**{inner.strip()}**"
        if tag == "br":
            return " "
        if tag == "img":
            return str(el.get("alt") or "")
        return inner

    def walk(self, el: Any) -> None:
        self.line.append(el.text or "")
        for child in el:
            self.element(child)
            self.line.append(child.tail or "")

    def element(self, el: Any) -> None:
        tag = str(el.tag).lower() if isinstance(el.tag, str) else ""
        cls = str(el.get("class") or "").upper()
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.flush()
            title = " ".join(self.inline(el).split())
            if title:
                self.blocks.append("#" * int(tag[1]) + " " + title)
        elif tag == "div" and cls == "MATHDISPLAY":
            self.flush()
            self.blocks.append(display(el.text_content(), self.defined))
        elif tag == "table" and el.find(".//caption") is not None:
            self.flush()
            self.figure(el)
        elif tag == "table":
            self.flush()
            for row in el.iter("tr"):
                cells = [" ".join(self.inline(c).split()) for c in row]
                if any(cells):
                    self.blocks.append(" | ".join(cells))
        elif tag in ("ul", "ol"):
            self.flush()
            for n, li in enumerate(el.iter("li"), 1):
                mark = f"{n}." if tag == "ol" else "-"
                text = " ".join(self.inline(li).split())
                if text:
                    self.blocks.append(mark + " " + self.math(text))
        elif tag == "pre":
            self.flush()
            self.blocks.append("```\n" + el.text_content().rstrip() + "\n```")
        elif tag in ("p", "div", "center", "blockquote", "dl", "dd", "dt"):
            self.flush()
            self.walk(el)
            self.flush()
        elif tag in ("br", "hr"):
            self.flush()
        elif tag in ("script", "style"):
            return
        else:
            self.line.append(self.marked(el))

    def figure(self, table: Any) -> None:
        caption = " ".join(self.inline(table.find(".//caption")).split())
        caption = self.math(caption).replace("**", "")
        img = table.find(".//img")
        url = None
        if img is not None:
            src = srcset(img.get("srcset") or "").get(2.0) or img.get("src")
            url = self.picture(urljoin(self.base, src)) if src else None
        if url:
            # the caption is the picture's: a line of its own would say it twice
            alt = caption.replace("]", ")").replace("[", "(")
            self.blocks.append(f"![{alt}]({url})")
        elif caption:
            self.blocks.append(caption)


def srcset(value: str) -> dict[float, str]:
    """An image's ``srcset`` by density: ``{1.0: "img2.png", 2.0: …}``."""
    out: dict[float, str] = {}
    for part in value.split(","):
        bits = part.split()
        if len(bits) == 2 and bits[1].endswith("x"):
            try:
                out[float(bits[1][:-1])] = bits[0]
            except ValueError:
                continue
    return out


def to_markdown(
    html: str,
    base: str,
    picture: Callable[[str], str | None] = lambda _: None,
    defined: Macros | None = None,
) -> str:
    """A latex2html page's content as Markdown. ``picture`` turns a
    figure's image URL into what the figure line links (a data URL the
    door files), None to leave the figure as its caption; ``defined`` is
    the book's macros (``macros``)."""
    import lxml.html

    root = lxml.html.fragment_fromstring(content(html), create_parent="div")
    render = _Render(base, picture, defined or {})
    render.walk(root)
    render.flush()
    return "\n\n".join(render.blocks).strip() + "\n"


def data_url(fetcher: Fetcher) -> Callable[[str], str | None]:
    """A figure's image as a data URL, fetched (and kept) by ``fetcher``."""

    def picture(url: str) -> str | None:
        data = fetcher.get(url)
        if not data:
            return None
        kind = "png" if data[:4] == b"\x89PNG" else "gif" if data[:3] == b"GIF" else ""
        if not kind:
            return None
        return f"data:image/{kind};base64,{base64.b64encode(data).decode('ascii')}"

    return picture
