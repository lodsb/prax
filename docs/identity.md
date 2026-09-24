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

## Why it is not being written today

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

## What is worth doing first, and is small

Step 1 on its own — **a preferred label for every entity** — is
independently useful and carries no risk: it adds rows, changes no
behaviour, and makes `entities_by_label` answer for the whole graph
rather than for the 1% the passes have touched. Every later step assumes
it. It is the right first commit whenever this is picked up.
