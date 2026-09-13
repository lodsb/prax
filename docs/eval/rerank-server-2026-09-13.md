# Reranking through llama-server (2026-09-13)

bge-reranker-v2-m3 (568 M, Q8_0 GGUF) behind `llama-server --reranking`
on the desktop's 24 GB card, beside the 35B extraction model (23.9 of
24.5 GB used together). prax's `server` reranker posts the query and the
top hits to `/rerank` (`PRAX_RERANK=server`), candidates cut to 3,000
characters; the server reads a pair in one batch (`-b 4096`). 92 ms for
ten candidates of ~400 tokens.

The library's 62 queries (`tests/eval/queries-library.yaml`), each mode
in its own process; hybrid without `PRAX_RERANK` set, since an unset
`rerank` argument follows it.

| mode | depth | hit@1 | hit@3 | MRR | keyword | paraphrase | structure |
|---|---|---|---|---|---|---|---|
| hybrid (fused list) | | 0.85 | 0.94 | 0.90 | 1.00 | 0.73 | 0.86 |
| server rerank | 10 | 0.74 | 0.90 | 0.83 | 0.83 | 0.77 | 0.91 |
| server rerank | 30 | 0.73 | 0.85 | 0.80 | 0.81 | 0.77 | 0.85 |

The same shape as the ONNX rerankers on 2026-09-08 (`retrieval-library-2026-09-08.md`):
paraphrase and structure queries gain a little, keyword queries lose a
lot — the cross-encoder scores a bare chunk, and a reference list or a
table that carries the query's exact terms is not the passage a reader
wants first; the fused list, which ranks documents, gets that right. The
baseline itself has moved since September 8 (0.77 → 0.85 hit@1) with the
acronym and document-field work.

Verdict: reranking stays off. The server route costs nothing to keep
(one flag on the launch script, one setting) and is the place to try a
document-aware candidate — title, heading path, then the chunk — which
is what these models were trained to judge.
