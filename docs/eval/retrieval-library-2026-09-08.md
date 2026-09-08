# Retrieval eval

| mode | n | hit@1 | hit@3 | MRR | MRR by style |
|---|---|---|---|---|---|
| fts | 62 | 0.74 | 0.90 | 0.82 | keyword 0.87, paraphrase 0.69, structure 0.88 |
| vec | 62 | 0.76 | 0.82 | 0.80 | keyword 0.87, paraphrase 0.66, structure 0.85 |
| hybrid | 62 | 0.73 | 0.87 | 0.79 | keyword 0.83, paraphrase 0.67, structure 0.90 |

Queries not at rank 1:

| mode | rank | style | query |
|---|---|---|---|
| fts | - | paraphrase | sharpening the time-frequency picture of the S-transform by reassigning energy along frequency |
| fts | 5 | keyword | instantaneous frequency analytic signal Hilbert transform quadrature |
| fts | 2 | paraphrase | why the Hilbert transform gives a misleading instantaneous frequency and how to compute it from quadrature instead |
| fts | 3 | paraphrase | undoing the window smoothing of a spectrogram to get closer to the Wigner distribution without cross terms |
| fts | 3 | keyword | practical empirical mode decomposition for audio synthesis |
| fts | 8 | keyword | nonnegative matrix factor 2-D deconvolution blind single channel source separation |
| fts | 2 | keyword | multi-microphone noise reduction post-filter superdirective beamformer |
| fts | - | paraphrase | comparing artificial reverb designs by looking at their impulse and phase responses in a scripting environment |
| fts | 2 | keyword | real-time acoustics simulation mesh-tracing |
| fts | 2 | keyword | spectral granular synthesis magnitude spectrum phase reconstruction |
| fts | - | paraphrase | learning how a violinist moves the bow so that a physical model can play more naturally |
| fts | 2 | keyword | Satellite CCRMA Beagle Board musical interaction platform |
| fts | - | paraphrase | why perfectly quantized drum patterns sound mechanical and how human timing deviations are structured |
| fts | 2 | paraphrase | wie man Daten linear so transformiert, dass die Komponenten statistisch unabhängig werden |
| fts | 2 | structure | time complexity of autoregressive and non-autoregressive waveform models WaveNet WaveRNN |
| fts | 3 | structure | confusion matrix of emotion recognition neutral sadness boredom anger |
| vec | - | paraphrase | sharpening the time-frequency picture of the S-transform by reassigning energy along frequency |
| vec | 4 | keyword | instantaneous frequency analytic signal Hilbert transform quadrature |
| vec | - | paraphrase | why the Hilbert transform gives a misleading instantaneous frequency and how to compute it from quadrature instead |
| vec | 6 | paraphrase | undoing the window smoothing of a spectrogram to get closer to the Wigner distribution without cross terms |
| vec | 7 | keyword | practical empirical mode decomposition for audio synthesis |
| vec | - | keyword | nonnegative matrix factor 2-D deconvolution blind single channel source separation |
| vec | - | paraphrase | comparing artificial reverb designs by looking at their impulse and phase responses in a scripting environment |
| vec | 2 | paraphrase | granular synthesis where grains are summed in the frequency domain and the phase is recovered by spectrogram inversion |
| vec | 4 | paraphrase | learning how a violinist moves the bow so that a physical model can play more naturally |
| vec | 2 | keyword | Satellite CCRMA Beagle Board musical interaction platform |
| vec | 3 | keyword | timbre affects perception of emotion in music |
| vec | 5 | keyword | Audiopad tag-based interface musical performance pucks |
| vec | - | paraphrase | wie man Daten linear so transformiert, dass die Komponenten statistisch unabhängig werden |
| vec | 2 | structure | time complexity of autoregressive and non-autoregressive waveform models WaveNet WaveRNN |
| vec | - | structure | confusion matrix of emotion recognition neutral sadness boredom anger |
| hybrid | - | paraphrase | sharpening the time-frequency picture of the S-transform by reassigning energy along frequency |
| hybrid | 2 | keyword | instantaneous frequency analytic signal Hilbert transform quadrature |
| hybrid | 5 | paraphrase | why the Hilbert transform gives a misleading instantaneous frequency and how to compute it from quadrature instead |
| hybrid | 3 | paraphrase | undoing the window smoothing of a spectrogram to get closer to the Wigner distribution without cross terms |
| hybrid | 3 | keyword | one or two frequencies empirical mode decomposition two-tone signal |
| hybrid | 3 | keyword | practical empirical mode decomposition for audio synthesis |
| hybrid | - | keyword | nonnegative matrix factor 2-D deconvolution blind single channel source separation |
| hybrid | 4 | keyword | framework for music analysis resynthesis based on matrix factorization |
| hybrid | - | paraphrase | comparing artificial reverb designs by looking at their impulse and phase responses in a scripting environment |
| hybrid | - | paraphrase | learning how a violinist moves the bow so that a physical model can play more naturally |
| hybrid | 2 | keyword | Satellite CCRMA Beagle Board musical interaction platform |
| hybrid | 5 | paraphrase | why perfectly quantized drum patterns sound mechanical and how human timing deviations are structured |
| hybrid | 3 | keyword | timbre affects perception of emotion in music |
| hybrid | 3 | keyword | Audiopad tag-based interface musical performance pucks |
| hybrid | 2 | keyword | Independent Component Analysis Infomax Algorithmus |
| hybrid | 3 | paraphrase | wie man Daten linear so transformiert, dass die Komponenten statistisch unabhängig werden |
| hybrid | - | structure | confusion matrix of emotion recognition neutral sadness boredom anger |

