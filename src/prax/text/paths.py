"""The paths a text refers to: what a project's notes say about each
other (stage AL, step 4).

Three shapes, each as the text writes it: a Markdown link
(``[the plan](../plan.md)``, resolved against the folder of the file that
holds it), a path in backticks (`` `docs/plan.md` ``) and a bare mention
of a document file (``see docs/plan.md``). A path in backticks or bare
may be written from the repository's root, from the project's folder or
from the file's own folder, so each reference carries its candidates in
that order of likelihood; the caller matches them exactly against the
paths it knows, and a reference that matches nothing is left for the next
time. Nothing here guesses at a file that does not exist, and nothing
here imports prax.
"""

from __future__ import annotations

import posixpath
import re

DOC_SUFFIXES = ("md", "markdown", "rst", "txt", "adoc")

_LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_TICKED = re.compile(r"`([^`\s]+\.[A-Za-z0-9]{1,8})`")
_BARE = re.compile(
    r"(?<![\w/.`\-\[(])((?:[\w.\-]+/)+[\w.\-]+\.(?:"
    + "|".join(DOC_SUFFIXES)
    + r"))(?![\w/])"
)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)


def _clean(target: str) -> str | None:
    """A link's target as a path: no scheme, no anchor or query, never an
    anchor alone."""
    if _SCHEME.match(target) or target.startswith(("#", "//")):
        return None
    path = target.split("#", 1)[0].split("?", 1)[0].strip()
    return path or None


def _norm(path: str) -> str | None:
    out = posixpath.normpath(path).lstrip("/")
    return None if out in ("", ".") or out.startswith("../") else out


def references(text: str, here: str, prefix: str = "") -> list[tuple[list[str], str]]:
    """Each reference to a path in ``text``, as ``(candidates, words)``:
    where in the repository the path may point (most likely first) and the
    words as the text wrote them, the evidence for an edge. ``here`` is
    the holding file's path in the repository, ``prefix`` the project's
    folder in it. One entry per distinct target, in the order met."""
    folder = posixpath.dirname(here)
    seen: set[tuple[str, ...]] = set()
    out: list[tuple[list[str], str]] = []

    def add(candidates: list[str | None], words: str) -> None:
        kept = list(dict.fromkeys(c for c in candidates if c and c != here))
        key = tuple(kept)
        if kept and key not in seen:
            seen.add(key)
            out.append((kept, words))

    for m in _LINK.finditer(text):
        path = _clean(m.group(1))
        if path is None:
            continue
        if path.startswith("/"):
            add(
                [_norm(path), _norm(posixpath.join(prefix, path.lstrip("/")))],
                m.group(0),
            )
        else:
            add([_norm(posixpath.join(folder, path))], m.group(0))
    # the backticks are part of what the text wrote; a bare mention is its path
    for rx, whole in ((_TICKED, True), (_BARE, False)):
        for m in rx.finditer(text):
            path = m.group(1)
            if _SCHEME.match(path) or "*" in path:
                continue
            add(
                [
                    _norm(path),
                    _norm(posixpath.join(prefix, path)) if prefix else None,
                    _norm(posixpath.join(folder, path)),
                ],
                m.group(0) if whole else m.group(1),
            )
    return out
