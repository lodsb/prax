# Local extractors on the 4090, 2026-09-11

Question: which open-weight model should extract the 6,900-document
backlog on the borrowed RTX 4090 (24 GB, power-limited to 320 W), and how
does it compare with the Sonnet 5 pass? Setup: `llama-server` b10900 (CUDA
12.4) started by `scripts/llama_server.ps1` with 8-bit KV cache and flash
attention, thinking disabled for the Qwen3.6 models; prax's
`LocalExtractor` with the tab line format under its GBNF grammar, 20-triple
cap, temperature 0.3; `scripts/bench_extractor.py` over five papers Sonnet 5
had extracted under ontology v3 (docs 7, 18, 35, 39, 50), nothing applied.
"Overlap" counts the model's triples whose relation and target name match
a live Sonnet edge for the same document (case-insensitive), a cheap recall
proxy that undercounts paraphrases.

## Per model, five papers summed

| model | GGUF | VRAM, slots | s/doc single stream | triples | valid | overlap with Sonnet (96 edges) |
|---|---|---|---|---|---|---|
| Qwen2.5-32B-Instruct (Sept 2024) | Q4_K_M, 19.9 GB | 22.2 GB, 2 × 8 K | 27-39 | 57 | 56 | 22 |
| Qwen3.6-35B-A3B (Apr 2026, MoE 3B active) | UD-Q4_K_S, 20.9 GB | 22.2 GB, 3 × 8 K | 9-13 | 82 | 79 | 26 |
| Qwen3.6-27B (Apr 2026, dense) | Q4_K_M, 16.8 GB | 19.4 GB, 4 × 8 K | 24-40 | 79 | 77 | 27 |

Throughput with parallel workers against the server (`extract_graph.py
--workers`), measured on live never-extracted documents:

| model | workers | effective s/doc | the 6,938-document backlog |
|---|---|---|---|
| Qwen2.5-32B | 2 | 15 | 29 h |
| Qwen3.6-27B | 4 | 13 | 25 h |
| Qwen3.6-35B-A3B | 3 | 6.6 | 13 h |

Power draw during extraction: 210-300 W at the 320 W cap, 54-59 °C.

## What the triples look like

All three get authors, venue and the proposed method right on every paper.
Differences:

- Qwen2.5-32B is the most conservative: about 11 triples per paper, no
  method-to-method edges, no claims. On one paper it wrote entity names as
  identifiers (`rwc_pop_dataset`); `lineformat` now turns those back into
  printed names. It also fills the unmapped list with numbered citations and
  guesses ("the document does not mention a dataset"), which
  `extraction.apply` now drops.
- Qwen3.6-27B is the richest: method-to-method `uses` and `implements`
  edges like Sonnet's, cited papers named by their titles (as INFERRED or
  AMBIGUOUS, which is right). Its one habit: on the benchmark paper it
  marked the compared methods as `proposes` where they are `uses`.
- Qwen3.6-35B-A3B sits between the two on richness and got that same paper
  right (`uses` for the benchmarked methods, `proposes` for HRNMF). It also
  names cited papers by title. Three times faster than the other two.

Sonnet 5 still finds more: claims, `extends` chains, `contrasts`, and
about twice the edges per paper. The provenance columns make a second pass
by Sonnet on the papers that matter a plain addition later.

## Verdict

Qwen3.6-35B-A3B extracts the backlog: about the 27B's quality at three
times the speed, 13 hours for the whole backlog for roughly 4 kWh, against
about $122 for a Sonnet batch. The `extract`, `ask` and `titles` steps in
the desktop's `prax.yaml` name it (`server-35b`); the server alias in that
entry is the producer name edges carry. Sonnet stays the extractor for
targeted passes and for the serving host, which runs no model.

Trial runs during this bench wrote edges for 24 documents under the
producer `local-server@127.0.0.1:8080` (runs `sync-20260911T*`): six from
Qwen2.5-32B, eight from the 27B, nine from the 35B-A3B; they stay.
