# Extractor bench: local-server@127.0.0.1:8080

| doc | seconds | in | out | triples | valid | ref edges | overlap |
|---|---|---|---|---|---|---|---|
| 7 | 40 | 5083 | 1645 | 20 | 20 | 23 | 8 |
| 18 | 24 | 4592 | 952 | 12 | 12 | 22 | 6 |
| 35 | 29 | 4764 | 1193 | 17 | 15 | 11 | 0 |
| 39 | 31 | 5555 | 1252 | 17 | 17 | 20 | 7 |
| 50 | 28 | 5449 | 1121 | 13 | 13 | 20 | 6 |

## 7: End-to-End Probabilistic Inference for Nonstationary Audio Analysis

Summary: This paper proposes an end-to-end probabilistic model for audio analysis that jointly formulates time-frequency analysis and nonnegative matrix factorization as a spectral mixture Gaussian process with nonstationary priors. It introduces a scalable inference algorithm using expectation propagation,

- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --authored_by--> William J. Wilkinson (author) [EXTRACTED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --authored_by--> Michael Riis Andersen (author) [EXTRACTED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --authored_by--> Joshua D. Reiss (author) [EXTRACTED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --authored_by--> Dan Stowell (author) [EXTRACTED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --authored_by--> Arno Solin (author) [EXTRACTED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --about--> gaussian process (concept) [EXTRACTED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --about--> time-frequency analysis (concept) [EXTRACTED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --proposes--> spectral mixture gaussian process (method) [EXTRACTED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --proposes--> expectation propagation (method) [EXTRACTED]
- spectral mixture gaussian process (method) --uses--> nonnegative matrix factorization (method) [EXTRACTED]
- expectation propagation (method) --implements--> spectral mixture gaussian process (method) [INFERRED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --proposes--> end-to-end audio analysis (concept) [EXTRACTED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --cites--> Probabilistic time-frequency analysis via Gaussian processes (paper) [INFERRED]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --cites--> Gaussian Process Latent Variable Model for Nonlinear Dimensionality Reduction (paper) [AMBIGUOUS]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --cites--> A fast algorithm for universal spectral mixture kernels (paper) [AMBIGUOUS]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --cites--> Infinite-horizon Gaussian processes (paper) [AMBIGUOUS]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --cites--> The probabilistic phase vocoder (paper) [AMBIGUOUS]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --cites--> A new algorithm for non-negative matrix factorization and denoising (paper) [AMBIGUOUS]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --cites--> Griffin-Lim algorithm (paper) [AMBIGUOUS]
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis (paper) --cites--> Gaussian Processes for Machine Learning (paper) [AMBIGUOUS]

unmapped:
- End-to-End Probabilistic Inference for Nonstationary Audio Analysis --published_in--> ICML 2019: Venue is not explicitly named in the text provided, only date and authors are listed.
- extended Kalman filter --contrasts--> expectation propagation: Text states EP outperforms EKF, but 'contrasts' relation is defined for claim/method/paper -> claim/method/paper, not method->method directly in this ontology version without a mediating claim or paper entity.
- spectral mixture gaussian process --extends--> gaussian time-frequency nmf model: Text says the proposed state space form is 'equivalent to' Gaussian TF NMF, not strictly an extension, but implies unification/improvement via inference. 'implements' or 'uses' might be better, but equivalence is nuanced.

## 18: Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation

Summary: This paper investigates Nonnegative Tucker Decomposition (NTD) for uncovering musical patterns and structure in pop songs by modeling audio as a Time-Frequency-Bar tensor. It demonstrates that NTD can capture repeated motifs and provides efficient features for structural segmentation, achieving high

- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --authored_by--> Axel Marmoret (author) [EXTRACTED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --authored_by--> Jérémy E Cohen (author) [EXTRACTED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --authored_by--> Nancy Bertin (author) [EXTRACTED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --authored_by--> Frédéric Bimbot (author) [EXTRACTED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --published_in--> ISMIR (venue) [EXTRACTED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --about--> music structural segmentation (concept) [EXTRACTED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --about--> musical pattern recognition (concept) [EXTRACTED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --proposes--> nonnegative tucker decomposition (method) [INFERRED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --uses--> rwc pop dataset (dataset) [EXTRACTED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --about--> chromagram (concept) [EXTRACTED]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --cites--> tensor decomposition for loop-based electronic music (paper) [AMBIGUOUS]
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation (paper) --contrasts--> example-based learning (method) [EXTRACTED]

unmapped:
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation --cites--> RWC Pop Music data set [2]: Citation by number only, no title provided in text.
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation --cites--> [3]: Citation by number only, no title provided in text.
- Uncovering Audio Patterns in Music with Nonnegative Tucker Decomposition for Structural Segmentation --cites--> [4]: Citation by number only, no title provided in text.

## 35: Phase recovery in NMF for audio source separation: an insightful benchmark

Summary: This paper benchmarks various Nonnegative Matrix Factorization (NMF) based audio source separation techniques that incorporate phase recovery, comparing blind and oracle approaches. It finds that High Resolution NMF (HRNMF) is particularly promising due to its ability to model temporal correlations,

- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --authored_by--> Paul Magron (author) [EXTRACTED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --authored_by--> Roland Badeau (author) [EXTRACTED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --authored_by--> Bertrand David (author) [EXTRACTED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --published_in--> 2015 IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP) (venue) [EXTRACTED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --about--> audio source separation (concept) [EXTRACTED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --about--> phase reconstruction (concept) [EXTRACTED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --uses--> nonnegative matrix factorization (method) [EXTRACTED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --uses--> High Resolution NMF (method) [EXTRACTED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --uses--> BSS EVAL (tool) [EXTRACTED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --proposes--> NMF-Wiener (method) [INFERRED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --proposes--> NMF-GL (method) [INFERRED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --proposes--> NMF-LR (method) [INFERRED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --proposes--> CNMF (method) [INFERRED]
- Phase recovery in NMF for audio source separation: an insightful benchmark (paper) --proposes--> CNMF-LR (method) [INFERRED]
- HRNMF is particularly promising for audio source separation because it models phases and temporal correlations (claim) --supports--> Phase recovery in NMF for audio source separation: an insightful benchmark (paper) [EXTRACTED]
- Consistency is not an appropriate criterion for audio quality (claim) --supports--> Phase recovery in NMF for audio source separation: an insightful benchmark (paper) [EXTRACTED]
- NMF-Wiener (method) --uses--> Wieners filtering (concept) [INFERRED]

unmapped:
- Phase recovery in NMF for audio source separation: an insightful benchmark --supported_by--> EDISON 3D project: Funded research programme, not a library entity type
- Paul Magron --affiliated_with--> INRIA Nancy - Grand Est: Affiliation is not a defined relation or entity type in this ontology
- Roland Badeau --affiliated_with--> Télécom ParisTech / CNRS LTCI: Affiliation is not a defined relation or entity type in this ontology

## 39: Generalized Wiener filtering with fractional power spectrograms

Summary: This paper provides a theoretical justification for using fractional power spectrograms (alpha-spectrograms) in single-channel audio source separation. The authors demonstrate that assuming additivity of alpha-spectrograms corresponds to modeling sources as locally stationary alpha-stable harmonizab

- Generalized Wiener filtering with fractional power spectrograms (paper) --authored_by--> Antoine Liutkus (author) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --authored_by--> Roland Badeau (author) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --published_in--> 40th International Conference on Acoustics, Speech and Signal Processing (ICASSP) (venue) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --about--> audio source separation (concept) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --about--> fractional power spectrogram (method) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --proposes--> alpha-harmonizable process (concept) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --about--> soft masking (method) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --extends--> Wieners filter (method) [INFERRED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --uses--> short-term Fourier transform (method) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --proposes--> alpha-Wiener filter (method) [INFERRED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --cites--> DUET (paper) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --cites--> ADRESS (paper) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --cites--> Probabilistic Latent Component Analysis (paper) [INFERRED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --supports--> fractional spectrograms better fit additivity assumption than power spectrograms (claim) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --supports--> alpha-harmonizable processes justify soft-masking for alpha-spectrograms (claim) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --supports--> alpha-Wiener filter outperforms classical Wiener filter for audio separation (claim) [EXTRACTED]
- Generalized Wiener filtering with fractional power spectrograms (paper) --contrasts--> Wieners filter (method) [INFERRED]

unmapped:
- Generalized Wiener filtering with fractional power spectrograms --supported_by--> EDiSon3D: funded research programme, not a project entity per ontology rules
- alpha-harmonizable process --extends--> wide-sense stationary process: document states alpha-harmonizable processes generalize WSS/Gaussian processes for separation context
- Generalized Wiener filtering with fractional power spectrograms --uses--> MUSDB18: document mentions "music separation performance" but does not explicitly name the dataset used in text provided; inferred from context of music source separation papers but not explicit in text snippet. Actually, looking closely at text: "In section IV, we compare the music separation performance...

## 50: Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease

Summary: This paper evaluates the robustness of ten freely-available pitch detection algorithms for assessing Parkinson's disease via smartphone recordings under various noise levels. It identifies SWIPE as the most accurate method and proposes monopitch (F0 standard deviation) as a robust biomarker for PD.

- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --authored_by--> Vojtech Illner (author) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --authored_by--> Pavel Sovka (author) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --authored_by--> Jan Rusz (author) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --published_in--> Biomedical Signal Processing and Control (venue) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --about--> fundamental frequency estimation (concept) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --about--> speech biomarkers (concept) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --about--> noise robustness (concept) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --uses--> sawtooth inspired pitch estimator (method) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --uses--> smartphone (tool) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --proposes--> monopitch (concept) [INFERRED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --proposes--> sawtooth inspired pitch estimator (method) [INFERRED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --proposes--> SWIPE is the most robust pitch detector for smartphone PD assessment (claim) [EXTRACTED]
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease (paper) --proposes--> monopitch distinguishes PD from healthy controls (claim) [EXTRACTED]

unmapped:
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease --dataset--> speech recordings from 30 PD patients and 30 controls: The dataset is described but not given a specific name like MUSDB18.
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease --cites--> [1]: Citations are referenced by number only, not by title.
- Validation of freely-available pitch detection algorithms across various noise levels in assessing speech captured by smartphone in Parkinson’s disease --cites--> [9]: Citations are referenced by number only, not by title.
