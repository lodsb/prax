# prax's data model beside Utopia, Graphiti, Cognee and others

This note compares how prax stores facts with how five related systems
store them. It covers the data model and the agent interface. It does
not compare retrieval quality, user interfaces or extraction prompts.
prax is described as of migration 0048 (2026-10-06).

## What was read, and when

Each system was read from a shallow clone of its default branch on
2026-10-07. Claims below name a table, column, function or tool found in
that clone.

| system | read at | what it is |
|---|---|---|
| [Utopia](https://github.com/deeplethe/utopia) | commit `0197bd8` (2026-10-04), workspace version 0.1.0, 96 migrations up to 0105 | Rust server over Postgres with pgvector and Tantivy, Apache-2.0 |
| [Graphiti](https://github.com/getzep/graphiti) | v0.30.2, commit `aa5bb27` (2026-10-06) | Python library over Neo4j, FalkorDB or Neptune |
| [Cognee](https://github.com/topoteretes/cognee) | v1.6.2, commit `b32d8af` (2026-10-01) | Python library, embedded by default |
| [Semantica](https://github.com/Hawksight-AI/semantica) | v0.7.0, commit `9a71df6` (2026-10-06) | Python library over many graph stores |
| [Basic Memory](https://github.com/basicmachines-co/basic-memory) | v0.23.2, commit `6982cfc` (2026-10-06) | Markdown files indexed in SQLite, with an MCP server |

[LightRAG](https://github.com/HKUDS/LightRAG),
[HippoRAG 2](https://github.com/OSU-NLP-Group/HippoRAG),
[Microsoft GraphRAG](https://github.com/microsoft/graphrag),
[Mem0](https://github.com/mem0ai/mem0),
[fast-graphrag](https://github.com/circlemind-ai/fast-graphrag),
[TrustGraph](https://github.com/trustgraph-ai/trustgraph) and
[atomic](https://github.com/kenforthewin/atomic) were read from source on
2026-10-03. They were not re-read for this note. Their section is short
and should be checked before anything is built on it.

Three terms are used throughout. *Record time* is when a system wrote a
fact and when it stopped holding it. *World time* is when the fact is
true in the world, as a source states it. A *run* is one batch of writes
by one producer, which can be named and undone as a whole.

## The dimensions

| dimension | prax | Utopia | Graphiti | Cognee | Semantica | Basic Memory |
|---|---|---|---|---|---|---|
| record time | `edges.valid_from`, `valid_to` | `facts.recorded_at`, `invalidated_at` | `created_at`, `expired_at` | `created_at`, `updated_at`, `valid_to` (ms epoch) | `recorded_at`, `superseded_at` in a relationship dict | file history (`okf.export.recorded_history`) |
| world time | `world_from`, `world_to` with precision year, month, day or `unknown` | `valid_from`, `valid_to` with precision year to second or `unknown`, plus `attested_from`, `attested_to` | `valid_at`, `invalid_at`, no precision | event nodes only, opt-in | `valid_from`, `valid_until` | `memory_time_index`, authored ranges |
| conflicts | `edge_conflicts`, kept, never resolved | `fact_conflicts` with three resolutions | a model picks candidates, code ends them | `contradicts` edge or `superseded` tag, both opt-in | `ConflictResolver` strategies | none found |
| evidence | quote, `source_doc`, character range and text hash on the edge | `fact_evidence` per chunk: quote, `quote_start`, `quote_end`, `doc_version` | `episodes` list | `provenance_edge_evidence` per chunk and pipeline run | PROV-O ledger with a hash chain | the note itself |
| producer and run | `producer`, `run` on every edge | none on `facts` | none | `source_pipeline`, `source_task`, `pipeline_run_id` | `activity_id`, `agent_id` | `created_by` |
| undo | `retire_run`, `restore_run`, `unmerge_run` | merges, retypes, adoptions, deletions | delete only | delete by owner | not checked | file edits |
| names and languages | `entity_labels`, one `pref` per language | `known_as` facts, no language column | `name` only | not checked | SKOS export | `title` only |
| ontology | YAML modules, a composed version on every edge | five RDF packs, mutable rows, no version on a fact | Pydantic types passed per call | `ontology_uri`, `ontology_valid` per node | OWL, SHACL, SKOS | Picoschema |
| derived facts | INFERRED edges with `edge_premises` | `derived_facts` with `fact_derivations` | none | none found | Rete, Datalog, SPARQL inference | none |
| access | named tokens by module and `documents.sensitivity` | roles per knowledge base, tokens by base and scope | `group_id` namespaces | users, tenants, ACL per dataset | not checked | projects |
| storage | one SQLite file, usearch files | Postgres 16, pgvector, Tantivy | a graph server | SQLite, LanceDB, LadybugDB | pluggable | SQLite or Postgres, sqlite-vec |
| agent interface | 31 MCP tools, each one HTTP call to the door | MCP inside the server, 10 tools | MCP server, 13 tools | MCP server, 4 tools | MCP server, 16 tools | MCP server, 29 tools |

## Utopia

Utopia is the nearest system to prax. Both keep two clocks on a fact
and evidence with offsets. Both record every merge. Several of prax's
shapes were taken from Utopia after a reading on 2026-10-03. The
comments of migrations 0035, 0036, 0043, 0046, 0047 and 0048 say so.

**Facts.** One `facts` table holds relations and literal values
(`object_id` or `object_value`, migration 0003). World time is
`valid_from` and `valid_to`, each with a precision. Since migration 0033
the precision reaches `hour`, `minute` and `second`, and a CHECK requires
the date to be truncated to it. An end with no known date is `valid_to`
NULL with precision `unknown`. Migration 0034 then requires an anchor for
it (`attested_to`). `attested_from` is the date of the document that
states a fact, used when the fact gives none. `valid_from_grade` (0069)
says whether a start date was written, computed from an anchor, or not
anchored.

**Corrections.** A correction inserts a new row with `supersedes`
pointing at the old one and sets the old row's `invalidated_at`. Reads
"as of T" are built in one module, `utopia-store/src/record_axis.rs`.
Its comment records a query that went from 61.6 s to 4.3 s when the
predicate degrades to `invalidated_at IS NULL` for "now".

**Evidence.** `fact_evidence` has one row per fact and chunk: `quote`,
`quote_start` and `quote_end` (0061), `document_id`, `doc_version` and
the verb the model used (`proposed_predicate`). Migration 0104 adds
`attested_at` and `attested_by` per evidence row. No column on `facts`
or `fact_evidence` names the extracting model or a run.

**Conflicts.** `fact_conflicts` pairs an old and a new fact with a
`reason`, a `status` of `open` or `resolved` and a `resolution`. The
README names three choices: close the old fact, keep both, or reject
the new one. A relation type carries `functional`, `inverse_functional`,
`is_transitive`, `is_symmetric`, `is_asymmetric` and `is_irreflexive`.
Its `temporal` column is `state`, `event` or `eternal`.

**Entity resolution and undo.** `resolution.rs` attaches a mention to
an entity at a profile similarity of 0.55 (`SIM_ATTACH`). Under 0.35
(`SIM_NEW`) it makes a new entity. Between the two, a new entity is made
and a `resolution_reviews` row is queued. A tie within 0.02 goes to
review. A model judges the queued pairs, and its verdicts are cached in
`resolution_verdicts` with the model name. `entity_merges` stores the
facts a merge moved, ended and corrected, the profile and type before,
who merged and why, and `reverted_at`. `agent_decisions` records each
automatic decision with its `precedents` and a status from `proposed` to
`reverted`. A name is a `known_as` attribute fact since migration 0055,
with both clocks and its source. It has no language.

**The ledger.** `audit_events` (0007) refuses UPDATE, DELETE and
TRUNCATE by trigger. Its references carry no foreign keys, so a record
outlives its object. The migration's comment names a hash chain as later
work, and none is implemented. No trigger guards `facts` itself, which
cascade on delete from their entity.

**Ontology.** Five packs ship gzipped in the binary
(`utopia-server/src/ontology_packs.rs`): schema.org (1,010 classes, 1,676
properties), W3C Org, PROV-O, FOAF and IOF Core. They are imported as
mutable rows (`entity_types`, `relation_types`), recorded in
`ontology_imports`. A fact carries no ontology version. Unknown words
are counted in `ontology_misses` and proposed in `ontology_proposals`.

**Rules.** Derived facts live in their own table, `derived_facts`, with
their `rule_id`, interval and confidence. Their premises are in
`fact_derivations`. Derivation is off by default.

**Agent interface and access.** The MCP endpoint is
`POST /api/v1/kbs/{kb_id}/mcp` inside the server
([guide](https://github.com/deeplethe/utopia/blob/main/web/src/docs/mcp.md)).
Its tools are `search_chunks`, `get_document`, `find_entities`,
`entity_facts`, `neighbors`, `timeline`, `paths_between`, `changes`,
`search_docs` and `remember`. `entity_facts` takes `at` for world time
and `as_of` for record time, separately. `remember` writes a pending
fact that waits for a person. `personal_tokens` have a `scope` of
`read` or `write` and an optional list of bases. `kb_members` gives a
role per base. Nothing restricts access per document or per fact. An RDF
export (`GET /api/v1/kbs/{kb_id}/export?format=turtle|jsonld`) writes
both clocks, evidence and derivations in PROV-O and schema.org terms.

**Weight.** Utopia needs Postgres 16 with pgvector and one Rust binary.
The README's roadmap still lists "benchmarks at 100k documents" as open.

## Graphiti

`EntityEdge` (`graphiti_core/edges.py`) carries `fact`, a sentence with
its `fact_embedding`, and `episodes`, the ids of the inputs that state
it. World time is `valid_at` and `invalid_at`. Record end is
`expired_at`. There is no precision field.

A model proposes which existing edges a new one contradicts.
`resolve_edge_contradictions` in `utils/maintenance/edge_operations.py`
then compares world times. It sets the older edge's `invalid_at` to the
new edge's `valid_at` and stamps `expired_at`. Entity resolution
normalizes names exactly, then matches by MinHash with a Jaccard
threshold of 0.9 (`dedup_helpers.py`), then asks a model. Duplicates are
linked by `IS_DUPLICATE_OF` edges. No producer, run or ontology version
is stored. The MCP server's `delete_entity_edge` and `clear_graph`
delete. `SagaNode`, new in this version, summarises a sequence of
episodes. The Kuzu driver warns that it is deprecated because upstream
Kuzu is no longer maintained.

## Cognee

Every node is a `DataPoint` with `created_at`, `updated_at`, `version`,
`valid_to`, `ontology_uri` and `ontology_valid`. Provenance is its
strongest part. `provenance_edge_evidence` holds one row per edge and
chunk, with dataset, data item, `pipeline_run_id`, `source_task` and
`confidence`. `provenance_entries` is a PROV-O ledger with `checksum`,
`previous_checksum` and a unique `sequence_id`, in a format its
docstrings trace to Semantica. A ledger row is tombstoned with
`invalidated` and never deleted.

`tag_superseded_edges` (`modules/graph/utils/temporal_conflict_resolver.py`)
marks the older values of a relation the caller declares functional.
It ranks by `updated_at`, which is record time. A separate
`detect_contradictions` task is switched by `contradiction_detection`.
`consolidate_entities` merges near-duplicates by cosine similarity or
equal names, re-points their edges and deletes the duplicate nodes. A
`merged_from` report is logged, and nothing undoes the merge. The
default stores are SQLite, LanceDB and LadybugDB, Kuzu's continuation.

## Semantica

Semantica is a library with many backends. Facts stay plain relationship
dictionaries, and `BiTemporalFact` (`kg/temporal_model.py`) reads
`valid_from`, `valid_until`, `recorded_at` and `superseded_at` from
them. `TemporalTruthMaintenanceAdapter` projects them into its reasoner
at a pair of `valid_at` and `known_at`. `ConflictResolver` resolves by
voting, credibility, recency, first seen or confidence, or flags a
conflict for review. `provenance/storage.py` has an in-memory and a
SQLite store with a hash-chain head (`get_chain_head`). Its README
claims OWL, SHACL and SKOS support and exports to RDF, OWL, Parquet,
Cypher and JSON-LD. The MCP server lists 16 tools, among them
`record_decision`, `find_precedents`, `run_reasoning`, `update_node` and
`delete_node`. Performance and memory were not measured here.

## Basic Memory

Markdown files are the source of truth, and SQLite or Postgres is an
index of them. `entity`, `observation` and `relation` rows are parsed
from notes. Relations carry `relation_type` and `context` with no
confidence or source beyond the note. Since SPEC-82 a note may state
when something holds: `memory_time_index` keeps `lower_value`,
`upper_value`, their inclusivity, a `range_axis` and the author's
`source_text`. Vectors use FastEmbed and sqlite-vec. The `okf` package
exports a project as an Open Knowledge Format bundle. Its footprint is
close to prax's. A relation records far less.

## The others, as read on 2026-10-03

LightRAG keeps creation time only and lets a model merge descriptions.
HippoRAG 2 links similar entities by synonymy edges without merging and
ranks passages by Personalized PageRank. Microsoft GraphRAG extracts a
period for claims only. Mem0 removed its graph memory from the open
source in v2.0.0. The fast-graphrag library records a match as an identity edge and
ranks by PageRank. TrustGraph stores provenance as RDF triples on
Cassandra. The atomic project is a SQLite knowledge base with an MCP server and
named tokens, linked by similarity without typed relations.

## What prax has that the others lack

- **A run on every fact, and its undo.** `edges.producer` and
  `edges.run` name who wrote a fact and in which pass. `retire_run`
  ends a run's edges. `restore_run` restates what a repair ended, from
  `edge_endings`. `unmerge_run` undoes a round of merges and renames,
  from `entities.merged_run` and `entity_labels.was`. Utopia can undo a
  merge but records no run on a fact. None of the others can end one
  extractor's work and keep it as history.
- **An ontology version on every fact.** `edges.ontology_version` holds
  the composed version string, or the subset's version for a document
  read against `meta.domains`. No other system stamps one.
- **Append-only facts by trigger.** Migration 0035 refuses a delete of
  an edge and a change to its fact. 0047 holds world dates fixed, and
  0048 holds an evidence place fixed once written. Utopia's append-only
  trigger guards only `audit_events`.
- **Names per language.** `entity_labels` keeps one `pref` per `lang`
  (index `idx_entity_labels_pref`, migration 0022) and any number of
  `alt`. The `vocabulary` step adds the word a reader of another
  language would search for. Utopia, Graphiti and Basic Memory keep no
  language on a name. Cognee and Semantica were not checked for it.
- **A wall per document.** `documents.sensitivity` and the `tokens`
  table (migration 0029) hide personal documents and whole modules from
  a named token. `store.hidden_by_premise` hides a derived edge whose
  premise is hidden. Utopia and Cognee grant per base or per dataset.
- **Pi-class weight.** One SQLite file and memory-mapped usearch files.
  The chunk index holds 1.3 million vectors in 1.3 GB at f16
  (2026-10-07), and the int8 index would halve it. Utopia and Graphiti need a
  database server.
- **A thin agent proxy.** `prax.mcp_server` makes one HTTP call per
  tool and holds no logic (invariant 5). Answers are bounded by design
  (invariant 6). Utopia's `search_chunks` returns six passages of up to
  800 characters, and `get_document` returns up to 24,000 characters.

## What the others have that prax lacks

- **World time on most facts.** Utopia resolves time words against the
  document's date as it extracts (`time_mentions`, migration 0064) and
  anchors undated facts with `attested_from`. In prax two passes write
  world time. The `markup` pass of `prax maintain` takes a page's
  schema.org dates: 275 live edges on 2026-10-07, producer `jsonld`. The
  `worlddates` step (`prax.graph.worlddates`) is built and off until a
  host names a model for it. The general extraction can ask
  for dates (`extraction.world_dates`), and the switch is off because it
  gave none (`docs/eval/extraction-standard-names-and-dates-2026-10-05.md`).
  An undated fact shows its document's date as `stated` in `traverse`,
  which is when it was said, not when it held.
- **A world-time read.** Utopia's `entity_facts(at=)` and
  `paths_between(at=)` answer "what held on that date". The prax
  `traverse(as_of=)` reads record time only. `changes(world=True)` lists
  what began or ended in a period but does not filter a walk.
- **Literal values.** Utopia's `facts.object_value` holds a number or a
  date as a fact with both clocks. In prax, `edges.dst` must be an entity.
- **Resolution of a conflict.** Utopia records a person's choice in
  `fact_conflicts.resolution`. In prax, `edge_conflicts` keeps the pair
  and `traverse` shows `disputed`, by design (invariant 8). No column
  records a decision on a conflict.
- **A ledger of decisions.** Utopia's `audit_events`,
  `agent_decisions` and `resolution_reviews` record who decided what and
  why. In prax, merges are stamped on the entity and endings are kept in
  `edge_endings`. A person's decision on a pair is a row of
  `entity_candidates` (`decided`, `decided_by`, migration 0014), without
  the reason they gave. A held pair keeps its reason (`held`, 0034).
- **Fact sentences in the index.** Graphiti embeds `fact`. The quote in
  `edges.evidence` is in no search index.
- **A standard export.** Utopia exports RDF with PROV-O, and Semantica
  exports OWL and SKOS. In prax, `producer` and `run` map to
  `prov:wasGeneratedBy` and `prov:Activity`. The labels map to
  `skos:prefLabel` and `skos:altLabel`. Nothing writes either mapping.
- **More rule kinds.** Utopia's rules include `sub_property` and
  person-written attribute rules (`attribute_rules`, migration 0028).
  `store.derive_rules` runs transitive, symmetric and inverse only.

## Ideas worth taking

Each idea names the prax place it would touch.

1. **`at` on `traverse` and `connect`.** Filter edges by
   `world_from <= at < world_to`, treating a missing date as unknown.
   The predicate would go beside `store.held_at` in
   `store/graph/traversal.py`, with a test that no read builds it
   inline. It is worth little until more edges carry world time.
2. **The document's date as an anchor, on the edge.** `traverse`
   already joins `meta.published` to show `stated`. A column such as
   `edges.stated_from`, copied when the edge is written, would let a
   world-time filter use it without the join. This is a numbered
   migration and a change to `store.link`, worth it only with idea 1.
3. **A decision on a conflict.** A `decided`, `decided_by` and `reason`
   on `edge_conflicts` would record a person's choice without ending
   either edge. The Review page and the `conflicts` pass of
   `prax maintain` would read it.
4. **A reason with a decision.** A `reason` column on
   `entity_candidates`, written by the Review page, and the same for the
   review queue's resolutions. The judge's precedents
   (`resolution.precedents_for`) could then show the reason beside the
   pair, as Utopia's `agent_decisions.precedents` do.
5. **Sub-property rules.** A `sub_property_of` key on a relation in the
   module YAML, linted by `ontology.lint`, and one more job kind in
   `store.derive_rules`.
6. **An RDF export.** A read-only route on the door that writes edges
   as reified statements with PROV-O and SKOS terms. It touches
   `prax.api` and the store's read side only.

Two shapes are not worth taking. Utopia's separate `derived_facts`
table would split one walk over two tables. The INFERRED edges with
`edge_premises` already answer `why`. Cognee's `tag_superseded_edges`
marks the older value superseded by record time. The `conflicts` pass
ends no fact for a conflict (invariant 8).
