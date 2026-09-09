# Document-level retrieval field, 2026-09-10

Question: why does "schematic" not find the library's one schematic (the
UREI 1176LN drawing, described by Claude vision), and what fixes it without
hurting the queries that already worked?

## Diagnosis

Chunk scoring rewards repetition. "schematic" matches 1,311 chunks; the
EAGLE CAD manual alone has 126 of them, each using the word several times,
so it and a German help page fill the top of both the BM25 and the vector
list. The 1176's description says "schematic" twice in one 600-character
chunk. "1176 schematic" lands at rank 17 because "1176" also matches page
and figure numbers (the TikZ manual wins). As soon as the query names what
the thing is ("UREI 1176", "limiting amplifier schematic") retrieval works:
the identity lives in one short sentence at the top of the description,
and chunk scoring treats that sentence like any paragraph.

## Change

Migration 0005 adds a document-level field: title, kind words (image, PDF
document, web page, note, source code, image description), creators,
venue and year, the extraction summary, and an image description's opening
paragraph. It is indexed in `documents_fts` and embedded once per document
into `vectors-doc-<model>.usearch`. `search` fuses four rank lists at
document level: chunk BM25, chunk KNN, field BM25, field KNN. The field
follows a document's text and metadata (`index_text`, `set_meta`);
`refresh_document_fields.py` is the backfill. `doctype` filters by
document type, which the same kind words back.

## Library set (62 queries, `tests/eval/queries-library.yaml`)

| mode | hit@1 | hit@3 | MRR | MRR by style |
|---|---|---|---|---|
| fts (unchanged) | 0.74 | 0.90 | 0.82 | keyword 0.87, paraphrase 0.69, structure 0.88 |
| vec (unchanged) | 0.74 | 0.81 | 0.79 | keyword 0.84, paraphrase 0.65, structure 0.85 |
| hybrid before (2026-09-08) | 0.73 | 0.87 | 0.79 | keyword 0.83, paraphrase 0.67, structure 0.90 |
| hybrid with the field | 0.85 | 0.92 | 0.89 | keyword 0.97, paraphrase 0.75, structure 0.86 |

The field lifted hybrid from below FTS-alone to well above it: keyword
queries are nearly solved (0.97), paraphrases gain too (0.67 to 0.75).

## Weighting the field list

With four equally weighted lists, the 1176 still sat at rank 18 for
"schematic": the field BM25 list had exactly two hits (the 1176 first, the
LA-2A drawing second), and one rank-1 list is worth less than two rank-2
chunk lists. A weight on the field list was measured:

| field weight | hit@1 | MRR | "schematic" | "1176 schematic" |
|---|---|---|---|---|
| 1.0 | 0.855 | 0.892 | 18 | 2 |
| 1.5 | 0.855 | 0.888 | 10 | 2 |
| 2.0 | 0.839 | 0.875 | 1 | 1 |
| 3.0 | 0.839 | 0.869 | 1 | 1 |

Every query that lost at weight 2 was a 12-to-17-word paraphrase, where
the short field matches on incidental words; queries of five words or
fewer only gained. So the weight follows query length: 2.0 up to three
words, fading linearly to 1.0 at seven. Result: hit@1 0.855, MRR 0.892
(identical to weight 1 on the eval set), "schematic" and "1176 schematic"
at rank 1, "LA2A" finds the LA-2A drawing first.

## Cost

9,235 fields, 9,235 vectors (one per document, a 14 MB file), the backfill
and embedding took about eight minutes on the CPU. A hybrid query gained
two small lists, under 10 ms.
