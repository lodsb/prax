# A library in two languages: what it costs, and what to do

A desk study, 2026-09-23, of the multilingual question. prax was built
on English defaults and the library is a fifth German. This note
measures what that costs today, sets out what the field does about it,
and says in what order prax should act. Everything in the first section
was measured on the live store the day it was written.

## What is actually there

| measured | 2026-09-23 |
|---|---|
| documents with text | 10,233 |
| English | 7,368 (72.0 %) |
| German | 2,131 (20.8 %) |
| French, Spanish | 47, 21 |
| too short to tell | 666 (6.5 %) |
| documents recording their language | **0** |
| entities | 143,412, of which 2,727 carry an umlaut or ß |
| the embedding model | `bge-small-en-v1.5`, English-only, over all of it |

The language guess is a stopword count over the first 6 kB of each text
artifact, which is coarse but not close to the line: the German fifth is
German. Nothing in the store records it. `documents.meta` has no `lang`
key anywhere, so neither retrieval nor extraction can act on the
language of what it is reading.

### What it costs in search

Four queries against the live door, same library, same day:

| query | what came back |
|---|---|
| `noise reduction in audio signals` | Multi-Microphone Noise Reduction…, Musical Noise Reduction…, Waves Z-Noise, Improved noise reduction in audio signals |
| `Rauschunterdrückung in Audiosignalen` | an ethnomusicology journal, packaging tips, a diploma thesis |
| `olive oil` | English recipes and articles about oil |
| `Olivenöl` | German recipes only |

The German query for a subject the library holds dozens of papers on
returns nothing about it. Both sides of the hybrid fail at once, and for
different reasons. BM25 cannot match `Rauschunterdrückung` against
*noise reduction* because they share no token, which is what a keyword
index is. The vector side, which is supposed to carry meaning across
exactly this gap, is an English-only model: `bge-small-en-v1.5` was
trained on English and never saw German, so the German query lands
nowhere near the English passages. A fifth of the library is embedded by
a model that does not read it.

### What it costs in the graph

The model behind the extraction sometimes translates and sometimes does
not, so one thing arrives under several names:

    Olivenöl (ingredient, 10 edges)    olive oil (ingredient, 8 edges)
    olivenöl (ingredient, 5 edges)     olive oil (concept, 1 edge)

    Knoblauch (9)  knoblauch (2)  Garlic (1)  garlic (6)
    Zwiebel (8)    zwiebel (1)    onion (4)
    Salz (7)       salz (4)       salt (7)
    Pfeffer (3)    pfeffer (3)    black pepper (3)

Three faults sit on top of each other here, and it is worth keeping them
apart because they have different answers. **Case**: `Olivenöl` and
`olivenöl` are the same string to a reader and not to the resolver.
**Language**: `Olivenöl` and `olive oil` are the same thing in two
languages. **Type**: `olive oil` exists as both an `ingredient` and a
`concept`, and `filter` exists five times over (concept, method, tool,
component, feature). Only the middle one is the multilingual problem.
The screenshot that started this. A German recipe whose ingredient list
reads *Sellerieknollen, Olivenöl, Salz* has a graph panel reading
*celeriac, olive oil, salt*: the model translated the entities and left
the text alone, so neither name reaches the other.

## What the field does

### Retrieval across languages

Two approaches, and the literature does not crown either.

**A multilingual embedding model.** One vector space for every language,
so a German query lands near an English passage that means the same.
This is the structural fix: nothing else in the pipeline changes, and it
works for the languages the model was trained on.

| model | parameters | dim | licence | multilingual retrieval (MTEB, 18 languages) |
|---|---|---|---|---|
| `bge-small-en-v1.5` (prax today) | 33 M | 384 | MIT | English only |
| `multilingual-e5-small` | 118 M | 384 | MIT | 50.9 |
| `granite-embedding-97m-multilingual-r2` | 97 M | 384 | Apache 2.0 | 60.3 |
| `granite-embedding-278m-multilingual-r2` | 311 M | 768 | Apache 2.0 | 65.2 |
| `EmbeddingGemma` | 300 M | 768 (128 truncatable) | Gemma terms | 61.2 |
| `bge-m3` | 568 M | 1024 | MIT | strong, and four times the size |
| `jina-embeddings-v3` | 572 M | 1024 | CC-BY-NC (non-commercial) | strong |

