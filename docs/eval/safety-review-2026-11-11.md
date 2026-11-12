# The safety and correctness review of 2026-11-11

A workflow of twelve agents read everything changed since `2093c44`
(the last review), the structure review's fixes among them: six
reviewers by area (data safety, the wall, the new logic, the host and
external input, contracts and sizes, tests and docs), each finding then
re-checked by a skeptic told to refute it and to reproduce it on a
temporary store. 19 confirmed, 3 refuted; 15
distinct defects, the unretire one found by four areas and the
vector search's by two. Read-only: no agent opened the live store or
called the running door. Before the fixes, a read-only look at the live
store found no retirement undone since finding 2's code went in, and no
local server configured with a key (findings 3 and 12), so no live data
needed repair.

Each fix has a test that fails on the code the review read, in
`tests/test_review_2026_11_11.py` or beside the code it changed.

The refuted ones:

- A keyed status line with a word the lexicon does not know stops the
  scan and so hides a stale banner below it (`src/prax/text/status.py`).
  It is meant to: the first status line is the document's status (AU
  step 1, decided with the project whose notes these are).
- `test_step_stamps`' guard skips `documents/meta.py`, which holds three
  due queries (`tests/test_step_stamps.py`). On purpose: that file builds
  them from `STAMPS`, so the hand-written test the guard looks for cannot
  be there.
- CLAUDE.md says every step's due query is built from `store.STAMPS`,
  while extraction keeps its own stamp (`CLAUDE.md`). Extraction's stamp
  is the ontology version, a mechanism of its own that the line does not
  claim to cover.

## 1. A named token could see a document just marked personal: the hidden set's memo was keyed by the connection's address

*high*, wall; `src/prax/store/base.py:1029`

**Scenario.** A named token's request runs on an anyio worker thread. Its thread_connection computes hidden_documents and memoises it under (viewer, file, id(con), PRAGMA data_version, total_changes). The owner then marks document D personal (set_sensitivity, committed on another connection). The worker thread sits idle past anyio's MAX_IDLE_TIME of 10 s and exits, which frees its connection. The next request thread opens a new connection. CPython reuses the freed object's address, so id(con) is the same. A fresh connection always reads data_version 2, and total_changes is 0 until it writes. That rebuilds the old key exactly, and _hidden_memo returns the set from before the change. D is then returned by search and every walk, as though it were open. The same key, store_state(con), is used by the media members memo (it holds the sensitivity column) and by the People memo, so those also serve rows from before the change. store_state's docstring says a memo keyed by it 'never outlives a write'. That is false across connections. The key shape predates the Memo refactor (7b6d963), which kept it.

**Evidence.** Ran a throwaway script against a temporary store (PRAX_DATA_DIR set to a temp folder) with .venv/Scripts/python.exe. (1) Plain sqlite3: five connections opened one after another in threads, with a commit between each, gave keys [(…1232, 2, 0), (…9552, 2, 0), (…1232, 2, 0), (…9552, 2, 0), (…1232, 2, 0)]: 2 distinct keys out of 5. (2) Through prax: a thread set VIEWER=Viewer('friend'), called store.thread_connection() and store.search(con, 'tomatoes', 5, mode='fts'), then exited; after the first round, store.set_sensitivity(admin, doc, 'personal'). Output: 'column says hidden: False  search returned: [1]', then 'True / []', then 'True / [1]', 'True / []', 'True / [1]'. On every second round the personal document comes back to the named viewer. Confirmed that _con() is store.thread_connection() and that anyio's WorkerThread.MAX_IDLE_TIME is 10 in the venv.

