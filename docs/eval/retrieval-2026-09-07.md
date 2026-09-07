# Retrieval eval

Fixture store: {'created': 10, 'merged': 3} documents, parsed {'upgraded': 8}, 351 vectors (bge-small-en-v1.5).

| mode | n | hit@1 | hit@3 | MRR | MRR by style |
|---|---|---|---|---|---|
| fts | 20 | 0.95 | 0.95 | 0.95 | keyword 1.00, paraphrase 0.88, structure 1.00 |
| vec | 20 | 0.90 | 0.95 | 0.93 | keyword 1.00, paraphrase 0.81, structure 1.00 |
| hybrid | 20 | 0.90 | 0.95 | 0.93 | keyword 0.93, paraphrase 0.92, structure 1.00 |

Queries not at rank 1:

| mode | rank | style | query |
|---|---|---|---|
| fts | - | paraphrase | reference for the C interface of basic linear algebra subprograms |
| vec | 2 | paraphrase | an extension of NMF that models the full covariance across time-frequency bins |
| vec | - | paraphrase | reference for the C interface of basic linear algebra subprograms |
| hybrid | 4 | keyword | BLAS dgemm matrix multiply routine signature |
| hybrid | 3 | paraphrase | reference for the C interface of basic linear algebra subprograms |
