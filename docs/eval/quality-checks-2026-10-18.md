# The checks of a text over the library (stage AO, 2026-10-18)

`prax.parsers.quality` run over every document with a text, read-only
against a live store of 17,499 texts: 11,493 PDFs, 3,444 HTML pages,
1,430 RTF, 562 Markdown, the rest office files, plain text, DjVu and
images. Each PDF was profiled (`parsers.pdf.profile`, its first 12
pages) and its arXiv id read from meta or the first page. 38 minutes on
the desktop, one process. Ids only: the library holds personal
documents.

## Two runs

| check | run 1 | run 2 | sample of 12 read by hand (run 2) |
|---|---|---|---|
| `maths-as-text` | 622 | 586 | 12 real: maths scattered in emphasis and `<sup>` |
| `ocr-layer` | 353 | 353 | profiles of scans with an OCR layer, all read by pymupdf4llm |
| `bare-figures` | 1,320 | 1,320 | captions with no picture: 1,022 pymupdf4llm, 180 marker, 71 LaTeX source |
| `short-text` | 38 | 5 | |
| `leftovers` | 8,425 | 321 by kind, without HTML | see below |

Run 1's `maths-as-text` sample was 4 of 12 real (9007, 4720, 8371,
2368). The false alarms were a LaTeX manual's code in backticks (3221,
5163, 1587, 1676), footnote URLs (`> 1 \`http://…\``: 2138, 17486), a
bold label in a list (6629) and page counters, "1 / 28" (7733). Run 2
takes inline code, addresses and Markdown's own marks out of a line
first, and wants an operator or a Greek letter among its tokens. A
bracket, a slash or a bar alone no longer counts. Run 2's sample: 17480,
1551, 2538, 3275, 2667, 13544, 17535, 3415, 5845, 14465, 2241, 1638, all
garbled; 14465 is maths as readable plain text (`y = a cos ωt + b sin
ωt`), still not LaTeX. The fix it names: 547 marker, 39 the LaTeX
source on arXiv.

Run 1's `short-text` compared a text with the first 12 pages scaled to
the whole: a 114-page manual whose first pages are its contents (9175)
came out at 0.15. Run 2 judges only a PDF the profile read whole, and
counts characters without whitespace on both sides. Left: 7131 (0.48),
3016 (0.23), 12879 (0.02), 6354 (0.04), 7290 (0.38); 6354 is a page
whose text layer is a scanner's garbage ("GG GG G G GGG"), long on the
layer and short in the text.

## leftovers

Run 1 flagged 8,425 documents, 8,173 of them for an HTML tag; run 2,
with `<br>` in table rows set aside, 7,678, 7,426 of them for an HTML
tag. The tags are pymupdf4llm's format, not a fault of a document:
`<sup>1</sup>` for a footnote's mark (12980, 6105), `<sup>-6</sup>`
for an exponent (10003), `<br>` in a table's cell and in its "picture
text" blocks (3774, 9055, 9071). The check now leaves HTML out; showing
it is the document page's business. What stays: a latex2html comment
(222 documents, the books imported before `latex2html/2`), a subscript
as emphasis (23), an HTML entity (76).

## Found on the way

`pdf.profile` raised on an image MuPDF cannot load ("image width is
zero"), which would have broken the door's reader choice for that
document. A page or an image it cannot read is now left out.

A picture's text that pymupdf4llm read by OCR can be garbage (6354,
6414: `7ILV�KYX����4YROXI`); no check sees it yet.
