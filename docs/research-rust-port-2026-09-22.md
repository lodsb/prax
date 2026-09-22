# A Rust port: the dependencies, resolved

A desk study, 2026-09-22, of what a Rust prax would stand on. Not a
plan to do it: the door serves in 44 ms warm because SQLite and usearch
are native already, and the parsers are Python's strength. This note
exists so the question is answered once, with the crates that exist
today and what each would replace. Versions are as published on
crates.io at the time of writing.

## What prax depends on, and what stands in

| prax uses | for | Rust | state, and what changes |
|---|---|---|---|
| `sqlite3` + FTS5 + JSON1 | the store, keyword search, `meta` | `rusqlite` with the `bundled` feature | FTS5 and the JSON functions are in the bundled build. The SQL and the sixteen migrations carry over as files. `serde_json::Value` for `meta`. The three locks (`_serialized`, `_reading`, `_INDEX_LOCK`) become a `Mutex` around a writer connection and a per-thread reader pool |
| `usearch` (Python binding) | the vector indexes | [`usearch` 2.26](https://crates.io/crates/usearch) | the same C++ core, so the `.usearch` files are read as they are: `vectors-<model>.usearch`, the doc index, the deltas. Needs a C++ compiler at build time (`cc`); f16 and i8 views as today |
| `onnxruntime` + `tokenizers` + `numpy` | the query embedder (bge-small), the reranker (bge-reranker-v2-m3) | [`ort` 2.0-rc](https://github.com/pykeio/ort) + [`tokenizers`](https://crates.io/crates/tokenizers), or [`fastembed` 6](https://crates.io/crates/fastembed) which wraps both and names both models | `fastembed` is the short path: `TextEmbedding` defaults to bge-small-en-v1.5, `TextRerank` lists bge-reranker-v2-m3. `ort` links ONNX Runtime's shared library (downloaded per platform, linux-arm64 included); a fully static binary is possible but is its own project. The pure-Rust alternative, `candle-transformers`, has BERT and XLM-RoBERTa (a project ran bge-m3 on it in-process) and would drop ONNX Runtime entirely, at the cost of the ONNX files: safetensors instead |
| `fastapi` + `uvicorn` + `pydantic` | the door | `axum` + `serde` + `tower-http` | the routers map one to one; NDJSON streaming for `/ask` is a `Body::from_stream`; CORS, the body cap and the auth middleware are `tower` layers. The UI and the extension are static files and do not change |
| `httpx` / `urllib` | llama-server, Claude, Crossref, OpenAlex, the fetcher | `reqwest` with `rustls` | no OpenSSL. The private-address check is `std::net` + `ToSocketAddrs`; redirects are checked with a `redirect::Policy::custom` |
| `mcp` | the MCP proxy | [`rmcp`](https://crates.io/crates/rmcp), the official SDK | a proxy of one HTTP call per tool, a few hundred lines |
| `pyyaml` | `prax.yaml`, the ontology modules | `serde-saphyr` (the maintained parser; `serde_yaml` is archived and its forks are on the same dead lineage) | the settings and the ontology loader |
| `re` | chunking, blocks, references, glyphs, acronyms | `regex`, and `fancy-regex` where the twelve lookarounds are (questions, references, documents) | Unicode `\w` matches in both. The trap is elsewhere: locators are **character** ranges (`chunk.text == artifact[start:end]`); Rust indexes bytes. Either keep char semantics with `char_indices` at every locator, or change the locator to byte offsets in one migration and one `--rechunk`. The second is the right call and the first thing to do |
| `unicodedata` | glyph cleaning, WordPiece's accent stripping | `unicode-normalization`, `unicode-bidi` (for `python-bidi`) | small |
| `pymupdf` | the plain PDF text, the page count, scan detection, **figure extraction by xref** (`get_image_info`, `extract_image`) | [`mupdf` 0.8](https://github.com/messense/mupdf-rs) (builds MuPDF; AGPL, the licence prax already lives under through PyMuPDF), or `pdfium-render` (downloads a PDFium binary; static only on macOS) | text, page count and image extraction are there in both. The figure hashes are of the image bytes MuPDF returns, so staying on MuPDF keeps every `figure:<sha256>` reference valid; PDFium would re-encode and change every hash |
| `pymupdf4llm` | PDF to Markdown with headings, tables, the figure lines | **no port.** Candidates: [`unpdf` 0.7](https://github.com/iyulab/unpdf) (pure Rust, MIT, Markdown with headings, lists, tables, multi-column XY-cut, image extraction), [`kreuzberg` 4.10](https://docs.rs/kreuzberg) (a document framework on PDFium with in-process Tesseract/PaddleOCR, MIT), `pdfrs`, `pdf-text-extract` | this is the port's risk. `pymupdf4llm`'s layout logic (font-size headings, table finder, page separators, the r2 image lines) is what every stamp `pymupdf4llm/1.28.2-r2` in the store names; a different extractor is a new stamp and a re-read of 9,300 PDFs on the worker. `unpdf` is the closest shape and the youngest; it would need the figure line convention and the page separators added, and its output measured against the eval sets in `docs/eval/` before a single document is re-read |
| `trafilatura` | captured pages to Markdown, comments included | [`trafilatura` 0.2](https://crates.io/crates/trafilatura) (rs-trafilatura, a port of the Go port; GFM output with headings, links, tables, code; F1 0.966 against 0.948 for the Python on the ScrapingHub set), with [`dom_smoothie`](https://github.com/niklak/dom_smoothie) (readability.js) and `article_scraper` (FTR configs + readability) as fallbacks | better than expected: the wall from the first answer is a 0.2 crate by one person, worked on in bursts. Comments extraction and the figure lines (`figures.html_figures` over the snapshot's data URLs) would be prax's own pass over the DOM, as they are now |
| `rapidocr` + `python-bidi` | OCR of scanned pages | [`oar-ocr`](https://crates.io/crates/oar-ocr) or [`paddle-ocr-rs`](https://crates.io/crates/paddle-ocr-rs) (PaddleOCR's ONNX models through `ort`; the same models RapidOCR ships), [`ocrs`](https://github.com/robertknight/ocrs) (pure Rust, Latin only) | the same detection and recognition models, so the text is the same; a new stamp all the same (`pymupdf4llm-ocr` names the pipeline) |
| `docling` | the layout extractor | none; PyTorch | stays a Python worker, or goes. It is optional today and rarely asked for |
| marker | the mathematics | its own server, HTTP | unchanged |
| `magika` | code detection for `.txt` captures | [`magika` 1.0](https://crates.io/crates/magika), which is the Rust implementation Google rewrote it in | the Python package now wraps this |
| `lxml` | the video transcript HTML, the importers' exports | `scraper` / `dom_query` (html5ever) | fine |
| `zipfile` + `ElementTree` | Word and ODT | `zip` + `quick-xml` | fine; the inflate bound carries over |
| `striprtf`, LibreOffice | RTF, old office files | `rtf-parser`; LibreOffice stays a subprocess | fine |
| `anthropic` | the Claude backend | `reqwest` JSON; the unofficial crates are thin | a hundred lines |
| llama-server | ask, extract, vision, formulas, polish, titles | HTTP as today; `llama-cpp-2` exists if a worker ever wanted the model in-process | unchanged. The GBNF grammars are strings |
| `pystray` + `pillow` | the tray | [`tray-icon`](https://crates.io/crates/tray-icon) (Windows, macOS, Linux via GTK/ksni) | the supervisor's client, as now |
| the Task Scheduler / systemd / launchd entries | autostart | `auto-launch` | one entry, as now |
| `subprocess`, pid files, the command queue | `prax up` | `std::process` + `sysinfo`; job objects through the `windows` crate | Rust is the better home for this module |
| `importlib.metadata` | the extractor stamps | `env!("CARGO_PKG_VERSION")` and the crates' versions at build time | the stamps change once, to Rust names |
| `pytest`, 585 tests | the specification | `cargo test` with the same fixtures (the Zotero storage, the PDFs, the transcript HTML) | the tests are the port's contract; they go first, per module |

## What the port changes in the data

Three things, each a migration and one pass, all before the first
Rust line that reads a chunk:

1. **Locators to byte offsets.** `chunks.locator` holds character
   ranges; Rust wants bytes. A migration rewrites every locator from
   the artifact (one read per document), and the contract becomes
   `chunk.text == artifact[start..end]` over bytes.
2. **Extractor stamps.** Every `text_source`, `parse_history` entry and
   reading stamp names a Python package version. A Rust extractor is a
   new name, so the stale-parse logic sees every document as read by
   an old extractor. Either the new names are registered as
   equivalents of the old (a table in `parsers.behind`), or the whole
   library is re-read on the worker over a week.
3. **Figure hashes.** Kept, as long as the images come out of MuPDF
   byte for byte as today. Any other PDF library changes them, and
   with them every `![caption](figure:<sha>)` line and every filed
   reading.

## The order, if it were done

Each stage ports the tests first, then the code. The Rust door runs
beside the Python one on a second port, against the same `prax.db` and
`.usearch` files. Responses are diffed for a week before the Python
side is switched off for that area.

1. the store's `base` and `documents` with the migrations; byte
   locators; `chunking`, `blocks`, `references`, `glyphs`, `acronyms`
2. `retrieval` and `vectors` on `rusqlite` + `usearch` + `fastembed`:
   search answers the same top ten on the 62 library queries of
   `docs/eval/`
3. `graph`, `pages`, `jobs`, `summary`, `repair`, `maintain`, `backup`
4. the door: `axum` routers one area at a time, the middleware, the UI
   served as files
5. `ask` and `surf`, `questions`, `extraction.apply` and the typing
   rules, `resolution`
6. the worker: `mupdf` text and figures, `trafilatura`, the OCR crate,
   Word/ODT; the Markdown layout extractor last, measured against the
   eval sets before it replaces `pymupdf4llm` for anything
7. `up`, the tray, autostart, the CLI, the MCP proxy

Stages 1–5 are the door on the board: two to three months for one
person. Stage 6 is where the risk is, and stage 7 is where Rust pays
back. The importers (2,800 lines of export formats) are a stage of
their own that nothing else waits for.

## What stays unresolved

- A Markdown layout extractor for PDFs with `pymupdf4llm`'s quality
  and its figure lines. `unpdf` is a candidate to measure, not an
  answer.
- Docling, if it is wanted at all.
- ONNX Runtime as a shared library beside the binary, unless the
  embedder moves to `candle`.
- The Pi's build: `mupdf-sys` and `usearch` compile C and C++ on the
  target (or in an arm64 builder); an hour on the board the first time.
