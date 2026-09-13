# Ontology v7: a first stable shape

2026-09-13. Core 1 → 2, research 6 → 7, studio 3 → 4; composed version
`core2+craft1+kitchen2+research7+studio4+workshop2`. Migration 0012.

## Why now

After the second and third batches of typing rules, research v6 and the
model typing pass, the review queue had gone from 25,827 to 3,706 open
items, and what was left had shapes rather than volume: 1,428 items
with a relation no module knows, 234 affiliations between two
organizations, 394 untyped `part_of`. The relations the models kept
asking for, by count: `is_a` 61, `located_in` 57, `role` 34, `author`/
`author_of` 62, `published_by` 27, `contains`/`is_part_of` 40,
`founded_by` 15. Three of those are real gaps; the rest are either
classification (`is_a`: a type, not a relation), a reversed name for a
relation that exists, or too small to matter yet.

## The changes

**Core 2** — what every kind of document and organization shares came to
the module every other one requires:

- **`authored_by`** moved here from research (domain `document`, range
  `person, organization`). Studio had `written_by` for the same thing;
  research's `author` is a person, so it passes as before. `written_by`,
  `author`, `author_of`, `authors` are aliases.
- **`part_of`** moved here from research and widened: domain and range
  `document, organization`. A chapter in a book, a paper on a project's
  reading list — and a lab in a university, a subsidiary in a group,
  which was the whole of the organization-to-organization "affiliation"
  the queue held. `is_part_of` is an alias.
- **`located_in`** (new): `organization, event` → `place`. "Native
  Instruments GmbH located_in Germany", "Glyndŵr University located_in
  Wrexham". Aliases `located_at`, `based_in`.
- **`published_by`** (new): `document` → `organization`, the publisher
  or issuing body — Springer, O'Reilly, the manufacturer behind a manual
  — as distinct from `published_in`, the journal or conference. Alias
  `publisher`.

**Research 7** — `authored_by` and `part_of` removed (they are core's
now); nothing else changed.

**Studio 4** — `written_by` removed; core's `authored_by` holds for a
manual, a schematic, an article, and the name lives on as an alias.

**Migration 0012** renames the five live `written_by` edges and the one
queued item to `authored_by`; their `ontology_version` stamps stay what
they were.

## Not taken, and why

- `is_a` (61): "R-table is_a data structure" is the model typing an
  entity, not relating two. The typing pass is where that belongs.
- `role` (34), `has_grade` (15), `date` (14): administrative documents
  in the private library talking about themselves; not research
  knowledge.
- `founded_by` (15), `contains` (20): too few to shape a relation on;
  they stay as evidence.
- `related_to`: still deliberately absent (v2).

## What it means

- Every document is due for a re-read by its stamp, as after v6; the
  readings under v5 and v6 stand until a re-run is decided. The worker
  re-reads captures on its own as they come due, and the typing rules
  run right after every extraction now, so the queue no longer collects
  what a rule can settle.
- With v7 the library has a first shape that holds for papers, gear,
  recipes and builds alike: who wrote it, where it appeared, who
  published it, where an organization is and what it is part of, all in
  core; each domain adds only what is its own.
