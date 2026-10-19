"""A page's slug: the one grammar for the names of pages (``store.pages``)
and for an importer that names its pages before it sends them
(``importers.mdwiki``). Lower-case letters and digits joined by hyphens,
cut at ``LIMIT``; a slug cut for length keeps a short hash of what it was
made from, so two long paths stay two pages. Imports nothing of prax."""

from __future__ import annotations

import hashlib
import re

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
