# The readers measured: marker with its OCR server, and three OCRs

2026-10-06, on the desktop's 4090 (the plan's AJ, steps 1 and 3). Two
measurements. The first is marker reading 544 pages with its OCR server
as a role of `prax up`, against the night of 2026-10-02. The second is
the three readers of a page without a text layer on the same 20 scans.

## Marker with its OCR server as a role

The OCR model (surya-ocr-2) was served by the `ocr-server` role, a
`models:` entry with `cache_ram_mb: 0`, 8 slots of 12,288 tokens and an
f16 KV cache, as surya runs it. Marker was told where it is
(`SURYA_INFERENCE_URL`). The sampler read each process of both trees
every 5 s: its resident set, its private bytes (commit) and its share of
the card (the `GPU Process Memory` counter). 15 documents, 544 pages,
`fast` mode:

| | 2026-10-02, surya's own server | 2026-10-06, `ocr-server` |
|---|---|---|
| OCR server, resident | 10.3 GB | 1.4 GB |
| OCR server, commit | 13.3 GB | 5.9 GB, 3.5 GB of it the card |
| OCR server, card | 3.4 GB | 3.5 GB |
| marker and its helpers, resident | 3.4 + 1.8 GB | 4.6 GB |
| marker and its helpers, commit | not measured | 9.1 GB |
| marker, card | not measured | none: its torch is the CPU build |
| time per page | 2 s (2026-09-16) | 1.67 s |
| least free while marker ran alone | 3.5 GB of RAM | 13.0 GB of RAM, 16.7 GB of commit |
| load, start to ready | not measured | OCR server 5–6 s, marker 12–25 s |

The prompt cache was the whole of the old OCR server's RAM. The numbers
are now `prax.host.readers.MARKER`'s.

For two minutes of the run the 27B sat beside marker on the card, and
free commit fell to 628 MB. Two formula readings had asked for it.
The 27B had been idle when marker took the card, so the loan was made
"beside nothing". The rule that makes an idle server wait for a lent
card applied only to a loan that did not fit. It was paused by hand. The
rule now holds for every loan (`Supervisor._lent_away`).

## Three readers of a scanned page

The scans are made from born-digital PDFs, so each page's own text
layer is the reference. Ten documents give two pages each, the second
and one from the middle: four papers, two German texts, two manuals, a
datasheet and a long manual. Each page was rendered at 200 dpi and put
back as a JPEG at quality 85. The readers were called on the
bytes as a worker calls them, and nothing was written to the store.
Scores are on lower-case words of letters, with Markdown, LaTeX
commands, page marks and figure lines taken out first:

- **word error rate**: the edit distance over the word sequence over the
  reference's words, so the reading order counts;
- **word recall**: the share of the reference's words of three letters
  or more that the reader found, with repeats, in any order.

| reader | where | WER mean | WER median | recall | s/page median | memory |
|---|---|---|---|---|---|---|
| RapidOCR (`pymupdf4llm-ocr`) | the worker's CPU | 0.203 | 0.129 | 0.924 | 1.8 | 0.8 GB resident |
| marker, fast | its server and the OCR server | 0.124 | 0.086 | 0.922 | 6.1 | as above, 3.5 GB of the card |
| vision-pages, the 27B | llama-server | 0.145 | 0.079 | 0.943 | 7.8 | the 27B: 17 GB of the card |

Marker's time per page here is a short document's: each request pays a
fixed cost, and the first after a start loaded its models (151 s). Over
the 544 pages of the first measurement it was 1.67 s.

Where they differ:

- **Two columns.** RapidOCR read the first page of a two-column paper
  (doc 1972, page 2) with a WER of 0.87 and a recall of 0.87: the words
  right, the order wrong. Marker and the 27B read it at 0.09 and 0.10.
- **German.** On a German page (doc 537, p. 76) RapidOCR's WER was 0.47
  against marker's 0.00 and the 27B's 0.39.
- **Short pages.** On the second page of a datasheet and of a white
  paper (docs 13245 and 8463) the 27B was best: 0.03 and 0.02, against
  0.12 and 0.12, and 0.38 and 0.28.
- **All three failed** on the same datasheet page (13245, p. 13; recall
  0.17–0.44): its text layer holds the labels of a drawing, which no
  reader puts in its text.

## What it means for routing

No reader is retired: each wins where the others are weak, at a
different cost.

1. **A scan first gets RapidOCR**, as it does now (`pymupdf4llm-ocr`,
   the unreadable and thin documents). It needs no card, takes 1.8 s a
   page and finds 92% of the words.
2. **A two-column page, a German text or anything with mathematics gets
   marker**: the lowest error, with the layout and the reading order
   right, at 3.5 GB of the card.
3. **vision-pages** finds the most words and is best on short and odd
   pages, but it needs the whole 27B. It stays for what the other two
   cannot read at all. Handwriting and photographed notes were not in
   this set, and that is where it was meant to win.

What this set does not hold: real scans (skew, noise, bleed-through)
and handwriting. Twenty pages are enough to see where a reader breaks,
and too few to rank two readers within a few points of each other.

## Found on the way

The marker run dropped the figure readings of every document it read.
The readings are lines under each figure in the text, and a replacing
read writes a text of its own. A check over the library found 35
documents that had lost 710 readings this way since September, 30 of
them on earlier marker evenings. A replacing read now carries the
readings over, from the text it replaces or an earlier one
(`figures.carry_readings`, `store.earlier_texts`). `prax heal --check
lost-figure-readings` brings back what was lost, without asking a
model.

One document should not have gone to marker: the *Voron Cascade
Assembly Manual* (doc 9829) scored 18 on the maths density because its
assembly steps are numbered "(4)". Its `pymupdf4llm` text was read
again.
