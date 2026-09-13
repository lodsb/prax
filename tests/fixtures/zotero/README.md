# The Zotero fixture

A small Zotero library — `zotero.sqlite` and the `storage/` folders it
points to — that the tests import, parse, chunk and search. Every file
in it is open access and carries its own terms, stated in the file
itself:

| storage key | file | terms (as printed in the file) |
|---|---|---|
| `FCEK3EI9` | Ambrits, Bank — *Improved polynomial transition regions algorithm for alias-suppressed signal synthesis* (DAFx 2013) | CC BY 3.0 Unported |
| `HJR45JMH` | Průša, Rajmic — *Toward high-quality real-time signal reconstruction from STFT magnitude* (IEEE SPL 2017, author version) | CC BY 3.0 |
| `M86T746S` | Carr, Zukowski — *Generating albums with SampleRNN to imitate metal, rock, and punk bands* (arXiv:1811.06633) | CC BY 4.0 International |
| `5QSJDXUQ`, `PTHEK9QS`, `R89PCW52` | *FugueGenerator — collaborative melody composition based on a generative approach for conveying emotion in music* (three copies of the same paper, as a Zotero library has them) | CC BY 3.0 Unported |
| `432URMMU`, `FBT2AP8S`, `RR3AAFYH`, `RWTJSFI7` | Rutz et al. — *On the traceability of the compositional process* (four copies) | CC BY 3.0 |
| `AYY57KAK` | *IIR Hilbert Transformer* — a Signal Processing Stack Exchange question and its answers, saved as a page | CC BY-SA 3.0 (the site's terms for posts of that date) |
| `UW29C5GP` | `sid-guts-diy-controls.pdf` — the controls sheet of ALM/Busy Circuits' SID GUTS DIY, byte for byte the file at [github.com/busycircuits/sid-guts-diy](https://github.com/busycircuits/sid-guts-diy) (`schematics/sid-guts-diy-controls.pdf`) | that repository states no terms for its schematics (its firmware is LGPL-2.1, © 2014 ALMCo Ltd); ALM's later open-sourced modules ([SID GUTS DELUXE](https://github.com/busycircuits/alm012-sid-guts-deluxe), Fizzle Guts) put their hardware files under CC BY-SA 3.0 and note that "ALM" is a registered trademark not to be used on derivative works. Here it is a one-page test of the studio module's schematic reading, unmodified and attributed |

The `.zotero-ft-cache` files beside the PDFs are Zotero's own full-text
cache of the same documents. The duplicates are deliberate: the importer
and the dedupe pass are tested on them.
