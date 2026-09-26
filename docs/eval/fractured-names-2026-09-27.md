# One name, several entities: how much of the graph is in pieces

2026-09-27. A traverse of `apple` returned the recipe's ingredient and
Apple the company as one answer. The traverse was treating a name as an
identity. Behind that, the company itself is at least six entities of
five types: `apple` (venue, with two `Apple` organizations merged into
it), `Apple Computer, Inc.` (venue, and separately author), `Apple Inc.`
(organization, and separately method). This is the measurement of how
common that is, taken read-only on the live store, before deciding what
to repair.

## What was counted

The canonical entities that have live edges (99,137 of 122,195),
grouped by name: exactly, ignoring case, and loosely, without
punctuation, spacing and legal suffixes (Inc., Ltd., GmbH).

| grouping | names held by more than one entity | entities | edges |
|---|---|---|---|
| exact, case ignored | 3,739 | 7,962 | 32,096 |
| loose key | 4,235 | 9,154 | 36,650 |

## Most of it is two things, not one thing in pieces

The commonest pairs are a document beside its topic: the paper
*Independent Component Analysis* and the method, the paper *Timbre* and
the concept (method/paper 509, paper/tool 451, concept/paper 331). Those
are two things, and the graph is right to hold them apart. They are
what a traverse by name must *not* merge.

Setting aside every group member that is a document (paper, page,
work, manual, article, recipe…), what is left is fracture:

| kind | names | edges | examples |
|---|---|---|---|
| unrelated types | 1,708 | 8,758 | `short-time Fourier transform` method 96 / tool 1; `SuperCollider` tool 65 / method 7; `TU Munich` organization 46 / author 13 |
| the same type twice | 75 | 336 | `Marc René Frieß` / `Friess`; `musical creativity` / `Musical Creativity` |
| a type and its subtype | 34 | 128 | `Simon Dixon` author / person; `butter` ingredient / material |

The unrelated-type splits are mostly **one thing typed inconsistently
from document to document**. Tool and method alone account for 640
names (`OSC`, `CataRT`, `ASIO`, `Open Sound Control`), then concept and
tool, author and organization. A few are genuinely two things: the
concept *music perception* and the journal *Music Perception*, the
concept *research* and a venue called Research.

## What this says about the fix

- **The traverse** has to stop treating a name as an identity. Most
  names shared across types are a document and its topic, and those
  are two things.
- **The repair** is a resolution problem, not a traverse one. The likely
  tier of entity resolution compares entities of one type, and the
  extractor's typing wobbles between tool and method. The 75 same-type
  pairs are what that tier missed on casing and ß/ss. The 34 subtype
  pairs are the fold resolution already knows how to make. The ~1,700
  unrelated-type splits want a rule for which type pairs may name one
  thing (tool/method, concept/method, author/organization), with a person
  deciding, since some are two things. Apple Inc. is the loose-key
  variant of the same problem.