## Fusion experiment (same query set, offline over the two candidate lists)

Chunk-level rank fusion (the first implementation) scored below FTS alone.
Fusing at document level, each side contributing a document's best chunk
rank, is the variant now in `store.search`.

| variant | depth | hit@1 | MRR | keyword | paraphrase | structure |
|---|---|---|---|---|---|---|
| fts only | 100 | 0.74 | 0.82 | 0.87 | 0.69 | 0.88 |
| vec only | 100 | 0.76 | 0.81 | 0.87 | 0.68 | 0.85 |
| chunk RRF (before) | 100 | 0.73 | 0.80 | 0.83 | 0.69 | 0.93 |
| doc RRF, best chunk rank (now) | 100 | 0.77 | 0.84 | 0.91 | 0.69 | 0.88 |
| doc RRF, vec weighted 1.5 | 100 | 0.77 | 0.83 | 0.90 | 0.68 | 0.87 |

Depth 30 and 300 give the same picture; 100 is the store default.
In-store hybrid after the change: hit@1 0.77, hit@3 0.85, MRR 0.83.

## Latency at 855,731 vectors (desktop, other jobs running)

sqlite-vec scans every vector. Measured with KNN k = 100:

| storage | median query | recall vs fp32 |
|---|---|---|
| fp32 (as stored) | 4.2 s (19 s cold) | 1.00 |
| int8, `vec_quantize_int8` | 1.8 s | 0.93 @100 |
| binary, `vec_quantize_binary` | 0.53 s | 0.48 @100 |
| binary k = 1000 + fp32 rescore | 2.7 s | 1.00 @10 |

FTS alone answers in 0.3 s; hybrid end to end took 5.3 s. Nothing inside
sqlite-vec gets under a second at this size; an approximate index is
needed for the vector side (see the usearch line below).

## Approximate index (usearch HNSW, same vectors, memory-mapped file)

| index | build | file | query median | recall@10 vs exact |
|---|---|---|---|---|
| usearch f16, connectivity 16, ef_search 64 | 218 s | 784 MB | 46 ms | 0.98 |
| usearch int8 | 118 s | 456 MB | 18 ms | 0.93 |

Queried through a memory-mapped view, as the serving host would use it; the
process holds only the pages it touches. This is the route that keeps
hybrid search interactive; it replaces the sqlite-vec table as the vector
store (decision recorded in rationale R6 once taken).
