---
description: What the library holds for this project — its page, its documents, the domain — before the work starts
argument-hint: "[project name]"
---

Establish the project's scope from the prax library, then summarise it
in a few lines for the user.

1. Find the project's name: `$ARGUMENTS` if given; else ask prax:
   `sync_project(root=".")` is a dry run that writes nothing, and its
   answer carries the `name` and `settings.domains` prax keeps for this
   working copy (a first sync would use the folder's name). An older
   `.prax-project` file, if there is one, says the same.
2. `context(slug="project-<name>")` — the project's page and members.
   If there is no such page, say so and offer to create one with
   `write_page(slug="project-<name>", kind="project", text=…)` from
   the README's first paragraph; do not create it unasked.
3. `documents(tag="project:<name>", limit=30)` — the project's own
   documents in the library (its synced notes).
4. `search(<the project's subject>, domain=<its domain>, limit=8)` —
   what else the library holds that bears on it.
5. Report: the page (or its absence), how many project documents and
   when the latest arrived, the five most relevant other documents with
   their `doc:<id>`, and the domain the project reads against. If the
   project was never synced (the dry run's plan is all `add`), offer
   `/prax:sync`, and leave the choice to the user.

Keep it short: this is orientation, not a report.
