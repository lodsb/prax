# "hast du ein rezept fuer apfelkuchen?" — three failures in one question

2026-09-26. The user asked the library, in German, for an apple cake
recipe. It said no. Asked again in English — "apple pie or apple bars?" —
it found *Helen Goh's recipe for brown butter apple and blackberry bars*
immediately, quoted its 750 g of apples, and named its author and
publisher from the graph.

The same library, the same document, one question in two languages. This
is what went wrong, and it is three unrelated things that happen to fail
together.

## 1. German compounds are one token, and that token is nowhere

    Apfelkuchen        fts →  0 hits
    Apfel              fts →  5 hits
    Apfel Kuchen       fts →  5 hits
    Rezept Apfelkuchen fts →  5 hits  (on "Rezept" alone)

`Apfelkuchen` is a single FTS5 token that appears in no document.
`Apfel` and `Kuchen` each match. Nothing decomposes a German compound, so
for that query the keyword half of the hybrid contributed **nothing** —
which is why the German answer came back holding a chickpea pan, a LaTeX
guide and insertion sort: those are what the vector side alone returned.

German is 1,988 of this library's documents and compounding is how the
language builds nouns, so this is not an edge case. It is also the
cheapest of the three to fix: FTS5 takes a custom tokenizer, and a
compound splitter needs a word list rather than a model.

## 2. In this library, "apple" means Apple Inc.

    apple       → Logic Pro 7 · Logic Express 7 Plug-In-Referenz · …
    apple bars  → Helen Goh's recipe (rank 1)

**Even in English the fruit loses.** The library holds dozens of Logic and
Apple manuals, and BM25 is right to rank them higher for a bare "apple" —
they say it far more often. It took "bars" to disambiguate.

So the German failure is not purely a language failure. A query for a
common noun collides with a brand that owns the word in this corpus, and
that is polysemy, which prax already has a place for: the `proposes` pass
and the review queue deal with one name meaning two things in the graph.
Nothing deals with it in retrieval.

## 3. The bridge built for exactly this has nothing standing on it

The designed route from a German query to an English document is
`entity_labels` (`docs/identity.md`): an `apple` entity of type
`ingredient`, carrying `Apfel` as its German label, so the German word
reaches the English recipe through the graph. It is what made 32 of 80
German queries reach new documents on 2026-09-24
(`docs/eval/query-dictionary-2026-09-24.md`).

It could not fire here, because **there is no `apple` ingredient entity at
all.** What the graph has is:

    Apple Macintosh (tool) · apple loops (tool) · Apple Computer, Inc. (author)
    Clyde W. Holsapple (author) · Scrapple (tool) · AppLens · Java Applets

— the brand, and false friends. Not the fruit.

And the reason is further back still. The library has **38 `ingredients`
chunks in total**, and the recipe in question has none:

    doc 10070  domains: ['kitchen']  lang: en  extracted: 18 edges
    chunks: text ×4, comment ×2, figure ×1

The document is correctly in the kitchen domain and was extracted. But
`prax.ingredients` cuts an ingredient list from a "Zutaten" or
"Ingredients" heading, and the Guardian writes its quantities into the
prose. No heading, no `ingredients` chunk, no ingredient entities, no
label to cross from.

## What this says

The three failures are independent and want different fixes:

| | fix | shape |
|---|---|---|
| the compound | a splitter in the FTS tokenizer | a word list, no model |
| the collision | disambiguation in retrieval, not only in the graph | design |
| the empty bridge | recognise an ingredient list without a heading | a parser change, then re-extract the kitchen domain |

The third is the one worth noticing, because it is not a bug in anything.
The identity work of 2026-09-24 built a route from a German word to an
English document and measured it working. Almost nothing is connected to
it: 38 ingredient lists in ten thousand documents. A mechanism that
works and is unattached looks exactly like a mechanism that does not
work, from the outside.

Nor would the multilingual embedder have saved this. It was reverted the
same day for being worse on both counts measured
(`docs/eval/embedder-multilingual-2026-09-25.md`), and on this question
the vector side found the recipe only for "apple cake" — the entity route
was empty either way.
