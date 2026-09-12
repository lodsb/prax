---
description: File this session's decisions and findings on the project's page in the prax library
argument-hint: "[what to remember]"
---

Keep what this session decided or found, on the project's page.

1. The project's name: `.prax-project`'s `name:`, else the root
   directory's name; the page is `project-<name>`. If the page does not
   exist, create it with `write_page(kind="project")` from one line
   about the project, then continue.
2. Gather from this conversation: decisions made and why, findings
   worth keeping, open questions, and anything the user asked to
   remember (`$ARGUMENTS` first, when given). Leave out routine
   mechanics, code that lives in git, and anything the user would not
   want written down.
3. `append_page(slug="project-<name>", section=…, heading="<today's
   date> — <one line>")`: a short Markdown section — decisions as a
   list, each with its reason; findings with `doc:<id>` when they came
   from the library; open questions last.
4. For a fact the library should hold as a fact (this project uses
   that tool, follows that design, was built with that component), one
   `link(...)` each against the ontology's types, with the project's
   page or the source document as `source_doc`. Only what the session
   actually established.
5. Tell the user what was written, in two or three lines, and where.
