---
description: Answer a question from the prax library with citations, and offer to keep the answer as a page
argument-hint: "<question>"
---

Answer this from the library, with citations: $ARGUMENTS

1. Decide the domain from the project (`.prax-project`, or the subject
   of the question); leave it unset when unsure.
2. `search(question, domain=…, limit=10)`; then `ask(question,
   domain=…)` for the bundle of passages and graph facts. Read the
   passages that matter with `get_chunk` before relying on them; follow
   an entity with `traverse` when the graph would add a fact the
   passages lack.
3. Answer in the user's terms, grounded in what was read. Every claim
   that rests on the library cites `doc:<id>` (and the passage number
   from `ask` when it helps). Say plainly what the library does not
   cover and what comes from general knowledge instead.
4. If the answer is worth keeping, offer — do not do it unasked — to
   write it as a page: `write_page(slug="<subject>", kind="topic",
   text=…)` with the citations kept, or `append_page` to the project's
   page when the project is known.
