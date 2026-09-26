# Repair or prevention: what a new library gets, and in which languages

2026-09-24. A week of passes fixed this library's accumulated damage —
summaries in the wrong language, one thing under two names, a graph that
could not be asked in German. The question worth asking afterwards is
whether any of that is a *fix* or whether every new library would need
the same week.

This is the audit, re-run after the two gaps it found were closed.

## What a new library gets

The repair passes exist because this library grew to 10,000 documents
before the prevention did. On a library starting today they find nothing,
because the prevention is in the paths that create things rather than in
a pass that follows behind:

- **A name is translated once, in one place, and the printed word is
  kept.** The extraction prompt writes names as the document prints
  them; the watched `vocabulary` step translates the ones the ontology's
  `naming:` key calls common and keeps the printed word as a label. For
  two days (2026-09-24 to -26) the prompt did the translating instead,
  which looked like prevention and quietly dropped the label a German
  query crosses on — the repair had a side effect the prevention did not
  reproduce (`docs/eval/apfelkuchen-2026-09-26.md`). Because the step is
  watched, it runs within a minute of the extraction, so a new library
  still never accumulates the split.
- **Every entity carries a label of its own name from birth**
  (`_entity_id`), so the identity design holds from document one rather
  than from migration 23. The labels are the truth about what a thing is
  called; `entities.name` is a cache of the preferred one.
- **A name that is replaced is recorded before it goes**
  (`_refresh_name`), so no path can drop one.
- **All 24 migrations apply to an empty store**, checked by building one.

## What corrects itself

A model that drifts is not a bug to be fixed by hand. Two of the three
language passes are **watched** — a worker asks for them without being
told, so drift is corrected within the minute:

| step | watched | why |
|---|---|---|
| `summaries` | yes | a summary in the wrong language, translated locally, seconds a document |
| `vocabulary` | yes | *since the candidate net learned to converge, below* |
| `sections` | no | a book is up to forty model calls; asked for deliberately |

`vocabulary` could not be watched at first. Its third condition — does
this name occur in an English document — costs an FTS lookup a name, and
on an exhausted queue it was paid for every candidate on every ask: **97
seconds to answer "nothing"**. A worker asking every twenty seconds would
have pinned the door.

The ruling is monotone, though: a name that occurs in an English document
will always occur in one, because documents are retired and never
deleted. So it is recorded — the entity is marked `vocabulary:corpus`,
which is a truthful thing to say about it — and the pass converges. The
second ask took **0.7 seconds**. That is what made it watchable.

## Which languages

The mechanisms turned out less English-shaped than expected, because the
ones that could have been rules asked the data instead:

- **The candidate net asks the corpus**, not a list of German endings. It
  caught `psycho-acoustique` with no French anywhere in it, and needs no
  rule per language.
- **`naming:` is about types**, not languages: a `dish` translates and a
  `person` does not, in any language.
- **The detector** is `py3langid` narrowed to the six languages a host
  says it expects (`parse.languages`).
- **`lower()` is full Unicode** where SQLite's own stops at Z, which had
  been quietly breaking entity search for every name with an umlaut.

What *was* English-shaped was the target. `summaries.CANONICAL` and
`vocabulary.CANONICAL` were constants and four prompts said "English" in
so many words. They now read **`graph.language`**, one setting meaning
*the language this library is written in* — its summaries, the common
names in its graph, and what the graph shows. Set it to `fr` and the
prompts ask for French.

It is deliberately one setting rather than two. A library has one
language the way it has one ontology, and a display preference that
silently disagreed with what the passes normalize to would be worse than
either. Changing it is not free — every summary in the old language
becomes work for the `summaries` step — but that work is a local model
and costs time rather than money.

## What is still not general

**The lexicon speaks English and German.** 40 organization cues
including `gmbh`, `hochschule`, `fraunhofer`; nothing French, Spanish,
Italian or Dutch, though the detector expects all six.

This one degrades rather than breaks. A cue that does not fire leaves the
typing rules silent, and the typing model does the work — which
`docs/eval/typing-rules-2026-09-24.md` shows it does better anyway on the
93% the rules have no opinion about.

And it is deliberately not fixed here. This library holds 41 French
documents, 12 Spanish, 10 Italian and 4 Dutch: too few to measure a cue
against, so adding them would be guessing dressed as engineering. The
lexicon is *data* now (`ontology/lexicon.yaml`) rather than regular
expressions inside the module that reads them, which is most of what
moving it bought: a French library's owner adds cues in a pull request
and can measure them against evidence this library does not have.

## The honest summary

Prevention, for a new library: **yes**, and in the create paths rather
than in a pass.

Self-healing, when a model drifts: **yes** for the document field and the
graph's names, **no** for section summaries, which are asked for.

Other languages: **the mechanisms, yes; the target language, now a
setting; the lexicon, only as far as someone measures it.**
