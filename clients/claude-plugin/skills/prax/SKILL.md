---
name: prax
description: Use the prax library (the `prax` MCP tools) when a question touches what the user has read, collected or decided; before a design decision in a domain the library holds (research, studio, kitchen, workshop, and the user's own modules); when the user says "did I…", "what do we know about…", "save this", "remember this"; and to file findings back as pages and edges with citations.
---

# Working with the prax library

The library is the user's own: papers, manuals, captured pages, chats,
starred repositories, project notes, and a graph of typed, evidenced
relations extracted from them. It answers through the `prax` MCP tools.
Everything you write into it is stamped `agent`, so it can be found,
inspected and retired as a unit; write freely, but write well.

## When to reach for it

- A question is about a subject the library holds, or about what the
  user read, saved or decided before. Search it before answering from
  general knowledge, and say which is which.
- Before a design decision in a domain the library covers: what the
  papers say, what the manual says, what was tried in an earlier
  project. A `search` with the project's domain costs one call.
- The user says "did I…", "what do we know about…", "have I read…",
  "save this", "remember this", "keep this", "note that".
- At the start of work on a project that has a `.prax-project` file
  (or a `project-<name>` page): read the project's context first.

Do not reach for it to store source code, build output or anything git
already keeps; the library holds what was read and what was decided.

## How to read

1. `search(query, domain=…)` — hybrid search, compact hits with ids;
   `domain` narrows to one module (`research`, `studio`, `kitchen`,
   `workshop`, …); `kind="table"` finds tables, `doctype="page"` the
   wiki. Read the hit before citing it: `get_chunk(chunk_id)` for the
   passage, `get(doc_id, offset, max_chars)` for more of the document.
2. `ask(question)` — the best passage per document plus the graph's
   facts, ready to answer from; cite the passage numbers it gives.
3. `context(doc_id | slug)` — what the library knows around one
   document or page: summary, entities, citations, nearest documents,
   notes, a project page's members. Start a project session with
   `context(slug="project-<name>")`.
4. `traverse(entity, hops)` — the graph around a name: a method's
   papers, a device's manual and components, a recipe's ingredients.
5. `documents(domain | tag | source | title)` — what the library holds
   for a project (`tag="project:<name>"`), a module, or a source.

Cite what you used as `doc:<id>` (and the chunk when it matters);
never invent an id. When the library has nothing on a subject, say so
and answer from general knowledge, marked as such.

## How to write

- A finding worth keeping goes on a page: `write_page(slug, text,
  kind="topic")` for a subject, `kind="project"` for a project's page,
  `kind="addendum"` for a note on one document. `append_page(slug,
  section, heading)` adds to a page; never replace a page a person
  wrote. Write Markdown; cite `doc:<id>` in the text; a `part_of`
  names the project page.
- A fact the library should know as a fact: `link(src, src_type, rel,
  dst, dst_type, source_doc)` against the ontology's types and
  relations, with the document it comes from. Only what a source
  supports; an edge is evidence, not opinion.
- Something read on the web that belongs in the library:
  `capture_url(url)`. A file the user points at: `ingest_file(path)`.
  A note of the user's own words: `ingest(text, title)`.
- A project's own docs need no tool: `/prax:sync` (or the session-end
  hook, once the project has a `.prax-project` file) sends them.

## Commands this plugin adds

- `/prax:scope` — what the library holds for this project, at the start.
- `/prax:research <question>` — search, read, answer with citations,
  offer to keep the answer as a page.
- `/prax:remember` — file this session's decisions and findings on the
  project's page.
- `/prax:sync` — send the project's documentation files to the library.

The door has to be running (`prax serve`, here or on the board;
`PRAX_DOOR` names it). A tool answering `{"error": "the door is not
reachable …"}` means it is not; tell the user rather than retrying.
