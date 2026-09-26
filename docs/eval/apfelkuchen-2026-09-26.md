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

### Corrected the same day, by the pass written to prevent exactly this

The paragraph above says "38 ingredient lists in ten thousand documents",
which is true and is the wrong denominator. The `attachment` pass
(`store.maintain`, added for this) gives the right one on its first run:

    in the kitchen domain               68
    with an ingredient list             36   = 53%
    entities                       121,651
    named in a second language      13,357   = 11%

So the bridge is **not** empty. Eleven per cent of entities carry a name
in a second language, and just over half the kitchen documents have their
ingredient list cut. What is small is the kitchen domain itself: 68
documents. The apple recipe is in the 47% that lack a list, which is a
parser gap worth fixing, not a mechanism with nothing on it.

Worth keeping as written and corrected rather than quietly edited,
because the error is the one the pass exists to stop: a count against the
whole library said "nothing uses this" where a count against the domain
says "half of it does". The denominator was the finding.

## The three failures

They are independent and want different fixes:

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

## What stage C changed, and what it found under the answer

Both fixes went in the same day. `Apfelkuchen` now splits into `apfel` +
`kuchen` on the keyword side (`prax.compounds`), and the apple recipe has
its ingredient list: a list without a heading is found by what its lines
say, and the kitchen domain went from 36 lists of 68 to 56.

The German question still does not reach the recipe, and the reason is
not either fix. It is that the list is a box and not edges — nothing turns
its data into `calls_for` — and, further back, that the extraction prompt
now writes a common noun in English and keeps the German word nowhere. The
vocabulary pass used to leave that word behind as a label whenever it
renamed; the prompt that made the pass unnecessary also made the label
disappear. The repair had a side effect the prevention did not reproduce,
and the side effect was the bridge.

And with the bridge built, the question would arrive at `apple` — which in
this library is Apple Inc. The three failures are one chain.


## After the re-extraction (same day, evening)

The two fixes from the stage were applied: extraction writes a name as
the document prints it, and the watched vocabulary pass translates the
common ones and keeps the printed word as a label. The 74 kitchen
documents were re-extracted through the door (`POST /doc/{id}/extract`).

**The box became edges without a new pass.** 1,029 live `calls_for`
edges from kitchen documents; the apple recipe has sixteen ingredients,
`apple` among them. The model reads the ingredient chunk well enough
once it exists, so the "edges from the list's data, no model" idea in
the plan is not needed for this. It chose craft's `made_of` rather than
kitchen's `calls_for` for this one recipe (50 such edges in all), which
the ontology allows, since a dish is a work and an ingredient a
material. It is a wobble, not a fault.

**The pass renamed 94 names that day, and one was wrong the
interesting way.** `Apfel-auflauf`, a variant named in passing in an
elderflower-lemon bake, came back as "elderflower lemon bake": the
model took the document's title, given as context, for the answer.
Asking the 81 German names again three ways on the same model:

| context | wrong |
|---|---|
| the title (as it was) | 1 — the title taken for the answer |
| the sentence the name was said in | 4 — named the dish around it: `koreanischer eintopf` → doenjang jjigae, `japanische Küche` → oyakodon |
| none | 1 — `Djakkatou` → jackfruit, where the title had said Senegal |
| the title, "which may be about something else: name this thing" | 0 |

**The net let the commonest words out.** A name was ruled English if
it occurred in *any* English document, and `Mehl` is in eight: a
programming textbook, an operating-systems tutorial sheet. It is in
eighteen German ones, out of a quarter as many. `Zucker`, `kartoffel`,
`Eier`, `Salz` the same. Whose word it is is a rate, not an occurrence,
and the test now compares the two (English documents per English
document against the others per other document). Over the 4,103
entities the old rule had ruled out, 979 would be candidates again: 59
ingredients, and some 900 concepts and methods (`interrupts`, `SSH`,
`microkernel`) that the German course material uses more often than
the English half does. For those the model should hand the word back
unchanged, at one local call each.

**And the bridge has nothing to stand on for this word.** No entity in
the library is called `Apfel`. Thirteen German documents contain the
word, and one is a recipe, which mentions apples in passing. The route
`Apfelkuchen` → `apfel` → `apple` works (`tests/test_printed.py`), and in
this library no German document has named an apple for it to cross
on. It is the attachment lesson a third time. The route is built from
what the library holds, so a word no German document uses has no
bridge until one does. Only a dictionary would change that, and "the
library is the word list" is the choice not to have one.

## The word the other way

The library holds no German word for an apple because no German
document printed one. So the vocabulary step now also asks the other
way. For a common name of up to three words that the library's own
documents use, it asks what a reader of German would search for. It
writes the answer as an alternative label, so the name the graph shows
does not move. The languages are the expected ones that hold at least
5% of the documents: German alone here (2,051 documents; French has 41).

