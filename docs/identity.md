# The name and the thing: what the restructure is, and what it is not

2026-09-24. `docs/stratification.md` step 5 left one item: `entities.name`
is still the identity of an entity, where SKOS and Wikidata would make it
a label and the row the identity. This is the survey that was owed before
any of it is written, and it changes the shape of the work.

## What was surveyed

| | |
|---|---|
| entities | 144,211 (121,303 live, the rest merged aliases) |
| labels | 1,560, over 1,516 entities |
| places that *look an entity up* by name | **6**, all in `prax.store` |
| places that *write* `entities.name` | **5**, all in `prax.store` |
| references to `s.name` / `t.name` / `e.name` across the codebase | **430**, in 26 modules |

The 430 is the number that mattered, and it is the one that changes the
plan. Almost all of them are `SELECT s.name … FROM edges JOIN entities s`
— a join that *displays* the name of an entity it already has by id.
None of them decides identity. They do not need to move.

## So the change is a denormalization, not a removal

The end state is not "`entities.name` goes away". It is:

- **`entity_labels` is authoritative.** Every name a thing answers to,
  with its language, one preferred per language (migration 22 holds it).
- **`entities.name` stays, as a cache**: the preferred label in the
  language this host displays. Maintained by the store, never by a
  caller.
- **Identity is the row.** A lookup by name goes to the labels; the
  column is for reading, joining and showing.

That keeps the 430 readers, the `documents_fts` field, every join and
the UI working untouched, and it keeps the name indexed, which a label
join would not. It is also what the graph already half is: `link`
already falls back to the labels when no entity carries a name
(2026-09-24), and `find_entities` and `traverse` already read them.

## What actually has to change

1. **Every entity gets a `pref` label when it is created.** Today only
   the 1,516 the passes touched have labels at all; the other 119,787
   live entities have a name and nothing else. A migration writes one
   `pref` label per live entity from its current name, and `_entity_id`
   writes one for every entity it creates. Until that holds, "the labels
   are authoritative" is false and the cache cannot be rebuilt from
   them.

2. **`UNIQUE (name, type)` becomes a constraint on the *cache*.** It
   says today that two entities of one type cannot share a name, which
   is what makes the name identity-ish. Under the new shape it means two
   entities of one type may not *display* the same, which is a weaker
   and more honest claim — and it is exactly what the type-clash review
   queue already exists for. Checked: switching the display language to
   German today would collide on **0** pairs, so the constraint survives
   a switch on this library.

3. **A rename becomes a label write plus a cache refresh.**
   `name_in_english` and `retitle` stop writing `entities.name` directly
   and write a preferred label instead; the store recomputes the column.
   The `was` column (migration 22) then has nothing to do and can go —
   the old name is simply a label that is no longer preferred, which is
   what SKOS says it is.

4. **A display language, chosen by the host.** `prax.yaml` gains one
   setting; a maintain pass rebuilds `entities.name` from the preferred
   label in that language, falling back to English and then to whatever
   the entity has. That is the feature the whole thing is for: the graph
   in German for a German reader, one node either way.

## As built, 2026-09-24

All four, with llama-server stopped for the duration — none of it calls a
model, which is what made it safe to do while the box was short of
memory.

- **Migration 23**: a preferred label for every entity, and `_entity_id`
  writes one for each it creates. 145,243 labels over 144,211 entities in
  3 seconds on a copy of the live store.
- **`entities.name` is a cache**, rebuilt from the labels by
  `_refresh_name` and by the `names` pass of `prax maintain`.
  `graph.language` in prax.yaml says which language a host shows.
- **A rename is two label writes**: the document's word stops being
  preferred where the translation now is, and the column follows. One
  preferred label *per language*, so the German name stays the preferred
  German name — which is the point.
- **Routes for what the store could already do**: `POST /graph/label`,
  since `add_label`'s own docstring said "what a dictionary import or a
  person writes" and nothing could write one.

### What went wrong, and what it taught

Migration 23 wrote a label `WHERE NOT EXISTS (a preferred label)`, which
skipped exactly the entities that most needed one — the 1,516 a pass had
already given a preferred name in another language. Their own name was
then in no row, and the first rebuild of the display names renamed four
of them with nothing left to put back: `Chomsky-Normalform` became
`Chomsky normal form` and the German spelling was gone from the store.

Migration 24 fixes the rule. The four were recovered from a copy taken
before the migration — which is the only reason this is a footnote rather
than a loss.

The lesson is in the code rather than in a guard. `_refresh_name` records
the name it is about to replace, so a name cannot be dropped by any path,
whether or not a migration prepared the ground. A guard would have caught
the one route that lost these four; recording the outgoing name makes the
loss impossible on all of them.

## Why it waited a few hours (kept, because the reasoning stands)

Three reasons, in order of weight.

**It is one change, not five.** Between step 1 and step 4 the graph is
half-migrated: some names authoritative, some cached, a rename meaning
two different things depending on which path wrote it. A half-migrated
graph is worse than an unmigrated one, and this is the sort of work that
wants a clear run, not the end of a long day.

**The migration is over 144,211 rows** and writes 119,787 labels. It is
not difficult, but it is the kind of thing to run with the machine quiet
and someone watching the count.

**The machine is not quiet.** Two background passes were reaped for
memory pressure while this survey was being written (2.4 GB free of 31,
llama-server holding twenty of them), and 2,005 documents of the
sections backlog and the rest of the vocabulary candidates are still
waiting. Those want the machine before a migration does.

What changed the answer was noticing that **none of it calls a model**.
llama-server was holding twenty of the machine's thirty-one gigabytes for
work this change does not need; stopping it freed the box, and the
migration ran on a copy first, then live, in seconds.

Step 1 was still the right first commit — a preferred label for every
entity, which adds rows and changes no behaviour — and the bug above is
what happens when it is written slightly wrong.
