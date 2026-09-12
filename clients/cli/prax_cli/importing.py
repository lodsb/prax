"""`prax import`: what a service or an app exported, into the library
through the door. The readers live in `prax.importers` (github, chats,
links) and are imported when the command runs, so the rest of `prax`
stays as light as it is; this is the command around them."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prax.client import Door

from . import out

if TYPE_CHECKING:
    from prax.importers import feed

WHAT = ("github", "chat", "links")


def import_(door: Door, a: Any) -> int:
    if a.what == "github":
        return _github(door, a)
    if a.what == "chat":
        return _chat(door, a)
    if a.what == "links":
        return _links(door, a)
    out.fail(f"unknown source {a.what!r}", "one of: " + ", ".join(WHAT))
    return 2


def _github(door: Door, a: Any) -> int:
    from prax.importers import github

    token = a.token_github or github.token()
    user = a.user or github.user()
    if not user and not token:
        out.fail(
            "whose stars? give a user name, or a token for your own",
            "prax import github octocat · PRAX_GITHUB_TOKEN=… prax import github",
        )
        return 2
    api = github.GitHub(token=token)
    who = user or "your account"
    out.say(
        out.bold("GitHub stars")
        + out.dim(f"   {who}" + ("" if token else " · no token: 60 requests an hour"))
    )
    try:
        return _run(door, a, github.SOURCE, github.items(api, user, log=out.hint))
    except RuntimeError as exc:
        out.fail(str(exc))
        return 1


def _chat(door: Door, a: Any) -> int:
    from prax.importers import chats

    conversations = []
    for path in a.files:
        try:
            found = chats.read(Path(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            out.fail(f"{path}: {exc}")
            return 2
        conversations += found
        for c in found:
            out.hint(
                f"  {path}: {c.name} ({c.app}), {out.num(len(c.messages))} messages"
            )
    if not conversations:
        out.fail("no conversations in those files")
        return 2
    out.say(
        out.bold("Chats")
        + out.dim(
            f"   {len(conversations)} conversations by month"
            + (" · links captured too" if a.links else "")
        )
    )
    return _run(door, a, chats.SOURCE, chats.items(conversations, with_links=a.links))


def _links(door: Door, a: Any) -> int:
    from prax.importers import links

    def everything() -> Iterable[feed.Item]:
        for path in a.files:
            yield from links.read(Path(path))

    try:
        items = list(everything())
    except (OSError, ValueError) as exc:
        out.fail(str(exc))
        return 2
    files = out.plural(len(a.files), "file")
    out.say(
        out.bold("Links")
        + out.dim(f"   {out.num(len(items))} in {files}; the door fetches each")
    )
    return _run(door, a, links.SOURCE, items)


def _run(door: Door, a: Any, source: str, items: Iterable[feed.Item]) -> int:
    from prax.importers import feed

    report = feed.run(
        door,
        source,
        items,
        domains=a.domain or None,
        tags=a.tag or None,
        refresh=a.refresh,
        limit=a.limit,
        dry_run=a.dry_run,
        log=None if a.json else out.hint,
    )
    if a.json:
        print(
            json.dumps(
                {
                    "source": report.source,
                    "added": report.added,
                    "refreshed": report.refreshed,
                    "seen": report.seen,
                    "same": report.same,
                    "failed": report.failed,
                    "planned": [
                        {"key": i.key, "title": i.title, "url": i.url, "tags": i.tags}
                        for i in report.planned
                    ],
                },
                indent=2,
            )
        )
        return 0 if not report.failed else 1
    if a.dry_run:
        out.say()
        out.say(
            out.bold(f"Would add {len(report.planned)}")
            + out.dim(f"   {report.seen} already there")
        )
        for i in report.planned[:60]:
            out.hint(
                f"  {i.title or i.url or i.key}"
                + (out.dim(f"  {i.url}") if i.url and i.title else "")
            )
        if len(report.planned) > 60:
            out.hint(f"  … and {len(report.planned) - 60} more")
        out.hint("Nothing was sent. Drop --dry-run to import.")
        return 0
    out.say()
    out.say(out.bold(str(report)))
    for key, why in report.failed[:20]:
        out.hint(f"  failed: {key}: {why}")
    if report.added or report.refreshed:
        out.hint("A worker reads what arrived: prax work --watch · prax jobs")
    return 0 if not report.failed else 1