**The skeptic.** Confirmed. store_state (src/prax/store/base.py:1029-1040) keys on (file, id(con), PRAGMA data_version, total_changes). A new connection always reads data_version 2 and total_changes 0, and CPython reuses a freed connection's id. Nothing clears _hidden_memo: Memo.clear is never called from api or store. api/_base.py _con is store.thread_connection(), which is thread-local, so the connection dies with its anyio worker thread. Repro (scratchpad r0.py, tmp PRAX_DATA_DIR, .venv python): each round, a fresh thread sets VIEWER=Viewer('friend'), runs thread_connection() and search('tomatoes', mode='fts'), then exits. After round 0, set_sensitivity(admin, doc, 'personal'). Output: round 0 (id …4064, 2, 0) hits [1]; round 1 (…0944, 2, 0) []; round 2 (…4064, 2, 0) [1]; round 3 []; round 4 [1], while the column reads 'personal' throughout. The personal document goes back to the named viewer whenever an earlier connection's address is reused. This is a leak through the wall. Other memos keyed by store_state (media members, People) have the same flaw. Fix: key on a serial number unique to each connection (for example, a counter attached in connect() or in thread_connection's thread-local), not on id(con). Or drop a connection's entries when it is collected (weakref.finalize).

**Fix proposed.** Key the memos on something a new connection cannot repeat. One option is a serial number given to each connection in store.connect, for example sqlite3.connect(..., factory=_Conn) where _Conn.__init__ sets self.serial = next(_SERIALS); store_state then returns that serial in place of id(con). Another is to also key on a global write stamp that only increases (for example a counter that _serialized commits bump, or a max-rowid read). Add a test that opens and closes thread connections across a set_sensitivity call and checks that hidden_documents follows the column.

**Done.** `store.Connection`, the class `connect` makes every connection with, carries a `serial` no other connection of the process has had, and `store_state` keys on it (a connection `connect` did not make falls back to its address). The People and media memos use the same key. Test: `test_schema.py::test_a_new_connection_never_takes_an_old_ones_memo_key`, which also shows the address does come round again.

## 2. Taking one document of a clean-up back stated the facts of every document of the set

*high*, logic, data, wall, tests; `src/prax/store/documents/library.py:254`

**Scenario.** retire_set retires documents A and B under one run, cleanup-<ts>-<rule> (retire_document(..., run=run) at cleanup.py:160). A person then unretires A alone (DELETE /doc/{id}/retire, api/capture.py:153). unretire_document calls _restore_run(con, meta.retired.run). The run is the whole set's, so the call restates every edge_endings row of that run, B's too. B stays retired (meta.retired is set, no chunks) but its edges are live again with source_doc = B. Retirement no longer ends a document's facts (invariant 8, 'its edges end'), and walks, rules and conflicts read facts sourced to a retired document. Before 023d2ad this could not happen: a single unretire restored no edges, and only restore_set ran restore_run for the set.

**Evidence.** Read git show 023d2ad (library.py, cleanup.py, base._restore_run). Ran a throwaway script against a tmp store (PRAX_DATA_DIR under %TEMP%): two documents, one edge each, both retired with retire_document(run=run_name('cleanup','test')), then unretire_document on the first. Output: "unretire a: {'doc_id': 1, 'chunks': 1, 'edges': 2}" and "b still retired: 1 live edges of b: 1".

**The skeptic.** Confirmed. unretire_document (library.py:254) calls _restore_run(con, meta.retired.run), and retire_set (cleanup.py:160) passes one run for the whole set, so _restore_run (base.py:463-483) restates every edge_endings row of that run (WHERE x.run = ? AND restated_as IS NULL), whichever document it came from. I reproduced it with a throwaway pytest in %TEMP%\sk0 on the repo's con fixture (tmp store). Two documents A and B each got one edge, both were retired with retire_document(run=run_name('cleanup','test')), then unretire_document(A) was called. Live edges' source_doc after the call: [1, 2]. B (2) still has meta.retired but its edge is live again. Fix: in unretire_document, restore only this document's endings (for example _restore_run(con, run, source_doc=doc_id), filtering edge_endings to edges with e.source_doc = doc_id, or the moved edges this retirement wrote), and keep the whole-run restore for restore_set.

**Fix proposed.** Scope the restatement to the document. One option: _restore_run takes an optional source_doc, which unretire_document passes, and restates only edge_endings of that run whose edge has that source_doc. restore_set keeps the whole run. The other option: give each retirement its own run (cleanup-<ts>-<rule>-<doc>), record the set's name separately for cleanup_runs and restore_set, and have restore_set iterate. Add a test: retire two documents under one clean-up run, unretire one, and assert the other has no live edges.

**Found again** by data (medium), wall (medium), tests (medium).

**Done.** `_restore_run(source_doc=)` undoes one document's part of a run: the edges it ended of that document, and none of the run's own. `unretire_document` passes it. Test: `test_unretire.py::test_one_document_of_a_clean_up_comes_back_alone`.

## 3. A free local server that takes an API key was charged at Opus rates

*high*, host; `src/prax/models.py:510`

**Scenario.** A chat server on the LAN or tailnet is set up the way prax.host.roles (lines 267-279) asks: api_key_env on the model, so llama-server gets --api-key. Steps run on it for free: titles, summaries, vocabulary, sections, typing, and extract with workers=1. Each OpenAIRuntime.chat now calls usage.record(self.model, ...) under the bare model name, e.g. 'qwen3-14b'. Pass.post sends that as 'spent', and work.take_in -> leases.note_spend -> budget.note(model='qwen3-14b') prices it with pricing.price. price_of only matches runtime_name ('qwen3-14b@192.168.1.5:8080'), and the bare name has no '@' and is not 'local:', so it gets the unknown-model fallback (5.0, 25.0). The ledger then shows real-looking dollars for free work. budget.allows then holds every step that really costs money (promote, extract on Claude, vision, and ask, which answers without a model) once the day's or month's limit fills up with these phantom charges. A keyed server that is priced (paid) is also charged under the wrong name, at Opus rates instead of its own price: (5, 25) here against price: [2, 8] in howto.md's example.

**Evidence.** Read models.OpenAIRuntime.chat (new block at lines 510-521, added in this range), steps/base.Pass.post (usage.take -> 'spent'), work.take_in lines 672-673, budget.note (the model= branch uses extraction_cost = pricing.cost_usd), pricing.price and models.price_of. Ran a snippet against a temp prax.yaml (qwen kind openai, base_url http://192.168.1.5:8080/v1, api_key_env QWEN_KEY, titles -> qwen) with post_json stubbed to return 1M prompt and 100K completion tokens. Output: spec.paid False, runtime_name qwen3-14b@192.168.1.5:8080, usage.take() {'qwen3-14b': {...}}, budget.extraction_cost('qwen3-14b', ...) = 7.5 USD.

**The skeptic.** Confirmed. models.OpenAIRuntime.chat (src/prax/models.py:510-521, added in 920c651) calls usage.record(self.model, ...) whenever api_key_env is set, under the bare model name. prax.host.roles (lines 267-279) gives every llama-server bound on a LAN or tailnet address --api-key from api_key_env, so the code's assumption that 'a local llama-server takes none' does not hold. pricing.price only gives 0 to names with '@' or 'local:', and models.price_of only matches runtime_name ('model@host'), so the bare name always falls back to Opus rates (5, 25). I ran a snippet against a temp prax.yaml (kind openai, base_url http://192.168.1.5:8080/v1, api_key_env QWEN_KEY, price [2, 8], titles -> qwen) with post_json stubbed to 1M prompt and 100K completion tokens. Output: resolve paid=True, runtime_name qwen3-14b@192.168.1.5:8080, price (2.0, 8.0); usage.take() {'qwen3-14b': {...}}; pricing.price('qwen3-14b') = (5.0, 25.0); budget.extraction_cost = 7.5 USD where the right figure is 2.8. Without a price the right figure is 0. This goes Pass.post -> work.take_in (line 672) -> leases.note_spend -> budget.note(model=...) -> ledger, which feeds the budget limits. Fix: record under the spec's runtime_name (self.name, which is model@host) and only when the spec is paid. Or have price_of match the bare model too.

**Fix proposed.** Record under the runtime name the ledger can price: usage.record(f"{self.model}@{urlparse(self.base_url).netloc or 'server'}", ...), which matches ModelSpec.runtime_name and so price_of. An unpriced server then costs 0 through the '@' rule. Better still, record only when the spec is paid (pass paid/price into OpenAIRuntime from models.runtime). An API key means only that the server is locked, not that it bills.

**Done.** `OpenAIRuntime.chat` records a keyed server's use under the runtime's name (`model@host`), which `prax.yaml` prices or which costs nothing (`pricing.price`). Test: `test_models.py::test_a_keyed_server_is_charged_by_its_own_name`, a priced and an unpriced server. On the live host no local server is configured with a key, so the ledger holds no such charge.

## 4. A node kept a retired namesake as its document: a note synced again listed its old version

*medium*, data; `src/prax/store/base.py:852`

**Scenario.** _claim_node writes entities.doc_id only while it is NULL, and nothing ever moves it. retire_document, _join_duplicate and the project refresh never touch it. A project note is synced as D1 and its node 'Note (lab)' claims D1. The note changes, a new sync ingests D2 with the same title and retires D1 with duplicate_of=D2, which is exactly what capture/projects.py:540 does. The node still says doc_id = D1. Every reader that now goes node -> document gets the retired copy. get_page(lab)['members'] lists D1 and not D2. pages.tree._project_edges (WHERE s.doc_id = D2) finds no projects for the current version. retrieval.fusion staleness (WHERE doc_id IN (hit docs)) finds no supersedes/invalidates edge reaching D2, and names no replacing document whose node still points at a retired doc. The same happens when two documents share a title and the one that claimed the node is retired. Before 2e2bdf2 these readers chose a live document by title.

**Evidence.** Read base.py:785-881, graph/edges.py:406-420, fusion.py:516-545 and pages/tree.py:47-60 and 145-158. grep finds no writer of entities.doc_id besides _claim_node, _claim_document_nodes and the heal repair, all of which only fill a NULL. Ran a throwaway test: write_page project 'lab', ingest D1 'Note (lab)', add_to_project, ingest D2 with the same title, retire D1 with duplicate_of=D2, add_to_project D2. Members were [('Note (lab)', 2)], the retired D1, and the entity row was (1, 'Note (lab)', 'document', doc_id=2). A second test: node_document('Note (lab)', 'document') returned the retired d1 (1) instead of the live d2 (2).

**The skeptic.** Read base.py:785-881 (_node_doc and _claim_node: they write doc_id only while it is NULL) and edges.py:406-420 (node_document: it returns e.doc_id and checks only the wall, not whether the document is retired). grep finds no other writer that moves entities.doc_id. I reproduced it on a tmp store (rep1b.py): a project 'lab', D1 'Note (lab)' added to it, D2 with the same title, D1 retired with duplicate_of=D2, then D2 added. The entity row was (1, 'Note (lab)', 'document', doc_id=2), which is the retired D1. node_document returned 2, and get_page('lab').members listed doc_id 2 while D1 was retired. Fix: when a document is retired as a duplicate, move its nodes' doc_id to the keeper; otherwise re-pick through _node_doc, which prefers a live document. node_document could also fall back to a live namesake when the kept document is retired.

**Fix proposed.** In retire_document, move the doc_id of entities claimed by the retired document to duplicate_of when one is given, else to the live namesake by _node_doc's rule, else leave it. Also let _claim_node replace a doc_id that points at a retired document when the edge's live source_doc carries the node's name. A heal ailment can re-point the existing nodes that point at a retired document.

**Done.** `retire_document` moves the nodes that are the retired document (`entities.doc_id`) to the copy it is a duplicate of, else to the live namesake of the lowest id (`library._move_nodes_off`). Test: `test_document_nodes.py::test_a_retired_copy_hands_its_node_to_the_live_one`.

## 5. Undoing a rename failed when the old name had a variant label in another case or language

*medium*, data; `src/prax/store/graph/labels.py:624`

**Scenario.** _relabel, used by rename_entity, retitle, page renames and the heal pass's retitled-nodes repair, marks every label equal to the old name under COLLATE NOCASE and in any language as was=1, run=<run>. unmerge_run then sets every was row of the run to kind='pref'. Take an entity with 'Wavelet' (en, pref), 'Welle' (de, pref) and 'wavelet' (de, alt), renamed to 'Wavelet transform'. The undo tries to make 'wavelet' the German pref beside 'Welle' and breaks idx_entity_labels_pref. The unmerge rolls back whole, so one such entity makes the whole run impossible to undo. A heal round of hundreds of renames, or a page rename, can then never be taken back, though both are documented as undoable through unmerge_run. The marking also overwrites the run column of a label another producer wrote, and unmerge then sets it to NULL.

**Evidence.** Read labels.py:609-640 (_relabel) and 539-606 (unmerge_run), plus migration 0022 (UNIQUE (entity_id, lang) WHERE kind='pref'). Ran a throwaway test: added those three labels, called rename_entity(eid, 'Wavelet transform', run='rn-1'), then unmerge_run('rn-1'). After the rename, 'Wavelet' en and 'wavelet' de both had was=1, run='rn-1'. The undo raised sqlite3.IntegrityError: UNIQUE constraint failed: entity_labels.entity_id, entity_labels.lang.

**The skeptic.** Read labels.py:539-640. _relabel marks every label equal to the old name under COLLATE NOCASE, in any language, as was=1 with the run. unmerge_run then sets each of them to kind='pref' with no check against an existing pref in that language. I reproduced it (rep.py): the entity had 'Ada' en pref, 'Ada-de' de pref and 'ada' de alt. rename_entity(eid, 'Ada Lovelace', run='rn-1') marked both 'Ada' and 'ada' was=1 run=rn-1. unmerge_run('rn-1') raised sqlite3.IntegrityError: UNIQUE constraint failed: entity_labels.entity_id, entity_labels.lang, so the whole round cannot be undone. Fix: restore only the was-label that was the cached name or pref in its language, or demote the current pref in that language first (add_label's pref path does this). Keep the label's original run instead of overwriting it.

**Fix proposed.** In _relabel, mark only the label rows that were the entity's preferred name. Match the label exactly, case-sensitive, and only the rows of kind 'pref' or of the display language. Record what each row was (its kind and run) so unmerge_run puts back exactly that. In unmerge_run, demote any existing pref of that language first, as add_label does, instead of a bare UPDATE kind='pref'.

**Done.** `_relabel` marks only the preferred label that is the old name. Test: `test_review_2026_11_11.py::test_a_rename_is_undone_with_a_variant_of_its_old_name`.

## 6. A paid Claude call whose answer could not be used was never charged

*medium*, host; `src/prax/graph/extraction.py:601`

**Scenario.** Promote runs ClaudeExtractor with --spend. The call returns, but the answer cannot be used: a refusal (from_message raises 'refused'), or output cut off at max_tokens=8000 so json.loads fails. extract() called create(..., record=False) because the usage is meant to travel home in Extraction.usage. That object is never built, do_extract returns {'error': ...}, and Extract.take_in skips note_spend on an error result. The tokens were billed by Anthropic but no ledger row is written, and the budget under-counts. ClaudeAdjudicator.decide is the same: it adds usage to self.usage batch by batch with record=False. If a later batch raises (an API error after the SDK's retries), do_adjudicate raises before 'usage' is put into the payload, so the batches already paid for are lost too. The commit says every path records 'unless the caller carries its usage home', but these callers carry it home only on success.

**Evidence.** Read ClaudeRuntime.create (record flag), ClaudeExtractor.extract/from_message, steps/extract.do_extract (except Exception -> error result) and Extract.take_in (note_spend only after extraction_from succeeds). Also read resolution.ClaudeAdjudicator.decide and steps/graph.do_adjudicate. The code before this range had the same gap: the old extractor never recorded either.

**The skeptic.** Confirmed by reading the code. ClaudeExtractor.extract (src/prax/graph/extraction.py:599-602) calls create(..., record=False). from_message raises on stop_reason 'refusal', and json.loads raises on output cut off at max_tokens=8000, both before out.usage is set, so the usage of a billed response is lost. steps/extract.do_extract turns any Exception into {'error': ...} with no usage, and note_spend in take_in (line 94) only runs for a parsed extraction. ClaudeAdjudicator (resolution.py:457-461) adds to self.usage with record=False. do_adjudicate (steps/graph.py:358-364) puts usage into out only after judge.decide returns, so an exception in a later batch loses the batches already paid for. This gap existed before the range, but commit 920c651 claims that every paid chat is recorded. The effect is an under-count in the ledger and the budget, not wrong data. Fix: record the usage in create (or in a finally) before parsing, and carry it on the error result; or have do_adjudicate attach judge.usage in a finally.

**Fix proposed.** In ClaudeExtractor.extract, record the response's usage when from_message raises (try/except: usage.record(self.model, response.usage); raise). Or record=True always, with take_in no longer charging ex.usage. In ClaudeAdjudicator.decide, record each batch's usage on failure, or wrap the loop so that self.usage is recorded via usage.record before re-raising.

**Done.** `ClaudeExtractor.extract` records the call's usage when its answer cannot be read (it goes home with the error's post), and `ClaudeAdjudicator.decide` records the batches answered before a failure. Tests: `test_a_refused_extraction_is_paid_for`, `test_the_batches_before_an_adjudicators_failure_are_paid_for`.

## 7. An ask or surf bundle dropped the facts of a document that shares its title with another

*medium*, contracts; `src/prax/store/graph/context.py:66`

**Scenario.** Two documents have the same title, for example arXiv v1 and v2 of one paper (CLAUDE.md: "two versions of one title are one node"), or two documents both called "Introduction" or "Manual". Both documents' facts are written from the one shared node, but entities.doc_id can hold only one of them: whichever document's edge reached the node first. document_facts now keeps an edge only when `s.doc_id = x.source_doc`, so the other document gets an empty list. That list is what ask (ask.py:401) and surf (surf.py:328/517/838) put in their bundles. Before this change the test was `s.name = d.title`, so both documents got their facts. document_context and document_edges select the node by name (doc_node_ids), so a document's page still shows these facts while ask leaves them out.

**Evidence.** Ran a throwaway script on a tmp store (PRAX_DATA_DIR in the scratchpad, file facts.py). Two documents, both titled "Introduction". Linked `Introduction paper authored_by Bob` with source_doc=1 and `... authored_by Eve` with source_doc=2. Output: `store.document_facts(con,[1,2])` -> `{1: [{'rel': 'authored_by', 'name': 'Bob', ...}], 2: []}`. The entities table holds one row, (1, 'Introduction', 'paper', doc_id=1). Read the old SQL with `git show 2093c44:src/prax/store/graph/context.py`: it matched `s.name = d.title`.

**The skeptic.** Reproduced on a tmp store (scratchpad rv/r0.py): two documents titled 'Introduction', one authored_by edge each, from its own source_doc. document_facts([1,2]) returns {1:[Bob], 2:[]}. The entities table has one node, (1,'Introduction','paper',doc_id=1). document_edges(2) still returns the Eve edge, so ask/surf bundles and the document page now disagree. The old SQL (git show 2093c44) matched s.name = d.title, which gave both documents their facts. docs/log.md 2026-11-10 says namesakes still share one node and 'the column says which of them it is'. It lists the ask-bundle facts among the readers moved to the column and names no loss for namesakes, so this was not a deliberate trade. 1,820 live documents share a title (log), so the effect is wide. Fix: in document_facts, keep the edge when s.doc_id = x.source_doc OR the node is one of doc_node_ids (s.name equals the document's node name and the type is a document type). The source_doc filter already keeps each document's own facts.

**Fix proposed.** Keep the document's own edges by its node name, as doc_node_ids does, not by the one document claimed on the node. For example `AND (s.doc_id = x.source_doc OR s.id IN <doc_node_ids(con, doc)>)` per document, or join on the node name from node_name_of (that also covers a picture's `file (#n)` name, which the old title match missed). Add a test with two documents of one title.

**Done.** `document_facts` keeps the edges whose node is one of the document's (`doc_node_ids`), not only the node kept on that document. Test: `test_two_versions_of_one_title_keep_their_facts`.

## 8. Restoring twice a run that ends its own edges ended its restated facts for good

*low*, data; `src/prax/store/base.py:472`

**Scenario.** A project sync now writes its links under run 'links:<name>' and ends the ones it no longer wants under the same run (sync_facts end_run=run, capture/projects.py:377). _restore_run ends every live edge of the run, then restates each ended edge under its own run, which is that same run. The first POST /graph/restore for 'links:proj' ends the current links and brings back every link any past sync had dropped. A second call ends those restated edges (run = 'links:proj') and restates nothing, because restated_as is set, so the project is left with no live links until the next sync. 'Restoring twice' is not a no-op.

**Evidence.** Read base.py:463-529 and projects.py:349-380. Ran a throwaway test: sync_facts stated 'One', then a second sync stated 'Two' (ending 'One' under the run). The first restore_run returned {'retired': 1, 'restated': 1}, live = ['One']. The second returned {'retired': 1, 'restated': 0}, live = [].

**The skeptic.** Read base.py:463-529, edges.py:776-889 (_sync_facts records its endings under end_run) and projects.py:349-380 (end_run=run). I reproduced it (rep3.py) with run 'links:proj': a sync stated 'One' and a second sync stated 'Two'. The first restore_run returned {'retired': 1, 'restated': 1} with live ['One']. The second returned {'retired': 1, 'restated': 0} with live [], so the project had no links left. This is reachable through POST /graph/restore (api/curation.py:89). Low severity, because the next sync puts the links back. Fix: record a sync's endings under a separate run (for example '<run>:ended'), or make _restore_run skip ending edges that it restated itself.

**Fix proposed.** In _restore_run, do not end edges that are themselves restatements made by an earlier restore of the run (exclude ids in edge_endings.restated_as for that run), or give restated edges a restore run of their own. Better still, refuse a restore of a run that ends its own edges (a sync's standing run), since 'undoing' a run that spans many syncs has no meaning.

**Done.** `_restore_run` does not end what an undo of the same run stated again (`edge_endings.restated_as`). Test: `test_a_run_that_ends_its_own_edges_is_restored_twice`.

## 9. Retiring a retired document again overwrote the run its undo needs

*low*, data; `src/prax/store/documents/library.py:82`

**Scenario.** retire_document does not check whether the document is already retired. A second retire, for example POST /doc/{id}/retire on a retired document or a second pass that picks it, mints a new run, ends nothing (all edges are already ended) and replaces meta.retired with that run. unretire_document then restores the empty second run and returns edges: 0. The first run's endings still sit in edge_endings, but no record on the document names that run any more, so the facts its retirement ended are not restated.

**Evidence.** Read library.py:46-117. Ran a throwaway test: link one edge, retire_document twice, then unretire_document. It returned {'doc_id': 1, 'chunks': 1, 'edges': 0} with 0 live edges.

**The skeptic.** Read library.py:45-117. retire_document does not check meta.retired and always writes a new run into meta.retired. I reproduced it (rep.py): one edge, retire_document called twice, then unretire_document returned {'doc_id': 3, 'chunks': 1, 'edges': 0} with 0 live edges. The first run's endings are left orphaned. POST /doc/{id}/retire allows the repeat. Fix: refuse to retire a document that is already retired, or keep the existing meta.retired.run (or a list of runs) and restore every run on unretire.

**Fix proposed.** Refuse to retire a document that is already retired (ValueError), or keep the earlier run (do not overwrite meta.retired.run when one is held, or keep a list of runs and restore each).

**Done.** `retire_document` keeps the first retirement's run. Test: `test_unretire.py::test_a_second_retirement_keeps_the_run_the_undo_needs`.

## 10. A vector-only search of a small domain came back short

*low*, logic, contracts; `src/prax/store/retrieval/fusion.py:765`

**Scenario.** Before 8c7757b, search() multiplied fetch by 3 when a domain was given and filtered after the fusion. Now the multiplier is gone. The vec mode calls kept(_vec_search(con, vector, fetch + len(aside), kind)), and _vec_search has no domain filter, so the domain filter runs after the KNN on a list three times shallower than before. For a small domain (computing, 1.3% of the library, the case the commit measured), a vector-only search with domain= returns about 1% of the requested hits, often none, where it returned three times as many before. The commit's 'none come back short' was measured on hybrid only.

**Evidence.** Read git show 8c7757b. The hunk at fusion.py:147 removes 'if domain: fetch *= 3', and the vec-mode branch at fusion.py:762-765 passes fetch + len(aside) to _vec_search, then calls kept(). legs._vec_search (legs.py:220-262) applies only the kind filter. Not run: it needs an embedder and an index.

**The skeptic.** Confirmed by reading git show 8c7757b. The 'if domain: fetch *= 3' in search() was removed. In the mode=='vec' branch (fusion.py:762-765), kept(_vec_search(con, vector, fetch + len(aside), kind)) still applies the domain scope after the KNN, because _vec_search has no within parameter. So a vector-only search with domain= now draws a third as many candidates before the same post-filter, and comes back shorter than before for a small domain. The hybrid path is unaffected for the fused depth because its vector lists use depth, which is why the commit's measurement (hybrid) saw no shortfall. I did not run it, since it needs an embedder and an index. Fix: in vec mode with within set, draw more (restore the multiplier, or loop widening the KNN until enough in-scope hits).

**Fix proposed.** In _search_hits, when within is set and the domain's _share is small, draw the KNN deeper for the vec mode, as _vec_search does for a kind (want = limit * 25, capped at VEC_SEARCH_CAP), or restore the x3 for the vec mode only. Add a test in tests/test_domains.py that runs mode='vec' with a domain.

**Found again** by contracts (low).

**Done.** With a domain, `mode=vec` draws as deep as the fused lists (`depth`) before the domain filter. Test: `test_embeddings.py::test_a_small_domain_fills_a_vector_page`, which drew no hit at all before.

## 11. The title filter folded case for ASCII alone: "über" never found "Über"

*low*, logic; `src/prax/store/documents/readings.py:276`

**Scenario.** A title 'Über Klang' and a search for title='Über' (list_documents through the door's documents route, select_for_reading's title filter, and since 6a0b03c select_documents). Python's 'Über'.lower() gives 'über', but SQLite's lower('Über') stays 'Über' (it lowercases ASCII only), and LIKE folds ASCII only. The document is not found, and neither lowercase nor capital spelling of the query finds it. select_documents used a plain title LIKE '%Über%', which matched; the shared helper broke it there. list_documents and select_for_reading had the same bug before.

**Evidence.** Ran title_contains('Über') against an in-memory table holding 'Über Klang' with the repository venv: the result was []. The old select_documents form, title LIKE '%Über%', returned [(1,)].

**The skeptic.** Confirmed with a throwaway pytest on a tmp store. Document titled 'Über Klang': select_documents(title='Über') -> [], select_documents(title='über') -> [], select_documents(title='Klang') -> [1]. title_contains (readings.py:272-278) lowercases the query in Python ('über') but SQLite's lower() and LIKE fold only ASCII, and the store registers no custom lower function (no create_function in store/*.py). So a title whose non-ASCII letter is a capital never matches. Before 6a0b03c, select_documents used a plain title LIKE '%Über%', which matched the exact spelling. That is a regression there, while list_documents and select_for_reading already had the bug. Fix: compare against a Python-registered casefold function (con.create_function('pylower', 1, str.casefold)), or skip lowering in Python and rely on LIKE's ASCII folding.

**Fix proposed.** Do not lowercase in one engine and not the other. Either compare without lower() on either side, relying on LIKE's ASCII folding and keeping non-ASCII matches exact in case, or fold both sides in Python: register a casefold function on the connection, or match on a stored folded title.

**Done.** A connection `connect` makes has `prax_fold` (`str.casefold`), and `title_contains` compares words beyond ASCII casefolded on both sides; ASCII words keep the old SQL. Test: `test_a_title_filter_folds_case_beyond_ascii`.

## 12. The calibrated judges never sent a keyed server its key

*low*, host; `src/prax/models.py:589`

**Scenario.** A llama-server is bound on a LAN address with --api-key (roles.py does this when api_key_env is set). top_logprobs calls post_json(..., None), and the new models.served()/calibration.p_yes pass only (base_url, model), so the key cannot reach it. Every judge request gets HTTP 401 -> RuntimeError. LocalAdjudicator.probability catches RuntimeError and returns None ('asked again'), so each pass hands out the same likely pairs, does nothing and logs nothing. The world-dates judge and the genres step fail the same way.

**Evidence.** Read models.top_logprobs (key None), models.served (returns URL and model only), calibration.p_yes, resolution.LocalAdjudicator.probability (except (ServerNotReady, RuntimeError, OSError, ValueError): return None), and host/roles.py lines 267-279. This was already the case before the range; this refactor kept the chain without a key.

**The skeptic.** Confirmed. models.top_logprobs (models.py:589) calls post_json(..., None), so no Authorization header is ever sent. Its only callers are calibration.p_yes (line 138), worlddates.py:416 and steps/writing.py:656 by way of models.served(), which returns only (base_url, model), so the key cannot reach it. Against a server started with --api-key (roles.py:270-273 for a non-loopback host with api_key_env set), every judge request gets HTTP 401. post_json raises that as RuntimeError, and LocalAdjudicator.probability (resolution.py:620-621) catches RuntimeError and returns None ('asked again'), so the pairs are handed out again without end and nothing is logged. This predates the range, and the refactor kept the chain without a key. Fix: have served() return the runtime's key (or the runtime itself) and pass it through p_yes and top_logprobs to post_json.

**Fix proposed.** Have served() also return the runtime's key (OpenAIRuntime._key()), or pass api_key_env through to top_logprobs, and send it in post_json. Raise on a 401/403 instead of treating it as 'no answer'.

**Done.** `top_logprobs` sends the key of the configured server whose URL it asks (`models._server_key`). Test: `test_models.py::test_a_judge_asks_a_keyed_server_with_its_key`. No server on the live host takes a key.

## 13. Carrying readings over put a repeated equation's readings under its first occurrence

*low*, host; `src/prax/parsers/annotate.py:120`

**Scenario.** A paper shows the same display equation twice, e.g. a restated E = mc^2, and each copy has its own reading. A replacing read (marker, the parser again) runs carry. held is keyed by the normalised LaTeX, so the readings of both copies are pooled, one per model. In the new text they all go under the first copy and the second copy gets none. One model's reading of the second copy is dropped, and another model's reading of it moves under the first copy. The formulas pass then reads the second copy again.

**Evidence.** Ran annotate.carry with an old text holding '$$ E = m c^2 $$' twice (m1's reading under the first, m2's under the second) and a new text holding both copies spaced differently. Result: both readings under the first copy and none under the second. Running carry again on that result returned it unchanged.

**The skeptic.** Reproduced. The old text had '$$ E = m c^2 $$' twice, with m1's Formula reading under the first copy and m2's under the second. The new text had '$$E=mc^2$$' and '$$E = mc^2$$'. annotate.carry put both readings under the first copy and none under the second. The cause is that held is keyed by anchor_of (the collapsed LaTeX), the pool is filled across all copies, and held.pop(ref) is consumed at the first matching line of the new text (annotate.py:105-152). The same applies to a figure that appears twice under one hash. Formula carrying is new in this range. The harm is small: a reading moves to the wrong copy, a model's second reading is dropped, and the formulas pass reads the second copy again. Fix: key held by (anchor, occurrence index) and match the n-th occurrence in the new text to the n-th in the old, falling back to the pooled entry when the counts differ.

**Fix proposed.** Key held by (anchor, occurrence index) and use the same index for the new text's lines, falling back to the anchor alone when the counts differ.

**Done.** `annotate.carry` matches an anchor by its turn: the second occurrence in the old text is the second in the new one. Test: `test_a_repeated_equation_keeps_each_ones_reading`.

## 14. A stop between migration 69's commit and its claim of the nodes left the claim never run

*low*, contracts; `src/prax/store/base.py:1506`

**Scenario.** An old store is upgraded. Migration 69 runs in its own transaction and stamps user_version=69. _claim_document_nodes then runs as a separate step and commits afterwards. If the door is killed, the claim raises (an ontology load error, for example), or the disk is full in between, the next init_db starts with before=69. The condition `before < DOCUMENT_NODES` is then false, so the claim is skipped for good. Every node from before the migration keeps doc_id NULL. The readers that now use the column then give empty or wrong answers: a project page's members and a page's projects (_members, _project_edges), what a page annotates, search staleness, a context's cites doc_id, and ask facts. This lasts until someone runs `prax heal --check unlinked-document-nodes --apply` by hand, and that check is capped at CAP rows per run. A clean upgrade is fine, and a fresh host's claim is instant.

**Evidence.** Ran a throwaway script on a tmp store (file interrupt.py): initialised the store, registered 'Wavelets', inserted the entity, set doc_id back to NULL to reproduce the state a crash leaves, then called init_db again. Output: `version 69`, `after init_db: None`, `node_document: None`. Read init_db: `if before < DOCUMENT_NODES <= current: _claim_document_nodes(con); con.commit()`.

**The skeptic.** Read init_db (base.py ~1477-1508). Each migration commits with its own user_version stamp. `_claim_document_nodes(con); con.commit()` runs afterwards under `if before < DOCUMENT_NODES <= current`. If the process stops between the migration's commit and the claim's commit, the next start has before=69 and skips the claim for good. _claim_node only links a node lazily when a new edge reaches it. The repair (_unlinked_document_nodes) runs only when someone runs prax heal, and finds at most CAP rows per run. Fix: run the claim on every init_db while any document-type entity with doc_id NULL matches a document title (cheap with the index), or stamp completion in a meta row instead of tying it to user_version, or do the claim in Python inside the same transaction as the migration.

**Fix proposed.** Run the claim inside migration 69's transaction, so the version stamp and the claim commit together (for example, apply the claim before `PRAGMA user_version = 69; COMMIT`). Or record its completion in store_state and run it whenever that marker is missing. The claim already skips nodes that have a doc_id, so running it again is safe.

**Done.** A migration's Python half (`store.base.AFTER_MIGRATION`, migration 69's `_claim_document_nodes`) runs in the migration's own transaction, before `user_version` is stamped; a stop rolls back both. Test: `test_schema.py::test_a_migrations_python_half_commits_with_it`.

## 15. The limit-route test skipped `int | None`, and `/traverse` went unclamped

*low*, contracts; `tests/test_api.py:779`

**Scenario.** test_every_list_route_declares_and_clamps_its_limit flags a parameter only when `schema['type'] == 'integer'`. A parameter declared `limit: int | None` has an anyOf schema with no top-level 'type', so the test skips it. GET /traverse takes `limit: int | None = None` (api/graph.py:85) with no AtMost, and the test passes. Any later route with an optional limit would also pass unclamped. /traverse was not clamped before this change either, so this is not a regression. But the test says it holds every list route to a cap, and for this shape it can never fail.

**Evidence.** Read the test (lines 768-799) and the traverse route. Checked FastAPI's OpenAPI output for AtMost in a throwaway app (file t.py): a plain int gives {'type': 'integer', 'minimum': 1, 'maximum': N}. Clamping works: limit=999 -> 20, -3 -> 1, abc -> 422.

**The skeptic.** Checked with FastAPI in the repository venv (scratchpad rv/t3.py): `limit: int | None = None` gives the schema {'anyOf': [{'type':'integer'},{'type':'null'}]}, with no top-level 'type'. The test at tests/test_api.py:779 tests `s.get('type') == 'integer'`, so it skips that shape. /traverse (api/graph.py:85) passes the limit unclamped to traverse_map -> _walk, which uses it as given when it is not None, so /traverse?limit=10**6 is uncapped. The same shape is at pages.py:96 (GET /page/{slug}). Not a regression: /traverse was not clamped before either. But the test cannot fail for any route with an optional limit. Fix: also look inside anyOf for an integer member and require 'maximum' there, and declare /traverse's and /page's limits as Page | None (or clamp them).

**Fix proposed.** In the test, also look inside `anyOf` for an integer branch without a maximum. Give /traverse an explicit cap (AtMost, keeping 0 as the UI's 'no per-type cap' if that is intended), or list it in the test as deliberately open.

**Done.** The test reads inside `anyOf`. `/traverse`'s limit is clamped to 1,000 (`api.graph.TRAVERSE_MOST`); `None` is still the host's `graph.edges` and 0 now means the most rather than every edge (the largest hub on the live store has 13,440). The graph view asks with 0, so it draws at most 1,000 of a hub's edges, `left_out` counting the rest. `/page/{slug}`'s limit is a window of one page's text in characters, like `/doc`'s `max_chars`, and is listed as such. Test: `test_a_walk_answers_at_most_its_cap`.

## What the reviewers read and found sound

### data

Read base._end_edges, _restore_run and _move_references; retire_document, unretire_document and _join_duplicate; cleanup retire_set and restore_set; undo_version; correct_edge and _link with _claim_node; _node_doc and _claim_document_nodes; migration 69; node_document, doc_node_ids and the readers of entities.doc_id (fusion staleness, pages.tree); the heal ailments unlinked-document-nodes, page-node-twins and retitled-nodes (their locking through locks_itself or _serialized; merges under a signed run); merge_entities, unmerge_run, rename_entity, _relabel and retitle; canonical_entity, alias_group and with_aliases (same connection, reentrant guard); _update_meta, _put_meta and _set_meta, and the sensitivity writers (_set_sensitivity, mark_suspected, undo_media keep the column in step); step_tried and _due/STAMPS; TRIED_ON_TEXT clearing in index_text; sync_facts and its three callers (markup, citations, project sync), compared with the code they replaced (find_edges defer semantics kept, ours excluded). Checked and found sound: _end_edges ends only live edges and records endings idempotently; _move_references moves only live holders and keeps history; a restated derived edge gets its premises copied before the premises are moved; restoring a run whose ended edge is live again by other means does not double it; update_meta does read-modify-write in one serialized call and commits only when the meta changed; the two in-store update_meta calls do not split a larger write. I wrote and ran five throwaway tests against tmp stores under the scratchpad (C:\Users\lodsb\AppData\Local\Temp\claude\6641ca86885383bb\1433c6e5-cbdc-4514-92f9-acfbb0e5cb78\scratchpad\rev\test_r1.py to test_r5.py). I did not open the live database or call the door, and changed nothing in the repository.

### wall

Read and checked these, and found them sound apart from the two findings:
- doc_visible and visible_edges at every site finding 39 switched to them (_walk, senses, _mark_disputed, _visible_ids which now also requires an asserted edge, labels_of_entities, _open_label/_said_openly, person_page/_own_facts/_people, region_map, the path index's connect_entities, subgraph_edges, edge_hidden, tree._seen_project_edges/_members). The None and int handling is the same as before.
- The premise check now skips a row whose producer is present and does not start with 'rule:'. That is safe: only rules.py writes edge_premises, and _move_references keeps the producer when it restates an edge.
- The path index excludes rule edges.
- why returns an empty answer for a hidden edge.
- changes keeps its premise filter for derived edges.
- Hub graphs put the hidden set into the cache key's SQL arguments.
- node_document hides a hidden document. On the readers moved to entities.doc_id (document_facts, context cites/members/_linked_documents under @_scrubbed, _members, _annotated with the document_hidden filter in _lifecycle, staleness replaced_by checked against hidden, the briefing nodes in _rows) I found no new way to name a hidden title. A node whose doc_id is NULL shows only a name that a visible document states.
- _SHARES only chooses a plan and returns no counts.
- The domain filter inside the ranking: the aside/hidden set is still removed from every list before fusion (kept()), in the wide branch and in fts mode.
- NotFound and the ValueError handler: _node_name raises NotFound for a missing and a hidden document alike. The only plain KeyError raises left are in zotero.py and vectors.py, which no wall route reaches.
- AtMost clamps.
- ReadingSelection.chosen/selection are equivalent to the old fifteen-field test.
- auth.READER_ROUTES and CONTRIBUTOR_ROUTES are unchanged, and no route was added (JOBS adds none).
- run_as_job now copies the context, so a contributor's page-save question job runs as that viewer, which is tighter than before.
- A reader's ask: _reader_may_not_pay goes through the same models.spec that answerer_named uses. ClaudeRuntime.params skips an empty system prompt.
- paid_refusal keeps the old spend rules for each step.
- The vision Claude branch has the same faces exposure as before (not changed).
- Single writer: no new SQLite access outside prax.store.

Both findings were reproduced with scripts in the scratchpad (C:\Users\lodsb\AppData\Local\Temp\claude\6641ca86885383bb\1433c6e5-cbdc-4514-92f9-acfbb0e5cb78\scratchpad\rev\hidden_stale3.py and unretire.py), each against a temporary store.

### logic

I checked old against new for each refactor in my area, using git show and, where useful, scripts under %TEMP% against a tmp store or in memory. The live store and the door were never touched.

**STAMPS, _due and _of_this_text (8e0fa37).** The new SQL keeps the documents the old queries kept, and the ? counts per call site still match:
- photos, faces, geocode: coalesce(v, 0) < ? and its NOT in the counts.
- sections and worlddates: IS NOT text_hash, the same as the old IS NULL OR != because text_hash IS NOT NULL is required.
- quality: the coalesce '' form, the same given a non-null text_hash.
- tried marks: unchanged.

current_finding dropped a coalesce, which only matters where findings exist without quality.text_hash. thin_documents' NOT(...) still keeps documents never checked. test_step_stamps can fail.

**Selectors (6a0b03c).** LIKE escaping is right. DOCTYPES used as written in select_for_reading (alias d) is fine.

**search(domain=) (8c7757b).** _in_scopes ANDs a sense's scope with the domain filter, wide is decided over both, and every fused list goes through kept(). An unknown domain is refused. DOMAIN_PRIOR is harmless.

**canonical_entity, alias_group, with_aliases, _named (5853c60).** These rely on merge_entities flattening canonical ids, which it does. The replaced helpers (imports, people, pictures, senses, _entity_ids, _sides, entity_answering, keep_printed, refuse_faces, face_matches, decide_foreign) are equivalent. document_edges' 'own' now comes from doc_node_ids.

**config.flag.** Empty environment variables are still ignored, YAML bools and 0/1 are read, and an unknown word raises a ConfigError.

**run_name, ago, moment.** Nothing compares or sorts run names. candidate_runs compares timestamps, not names.

**DisjointSet and carry_ids.** Union order is kept in same_answers and the clustering. Communities are now per level and largest first, events need jaccard 1.0, which is an exact match, and face groups are unchanged.

**sure_labels.** A missing or null p counts, and the old 0.5 thresholds are kept.

**reference_name.** Bare numbers were already dropped before; the bracketed rule is new and intended.

**domains._matched.** Preview and assignment now select alike. Rows are fetched before any write.

**Path index stamp.** It is now _graph_stamp, which moves on merges.

**Status reader.** Front matter with an unknown status: word plus superseded_by still comes out superseded. retracted is stale and maps to invalidates, and staleness reads it through STALE_STATES.

**ask, gather, surf.** domain is passed through to every search; _reask is equivalent to the old page and block re-asks.

**sync_facts (91e187d).** The fact key, the ending of stale edges with end_run, and the defer check against ours_all keep what the markup, references and project-link passes did before.

**Memo for the hidden set.** The key is unchanged.

**_restore_run and _move_references.** Correct for a single retirement's own run; the clean-up's shared run is the high finding.

### host

Read the diffs from 2093c44 to HEAD for models.py (ClaudeRuntime.params/create/chat, claude_usage, ContextTooLong, post_json, served, STEPS derived from STEP_DEFAULTS; checked by script that the set of steps is unchanged), and every Claude call site: the extractor (same request dict, effort, cached system prompt and schema; record=False with usage charged in take_in), the adjudicator (effort low, schema, a client with a timeout, cache-write tokens now counted), vision (one chat with the picture, recorded), and ask's ClaudeAnswerer. Followed the usage bag (thread-local, taken by Pass.post and by do_parse per document) and the door's take_in 'spent' -> note_spend. I found no double charge on the Claude paths: extract and adjudicate say record=False, parse readings take per document, and promote's vision reading is taken by p.post. paid_refusal with spend_allowed matches the old per-step wording and policy (promote, adjudicate, readings asked for with --spend; the others never). pipeline.free = not spec.paid. ContextTooLong: the same regex as the old _EXCEEDS, one shorter retry in LocalExtractor and LocalAnswerer, then it propagates, so no infinite retry. It subclasses RuntimeError, so older catches still hold. calibration.p_yes/Platt.of/each are the same computation as before, and Platt.of also reads [a, b]. sessions.session: providers, threads and arena kept for the embedder; the labeller is still on the CPU and now reads embeddings.threads; the faces stay on the CPU with the default arena; the reranker now has the shrink arena and its run options. IndexFile and vectors: _add_and_record matches the old SQL for chunks and documents. _reconcile keeps the old 'no files: skip' for the text indexes and the faces' 'no file: all lost'. The face dimension is read from the file name. Compaction of the document and face indexes cannot loop: retire deletes the documents_fts and document_embeddings rows, and the facevectors due query needs gone_at IS NULL. The swap under _INDEX_LOCK is unchanged. Supervisor: each transition method reproduces the set edits it replaced, in the same order, including restart (release, then restart_now, then end) and the swap/unswap paths. The --restart command path is unchanged. api.passes.JOBS: the clock starters are the same six as the old dict and match schedule.NAMES. start_questions keeps the one-job-per-key guard and its release on every path. run_as_job now copies the caller's context, so a contributor's page save runs its ask block as that viewer, which narrows what the answer is drawn from rather than leaking. Step registries: _HOMES and CORE_STEPS are unchanged as sets; SERVER_READINGS now also covers pack readings (none exist today). parsers.annotate: place and nearby keep the old behaviour, now also skipping formula readings as intended; carry is idempotent when re-run, checked by running it.

### contracts

Checked AtMost and Page in a throwaway FastAPI app (clamps 999->max and -3->1, rejects 'abc' with 422, writes min/max into OpenAPI) and compared each route's old hand clamp with its new AtMost cap (spending 200, ask 20, cleanup 200, references, cited 200, face similar 60, communities map/members 120, events 100, versions VERSIONS_PAGE, places 500); every default is kept. Checked the clients' limits against the new caps (UI, MCP, CLI, feed importer, scripts); none needs more than the cap, and no client sent limit=0 to a capped route. Every door route the MCP tools call (33) exists with its method in app.openapi(); the tools are still one-call proxies. The ValueError/NotFound exception handlers: the KeyErrors that non-store callees used to raise (packs.by_name, parsers.by_name, routes_for, maths) are now NotFound, and the routes that still mean 404/409 catch it themselves. The Jobs view: passes.JOBS gives the same seven entries with the same start bodies and 'where', and MAINTENANCE holds the same names. config.flag keys: empty env values fall back to the default, and the keys are documented in prax.example.yaml, architecture.md and howto.md. Public store surface: scanned src, scripts, clients and tests for store.<name>, `from prax.store import` and setattr/getattr strings. Only names that are guarded with hasattr (focus_entities) or that appear in docstrings are missing; test_invariants and test_document_nodes pass (23 passed). Migration 69: fast on a fresh host, idempotent claim, partial index entities_by_doc used for `=` and IN. Heal: unlinked-document-nodes and page-node-twins are registered ailments, and the generic CLI `prax heal --check` reaches them. Response shapes: traverse (visible_edges keeps the walk's rows and adds the premise rule; _visible_ids now skips rule edges, which only tightens the wall), get_page members (same keys, PROJECT_MEMBERS cap kept), context (same keys; doc_node_ids keeps the old match by title, cites' doc_id comes from the column and is still scrubbed), document_edges, subgraph_edges, the staleness entry shape, the briefing and doc_link markup, changes (rename only). title_contains and starts_with match the old LIKE semantics, case-insensitive.

### tests

Read the range's commit list and the test and doc diff stats. Checked tests/test_step_stamps.py in full: _due and _of_this_text against the SQL they replaced (photos, faces, geocode, sections, quality, worlddates, the tried marks). quality's `COALESCE(x,'') != text_hash` becomes `x IS NOT text_hash` under `text_hash IS NOT NULL`, which is equivalent. sections' `IS NULL OR !=` becomes `IS NOT`, also under `text_hash IS NOT NULL`. current_finding's `NOT (...)` in thin_documents still evaluates to false/true when there is no quality record. tests/test_unretire.py: run names are unique per retirement (run_name unique=True), so the double retire/unretire does not depend on timing. The premise, conflict and import_held moves are asserted both ways. tests/test_document_nodes.py: the claim-on-first-edge rule, the upgrade fill and the two heal ailments. test_status.py additions use a fixed HERE. 'retracted' reaches fusion through status.STALE. test_invariants.py: the store ORDER lists match CLAUDE.md for graph (citations, schemaorg) and repair (cleanup). An AST scan of all store functions that commit outside _serialized found only the pre-existing ones (_committing, _drop_legacy_vec_table, maintain._proposes, _repair_stray_versions which is exempted, vectors._forget), so nothing in the range escapes the lock test. The live-spelling and public-name tests are sound. Test diffs that use threads or clocks were checked: test_api's job and questions tests wait on Events with bounded loops, test_domains patches time.monotonic for SHARE_REFRESH, test_embeddings fakes onnxruntime and needs no model files, test_extraction fakes urlopen. None touch data/, C:/prax-data or the network. CLAUDE.md invariants 3 and 8 and the doc_id convention name functions that exist (_claim_node, node_document, document_node, _changed_between). Checked the docs/architecture.md and docs/meta.md diffs, and the PLAN.md [x] items against the log entries for findings 1, 2 and 17. Did not run the full suite or open the live store.
