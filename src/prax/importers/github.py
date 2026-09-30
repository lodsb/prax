"""Starred repositories on GitHub, one document each.

A star is a bookmark the way a Zotero item is: the user chose it. The
document is the repository's README under a header of what GitHub knows
— description, language, topics, stars, licence, when it was starred and
last pushed to — so search finds the tool by what it does and the
extractor reads it as a tool (or a paper's code, a schematic's firmware).

``GET /users/{user}/starred`` lists public stars without a token, at
sixty requests an hour — a README is one request, so a token
(``PRAX_GITHUB_TOKEN``, or ``sources.github.token``) is what makes a
library of stars practical; with one, ``GET /user/starred`` lists the
token's own account. Idempotent by full name; ``pushed_at`` is the
version, so ``--refresh`` re-reads a repository that moved. Nothing here
writes to GitHub (invariant 10).
"""

from __future__ import annotations

import base64
import contextlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

from prax import config

from .feed import Item

API = "https://api.github.com"
SOURCE = "github"
PER_PAGE = 100
USER_AGENT = "prax (+https://github.com/lodsb/prax)"

Fetcher = Callable[[str, dict[str, str]], tuple[int, dict[str, str], bytes]]


def token() -> str | None:
    value = config.setting("sources.github.token", "PRAX_GITHUB_TOKEN")
    return str(value).strip() if value else None


def user() -> str | None:
    value = config.setting("sources.github.user", "PRAX_GITHUB_USER")
    return str(value).strip() if value else None


