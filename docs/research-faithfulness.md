# A faithfulness score for `ask`: the analysis before anything runs

2026-10-06, asked for by the user before any model is downloaded or run
(docs/PLAN.md, "A faithfulness score for `ask`"). What the plan named,
Vectara's HHEM-2.1-Open, what it would take, what else there is, and how
prax would decide. Nothing here has been downloaded or executed.

## What prax wants measured

`ask` answers from a bundle of at most eight passages
(`answering.ask.PASSAGES`), one per document and 1,200 characters each
(`PASSAGE_CHARS`), and the answer cites them by number. Today's
evaluation checks that the expected document is among the sources and
that the answer cites it (`scripts/eval_ask.py`). It does not check that
what the answer says is in the passages it cites. A faithfulness score
is that check: for each claim of the answer, is it supported by the
cited passages?

The plan's order: first a measurement on the existing eval sets, then
perhaps a mark on answers. Four facts about prax shape the choice:

- **Languages.** 73% of the library is English, 18% German, 9% without
  a detected language (`meta.lang`). An answer is written in the
  library's language (English), so a German passage can stand under an
  English sentence: the check must be cross-lingual.
- **The serving path.** Invariant 7 allows no dependency over 1 GB of
  resident RAM in the door's path, and models run as batch jobs. A
  checker belongs in the worker or a script, never in a request.
- **Granularity.** An answer is several sentences, each citing some
  passages. The natural unit is one sentence against the passages it
  cites, not the whole answer against the whole bundle.
- **What the eval found today.** Only a third of the graph's quotes
  stand verbatim in their text (`docs/log.md`, 2026-10-06): the models
  prax uses paraphrase heavily. A checker that penalises paraphrase
  would flag correct answers.

## HHEM-2.1-Open, as published

From the model card and repository (huggingface.co/vectara/hallucination_evaluation_model):

| | |
|---|---|
| base | `google/flan-t5-base`, about 0.1B parameters |
| licence | Apache-2.0 |
| weights | `model.safetensors`, 439 MB (a format that runs no code) |
| input | a pair, premise and hypothesis, in a fixed prompt ("Determine if the hypothesis is true given the premise?") |
| output | 0 to 1: how far the premise supports the hypothesis |
| languages | English only; the multilingual HHEM-2.3 is Vectara's commercial model |
| length | "unlimited" by the card; the code truncates nothing |
| cost | under 600 MB of RAM at 32-bit; about 1.5 s for a 2,000-token input on a modern x86 CPU, by the card |

**Its own numbers** (balanced accuracy, the card): AggreFact-SOTA 76.6%,
RAGTruth-Summ 64.4%, RAGTruth-QA 74.3%; F1 66.8%, 44.8% and 60.0%. On the
independent LLM-AggreFact leaderboard it averages 73.2% balanced
accuracy, against 77.4% for Bespoke-MiniCheck-7B, the best of 39. An
open issue on Vectara's own repository (#128) argues that F1 of 45–67%
is too weak to rank models by, and no maintainer has answered it.

**The remote code.** Loading it as documented needs
`trust_remote_code=True`: transformers then downloads and runs two
Python files of the repository, `configuration_hhem_v2.py` (760 bytes)
and `modeling_hhem_v2.py` (2.7 kB). The modelling file, read on 2026-10-06,
builds `T5ForTokenClassification` from flan-t5-base's config. The score
is the softmax of the first token's two logits. It writes no file and
runs no `exec`. Its only network calls are `AutoConfig` and
`AutoTokenizer.from_pretrained("google/flan-t5-base")`, two more
downloads. The trust is not in what it does today. It is that
the repository can change, and an unpinned load runs whatever it holds
next. Two ways around it:

1. **Pin and read.** Load with `revision=<commit>` after reading that
   commit's two files; nothing new is fetched later.
2. **Vendor.** The class is about thirty lines of Apache-2.0 code. Copy
   it into prax, load flan-t5-base's tokenizer and config from a pinned
   revision, and the weights from the pinned safetensors file. No remote
   code runs. This is the better of the two. It also lets the model be
   exported to ONNX, which prax's embedder already runs on, so the
   worker would not need torch.

