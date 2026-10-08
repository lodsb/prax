<p align="center"><a href="docs/design/BRIEF.md"><img src="docs/design/assets/logo/themes/prax-mark-bindery.svg" width="128" alt="The prax mark: a praxinoscope in elevation, printed in three passes in block, key and one colour facet"></a></p>

# prax

prax is a self-hosted library for the papers, web pages, manuals and
notes one person reads. Its search matches words and meaning at once,
its graph records what the documents say about each other, and an AI
agent can search and read it through the same interface its owner
uses. Everything lives in one
SQLite file and a folder of originals, on your own machines, with
models you choose.

    pip install -e ".[serve,work]"
    export PRAX_DATA_DIR=~/prax-data
    prax serve                                  # http://127.0.0.1:8000/ui/

    prax add paper.pdf
    prax add https://example.org/article
    prax search feedback delay networks
    prax ask --answer why do FDNs colour the tail

An empty store works from the first command. A new document is
archived as it arrives, and `prax work --watch`, run on the machine
with the models, does the rest: its text, title, graph links and
vectors. To keep the parts running, list them under `run:` in
`prax.yaml` and run `prax up --install`, which starts them at login on
Windows, Linux or macOS. Setting up the Zotero import and the browser
extension is in [`docs/howto.md`](docs/howto.md).

## Why I built it

My reading was spread over a Zotero library, an external disk I called
zoetrope, a NAS and a year of browser tabs. I could only find a paper
again if I remembered where I had put it. I wanted one place that keeps
the originals and reads them closely enough to answer "which of my
papers says this, and where". An agent working with me should be able
to search it without being handed whole PDFs. I built prax with Claude
Code over a few weeks in 2026. I decided what it should do and measured
whether it did, and the log of that work is in
[`docs/log.md`](docs/log.md). It is shaped by one library and one set
of machines, and it is published so the design and the measurements
can be read and reused.

## One question, followed through

Say I want to know why a feedback delay network colours the tail of a
reverb. A search from the shell returns documents, each with the
passage that matched (the brackets mark the matched words) and where
in the document it sits:

    $ prax search feedback delay networks colour the tail
     …
     3. The Role of Modal Excitation in Colorless Reverberation             doc 9577
        [Feedback] [delay] [networks] (FDNs) are a computationally efficient
        structure for artificial reverberation…
         text · page 1 · THE ROLE OF MODAL EXCITATION IN COLORLESS R…
     4. Allpass Feedback Delay Networks                                    doc 13371
        …arbitrary connection of [delay] lines, namely [feedback] [delay] [networks]
        (FDNs). We present…

`prax ask --answer` goes further when there is a model on the host. It
searches again with words of its own, reads on in the documents that
looked promising and drops what does not help. Then it writes an answer
whose citations point at the passages it kept. The trail of those steps
is shown under the answer, so you can see why a source was used.

<table>
<tr>
<td width="50%"><a href="docs/images/search.png"><img src="docs/images/search.png" alt="Search results in the web UI, each hit marked with the side that found it"></a></td>
<td width="50%"><a href="docs/images/ask.png"><img src="docs/images/ask.png" alt="An answer in the web UI with its cited passages beside it and the model's four steps of searching and reading under it"></a></td>
</tr>
<tr>
<td><sub>In the web UI each hit says whether keywords, vectors or the document's own summary found it.</sub></td>
<td><sub>Here the model searched again and read on before answering, and the trail under the answer shows each step.</sub></td>
</tr>
</table>

An agent asks the same library through the MCP server, and what comes
back is sized for its context window. The same question asked by an
agent, with three hits, is 1.8 KB of JSON. It opens with the region of
the library the hits live in, and each hit carries a `cite` link that
still finds the passage after the document is re-chunked:

```json
{"doc_id": 270, "title": "Building the Erbe-Verb: Extending the Feedback Delay Network Reverb for Modular Synthesizer Use",
 "snippet": "…on a 4-[delay] [feedback] [delay] network [reverb] (FDN) as proposed by…",
 "heading": ["…", "2. BASIC DESIGN"], "page": 1, "published": "2015",
 "cite": "#doc/270?chunk=1757785&find=a+unitary+feedback+matrix"}
```

