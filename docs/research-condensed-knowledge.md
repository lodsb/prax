# Condensed knowledge: what else a model could combine (research, AF)

The first research question of stage AF in `docs/PLAN.md`. The graph's
facts are knowledge a model can find. The question is which other forms
hold knowledge a model can *combine*: compute with, compare across
documents, scale, check. This page says what the library holds of each
form, what exists to extract it, how it would fit prax, and what is
worth doing first. Nothing here is built. Measured read-only on a copy
of the store on 2026-09-30.

## What the library holds

The store holds 13,361 documents, 13,098 of them indexed. The chunks
that already carry structure:

| kind | chunks | with `data` | documents |
|---|---|---|---|
| `table` | 52,220 | 52,220 | 4,724 |
| `formula` | 17,417 | 17,417 | 243 |
| `code` | 13,869 | 0 | 639 |
| `ingredients` | 90 | 90 | 63 |

A table's `data` is `{header, rows}` of strings. Nothing in it is typed.
A regex over the cells sorts the tables:

- 8,677 tables (17%) are mostly numbers; 7,644 are partly numbers.
- 3,264 have a number with a unit.
- 35,899 are mostly text. Many are layout: an author block, a register
  map, a form.
- 8,459 have fewer than two rows or columns, and 2,664 are empty.

Two shapes are ready to type as they stand:

- **Spec tables**: a header with min, typ or max beside a parameter,
  symbol, characteristic or condition. About 170 to 190 tables in 45 to
  50 documents, depending on the pattern; 108 have a column of
  conditions. `["Parameter","Min","Max","Unit"] / ["Supply Voltage
  VCC","–0.5","1.42","V"]` is one row.
- **Result tables**: a header with a metric (accuracy, F1, SDR, WER),
  and at least 60% of the value cells numbers. At least 619 tables with
  5,612 rows in 312 documents.

Beyond tables:

