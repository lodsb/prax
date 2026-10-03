---
description: Send this project's documentation files to the prax library (README, docs, notes; git-tracked only, keyed by remote and path, a rewritten note replaces itself)
argument-hint: "[--apply] [--auto]"
---

Send the project's written knowledge to the library.

1. Ask for the plan first: call the MCP tool
   `sync_project(root=".")` (a dry run by default). If the tool is not
   available, run `prax sync .` with the Bash tool (or `"$PRAX_PYTHON"
   -m prax_cli.main sync .` when `prax` is not on PATH).
2. Show the user the plan in a few lines: the counts, the paths that
   would be added, refreshed or moved, and what was skipped and why
   (build and vendored folders, files that are not documents). On a
   first sync, ask for the project's name and its ontology modules
   (`domains`, for example `[workshop]`) if the folder's name or the
   defaults would be wrong.
3. With the user's go-ahead, apply it: `sync_project(root=".",
   dry_run=false, name=…, domains=[…])`, or `prax sync . --apply
   --name … --domain …`. The settings are kept in prax from then on, so
   later syncs need only `dry_run=false` (`--apply`). With `--apply` in
   `$ARGUMENTS`, skip the confirmation.
4. If the user wants the session-end hook to sync this project on its
   own, pass `auto_sync=true` (`--auto`). Nothing in the repository
   changes; the switch is in prax's manifest.
5. Report: the counts, the project page (`project-<name>`), and that a
   worker (`prax work --watch`) reads what arrived.

What goes: `.md`, `.rst`, `.txt`, `.adoc` files git tracks, outside
build and vendored folders (`build/`, `_deps/`, `CMakeFiles/`,
`*-subbuild/`, `node_modules/`, virtual environments). Source code
does not; git keeps it. A subdirectory of a repository is a project of
its own. A document that is gone from the working copy is reported,
never retired by a sync.
