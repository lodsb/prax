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
Nothing here opens the store.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

SOURCE = "mdwiki"
# folders that are tooling, generated, copies or admin, not the wiki
SKIP_DIRS = frozenset(
    {".git", ".obsidian", "_template", "txt", "admin", "public", "vault", "tests"}
)
# files of a wiki that are its tooling or its own bookkeeping
SKIP_FILES = frozenset({"CLAUDE.md", "log.md", "SCHEMA.md"})
SLUG_CHARS = 80  # store.pages.slugify's cut

_FRONT = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_LINK = re.compile(r"(!?)\[([^\]]*)\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Note:
    path: str  # relative to the repository, with forward slashes
    slug: str
    title: str
    text: str  # without its front matter

    @property
    def topic(self) -> str:
        return self.path.split("/", 1)[0] if "/" in self.path else ""


def _skipped(rel: Path) -> bool:
    return any(part in SKIP_DIRS for part in rel.parts[:-1])


def pdfs(root: Path) -> list[str]:
    """The repository's PDFs, as paths relative to it, in order."""
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.pdf")
        if not _skipped(p.relative_to(root))
    )


def slug_for(name: str, path: str) -> str:
    """A page's slug: the collection's name and the note's path, cut to
    what a slug may hold with a short hash of the path to keep two long
    paths apart."""
    stem = path.removesuffix(".md")
    slug = _SLUG.sub("-", f"{name}-{stem}".lower()).strip("-")
    if len(slug) <= SLUG_CHARS:
        return slug
    tag = hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]
    return slug[: SLUG_CHARS - 9].rstrip("-") + "-" + tag


def notes(root: Path, name: str) -> list[Note]:
    """The wiki's pages: every Markdown file in a ``wiki`` folder (not its
    tooling or bookkeeping), each topic folder's README (which lists its
    papers), and the repository's own README."""
    out: list[Note] = []
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if _skipped(rel) or p.name in SKIP_FILES:
            continue
        parts = rel.parts
        in_wiki = "wiki" in parts[:-1]
        topic_readme = len(parts) == 2 and p.name == "README.md"
        root_readme = len(parts) == 1 and p.name == "README.md"
        if not (in_wiki or topic_readme or root_readme):
            continue
        raw = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        body = _FRONT.sub("", raw, count=1).strip()
        if not body:
            continue
        h1 = _H1.search(body)
        path = rel.as_posix()
        title = h1.group(1).strip() if h1 else path
        out.append(Note(path, slug_for(name, path), title, body + "\n"))
    return out


def topic_entries(found: list[Note]) -> dict[str, str]:
    """Each topic folder's entry note: its wiki's index, else its README."""
    out: dict[str, str] = {}
    for n in found:
        if n.path.endswith("/wiki/index.md") and n.path.count("/") == 2:
            out[n.topic] = n.path
    for n in found:
        if n.path.count("/") == 1 and n.path.endswith("/README.md"):
            out.setdefault(n.topic, n.path)
    return out


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
