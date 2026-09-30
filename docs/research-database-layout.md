# The database layout after 30 migrations (research, AF)

The second research question of stage AF in `docs/PLAN.md`: does the
schema still fit? Measured on a copy of the store on 2026-09-30, and
checked against the live store read-only where a number mattered. The
copy was changed during the measurement (indexes tried, `ANALYZE`,
`VACUUM` on further copies) and thrown away. Nothing here is built.

**The short answer.** It fits. Nothing calls for another engine. The
slow queries have two causes: indexes that do not match the queries,
and filters that read `documents.meta` as JSON row by row. Five small
migrations and two code changes would fix most of it.

## Sizes

The store is 2.95 GB in 4 KB pages, with no free pages, WAL mode, no
auto-vacuum.

| table or index | rows | size |
|---|---|---|
| `chunks` | 1,436,638 | 1,601 MB |
| `chunks_fts` (its data) | | 763 MB |
| `edges` | 374,643 (212,023 live) | 97 MB |
| `chunk_embeddings` and its model index | 1,275,825 | 88 + 40 MB |
| `documents` | 13,361 | 78 MB |
| `entity_labels` and 7 indexes | 249,289 | 27 + 38 MB |
| `review_queue` | 123,873 (19,500 open) | 47 MB |
| `entities` | 198,997 (160,571 canonical) | 17 MB |

A chunk's bytes are its text (875 MB), its heading path (128 MB, 384,000
distinct values over 1.44 million rows), `data` (143 MB) and the locator
(77 MB). `chunks` has 308 MB of unused space, mostly half-filled pages
rather than deleted rows. `VACUUM` gives back 5.5%. With 8 KB pages it
gives back 9%, and query times stay the same.

## `documents.meta`

`meta` holds 53 MB over 13,361 documents: 3.3 KB at the median, 310 KB
at the most. 4,113 documents have a `meta` over 4 KB, which overflows a
page. Every scan that reads it costs 80 to 110 ms. Without the cold keys
the same scan costs 51 to 70 ms.

The largest keys by bytes are `parse_history` (6.9 MB),
`extraction_history` (4.9 MB), `sections` (4.8 MB), `summary` (4.3 MB)
and `summaries` (3.7 MB).