- **Definitions.** 36,885 sentences with a definition cue ("is defined
  as", "we define", "denotes", "is called", and German ones) in 3,733
  documents. About 65% of them are real definitions or symbol glosses,
  by a hand check of 23. There are 2,360 formal "Definition n" blocks in
  456 documents, and 2,657 theorem, lemma or proposition blocks in 242.
- **Procedures.** 4,376 documents are labelled instructional (manual
  2,484, tutorial 1,177, recipe 73). 3,857 chunks in 645 of them hold a
  numbered list, and 862 have a heading such as "step", "procedure",
  "installation" or "Zubereitung". Only the 63 recipes with an
  `ingredients` chunk are parsed.
- **Quantities in the graph.** About 5,000 `claim` entities, 506 of them
  with a number in the name ("K-NN … yields 56.5% accuracy with only four
  features"). About 380 `spec` entities, strings as well. Papers mention
  a number with %, dB, Hz, ms or × 17,440 times in 1,563 papers.
- **Notebooks.** None. The `code` chunks are listings.

The genres are the small labeller's; the definition and procedure
counts are regex estimates.

## The forms

### Formulas as expressions

Planned in `docs/symbolic-maths.md` (stage AD). One addition: the
"where X denotes …" glosses are the formulas' symbol definitions. A
reading that binds each symbol to its gloss (`data.symbols`) makes an
expression from one paper comparable with one from another. SemEval-2022
Task 12, *Symlink* (arXiv 2202.09695), is the benchmark for linking a
symbol to its description.

### Values with units

A value becomes something to compare and compute with once its
parameter, conditions and unit are explicit.

- **Tools.** Pint (units, mature). CQE (Almasian et al., EMNLP 2023,
  arXiv 2305.08853) reads value, unit, change and the concept a quantity
  is about. D2S-FLOW (arXiv 2502.16540) extracts datasheet parameters
  with a language model, F1 0.92. TableRAG (arXiv 2410.04739) retrieves
  a table's schema and cells for a question instead of the whole table.
- **In prax.** No new table. A `values` reading fills `data.values` of
  a `table` chunk. A spec table's row becomes `{param, symbol, min, typ,
  max, unit, conditions}` with the unit normalized by Pint. A result
  table's row becomes `{method, dataset, metric, value}`. Rules come
  first (the header patterns above, Pint), and the local model reads the
  headers the rules miss. A door read and a surfer action,
  `values(param, unit, range)`, let `ask` compare numbers across
  datasheets or papers. studio's `has_spec` edge could take the chunk
  as its evidence and the value from it, where it holds a string today.
- **Risk.** A misread "–0.5" is worse than no value. The reading needs a
  hand-checked sample before a search or an answer uses it.

### Definitions

- **Tools.** DEFT (SemEval-2020 Task 6) and document-level definition
  detection (Kang et al., arXiv 2010.05129). A local model reads a
  definition zero-shot about as well today; no one tool leads.
- **In prax.** A definition is a region of text. A formal "Definition n"
  block becomes a chunk kind `definition` with `{term, definiens,
  conditions}` in `data`. A sentence definition becomes a relation
  `defines` (document to concept) with the sentence as its evidence,
  which hangs definitions on the 34,277 `concept` entities. The
  `acronyms` table is the precedent of a derived, rebuildable index of
  terms.

### Procedures as steps

- **Tools.** The English Recipe Flow Graph corpus (Yamakata et al., LREC
  2020); open-domain flow graphs (arXiv 2305.19497); the Procedural
  Knowledge Ontology (Carriero et al., ESWC 2025, arXiv 2503.20634) on
  P-Plan and PROV-O; schema.org's HowTo and Recipe as a light target.
- **In prax.** A chunk kind `steps`, as `ingredients` is one: the
  numbered list under a step heading, with `data.steps: [{n, action,
  inputs, tools, output, params}]`. A recipe's ingredient lines link as
  the inputs. Steps are the document's structure, not facts about the
  world, so they stay in chunk `data` and add nothing to the ontology.
  It answers "the same for six people" (the ingredients' `data` was
  written for it) and "how do I …" across manuals.

### Claims with quantities and conditions

prax's edges already have the shape of a nanopublication (Groth et al.
2010): an assertion with its evidence, its producer and run, and its
time. What they lack is qualifiers, the value, metric, dataset and
condition that make "56.5% accuracy" comparable. ORKG's research
contributions (ORKG ASK, arXiv 2412.04977) and ArxivDIGESTables (EMNLP
2024, arXiv 2410.22360) build comparison tables of papers by hand or by
model. In prax this would be a column `edges.qualifiers` (JSON), filled
from the 506 numeric claims and the result tables. It is best done after
the values reading has settled the shape of a value.

### Forms left aside

- **Propositions** (Dense X Retrieval, arXiv 2312.06648). They improve
  what a search returns, not what a model can combine. A proposition is
  a generated sentence, which CLAUDE.md does not allow as a chunk (the
  sections convention); `meta.sections` does the summarizing.
- **Notebooks.** The library has none, and Program-of-Thoughts (arXiv
  2211.12588) argues for computing in a sandboxed tool, which AD plans.
- **Knowledge compilation** (Darwiche and Marquis, JAIR 2002) compiles
  propositional logic. It does not fit a library of prose.

StructRAG (arXiv 2410.08815) chooses a structure per question: a table,
a graph, an algorithm, a catalogue. In prax the surfer is that router,
and the forms above are what it could choose between.

## What is worth doing first

1. **Values in table `data`, and a units-aware compare tool.** The base
   is the largest and already parsed: 52,220 tables, among them the spec
   tables and at least 619 result tables. About two sessions: the rules
   and an evaluation on 100 hand-checked rows, then the local model's
   fallback and the door read. The user decides whether only spec and
   result tables are read or every numeric table, whether `has_spec`
   moves from strings to values, and the faithfulness bar before `ask`
   uses them.
2. **Definitions and symbol glosses.** About 24,000 real definition
   sentences at the measured precision, 456 documents with formal ones,
   and 34,277 concepts to hang them on. It gives stage AD's symbols
   their meanings. About two sessions, with a `defines` relation in
   research's next version. The user decides whether a definition is an
   edge or chunk `data`, and whether AD waits for it.
3. **Procedures as steps**, recipes and tutorials first. 645
   instructional documents with numbered steps and 63 recipes with their
   ingredients parsed. One or two sessions and a rechunk. The user
   decides the scope (kitchen and workshop, or every manual) and whether
   a tool that scales a recipe follows.

Qualifiers on claim edges wait for the first; propositions are left
aside.