def _http(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class GitHub:
    """A few calls of the REST API, with the token when there is one."""

    def __init__(
        self, *, token: str | None = None, fetch: Fetcher | None = None
    ) -> None:
        self.token = token
        self.fetch = fetch or _http
        self.remaining: int | None = None

    def _headers(self, accept: str) -> dict[str, str]:
        h = {
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _call(self, path: str, accept: str) -> tuple[int, dict[str, str], bytes]:
        url = path if path.startswith("http") else API + path
        status, headers, body = self.fetch(url, self._headers(accept))
        low = {k.lower(): v for k, v in headers.items()}
        if "x-ratelimit-remaining" in low:
            with contextlib.suppress(ValueError):
                self.remaining = int(low["x-ratelimit-remaining"])
        if status == 403 and self.remaining == 0:
            reset = low.get("x-ratelimit-reset")
            wait = ""
            if reset:
                try:
                    minutes = max(0, int(reset) - time.time()) / 60
                    wait = f"; it resets in {minutes:.0f} min"
                except ValueError:
                    pass
            raise RuntimeError(
                "GitHub's rate limit is spent"
                + wait
                + (
                    ""
                    if self.token
                    else " (a token raises it from 60 to 5,000 an hour)"
                )
            )
        if status == 401:
            raise RuntimeError("GitHub refused the token")
        return status, low, body

    def starred(self, user: str | None) -> Iterator[dict[str, Any]]:
        """Every star, newest first, with ``starred_at`` beside ``repo``."""
        path = f"/users/{urllib.parse.quote(user)}/starred" if user else "/user/starred"
        url: str | None = f"{API}{path}?per_page={PER_PAGE}"
        while url:
            status, headers, body = self._call(url, "application/vnd.github.star+json")
            if status == 404:
                raise RuntimeError(f"GitHub knows no user {user!r}")
            if status != 200:
                raise RuntimeError(f"GitHub answered {status} for {url}")
            yield from json.loads(body)
            url = _next_link(headers.get("link", ""))

    def repo(self, full_name: str) -> dict[str, Any]:
        """One repository's facts (``GET /repos/{owner}/{repo}``)."""
        status, _, body = self._call(
            f"/repos/{full_name}", "application/vnd.github+json"
        )
        if status == 404:
            raise KeyError(f"GitHub knows no repository {full_name!r}")
        if status != 200:
            raise RuntimeError(f"GitHub answered {status} for {full_name}")
        data: dict[str, Any] = json.loads(body)
        return data

    def readme(self, full_name: str) -> str | None:
        status, _, body = self._call(
            f"/repos/{full_name}/readme", "application/vnd.github+json"
        )
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(
                f"GitHub answered {status} for the README of {full_name}"
            )
        data = json.loads(body)
        if data.get("encoding") == "base64":
            return base64.b64decode(data.get("content") or "").decode(
                "utf-8", "replace"
            )
        return data.get("content") or None


# the first path segments of github.com that are GitHub's own pages, not an
# owner (the extension keeps the same list, lib.githubRepoOf)
_NOT_OWNERS = frozenset(
    [
        "orgs",
        "settings",
        "marketplace",
        "explore",
        "topics",
        "features",
        "login",
        "notifications",
        "search",
        "sponsors",
        "collections",
        "trending",
        "about",
        "pricing",
    ]
)


def repo_of_url(url: str) -> str | None:
    """``owner/repo`` of a github.com URL of a repository or a page in it
    (a file, a branch, an issue), or None."""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0].lower() in _NOT_OWNERS:
        return None
    repo = parts[1].removesuffix(".git")
    return f"{parts[0]}/{repo}" if repo else None


def one(api: GitHub, full_name: str) -> Item:
    """One repository as the starred import makes it, README included."""
    item = item_for(api.repo(full_name), starred_at=None)
    readme = api.readme(item.key)
    item.text = text_for(item.text or "", readme)
    item.meta["readme"] = readme is not None
    return item


def _next_link(link: str) -> str | None:
    for part in link.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def items(
    api: GitHub,
    user: str | None,
    *,
    readmes: bool = True,
    log: Callable[[str], None] | None = None,
) -> Iterator[Item]:
    """Every starred repository as an ``Item``; the README is fetched when
    the item is sent, so a star the library holds costs no request."""
    for row in api.starred(user):
        repo = row.get("repo") or row  # the star+json shape, or the plain one
        item = item_for(repo, starred_at=row.get("starred_at"))
        if readmes:

            def fill(it: Item, full: str = repo["full_name"]) -> None:
                readme = api.readme(full)
                it.text = text_for(it.text or "", readme)
                it.meta["readme"] = readme is not None

            item.fill = fill
        yield item


def text_for(head: str, readme: str | None) -> str:
    """The document: the facts, a rule, the README (or a word that there
    is none). ``head`` may already carry an earlier rule and tail."""
    body = head.split("\n---\n", 1)[0].rstrip() + "\n"
    if readme:
        return body + "\n---\n\n" + readme.strip() + "\n"
    return body + "\n(no README)\n"


def item_for(repo: dict[str, Any], *, starred_at: str | None) -> Item:
    full = repo["full_name"]
    description = (repo.get("description") or "").strip()
    topics = [t for t in repo.get("topics") or [] if t]
    licence = (repo.get("license") or {}).get("spdx_id") or (
        repo.get("license") or {}
    ).get("name")
    if licence == "NOASSERTION":
        licence = None
    facts = [f"- Repository: {repo.get('html_url') or 'https://github.com/' + full}"]
    about = " · ".join(
        x
        for x in (
            f"Language: {repo['language']}" if repo.get("language") else "",
            f"Stars: {repo['stargazers_count']:,}"
            if repo.get("stargazers_count") is not None
            else "",
            f"Licence: {licence}" if licence else "",
            "Archived" if repo.get("archived") else "",
            "Fork" if repo.get("fork") else "",
        )
        if x
    )
    if about:
        facts.append("- " + about)
    if topics:
        facts.append("- Topics: " + ", ".join(topics))
    if repo.get("homepage"):
        facts.append(f"- Homepage: {repo['homepage']}")
    when = " · ".join(
        x
        for x in (
            f"Starred: {starred_at[:10]}" if starred_at else "",
            f"Last push: {repo['pushed_at'][:10]}" if repo.get("pushed_at") else "",
            f"Created: {repo['created_at'][:10]}" if repo.get("created_at") else "",
        )
        if x
    )
    if when:
        facts.append("- " + when)
    head = [f"# {full}", ""]
    if description:
        head += [description, ""]
    head += facts
    title = full if not description else f"{full}: {description[:120]}"
    return Item(
        key=full,
        title=title,
        url=repo.get("html_url") or f"https://github.com/{full}",
        text=text_for("\n".join(head), None),
        tags=[f"github:{t}" for t in topics],
        meta={
            "id": repo.get("id"),
            "description": description or None,
            "language": repo.get("language"),
            "topics": topics,
            "stars": repo.get("stargazers_count"),
            "licence": licence,
            "homepage": repo.get("homepage") or None,
            "starred_at": starred_at,
            "pushed_at": repo.get("pushed_at"),
            "archived": bool(repo.get("archived")),
            "readme": False,  # True once `fill` found one
        },
        version=repo.get("pushed_at"),
    )
