> **Status:** the landscape survey the first decisions were drawn from
> (2025–2026), kept as source material, with a revisit written after the
> system was built (2026-09-13) and a survey of living answers and
> mixed pages (2026-09-21) placed first. The decisions, with the
> reasoning that still applies, are in `rationale.md`. Star counts,
> versions and benchmark figures are point-in-time and were not
> re-verified.

## Living answers and mixed pages: what the neighbours do (2026-09-21)

Written before the "ask block" stage (a model-maintained region inside a
person's page) and after the standing-question page (a page that is one
question). Four threads, read from the projects' own sources: atomic's
repository; the products that keep a query alive; the note tools'
conventions for a region a program owns inside a note; and the research
on LLM-maintained wikis, agent memory, living reviews and stale answers.
Sources are listed at the end of each thread; what could not be read is
said so.

### atomic, from its source

One wiki article per *tag* (the tag and its descendants), generated from
the chunks of captured atoms under it — centroid-ranked to about 60 % of
the model's context, or curated by a fifteen-step search agent — as one
long-form Markdown call with `[N]` citations resolved back to
`(atom, chunk, excerpt)`. Articles are AI-only records in their own
tables (`wiki_articles`, `wiki_citations`, `wiki_links`,
`wiki_article_versions`, `wiki_proposals`); a person never writes one,
only overrides the per-tag prompts and accepts or dismisses. An update is
a **proposal**: the atoms newer than the article's `updated_at` are
ranked, and the model emits *section operations* against the exact
existing headings — `NoChange | AppendToSection | ReplaceSection |
InsertSection`, citation numbering continued from the current maximum —
which a pure applier executes so that untouched sections stay
byte-identical; the person sees a line diff and accepts, which archives
the old version. Staleness is a **count** ("N new atoms available"): an
edit or a deletion never marks an article stale, a documented gap, and
centroid selection can drop an atom for good. The trigger is a button;
the planned background loop (a dirty set, a ten-minute quiet window, a
thirty-minute cooldown, a supersede budget, daily caps, a provider
precheck) is a plan, not code (issue #202 open).

Reports (the daily briefing, the weekly contradiction scan) are a
different primitive — *"wikis converge; reports accrue"* — a cron row
with a research prompt, a source scope (the atoms since the last run)
and a context scope (everything, or older than the source), a citation
policy (`source_only` or `source_and_context`), and caps; a run takes its
watermark when the scope is resolved, runs a three-tool agent
(`read_atom`, `semantic_search`, `done`) whose search excludes the source
batch and the report's own earlier findings, and writes one atom of
`kind = 'report'` plus a citation ledger (`report_finding_citations`
with position and excerpt, `ON DELETE SET NULL` and a name snapshot, so
provenance survives the report's deletion). Generated atoms are excluded
from wiki inputs, auto-tagging and other reports unless opted in — a
*kind discipline* every context-assembling method must honour. The
contradiction scan is a prompt over `since_last_run` against
`older_than_source` ("state the new claim with a citation, the older
claim, whether this is a clean contradiction or a tension"); no
embedding-side detection, no edge type. There is **no block-level
ownership**: no AI-maintained section inside a human note, no marker in
Markdown; separation is per record (`atoms.kind`, `atom_tags.source`),
and a report finding, being an ordinary atom, can be edited by hand with
nothing re-checking its `[N]` markers afterwards. Edges are chunk kNN
(cosine ≥ 0.5, ≤ 15 per atom, recomputed by deleting every edge that
touches the atom), untyped, with no provenance beyond the score.

Sources: the repository (README, CHANGELOG, `docs/manual/concepts/*`,
`docs/plans/wiki-proposal-loop-plan.md`, `docs/plans/reports.md`,
`docs/plans/automations-vision.md`, `docs/research/llm-wiki-gist-analysis.md`),
`crates/atomic-core/src/{db.rs, wiki/*, storage/sqlite/wiki.rs,
reports/*, embedding.rs, graph_maintenance.rs}`,
`crates/atomic-server/src/routes/wiki.rs`, `src/lib/reportTemplates.ts`,
issues #202 and #233, PR #182.

### Products that keep a query alive

- **Khoj automations**: a cron job whose run POSTs the query to the
  app's own chat endpoint into a dedicated conversation, so every run is
  an *appended chat turn*, never replaced or versioned; an LLM judge
  ("notify or not") decides whether the answer meets the person's
  condition before an email goes; a run is skipped within six hours of
  the last. No write into notes, no dedupe against the earlier answer.
- **Perplexity**: a Page is built once from a thread and edited by
  prompting sections; Scheduled Tasks run a prompt on a cadence, each
  run a fresh session, "stays quiet" when nothing is new. Page and run
  are separate objects; nothing ties runs back into the page.
- **Elicit, Consensus, Scite**: the *retrieval* stays alive (alerts as
  saved searches with new papers marked unread; scite dashboards that
  update as citations arrive), the *synthesis* is re-run by hand.
- **NotebookLM**: a "Saved Response" note is uneditable and blue-labelled
  (provenance by immutability); sources from Drive now sync, but
  "AI-generated notes remain intact".
- **Onyx, Glean**: scheduled tasks with a run ledger (queued, running,
  succeeded, failed, skipped, awaiting approval) and a fallback line in
  the prompt ("if nothing qualifies, say so; do not invent"); Glean's
  *content triggers* run an agent when a document changes, with
  source-specific filters ("unscoped triggers produce noisy results"),
  and a *verification* badge with an expiry that puts the document back
  on a to-do list.
- **AI-maintained fields in a human document**: Notion autofill
  overwrites a property on create, on edit (minutes later) or on a
  schedule, and "only if empty" is a prompt convention; Tana AI fields
  replace (fields) or append (children) with a sixty-second loop guard;
  Capacities previews the change with Generate / Deny / Approve; Mem
  removed its in-document writes for a side-by-side chat; the Obsidian
  Dataview Serializer materialises a query between
  `<!-- QueryToSerialize -->` and `<!-- SerializedQuery: END -->` with
  per-block policies (auto, manual, once, once-and-eject).
- **Saved-search alerts** (Zotero, Scholar, PubMed, ResearchRabbit): a
  query plus a high-water mark; only the delta is delivered.

Patterns: a live query block *or* materialised text between markers;
the run as an appended record and the page as another object (nobody
folds runs back into a page); overwrite by default, non-overwrite as a
policy; propose-then-accept only where the model touches human text;
quiet unless the delta matters; provenance by immutability or label;
triggers by clock, by content change with filters, or by hand, with loop
guards.

Sources: docs.khoj.dev and khoj's `routers/helpers.py`,
`api_automation.py`, `prompts.py`; Perplexity's help centre (blocked;
snippets) and third-party guides; support.elicit.com, elicit.com/blog;
scite.ai/features; support.google.com/notebooklm/answer/16262519 and
the 2026-05 Workspace update; docs.onyx.app (craft_scheduled_tasks);
docs.glean.com (schedule/content triggers, verification);
notion.com/help/autofill and /custom-agents; outliner.tana.inc
(ai-command-nodes); docs.capacities.io; get.mem.ai (2.0 transition);
dsebastien/obsidian-dataview-serializer; zotero.org/support/searching;
scholar.google.com/intl/en/scholar/help.html. Not reachable: Consensus'
help centre, My NCBI, Medium.

### A region a program owns inside a note

The clean precedent is **org-babel**: a named source block, its results
materialised under `#+RESULTS[hash]:` with a typed extent, `:results
replace | append | prepend | silent`, and the hash of block plus
arguments packed on the results line so a matching hash skips
evaluation; hand edits inside a `replace` result are simply lost.
**cog** adds the missing safety: a checksum on the generated region, and
"if the generated code is edited, the checksum won't match, and cog will
stop to avoid overwriting". **doctoc** and **markdown-magic** use
HTML-comment sentinels (`<!-- START doctoc --> … <!-- END doctoc -->`,
`<!-- doc-gen … --> … <!-- end-doc-gen -->`): invisible in every viewer,
surviving every editor, two stable lines in a diff. **Zotero
Integration** inverts the ownership — the file is the generator's and a
named `{% persist %}` region, rendered as `%% begin notes %% … %% end
notes %%`, is the person's, carried over verbatim on re-import — a
pattern for annotating inside a generated block without forking it.
**Dataview/Datacore/Bases** keep the query in the file and never
materialise the result; **Logseq** renders `{{query}}` live but persists
the view state as block properties; **Foam**'s regenerated link section
(deleted and recreated on save) documents the cost of churn (issue
#1339). Smart Connections and Copilot do not write into notes; Text
Generator and Templater insert once, unmarked. Typed links in Markdown
exist already: Breadcrumbs and Juggl read `rel:: [[x]]` inline fields
and quoted links in frontmatter; a bare `[[x]]` is untyped.

The delimiters compared — HTML-comment pair, `%% %%`, fenced block with
a language tag, a heading with a marker, a frontmatter record, a block
id — the comment pair is the only one that renders as nothing
everywhere, survives round trips, diffs cleanly and can carry a hash;
the fenced block is right when the query should be visible and the
result is not stored.

Sources: orgmode.org/manual (Evaluating Code Blocks, Results of
Evaluation, Structure of Code Blocks); cog.readthedocs.io;
github.com/thlorenz/doctoc; DavidWells/markdown-magic;
mgmeyers/obsidian-zotero-integration `docs/Templating.md`;
blacksmithgu.github.io/obsidian-dataview; github.com/blacksmithgu/datacore;
silentvoid13.github.io/Templater; smartconnections.app docs;
docs.obsidiancopilot.com; docs.text-gen.com; obsidian.md/help
(properties, bases, links, graph); publish.obsidian.md/breadcrumbs-docs;
juggl.io/link-types; logseq/docs (Properties, Built-in Properties);
briansunter/logseq-plugin-gpt3-openai; docs.foam.md and foam #1339;
wiki.dendron.so (note refs); nbformat and quarto docs.

### LLM-maintained wikis, agent memory, living reviews, stale answers

STORM and Co-STORM (Stanford) write Wikipedia-like articles section by
section from a source pool with citations; neither updates an article
when sources change, and Co-STORM's moderator keeps a list of sources
*retrieved but never cited* — a ready-made candidate list for revision.
Karpathy's LLM-wiki gist (raw, immutable; wiki, model-written; a lint
that finds contradictions, superseded claims, orphans) is silent on
regenerate-versus-edit; its derivatives answer it two ways —
`<!-- @generated -->` / `<!-- @user -->` sentinel blocks with a nightly
agent that never deletes without confirmation (obsidian-second-brain),
and per-claim *evidence-version pinning* with a never-rewritten human
file and changes shipped as a pull request (LangChain OpenWiki). Agent
memory converged on: a trigger that is a count or a budget, never a
clock (Generative Agents' importance sum; Letta's sleep-time agent after
N steps); edits in place under a size cap (Letta memory blocks); and
contradictions resolved by **invalidation with dates, not deletion**
(Zep/Graphiti's four timestamps — prax's edges already carry them; Mem0's
graph marks relations invalid). Cochrane's living systematic reviews
separate the two channels: a dated "Amended" line on every monthly
search ("N studies pending") and a rare full update when new evidence
"is likely to impact review conclusions", with an author-written
"what changed" rather than a textual diff, and an editorial gate to skip
one.

On stale answers: models struggle with fast-changing facts and false
premises (FreshQA); naming the *type* of conflict — outdated,
complementary, debate — lifts type-appropriate behaviour by 9–24 points
(DRAGged into Conflicts); new evidence is retrieved in 77 % of cases but
old memory entries are judged as needing an update in 3 % — the
"adjudication gap" — and a write-time KEEP / STALE / REPLACE / UNKNOWN
pass lifts it to 68 % (STALE); anchoring on a prior answer survives
chain-of-thought, reflection and "ignore the hint", and self-refinement
converges in about three iterations without dislodging it; the
edit-based updater (FRUIT/EdiT5: emit edits with copy tokens and a
reference per edit) hallucinated less than regeneration. The gain,
several papers find, comes from separating evidence identification from
answer writing rather than from asking the model to track freshness.

Lessons: trigger on evidence, not clocks (the clock runs the search);
pin a block's claims to the documents and text versions they rest on;
revise as typed edits, not regeneration; adjudicate old claims against
new hits at write time, outside generation; hand the model only active
evidence, newest last, old claim and new evidence side by side;
invalidate, never delete, stamp the pass; mark machine text and fence
human text; two channels to the reader (a cheap dated status every pass,
a rare versioned rewrite with a prose "what changed"); a human gate for
the rewrite.

Sources: arXiv 2402.14207 and 2408.15232 (STORM, Co-STORM);
gist.github.com/karpathy/442a6bf555914893e9891c11519de94f;
Astro-Han/karpathy-llm-wiki; eugeniughelbur/obsidian-second-brain;
langchain-ai/openwiki; docs.factory.ai (AutoWiki); letta.com/blog/memory-blocks
and docs.letta.com (sleep-time); arXiv 2504.19413 (Mem0); arXiv
2501.13956 and getzep/graphiti; arXiv 2304.03442 (Generative Agents);
Cochrane's 2019 LSR guidance (PDF), PMC12018299, f1000research.com
(living systematic reviews); arXiv 2310.03214 (FreshLLMs), 2506.08500
(DRAGged into Conflicts), 2506.07270, 2305.13300 (Adaptive Chameleon),
2605.06527 (STALE), 2608.01619 (StateAuditor), 2606.01435, 2412.06593
and 2505.15392 (anchoring), 2303.17651 (Self-Refine), 2607.22653,
2112.08634 (FRUIT/EdiT5). Not verified: Mem0's "State of Agent Wikis"
(paywalled), Letta's memory-tool names (snippets), LangMem and Cognee
(snippets), the Elliott 2017 article (403).

### What prax takes from it

Recorded as a decision in `rationale.md` (R17). In one line each: the
comment-pair sentinel with a hash (cog's rule) for a block a model owns
inside a person's page; the query in the marker, the result inside, the
pass in the marker's attributes, never in the text; a person's text
outside the pair untouchable and a `keep` region inside carried over
verbatim; the check as a cheap dated status, the rewrite as a rare
revision with a prose note; typed edits with the kind of change named;
the block's words set aside from retrieval like a reference list's; a
`page_sources` record of what a block consumed, so an edit, a re-read
or a retirement marks it stale, not only a count; and links a person
writes as edges. What it does not take: proposals with a diff for a
block the model owns (fields overwrite; a person who wants the gate has
`mode=propose` later), similarity edges, and a contradiction scan that
is only a prompt — that waits for `argues` edges and an adjudication
pass.

## Revisit, 2026-09-13: the landscape a year on, and where prax sits

The survey below was written before the first line of prax; this
section looks at the same field with the system built, and says where
it lands. Numbers are from the projects' own pages on 2026-09-13 and
were not re-verified beyond that; the point is the shape of each
neighbour, not its star count.

### What changed in a year

- **MCP is table stakes.** Every neighbour that matters exposes tools to
  an agent now: Karakeep's server grew from 8 to 29 tools, Cognee,
  Graphiti, LightRAG, txtai, SwarmVault, Basic Memory and Smart
  Connections ship one, Zotero has four third-party ones (ZotSeek,
  ZotPilot, zotero-mcp, llm-for-zotero) that read `zotero.sqlite` and
  build a vector index beside it, Paperless-ngx has one with 115 tools.
  "An agent can search my library" no longer distinguishes anything.
- **The Markdown-wiki pattern arrived.** Karpathy's "LLM wiki" gist
  (April 2026, 5k stars) — raw sources, an agent-maintained wiki of
  pages, a schema file, ingest/query/lint — spawned a family:
  SwarmVault (TypeScript, MIT, ~700 stars) compiles 30+ formats into a
  wiki plus a `graph.json`, hybrid SQLite FTS and embeddings, and tags
  "every edge `extracted`, `inferred`, or `ambiguous`" with
  contradiction detection; TideMind is a single-SQLite "memory layer"
  between note apps and AI tools with decay and reinforcement; Basic
  Memory keeps typed relations in Markdown for an assistant's memory.
  These are the closest relatives in spirit, and the convergence on the
  same three confidence words was independent.
- **The agent-memory platforms grew up.** Cognee reached 1.0 in April
  2026 (30k stars, a seed round, an embedded SQLite + LanceDB + Kùzu
  stack, RDF ontologies, fourteen retrieval modes, a UI and MCP);
  Graphiti's bi-temporal graph now runs on embedded Kùzu as well as
  Neo4j/FalkorDB; LightRAG merged multimodal parsing (May 2026) and has
  role-specific model configuration; txtai 9 added sparse and late-
  interaction retrieval. All are frameworks or memory for agents, not a
  library for a person.
- **The bookmark managers learned to read.** Karakeep (AGPL, 29k stars,
  SQLite + Meilisearch) added semantic search (0.33), embedding-guided
  auto-tagging with local Ollama, highlights, browser extensions and
  mobile apps; Linkwarden added AI tagging through OpenAI, Anthropic and
  Ollama. Neither has a graph or provenance; both organise what was
  saved rather than read it.
- **Khoj Cloud closed (April 2026)**; the self-hosted Django + Postgres
  app remains, with an embedded-database option for pip installs.
- **Reference managers stayed put.** Zotero 7/8 have no AI of their own;
  the MCP servers around it give an agent passage-level search over the
  library and nothing more (no captures, no graph, no pages).
- **Literature agents** (PaperQA2, FutureHouse) got the headlines for
  autonomous reviews with citations; they are a library over a folder
  of PDFs, not a store that grows from other sources.
- **Commercial desktop**: DEVONthink 4 (macOS) added local models via
  Ollama/LM Studio, versioning and a reader view — the closest
  commercial "one person's everything", closed and Mac-only.

### Where prax sits

| family | representatives (2026-09) | what they are | what prax does differently |
|---|---|---|---|
| bookmark and read-later managers | Karakeep, Linkwarden, Raindrop, Readwise Reader | save, tag, summarise, search what you saved; apps and extensions | reads what was saved: originals archived by hash, text artifacts, addressable chunks, a graph with evidence; papers and books first-class; no mobile app, no tagging UI |
| reference managers and their MCPs | Zotero + ZotSeek / ZotPilot / zotero-mcp | the curated library, and an agent searching it by passage | imports the library read-only and owns the rest: captures, chats, stars, bookmarks in the same store; a graph and pages on top; models chosen per step |
| Markdown wikis for agents | Karpathy's LLM wiki, SwarmVault, TideMind, Basic Memory, Obsidian + Smart Connections | an agent-maintained wiki or memory as plain files, with a graph derived from them | the originals stay canonical and the wiki is one layer among several (pages are documents); a hand-written, versioned ontology per kind of life instead of a schema the model infers; built and measured at 10k documents and 900k chunks, where a wiki of pages is not the index |
| agent memory and GraphRAG platforms | Cognee, Graphiti/Zep, LightRAG, txtai, mem0 | frameworks: extract, store, retrieve for an application's agents | a finished tool for a person that agents also use; provenance on every edge (confidence, evidence, source document, ontology version, producer, run, bi-temporal), a review queue for misfits, retire-not-delete, heal; one SQLite file, no server databases |
| document archives | Paperless-ngx (+ paperless-ai, PaperCortex, paperless-mcp) | OCR and file administrative documents; AI tagging and semantic search bolted on | research and making rather than administration; OCR explicit, bounded and language-aware; the graph as the organising structure instead of tags and correspondents |
| literature agents | PaperQA2 | an agent that reviews a folder of PDFs with citations | `ask` is the modest cousin: a bounded bundle, citations resolved to chunks, the answer kept on a page; the store, not the agent, is the product |

### The closest one per family, feature by feature

● yes, ◐ partly or through an add-on, – no. September 2026, from the
projects' own pages.

| | originals kept, by hash | reads PDFs and papers | words + meaning search | typed graph | an ontology you write | who wrote each edge, from what | local models | no server database | apps, extension |
|---|---|---|---|---|---|---|---|---|---|
| **prax** | ● | ● | ● | ● | ● | ● | ● | ● | ◐ extension, no mobile |
| Karakeep (bookmarks) | ◐ page archives | ◐ stored, text searched | ● | – | – | – | ● | ◐ SQLite + Meilisearch | ● |
| Zotero + an MCP server | ● Zotero's own | ● | ◐ meaning | – | – | – | ● | ◐ Zotero's SQLite + an index | ● |
| SwarmVault (LLM wiki) | ● `raw/` | ● | ● | ● | ◐ a schema file | ◐ extracted / inferred / ambiguous | ● | ● | ◐ clipper |
| Cognee (agent memory) | – | ● | ● | ● | ● RDF | ◐ | ● | ◐ three embedded engines | – |
| Paperless-ngx (documents) | ● | ● OCR | ◐ add-on | – | – | – | ◐ add-on | – | ◐ third-party apps |

Read the columns, not the dots. The bookmark manager is the one to keep
links in, and it has the mobile app. The Zotero servers are the way to
give an agent a curated library and nothing else. The LLM-wiki family
makes the generated pages the index, where prax keeps the originals
canonical and the pages as one layer among several. Cognee is a
framework for an application's agents; prax is a finished tool for a
person that agents also use. Paperless files paperwork.

What is genuinely prax's own, as far as this survey can see: the
combination of a **content-addressed store of the originals** under a
single-writer door, a **small modular ontology written by hand and
versioned** (one module per kind of life, a document read against its
subset, the version stamped on the edge), **provenance complete enough
to redo a model's work** (producer and run on every edge, `retire_run`,
bi-temporal validity), **models as configuration with a measured
local-first stance** (`docs/eval/`: a 35B model on one card against
Sonnet 5 over the same papers), and a **Pi-class serving target** with
the model work drained through the door from wherever the GPU is —
exercised on one library of 9,700 documents rather than a demo.

What the neighbours have that prax does not: mobile apps and a
tagging-first UI (Karakeep); audio, video, e-mail and calendar
ingestion (SwarmVault); memory that decays and reinforces (TideMind);
global "summarise the whole corpus" retrieval over community summaries
(GraphRAG, Cognee's modes); an autonomous literature review (PaperQA2);
a hosted option and multi-user (Khoj, Basic Memory Cloud, Zep); RDF
ontologies (Cognee); thirty-tool MCP surfaces with CRUD on everything
(Karakeep, LightRAG, Paperless). The agent story here is Claude Code
first, the change feed is polling, scheduling is cron.

Sources consulted: Karakeep [releases](https://github.com/karakeep-app/karakeep/releases) and [README](https://github.com/karakeep-app/karakeep);
Linkwarden [2.10](https://linuxiac.com/linkwarden-2-10-bookmark-manager-released/) and [linkwarden-mcp-server](https://deepwiki.com/irfansofyana/linkwarden-mcp-server);
Khoj [setup](https://docs.khoj.dev/get-started/setup/) and [app.khoj.dev](https://app.khoj.dev/) (cloud sunset);
Zotero MCPs: [ZotSeek](https://github.com/introfini/ZotSeek), [ZotPilot](https://forums.zotero.org/discussion/130483/zotpilot-mcp-server-for-semantic-search-classification-and-literature-review-drafting-from-your-z), [zotero-mcp](https://github.com/54yyyu/zotero-mcp), [llm-for-zotero](https://yilewang.github.io/llm-for-zotero/), [an overview](https://danielborek.me/2026/zotero-mcp-ai/);
[Karpathy's LLM wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), [SwarmVault](https://github.com/swarmclawai/swarmvault), [TideMind](https://github.com/SawyerHan-AI/TideMind), [Basic Memory](https://docs.basicmemory.com/start-here/what-is-basic-memory), [Smart Connections](https://community.obsidian.md/plugins/smart-connections), [Neural Composer](https://community.obsidian.md/plugins/neural-composer);
[Cognee](https://github.com/topoteretes/cognee) and its [MCP](https://github.com/topoteretes/cognee/tree/main/cognee-mcp), [Graphiti](https://github.com/getzep/graphiti), [LightRAG](https://github.com/hkuds/lightrag) and [lightrag-mcp](https://pypi.org/project/lightrag-mcp/), [txtai 9.0](https://medium.com/neuml/whats-new-in-txtai-9-0-d522bb150afa);
Paperless-ngx: [paperless-mcp](https://www.npmjs.com/package/@orellbuehler/paperless-mcp), [PaperCortex](https://github.com/renefichtmueller/PaperCortex), [paperless-ai](https://github.com/clusterzx/paperless-ai);
[PaperQA2](https://github.com/future-house/paper-qa); [DEVONthink 4](https://www.devontechnologies.com/apps/devonthink/new) and [local AI](https://www.devontechnologies.com/blog/20251111-local-ai-devonthink).

---

# The original survey (2025–2026): Building a Self-Hosted Research Knowledge Base with a Claude/MCP Interface: Open-Source Landscape and Stack Recommendations

## TL;DR
- **Compose from libraries around SQLite, don't adopt a monolith.** Your planned architecture (SQLite + FTS5 + sqlite-vec + edge table, content-hash files, one FastAPI door, thin MCP server, replaceable capture front-ends, nightly LLM enrichment) is the right shape for Pi-class hardware and a single maintainer. No existing all-in-one (Khoj, RAGFlow, R2R, Cognee) matches it without dragging in Postgres/Elasticsearch/Neo4j/Docker sprawl that fights your low-power, single-file-durability goals.
- **For the graph layer, do not adopt Microsoft GraphRAG; borrow LightRAG's incremental dual-index idea and keep edges in SQLite (or Kùzu if you outgrow it).** GraphRAG re-computes community summaries on update; LightRAG and nano-graphrag support incremental insert. But even rich LLM-extracted graphs structurally fail "inverse"/complement and weighted risk-propagation queries — so treat GraphRAG as an entry-point enhancer, not a reasoning engine.
- **Use Karakeep or Linkwarden as the replaceable capture front-end, Docling for PDFs + trafilatura/SingleFile for web, a quantized bge-small ONNX embedder for CPU/ARM, RRF + an optional bge-reranker cross-encoder for hybrid retrieval, and FastMCP for the agent door.** Migrate the external-disk store by hashing files into a content-addressed store and back-filling FTS/vectors/edges through the same FastAPI ingest path.

## Key Findings

**1. The "adopt-a-system" options are capable but heavier than your target.** Khoj (AGPL-3.0, ~37k stars) is the closest philosophical match — a self-hostable "second brain" with semantic search, PDF/Markdown/Notion ingestion, agents, and multi-client access — but it is a Django + Postgres/pgvector application, not a single-file SQLite system, and it centralizes rather than exposes a plain edges table. RAGFlow (~70k stars) and R2R are production-grade but assume Postgres/Elasticsearch/MinIO/Redis-class infrastructure. Cognee (Apache-2.0, ~30.3k stars, 9,790 commits) is the most feature-complete "AI memory" match, targets document-heavy self-hosted knowledge graphs, defaults to an embedded Kùzu + LanceDB + SQLite stack, and can run entirely on one Postgres — but it is a fast-moving product platform, more than a single maintainer needs.

**2. GraphRAG library trade-offs are dominated by incremental-update cost and known reasoning failures.**
- **Microsoft GraphRAG**: highest indexing cost (community detection + summarization); a `graphrag update` command exists since v0.4.0 (Nov 2024) that computes a delta rather than a full re-index, but community summaries still need periodic recompute after drift. LazyGraphRAG defers LLM calls to query time — per Microsoft Research's blog (editor's note, June 6, 2025), it "has been integrated into Microsoft Discovery… and into Azure Local services as part of a public preview," and its "data indexing costs are identical to those of vector RAG" (~0.1% of full GraphRAG) while matching global-search quality at "more than 700 times lower query cost."
- **LightRAG (HKU)**: dual-level (low/high) graph+vector retrieval; incremental insert with minimal API calls and no full re-index; pluggable storage (NetworkX default; Neo4j; PostgreSQL as an all-in-one KV+vector+graph via pgvector+Apache AGE; also Milvus/Qdrant/Mongo/OpenSearch). Has a first-party server + WebUI and multiple community MCP servers (30+ tools). Strong on diversity metrics but poor faithfulness in one financial-QA benchmark: per the FinSage paper (arXiv:2504.14493), "LightRAG optimized for speed (mean: 12.16s) but delivered the poorest faithful evaluation performance with only a 13.67% pass rate, which is not acceptable in the financial question answering domain" (vs GraphRAG 42.50%, FinSage 82.67%).
- **nano-graphrag**: ~4.0k stars, MIT, ~1,100 LOC, "smaller, faster, cleaner GraphRAG." Supports incremental insert (md5-hash dedup) but re-computes communities on each insert. NetworkX/Neo4j graph backends, swappable vector/KV. Appears low-maintenance/quiescent (155 commits, 68 open issues) but not archived; its momentum moved to derivatives (LightRAG, fast-graphrag, HiRAG).
- **Graphiti (Zep)**: ~30k–45k stars (sources vary), Apache-2.0, `pip install graphiti-core`. The standout is a **bi-temporal model** (valid-time + ingestion-time per edge) with fact invalidation (supersede, don't delete) and real-time incremental updates — no batch recompute. Runs on Neo4j or FalkorDB (not embedded/SQLite), and ships a full MCP server. Best fit if temporal/versioned edges matter.
- **LlamaIndex PropertyGraphIndex**: flexible orchestration; default in-memory `SimplePropertyGraphStore` persists to disk; pluggable to Neo4j/Kùzu/etc.; supports `insert_nodes` incremental updates and configurable extractors (SchemaLLMPathExtractor against a fixed ontology). Good "glue" if you want a framework rather than a from-scratch pipeline.
- **txtai**: Apache-2.0, all-in-one embeddings DB with SQLite-backed content store, sparse+dense indexes, **semantic graph** built from the embeddings index, inline SQL (`similar()`), and an existing MCP server. The lightest "batteries-included" option that stays close to SQLite.
- **Neo4j graphrag-python / Kùzu**: Neo4j is a server dependency (heavier); Kùzu is an **embedded** property-graph DB (C++, Cypher, ACID, file-on-disk, WASM build) that LlamaIndex/LangChain integrate with — the natural upgrade path from a plain SQLite edge table if you need real Cypher traversal.
- **Documented failure modes** (arXiv 2606.06003, small synthetic 46-node aerospace KG, single-author v1 preprint — treat as directional): both LLM-GraphRAG and LightRAG (v1.4.16, which extracted 244 entities and 362 relationships) **fail on inverse/complement queries** ("which customers are NOT affected…", requires computing a blast-radius subgraph and returning the complement) and on **weighted risk-propagation scoring** (multi-hop numeric scores with hop-distance decay). Only a deterministic hand-built traversal engine solved all queries, and its latency stayed ≤11 ms P95 even at 1,100 nodes. Lesson: retrieval graphs are entry points, not calculators.

**3. Building-block libraries are mature and Pi-friendly.**
- **PDF parsing**: Docling (IBM Research, now Linux Foundation-hosted) is the recommended self-hosted, no-per-page-fee choice, reporting 97.9% table extraction accuracy (published by IBM Research on the DocLayNet benchmark and confirmed independently by Ertas AI, whose testing found Docling's figure "held up," vs Unstructured hi-res 93.4% and Marker 91.7%); it ships a compact Granite-Docling-258M model. Marker is fastest and best for academic/equation-heavy PDFs (91.7% table accuracy, weaker on borderless tables). Unstructured gives typed semantic elements (Title/Table/ListItem) useful for chunking but is slower. Note: these carry ML dependencies and are heavy on a Pi — run parsing as a nightly batch job, not inline.
- **Web capture/parsing**: trafilatura/readability for article-text extraction; SingleFile (~20k stars) and monolith (~15k stars) for faithful single-HTML snapshots; WARC (ISO 28500) / WACZ for archival fidelity. ArchiveBox already stores exactly your target layout — SQLite index + per-snapshot folders with HTML/PDF/PNG/WARC/SingleFile/readability outputs as plain files.
- **Embedded stores**: sqlite-vec keeps vectors in the same SQLite file (no server; "deploying a new index is copying a file") — the recommended default for personal/single-dev RAG. Chroma is a "batteries-included" local process; LanceDB (columnar, disk-based, memory-mapped) scales to millions/billions of vectors and handles the ingestion/update cycle gracefully if you outgrow sqlite-vec. Meilisearch is the full-text engine Karakeep/Linkwarden already use.
- **Embedded graph**: plain SQLite edge tables + recursive CTEs are adequate under ~50k entities (one writeup runs a knowledge graph this way and "ditched Neo4j"); Kùzu is the embedded upgrade; DuckDB + VSS is an analytics-flavored alternative.
- **Hybrid retrieval**: run BM25 (FTS5) and vector search in parallel, fuse with **Reciprocal Rank Fusion** (rank-based, sidesteps incompatible score scales), then optionally rerank the fused top-N with a cross-encoder. Reported lifts, per denser.ai's 2026 hybrid-search guide citing Turnbull (2025): a tuned hybrid reached 0.7497 NDCG on WANDS, "a 7.4% lift over either BM25 (0.6983) or pure vector search (0.6953) alone"; and per Strich et al. (2026, arXiv:2604.01733, T2-RAGBench, 23,088 queries / 7,318 docs), "a two-stage pipeline combining hybrid retrieval with neural reranking achieves Recall@5 of 0.816" vs 0.587 dense-only.
- **CPU/ARM embedding + rerank**: bge-small-en-v1.5 (384-dim, MiniLM-class) balances speed/quality; INT8-quantized ONNX variants keep accuracy loss within ~1% while cutting latency (neuralmagic/DeepSparse reports 3–5× speedups on CPU). Model2Vec static embeddings are an option for the tightest hardware — per MinishLab's README, it "reduces the size of a Sentence Transformer model by a factor of up to 50… just ~30 MB on disk, and our smallest model just ~8 MB (making it the smallest model on MTEB)" and runs "up to 500 times faster on CPU than the original model" (its 8M model scores ~50 MTEB vs all-MiniLM-L6-v2's ~56). bge-reranker-v2-m3 is the common cross-encoder reranker; LightRAG recommends it and switches to "mix mode" when a reranker is enabled.
- **MCP framework**: FastMCP is the de-facto standard (its high-level API was folded into the official SDK; standalone FastMCP reached 3.0 GA Feb 2026, now under PrefectHQ). Recommendation from multiple sources: use standalone `fastmcp` unless you have a hard constraint to depend only on the official `mcp` package. Both support stdio (for local Claude Desktop/Code) and streamable HTTP (for remote access over Tailscale).

**4. Capture front-ends map cleanly onto a replaceable "inbox."** Karakeep (AGPL-3.0, ~28.6k stars, formerly Hoarder) = keep+organize+AI-tagging (local Ollama or OpenAI), Meilisearch search, saves links/notes/images/PDFs. Linkwarden (AGPL-3.0, ~19.6k stars) = archive-first (auto PDF + screenshot of every page). ArchiveBox = evidence-grade multi-format preservation. Paperless-ngx = OCR document archive (Django+Postgres+Redis), runs on a Pi 4/5 (4 GB) but OCR is bursty (idles ~800 MB, spikes to 1.5–2 GB and pins one core); bulk imports are much faster on an N100/x86 box. All expose REST APIs and store files as plain files, so any of them can feed your FastAPI ingest via folder-watch or API poll.

**5. Real-world personal-KB builds converge on your exact pattern.** Multiple 2025–2026 writeups describe RAG-over-SQLite personal second brains: Pagefind indexing + embeddings into sqlite-vec at "under a cent per query"; a team that ran a knowledge graph on SQLite recursive CTEs + sqlite-vec and "ditched Neo4j" (brute-force cosine over 10k 384-dim embeddings loads in milliseconds; fine below ~50–100k entities). The Karpathy "LLM wiki" pattern (plain interlinked Markdown files an agent reads/writes, no vector store) is a credible lightweight alternative for a few hundred pages, claimed ~70× more efficient than RAG for agent-accessible knowledge — worth knowing as a fallback, though it doesn't scale to your PDF corpus.

## Details

### Comparison: adopt-a-system candidates
| Project | Stack / storage | Retrieval | API / MCP | Capture | Health | License | Pi-class? |
|---|---|---|---|---|---|---|---|
| **Khoj** | Django + Postgres/pgvector | Semantic + keyword, agents | REST; Obsidian/Emacs/desktop clients; no first-party MCP | PDF, MD, Notion, Word, org, images | ~37k stars, very active | AGPL-3.0 | Runs, but Postgres-class; not single-file |
| **RAGFlow** | Postgres/MySQL + Elasticsearch/Infinity + MinIO + Redis | Deep-doc chunking, hybrid, GraphRAG | REST; agent templates | Many formats, deep OCR | ~70k stars, very active | Apache-2.0 | Too heavy for a Pi |
| **R2R** | Postgres (light mode: pip install) | Agentic/hybrid, KG, Deep Research | RESTful API | Multimodal (text/PDF/image/audio) | Active | (open) | Light mode possible; full mode Docker |
| **Cognee** | Kùzu+LanceDB+SQLite embedded default; Neo4j/Neptune/Postgres/pgvector/Qdrant | Vector + graph + ontology | MCP server, Claude Code plugin, Python/TS/Rust | Any format | ~30.3k stars, 9,790 commits, very active | Apache-2.0 | Embedded stack runs locally; product-heavy |
| **txtai** | SQLite (+ optional Postgres) content store, ANN + keyword + graph | Semantic + SQL + semantic-graph traversal | FastAPI; MCP server exists | Text/docs/audio/image | Active | Apache-2.0 | **Yes** — closest to SQLite-native |

### Comparison: GraphRAG / KG libraries
| Library | Incremental update | Indexing cost | Retrieval modes | Storage backends | Temporal/versioning | Self-host fit |
|---|---|---|---|---|---|---|
| **MS GraphRAG** | `update` delta since v0.4.0; communities still recompute on drift | High (community detection + summaries) | Local / global / DRIFT | Parquet files | No | Heavy; LLM-call-intensive |
| **LightRAG** | Yes, minimal API calls, no full re-index | Low (~embed-text parity) | Naive/local/global/hybrid/mix | NetworkX / Neo4j / Postgres(AGE+pgvector) / Milvus / Qdrant / Mongo / OpenSearch | No | Good; server+WebUI+MCP |
| **nano-graphrag** | Yes (md5 dedup); communities recompute per insert | Low, ~1,100 LOC | Local/global + naive | NetworkX / Neo4j; hnswlib/faiss/milvus | No | Good if forked; quiescent |
| **Graphiti** | Yes, real-time, no batch recompute | Per-episode LLM extraction | Semantic + keyword + graph, point-in-time | Neo4j / FalkorDB (not embedded) | **Bi-temporal edges, invalidation** | Needs a graph server |
| **LlamaIndex PGIndex** | `insert_nodes` | Depends on extractors | Vector + graph + text; multi-hop | Simple(disk) / Neo4j / Kùzu / Chroma / Qdrant | Limited | Flexible glue |
| **txtai graph** | Append to embeddings index | Low | Semantic-graph traversal + SQL | SQLite content + ANN | No | **Lightest** |
| **Kùzu (raw)** | Native inserts | N/A (you build extraction) | Cypher multi-hop | Embedded file | No (TuringDB/FalkorDBLite offer versioning) | Embedded, Pi-friendly |

### Your architecture, validated and annotated
- **SQLite canonical store**: correct. FTS5 for BM25, sqlite-vec for vectors, plain edges table (`src, dst, rel, confidence, valid_from, valid_to, source_doc`) for the graph. Recursive CTEs handle 1–2 hop traversal well under ~50k entities. Borrow Graphiti's bi-temporal columns (valid-time + ingestion-time) so nightly enrichment can invalidate rather than delete edges.
- **Content-hash filesystem**: correct and matches ArchiveBox's proven layout. On a Pi, **put the SQLite DB and archive on an external SSD, not the SD card** — SD-card write wear from FTS/vector index churn and nightly jobs is the #1 reliability risk; SD cards should be OS-only.
- **One FastAPI service as the single door**: correct. Keep ingest/get/search/link/traverse as the only mutation path so capture front-ends, the MCP server, and cron all go through the same validation and hashing.
- **Thin MCP server**: use FastMCP; expose `search` (hybrid), `get`, `traverse`, `link`, `ingest` as tools over stdio (Claude Desktop/Code local) and streamable HTTP (remote over Tailscale). Keep it a thin proxy to the FastAPI layer — do not duplicate logic.
- **Capture as replaceable front-ends**: Karakeep (best organization + local AI tagging) or Linkwarden (best archival) writing to an inbox folder/S3 that a watcher syncs into your ingest path. Both use Meilisearch internally, but you should re-index into your own FTS5/sqlite-vec so search is unified and independent of the front-end.
- **Nightly cron LLM enrichment**: entity/relation extraction against a small versioned ontology, confidence-tagged edges, entity resolution. This is where LightRAG/nano-graphrag prompt patterns are worth borrowing. Run it as a batch to avoid inline latency and to batch LLM calls. Version the ontology in the DB and stamp each edge with the ontology version so re-runs are auditable.
- **GraphRAG-style retrieval**: vector/keyword entry points → 1–2 hop traversal. This is exactly LightRAG's dual-level idea. Accept that complement ("what is NOT connected") and weighted multi-hop scoring queries won't be answerable by retrieval alone — handle those with explicit SQL/graph queries exposed as separate MCP tools.

### Migration path from the external-disk store
1. **Freeze and inventory**: enumerate the existing PDFs/snapshots; compute a content hash (e.g., SHA-256) per file.
2. **Content-address**: copy each file into `archive/<hash[:2]>/<hash>` on the SSD; record (hash, original_path, mime, added_at) rows in SQLite. De-dup naturally falls out of hashing.
3. **Parse + index in a batch**: run Docling (PDFs) and trafilatura/readability (HTML) as a one-time backfill job, chunk, embed with quantized bge-small ONNX, write FTS5 + sqlite-vec rows. On a Pi this may take hours — run it on the N100 or a laptop once, then copy the single SQLite file over.
4. **Enrich**: run the nightly extraction pipeline over the backlog to populate the edges table with confidence-tagged, ontology-versioned relations; run entity resolution.
5. **Point capture tools at the inbox**; wire the MCP server; validate hybrid search + traversal from Claude.
6. **Thresholds to revisit the design**: if entity count exceeds ~50–100k or traversal latency degrades, migrate the edges table to Kùzu (embedded, keeps single-file-ish durability). If concurrent writes from multiple capture tools cause SQLite lock contention, move the metadata DB to Postgres while keeping files content-addressed.

## Recommendations

**Stage 0 — Prove the core (1 weekend).** Stand up the FastAPI door + SQLite (FTS5 + sqlite-vec + edges) + a FastMCP server exposing `search`/`get`/`traverse`. Ingest 20–50 existing PDFs via Docling and a handful of web snapshots via trafilatura. Embed with a quantized bge-small ONNX model. Confirm Claude can hit it over stdio locally and over streamable HTTP through Tailscale. This validates the "single door + thin MCP" thesis before any graph work.

**Stage 1 — Adopt a replaceable capture front-end.** Deploy Karakeep (if you want AI auto-tagging on-device via Ollama) or Linkwarden (if link-rot/archival is the priority) in Docker on the N100, writing to an inbox your watcher ingests. Keep your FTS5/sqlite-vec as the authoritative search index.

**Stage 2 — Add hybrid retrieval.** Implement BM25 (FTS5) + vector parallel retrieval fused with RRF. Add an optional bge-reranker-v2-m3 cross-encoder pass over the fused top-N — but benchmark it on the Pi; if it's too slow, keep it as an N100-only or on-demand step.

**Stage 3 — Add the graph, incrementally.** Write the nightly enrichment job yourself, borrowing LightRAG/nano-graphrag extraction prompts, against a small versioned ontology, writing confidence-tagged, bi-temporally stamped edges into the SQLite edges table. Expose 1–2 hop traversal as an MCP tool. Do **not** adopt Microsoft GraphRAG (re-index cost) or stand up Neo4j/Graphiti (server dependency) at this scale.

**Decision rules / thresholds:**
- **Stay with sqlite-vec** until vectors exceed ~1M or you need multimodal — then move that layer to LanceDB, keeping the rest in SQLite.
- **Stay with the SQLite edge table** until ~50–100k entities or slow recursive-CTE traversal — then move edges to **Kùzu** (embedded) before considering Neo4j.
- **Adopt Graphiti** only if you find you genuinely need point-in-time "what did I believe when" queries and are willing to run FalkorDB/Neo4j.
- **Adopt Cognee or LightRAG-server wholesale** only if maintaining your own enrichment pipeline becomes the bottleneck and you accept a heavier dependency; both give you MCP + graph out of the box.
- **Consider the Karpathy Markdown-wiki pattern** for the subset of knowledge that is your own notes (as opposed to PDFs) — it may beat RAG for agent recall at small scale.

## Caveats
- **Star counts and release states are point-in-time (2025–2026) and drawn from secondary trackers**; verify current numbers on GitHub before committing. FastMCP versioning in particular is fast-moving (standalone 3.0 vs the official SDK's renamed `MCPServer`).
- **The GraphRAG failure-mode findings come from a single-author v1 arXiv preprint on a tiny (46-node) synthetic dataset** with author-acknowledged validity threats (LLM-as-judge circularity, co-designed queries/handlers). Treat "inverse/risk-propagation failure" as directional, not a large-scale independent benchmark — but the underlying point (retrieval graphs are not calculators) is sound and architecturally robust.
- **PDF parsing accuracy benchmarks are vendor/blog-published**, not neutral; Docling's 97.9% table figure originates with IBM and was corroborated by one independent blog test, but real accuracy depends heavily on your specific documents — test on a representative sample.
- **CPU/ARM embedding latency numbers** (INT8 3–5×, Model2Vec up to 500×) are from Intel/Neural Magic/MinishLab sources on x86 Xeon or generic CPUs, not specifically a Raspberry Pi 5 / N100 — expect the Pi to be meaningfully slower; budget parsing and embedding as batch jobs.
- **Paperless-ngx explicitly stores documents unencrypted** and its maintainers warn against untrusted hosts; if you include it, keep it on the trusted home server behind Tailscale only.
- **SQLite concurrent-write limits** are real: it handles concurrent reads well but serializes writes. With multiple capture tools plus cron writing simultaneously, use WAL mode and a single writer (your FastAPI door) to avoid lock contention — this is another reason the "single door" design matters.