# Clients

Everything here talks to the door over HTTP and nothing else: no client
opens the database, imports `prax.store`, or knows where the archive
lives. That is invariant 4 in `CLAUDE.md` — the door is the only writer —
and it is what makes the same client work against the door on this
machine and against the one on the board:

    prax --door http://board:8000 status

| | What | Talks to |
|---|---|---|
| `cli/` | the `prax` command: search, ask, add, show, status, work, serve (`docs/howto.md` 4a) | the door, through `prax.client` |
| `browser-extension/` | the browser extension: send a page, a PDF or a link to the library (`docs/extension.md`) | `/ingest/html`, `/ingest/file`, `/ingest/url` |
| `claude-plugin/` | the Claude Code plugin: the MCP server registered for every session, a skill saying when to use the library, `/prax:scope`, `/prax:research`, `/prax:remember`, `/prax:sync`, a session-end hook (`docs/claude-workflow.md`) | the door, through `prax.mcp_server` and the `prax` command |

Two more clients live outside this folder because they ship inside the
package: `prax.mcp_server` (the tools Claude Code sees, also a proxy of
the door) and `prax.worker` (the machine with the models, which fetches
work and posts results). The web UI in `src/prax/ui/` is served by the
door itself.

Licensing: the CLI is MIT with the rest of the repository;
`browser-extension/` is AGPL-3.0 (`browser-extension/LICENSE`) because it vendors
SingleFile for page snapshots.
