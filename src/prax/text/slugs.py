"""A page's slug: the one grammar for the names of pages (``store.pages``)
and for an importer that names its pages before it sends them
(``importers.mdwiki``). Lower-case letters and digits joined by hyphens,
cut at ``LIMIT``; a slug cut for length keeps a short hash of what it was
made from, so two long paths stay two pages. Imports nothing of prax."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import unquote

LIMIT = 80  # the characters a slug may hold
TAG = 8  # hex characters of the hash a cut slug ends with

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """The slug of ``text``: "page" when nothing of it is left."""
    return _NOT_SLUG.sub("-", text.lower()).strip("-")[:LIMIT] or "page"


def tagged(text: str, key: str) -> str:
    """The slug of ``text``, its end replaced by the hash of ``key`` when it
    is longer than ``LIMIT``: within the limit, so ``slug`` keeps it as it
    is (the quality review of 2026-10-18, 15: a copy of the cut in the
    importer meant a shorter one would cut the tag off)."""
    whole = _NOT_SLUG.sub("-", text.lower()).strip("-")
    if len(whole) <= LIMIT:
        return whole or "page"
    tag = hashlib.sha256(key.encode("utf-8")).hexdigest()[:TAG]
    return whole[: LIMIT - TAG - 1].rstrip("-") + "-" + tag


# ------------------------------------------------------------- pointers
#
# ``prax:<slug>``, a pointer that outlives a sync (plan item AU, step 3): it
# names a page or a project's file or journal entry, never a document id,
# which a refresh retires, and no host. Three shapes:
#
#   prax:onset-notes                         a page, by its slug
#   prax:<project>/<path in the repository>  a synced file, the current text
#   prax:<project>/<path>#<entry>            a journal entry, its newest text
#
# The project is its name's slug; the entry is its key's slug
# (``2026-09-16 (late)`` is ``2026-09-16-late``), so a pointer holds no
# space and stands in a Markdown link as it is.

POINTER = "prax:"


@dataclass(frozen=True)
class Pointer:
    page: str | None = None  # a page's slug
    project: str | None = None  # a project's name, as a slug
    path: str | None = None  # the file's path in the repository
    entry: str | None = None  # a journal entry's key, as a slug


def pointer(project: str, path: str, entry: str | None = None) -> str:
    """The pointer of a project's file, or of an entry of its journal."""
    out = f"{POINTER}{slug(project)}/{path}"
    return f"{out}#{slug(entry)}" if entry else out


def page_pointer(page_slug: str) -> str:
    return f"{POINTER}{page_slug}"


def parse_pointer(ref: str) -> Pointer | None:
    """What a pointer names, or None when ``ref`` is no pointer. Percent
    escapes are read (a browser's copy of a link)."""
    ref = unquote(ref.strip())
    if not ref.startswith(POINTER):
        return None
    rest = ref[len(POINTER) :].strip("/")
    if not rest:
        return None
    if "/" not in rest:
        return Pointer(page=rest.split("#", 1)[0]) if rest.split("#", 1)[0] else None
    project, _, place = rest.partition("/")
    path, _, entry = place.partition("#")
    if not path:
        return None
    return Pointer(
        project=slug(project), path=path, entry=slug(entry) if entry else None
    )
