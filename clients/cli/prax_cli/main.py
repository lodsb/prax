"""The `prax` command: one thing to learn for the library on the other end
of the door. Every subcommand is a client call (`prax.client.Door`), so the
same command works against the door on this machine and against the one on
the board: `prax --door http://board:8000 status`.

`PRAX_DOOR` and `PRAX_TOKEN` in the environment stand in for `--door` and
`--token`.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import httpx

from prax.client import Door, DoorError

from . import importing, library, out, running

DEFAULT_DOOR = "http://127.0.0.1:8000"
EPILOG = """\
examples
  prax                             where things stand, and what to type next
  prax search granular synthesis   find documents
  prax ask what is a wave digital filter
  prax add ~/Downloads/paper.pdf   add a file, a folder or a URL
  prax import links bookmarks.html what a service exported
  prax show 4312                   read one
  prax work --watch                keep new documents moving

commands
  everyday     search, ask, add, import, show, open, graph, pages
  running it   status, jobs, inbox, work, heal, backup, serve, doctor, models
"""


def _door_of(a: Any) -> Door:
    return Door(a.door, token=a.token, name="cli")


def build_parser() -> argparse.ArgumentParser:
    door_opts = argparse.ArgumentParser(add_help=False)
    door_opts.add_argument(
        "--door",
        default=os.environ.get("PRAX_DOOR", DEFAULT_DOOR),
        metavar="URL",
        help="the door to talk to (default: $PRAX_DOOR or %(default)s)",
    )
    door_opts.add_argument(
        "--token",
        default=os.environ.get("PRAX_TOKEN") or None,
        metavar="SECRET",
        help="bearer token when the door asks for one ($PRAX_TOKEN)",
    )
    as_json = argparse.ArgumentParser(add_help=False)
    as_json.add_argument(
        "--json", action="store_true", help="print the door's answer as JSON"
    )

    p = argparse.ArgumentParser(
        prog="prax",
        description="Your library: search it, ask it, add to it, keep it running.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="store_true", help="print the version and stop")
    p.add_argument(
        "--door",
        default=os.environ.get("PRAX_DOOR", DEFAULT_DOOR),
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--token", default=os.environ.get("PRAX_TOKEN") or None, help=argparse.SUPPRESS
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")

    # ---- everyday
    s = sub.add_parser(
        "search",
        parents=[door_opts, as_json],
        help="find documents by words, meaning or both",
        description="Find documents. Words you type need no quotes.",
        epilog="example: prax search feedback delay network reverberation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("query", nargs="+", help="what to look for")
    s.add_argument("-n", "--limit", type=int, default=10, help="how many hits")
    s.add_argument(
        "--mode",
        choices=("hybrid", "fts", "vec"),
        default="hybrid",
        help="hybrid (default), fts for exact words, vec for meaning",
    )
    s.add_argument("--kind", help="text, table, figure or code")
    s.add_argument("--doctype", help="pdf, web, image, text, note or page")
    s.add_argument("--domain", help="one ontology module, e.g. research")
    s.set_defaults(func=library.search, needs_door=True)

    s = sub.add_parser(
        "ask",
        parents=[door_opts, as_json],
        help="gather passages for a question (and answer it)",
        description=(
            "Gather the passages that speak to a question. With --answer the"
            " door's own model answers from them and cites what it used."
        ),
        epilog="example: prax ask --answer how does a feedback delay network work",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("question", nargs="+")
    s.add_argument(
        "-a", "--answer", action="store_true", help="let the door's model answer"
    )
    s.add_argument("-n", "--limit", type=int, default=8, help="how many passages")
    s.add_argument("--doctype", help="pdf, web, image, text, note or page")
    s.add_argument("--save", metavar="PAGE", help="keep the answer on a page")
    s.set_defaults(func=library.ask, needs_door=True)

    s = sub.add_parser(
        "add",
        parents=[door_opts],
        help="put a file, a folder, a URL or piped text into the library",
        description=(
            "Add things. Files and folders are uploaded, URLs are fetched by"
            " the door, and '-' takes text from the pipe."
        ),
        epilog=(
            "examples:\n"
            "  prax add paper.pdf notes.md\n"
            "  prax add ~/Downloads/papers --domain research\n"
            "  prax add https://example.org/article\n"
            "  pbpaste | prax add - --title 'a thought'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("what", nargs="+", help="paths, URLs, or - for standard input")
    s.add_argument(
        "--domain",
        action="append",
        metavar="NAME",
        help="ontology module to read it against (repeatable)",
    )
    s.add_argument("--title", help="a title of your own")
    s.add_argument(
        "-r", "--recursive", action="store_true", help="folders: go into subfolders"
    )
    s.set_defaults(func=library.add, needs_door=True)

    s = sub.add_parser(
        "import",
        parents=[door_opts, as_json],
        help="what you keep elsewhere: stars, chats, links, a project's docs,"
        " Claude Code sessions",
        description=(
            "Bring in what you keep elsewhere. github: your starred repositories"
            " (README and facts, one document each). chat: a Telegram Desktop or"
            " sigtop JSON export, a WhatsApp .txt, one document per conversation"
            " and month. links: browser bookmarks (.html), Pocket or Raindrop"
            " (.csv), a text file of URLs, or Medium's export (.zip); the door"
            " fetches each link. project: the documentation files under a"
            " directory (.md, .rst, .txt, .adoc; a .prax-project file names the"
            " project and its modules), keyed by path so --refresh replaces a"
            " rewritten note. claude: Claude Code sessions of a project (what was"
            " said, not what was run: tool calls and results are left out), one"
            " document each. Every run skips what the library already has."
        ),
        epilog=(
            "examples:\n"
            "  prax import github octocat --domain workshop\n"
            "  PRAX_GITHUB_TOKEN=… prax import github        # your own stars\n"
            "  prax import chat 'Telegram Desktop/result.json' --links\n"
            "  prax import links bookmarks.html pocket.csv medium-export.zip\n"
            "  prax import links reading.txt --dry-run\n"
            "  prax import project . --domain workshop --refresh\n"
            "  prax import claude . --since 2026-09-01 --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("what", choices=importing.WHAT, metavar="SOURCE")
    s.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="export files (chat, links); a directory (project, claude); a user name"
        " (github)",
    )
    s.add_argument(
        "--since", metavar="DATE", help="claude: sessions that ended after DATE"
    )
    s.add_argument(
        "--name", metavar="NAME", help="project: its name (default: the directory's)"
    )
    s.add_argument(
        "--domain",
        action="append",
        metavar="NAME",
        help="ontology module to read them against (repeatable)",
    )
    s.add_argument("--tag", action="append", metavar="NAME", help="a tag for each")
    s.add_argument(
        "--refresh",
        action="store_true",
        help="re-read what changed at the source (a repository pushed to, a month"
        " that grew)",
    )
    s.add_argument(
        "--links", action="store_true", help="chat: capture the links sent, too"
    )
    s.add_argument("--limit", type=int, metavar="N", help="send at most N")
    s.add_argument("--dry-run", action="store_true", help="list what would be added")
    s.add_argument("--quiet", action="store_true", help="only failures are printed")
    s.add_argument("--token-github", metavar="TOKEN", help=argparse.SUPPRESS)
    s.set_defaults(func=importing.import_, needs_door=True)

    s = sub.add_parser(
        "show",
        parents=[door_opts, as_json],
        help="read a document in the terminal",
        description="Print one document: what it is, and its text.",
        epilog="example: prax show 4312 --chars 4000",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("doc_id", type=int, metavar="DOC")
    s.add_argument("--chars", type=int, default=4000, help="how much text")
    s.add_argument("--offset", type=int, default=0, help="where to start")
    s.set_defaults(func=library.show, needs_door=True)

    s = sub.add_parser(
        "open",
        parents=[door_opts],
        help="open a document in the browser",
        description="Open a document in the web UI, or its original file.",
        epilog="example: prax open 4312 --original",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("doc_id", type=int, metavar="DOC")
    s.add_argument("--original", action="store_true", help="the PDF or snapshot itself")
    s.set_defaults(func=library.open_doc, needs_door=True)

    s = sub.add_parser(
        "graph",
        parents=[door_opts, as_json],
        help="what the graph knows around a name",
        description="Follow the knowledge graph one or two hops from a name.",
        epilog='example: prax graph "short-time Fourier transform" --hops 2',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("entity", help="an entity name as the extraction wrote it")
    s.add_argument("--hops", type=int, default=1, choices=(1, 2))
    s.add_argument("-n", "--limit", type=int, default=25, help="rows to print")
    s.set_defaults(func=library.graph, needs_door=True)

    s = sub.add_parser(
        "pages",
        parents=[door_opts, as_json],
        help="the notes kept in the library",
        description="List the library's pages, or read one.",
        epilog="example: prax pages fdn-notes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("slug", nargs="?", help="a page to read")
    s.set_defaults(func=library.pages, needs_door=True)

    # ---- running it
    s = sub.add_parser(
        "status",
        parents=[door_opts, as_json],
        help="what the library holds and what is going on",
        description="Everything in one screen: the store, the graph, the host.",
    )
    s.set_defaults(func=running.status, needs_door=True)

    s = sub.add_parser(
        "jobs",
        parents=[door_opts, as_json],
        help="passes running now and lately",
        description="What the door and its workers are doing.",
    )
    s.add_argument("-n", "--limit", type=int, default=10, help="how many past jobs")
    s.set_defaults(func=running.jobs, needs_door=True)

    s = sub.add_parser(
        "inbox",
        parents=[door_opts, as_json],
        help="what has come in lately and what still waits",
        description="Captures: uploads, sent pages, fetched URLs, dropped files.",
    )
    s.add_argument("-n", "--limit", type=int, default=15)
    s.set_defaults(func=running.inbox, needs_door=True)

    s = sub.add_parser(
        "work",
        parents=[door_opts],
        help="do the model work the door hands out",
        description=(
            "Parse, title, extract and embed what is waiting, with this"
            " machine's models. Never opens the database, never spends money"
            " unasked."
        ),
        epilog=(
            "examples:\n"
            "  prax work                       one pass, then stop\n"
            "  prax work --watch               keep going (the usual way)\n"
            "  prax work --scope all --steps extract -n 20   a backlog pass\n"
            "  prax work --steps promote --spend   the paid pass over flagged documents"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("--watch", action="store_true", help="keep going")
    s.add_argument("--interval", type=float, default=20.0, metavar="SECONDS")
    s.add_argument(
        "--scope",
        choices=("captures", "all"),
        default="captures",
        help="captures (default) or the whole library",
    )
    s.add_argument(
        "--steps",
        default="parse,titles,extract,embed",
        help="promote (the paid pass over flagged documents) only when named",
    )
    for step in ("parse", "titles", "extract", "promote", "embed"):
        s.add_argument(
            f"--no-{step}", action="store_true", help=f"skip the {step} step"
        )
    s.add_argument(
        "--spend",
        action="store_true",
        help="let the promote step run its paid model: money is spent",
    )
    s.add_argument("-n", "--limit", type=int, default=10, help="documents per batch")
    s.add_argument("--workers", type=int, default=3, help="parallel model calls")
    s.add_argument(
        "--also", action="append", metavar="FOLDER", help="another drop folder to send"
    )
    s.add_argument("--domains", help="domains for files that name none")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=running.work, needs_door=False)

    s = sub.add_parser(
        "serve",
        help="run the door on this machine",
        description="Start the HTTP door and the web UI.",
        epilog="example: prax serve --host 0.0.0.0 --port 8000",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true", help="restart on code changes")
    s.add_argument("--ssl-certfile", help="serve HTTPS with this certificate (PEM) …")
    s.add_argument("--ssl-keyfile", help="… and this private key (PEM)")
    s.set_defaults(func=running.serve, needs_door=False)

    s = sub.add_parser(
        "reread",
        parents=[door_opts, as_json],
        help="ask for a named extractor over a selection of documents",
        description=(
            "A reading request on every document selected — the same as"
            " 'read again…' on a page, for many at once. A worker drains them"
            " (a paid model is refused). --dry-run counts."
        ),
        epilog=(
            "examples:\n"
            "  prax reread --extractor pymupdf4llm-ocr --unreadable   OCR, the scans\n"
            "  prax reread --extractor vision-pages --mode scans --unreadable\n"
            "  prax reread --extractor figures --mode all --mime application/pdf\n"
            "  prax reread --extractor pymupdf4llm --text-source pymupdf4llm/1.28.2\n"
            "  prax reread --extractor trafilatura --ids 9706 9712"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("--extractor", required=True, help="one of the readings")
    s.add_argument("--mode", help="vision-pages: scans|all; figures: captioned|all")
    s.add_argument("--ids", nargs="+", type=int, metavar="ID")
    s.add_argument("--mime", help="a type or a prefix: application/pdf, image/")
    s.add_argument(
        "--text-source", help="a stamp prefix: the documents an old extractor read"
    )
    s.add_argument(
        "--unreadable",
        action="store_true",
        help="the documents nothing here could read (scans without a text layer)",
    )
    s.add_argument("-n", "--limit", type=int, help="at most this many")
    s.add_argument("--dry-run", action="store_true", help="count, ask for nothing")
    s.set_defaults(func=running.reread, needs_door=True)

    s = sub.add_parser(
        "heal",
        parents=[door_opts, as_json],
        help="find what goes wrong often, and repair it",
        description=(
            "The store's ailments: entities named after the prompt, edges"
            " from a thing to itself, edges of a retired duplicate, review"
            " items nobody can resolve. Looks only, until --apply."
        ),
        epilog=(
            "examples:\n"
            "  prax heal                              what is wrong\n"
            "  prax heal --apply                      repair all of it\n"
            "  prax heal --check self-edges --apply   one kind"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument(
        "--check",
        action="append",
        metavar="NAME",
        help="one ailment by name (repeatable); all of them by default",
    )
    s.add_argument(
        "--apply", action="store_true", help="repair, instead of only looking"
    )
    s.set_defaults(func=running.heal, needs_door=True)

    s = sub.add_parser(
        "backup",
        parents=[door_opts, as_json],
        help="copy the store to a directory on the door's host",
        description=(
            "The door copies its store: the database as one consistent snapshot,"
            " the vector indexes, the config, and the archive files the copy does"
            " not have yet (they are named by hash and never change, so a nightly"
            " run copies only what the day added). The directory is on the door's"
            " machine; the copy is a store of its own."
        ),
        epilog=(
            "examples:\n"
            "  prax backup D:/prax-backup\n"
            "  prax backup                 # the door's paths.backup setting\n"
            "  prax backup E:/prax-db --no-archive   # what cannot be rebuilt, a few GB"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument(
        "dest",
        nargs="?",
        metavar="DIR",
        help="where; default: paths.backup in prax.yaml",
    )
    s.add_argument(
        "--no-archive",
        action="store_true",
        help="the database, the indexes and the config only — not the originals",
    )
    s.set_defaults(func=running.backup, needs_door=True)

    s = sub.add_parser(
        "doctor",
        parents=[door_opts],
        help="check the door, the store, the models and this machine",
        description="A check-up when something feels wrong.",
    )
    s.set_defaults(func=running.doctor, needs_door=True)

    s = sub.add_parser(
        "models",
        help="which model does which step, and fetch one",
        description="What prax.yaml resolves for each step.",
        epilog="example: prax models fetch server-35b",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument(
        "fetch",
        nargs="?",
        metavar="fetch NAME",
        help="'fetch' followed by a model name downloads its file",
    )
    s.add_argument("name", nargs="?", help=argparse.SUPPRESS)
    s.set_defaults(func=running.models, needs_door=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not args:  # `prax` alone: the same door every command would use
        bare = argparse.Namespace(
            door=os.environ.get("PRAX_DOOR", DEFAULT_DOOR),
            token=os.environ.get("PRAX_TOKEN") or None,
        )
        return running.overview(_door_of(bare))
    a = parser.parse_args(args)
    if getattr(a, "version", False):
        return running.print_version()
    if not getattr(a, "func", None):
        parser.print_help()
        return 0
    if a.command == "import":
        a.user = a.files[0] if a.what == "github" and a.files else None
        if a.what not in ("github", "project", "claude") and not a.files:
            out.fail("which files?", f"prax import {a.what} FILE…")
            return 2
    if a.command == "models":  # "prax models fetch <name>" reads better than a flag
        if a.fetch == "fetch":
            a.fetch = a.name
            if not a.fetch:
                out.fail("which model?", "prax models  lists the names")
                return 2
        elif a.fetch:
            out.fail(f"unknown word {a.fetch!r}", "did you mean: prax models fetch …")
            return 2
    try:
        if a.needs_door:
            return int(a.func(_door_of(a), a) or 0)
        return int(a.func(a) or 0)
    except KeyboardInterrupt:
        return 130
    except DoorError as exc:
        if exc.status in (401, 403):
            out.fail(
                "the door refused the request: it wants a bearer token",
                "set PRAX_TOKEN in the environment, or pass --token",
            )
        elif exc.status == 404 and (exc.detail or "").lower() == "not found":
            out.fail(
                f"the door at {a.door} does not know that request",
                "it is probably running older code than this command: restart"
                " it (prax serve)",
            )
        elif exc.status == 404:
            out.fail(exc.detail or "not found")
        else:
            out.fail(f"the door answered {exc.status}: {exc.detail}")
        return 1
    except (httpx.HTTPError, OSError) as exc:
        out.fail(
            f"no door at {a.door} ({type(exc).__name__})",
            "start one here with `prax serve`, or name another with"
            " --door http://<host>:8000 (or PRAX_DOOR)",
        )
        return 2
    except Exception as exc:
        out.fail(f"{type(exc).__name__}: {exc}")
        if os.environ.get("PRAX_DEBUG"):
            raise
        out.hint("  PRAX_DEBUG=1 shows the whole error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
