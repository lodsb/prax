# Extractor comparison, 2026-09-07

Twenty PDFs of at most 30 pages whose text mentions tables most often per
chunk (the proxy also caught several "tabletop" papers, which serve as plain
two-column controls). Produced by `scripts/compare_extractors.py`; pymupdf4llm
ran with OCR enabled here (the default was switched off afterwards, which
does not affect documents that have a text layer). Verdict: rationale R8.

| doc | title | extractor | seconds | chars | table rows | headings | formula | bad |
|---|---|---|---|---|---|---|---|---|
| 1929 | The ltcaption package | pymupdf | 0.0 | 5411 | 0 | 0 | 6 | 0 |
| 1929 | The ltcaption package | pymupdf4llm | 1.5 | 5768 | 5 | 8 | 6 | 0 |
| 1929 | The ltcaption package | docling | 31.6 | 5857 | 5 | 10 | 5 | 0 |
| 7034 | 1408fe87471fd592b8f33f68ed1eab72-Fb et al. - 2009  | pymupdf | 0.0 | 9430 | 0 | 0 | 0 | 0 |
| 7034 | 1408fe87471fd592b8f33f68ed1eab72-Fb et al. - 2009  | pymupdf4llm | 4.8 | 10384 | 26 | 5 | 0 | 0 |
| 7034 | 1408fe87471fd592b8f33f68ed1eab72-Fb et al. - 2009  | docling | 16.2 | 10559 | 22 | 4 | 0 | 0 |
| 7762 | a299dd0f2e1ca057fc0cc7eb9f84db77-TableHelp1.pdf | pymupdf | 0.0 | 8284 | 0 | 0 | 0 | 0 |
| 7762 | a299dd0f2e1ca057fc0cc7eb9f84db77-TableHelp1.pdf | pymupdf4llm | 4.9 | 9283 | 11 | 4 | 0 | 0 |
| 7762 | a299dd0f2e1ca057fc0cc7eb9f84db77-TableHelp1.pdf | docling | 16.7 | 9621 | 17 | 2 | 0 | 0 |
| 7079 | 03048d83bd2ce435011e3ccadbe4eaf9-PTEUserGuide.pdf | pymupdf | 0.0 | 5036 | 0 | 0 | 46 | 0 |
| 7079 | 03048d83bd2ce435011e3ccadbe4eaf9-PTEUserGuide.pdf | pymupdf4llm | 26.3 | 11100 | 0 | 12 | 64 | 0 |
| 7079 | 03048d83bd2ce435011e3ccadbe4eaf9-PTEUserGuide.pdf | docling | 33.9 | 4936 | 0 | 9 | 40 | 0 |
| 1573 | Collaborative problem solving on mobile hand-held  | pymupdf | 0.0 | 19677 | 0 | 0 | 0 | 0 |
| 1573 | Collaborative problem solving on mobile hand-held  | pymupdf4llm | 2.7 | 20809 | 10 | 16 | 0 | 0 |
| 1573 | Collaborative problem solving on mobile hand-held  | docling | 11.2 | 19777 | 0 | 17 | 0 | 0 |
| 93 | Differentiable Wavetable Synthesis | pymupdf | 0.0 | 21511 | 0 | 0 | 30 | 0 |
| 93 | Differentiable Wavetable Synthesis | pymupdf4llm | 4.6 | 22920 | 3 | 15 | 22 | 0 |
| 93 | Differentiable Wavetable Synthesis | docling | 14.4 | 21892 | 4 | 14 | 24 | 0 |
| 756 | Finger talk: collaborative decision-making using t | pymupdf | 0.0 | 20326 | 0 | 0 | 0 | 0 |
| 756 | Finger talk: collaborative decision-making using t | pymupdf4llm | 1.7 | 20196 | 0 | 13 | 0 | 0 |
| 756 | Finger talk: collaborative decision-making using t | docling | 8.8 | 21110 | 0 | 14 | 0 | 0 |
| 6942 | 0737bd37fe3c5eea62111dfde089adf1-TE0140-01.pdf | pymupdf | 0.0 | 13817 | 0 | 0 | 0 | 0 |
| 6942 | 0737bd37fe3c5eea62111dfde089adf1-TE0140-01.pdf | pymupdf4llm | 5.5 | 16663 | 201 | 30 | 1 | 0 |
| 6942 | 0737bd37fe3c5eea62111dfde089adf1-TE0140-01.pdf | docling | 55.8 | 20697 | 195 | 28 | 0 | 0 |
| 800 | Territoriality in collaborative tabletop workspace | pymupdf | 0.0 | 63295 | 0 | 1 | 0 | 0 |
| 800 | Territoriality in collaborative tabletop workspace | pymupdf4llm | 5.0 | 63442 | 7 | 29 | 0 | 0 |
| 800 | Territoriality in collaborative tabletop workspace | docling | 17.6 | 64481 | 7 | 29 | 0 | 0 |
| 2333 | Proceedings of the International Conference on New | pymupdf | 0.0 | 26387 | 0 | 0 | 1 | 0 |
| 2333 | Proceedings of the International Conference on New | pymupdf4llm | 3.3 | 27489 | 12 | 23 | 1 | 0 |
| 2333 | Proceedings of the International Conference on New | docling | 12.9 | 25694 | 12 | 25 | 0 | 0 |
| 5280 | 4d6ef4049862cf453217359f9f96b064-colortbl.pdf | pymupdf | 0.0 | 11519 | 3 | 0 | 8 | 0 |
| 5280 | 4d6ef4049862cf453217359f9f96b064-colortbl.pdf | pymupdf4llm | 1.2 | 13103 | 3 | 13 | 8 | 0 |
| 5280 | 4d6ef4049862cf453217359f9f96b064-colortbl.pdf | docling | 15.0 | 11225 | 9 | 13 | 8 | 0 |
| 2186 | The <i>ColorTable</i>: a design story | pymupdf | 0.0 | 40180 | 0 | 0 | 0 | 0 |
| 2186 | The <i>ColorTable</i>: a design story | pymupdf4llm | 4.0 | 40661 | 0 | 24 | 0 | 1 |
| 2186 | The <i>ColorTable</i>: a design story | docling | 12.6 | 40813 | 0 | 25 | 0 | 0 |
| 6705 | 307c648ea8fc11759296e958bf8cd008-Kaltenbrunner et  | pymupdf | 0.0 | 25218 | 0 | 0 | 0 | 0 |
| 6705 | 307c648ea8fc11759296e958bf8cd008-Kaltenbrunner et  | pymupdf4llm | 2.1 | 25803 | 0 | 15 | 0 | 0 |
| 6705 | 307c648ea8fc11759296e958bf8cd008-Kaltenbrunner et  | docling | 9.0 | 25881 | 3 | 17 | 0 | 0 |
| 1806 | The cuetable: cooperative and competitive multi-to | pymupdf | 0.0 | 19856 | 0 | 0 | 0 | 0 |
| 1806 | The cuetable: cooperative and competitive multi-to | pymupdf4llm | 2.1 | 20609 | 0 | 14 | 0 | 0 |
| 1806 | The cuetable: cooperative and competitive multi-to | docling | 9.5 | 19378 | 0 | 14 | 0 | 0 |
| 2924 | The subcaption package | pymupdf | 0.0 | 17463 | 0 | 0 | 47 | 0 |
| 2924 | The subcaption package | pymupdf4llm | 1.8 | 18669 | 18 | 17 | 47 | 0 |
| 2924 | The subcaption package | docling | 15.0 | 19237 | 18 | 21 | 47 | 0 |
| 1633 | CollaborativeMusic MakingwithInteractiveTabletops | pymupdf | 0.0 | 10940 | 0 | 0 | 0 | 0 |
| 1633 | CollaborativeMusic MakingwithInteractiveTabletops | pymupdf4llm | 1.7 | 11057 | 0 | 7 | 0 | 24 |
| 1633 | CollaborativeMusic MakingwithInteractiveTabletops | docling | 13.2 | 10909 | 0 | 2 | 0 | 0 |
| 2116 | Contextual design considerations for co-located, c | pymupdf | 0.0 | 44702 | 0 | 0 | 0 | 0 |
| 2116 | Contextual design considerations for co-located, c | pymupdf4llm | 2.3 | 44349 | 0 | 25 | 0 | 0 |
| 2116 | Contextual design considerations for co-located, c | docling | 11.1 | 44569 | 0 | 25 | 0 | 0 |
| 8955 | f1ce8e09a1b3e80586c5b946c022bbba-avr_div_mul.pdf | pymupdf | 0.0 | 21405 | 0 | 0 | 32 | 0 |
| 8955 | f1ce8e09a1b3e80586c5b946c022bbba-avr_div_mul.pdf | pymupdf4llm | 3.9 | 25034 | 184 | 42 | 32 | 0 |
| 8955 | f1ce8e09a1b3e80586c5b946c022bbba-avr_div_mul.pdf | docling | 57.3 | 30903 | 185 | 42 | 22 | 0 |
| 1509 | EXPLORING MOOD METADATA: RELATIONSHIPS WITH GENRE, | pymupdf | 0.0 | 31191 | 0 | 0 | 1 | 0 |
| 1509 | EXPLORING MOOD METADATA: RELATIONSHIPS WITH GENRE, | pymupdf4llm | 3.4 | 31828 | 105 | 28 | 1 | 0 |
| 1509 | EXPLORING MOOD METADATA: RELATIONSHIPS WITH GENRE, | docling | 45.3 | 35719 | 105 | 29 | 1 | 0 |
| 475 | The Table is The Score: An Augmented-Reality Inter | pymupdf | 0.0 | 17393 | 0 | 0 | 0 | 0 |
| 475 | The Table is The Score: An Augmented-Reality Inter | pymupdf4llm | 2.9 | 18335 | 0 | 13 | 0 | 0 |
| 475 | The Table is The Score: An Augmented-Reality Inter | docling | 13.1 | 18063 | 0 | 13 | 0 | 0 |

| extractor | total s | total chars |
|---|---|---|
| pymupdf | 0 | 433041 |
| pymupdf4llm | 86 | 457502 |
| docling | 420 | 461321 |
