# Ontology v8: affiliation beyond persons, and the names the models use

2026-09-22. Core 2 to 3, research 7 to 8; composed version
`core3+craft1+kitchen2+research8+studio4+workshop2`. Migration 0018.

## Why now

After the model typing pass ran over the whole queue for the first time
(3,714 open to 2,668: 860 linked, 82 dropped, 930 misfits), what was
left had two shapes. 1,014 untyped items named relations no module
knows, and most of those were relations that exist under another name:
`is_about`, `proposed`, and the reversed names `mentioned_in`,
`used_in`, `publisher_of`, `has_part`. And 930 typed misfits. Their
largest families were affiliations the ontology refused: organization
to organization (46), organization to paper (39), organization to
place (21), person to place (20), person to concept (16). The decision behind v8 is that affiliation is not a person's
relation alone. An organization is affiliated with a network, a parent
body or a movement, and a person with a school of thought.

## The changes

**Core 3**

- **`affiliated_with`** widened: domain `person, organization`, range
  `organization, concept`. Two peer organizations are affiliated; one
  inside the other is still `part_of` (the typing rules say which, from
  the names). A person's or organization's movement or school of
  thought is a concept on the right.
- **`located_in`** takes a `person` on the left ("based in Berlin").
- **Reversed aliases.** An alias may say the relation runs the other way
  round: `publisher_of: {to: published_by, reversed: true}`,
  `has_part` and `contains` for `part_of`. `Ontology.is_reversed`
  answers, and the typing pass and the rules swap the ends when they
  take the canonical name. (`has_part` yields to studio's own
  `has_part` where studio is loaded; the composition rule that a
  declared name wins holds.)
- `member_of` is an alias of `affiliated_with`.

**Research 8.** Aliases only: `is_about` and `proposed` for `about` and
`proposes`; reversed, `mentioned_in` and `discussed_in` for `mentions`,
`used_in` and `used_by` for `uses`, `cited_by` for `cites`. No type or
relation changed.

**The typing rules.** A typed item is read under its canonical name,
flipped when the alias runs backwards, and linked as it stands when it
fits. An affiliation with a place is `located_in`. An organization
"affiliated with" the paper is the paper's `written_at`. Two
organizations are `part_of` when one is inside the other, else
affiliated. An attribute the model wrote as a relation (`date`,
`language`, `role`, `has_grade`, `document_type`, `has_content`,
`domain`, and the like) is dropped: no relation holds it.

**Migration 0018** moves the extraction stamps from the v7 strings to
the v8 ones (`core2+…research7…` to `core3+…research8…`). The bump only
widens what is accepted and adds names, so every reading made under v7
is valid under v8. What the models parked as unmapped is the queue's
replay to recover, not a re-extraction. The 7,018 documents
still stamped `core1+research5` from before v7 stay due, as they were.
Edges keep the version they were written under.

## Not taken, and why

- `is_a` (64, with `subclass_of` 12 and `instance_of` 9): as in v7, the
  model typing an entity ("KH 120 instance_of loudspeaker"), not
  relating two. The queue keeps them as evidence for a typing pass that
  reads them as types.
- `founded_by` (12), `event` (15), `submitted_to` (10), `issued_to`
  (11): too few to shape a relation on, or the private library's
  administrative documents talking about themselves.
- `related_to`: still deliberately absent (v2).

## What to run

    prax maintain --only review        # replay and the rules under v8
    prax work --steps typing -n 60     # the untyped items the aliases admit

## The rules round of 2026-09-23

No version bump: the ontology stood, and the rules learned to read
what the queue holds. 2,655 items were open. The first pass cleared
560 of them, and the second, with the rules below it, another 68.
About 2,000 are left, and the count moves: extraction opens new items
as it runs.

**The document, under the type its relation wants.** The old rule
retyped a source that was the document's title, to the type the graph
had given the document. That is not always the type the relation
asks for. `calls_for` takes a recipe, and a recipe captured from a
food site is a `document` in the graph. So the rule now asks the
relation: the document's own type where the relation admits it, else
the one kind of document its domain names (`_self_document_type`).
Where more than one kind fits, a document nobody typed is a paper,
the library's default. A page of my own, or a project, is never
retyped to make an edge fit; those items wait for the ontology
instead. The match is normalized, and a title's first part counts:
the model names the recipe, and the capture's title carries the
site's tail ("… | ZEIT"). 348 items.

**The shapes the queue kept coming back with.** A manual "part of"
the thing it documents is `about` it (83). The firm behind a manual
`published_by` it, not `developed_by` (46, core's own wording). An
institution is not something a paper `uses`: it `mentions` it (17).
A paper that "implements" a tool `uses` it (15). "applies" over an
ingredient is `calls_for` (25). An ingredient written first, with the
dish after it, is the dish calling for the ingredient (13). A manual
that "covers" a device is the manual of it, which studio calls
`describes`; `covers` is for a concept or a standard (35).

**The author, under either type.** `authored_by` written backwards
was flipped when the model typed the author `author`; it now flips a
`person` too (48, with the reversed alias `author_of`).

**Names that are not names.** "Author not listed", "no author" and
"author unknown" join the noise list. A name that begins with a field
the model echoed (`build=`, `component=`) is malformed, as the fields
prax already knew were (9 dropped).

The passes wrote their edges as `typing-rules`, INFERRED and
retirable with `retire_run`: 560 in run `typing-20260922T225935`, the
rest in the run after it.

## What is still open

Just under 2,000 items. Four groups the rules do not touch. `is_a`
and `subclass_of`, which type an entity rather than relate two.
`mentions` said of an organization or a person rather than of the
document (86). `about` said of a work, an organization or a tool
(52). A document `funded_by` an organization (15), which no v8
relation holds. The rest is a long tail of one-off shapes.

What is left is a v9 question rather than a rules question. The
relations a document takes name `paper` where they could name
`document`, and a captured page has to be read as a paper for an
edge to fit.