The two that matter for prax are the 384-dimension ones, because
`VEC_DIM` is 384 and the usearch files are keyed by it. IBM's 97 M model
claims the best retrieval quality of any open multilingual embedder
under 100 M parameters, and ships ONNX and OpenVINO weights for CPU
inference. `multilingual-e5-small` is nine points behind it. It is also
**already registered in `prax.embeddings.MODELS`**, with its repo, its
ONNX files, mean pooling and the `query: ` prefix. It costs three to
four times bge-small's compute per chunk, which the module's own comment
already records.

**Translating the query.** Ask a small model to render the query in the
other language, search twice, fuse. Measurements in the wild find this
often beats a multilingual embedder outright, and it is the cheaper path
when a local model is already running — which on this host it is. It
also helps the keyword side, which a multilingual embedder cannot: BM25
will never match across languages, so the only way a German query
reaches an English passage lexically is to send the English words too.

The two are not exclusive. The honest reading of the field is that a
multilingual embedder fixes the vector half structurally and query
translation fixes both halves opportunistically.

### Normalization in the graph

The task is cross-lingual entity label mapping, and it has a clear
result behind it. Over Wikidata labels in ten languages, methods built
on **sentence embeddings outperformed statistical word alignment and
string similarity, even across scripts**. They improved the mapping by
up to 20 points of F1. Medical entity normalization toolkits (xMEN) are
built the same way: a cheap candidate generator (embeddings and string
similarity together) followed by a ranker.

That is the same shape as prax's own resolution pass: candidates by
normalized name and by name embedding, then a tier that adjudicates. The
change is not a new mechanism, but a different embedder inside an
existing one.

The dictionary route is the other half. Wikidata carries labels and
aliases in hundreds of languages under one identifier, which is exactly
a translation table for entities that are notable; 2026's FactNet adds a
cross-lingual normalization layer over Wikidata claims with span-level
evidence. For a closed domain (ingredients, say) a hand-made list of a
few hundred pairs is worth more than any of it and takes an hour.

The representation the field settled on long ago is one canonical entity
with language-tagged labels beside it. A preferred label per language,
alternative labels for the rest. prax has the second half already:
`merge_entities` keeps 18,931 aliases. It has none of the first, because
an alias carries no language.

### Knowing the language at all

`fastText`'s `lid.176.ftz` is 917 kB, recognises 176 languages and is
fast enough to run over every artifact; `lingua-py` is the better choice
on short text — titles, queries, a chunk of four lines — and covers 75
languages. A document's language is one field; a chunk's language
matters too, because captured pages mix (an English abstract over a
German paper, a German comment section under an English post).

## What prax should do, in order

**1. Record the language.** A `lang` in `documents.meta` at parse time,
and on a chunk when it differs from the document's. This is a day's
work, it is the precondition for everything below, and it pays at once:
the UI can say it, a search can filter on it, and the extraction prompt
can stop guessing. `lingua-py` for the short texts, `fastText` for the
artifacts. No migration: `meta` is where a new key goes.

**2. Change the embedding model.** This is the big one and it is nearly
free of code. `multilingual-e5-small` is in the registry today, so it is
`embeddings.model` in `prax.yaml` plus a re-embed; the invariant already
says a new model means a new vector file, and the store already names
them `vectors-<model>.usearch`. The two files can sit side by side while
the old one still serves, which makes this reversible. `granite-
embedding-97m-multilingual-r2` is the better model by nine points and
wants a `ModelSpec` of its own — verify the ONNX export in the repo
first, and that it wants CLS pooling and no query prefix. Both are 384
dimensions, so `VEC_DIM`, the index shape and the Pi-class budget are
untouched.

