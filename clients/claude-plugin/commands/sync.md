---
description: Send this project's documentation files to the prax library (README, docs, notes; keyed by path, a rewritten note replaces itself)
argument-hint: "[--dry-run]"
---

Send the project's written knowledge to the library.

1. Look for `.prax-project` in the project root. If it is missing,
   show the user the file that would name the project, its ontology
   modules and what to include, for example:

       name: <directory name>
       domains: [workshop]
       include: ["README.md", "docs/**/*.md", "adr/*.md"]

   and ask whether to create it; with the file in place the plugin's
   session-end hook syncs on its own from then on. Without it, run
   once with the directory's name.
2. Run, with the Bash tool: `prax import project . --refresh
   $ARGUMENTS` (the `prax` command; if it is not on PATH, `"$PRAX_PYTHON"
   -m prax_cli.main import project . --refresh $ARGUMENTS`). With
   `--dry-run` nothing is sent and the plan is printed.
3. Report the command's summary line: how many documents were added,
   refreshed, already there, or failed, and remind the user that a
   worker (`prax work --watch`) reads what arrived.

What goes: `.md`, `.rst`, `.txt`, `.adoc` files outside `.git`,
`node_modules`, virtual environments, build output and vendored code.
Source code does not; git keeps it.
