# Retrieval eval

Fixture store: {'created': 12, 'merged': 3} documents, parsed {'upgraded': 7, 'kept': 2}, 358 vectors (bge-small-en-v1.5).

| mode | n | hit@1 | hit@3 | MRR | MRR by style |
|---|---|---|---|---|---|
| fts | 20 | 0.90 | 0.95 | 0.94 | keyword 1.00, paraphrase 0.93, structure 0.75 |
| vec | 20 | 0.95 | 0.95 | 0.95 | keyword 0.90, paraphrase 1.00, structure 1.00 |
| hybrid | 20 | 0.85 | 0.95 | 0.91 | keyword 1.00, paraphrase 0.81, structure 0.83 |

Queries not at rank 1:

| mode | rank | style | query |
|---|---|---|---|
| fts | 2 | paraphrase | allpass filter pair that produces a ninety degree phase shift for an analytic signal |
| fts | 4 | structure | table of results comparing polynomial transition region variants |
| vec | - | keyword | SID GUTS controls schematic |
| hybrid | 6 | paraphrase | neural network trained on a band's records producing raw audio in its style |
| hybrid | 2 | paraphrase | allpass filter pair that produces a ninety degree phase shift for an analytic signal |
| hybrid | 2 | structure | table of results comparing polynomial transition region variants |
