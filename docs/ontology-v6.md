# Ontology v6: what the v5 re-read asked for

2026-09-13. Research module 5 → 6; composed version
`core1+craft1+kitchen2+research6+studio3+workshop2`.

## The evidence

After the v5 re-read of the whole library (2026-09-12) the review queue
held 25,827 open items. A second batch of typing rules (`prax.review`,
commit 1cfd1ac) linked 3,091 and dropped 3,390 — placeholder names, a
cited "document" that is a paper, a listed "person" who is the author,
the document published in a venue-shaped thing. What stayed open said,
by count, what the models kept wanting to say and the ontology had no
home for:

| open | shape | v6 |
|---|---|---|
| 2,756 | `paper affiliated_with organization` | `written_at`: the institution the document came out of |
| 611 + 444 + 224 + 117 + 114 + 90 | `mentions` towards an organization, a person, a document, a paper, a work, a place | `mentions` from any document to any kind of thing |
| 303 + 296 + 100 + 88 + 57 | `about` a person, an organization, a work, a place, an event | `about` towards those too |
| 256 + 97 + 41 (+387 `mentions`) | `about`/`mentions` from a plain `document` (a manual, a sheet, a capture typed by its kind) | `about`, `mentions`, `authored_by`, `published_in` from any document |
| 140 | `paper authored_by organization` | `authored_by` towards an organization that issued the document |

## The changes

- **`written_at`** (new): domain `document`, range `organization`. "The
  institution the document came out of, as it states it (the authors'
  affiliation on the first page, the company behind a manual);
  `affiliated_with` is for a person, this is for the document." Aliases
  `produced_at`, `from_institution`. The typing rules remap a document's
  `affiliated_with` an organization to it, and an untyped
  `affiliated_with` from the document itself towards something
  organization-shaped.
- **`mentions`**: domain `document`; range every core kind — tool,
  method, dataset, concept, person, organization, document, place,
  event, work. The description still says it is the weakest relation
  and never for something the document uses, cites or is about.
- **`about`**: domain `document, claim`; range adds person,
  organization, place, event, work — a biography, a history of a studio,
  a paper on one piece.
- **`authored_by`**: domain `document`; range `author, organization`.
- **`published_in`**: domain `document`.

Nothing was renamed or removed, so no data migration; edges keep the
version they were written under.

## What it means for the library

- `scripts/replay_review.py` links what now fits as it stands; the
  typing rules take the `affiliated_with → written_at` remap.
- Every research document is due for re-extraction under v6
  (`extract_graph.py` selects by stamp). That re-run is a decision, not
  a consequence: the v5 readings stand until it is made, and a v6 pass
  would retire each document's v5 edges as it goes.
- What stays in the queue after this is the untyped remainder (`about`,
  `part_of`, `uses`, `mentions` with no endpoint types — a model typing
  pass) and the genuine misfits.
