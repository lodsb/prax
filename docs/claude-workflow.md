# prax in a Claude Code workflow

Two directions: what a project learns becomes part of the library, and
a project draws on what the library holds. Both go through the door;
the plugin (`clients/claude-plugin/`) is the packaged form, the pieces
work on their own too.

## The plugin

    export PRAX_PYTHON=/path/to/prax/.venv/bin/python   # the interpreter with prax
    export PRAX_DOOR=http://127.0.0.1:8000               # or the board's door
    claude plugin marketplace add lodsb/prax
    claude plugin install prax@prax

It registers the `prax` MCP server for every session, a skill that says
when to reach for the library and how to cite and write back, four
commands and a session-end hook:

| | |
|---|---|
| `/prax:scope [name]` | at the start: the project's page, its documents in the library, what else bears on it |
| `/prax:research <question>` | search, read, answer with `doc:<id>` citations; offers to keep the answer as a page |
| `/prax:remember [what]` | this session's decisions and findings appended to the project's page, facts as edges |
| `/prax:sync [--dry-run]` | the project's `.md`/`.rst`/`.txt`/`.adoc` files into the library |
| hook `SessionEnd` | runs the sync for a project with a `.prax-project` file |

From a checkout, `claude plugin marketplace add /path/to/prax` in place
of the GitHub name. Restart Claude Code after installing or updating:
plugins, servers and tools are read at startup. Inside the prax
repository itself the repo's `.mcp.json` registers a second `prax`
server; disable one of the two there.

Without the plugin, the same server is one line at user scope —
`claude mcp add --scope user prax -e PRAX_DOOR=… -- <python> -m
prax.mcp_server` — and the skill's guidance goes into the project's
`CLAUDE.md` by hand (the snippets below).

## Project → library

Three levels, from the cheapest up:

1. **The project's own docs.** A `.prax-project` file in the root:

       name: synth-firmware
       domains: [workshop, studio]
       include: ["README.md", "docs/**/*.md", "adr/*.md"]

   Then `prax import project .` (or the hook at session end) sends
   every documentation file, keyed by `<name>/<path>` and versioned by
   its content, so a rewritten note replaces its earlier self
   (`--refresh`) and nothing is sent twice. Source code stays in git;
   what was decided and why is what the library keeps.
2. **The project's page.** `project-<name>` in the wiki, kind
   `project` (slugs are slugified: `Project Synth` and `project/synth`
   both become `project-synth`): the agent appends decisions, findings and open questions
   as it goes (`/prax:remember`), citing the documents they rest on;
   the page is a document — searchable, in the graph, with revisions —
   and the agent appends, never overwrites.
3. **Facts.** What the project uses, follows, was built with: `link`
   edges against the ontology, with the page or the source document as
   `source_doc`. Everything the agent writes is stamped `agent`, so a
   session's work can be found and, if it was wrong, retired as a unit
   (`retire_run`).

## Library → project

The scope knob is the domain: a project about a synthesiser reads
`studio` and `workshop`, not the papers. In a project's `CLAUDE.md`,
when the plugin is not installed:

    ## The library
    This project lives in the `workshop` and `studio` modules of the prax
    library (MCP server `prax`). Before a design decision or a question
    about the domain, `search(query, domain="workshop")`, read the hit with
    `get_chunk`, and cite `doc:<id>`; `ask(question)` for a synthesis.
    Start with `context(slug="project-synth-firmware")`. Keep decisions on
    that page with `append_page`; never store source code in the library.

`context(slug=…)` returns the page and its members, the entities its
edges point at, citations and nearest documents — the orientation a
session needs in one call. `documents(tag="project:<name>")` lists what
the project has synced; `documents(domain=…)` what a module holds.

## What is not there

- The agent story is built for Claude Code; another MCP client gets
  the server and the tools but not the skill and commands.
- The change feed (`GET /changes`) is polling, not push; a workflow
  that should react to new documents polls it or runs on a schedule.
- "Scheduled" means a cron line: `prax backup`, `prax import github`,
  `prax import project ~/work/synth`.
- Whether a project's decisions and requirements deserve ontology types
  of their own (a `project` module: decision, requirement, component)
  is open; pages and tags carry them today, and the review queue will
  say when the models keep wanting to say more.
