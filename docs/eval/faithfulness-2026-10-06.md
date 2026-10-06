# Three faithfulness checkers on prax's own answers

2026-10-06, the desktop, the 27B answering. Measured as
`docs/research-faithfulness.md` proposed, before any gold labels: planted
errors and paraphrases. The verdict waits for the user's labels.

## How

`scripts/eval_faithfulness.py`. `collect` asked the live door the
questions of `questions-sections.yaml`, `questions-equations.yaml` and
`followups-library.yaml` with `steps: 0`: 112 answers to 101 distinct
questions, each kept with its bundle. `pairs` split every answer into
sentences. A sentence citing passages (`[n]`) is a hypothesis, and the
passages it cites are its premise: 475 sentences. Each got variants:

- **planted errors**, by rule and no model: 93 with a number changed,
  214 with a negation added, 204 with a name swapped for another the
  premise names (511 in all);
- **a paraphrase** by the 27B, told to keep every fact (475).

1,461 pairs. Three checkers scored each as a probability that the
premise supports the hypothesis, accepted at 0.5:

- **HHEM-2.1-Open**: its class vendored in the script, weights and
  flan-t5-base pinned by revision, no remote code. 439 MB.
- **LettuceDetect base** (ModernBERT, English): its detector module
  read from the downloaded wheel. The score is one minus the largest
  probability, over the sentence's tokens, of being unsupported.
- **The 27B as a judge**: asked yes or no, the score is P(yes) from the
  logprobs, as the judge of likely pairs is.

The data is in `C:/prax-data/eval/faithfulness-2026-10-06/` on the
desktop, not in the repository: it quotes the library.

## The numbers

| | HHEM | LettuceDetect | 27B judge |
|---|---|---|---|
| originals accepted | 309/475 | 326/475 | 154/475 |
| originals, mean score | 0.64 | 0.67 | 0.32 |
| planted errors caught, all | 323/511 | 248/511 | 507/511 |
| — numbers | 79/93 | 80/93 | 93/93 |
| — negations | 156/214 | 77/214 | 214/214 |
| — names | 88/204 | 91/204 | 200/204 |
| **caught, of errors in an accepted original** | **125/303 (41%)** | **60/315 (19%)** | **137/141 (97%)** |
| paraphrases accepted | 296/475 | 312/475 | 129/475 |
| paraphrase of an accepted original, within 0.2 of it | 268/309 (87%) | 281/326 (86%) | 103/154 (67%) |
| seconds a pair | 0.14 (CPU) | 0.15 (CPU) | 0.26 (llama-server, 4090) |

The bold row is what counts. A planted error is only a catch when the
checker accepted the sentence it was planted in: a checker that rejects
everything "catches" every error. Read that way:

- **HHEM** misses most errors. A negation it often sees. A name swapped
  for another name of the same passage it almost never sees.
- **LettuceDetect base** misses four in five. It finds a changed number
  and little else.
- **The 27B** catches nearly every error in what it accepts. It accepts
  only a third of the originals, and a third of the paraphrases of
  sentences it had accepted fall away.

Only 4 of the 475 sentences cite a German passage, too few to say
anything about the cross-language case.

## What it means, before the labels

The two small checkers are not a mark on an answer: a sentence they
accept is not much more likely true than one they reject. The 27B
discriminates. What is not known is whether the 321 originals it
rejects are unsupported or whether it is too strict. Two thirds of
prax's cited sentences unsupported would be a finding about `ask`, not
about the judge. The gold labels decide it.
`to-label.md` in the data folder holds 150 originals, each with a
`label:` line (yes, partly or no). Once labelled:

    python scripts/eval_faithfulness.py labels C:/prax-data/eval/faithfulness-2026-10-06
    python scripts/eval_faithfulness.py report C:/prax-data/eval/faithfulness-2026-10-06

`report` then adds each checker's balanced accuracy on them.

Not tried: LettuceDetect large (about 400 MB more) and its EuroBERT
models, which need transformers below 5. MiniCheck-7B is out by the
1 GB download limit.