Dry run over the kitchen subset, read-only, with the real candidate
query and prompt:

| | |
|---|---|
| offered | 203 (22,389 library-wide, mostly research concepts) |
| time | 0.14 s a name; the candidate query 0.7–1.6 s |
| labelled | 162, `apple` → `Apfel` among them |
| the same word in German | 37 (Mozzarella, Prosecco, Tahini) |
| refused, written as they are | 4 |
| wrong | about 6: blackberry jam → Himbeermarmelade, cumin → Kümmel (caraway), white short-grain rice → Klebreis, sourdough baking → Sauerteigbrot |

A three-word limit keeps out dish titles, which an earlier probe turned
into literal translations nobody types ("Olivenpökellake-Dressing"). A
wrong label adds one false route from a German word to an English
entity. It changes no name, and `unmerge_run` takes a run back whole.

**Applied, same evening.** The rejudge pass overturned the 979 rulings
and the worker folded them (Zucker → sugar, Zwiebel → onion). The
outward labels ran over the library in 109 passes of 200 at about 90
s each: 22,382 German labels. `apple` now carries `Apfel`, and
`Apfelkuchen` expands to `apfel`, `apple` and `kuchen`. With
`domain=kitchen` the apple recipe is the **first hit**. Without a domain
the whole first page is Apple Inc. (Logic manuals, a Power Mac). The
bridge is built, and the brand collision is now the only thing between
the question and the recipe.

## Layer 1: a word the graph added is searched where its sense lives

The user typed `Apfelkuchen`, and prax added `apple` by way of the
ingredient's German label. A word only the graph added is now searched
only where the thing it names lives: its type's domains (ingredient:
the kitchen), and the domains of the documents with an edge about it.
What the user typed is searched everywhere, as before.

Three shapes were tried against the 19 German questions (targets as
before: the English question's top hit), every question's top ten, and
five German kitchen questions. All runs were read-only on the live store.

| shape | German found / MRR sum | top tens moved | Apfelkuchen first |
|---|---|---|---|
| off (before) | 9 / 4.58 | — | Logic Express manual |
| scoped words as extra rank lists | 8 / 4.83 | 7 of 44 | apple recipe |
| scoped by occurrence as well as type | 8 / 4.83 | 7 of 44 | apple recipe |
| **merged by BM25 into the one keyword list** | **9 / 4.58** | **1 of 44** | cocktail with apple juice, the recipe second |

Extra rank lists added a vote: every in-scope document that matched any
word counted twice, and "Signalsynthese" lost its paper to other
research documents. The paper was never out of scope. A term's BM25
weight does not depend on the rest of the expression, so the merged list
scores a chunk inside the scope as it scored before, and one outside
as if the word had not been added. Only the Apple question moves.

The cost is one lookup of where each added word lives, plus one scoped
keyword search. A scope that is most of the library (research, about
80%) is filtered after the ranking, drawn nine times deeper. Filtering
inside the ranking joined every matched chunk first, which took 940 ms
for "signal synthesis". A small one (kitchen) keeps the filter inside.
Measured: +0 to +20 ms on most questions, +107 ms on "Signalsynthese",
where `synthesis` has many edges to look through.

## Layer 2: a question for one of the small domains

Layer 1 can't help an English "apple cake", because the user typed
"apple". The graph can't say the word is ambiguous either. The only
things it calls "apple" are the ingredient and a stray research venue.
The brand is in the manuals' text, not in the graph under that name.

The words' own statistics didn't work. The domains are very uneven
(research 10,185 documents; kitchen 68, studio 38, workshop 35, craft
27), and in a domain of thirty documents any common German word gets a
lift of 20–96 by accident. By that measure half the eval questions
"belonged" to craft.

What worked is the search's own candidates. If at least three of the
best thirty fused hits are in one small domain (chance gives 0.2), and
those hits hold every word of the question between them, that domain's
hits get one more vote in the fusion, ranked as their own list. Nothing
is filtered. The every-word condition came from a test: in a small
library, three recipes that say "apple" once got the vote for "Apple
Loops". No recipe says "loops".

| | before | after |
|---|---|---|
| eval, English (20, by Zotero key) | 13 found, MRR 0.392 | the same |
| eval, German (19) | 7 found, MRR 0.131 | the same |
| eval top tens moved | — | 0 of 39 |
| apple cake | recipe 4th, manuals 2nd and 3rd | recipe 2nd, manuals 3rd and 4th |
| apple juice | cocktail 1st, manuals 2nd–4th | cocktail, recipe, then manuals |
| Apfelkuchen | two recipes, then Melodyne, maths | the top four all kitchen |
| Apple Loops, apple logic, apple | manuals | unchanged, no vote |
| cost | | +0 to +25 ms |
