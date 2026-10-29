# Graph files: a piece of the graph, exported and imported

`prax export` writes what a seed reaches as one file. `prax import graph`
reads such a file into another library. The code is `prax.graph.graphio`; the
door's routes are `GET /graph/export` and `POST /graph/import`.

    prax export --project synth -o .prax/graph.jsonl
    prax export --domain kitchen -o kitchen.graph.jsonl
    prax export --entity "wave digital filter" --hops 2 -o wdf.graph.jsonl
    prax import graph .prax/graph.jsonl --name synth --dry-run
    prax import graph .prax/graph.jsonl --name synth

## The seed

Exactly one of these:

| option | the documents | the edges |
|---|---|---|
| `--project NAME` | tagged `project:NAME`, and the page `project-NAME` | the live edges of those documents |
| `--domain NAME` | those whose `meta.domains` names the module (a document without a set is left out) | the same |
| `--tag TAG` | those tagged `TAG` | the same |
| `--entity NAME` (`--type`, `--hops 1` or `2`) | the source documents of the edges | the entity's own edges, and with two hops those of every entity it touches |

`--history` adds the ended edges (`valid_to` set). Without it an export
holds the live graph only.

`--as TOKEN` makes the file for a named token's holder: it holds only
what that token sees (`GET /graph/export?as=`), no document it may not
read and no edge from one. No export holds who a picture shows (a
`depicts` edge), whoever it is for: faces never leave the library.

## The file

JSON lines, UTF-8, one object per line, each with `kind`. Keys are
sorted and every group is in a stable order, so an export kept in a
repository diffs line by line. The first line is the header:

| key | what |
|---|---|
| `format` | `prax-graph/2`; an import reads `/1` and `/2` and refuses any other |
| `exported_at` | UTC, `store.now()` |
| `seed` | the options that chose it |
| `ontology` | the composed version, e.g. `core7+computing2+craft1+electronics1+kitchen2+research9+society1+studio5+workshop2` |
| `modules` | each ontology module's YAML, as text |
| `counts` | `documents`, `edges` |

Then, in this order:

- **`document`**, by `hash`: the sha256 of the original, `id` (in the
  exporting library, for relinking pages), `title`, `source_url`,
  `mime`, and from `meta` the ids (`doi`, `arxiv`, `isbn`, `zotero`),
  `domains`, `tags`, `lang` and `summary`
  (`store.document_identities`). Never the bytes.
- **`entity`**, by type and name: `name`, `type` (canonical), `gid`
  (its global id, `docs/global-ids.md`), `aliases`, and `labels` (every
  other name it answers to, with `lang` and `kind`). The aliases are the
  `gid`s of the rows folded into it and of other libraries' entities
  decided to be it.
- **`edge`**, by source document hash and triple: `src`, `src_type`,
  `rel`, `dst`, `dst_type` under the canonical names, and every
  provenance column: `confidence`, `evidence`, `producer`, `run`,
  `ontology_version`, `valid_from`, `valid_to`, `ingested_at`, and
  `source_hash` for the source document. `src_gid` and `dst_gid` are
  its ends' global ids, and `gid` is the claim's (`graphio.edge_gid`). A
  page's own edges (from its links) are left out, because importing the
  page makes them again, and so is an INFERRED edge a rule derived from
  premises: the receiving library derives its own.
- **`page`**, by slug: `gid`, `slug`, `title`, `page_kind`, and `revisions`,
  each with `revision`, `author`, `note`, `created_at` and the full
  `text`.

Not exported: chunks, vectors, the review queue, the originals, the
regions of the library (they are rebuilt from the graph), the faces and
what a person said of them.

## Import

An import is a producer, `import:<name>` (`--name`, default the file's
stem), with a run per file (`import-<name>-<stamp>`). A file of format
2 names its entities by global id and is read as `docs/global-ids.md`
"The import" says:

1. Each entity lands where it belongs (`store.settle_foreign`), and
   its labels are added once it has a row here:
   - `own`: its `gid`, or one of its aliases, is one this library minted.
   - `same`, `new` or `left_out`: an earlier import of the same name
     decided it.
   - `likely`: a thing here answers to its name. It waits on the Review
     page ("from other libraries").
   - `new`: nothing answers to its name. Its first edge makes it.
2. Each live edge whose two ends are settled is written through
   `store.link` onto the local entities, unless its claim is live here
   already for that document (`same`, counted). An edge with a `likely`
   end is held until a person answers; one with an end left out is not
   written.
3. The source's live edges the file no longer says are ended under the
   import's run (`retired` in the report).

The report says where the entities landed (`landed`, a count per
state) and what became of the edges (`linked`, `same`, `held`,
`left_out`, `queued`). A file of format 1 names its entities by name
alone, and is read as before:

1. The documents are matched by hash to this library's. An edge whose
   source document is held here points at it (`source_doc`). One whose
   document is not keeps the title and the first twelve hex digits of
   its hash in the evidence.
2. The last import of the same name is retired first
   (`store.retire_run(producer=…)`): importing a newer file makes the
   graph converge on it.
3. Each live edge goes through `store.link` under the import's producer
   and run. The evidence keeps the original, with
   `[imported: <producer>/<run>]` appended. A name this library knows,
   as its own name or as the only entity of that type with such a label,
   lands on that entity. An ended edge is counted and left out.
4. A triple the local ontology refuses goes to the review queue
   (`store.queue_review`) with the reason and the version it was
   written against. A later ontology version takes it in through the
   queue's replay.
5. Each entity's labels are added as alternative labels.
6. A page of format 2 is the page of its `gid` here, never one with an
   equal slug. A new one whose slug is taken gets the first free
   `<slug>-2`, and keeps the `gid` it was minted with. A page of format 1
   is found by its slug. A page that does not exist here is written with all its revisions, in
   order, each with a note naming the source and the original revision.
   One that exists with the same text is left alone. One with other
   text gets the imported text as a new revision, and the note says
   this library's page differed. An agent's text over a person's page
   is refused here as anywhere, and the report lists the slug. Links
   `[title](#doc/N)` are rewritten from the exporting library's ids to
   this one's. A link to a document not held here keeps the title
   alone.

`--dry-run` (`dry_run=true`) writes nothing. It reports the documents
held, the edges the local ontology takes and the ones it would queue,
and the pages that are new, changed or the same.

## Kept beside a project

A project that keeps its graph in its repository exports it once by
hand:

    prax export --project synth -o .prax/graph.jsonl

From then on the Claude Code plugin's session-end sync (`prax sync
--if-auto`) exports it again after the project's files are sent, and
writes the file only when more than its header's `exported_at`
changed. A copy kept in git then diffs only when the graph did. A
project without the file gets none: the first export is the choice to
keep one.
