# Ids that mean the same in another library (plan item AP)

2026-10-28, a design note. Its four steps were built on 2026-10-29
(migrations 64 and 65, `store.graph.imports`, `prax-graph/2`, `prax
export --as`). Where the build differs from the design, the design
below says so. The user, 2026-10-18:
"the document ids should probably be derived from stable hashes, so i
can merge a friend's library in, or networking etc and also share a
subgraph". This note says what each kind of row would carry, what an
export and an import would speak, and what it costs. The graph-file
format it extends is `docs/graph-files.md` (`prax.graph.graphio`,
`prax-graph/1`).

## What is there

Counted on the live store, read-only, 2026-10-28:

| row | count | local key | what already means the same elsewhere |
|---|---|---|---|
| document | 32,919 | `documents.id` | `documents.hash`, the sha256 of the original (invariant 2); unique, 32,919 distinct |
| entity | 263,645 (48,861 folded into another, `canonical_id`) | `entities.id` | nothing: `(name, type)` is unique here, but a name is not an identity (invariant 6) |
| edge | 526,980 (328,237 live; 106,700 INFERRED; 527 with no source document) | `edges.id` | nothing |
| page | one per wiki document | `pages.slug` | the slug, by convention |

`prax-graph/1` already matches documents by hash. It names an entity by
`(name, type)` and lands it on the local entity that answers to that name
(`store.link`'s label fallback). Two different people called "J. Smith"
in two libraries become one entity on import, and a friend's entity that
this library calls by another name becomes a second one. Both are
resolution's work, and the format gives resolution nothing to keep: a
second import of the same file cannot say which local entity the first
one decided for.

## The shape

The integer keys stay. Every table, index, foreign key and the vector
files (keyed by chunk and document id, invariant 1) are built on them,
and none of that leaves the library. Beside them, each kind of row gets a
global id, `gid`, written once:

- **A document's** is its `hash`. Nothing new: the column exists and is
  unique. A retired copy (`retire_document(duplicate_of=)`) keeps its
  own hash, so an import that names the copy's hash lands on the keeper
  through `retired.of`.
- **A page's** is minted once when the page is made, like an entity's:
  128 random bits, written `p:` and 26 Crockford base32 characters
  (decided 2026-10-28). Not its slug: two libraries that both have
  `project-synth` are probably talking about different projects, and a
  rename changes the slug. Not its hash: every revision is a new
  original with a new hash. The `gid` stays through edits and renames;
  the page's revisions keep their own hashes as documents.
- **An entity's** is minted once when the row is made: 128 random bits,
  written `e:` and 26 Crockford base32 characters. It is not derived from
  the name, because the name changes (`rename_person`, the `names` pass)
  and two libraries' equal names are not one thing. It is not a ULID:
  `created_at` already holds the time, and SQLite's `randomblob(16)` fills
  263,645 rows in the migration without leaving SQL. A merge keeps both
  rows, as it does now. The folded row keeps its `gid`, so the twin's id
  is an alias with no table of its own: a lookup by `gid` follows
  `canonical_id` like every read does. `unmerge_run` needs no change.
- **An edge's** names the claim, not the row: `c:` and the sha256 of the
  source document's hash, the relation, and the two ends' `gid`s
  (`graphio.edge_gid`). It is not unique, and must not be. 50,007 of the live store's (src, rel, dst, document)
  combinations are held by more than one row, ended and live, one per
  extraction. They are one claim read several times. An edge with no
  source document (the 527: a person's link, a rule over two documents)
  hashes the empty string in its place. A rule-derived edge is not
  exported at all (below), so its `gid` is never compared across
  libraries.

  *Built differently:* an edge's `gid` is computed when an export writes
  it, not stored. Its ends and relation never change (migration 35), but
  its document may: a duplicate's edges move to the survivor, and a
  stored `gid` would then name a document the edge no longer has. The
  import's question, "is this claim here already?", is answered by the
  local key it hashes: a live edge of the same relation between the two
  ends (or rows folded into them) for that document, whoever wrote it.

Migration 64 adds `entities.gid` and `pages.gid` with a unique index
each and fills them in SQL (`random() & 31` per Crockford character, 26
a row). An `AFTER INSERT` trigger mints every later row, so no writer
can make a row without one. Another trigger refuses a change to a
`gid` once set. Measured on a copy of the live store on 2026-10-29: the
migration took 1.3 s for 263,677 entities and 571 pages, every `gid`
distinct, and the entities' index is 9.8 MB. No edge index: there is no
edge column.

## The file: `prax-graph/2`

The header says `prax-graph/2`. An import still reads `/1`, as it does
now.

- **`document`** gains nothing: `hash` is already its global id.
- **`entity`** gains `gid` and `aliases`: the `gid`s of the rows folded
  into it, and those of other libraries' entities an import decided are
  it (`store.folded_gids`). The second kind is what makes a file that
  goes out and comes back land on its own rows: a friend's library
  exports our entity under its own `gid`, with ours among the aliases.
  It keeps `name`, `type` and `labels`, which a receiving library
  resolves on.
- **`edge`** names its ends by `gid` (`src_gid`, `dst_gid`) beside the
  names, and carries its own `gid`. Its `source_hash` stays.
- **INFERRED edges with premises are left out.** The receiving library
  derives its own from what it holds (`derive_rules`); a derived fact
  shipped without its premises would be a claim nothing here supports.
- **`page`** gains `gid`. An import lands a page on the local page of
  that `gid` and never on one with an equal slug; a new page whose slug
  is taken here gets a free one (`-2`).

## The import

The receiving library keeps what it decided about another library's
ids, in a new table:

    foreign_entities (source TEXT, gid TEXT, entity_id INTEGER,
                      decided_by TEXT, run TEXT, decided_at TEXT,
                      PRIMARY KEY (source, gid))

`source` is the import's name (`import:<name>`). A row says "their `e:…`
is our entity N". Rows are made in this order:

1. A `gid` this library minted itself (a file that went out and came
   back) lands on its own row: an equal `gid` is the same row, because
   only this library mints its own.
2. A `gid` already in `foreign_entities` for this source lands where it
   was decided.
3. Otherwise the entity is matched by name and type (`store.link`'s
   lookup, writing nothing) and the result is written as a `likely` row,
   `decided_by` `import`. When nothing here answers to the name, the
   entity is new: it is made, and the row is `new`, decided.

A `likely` row waits on the Review page (decided 2026-10-28; the tab
"from other libraries", `GET /graph/imports/likely`). Its edges are held
in `import_held`, not written, until the row is decided
(`store.decide_foreign`, `POST /graph/imports/decide`): "theirs is our
N", "theirs is a new entity", or "leave it out". A new entity whose name
ours already holds is named after the import (`wave digital filter
(laptop)`), because `(name, type)` is unique here. A decided row writes
its held edges under the run of the import that held them, and a later
import follows it. An answer is taken back with `store.undo_foreign`
(`POST /graph/imports/undo`): the edges written onto that entity end
under a run of the undo, which `restore_run` takes back in turn, and
wait with the row again. The judges of resolution's likely pairs
(`steps.adjudicate`) may answer first, as they do for two entities of
this library, and a person's answer replaces theirs. That is not wired
yet; `by` on the route signs a judge's answer when it is.

An edge whose claim this library already holds live for that document
is counted (`same`) and not written again: a re-import of the same file
writes nothing. One whose ends land on other entities than last time is
the new claim it is. The source's live edges that the newer file no
longer says are ended under the import's run (`store.end_unconfirmed`),
so `restore_run` can put them back; nothing else of the import is
rewritten.

A friend's entity never meets this library's entity by an equal number.
It meets it through resolution, as two entities of this library do.

## The wall on an export

`GET /graph/export` is the administrator's route today, and its reads
(`seed_documents`, `subgraph_edges`) do not filter by viewer: the
administrator sees everything. A file meant for someone else needs the
wall. The design: `prax export --as <token-name>` runs the export under
that named token's `store.VIEWER`, so the file holds exactly what that
token would see:

- no document `hidden_documents` hides from it (suspected, personal, a
  picture with a named face without `--personal`, outside its modules);
- no edge whose source document is hidden, and no edge derived from a
  hidden premise (`hidden_by_premise`; INFERRED edges are left out
  anyway);
- no face, face vector, face name or `depicts` edge from a face answer,
  whatever the token: faces are biometric and never leave the library;
- no entity only those edges reach.

The reads that the export calls filter by the viewer like every
many-row read, so `--as` is a property of the store and not of
`graphio`. `seed_documents` leaves out what `hidden_documents` hides.
`subgraph_edges` leaves out an edge whose document or a premise's is
hidden. The
door's `GET /graph/export?as=<name>` sets `store.VIEWER` to the named
token's (`store.named_viewer`) while the file is built; the route stays
the administrator's. Faces are left out by `graphio`, for every file: no
`depicts` edge (`FACE_RELATIONS`), and the face tables are never read by
an export. An export without `--as` stays the administrator's whole
view, for a backup of a subgraph or a project kept in git.

## Steps, when it is built

1. (done 2026-10-29, migration 64) `entities.gid` and `pages.gid`,
   filled in SQL. No `edges.gid`: it is computed (above).
2. (done 2026-10-29) Every writer mints the `gid`: the triggers of
   migration 64 do it for any row, and `tests/test_global_ids.py` holds
   every entity and page to one, distinct.
3. (done 2026-10-29, migration 65) `prax-graph/2` out and in,
   `foreign_entities`, the re-import that writes nothing, the held edges
   of `likely` rows with their tab on the Review page, and the undo
   (`tests/test_graph_import_gids.py`). A page is found by its `gid` and
   keeps the one it was minted with in the library it came from.
4. (done 2026-10-29) `--as <token>`: the export's reads filtered by
   the viewer, faces left out, two cases in `tests/test_wall.py`.

Sharing over a network ("networking") is the same file over HTTP and is
not designed here.

## Decided (the user, 2026-10-28)

- An import's `likely` rows wait on the Review page before their edges
  are written (above, "The import").
- A page's global id is random and minted once, neither its slug nor a
  hash, so it stays through edits and renames.