The agent can then ask how two things are connected. Asked for
"feedback delay network" and "Schroeder reverberator", `connect`
answers in 1.2 KB with one path of two steps through a paper on
scattering delay networks, and each step quotes the sentence it rests
on. One says the paper uses the Schroeder reverberator: "Starting with
the Schroeder reverberator [3, 4], a wide variety of approaches for
room acoustics simulation have been introduced…". The other says it
uses feedback delay networks, on the strength of "…found that they
perform better than Feedback Delay Networks (FDNs)". That is a generous
reading, and because the quote comes with it, an agent or a person can
see so and weigh it.

## What it does

### Reading figures with the words around them

Most PDF tools stop at the text. prax pulls each figure out, or renders
the region above its caption when the figure was drawn with vector
paths. A vision model then reads it together with the paragraphs that
refer to it. That is why a plot comes back as "the frequency responses
of the five learned CNN kernels against the ground truth filter"
instead of "six stacked curves". The reading
is a passage of the document, so you can search for a figure by what it
shows, cite it, and an answer can use it. A scanned book with no text
layer is read the same way, page by page.

<table>
<tr>
<td colspan="2"><a href="docs/images/vision-with-context.png"><img src="docs/images/vision-with-context.png" alt="A figure in the document view: the image, its caption, and the vision model's reading of it naming each step of the process"></a></td>
</tr>
<tr>
<td colspan="2"><sub>The reading names the process the figure shows and its four steps, because the model was given the caption and the text that refers to it.</sub></td>
</tr>
<tr>
<td width="50%"><a href="docs/images/image-recognition.png"><img src="docs/images/image-recognition.png" alt="A schematic as a document, described and transcribed by two vision models"></a></td>
<td width="50%"><a href="docs/images/citations.png"><img src="docs/images/citations.png" alt="A paper's reference list in the document view: each entry a chunk of its own, and under it the library document it cites, matched by title with its score"></a></td>
</tr>
<tr>
<td><sub>A schematic read by two vision models; both readings are kept, each with the model that wrote it.</sub></td>
<td><sub>The entries of a paper's reference list are matched against the library, and the [12] in the text links to the paper it cites.</sub></td>
</tr>
</table>

### A graph whose links say where they came from

A model reads each document against a small ontology: which paper uses
which method, which manual belongs to which piece of gear, which recipe
needs which ingredient. Every link it writes records the document, the
sentence, the model, the batch it ran in and the ontology version. I
wanted that because models misread papers, and a link I cannot trace to
a sentence is one I cannot check. When a later reading disagrees, the
old link is given an end date and kept. A database trigger refuses any
attempt to delete a link or change what it says, so you can ask what
the graph said on a given day. A whole batch from one model can be
withdrawn without touching anyone else's work. A fact that states its
own date ("she worked at the lab from 2019") keeps that date
apart from the date prax learned it. Names are kept apart from things:
*apple* the ingredient and *Apple* the company are two entities, and a
merge of two names into one thing can be taken back.

<table>
<tr>
<td colspan="2"><a href="docs/images/graph.png"><img src="docs/images/graph.png" alt="A method's neighbourhood in the graph view, every link with its evidence and its source"></a></td>
</tr>
<tr>
<td colspan="2"><sub>A method and its neighbours, where a link shows how sure the extraction was, which model wrote it, and the document and sentence it came from.</sub></td>
</tr>
</table>

### Search

