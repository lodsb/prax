# Ontology v2

Adopted on 2026-09-10 in the conservative form: `uses`, `about`, `extends`
and `proposes` widened; `defines`, `contrasts` and `advised_by` added;
`related_to` and the music `work` type left out (co-occurrence links in
the overview cover the first, the second belongs with its importer). The
replay linked 377 of the 551 typed items; 170 stay open, mostly `cites`
pointing at tools and methods, which are relation errors. The rest of this
note is the proposal as written from the queue, kept for the reasoning.

The evidence is the review queue after the first 1,021 extracted documents
(2026-09-09): 551 complete triples that only the domain or range rules
rejected, and 2,033 "unmapped" triples where the model asked for a
relation the ontology lacks. After editing `ontology.yaml`, run

    python scripts/replay_review.py --dry-run     # how many typed items would link
    python scripts/replay_review.py --commit

which links the typed items the new rules accept, evidence and source
intact, without calling a model. The version bump also re-selects every
document for extraction; that is the intended trigger for a later delta
pass, not something to run right away.

## What the queue rejected, and the proposed rule change

| rejected shape | count | proposal |
|---|---|---|
| tool uses tool | 87 | `uses` domain gains `tool` (a library building on another) |
| paper cites tool | 53 | no rule change: this is `uses`; drop or relink by hand |
| paper about method | 48 | `about` range gains `method` and `tool` (a paper about NMF the technique) |
| paper uses concept, method uses concept | 83 | `uses` range gains `concept` (applies an idea); `about` remains "what the document is on" |
| tool uses method | 29 | covered by `uses` domain `tool` |
| claim about method | 25 | covered by `about` range `method` |
| paper extends concept, tool extends tool | 31 | `extends` domain gains `paper` and `tool`, range gains `tool` |
| paper proposes concept | 15 | `proposes` range gains `concept` |
| paper cites method | 16 | no rule change: drop or relink as `uses` |
| author authored_by author | 11 | nonsense, drop |
| method about concept | 10 | no rule change |

## Relations the model asked for (2,033 unmapped items, 553 distinct names)

| wanted | count | proposal |
|---|---|---|
| cites (reference numbers, no title) | 632 | drop in bulk: the citation importer now supplies the reference network |
| proposes / uses / about / authored_by / published_in / extends / implements / supports | ~380 | existing relations; the model left out the types. Link by hand from the review view, or a later typing pass |
| related_to, related to | 45 | **new** `related_to`: concept/method/tool to the same kinds, symmetric in meaning, weak by nature; the extractor should use it sparingly and mark it `AMBIGUOUS` unless the text says how |
| discusses, describes, mentions | 37 | no new relation: prompt rule "a concept the document discusses at length is `about`; a passing mention is nothing" |
| introduced, developed, proposed (by a person) | 59 | `proposes` domain gains `author` (Smaragdis proposed NMF for audio) |
| defines | 18 | **new** `defines`: paper/claim to concept, "the document gives the term its definition" |
| contrasted with, compared to | ~15 | **new** `contrasts`: paper/method/claim to method/claim/paper; `contradicts` stays for claims that cannot both hold |
| advised_by | 10 | **new** `advised_by`: author to author (theses; academic lineage) |
| composed, performed | ~12 | the music side: **new** entity type `work` (a piece or recording) with `composed_by` and `performed_by` (work to author). Optional; it opens the door to the personal collection you mentioned |
| everything else | long tail | leave in the queue |

Two prompt rules that are not ontology changes but belong in the same
release, because the queue and the graph show the confusion:

* "A technique is a `method`; the field it belongs to is a `concept`. Do
  not create both for one name." (`empirical mode decomposition` exists
  as both today, with 46 and 32 edges.)
* "The document's own reference numbers are never entity names."

## Proposed `ontology.yaml`

```yaml
version: "2"

entity_types:
  paper: {description: unchanged}
  author: {description: unchanged}
  venue: {description: unchanged}
  concept: {description: unchanged, plus "the field a technique belongs to; never the technique itself"}
  method: {description: unchanged, plus "a technique is a method, not also a concept"}
  dataset: {description: unchanged}
  tool: {description: unchanged}
  claim: {description: unchanged}
  work:              # optional, the music side
    description: >-
      A musical work or recording: a piece, an album, a performance.
      Name as the document names it.

relation_types:
  cites:        {domain: [paper], range: [paper]}                       # unchanged
  authored_by:  {domain: [paper, work], range: [author]}               # work added if work is adopted
  published_in: {domain: [paper], range: [venue]}                      # unchanged
  about:        {domain: [paper, claim], range: [concept, method, tool]}
  implements:   {domain: [tool, method], range: [concept, method]}     # unchanged
  extends:      {domain: [paper, method, concept, tool], range: [method, concept, tool]}
  contradicts:  {domain: [claim], range: [claim]}                      # unchanged
  supports:     {domain: [claim, paper], range: [claim]}               # unchanged
  uses:         {domain: [paper, method, tool], range: [dataset, tool, method, concept]}
  proposes:     {domain: [paper, author], range: [method, tool, claim, concept]}
  related_to:   {domain: [concept, method, tool], range: [concept, method, tool],
                 description: the two are connected in the text without a more specific relation; AMBIGUOUS unless the text says how}
  defines:      {domain: [paper, claim], range: [concept],
                 description: the document gives the concept its definition}
  contrasts:    {domain: [paper, method, claim], range: [method, claim, paper],
                 description: the document sets the two against each other}
  advised_by:   {domain: [author], range: [author],
                 description: doctoral or research supervision stated in the text}
  composed_by:  {domain: [work], range: [author]}                      # only with work
  performed_by: {domain: [work], range: [author]}                      # only with work
```

Write the real file with full descriptions in the v1 style: the extractor
builds its prompt from them, so each description is an instruction.

## What v2 does not fix

* The 53 `paper cites tool` and 16 `paper cites method` items are relation
  errors, not rule gaps; the replay leaves them open.
* Unmapped items have no types, so a wider ontology does not link them;
  the review view's row form does, one at a time, and a typing pass with a
  small model is the batch option.
* Cross-type duplicates already in the graph (a concept and a method with
  one name) are a resolution rule, not an ontology change.
