# Acronym expansion and rare terms in the ranking, 2026-09-12

Trigger: "adaa iir algorithms" returned IIR filter papers and nothing about
antiderivative antialiasing, while "adaa" alone did. Diagnosis on the
store: the literal token "ADAA" occurs in chunks of five documents; the
papers write "antiderivative antialiasing" (27 documents); no chunk holds
"adaa" and "iir" together; the keyword side joins terms with OR, so "iir"
and "algorithms" outvote the rare term; the embedder has never seen
"ADAA"; and rank fusion lets a document ranked eighth and twelfth on two
lists beat one ranked first on one.

Change: the library's own acronym definitions ("phrase (ACRONYM)") become
the `acronyms` table (5,887 pairings from 8,452 documents, 3,597 acronyms;
`scripts/build_acronyms.py`), and `store.search` expands a query token that
is a known acronym. Four candidate rules were measured on the 62 library
queries (`tests/eval/queries-library.yaml`, hybrid mode, `scratchpad/grid.py`):

| rule | MRR | hit@1 | ADAA documents in the top 5 for "adaa iir algorithms" |
|---|---|---|---|
| before this change | 0.89 | 0.85 | 0 |
| expansion on the keyword side only (token OR phrase) | 0.905 | 0.87 | 0 |
| plus the expansions in the embedder's input | 0.887 | 0.85 | 0 |
| plus a rank list of chunks holding every term (weight 2) | 0.890 | 0.84 | 0 |
| plus a rank list of chunks holding the rare terms (weight 3) | 0.893 | 0.85 | 4 |
| rare-terms list restricted to acronym-shaped tokens (kept) | 0.905 | 0.87 | 4 |

The keyword-side expansion alone is the gain. Feeding the expansions to
the embedder hurt the paraphrase queries (0.79 to 0.69 on that style), and
the all-terms tier cost two first places. The rare-terms list (a term in
fewer than 50 chunks gets a rank list of its own, weight 3) is what makes
the ADAA case work, but as first written it cost two paraphrase queries
where a rare inflection ("reassigning", "upmixing") occurs in the wrong
documents; restricted to acronym-shaped tokens (at most six letters, or
digits, or a known acronym) it keeps the gain at no cost. A query that is
entirely rare tokens ("adaa") gives the plain keyword list the same weight
instead, so vector neighbours of unknown letters do not outvote the one
exact match.

Kept: keyword-side expansion, the rare-terms list for acronym-shaped
tokens, single-document definitions allowed (the phrase only adds an
alternative to its own token). Off but present as constants
(`ALL_TERMS_WEIGHT`, `VEC_EXPAND`): the all-terms tier and embedder
expansion. `fts` mode stays the raw chunk list, expanded but not fused.

After: "adaa" returns the Buchla wavefolder paper, the DAFx 2020 and 2023
volumes, the circuit-to-code overview and Holters' stateful-systems paper;
"adaa iir algorithms" returns four of those plus one IIR filter paper. The
paper that answers the question ("Antiderivative Antialiasing for
Stateful Systems") never writes IIR, it writes "stateful"; that link is
the graph's job, and the promoted pass on that paper is the way to get it.
