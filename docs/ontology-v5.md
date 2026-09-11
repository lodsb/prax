# Ontology v5: organizations, mentions, and the queue as witness (2026-09-12)

After the local model read the whole library (`docs/eval/extractors-local-
2026-09-11.md`) the review queue held 20,000 items. The typed ones were
handled by rules (`prax.review.apply_typing_rules`, howto 3e); the unmapped
ones say what the ontology could not hold. Counted by the relation the
model named:

| the model wanted | items | v5 answer |
|---|---|---|
| `affiliation`, `affiliated_with` (person to institution) | 662 | entity type `organization`, relation `affiliated_with` (author to organization) |
| `mentions`, `discusses` (named as related work, not used) | 640 | relation `mentions` (paper, page or project to tool, method, dataset or concept), described as the weakest relation |
| `funded_by`, `developed_by` | about 100 | `funded_by` (paper or project to organization), `developed_by` (tool to organization or author) |
| `contrasts` with a tool or concept, `extends` a paper, `about` a dataset, `contains` and `has_part` between papers | 356 typed leftovers | ranges widened: `contrasts` to tools and concepts, `extends` to papers, `about` to datasets, `part_of` from paper to paper (a chapter in a book, a paper in a proceedings volume) |
| `advised_by`, `supervised_by`, `author`, `author_of` without types | 1,400 | no change: `advised_by` and `authored_by` existed; rules assign the types from the shape of the names |
| `related_to` | 140 | still out: too vague to traverse (the v2 decision) |
| `date`, `describes`, `venue` as relations | 300 | no: metadata, or synonyms of `about` and `published_in` |
| `part_of` for course sheets and homework | 2,200 | no type for a semester of one university; search finds them |

`organization` also takes what v3 parked as "a research project a paper
mentions": a funded programme or consortium is an organization, and the
`project` type stays reserved for this library's own threads.

The extraction prompt derives from the file, so the next pass emits the
new types and relations on its own. The bump re-selects every extracted
document as before; the queue was replayed and the rules for unmapped
items were run instead (numbers in the plan), which needs no model.