The cost is the re-embed: 1,115,976 chunk vectors and 9,934 document
vectors, at three to four times bge-small's compute. Measure it on the
desktop before starting; it is a batch job, and the door keeps serving
the old file until the new one is complete.

**3. Send the query in both languages.** Detect the query's language and
have the local model translate it once, which is a dozen tokens and free
on this host. Add the translation as a second keyword list in the
fusion. It is the same shape as the acronym expansion, which already
adds a rank list of its own. This is what fixes the BM25 half, and it
needs no model change and no re-embed. Do it after step 1, measure
against step 2 rather than instead of it.

**4. Make the resolution pass cross-lingual.** Once the entity names are
embedded by a multilingual model, `Olivenöl` and `olive oil` sit next to
each other and the likely tier proposes the pair; the adjudicating tier
decides as it does now. This is the literature's own answer to this
exact task, and in prax it is a consequence of step 2 rather than new
machinery. Give the alias a language while you are there, so the
canonical name can be chosen per language rather than by whichever
spelling arrived first.

**5. A dictionary where it pays.** The kitchen domain is small, closed
and entirely bilingual here: a few hundred German-English ingredient
pairs in a file would clean the recipe graph in one pass and never need
a model. Wikidata's labels are the general fallback and should be a
`prax import` of its own, not a runtime dependency.

### What not to do

Do not translate the documents: the artifact is the parser's output and
the hash is the original's, and a translation is a new document with
none of the evidence. Do not keep a second vector file per language; one
multilingual space is the point. Do not reach for `bge-m3` or `jina-
embeddings-v3`. Four times the parameters, 1024 dimensions, a new
`VEC_DIM`, and in Jina's case a non- commercial licence, when a 97 M
model at the same dimension is within nine points of them. And do not
let the extraction prompt decide the language of an entity name by
itself, which is what produced `celeriac` out of *Sellerieknollen*. Say
in the prompt what the canonical language is, and keep the document's
own words as the alias.

### What to measure

The eval set exists (`scripts/eval_retrieval.py`, 62 library queries,
MRR 0.89 hybrid). The multilingual version of it is the same queries
translated into German, scored against the same answers: a cross-
language MRR that is near zero today and is the number every step above
should move. Run it before step 2 and after, and again with step 3 on
top.

## Sources

- [Multilingual E5 Text Embeddings: A Technical Report](https://arxiv.org/pdf/2402.05672)
- [M3-Embedding (BGE-M3)](https://arxiv.org/pdf/2402.03216)
- [Granite Embedding Multilingual R2](https://huggingface.co/blog/ibm-granite/granite-embedding-multilingual-r2)
- [EmbeddingGemma: Powerful and Lightweight Text Representations](https://arxiv.org/pdf/2509.20354)
- [jina-embeddings-v3](https://jina.ai/models/jina-embeddings-v3/)
- [Statistical and Neural Methods for Cross-lingual Entity Label Mapping in Knowledge Graphs](https://arxiv.org/abs/2206.08709)
- [xMEN: A Modular Toolkit for Cross-Lingual Medical Entity Normalization](https://arxiv.org/pdf/2310.11275)
- [Entity Linking with Wikidata: A Systematic Literature Review](https://dl.acm.org/doi/10.1145/3795134)
- [FactNet: A Billion-Scale Knowledge Graph for Multilingual Factual Grounding](https://arxiv.org/html/2602.03417v1)
- [How to Deal with Different Language Questions in Your RAG Application](https://medium.com/contact-research/how-to-deal-with-different-language-questions-in-your-rag-application-714eb3ccb772)
- [The Cross-Lingual Cost: Retrieval Biases in RAG over Arabic-English Corpora](https://arxiv.org/pdf/2507.07543)
- [Language identification with fastText](https://fasttext.cc/docs/en/language-identification.html)
- [Best Free Language Detection Tools, APIs & Open-Source Models (2026)](https://www.edenai.co/post/top-free-language-detection-tools-apis-and-open-source-models)