## The alternatives

| checker | size, licence | languages | granularity | standing |
|---|---|---|---|---|
| HHEM-2.1-Open | 0.1B, Apache-2.0 | English | a pair | LLM-AggreFact 73.2% |
| LettuceDetect (ModernBERT) | 150M and 396M, MIT | English | the answer's unsupported spans | RAGTruth example F1 76.1% and 79.2%, against GPT-4-turbo's 63.4% in its paper |
| LettuceDetect (EuroBERT) | 210M and 610M, MIT | en, de, fr, es, it, pl, zh | spans | needs `transformers` below 5 (prax has 5.17), and EuroBERT itself may need remote code: to verify |
| Bespoke-MiniCheck-7B | 7B, CC BY-NC 4.0 (personal use only) | trained on English | a sentence against a document, 32K tokens | LLM-AggreFact 77.4%, the best |
| the 27B as a judge | nothing new | the library's | any | unmeasured; the model would check its own answers |

A re-evaluation of five such metrics over 11 datasets (arXiv
2501.14883) found what they share. They disagree with each other and
misestimate system-level quality. They are biased against heavily
paraphrased answers, and against answers that draw on distant parts of
a source. The authors'
advice is to validate a metric on one's own domain before trusting it.
For prax, whose models paraphrase, that bias is the first thing to
measure.

## How prax would decide

A checker is adopted only if it is right on prax's own answers. So the
measurement comes before any wiring, on one set and three checkers:

1. **The gold set.** The answers of the existing eval sets
   (`questions-sections.yaml`, `questions-equations.yaml`), split into
   sentences, each with the passages it cites. A person labels each
   sentence supported, partly or unsupported, about 150 sentences.
   Labels a model writes are not gold.
2. **Planted errors.** For a supported sentence, a copy with one number,
   name or negation changed: it must score low. This measures
   sensitivity without a person, and is the half that cannot be fooled
   by paraphrase.
3. **Paraphrase pairs.** A supported sentence rewritten in other words:
   it must still score high. This is the bias the re-evaluation found.
4. **Cross-language.** The German passages under English sentences, kept
   apart in the scores.
5. **The checkers.** HHEM-2.1-Open vendored, LettuceDetect's English
   large model, and the 27B asked the same question; MiniCheck-7B only if
   the first three fail and its licence suits the library.
6. **The decision.** Balanced accuracy on the gold set, recall on the
   planted errors, and the false alarms on the paraphrases, each for
   English and for German passages. A checker earns a place when it
   catches most planted errors without flagging most paraphrases. If
   none does, prax keeps today's citation check and says why.

The cost, estimated from the card: one sentence against two cited
passages is about 600 tokens, well under a second on the CPU for HHEM.
150 gold sentences and their variants take a few minutes. The labelling is
the real cost: about an hour of a person's reading.

## Open for the user

- Whether to label the gold set (about 150 sentences, an hour), without
  which only the planted errors and paraphrases can be measured.
- Whether downloads are acceptable: HHEM's 439 MB weights and
  flan-t5-base's tokenizer from pinned revisions, and LettuceDetect's
  model (about 400 MB for the large one).
- Whether MiniCheck's non-commercial licence matters for a personal
  library: it would only be tried if the others fail.

## Sources

- [HHEM-2.1-Open model card](https://huggingface.co/vectara/hallucination_evaluation_model)
  and its [modeling file](https://huggingface.co/vectara/hallucination_evaluation_model/blob/main/modeling_hhem_v2.py)
- [Issue #128: HHEM2.1-Open is a poor evaluator](https://github.com/vectara/hallucination-leaderboard/issues/128)
- [MiniCheck overview, Bespoke Labs](https://docs.bespokelabs.ai/minicheck/overview)
- [LettuceDetect paper](https://arxiv.org/html/2502.17125) and [repository](https://github.com/krlabsorg/lettucedetect)
- [Verify with Caution: the pitfalls of relying on imperfect factuality metrics](https://arxiv.org/abs/2501.14883)
