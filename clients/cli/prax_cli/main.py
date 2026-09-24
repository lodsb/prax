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
from prax.steps import STEPS, WATCHED_STEPS

from . import importing, library, out, running
from . import up as up_cmd

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
  running it   up, status, jobs, readings, inbox, work, heal, maintain, questions,
               resolve, backup, serve, doctor, models
"""


def _door_of(a: Any) -> Door:
    return Door(a.door, token=a.token, name="cli")


def _wait_opts(s: argparse.ArgumentParser) -> None:
    s.add_argument(
        "--wait",
        action="store_true",
        help="block until no request waits (exit 2 on --timeout)",
    )
    s.add_argument("--timeout", type=float, metavar="MIN", help="give up waiting after")
    s.add_argument(
        "--every", type=float, default=30, metavar="SEC", help="how often to look"
    )


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
            " door's own model surfs the library first — searches again,"
            " reads on, walks the graph, drops what is beside the point — for"
            " as many steps as --steps allows (0: one shot from the first"
            " search), then answers from what it kept and cites it. The"
            " trail of steps is printed as it happens."
        ),
        epilog=(
            "examples:\n"
            "  prax ask --answer how does a feedback delay network work\n"
            "  prax ask --answer --steps 12 --tokens 6000 what does ADAA do"
            " to a stateful nonlinearity\n"
            "  prax ask --answer --steps 0 quick question   # no surfing"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("question", nargs="+")
    s.add_argument(
        "-a", "--answer", action="store_true", help="let the door's model answer"
    )
    s.add_argument(
        "-n", "--limit", type=int, default=8, help="how many passages per search"
    )
    s.add_argument(
        "--steps",
        type=int,
        help="surfing steps before the answer (the host's default; 0: none)",
    )
    s.add_argument(
        "--tokens",
        type=int,
        help="the reading budget of the steps in tokens (the model's default)",
    )
    s.add_argument("--doctype", help="pdf, web, image, text, note or page")
    s.add_argument("--save", metavar="PAGE", help="keep the answer on a page")
    s.add_argument(
        "--stand",
        action="store_true",
        help="keep it as a standing question: a page the door asks again when"
        " the library learns something (prax questions)",
    )
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
            " document each. citations: the citation network from OpenAlex or"
            " Crossref, fetched by the door for the documents not looked up yet."
            " zotero: a Zotero data directory, planned here over a copy of its"
            " database, each document sent to the door. Every run skips what the"
            " library already has."
        ),
        epilog=(
            "examples:\n"
            "  prax import github octocat --domain workshop\n"
            "  PRAX_GITHUB_TOKEN=… prax import github        # your own stars\n"
            "  prax import chat 'Telegram Desktop/result.json' --links\n"
            "  prax import links bookmarks.html pocket.csv medium-export.zip\n"
            "  prax import links reading.txt --dry-run\n"
            "  prax import project . --domain workshop --refresh\n"
            "  prax import claude . --since 2026-09-01 --dry-run\n"
            "  prax import citations --source crossref -n 50\n"
            "  prax import zotero ~/Zotero --dry-run"
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
        " that grew; citations: the fetched ones again)",
    )
    s.add_argument(
        "--links", action="store_true", help="chat: capture the links sent, too"
    )
    s.add_argument("--limit", type=int, metavar="N", help="send at most N")
    s.add_argument("--dry-run", action="store_true", help="list what would be added")
    s.add_argument("--quiet", action="store_true", help="only failures are printed")
    s.add_argument("--token-github", metavar="TOKEN", help=argparse.SUPPRESS)
    s.add_argument(
        "--source",
        choices=("openalex", "crossref"),
        help="citations: where to look (default openalex)",
    )
    s.add_argument("--ids", nargs="+", type=int, metavar="ID", help="citations: these")
    s.add_argument(
        "--resolve-titles",
        action="store_true",
        help="citations: documents without a DOI too, by exact title",
    )
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
        default=",".join(WATCHED_STEPS),
        help="promote (the paid pass over flagged documents), typing (untyped"
        " review items to the typing model) and adjudicate (the likely pairs"
        " of entity resolution to the adjudicate model, paid: --spend) only"
        " when named; resolve is the likely tier's pairs, a type's names a week",
    )
    for step in STEPS:
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
    s.add_argument(
        "--nightly",
        metavar="HH:MM",
        help="with --watch: once past this hour each day, one bounded pass over"
        " the whole library (the backlog and the stale texts)",
    )
    s.add_argument(
        "--nightly-limit", type=int, default=100, help="documents a step, that pass"
    )
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
        "up",
        help="keep this host's door, worker and model server running",
        description=(
            "What run: in prax.yaml names — the door, the worker, llama-server —"
            " started in order, watched, restarted when they die, stopped in"
            " order. In the foreground here, or detached with -d; --install"
            " makes it start when you log in. Nothing here needs a terminal"
            " to stay open."
        ),
        epilog=(
            "examples:\n"
            "  prax up                  here, in this terminal (ctrl-c stops all)\n"
            "  prax up -d               detached: survives this terminal\n"
            "  prax up --status\n"
            "  prax up --restart door   after a code change\n"
            "  prax up --stop llama-server   the card free for a while;"
            " --start brings it back\n"
            "  prax up --stop\n"
            "  prax up --install        start at login (Task Scheduler, systemd,"
            " launchd; with a tray icon on a desktop)\n"
            "  prax up --tray           the same, with a tray icon as its face"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("-d", "--detach", action="store_true", help="run in the background")
    s.add_argument(
        "--tray",
        action="store_true",
        help="run with a tray icon: the roles' state, open prax, restart, stop"
        " (pip install prax[tray])",
    )
    s.add_argument(
        "--data-dir", metavar="DIR", help="the store (default: $PRAX_DATA_DIR)"
    )
    what = s.add_mutually_exclusive_group()
    what.add_argument("--status", action="store_true", help="what is running")
    what.add_argument(
        "--stop",
        nargs="?",
        const="all",
        metavar="ROLE",
        help="stop everything, in order; with a role, that one until --start",
    )
    what.add_argument(
        "--start", metavar="ROLE", help="start a stopped role again (or all)"
    )
    what.add_argument("--restart", metavar="ROLE", help="restart one role, or all")
    what.add_argument(
        "--swap",
        metavar="ROLE",
        help=(
            "give this role the card its group shares: what holds it stops"
            " unless both fit, and it comes back when nothing waits"
        ),
    )
    what.add_argument(
        "--unswap",
        nargs="?",
        const="all",
        metavar="GROUP",
        help="give a borrowed resource back now (default: every group)",
    )
    what.add_argument(
        "--install", action="store_true", help="start prax up when you log in"
    )
    what.add_argument("--uninstall", action="store_true", help="remove that entry")
    s.set_defaults(func=up_cmd.up, needs_door=False)

    s = sub.add_parser(
        "tray",
        help="a tray icon for the prax up already running (or to start one)",
        description=(
            "The mark in the tray: a red dot when a role is down or nothing"
            " runs; the menu opens prax, restarts or stops a role, opens the"
            " logs. Attaches to the supervisor running here; `prax up --tray`"
            " runs both together, and `prax up --install` registers that at"
            " login on a desktop. Needs pystray and Pillow: pip install"
            " prax[tray]."
        ),
    )
    s.add_argument(
        "--data-dir", metavar="DIR", help="the store (default: $PRAX_DATA_DIR)"
    )
    s.set_defaults(func=up_cmd.tray, needs_door=False)

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
            "  prax reread --extractor marker --thin       scans read as their cover\n"
            "  prax reread --extractor vision-pages --mode scans --unreadable\n"
            "  prax reread --extractor figures --mode all --mime application/pdf\n"
            "  prax reread --extractor pymupdf4llm --text-source pymupdf4llm/1.28.2\n"
            "  prax reread --extractor trafilatura --ids 9706 9712"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("--extractor", required=True, help="one of the readings")
    s.add_argument(
        "--mode",
        help="vision-pages: scans|all; figures: captioned|all; OCR: a language"
        " (ch, en, latin, arabic, cyrillic…)",
    )
    s.add_argument("--ids", nargs="+", type=int, metavar="ID")
    s.add_argument("--mime", help="a type or a prefix: application/pdf, image/")
    s.add_argument(
        "--text-source", help="a stamp prefix: the documents an old extractor read"
    )
    s.add_argument("--title", help="words the title contains")
    s.add_argument(
        "--unreadable",
        action="store_true",
        help="the documents nothing here could read (scans without a text layer)",
    )
    s.add_argument(
        "--doctype",
        choices=("pdf", "web", "video", "image", "text", "note", "page"),
        help="documents of that kind (the search's doctype filter)",
    )
    s.add_argument(
        "--unpolished",
        action="store_true",
        help="videos with an automatic transcript the polish has not written yet",
    )
    s.add_argument(
        "--thin",
        nargs="?",
        const=100,
        type=int,
        metavar="BYTES",
        help="PDFs of five pages or more with under BYTES of text a page"
        " (100: a scan whose text layer is its cover's)",
    )
    s.add_argument("-n", "--limit", type=int, help="at most this many")
    s.add_argument(
        "--read-figures",
        action="store_true",
        help="documents whose figures a model has read (with --mode again)",
    )
    s.add_argument(
        "--unread-figures",
        action="store_true",
        help="documents holding a figure nobody has read",
    )
    s.add_argument(
        "--bare-captions",
        action="store_true",
        help="documents holding a caption with no picture behind it: what"
        " figure-crops renders off the page",
    )
    s.add_argument(
        "--read-formulas",
        action="store_true",
        help="documents whose display equations a model has read (--mode again)",
    )
    s.add_argument(
        "--unread-formulas",
        action="store_true",
        help="documents holding a display equation nobody has read",
    )
    s.add_argument(
        "--maths",
        type=float,
        metavar="DENSITY",
        help="the mathematical documents: at least this many references to"
        " numbered equations per 10,000 characters (6 is a paper with"
        " equations, 10 a mathematical one); with --mime application/pdf"
        " and --extractor marker, the evening that reads the mathematics",
    )
    _wait_opts(s)
    s.add_argument("--dry-run", action="store_true", help="count, ask for nothing")
    s.set_defaults(func=running.reread, needs_door=True)

    s = sub.add_parser(
        "readings",
        parents=[door_opts, as_json],
        help="the reading queue: what waits, per extractor; --wait blocks",
        description=(
            "What the door has been asked to read again and has not yet"
            " (per extractor), and how the recent requests ended. --wait"
            " blocks until nothing waits — the line between the steps of an"
            " evening that swaps the card to marker and back."
        ),
        epilog=(
            "examples:\n"
            "  prax readings\n"
            "  prax readings --wait --extractor marker --timeout 420\n"
            "  prax readings --wait                         the follow-ups too"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("--extractor", help="one reading only")
    s.add_argument("-n", "--limit", type=int, default=25, help="recent rows")
    _wait_opts(s)
    s.set_defaults(func=running.readings, needs_door=True)

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
        "maintain",
        parents=[door_opts, as_json],
        help="what the store does to itself: acronyms, fields, domains, duplicates,"
        " references",
        description=(
            "The maintenance pass, a job on the door: the acronyms table rebuilt"
            " from every text, the document retrieval fields, the domain rules"
            " over documents without a set, duplicate captures retired. No"
            " model, no decision; the nightly task runs it after the worker's."
        ),
        epilog=("examples:\n  prax maintain\n  prax maintain --only acronyms"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument(
        "--only",
        help="a comma-separated subset of: acronyms, fields, domains, dedupe, review,"
        " references, fts",
    )
    s.add_argument(
        "--rechunk",
        action="store_true",
        help="also rebuild every chunk from its text (after a chunker change)",
    )
    s.set_defaults(func=running.maintain, needs_door=True)

    s = sub.add_parser(
        "questions",
        parents=[door_opts, as_json],
        help="the standing questions: what each remembers, what is new; ask again",
        description=(
            "A standing question is a page the door keeps answered: when a"
            " document that arrived since ranks for the question, shares two"
            " of the answer's entities, or a source was read again, the"
            " question is asked again and the answer is a new revision, the"
            " sections you added under it kept. `prax ask --stand` starts"
            " one; an ask block in a page of your own (<!-- prax:ask id=q1"
            ' "…" --> … <!-- /prax:ask id=q1 -->) is one too, listed as'
            " slug#id. schedule: questions: HH:MM in prax.yaml runs the check"
            " daily, with the day's briefing (what arrived)."
        ),
        epilog=(
            "examples:\n"
            "  prax questions                      each question, and what is new\n"
            "  prax questions --ask                ask again what is due (a job)\n"
            "  prax questions --ask q-my-slug --force   one, whatever is new\n"
            "  prax questions --ask notes#q1 --release  a block you edited: anew\n"
            "  prax questions --briefing           the day's page of what arrived"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument(
        "--ask",
        nargs="?",
        const="",
        metavar="SLUG",
        help="ask again what is due (one question by slug, or every one)",
    )
    s.add_argument(
        "--force", action="store_true", help="ask again even when nothing is new"
    )
    s.add_argument(
        "--briefing", action="store_true", help="write the day's briefing page too"
    )
    s.add_argument(
        "--release",
        action="store_true",
        help="an ask block edited by hand: ask it again all the same (with --ask)",
    )
    s.set_defaults(func=running.questions, needs_door=True)

    s = sub.add_parser(
        "resolve",
        parents=[door_opts, as_json],
        help="merge entities that name the same thing",
        description=(
            "Entity resolution: sure candidates (the same name after"
            " normalization, an initials form of one author) and concept/method"
            " twins are merged with --apply; likely ones (close by name"
            " embedding) are listed for you. Merges are pointers, nothing is"
            " deleted, and traverse follows them."
        ),
        epilog=(
            "examples:\n"
            "  prax resolve                      the plan\n"
            "  prax resolve --type author\n"
            "  prax resolve --apply --twins"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.add_argument("--apply", action="store_true", help="merge the sure ones")
    s.add_argument("--type", help="one entity type")
    s.add_argument(
        "--subtypes",
        action="store_true",
        help="merge a name's general type into its specific one (a person"
        " who is an author, a document that is a paper): the ontology's"
        " own hierarchy, never a page or a project of your own",
    )
    s.add_argument(
        "--twins",
        action="store_true",
        help="a concept into the method of the same name",
    )
    s.add_argument(
        "--no-likely",
        action="store_true",
        help="leave out the likely tier (the pairs a worker's resolve step left)",
    )
    s.add_argument("--show", type=int, default=40, help="candidates per tier")
    s.add_argument(
        "--unmerge",
        metavar="RUN",
        help="take a round back: every entity that run folded stands on its"
        " own again and every one it renamed is called what it was called",
    )
    s.set_defaults(func=running.resolve, needs_door=True)

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
    # a title in Arabic, a symbol in a snippet: never a crash for the codepage
    # a pipe or a redirect on Windows would give the output
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
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
    except BrokenPipeError:
        # `prax show 12 | head`: the reader went away, which is not news
        try:
            sys.stdout.close()
        except OSError:
            pass
        os._exit(0)
    except OSError as exc:
        if getattr(exc, "errno", None) == 22 and sys.platform == "win32":
            # a closed pipe on Windows comes back as EINVAL on the write
            os._exit(0)
        out.fail(
            f"no door at {a.door} ({type(exc).__name__})",
            "start one here with `prax serve`, or name another with"
            " --door http://<host>:8000 (or PRAX_DOOR)",
        )
        return 2
    except httpx.HTTPError as exc:
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
