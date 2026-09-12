# prax for Claude Code

The library in every session. The plugin registers the `prax` MCP
server (the door's tools: search, read, ask, the graph, pages,
captures), adds a skill that says *when* an agent should reach for the
library and how to cite and write back, four commands, and a
session-end hook that keeps a project's documentation in the library.

## Install

The door must be running somewhere (`prax serve`, on this machine or on
the board) and a Python with prax installed must be reachable:

    export PRAX_PYTHON=/path/to/prax/.venv/bin/python   # Windows: I:\proj\prax\.venv\Scripts\python.exe
    export PRAX_DOOR=http://127.0.0.1:8000               # or the board
    export PRAX_TOKEN=…                                  # when the door asks for one

Set those in the environment Claude Code starts from (the user
environment on Windows, the shell profile elsewhere). Then:

    claude plugin marketplace add lodsb/prax
    claude plugin install prax@prax

From a checkout instead of GitHub (a private fork, or before a push),
the marketplace is the repository's path:

    claude plugin marketplace add /path/to/prax        # Windows: I:\proj\prax
    claude plugin install prax@prax

Restart Claude Code: plugins, MCP servers and their tools are read at
startup only, so a fresh install or an update (`claude plugin update
prax`) shows up in the next session, not the current one. Then
`/prax:scope` in any project says what the library holds for it;
`claude plugin uninstall prax` takes it out again.

## What it adds

| | |
|---|---|
| MCP server `prax` | `search`, `get`, `get_chunk`, `context`, `documents`, `traverse`, `link`, `ask`, `get_page`, `write_page`, `append_page`, `ingest`, `ingest_file`, `capture_url`, `promote`, `set_domains` |
| skill `prax` | when to reach for the library (a question about what you read or decided, a design decision in a domain it holds, "did I…", "save this"), how to read it (search, read the passage, cite `doc:<id>`), how to write back (pages, edges, captures) |
| `/prax:scope [name]` | the project's page, its documents in the library, what else bears on it — at the start |
| `/prax:research <question>` | search, read, answer with citations; offers to keep the answer as a page |
| `/prax:remember [what]` | this session's decisions and findings appended to the project's page, with edges for the facts |
| `/prax:sync [--dry-run]` | the project's `.md`/`.rst`/`.txt`/`.adoc` files into the library, keyed by path |
| hook `SessionEnd` | runs the sync for a project that has a `.prax-project` file; silent otherwise |

## Opting a project in

A `.prax-project` file in the project root names it and says which
ontology modules its documents are read against:

    name: synth-firmware
    domains: [workshop, studio]
    include: ["README.md", "docs/**/*.md", "adr/*.md"]

With it, the session-end hook sends the project's docs every time a
session ends; without it, `/prax:sync` does the same on request.

The project's page in the library is `project-<name>`. Page slugs are
slugified — lower case, letters and digits, hyphens for the rest — so
`project/synth-firmware` and `Project Synth Firmware` both become
`project-synth-firmware`; `/prax:remember` appends to that page and
`context(slug="project-synth-firmware")` reads it.

## Notes

- Everything the agent writes is stamped `agent` (edges' producer,
  pages' author), so a session's work can be found and retired as a
  unit.
- When Claude Code is opened in the prax repository itself, the repo's
  own `.mcp.json` registers a second `prax` server with the same tools;
  disable one of them there (`/mcp`), or the agent sees each tool twice.
- The hook runs `python`; when that interpreter has no prax, `PRAX_PYTHON`
  must name one that does, or the hook says so on stderr and does
  nothing. A door that is down is a warning, never a failed session.
- The plugin packages the client side only. The door, the store and the
  models are prax's own business (`docs/howto.md`).