Keyword (SQLite FTS5) and vector search run over the passages and over
a short description of each document. The four lists are merged per
document, and you can filter them by kind of document or by subject. Acronyms the library defines ("feedback delay network
(FDN)") are expanded on the keyword side. Each hit shows when its
document was published, as precisely as the source says, so you can
ask for only what appeared after a given year. A document that a newer
one replaced is still found, a few places lower, with a note naming its
replacement. The numbers, with their caveats, are further down.

### Pages of your own

Notes, project logs and write-ups are Markdown pages with a revision
history, and they are searched and read like any other document. A link
from a page to a document becomes a link in the graph. If you put a
question between two comment lines, prax answers it there with sources
and answers again when new documents arrive. The second time, the model
sees its earlier answer and is told what is new, so it revises. If you
edit inside an answer, prax leaves that block alone. A daily briefing page lists what
arrived and which answers changed.

<table>
<tr>
<td colspan="2"><a href="docs/images/ask-block.png"><img src="docs/images/ask-block.png" alt="A page of one's own notes with an ask block: the person's prose and links above, the answer below with the question on its rim, the documents the page links and cites beside it"></a></td>
</tr>
<tr>
<td colspan="2"><sub>A page of notes with a standing question in it; the documents the page links and the ones the answer cites are listed beside it.</sub></td>
</tr>
</table>

### And the rest

- Documents come from a Zotero library (read from a copy, never
  written to), a drop folder, and the browser extension. The extension
  sends the page you are reading as a self-contained snapshot, a paper
  as its PDF and a YouTube talk as its transcript and frames. `prax
  import` reads exports from GitHub stars, chat apps, bookmarks, Pocket,
  Raindrop and Medium, and `prax sync` sends a project's notes from its
  git working copy.
- PDFs are read by MuPDF with optional OCR, or by marker when you want
  the maths as LaTeX. HTML goes through trafilatura, and Word files,
  code and LaTeX sources have parsers of their own.
- Rules flag documents that look personal (a bank statement, a letter)
  and you decide. A token handed to an agent or another machine sees
  only the domains you give it and no personal documents. Because the
  filter sits in the store, search, the graph and answers all leave out
  the same documents.
- `prax heal` lists recurring damage (a placeholder entity, a page
  captured twice, a scan filed under its cover's title, figures not yet
  read) and prints the command that fixes each. A fix hides a wrong
  document with its history and deletes nothing.
- The UI has six themes, each four colours that change the page and the
  logo together ([`docs/design/BRIEF.md`](docs/design/BRIEF.md)).

## Ways in

The web UI at `/ui/` has search and ask, a document with its context
and figures, the graph, the review queue, your pages, the inbox and the
jobs. The `prax` command covers the whole library from a shell, and it
is how I use it most:

    prax                              where things stand, what to type next
    prax search granular synthesis    find documents
    prax ask --answer how does a feedback delay network work
    prax add ~/Downloads/paper.pdf    a file, a folder, a URL, or piped text
    prax import links bookmarks.html  what a service exported
    prax show 4312 | less             read one in the terminal
    prax graph "wave digital filter"  what the graph knows around a name
    prax status · jobs · heal · backup · doctor
    prax up · serve · work --watch    run it (--tray: an icon in the tray)

A command is one HTTP call to the service, so `--json` makes any of
them usable from a script and `--door` points the same command at
another machine. The browser extension sends what you are reading,
including pages behind your login. The MCP server gives an agent the
same reads as the UI, each as one HTTP call. They cover search, a
document by offset and length, the graph around a name, how two things
connect, what changed in a week, and a figure or a scanned page as an
image. Whatever a script or an agent writes carries its name, so you can
inspect it or take it back as a unit. Recipes and the contract are in
[`docs/integrating.md`](docs/integrating.md).

## Models and hardware

Which model does which step is a line in `prax.yaml`. It can be a GGUF
served by llama.cpp on your own card, any OpenAI-compatible server, or
the Claude API for the few documents worth paying for, and where a
person does the job better the line says `none`. A paid model is called
only by a command you run, so private material stays on your machines unless you send it. The
library below was read by local Qwen models on one RTX 4090, and the
comparisons with the hosted model are in [`docs/eval/`](docs/eval/).

prax runs on one machine or two. In the two-machine setup a small board
holds the database and the service, and a desktop with a GPU does the
model work through the same HTTP interface. The serving path is held to
a gigabyte of memory so that the board can be a Raspberry Pi or an N100
box.

## The library it was built on

These are the numbers of my own instance on 7 October 2026. It holds a
Zotero import, the contents of a NAS and a year of browser captures,
after a few weeks of model passes. They show the scale prax has been
run at; they are not a benchmark.

| | |
|---|---|
| Documents | 13,000: 10,200 PDFs, 2,200 office and text files, 600 web pages, 42 talks from YouTube, 28 pages of my own. 8,960 came from Zotero, 3,360 were uploaded or dropped in the folder, 620 were sent from the browser. About 400 are marked personal |
| Text and passages | 1,486,000 passages (1,102,000 text, 160,000 reference entries, 120,000 figures, 55,000 tables, 33,000 formulas, 14,000 code), 1,325,000 of them with a vector; 8,900 acronyms the library defines |
| Figures | 120,000 across 6,200 documents, served out of the originals; 62,000 read by the local vision model |
| Graph | 216,000 live relations over 162,000 entities (48,000 papers, 34,000 concepts, 19,000 methods, 10,000 authors, 10,000 tools), against nine ontology modules in eight domains; 45,000 of the relations are citations. 181,000 ended relations are kept as history, and 43,000 names are folded into another (a different spelling, an initials form, another language) |
| Languages | 74% English, 18% German, a few in French, Spanish, Italian and Dutch, and 12% too short or too scanned to tell |
| Running | one Windows desktop: service, worker and llama-server started at login, figures read from 21:00, merges at 03:00, maintenance at 03:30, a backup at 04:30, the standing questions at 06:30. A 3.1 GB database, a 1.3 GB vector index and a 26 GB archive |

Retrieval is measured on 62 questions I wrote about my own library
(keyword, paraphrase and structure questions), as the mean reciprocal
rank of the document I had in mind. On 8 September 2026 keyword search
alone scored 0.82, vector search alone 0.80 and the merged search
0.79, so merging did not help yet
([`retrieval-library-2026-09-08`](docs/eval/retrieval-library-2026-09-08.md)).
After a month of changes the merged search scores 0.90, with the right
document first for 87% of the questions
([`retrieval-facts-list-2026-10-05`](docs/eval/retrieval-facts-list-2026-10-05.md)).
Those changes were checked against the same 62 questions, so 0.90 is
an optimistic figure. A held-out set of questions is still to be
written, and the results can be repeated only on this one library.

## When to use something else

prax is for one person who wants to keep the originals, see where every
claim in the graph came from, and give agents bounded access. If one of
these describes you better, another tool is the better choice:

- You need citations in Word or Google Docs, sync across devices, or
  shared group libraries: use [Zotero](https://www.zotero.org/). prax
  reads a Zotero library and does not replace it.
- Your knowledge is notes that you and your agents write, and the
  Markdown files themselves should be the record:
  [Basic Memory](https://github.com/basicmachines-co/basic-memory).
- You want a chat assistant over your documents on every device, or a
  hosted service: [Khoj](https://github.com/khoj-ai/khoj).
- You are building an application whose agents need memory:
  [Graphiti](https://github.com/getzep/graphiti) (a library over Neo4j,
  FalkorDB or Neptune) or [Cognee](https://github.com/topoteretes/cognee)
  (a library, embedded by default).
- You need most facts dated as they are read, or facts that hold a
  plain number or date: [Utopia](https://github.com/deeplethe/utopia)
  does both. A walk in prax can keep what held in the world on a date
  (`world_at`), but under 1% of its facts are dated so far, and its
  facts always link two things.
- Several people will use it, or it must face the open internet: prax
  has no user accounts and no installer.

[`docs/compared.md`](docs/compared.md) sets out how prax stores facts,
time and evidence beside Utopia, Graphiti, Cognee and Basic Memory, as
read from their code at named commits. The wider survey is in
[`docs/research.md`](docs/research.md#where-prax-sits).

## How it works

A document's bytes are archived once under their SHA-256, so the same
file sent twice is one document, and the database keeps the metadata
and the hash. A parser writes the text, which is stored under its own
hash and stamped with the parser that made it. The text is cut into
passages that each know their place in it: text under a heading,
tables, figures, display equations, code, the entries of a reference
list. The passages go into an FTS5 index and a usearch vector index.
Then a model reads the document against the ontology modules it belongs
to. Each relation it finds becomes a link with its evidence, or an item
in a review queue when it fits no type.

Everything derived from a document (text, passages, vectors, links,
summaries, figure readings) is a model's work and is stamped with what
produced it. When a better model or prompt comes along, the documents
read under the old stamp can be found and read again, and the earlier
reading stays beside the new one. The originals are the only thing that
cannot be made again, which is why they are the only thing kept as they
arrived.

The service is the only process that writes to the database. Parsing,
extraction and embedding are batch jobs, done by a worker that fetches
work from the service over HTTP and posts the results back. That lets
the worker run on the GPU machine while the database stays on the
board.
[`docs/architecture.md`](docs/architecture.md) follows a document and
a query through the code.

## Status

prax is one person's tool. There is no installer and no multi-user
story, and the defaults reflect one library. The service has not yet
moved onto the serving board, though the code for it is in
[`deploy/`](deploy/), and what else is unfinished is in
[`docs/PLAN.md`](docs/PLAN.md). Every push runs the test suite on Linux
and Windows and the quick start above in a fresh venv
([`ci.yml`](.github/workflows/ci.yml),
[`scripts/smoke.sh`](scripts/smoke.sh)). The extension has a test bed
that drives it in headless Chrome and Firefox
([`scripts/extension_bed.mjs`](scripts/extension_bed.mjs)), and I use
it daily in Firefox, Waterfox and Chrome. Issues and pull requests are
welcome, though they may wait.

The service is meant for a private network (a LAN or a VPN such as
Tailscale). Its administrator token can read and change everything, the
named tokens you hand out see only what you give them, and nobody has
audited it. Do not expose it to the internet.

## Documentation

| | |
|---|---|
| [`docs/howto.md`](docs/howto.md) | Setting up, the batch jobs, `prax.yaml`, the service, the board, backup. |
| [`docs/integrating.md`](docs/integrating.md) | Using the library from scripts, agents and other tools. |
| [`docs/ask.md`](docs/ask.md) | What the asking model can and cannot do, what it costs, how to steer it. |
| [`docs/sources.md`](docs/sources.md), [`docs/extension.md`](docs/extension.md) | Where documents come from, and the browser extension. |
| [`docs/architecture.md`](docs/architecture.md) | The system as built: hosts, the life of a document and of a query, the modules, where to touch what. |
| [`CLAUDE.md`](CLAUDE.md) | Invariants and conventions; the file an agent session loads. |
| [`docs/rationale.md`](docs/rationale.md) | Decision records: what was chosen, what was measured, when to revisit. |
| [`docs/compared.md`](docs/compared.md), [`docs/research.md`](docs/research.md) | prax beside the systems nearest to it, and the survey of the field. |
| [`docs/eval/`](docs/eval/) | Measurements: extractors, retrieval, the local models. |
| [`docs/PLAN.md`](docs/PLAN.md), [`docs/log.md`](docs/log.md) | What is next, and the record of what was done, with the measurements under each night. |
| [`prax.example.yaml`](prax.example.yaml) | Template for `prax.yaml`: models, steps, every other setting. |

The design notes behind particular parts are
[`stratification`](docs/stratification.md),
[`identity`](docs/identity.md),
[`generalizing`](docs/generalizing.md),
[`normalization`](docs/normalization.md),
[`review`](docs/review.md),
[`packs`](docs/packs.md),
[`communities`](docs/communities.md),
[`graph-files`](docs/graph-files.md),
[`meta`](docs/meta.md),
[`ui`](docs/ui.md),
[`claude-workflow`](docs/claude-workflow.md) and the
[`design brief`](docs/design/BRIEF.md). How the ontology grew is in
[`v2`](docs/ontology-v2.md), [`v4`](docs/ontology-v4.md),
[`v5`](docs/ontology-v5.md), [`v6`](docs/ontology-v6.md),
[`v7`](docs/ontology-v7.md), [`v8`](docs/ontology-v8.md),
[`v9`](docs/ontology-v9.md), [`studio`](docs/ontology-studio.md),
[`electronics`](docs/ontology-electronics.md) and
[`craft`](docs/ontology-craft.md).

## The name

The praxinoscope came after the zoetrope: the same spinning drum, with
mirrors that gave a sharper image. prax came after zoetrope, the
external disk that held the same library before.

## License

MIT, see [`LICENSE`](LICENSE), except the browser extension.
[`clients/browser-extension/`](clients/browser-extension/) is AGPL-3.0
because it bundles SingleFile for page snapshots, the way the Zotero
connector does; it is a separate program that talks to the service over
HTTP. The test fixture under [`tests/fixtures/`](tests/fixtures/) holds
open-access papers under their own Creative Commons terms, listed with
their licenses in
[`tests/fixtures/zotero/README.md`](tests/fixtures/zotero/README.md).
