"""A repository of papers with a Markdown wiki written about them
(``prax import mdwiki``): the PDFs as documents, the wiki as prax pages
whose links are the library's.

Julius O. Smith's Music 423 repository is the case this was built for:
a folder a topic, its papers as PDFs, and beside them an "LLM wiki" Claude
wrote from them (``<topic>/wiki/``): a summary a paper, concept pages
across papers, landmark notes, an index, linked to each other and to the
PDFs by relative paths. In prax a page that links ``[title](#doc/N)``
annotates that document, an edge in the graph, so the wiki's links
become the graph's: a note to its paper, a concept to the notes it
draws on.

``pdfs`` and ``notes`` read the working copy; ``rewrite`` turns a note's
relative links into links to what the door holds, by the maps the
import builds (a PDF's path to its document, a note's path to its page's
document, a topic's folder to its entry page). A link to something not
imported keeps its words and loses its target. What is not the wiki is
left out: the tooling (``CLAUDE.md``, Makefiles, scripts, ``.obsidian``),
the generated graph, text copies of the PDFs, and a course's admin files.
That layout is the default, and a ``Layout`` says another: the folder
the wiki lives in and the folders left out (``--wiki-folder``,
``--skip``). ``left_out`` counts the Markdown files a layout does not
read, so a wiki of another shape is said to be one rather than imported
as its READMEs (the quality review of 2026-10-18, 17). Obsidian's
``[[wikilinks]]`` are not read: their words stay and link nowhere.
Nothing here opens the store.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from prax.text import slugs, status

SOURCE = "mdwiki"
WIKI = "wiki"  # the folder a topic's wiki is in
# folders that are tooling, generated, copies or admin, not the wiki: the
# defaults are the Music 423 repository's
SKIP_DIRS = frozenset(
    {".git", ".obsidian", "_template", "txt", "admin", "public", "vault", "tests"}
)
# files of a wiki that are its tooling or its own bookkeeping
SKIP_FILES = frozenset({"CLAUDE.md", "log.md", "SCHEMA.md"})

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_LINK = re.compile(r"(!?)\[([^\]]*)\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")


@dataclass(frozen=True)
class Note:
    path: str  # relative to the repository, with forward slashes
    slug: str
    title: str
    text: str  # without its front matter

    @property
    def topic(self) -> str:
        return self.path.split("/", 1)[0] if "/" in self.path else ""


@dataclass(frozen=True)
class Layout:
    """Where a repository keeps its wiki: the folder name (``WIKI``) and
    the folders that are not the wiki or its papers (``SKIP_DIRS``)."""

    wiki: str = WIKI
    skip: frozenset[str] = SKIP_DIRS


DEFAULT = Layout()


def _skipped(rel: Path, layout: Layout) -> bool:
    return any(part in layout.skip for part in rel.parts[:-1])


def pdfs(root: Path, layout: Layout = DEFAULT) -> list[str]:
    """The repository's PDFs, as paths relative to it, in order."""
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.pdf")
        if not _skipped(p.relative_to(root), layout)
    )


def _is_note(rel: Path, layout: Layout) -> bool:
    """A Markdown file the layout reads as a page: in a wiki folder, a
    topic's README or the repository's own."""
    parts = rel.parts
    return layout.wiki in parts[:-1] or (len(parts) <= 2 and rel.name == "README.md")


def left_out(root: Path, layout: Layout = DEFAULT) -> dict[str, int]:
    """The Markdown files the layout does not read, by why: ``skipped``
    (under a skipped folder, or a wiki's tooling) and ``outside`` (not in a
    wiki folder nor a README). The import says them, so a wiki of another
    shape is not imported silently as its READMEs."""
    out = {"skipped": 0, "outside": 0}
    for p in root.rglob("*.md"):
        rel = p.relative_to(root)
        if _skipped(rel, layout) or p.name in SKIP_FILES:
            out["skipped"] += 1
        elif not _is_note(rel, layout):
            out["outside"] += 1
    return out


def slug_for(name: str, path: str) -> str:
    """A page's slug: the collection's name and the note's path, cut to
    what a slug may hold with a short hash of the path to keep two long
    paths apart."""
    return slugs.tagged(f"{name}-{path.removesuffix('.md')}", path)


def notes(root: Path, name: str, layout: Layout = DEFAULT) -> list[Note]:
    """The wiki's pages: every Markdown file in a wiki folder (not its
    tooling or bookkeeping), each topic folder's README (which lists its
    papers), and the repository's own README."""
    out: list[Note] = []
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if _skipped(rel, layout) or p.name in SKIP_FILES:
            continue
        if not _is_note(rel, layout):
            continue
        raw = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        body = status.FRONT_MATTER.sub("", raw, count=1).strip()
        if not body:
            continue
        h1 = _H1.search(body)
        path = rel.as_posix()
        title = h1.group(1).strip() if h1 else path
        out.append(Note(path, slug_for(name, path), title, body + "\n"))
    return out


def topic_entries(found: list[Note], layout: Layout = DEFAULT) -> dict[str, str]:
    """Each topic folder's entry note: its wiki's index, else its README."""
    out: dict[str, str] = {}
    for n in found:
        if n.path.endswith(f"/{layout.wiki}/index.md") and n.path.count("/") == 2:
            out[n.topic] = n.path
    for n in found:
        if n.path.count("/") == 1 and n.path.endswith("/README.md"):
            out.setdefault(n.topic, n.path)
    return out


def parent_of(path: str, paths: set[str], layout: Layout = DEFAULT) -> str | None:
    """The note a note sits under in the tree of pages, from where it lies
    in the repository: a page of a topic's wiki under that wiki's index
    (``topic/wiki/index.md``), else the topic's README; the index under its
    topic's README; a topic's README under the repository's; the
    repository's README at the top. None where nothing above it was
    imported."""
    parts = path.split("/")
    root = "README.md" if "README.md" in paths else None
    if len(parts) == 1:
        return None  # the repository's own README, or a loose note
    topic = parts[0]
    readme = f"{topic}/README.md" if f"{topic}/README.md" in paths else None
    at = f"{topic}/{layout.wiki}/index.md"
    index = at if at in paths else None
    if path == f"{topic}/README.md":
        return root
    if path == index:
        return readme or root
    return index or readme or root


def rewrite(
    text: str,
    here: str,
    documents: dict[str, int],
    pages: dict[str, int],
    topics: dict[str, int],
) -> str:
    """A note's relative links as links to what the door holds: a PDF's to
    its document, a note's to its page, a topic folder's to its entry
    page, all ``[words](#doc/N)``; a link to anything else keeps its
    words. An image, a web address, an anchor in the page are left."""
    base = posixpath.dirname(here)

    def link(m: re.Match[str]) -> str:
        bang, words, target = m.group(1), m.group(2), m.group(3)
        if bang or re.match(r"^[a-z]+:|^#", target, re.IGNORECASE):
            return m.group(0)
        path = posixpath.normpath(posixpath.join(base, target.split("#", 1)[0]))
        if path.startswith(".."):
            return words
        doc = documents.get(path) or pages.get(path)
        if doc is None and "/" not in path:  # a topic's folder, "../../diffusion/"
            doc = topics.get(path)
        return f"[{words}](#doc/{doc})" if doc else words

    return _LINK.sub(link, text)