`parse_history` grows without a bound. Documents 10380, 11872 and 12036
hold 902, 789 and 759 entries. They are one loop of 2026-09-28. The DjVu
reader refused each book 897 times in 14 hours ("281 pages without a
text layer exceeds the OCR budget of 60"). Each refusal was recorded,
and the book was handed out again. Stage Y's windowed OCR read the books
that afternoon, and the loop stopped. Nothing caps the history, and a
refusal is still no reason not to hand the book out again.

The paths the store's SQL filters by, by uses: `$.retired` 55,
`$.source` 16, `$.lang` 14, `$.domains` 13 (and every `domain_clause`),
`$.genres_by` 7. Expression indexes exist for source, the retired side
of retired, domains, the ontology stamp, promote, the Zotero parent and
a requested extraction. There is none for the live side of retired, for
the language, or for the domain inside a set.

## The slow queries

Times are warm, the best of two after a first call.

| call | ms | cause |
|---|---|---|
| a search scoped to a domain (`search("feedback delay network")`) | 650–750 | the keyword leg under a domain scope, 550–590 ms: `domain_clause` reads `meta.domains` with `json_each` once for each of 51,000 matching chunks. Unscoped it is 65 ms |
| `_share`, a domain's share of the library | 140–180 | a scan with `json_each`, paid again after every ingest |
| `traverse`, `traverse_map`, `senses` | 30–56 | two lookups that scan (below) |
| `unlabelled_names("de")` | 780–810 | a document's language read from `meta` for each of 424,000 edges |
| `foreign_names` | 970–1020 | the same, and an aggregate over every edge |
| `select_for_extraction` | 208; 1,500 after an ontology bump | `sum(length(text))` over a document's chunks, where `documents.text_len` holds the same |
| `list_documents(domain="kitchen")` | 270 | `json_each` over every document, twice |
| the embedding reconcile at start | 100 warm, 1,780 cold | a scan of `chunk_embeddings` by model and time |

The two lookups behind a slow traverse:

- `label = ? COLLATE NOCASE`, in five places (`traversal.py`,
  `retrieval.py`, `edges.py`, `context.py`, `labels.py`). The index on
  `label` is case-sensitive, so each lookup scans it: 12 to 26 ms warm,
  302 ms cold on the live store.
- `id = ? OR canonical_id = ?`, in `traversal.py` and `edges.py`. The
  index is on `COALESCE(canonical_id, id)`, not on the column, so each
  is a scan of `entities`, 10 to 20 ms.

## The thresholds of CLAUDE.md

- **Vectors.** The chunk index is 1.58 GB for 1.28 million vectors,
  1,242 bytes a vector where CLAUDE.md recorded 917. Chunk ids reach 3.68
  million for 1.44 million rows, so rechunks probably left slots behind.
  The document index has not been merged since 2026-09-12, and its delta
  (11.5 MB) is larger than its main file (8.8 MB). At 1.6 GB mapped, the
  int8 index is due for the Q6A.
- **Entities.** 160,571 canonical entities, past the line of 50,000 to
  100,000 for moving the graph to Kùzu. By latency the line is not
  passed: with the two indexes below a traverse takes 0.7 to 4.5 ms. The
  threshold would be better said as a traverse time.
- **Archive.** 63,628 files in 256 folders, 194 to 290 each, far from
  10,000 a folder.
- **Maintenance.** The WAL is 48 MB. Nothing sets `journal_size_limit`,
  and nothing runs `VACUUM`, `ANALYZE`, `PRAGMA optimize` or a
  checkpoint. `ANALYZE` alone changes one plan, and an FTS `optimize`
  changes nothing measurable.

## Structure

- Two redundant indexes: `idx_chunks_doc` (18 MB) repeats the primary
  key's `(doc_id, seq)`, and `idx_entity_labels_entity` (3 MB) repeats
  `idx_entity_labels_one`. Dropped on the copy, the plans move to the
  other index and the times stay the same.
- `idx_chunk_embeddings_model` spends 40 MB on a column with one value.
- `review_queue` holds 104,000 resolved items of September, 47 MB. How
  long a resolved item is kept is not decided anywhere.
- Every table is used, and every column checked is read.
- Beside the store: the abandoned `multilingual-e5-small` indexes (1.04
  GB) and `prax-before-heal.db` of 2026-09-12 (1.7 GB).

## Proposals, most gained first

1. **Two indexes for the graph** (a migration). An index on
   `entity_labels(label COLLATE NOCASE)` and one on
   `entities(canonical_id)` where it is set; the two redundant indexes
   dropped. A traverse goes from 30–56 ms to 0.7–4.5 ms, and the label
   lookup of a query's expansion from 12–26 ms to under 0.1 ms. About 10
   MB more, 21 MB less. Very low risk.
2. **A table of a document's domains** (a migration). `document_domains
   (domain, doc_id)`, written by `store.set_domains` beside
   `meta.domains`, which stays the record. `domain_clause` reads it. The
   scoped keyword leg goes from 550 to 107 ms, so a search scoped to a
   domain gets about 450 ms faster, and `_share` goes from 138 to 3 ms.
   The risk is a second copy to keep in step: one writer, and a `heal`
   check that compares the two. Without a migration, a filter on the
   dozen distinct sets through the existing index measured 110 ms.
3. **Indexes for the live documents and the language** (a migration).
   Listing the live documents goes from 84 to 2.4 ms and
   `unlabelled_names` from 800 to about 300 ms. `foreign_names` also
   needs rewriting to find the entities of non-English documents first:
   1,020 to 161 ms, the same answer.
4. **`select_for_extraction` on `text_len`** (code). 208 ms a hand-out,
   and 1,500 to 87 ms after an ontology bump. `text_len` counts the whole
   artifact, reference entries included, so the 500-character bar moves a
   little.
5. **The embedding stamps** (a migration). An index on
   `chunk_embeddings(model, embedded_at)` in place of the one on the model:
   the reconcile at start goes from 1.8 s cold to 0.4 ms.
   `_all_chunks_embedded` could compare the highest chunk id with a
   stored mark instead of counting 1.44 million rows, which costs 190 ms
   each embed hand-out.
6. **A smaller `meta`** (code and a data pass). Cap `parse_history` and
   `extraction_history`, at 20 entries for example. Stop handing out a
   document a reader has refused for a reason that will not change.
   Move a briefing's text out of `meta`, since it is a page's text. Scans
   get about twice as fast, and the growth stops.
7. **Maintenance** (a step of `prax maintain`, not a migration). `PRAGMA
   optimize` after a large pass, `journal_size_limit`, and a `VACUUM` now
   and then with the door stopped, at 8 KB pages if the user wants the 9%.

## What the user decides

- The migrations 1 to 3 and 5. They add indexes and one table, and they
  change no data a person wrote.
- The cap on the histories: the entries past it are dropped from `meta`,
  which is a change to data.
- How long resolved review items are kept.
- Whether the e5 indexes (1.04 GB) and `prax-before-heal.db` (1.7 GB)
  are deleted.
- Whether the entity threshold of CLAUDE.md becomes a traverse time.
