# The quality review of 2026-10-18

The project's skill `.claude/skills/code-review` in full mode, over
everything changed in `src` since `72710d3` (2026-10-05 to -18, about
100 commits, after the safety review's fixes of the same day): five
reviewers, one a dimension (hygiene, extensibility against the plan,
independence from the owner's library, conceptual generality,
performance on the target), each finding re-checked by a skeptic. 23
confirmed, 5 rejected. Several are one problem seen twice (2 and 8; 3,
12, 16 and 21; 6 and 14; 7 and 16). The user agreed on 2026-10-18 to fix
1, 2, 3, 5, 6, 7 and 9 with what they share, and to plan the rest
(`docs/PLAN.md`, "The quality review of 2026-10-18"). Each finding
below says which. The tests are in
`tests/test_review_quality_2026_10_18.py`, numbered as here.

## 1. Repairs that end edges commit twice per edge while holding the store's write lock: duplicate-facts' 1,711 edges held it 11.4 s

high cost, performance, fixed: `store.end_edges`, one commit per 500 edges. `src/prax/store/repair/common.py:91`.

**Evidence.** On a test store built by the real migrations (400k edges, store.connect, synchronous=FULL), _serialized(common._invalidate)(con, 1711 ids, run='heal:duplicate-facts') took 11.35 s (6.6 ms per edge), and the store logged 'held the write lock 11.4 s'. 1,711 is the count the log gives for the duplicate-facts check. invalidate_edge commits (edges.py:528) and record_ending commits again, once per edge. heal wraps the whole repair in _serialized (ailments.py:722). The same 1,711 ends written as two executemany statements with one commit took 222 ms. end_edge (edges.py, new) has the same per-edge commit and is used by worlddates.apply.

**Why here.** The lock checklist item and invariant 4. Every door writer waits behind that lock: worker results, capture, the inbox. Other processes have a 30 s busy timeout. On the Pi's SD or eMMC an fsync costs several times what the desktop SSD does, so the same heal can hold the lock for a minute or more. The new duplicate-facts, not-venues and backwards-part-of repairs all go through _invalidate.

**Fix** (an hour). Add a batched end_edges(con, ids, run, corrected_by=None): one executemany UPDATE ... WHERE valid_to IS NULL, then one INSERT OR IGNORE INTO edge_endings, committing every ~500 rows, and have _invalidate and the new repairs use it. Commit-per-edge stays only where each row must be visible on its own.

**Checked by the skeptic.** Confirmed. duplicate-facts does not set locks_itself (ailments.py:261-272), so heal wraps the whole repair in _serialized (ailments.py:721-722). _invalidate (common.py:91) calls invalidate_edge, which commits at edges.py:528, and then record_ending, which commits again at edges.py:628, once per edge. store.connect sets no synchronous pragma, so it runs at FULL and every commit fsyncs the WAL. The log gives 2,018 edges for this check (1,711 facts). I re-ran the pattern on a WAL database, 2,000 updates plus 2,000 inserts: with a commit after each statement it took 12.1 s, and with executemany and one commit it took 6 ms. That matches the reported 11.4 s lock hold. All other door writers wait behind that lock, and fsync is slower on the Pi's eMMC or SD card, so the hold gets longer there. end_edge (edges.py:472) also commits once per edge.

## 2. Five arXiv-id grammars, and only the new one normalises: reference matching by arXiv id misses versioned citations and old-style ids

medium cost, hygiene, fixed: `prax.text.arxiv`, one grammar for every reader of an id. `src/prax/text/references.py:40`.

**Evidence.** The range added `latexsrc.arxiv_of` (src/prax/parsers/latexsrc.py:55,74), which takes new and old ids and drops the version. Four other grammars live elsewhere: `inbox._ARXIV_URL` (capture/inbox.py:258, old style `[a-z][a-z.\-]*/\d{7}`, version dropped), `dates._ARXIV_NEW/_OLD` (text/dates.py:106-107), `references._ARXIV` (text/references.py:40, new style only, version *kept* inside the group), and `reads._work_key` (store/documents/reads.py:1176, `str(data['arxiv']).split('v')[0]`). maintain.py:335 lowercases the library's meta.arxiv but does not strip a version. I ran these in the venv: `references.parse('[3] A. Smith. Deep things. arXiv:2501.13484v2, 2025.').arxiv` gives '2501.13484v2'; `inbox.url_ids('https://arxiv.org/abs/2501.13484v2')` gives '2501.13484'; `references.parse('... arXiv:hep-th/9901001 ...').arxiv` gives None; `'solv-int/9901001'.split('v')[0]` gives 'sol'.

**Why here.** The `references` pass of `prax maintain` (maintain.py:383, an exact `ref.arxiv in by_arxiv` lookup) and `cited_but_missing`'s work key depend on these ids matching. A citation that prints its version never reaches the library copy captured from an arXiv URL, and old-style ids are missed or mangled. The id is also now load-bearing for choose_reader's latex-source reading. A fifth reader of ids will add a fifth grammar unless one place owns it.

**Fix** (a few hours). Put one arXiv-id grammar in `prax.text` (it imports nothing of prax, so the importers, parsers and store can all use it): `find`, `canonical` (version dropped, lowercased) and the URL form. Make references, inbox.url_ids, dates.from_arxiv, latexsrc, `_work_key` and maintain's library index all use it. Add a test that the versioned and old-style ids match. A few hours. The next `references` pass relinks what was missed.

**Checked by the skeptic.** Reproduced it in the venv. references.parse('... arXiv:2501.13484v2 ...').arxiv gives '2501.13484v2'. inbox.url_ids on the same URL with v2 gives '2501.13484'. An old-style 'arXiv:hep-th/9901001' parses to None. maintain.py:383 looks the id up exactly in by_arxiv (meta.arxiv lowercased, version kept), so a citation that prints its version never links to the copy captured from the abs URL. reads.py:1176 has split('v')[0], which mangles old ids that contain a 'v' ('solv-int/…' becomes 'sol'). The range added latexsrc.arxiv_of with the right grammar but left the others as they were: five grammars that disagree, on a path the references pass and choose_reader depend on.

## 3. A finding's fix is an encoded string ('marker-ocr'), decoded separately in four places in two languages, and the reader choice still checks its own conditions beside the findings

medium cost, plan, fixed: `quality.Fix`, the host's mode chosen by `quality.reading`, the `no-layer` finding. `src/prax/parsers/quality.py:54`.

**Evidence.** Read quality.py, capture/pipeline.py:172-291, api/process.py:399-406, store/documents/readings.py:361-377 and ui/view-admin.js:43-47. `Finding.fix` is a bare string. 'marker-ocr' means extractor marker plus an OCR mode, and each reader decodes that its own way. `quality.FIXING` = {marker, latex-source, figure-crops} leaves it out. readings.py matches it with a `LIKE fixed_by || '-%'` prefix convention. pipeline.choose_reader turns it into `_marker_mode(scan)`, which follows the host's `parse.marker_mode` (fast or balanced). The Admin page's CHECK_FIX hard-codes `mode: 'fast-ocr'`. So on a host set to balanced, the door asks for balanced-ocr while the 'text checks' button asks for fast-ocr for the same finding. 'latex-source' needs its mode from `detail.arxiv` and has no button at all. choose_reader also keeps a scan rule of its own, a second `SCANNED = 0.5` (pipeline.py:172 beside quality.py:56) for a scan with no text layer. No check emits a finding for that case, so the bulk button never reaches those documents.

**Why here.** AO step 5 ("follow_ups asks for the fix a finding names instead of its own conditions") and AO step 1's "First checks": more checks are planned. AJ's open "routing as rules" item (a two-column or German page to marker) is another branch in the same choose_reader. Each new check whose fix has a mode, or each new routing rule, has to be added in quality.py, pipeline.py, readings.py, process.py and view-admin.js and kept in step between them.

**Fix** (an hour or two, plus one quality pass (a VERSION bump)). Make a fix a structured reading on the Finding, `{extractor, mode}`, with the mode resolved where the finding is made (for marker-ocr through the same `_marker_mode` helper; for latex-source the arXiv id). Store it like that in meta.quality, bumping VERSION, which re-checks for free. FIXING, the LIKE match and CHECK_FIX then read it instead of decoding it. Turn the no-text-layer scan into an `ocr-layer` (or `no-layer`) finding so choose_reader acts only on findings and the second SCANNED goes away.

**Checked by the skeptic.** Confirmed. quality.py:89 Finding.fix is a bare string. FIXING (quality.py:54) leaves out 'marker-ocr'. readings.py:370-376 matches it with the `fixed_by + '-'` LIKE prefix. pipeline.py:189-198 builds the mode from parse.marker_mode, but view-admin.js:45 hard-codes mode 'fast-ocr', so on a host set to balanced the door and the Admin button ask for different modes for the same finding. pipeline.py:172 repeats SCANNED=0.5 from quality.py:56. The ocr-layer check at quality.py:185 needs prof.get('layer'), while choose_reader:284-287 adds its own rule for a scan with no layer, which no finding names, so the bulk fix never reaches those documents. A new check whose fix has a mode, or a new AJ routing rule, means edits in four or five places.

## 4. The PDF profile behind the checks is never kept, so every change to the checks, and every text that lands, re-reads and re-profiles the original

medium cost, plan, planned, `docs/PLAN.md`. `src/prax/store/maintain.py:1001`.

**Evidence.** Read maintain._quality (984-1035) and pipeline.assess (201-233). The pass selects every document whose `meta.quality.version < quality.VERSION`. For each PDF it reads the original's bytes and runs `pdf.profile`, plus `latexsrc.arxiv_id_of` on the same bytes. Its own docstring says that took 38 minutes for the library's 11,493 PDFs on the desktop. VERSION went from 1 to 4 on 2026-10-18 alone (the comment at quality.py:41-43), and every bump redoes the whole run. The profile depends only on the original's bytes, which are content-addressed and never change, yet neither meta.quality (quality_record, meta.py:716) nor anything else stores it. pipeline.assess also re-profiles on every landing, including the 'same' outcome of figure-crops and formulas (pipeline.py:315-320). The same read, profile, check and record sequence is written twice: once in the door's assess and once in the nightly pass.

**Why here.** AO: 'First checks' means more checks and more VERSION bumps. Invariant 7 and the deployment shape put the door, and so `prax maintain`, on the Pi-class board. AQ (an original that stays where it is, such as video or audio on the NAS) makes 'read the original again' a network read of a file that may not be reachable. A pass that redoes a fixed per-original computation on every bump gets slower in exactly the direction the plan is heading.

**Fix** (an hour or two, plus one profiling pass). Keep the profile, keyed by the original's hash, in `meta.profile` with its own version (`pdf.PROFILE_VERSION`), and have the checks read it. A check-only bump then reads text artifacts and nothing else. Make one function the single 'assess one document' used by both assess and _quality, so a new input to the checks (a non-PDF profile, a referenced original) is added once.

**Checked by the skeptic.** Confirmed. maintain._quality (984-1035) selects every document with quality.version < VERSION and, for each PDF, reads the original's bytes and runs profile_of (pdf.profile plus arxiv_id_of). Its docstring gives 38 minutes for 11,493 PDFs on the desktop. VERSION is at 4 after bumps on one day (quality.py:41-43). quality_record (meta.py:716) keeps no profile, though it depends only on content-addressed bytes. pipeline.assess re-profiles on every landing, including the 'same' outcome at pipeline.py:315-320. Each bump redoes a fixed per-original computation on the Pi-class door, and with AQ that becomes a read over the network. The duplication between assess and _quality is minor: the logic itself is shared through quality.check.

## 5. mended_name strips a type the model wrote into a name only for research/core types; studio, kitchen and computing names keep 'device=…'

medium cost, independence, fixed: the type marks built from the composed ontology. `src/prax/store/repair/graph.py:897`.

**Evidence.** _TYPE_HEAD/_TYPE_TAIL hard-code `concept|method|tool|target|type|dataset`. I ran store.mended_name with the venv: 'concept=social networks' gives 'social networks' and 'method: FFT' gives 'FFT', but 'device=Minimoog' (device), 'ingredient=apple' (ingredient) and 'Minimoog device=synth' come back unchanged. mended_name runs on every triple in extraction (graph/extraction.py:547,550) and in the heal check `_damaged_names` (repair/graph.py:946). That check only lists names that mended_name would change, so it never surfaces these, even though _DAMAGE_HINT matches their '='. retrieval/query.py also drops any label holding '=' from query expansion, so such an entity can't be searched by that label.

**Why here.** The research pack's types are baked into a core repair (checklist: a domain's words outside its pack). A studio-, kitchen- or computing-heavy library quietly collects 'device=X' / 'ingredient=Y' entities that no pass mends and no heal check reports. They also stay apart from the clean 'X', which splits the graph's hubs.

**Fix** (an hour). Build the alternation from ontology.load().entity_types plus the wire keys ('type', 'target'), cached per composed version, and add a test that uses a studio and a kitchen type.

**Checked by the skeptic.** Confirmed by re-running store.mended_name: 'concept=social networks' becomes 'social networks' and 'tool=Ableton' becomes 'Ableton', but 'device=Minimoog' and 'ingredient=apple' come back unchanged. _TYPE_HEAD/_TYPE_TAIL (repair/graph.py:897-903, added in this range) list research/core type names only. The damage comes from the line format's own 'src_type=<type>' field, and that field is the same for every pack's types, so other packs' names get it the same way. The source to use is onto.entity_types, not a literal list. _damaged_names only reports names that mended_name changes, so the heal check stays silent on these. Nothing measures how often it happens outside research, so medium, not high.

## 6. world-dates hands out German and French sentences but its guards against date-names and stand-ins are English-only

medium cost, independence, fixed: `world_time:`'s stand-ins and period words in the lexicon. `src/prax/graph/worlddates.py:53`.

**Evidence.** The lexicon's world_time cues cover EN/DE/FR (ontology/lexicon.yaml:367-369), so worlddates.sentences() picks both 'Die Firma wurde 1975 … gegründet …' and "L'entreprise a été fondée en 1975 …" (I ran it). The filters at lines 53-61 are in code and in English. _STAND_IN matches 'unknown' and 'the company' but not 'unbekannt', 'inconnu' or 'die Firma'. _DATE_NAME matches 'the mid-1980s' but not 'Mitte der 1980er', 'Anfang 1990', 'début 1990' or 'les années 1980' (it does match 'Juni 1977' and 'juin 1977', through dates.month_names). tests/test_world_dates.py has no German or French case. The extraction prompt says names are written as printed, so a German document gives German stand-ins.

**Why here.** AL step 5 writes these as dated EXTRACTED edges (it ends an undated edge and restates it). On a German or French document a fact whose other end is 'unbekannt' or 'Mitte der 1980er' gets through the guards. The 0.9 judge checks that the date is supported, not that the end is a real entity, so the wrong edge goes in quietly. The guards miss the very languages the cues were added for.

**Fix** (an hour). Move the stand-in words and the decade/period prefixes (early/mid/late, Anfang/Mitte/Ende, début/milieu/fin, '1980er', 'années') into the lexicon's world_time section next to the cues, read them like Cues, and add a German and a French test.

**Checked by the skeptic.** Confirmed. worlddates.py (new in 0a42488) checks both ends with _DATE_NAME/_STAND_IN, and I ran them: 'unbekannt', 'inconnu', 'die Firma', 'Mitte der 1980er', 'Anfang 1990', 'début 1990' and 'les années 1980' all pass. Only the month names are multilingual ('Juni 1977' is caught). extraction._drop_reason/_placeholder reads lexicon not_a_name, which is also English only ('unbekannt' -> False). check_names only looks at part_of. The world_time cues include DE/FR stems, so sentences in those languages are handed out, and apply() then writes the edge and ends the undated one. The guard words belong in the lexicon per language, like the cues next to them.

## 7. "The readers that write the maths as LaTeX" is three separate lists, and the one that asks for formula readings leaves out latex2html

medium cost, generality, fixed: the formula readings follow `quality.writes_latex`. `src/prax/capture/pipeline.py:363`.

**Evidence.** quality.py:48 has LATEX_READERS = ("marker/", "latex-source/", "latex2html/"). ui/view-doc.js:216 has MATHS_READERS = ["marker", "latex-source", "latex2html"]. pipeline.follow_ups writes its own literal, stamp.startswith(("marker/", "latex-source/")), before it asks for the `formulas` reading. latex2html.py:15-16 says a display equation becomes a `$$…$$` line, which is a `formula` chunk, and inbox.py:499 runs follow_ups for a sender's text stamped latex2html/2. So a latex2html book's display equations are never asked for their formula readings, while a paper from `prax import latex` (stamped latex-source/) gets them. Nothing in the code or its comments says latex2html is left out on purpose. The other literals in follow_ups (lines 260, 310, 356) are a fourth copy of the stamp prefixes.

**Why here.** The second case is real and arrived in this range. latex2html was added (5e5b1db, 777e884), and it had to be added by hand to quality.py and to the UI, and was missed in the pipeline. The next reader that writes LaTeX (the plan's other OCR readers under AJ) will meet the same three-way edit.

**Fix** (an hour). Keep one tuple in prax.parsers.quality (or in the readers' manifests) and use it from pipeline.follow_ups for the formulas and crops decisions. Have the UI read the same set from the door, or at least cite the Python constant. Then decide in one place whether latex2html texts get formula readings.

**Checked by the skeptic.** Confirmed. pipeline.py:363 tests stamp.startswith(("marker/", "latex-source/")) before it asks for `formulas`. quality.py:48 LATEX_READERS includes latex2html/, and so does view-doc.js:216. importers/latex2html.py's docstring says a display equation becomes a `$$` formula chunk stamped latex2html/<rev>. The formulas extractor accepts text/ and xhtml (parsers/__init__.py:313), and inbox.py:499 runs follow_ups after a sender's text lands. So a latex2html page's formulas are never asked for automatically; only the heal ailment catches them. Nothing marks this as deliberate. pipeline already imports quality and could use LATEX_READERS.

## 8. Four ways to read an arXiv id; the new canonical form (no version) is not the one references or the library index use

medium cost, generality, fixed with 2. `src/prax/parsers/latexsrc.py:74`.

**Evidence.** Four separate patterns read an arXiv id: latexsrc.ARXIV_ID/_STAMP/_BARE and arxiv_of (new in this range, which drops the version), inbox._ARXIV_URL (drops the version, and its old-style pattern differs), dates._ARXIV_NEW/_OLD, and references._ARXIV (new-style only, keeps the version). I ran references.parse on '[3] ... arXiv:2501.13484v2' and got '2501.13484v2'; on 'arXiv:math.GT/0309136' I got None. inbox.url_ids('https://arxiv.org/abs/2501.13484v2') gives '2501.13484'. maintain.link_references keys the library by meta.arxiv as stored (maintain.py:335) and looks up ref.arxiv exactly (maintain.py:383). So a versioned citation of a paper captured from its arXiv URL misses the id tier and falls back to title matching, and an old-style id is never matched at all. reads._work_key (reads.py:1176) normalises with .split("v")[0], which cuts 'solv-int/9901001' down to 'sol'.

**Why here.** This range added latexsrc.arxiv_of as the form the latex-source reading takes, which is a fourth rule for one concept, and it was not moved under references, inbox or the citation index. The second case is real: citations, captured URLs, published dates and the reader choice all read the same id.

**Fix** (an hour plus a references re-run). Put one `arxiv_id(text) -> str | None` (canonical, without version, both styles) in prax.text (it imports nothing of prax) and use it in references.parse, inbox.url_ids/paper_meta, dates.from_arxiv, latexsrc and the by_arxiv index and _work_key. Afterwards, re-run the references pass to measure the links gained.

**Checked by the skeptic.** Re-ran it with the venv. references.parse('...arXiv:2501.13484v2...').arxiv gives '2501.13484v2', and the old-style 'arXiv:math.GT/0309136' gives None. inbox.url_ids on the /abs/...v2 URL gives '2501.13484'. 'solv-int/9901001'.split('v')[0] gives 'sol' (reads.py:1176). maintain keys by_arxiv on meta.arxiv as stored and looks up ref.arxiv exactly (lines 335, 383), so a versioned citation misses the id tier. Four patterns read one concept, and latexsrc.arxiv_of added a fifth form in this range without being reused.

## 9. document_fingerprint scans the whole edges table every 10 s for each open document page, under the shared connection's lock

medium cost, performance, fixed: the live facts only, served by migration 16's index. `src/prax/store/documents/reads.py:408`.

**Evidence.** EXPLAIN QUERY PLAN for 'SELECT count(*), max(id), max(valid_to) FROM edges WHERE source_doc = ?' returns 'SCAN edges'. idx_edges_source_doc is partial (WHERE valid_to IS NULL, migration 16), so a query with no valid_to condition cannot use it. On the 400k-edge test store, document_fingerprint took 22 ms median (29 ms max), warm, on the desktop. The live-only form ('... AND valid_to IS NULL') uses the index and took 0.11 ms. The docstring says 'Five lookups on indexes'. GET /changes?doc= calls it inside request.app.state.con_lock (api/work.py:244), and boot.js polls every 10 s.

**Why here.** Per-request work that grows with the library: edges are append-only, so ended edges are never deleted and the scan only gets longer. On a Pi-class CPU it is roughly 100 ms per poll per open tab, and it serialises every other /changes caller behind con_lock.

**Fix** (an hour). Fingerprint the live edges only: count(*) and max(id) WHERE source_doc = ? AND valid_to IS NULL. An edge that ends lowers the count and a new one raises max(id), so the fingerprint still moves on both. Alternatively, a migration adding a full index on edges(source_doc, valid_to).

**Checked by the skeptic.** Confirmed. Migration 0016 makes idx_edges_source_doc partial (WHERE valid_to IS NULL). The fingerprint query has no condition on valid_to, so EXPLAIN QUERY PLAN gives 'SCAN edges'. On a test store built by init_db with 400k edges (40% ended), the query took 20.8 ms median and document_fingerprint 21.3 ms per call, warm, on the desktop. The docstring says 'Five lookups on indexes', which is wrong. The /changes?doc= route runs it under con_lock (api/work.py:244), and boot.js polls every 10 s per open document page. Edges are append-only, so the scan keeps growing with the library.

## 10. cited_but_missing runs up to 60 library-wide FTS phrase queries per call to count citers (about 0.85 s at library size)

medium cost, performance, planned, `docs/PLAN.md`. `src/prax/store/documents/reads.py:1297`.

**Evidence.** _library_citers (line 1208) runs chunks_fts MATCH '"<first 8 title words>"' joined to chunks, filtered on kind='reference' after the join. The plan is 'SCAN chunks_fts VIRTUAL TABLE INDEX 0:M1' with no rowid bound, so each query reads the full doclist of every word in the title. It runs for limit*2 = 60 works on every call (line 1295). On a synthetic FTS5 store with the same table definition (Zipf vocabulary, 10.8% reference chunks), 60 title-like phrases took 292 ms at 500k chunks and 830-878 ms at 1.47M chunks (the library's size), warm, desktop. Titles of common words took 35 ms median and 62 ms max each at 1.47M. Cost grows linearly with chunk count.

**Why here.** cited_but_missing is an MCP tool (an agent-facing request). On the Pi it costs several seconds per call, and that grows with the library. The checklist's growth and per-request items apply.

**Fix** (an hour (cap and deadline) or a stage (counts kept by the references pass)). Compute the citer counts once rather than per request. One option is a table or meta map of normalised reference titles to citing-document counts, built by the references pass of prax maintain and looked up by key here. Cheaper short-term options: count only the `limit` works actually returned, not limit*2, and pass a deadline as cite_link does, leaving cited_in_library absent past it.

**Checked by the skeptic.** Confirmed. _library_citers (reads.py:1208) runs one phrase MATCH over all of chunks_fts and applies kind='reference' only after the join. cited_but_missing calls it for each of the top limit*2 works (reads.py:1295-1297). I re-measured on my own synthetic FTS5 table with the same definition: 1M chunks, Zipf vocabulary, 10.8% reference chunks. Phrase queries with common title words took 22-57 ms each, and 60 queries took 1.4 s, warm, on the desktop. That agrees with the reported 0.85 s at 1.47M chunks. It is an MCP tool and agent-facing, so on the Pi each call costs seconds, growing linearly with the number of chunks.

## 11. ontology.SELF_KINDS is redefined twice in repair/graph.py and written as a literal three times in extraction.py

low cost, hygiene, planned, `docs/PLAN.md`. `src/prax/store/repair/graph.py:684`.

**Evidence.** `_NOT_FOLDED = frozenset({"page", "project"})` (line 684) and `_NOT_SPACED = frozenset({"page", "project"})` (line 773) were both added in the range. `prax.graph.ontology.SELF_KINDS` (ontology.py:56) is that set, with a comment saying why neither kind is ever folded. graph/resolution.py:95 and maintain.py:635 already import it. extraction.py:925, 934 and 1000 test `in ("page", "project")` inline.

**Why here.** The store's own kinds of document are one decision. When a third kind joins it (the plan's synthesis and project pages are the likely growth), SELF_KINDS changes, but these two repairs and the extraction checks keep folding or proposing it. That is a cross-type merge, which only unmerge_run can take back.

**Fix** (under an hour). Use `ontology.SELF_KINDS` in the five places. Under an hour.

**Checked by the skeptic.** Confirmed. _NOT_FOLDED (line 684) and _NOT_SPACED (line 773) are both new and both frozenset({'page','project'}). ontology.SELF_KINDS (ontology.py:56) is that same set, with the comment that neither kind is folded, and resolution.py and maintain.py already import it. When a kind is added to SELF_KINDS, these two merge repairs will not follow it and will fold that kind. Only unmerge_run can undo such a merge.

## 12. choose_reader restates quality's scan threshold and the reader-stamp groups as literals

low cost, hygiene, fixed with 3. `src/prax/capture/pipeline.py:172`.

**Evidence.** `SCANNED = 0.5  # the share of profiled pages that makes a PDF a scan` is defined, comment and all, at capture/pipeline.py:172 and parsers/quality.py:56. The pipeline already imports `quality`. Both compute `max(profile['scanned'], profile['ocr_layer']) >= SCANNED` themselves (pipeline.py:285, quality.py:184). Stamp prefixes are written as literals in pipeline.py:260, 265, 269, 310, 356 and 363 ('latex-source/', 'marker/', 'pymupdf4llm/'), and the `source_failed` expression is written twice in the same module (260 and 310). quality.py:48-50 names the same groups (`LATEX_READERS`, `LAYER_READERS`), and importers/latex.py:34 holds `STAMP = 'latex-source'`.

**Why here.** AO steps 4-5 rest on the reader choice and the quality findings agreeing on what a scan is and which readers write LaTeX. If one threshold is retuned (the survey lists SCANNED as a tuned number), the finding 'ocr-layer → marker-ocr' and the pipeline's own scan test drift apart. A new reader (the plan's AP/AQ) has to be added in both lists.

**Fix** (an hour). Have the pipeline use `quality.SCANNED` and a `quality.scan_share(profile)`, and the stamp groups from quality, with `latex.STAMP` as the source of the prefix. Compute `source_failed` once in follow_ups and pass it on. An hour.

**Checked by the skeptic.** Confirmed. pipeline.py:172 adds SCANNED = 0.5 with the same comment as quality.py:56, though pipeline already imports quality. Both modules run their own max(scanned, ocr_layer) >= SCANNED test. The stamp prefixes are literals at pipeline.py:260/265/269/310/356/363, while quality.py:48-50 names LATEX_READERS and LAYER_READERS. The source_failed expression appears twice (260, 310). The reader choice and the findings that drive it can drift apart when one threshold is retuned or a reader is added.

## 13. The worker's latex-source reading depends on the importers package for its conversion, through four lazy imports

low cost, hygiene, planned, `docs/PLAN.md`. `src/prax/parsers/latexsrc.py:174`.

**Evidence.** `from prax.importers import latex` is repeated inside `_main`, `pandoc_stamp`, `available` and `_latex_source` (lines 174, 190, 199, 206). importers/latex.py imports nothing of prax (lines 22-29), so the imports are not avoiding a cycle. The conversion (flatten, bibliography, number_displays, to_markdown, convert, figure_captions, REVISION) is shared by the CLI importer (clients/cli/prax_cli/importing.py) and the worker reading, and the stamp both write is defined in the importer.

**Why here.** CLAUDE.md's layout: `parsers` reads a document into text and `importers` are clients of a source. The pandoc conversion is the parser, and the importer only walks folders of manuscripts. As it stands, a change to how a folder of manuscripts is found or dated (an importer's concern) is a change to the module the worker's parse depends on, and the stamp revision lives in the client.

**Fix** (half a day). Move the conversion (and STAMP/REVISION) into `prax.parsers` (latexsrc or a `parsers/latex` part), import it at module top, and keep `importers/latex.py` as the manuscript walker that calls it. Half a day, with the tests' imports updated.

**Checked by the skeptic.** Confirmed. parsers/latexsrc.py imports prax.importers.latex lazily at lines 174/190/199/206. importers/latex.py imports only the stdlib, so no cycle is being avoided. No other parser depends on an importer: grep finds only api and evaluation importing prax.importers. The conversion and the stamp/REVISION the worker's reading writes live in a client-side importer module. That goes against the layout (and architecture.md's 'add an extractor' row), and it ties the worker's parse to changes made for the folder importer. The cost is structural and small.

## 14. worlddates writes not-a-name words as an English regex in code, beside the lexicon's not_a_name section

low cost, hygiene, fixed with 6. `src/prax/graph/worlddates.py:53`.

**Evidence.** `_STAND_IN = re.compile(r"(?i)^(the )?(author|authors|he|she|they|we|company|unknown|none)$")`, new in the range. ontology/lexicon.yaml:133-160 `not_a_name.exact` already holds 'unknown', 'none', 'author' and 'authors' for every extraction, read through ontology.py:705. The module goes on to call `extraction._drop_reason`, which applies that lexicon, so half the regex restates it and the rest (pronouns, 'company') is English-only.

**Why here.** Invariant 9 / Structure: the words that say a name is not a name are in `ontology/lexicon.yaml`, so that a library in another language extends data, not code. A French or German world-dates answer ('l'auteur', 'er', 'die Firma') gets through, and a pronoun the extraction prompt also produces stays outside the general filter.

**Fix** (an hour). Add the pronoun and stand-in words to `not_a_name.exact` (or a `stand_in` list beside it) and drop `_STAND_IN`. The world-dates check then relies on `_drop_reason` alone. An hour.

**Checked by the skeptic.** The regex is as described, and CLAUDE.md says plainly that the words saying what is not a name live in ontology/lexicon.yaml, not as patterns in the module that reads them. lexicon not_a_name.exact already holds unknown/none/author/authors. One detail of the finding is wrong: extraction._drop_reason does not apply the lexicon (clean_name passes 'unknown', 'author', 'he'; only review.py:193 reads lexicon().exact). That makes the duplication worse, not better: the English pronoun list sits in code and does not cover a library whose graph.language is not English.

## 15. mdwiki copies store.slugify's grammar and 80-character cut by comment, and has its own front-matter regex

low cost, hygiene, planned, `docs/PLAN.md`. `src/prax/importers/mdwiki.py:39`.

**Evidence.** `SLUG_CHARS = 80  # store.pages.slugify's cut` and `_SLUG = re.compile(r"[^a-z0-9]+")` (lines 39, 44) restate store/pages.py:55 and 64. `slug_for` puts an 8-hex hash tag at the end of a long slug. The CLI sends that slug to the door (clients/cli/prax_cli/importing.py:356, 367), and `write_page` runs `slugify(slug)` on it again (pages.py:297). `_FRONT = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)` (line 41) is a stricter copy of text/status.py:55's front-matter pattern, without its allowance for trailing spaces or a file that ends at the closing marker.

**Why here.** If slugify's cut drops below 80, the door cuts off mdwiki's hash tag. Two long note paths then share a slug, and the second import overwrites the first page without an error. A note whose front matter has a trailing space after `---` keeps its YAML in the page body.

**Fix** (an hour). Move the slug grammar into `prax.text` (`slug(text, limit)` plus a tagged variant). store.pages and mdwiki then use the same one, and mdwiki uses status.py's front-matter pattern, exposed from prax.text. An hour.

**Checked by the skeptic.** Confirmed. mdwiki.py:39/44 copies slugify's grammar and its 80-character cut by comment (pages.py:64 hard-codes slug[:80]). slug_for's hash tag survives only while the two agree. mdwiki's _FRONT (line 41) is stricter than text/status.py:55, which allows trailing spaces and end-of-file after the closing marker. Both could come from prax.text (which imports nothing of prax), so the importer would not need its own copies.

## 16. The readers that write LaTeX are listed separately in Python and JS, as stamp prefixes, apart from the extractor registry

low cost, plan, fixed with 3 and 7: the door says `writes_latex`, the page reads it. `src/prax/parsers/quality.py:48`.

**Evidence.** `quality.LATEX_READERS = ('marker/', 'latex-source/', 'latex2html/')` and `LAYER_READERS`, `view-doc.js:216 MATHS_READERS = ['marker', 'latex-source', 'latex2html']` (it decides whether inline $…$ is typeset), and pipeline.py:356 and :363 test `stamp.startswith(('marker/', 'latex-source/'))` to decide on the formula readings. latex2html is an importer's stamp and appears in no parser registry. None of these lists comes from `parsers.REGISTRY`'s Extractor, which already carries a reader's other properties (explicit_only, variant, previous).

**Why here.** AJ ('the OCR readers measured … the result becomes routing rules', with vision-pages and marker's OCR modes in the comparison) and AO's checks. A next reader that writes LaTeX, or vision-pages once it does, has to be added in three places, or maths-as-text flags its text, the page shows raw $…$, and the formula readings are never asked for.

**Fix** (an hour). Declare 'writes LaTeX' and 'reads the text layer' as an attribute of a text source: on `Extractor`, plus a small table for importer stamps such as latex2html. Have the door serve that list to the UI (a field on the document or a constant endpoint) instead of a JS copy.

**Checked by the skeptic.** Confirmed. The LaTeX readers are listed three times: quality.LATEX_READERS (quality.py:48), view-doc.js:216 MATHS_READERS, and the startswith tuple at pipeline.py:363 for the formula readings. pipeline.py:356 is the crops rule, a different list. The parsers REGISTRY Extractor already carries other per-reader properties (explicit_only, previous), so 'writes LaTeX' would fit there. latex2html is an importer stamp, though, so a registry flag alone would not cover it. A next reader that writes LaTeX (vision-pages under AJ) needs three edits, or it is flagged maths-as-text and its inline maths goes untypeset. Low cost, but it will be paid.

## 17. The mdwiki importer reads one repository's layout and silently drops other Markdown wikis

low cost, independence, planned, `docs/PLAN.md`. `src/prax/importers/mdwiki.py:94`.

**Evidence.** notes() keeps only Markdown inside a folder literally named 'wiki', a topic's README and the root README (lines 94-98). SKIP_DIRS (line 34) hard-codes {'txt','admin','public','vault','tests',...}, taken from the one course repository the docstring names. topic_entries/parent_of assume '<topic>/wiki/index.md'. Obsidian-style '[[wikilinks]]' are not rewritten (_LINK matches only '[..](..)'). There is no CLI option to change any of this (clients/cli/prax_cli/importing.py:290). On an ordinary Obsidian vault (notes at topic/note.md, or under a 'vault/' or 'public/' folder) the importer imports only the READMEs and reports success.

**Why here.** `prax import mdwiki` has a generic name and is offered to anyone. On a wiki unlike the owner's it does something quietly wrong (it drops nearly everything), which the independence checklist names as the defect.

**Fix** (an hour). Make the wiki-folder name and the skip list options (defaults as now), count and print the Markdown files left out, and either rewrite [[wikilinks]] or say in the help text that they are not read.

**Checked by the skeptic.** Partly confirmed, scope narrower than claimed. The docstring says the importer is for one layout (a repository of papers with an LLM wiki in <topic>/wiki/), so an Obsidian vault is outside its purpose and that part of the cost is overstated. Still, SKIP_DIRS hard-codes folder names from the owner's course repository ('txt', 'admin', 'public', 'vault', 'tests'), and they are applied to PDFs as well as notes (pdfs() uses _skipped). Another person's repository with papers under public/ or admin/ loses them silently, and no CLI option changes the list. The cost is small because the importer is niche and documented as shape-specific.

## 18. Text-check and LaTeX-source thresholds were tuned on this library and no script re-measures them

low cost, independence, planned, `docs/PLAN.md`. `src/prax/parsers/quality.py:56`.

**Evidence.** SCANNED=0.5, MATHS_PAGES=2, DEBRIS_LINES=3 and SHORT=0.5 come from a one-off run and two hand samples written up in docs/eval/quality-checks-2026-10-18.md. No scripts/eval_* or bench_* imports parsers.quality (I grepped scripts/). latexsrc.SAME_PAPER=0.75 (parsers/latexsrc.py:70; '0.83-0.99 for 62 of the library's papers') and LEAST_SHARE=0.3 are checked only by tests/test_review_2026_10_18.py, which tests behaviour, not the cut-off. SCANNED is also defined a second time at capture/pipeline.py:172, so the reader choice and the finding can drift apart. None of these is a prax.yaml setting. By contrast, paths.SOUND (6 to 7) is re-measured by scripts/eval_paths.py, worlddates.SUPPORTED is a setting with scripts/bench_world_dates.py, and the venue words have scripts/eval_venues.py.

**Why here.** These findings choose a reader for what a person adds (marker or the arXiv source: GPU time) and fill the Admin 'text checks' list. On a library with different typesetting (heavy maths, scans, non-arXiv papers) there is no repeatable way to measure precision before trusting the bulk-fix button. SAME_PAPER's failure is at least loud (an ExtractionError).

**Fix** (an hour). Turn the measurement into scripts/eval_quality.py (read-only, ids only, a precision sample per check), import one SCANNED in pipeline from quality, and optionally read SAME_PAPER through config.number.

**Checked by the skeptic.** Confirmed: SCANNED = 0.5 is defined both in parsers/quality.py:56 and capture/pipeline.py:172 with the same comment, so the finding and the reader choice can drift apart. I grepped scripts/: no eval or bench script imports parsers.quality or latexsrc. The independence part is weaker. SCANNED/SHORT are generic shares, and SAME_PAPER sits in a measured gap (0.71 vs 0.83), so it is robust. MATHS_PAGES/DEBRIS_LINES were tuned on a 12-document hand sample and nothing re-measures them before the bulk-fix button spends GPU time. Low.

## 19. COMMIT_RESERVE_MB is a constant sized from the owner's desktop, not a host setting

low cost, independence, planned, `docs/PLAN.md`. `src/prax/host/up.py:123`.

**Evidence.** COMMIT_RESERVE_MB = 4096 comes from one incident on the owner's machine (comment: '2026-10-06 the 27B beside marker left 628 MB, and a page file of twice the RAM'). It is a hard constant, and the project's convention says what a host chooses lives in prax.yaml (`config.number`).

**Why here.** On a Windows host with less RAM or a smaller page file, 4 GB of reserve can refuse every swap. On a larger one it is too cautious. The only fix today is a code change.

**Fix** (an hour). Read it through config.number('host.commit_reserve_mb', 4096) or similar.

**Checked by the skeptic.** Confirmed: COMMIT_RESERVE_MB = 4096 is a module constant (host/up.py:123), used at up.py:725 as `commit_free - need >= COMMIT_RESERVE_MB`. Its comment gives one incident on the owner's desktop as the reason. It is a property of the host (RAM, page file), and the convention puts host choices in prax.yaml through config.number. Only Windows enforces commit and the Pi target is unaffected, so the cost falls on another Windows host that swaps roles. Low.

## 20. The reference-list biography detector knows only English verbs

low cost, independence, fixed: the lexicon's `biography:` verbs. `src/prax/text/references.py:147`.

**Evidence.** _BIO matches 'Name received|is|was|joined|studied|…' only. A German or French author biography printed after a bibliography ('… studierte …', '… a obtenu …') falls through to the long-prose rule. That rule needs at most one year, and a biography usually has several, so the paragraph becomes a `reference` chunk that the references pass then tries to match as a citation.

**Why here.** This is AL step 9 N3's fix, and it holds only for English journals. The lexicon already has per-language sections where these verbs belong.

**Fix** (an hour). Move the verbs into a lexicon section (EN/DE/FR like world_time) and add a German case to the test.

**Checked by the skeptic.** Confirmed: _BIO (text/references.py:147-152) matches only English verbs, though is_bibliography_heading accepts 'Literaturverzeichnis'. A German or French biography with two or more years fails the is_prose fallback (`len(_YEAR.findall(p)) <= 1`), so it stays part of the reference entries. That was the N3 defect (log: 354 chunks in 209 documents held such prose), and it pollutes the entry's parsed surnames and year. Journals that print author biographies after the list are mostly English (IEEE style), so the cost is small. The verbs belong in a lexicon section per language.

## 21. Whether a PDF is a scan is decided twice with the same threshold, and the scan with no text layer exists only in the pipeline

low cost, generality, fixed with 3. `src/prax/capture/pipeline.py:172`.

**Evidence.** pipeline.py:172 SCANNED = 0.5 and quality.py:56 SCANNED = 0.5 carry the same comment. Both compute max(profile['scanned'], profile['ocr_layer']) >= SCANNED (pipeline.py:285, quality.py:184). quality's ocr-layer finding requires prof.get('layer'), so the pipeline adds its own rule for a scan without a layer ('a scan with no text layer at all is a scan as much as one with an OCR's'). That rule never becomes a finding in meta.quality, so the Admin page's 'text checks' tab cannot list it or offer its fix.

**Why here.** Stage AO said the findings decide the reader (commit 273c0d2: 'the reader choice and the crops follow the findings'). The second definition means tuning one SCANNED leaves the other behind, and one kind of scan stays outside the findings.

**Fix** (an hour). Give quality a finding for a scan with no layer (fix marker-ocr), or a quality.is_scan(profile) helper. Then delete pipeline.SCANNED and have choose_reader read only quality.fixes(found).

**Checked by the skeptic.** Confirmed. pipeline.py:172 and quality.py:56 each define SCANNED = 0.5 with the same comment, and pipeline already imports quality. The no-text-layer scan rule at pipeline.py:283-287 never becomes a finding, because quality's ocr-layer check requires prof.get('layer'). Tuning one threshold leaves the other behind, which goes against AO's goal that the findings drive the reader choice.

## 22. Two detectors for 'this PDF's text is not the book', with different thresholds and different fixes

low cost, generality, planned, `docs/PLAN.md`. `src/prax/parsers/quality.py:184`.

**Evidence.** This range added quality's ocr-layer and short-text checks: a scan read from its layer, or a text under half of the layer, with fix marker-ocr. The heal ailments that predate them, thin-texts and unreadable-documents (store/repair/ailments.py:461-500), find the same documents by bytes per page and offer pymupdf4llm-ocr. A Google Books scan whose layer is only its front matter matches both. The Admin page then shows it in the 'text checks' tab with marker and in heal with OCR, and the two never refer to each other.

**Why here.** This is two mechanisms for one concept. The second came in this range, and its docstring says it exists so that 'a garbled paper says so itself'.

**Fix** (an hour). Make thin-texts and unreadable-documents read meta.quality (or call quality.check), so there is one detector, and decide which reading is the fix for a cover-only layer. Alternatively, say in each docstring which documents belong to which, so a person does not get two answers.

**Checked by the skeptic.** Partly as described. short-text cannot match a scan: it requires scan < SCANNED, and a layer-only read is about as long as the layer. ocr-layer, though, does overlap thin-texts (ailments.py:461): a Google Books scan with a front-matter layer is scanned >= 0.5 with a layer, and it is also under 100 bytes a page. The first offers marker-ocr, the second pymupdf4llm-ocr, and neither refers to the other. That is a real but small duplication of one concept.

## 23. changes() counts each side twice by default to report 'corrected', doubling its cost

low cost, performance, planned, `docs/PLAN.md`. `src/prax/store/graph/traversal.py:893`.

**Evidence.** With corrections=False (the default for the MCP tool and the route), each side runs _side_counts over the candidate rows and then a second _side_counts with the 'healed' condition. On the 400k-edge test store: a one-day span took 70.5 ms by default against 39.0 ms with corrections=True; a month 374 ms against 189 ms; a day with 185k endings 943 ms against 471 ms. The log gives a real day at 0.37 s after migration 42, so the default call is now about twice that, before the Pi's factor.

**Why here.** changes is an MCP tool and a library-growth query. The extra pass re-reads the same candidate edges and adds no information that one pass could not give.

**Fix** (an hour). Count once per side, with the healed predicate as a selected column: SELECT e.rel, e.source_doc, <healed> AS h, count(*) ... GROUP BY e.rel, e.source_doc, h. Then sum the h=0 rows into counts and total, and the h=1 rows into corrected.

**Checked by the skeptic.** Confirmed. With corrections=False (the default), traversal.py:893-897 runs a second _side_counts over the same side condition with the healed clause, which scans the same candidate edges a second time. On my 400k-edge test store: a one-day span took 299 ms by default and 140 ms with corrections=True; a 40-day span took 6.6 s and 3.4 s. The default is about twice the cost. A single GROUP BY with a CASE on the healed condition would return both counts in one pass. Lower priority than the others, but changes is an MCP tool that grows with the library.

## Rejected

- **The run-id shape that `changes` and the undo passes depend on is built by hand in ten places, in three formats and with two clocks** (hygiene, `src/prax/store/repair/graph.py`). The three new copies at repair/graph.py:752/875/973 take their prefix from the named constants (TWINS_PRODUCER = 'heal:document-twins' etc.), so the 'heal:%' test in traversal.py:867 cannot be missed by mistyping it. They use UTC store.now(). restore_run and unmerge_run compare the run by equality only (edges.py:676), and nothing sorts runs by their stamp. The local-time strftime ids in maintain.py:541/614 and steps/base.py are outside the new code and are not read back by format. What is left is a repeated one-line expression with no failure that someone will actually hit.

- **repair/graph.py lists five of glyphs.ACCENTS' eleven detached accents by hand, so the spacing-twin keeper can keep the damaged spelling** (hygiene, `src/prax/store/repair/graph.py`). _DETACHED does list five of glyphs.ACCENTS' eleven. But the claimed failure does not happen. In the venv, names.normalize('Erd˝os') gives 'erd os' (spacing key 'erdos') and normalize('Erdős') gives 'erdo', so they never land in one spacing-twin group. 'Dvoˇrák' normalizes to 'dvoˇrak', which is not a spacing twin of 'dvorak' either. A group needs two spellings that differ only in spacing, and then both usually carry the same accent damage, so the first key ties whatever list is used. The drift is real but no wrong keeper was shown or found.

- **The tree of pages points to its parent by this store's integer document id inside meta JSON** (plan, `src/prax/store/pages.py`). Not an extensibility cost against the plan as written. AP chooses its shape explicitly: 'the integer keys stay (every table and index is built on them), and beside them a global id', and export and import translate to global ids. meta.page.parent is one more local integer of the same kind as edges.source_doc, entity ids and the '[title](#doc/N)' links inside page text, which an export has to rewrite anyway. The parent id adds nothing new to that work. Indexability is a performance question, and page counts are small.

- **A slug that stays unique when cut is mdwiki's own; store.slugify still cuts long names into collisions** (generality, `src/prax/importers/mdwiki.py`). There is no real second case that hits the 80-character cut. Question slugs come from questions._slug_of, which already cuts to 8 words and 70 characters, so any collision comes from that design and not from slugify's cut. Project names are repository names, far under 80 characters. The other slugify callers normalise a slug a caller gave. mdwiki needed the hash because it builds slugs from long file paths, a case the other callers do not have.

- **The reader manifest is a registry with one entry, and its consumer is written for that entry** (generality, `src/prax/host/readers.py`). The manifest is chosen in CLAUDE.md ("A reader that runs as processes of its own (marker) declares them in its manifest"), and work.py uses role_work generically. Nobody would take it as add-by-manifest-only, because roles.py's ROLES tuple names 'marker' and 'ocr-server' outright. The cost is a little reading at most, and no plan item needs a second reader.

## What the reviewers covered

- **hygiene.** Ran .venv python survey.py 72710d3 src: 95 files changed, no module past 1,500 lines (the largest are maintain.py at 1,385 and reads.py at 1,368). I compared function sizes against the base: five long functions grew by more than 25 lines (inbox.ingest_bytes, pipeline.follow_ups, routes._text_routes, readings.select_for_reading, context.hub_graph). Each lists cases rather than doing two jobs, so none is reported. Also checked and found sound: STATUS_TRIES in host/process.py and host/up.py (different operations, read and write, with different counts on purpose); the log handling split between process.rotate and readers.trim_log (different jobs); venue words in graph/venues.py, which mostly come from the research pack's lexicon (the 'international/internationale/internazionale' tuple at venues.py:389, added in the range, is the one code-side list, a small independence point and not reported); latex2html.Fetcher with robots.txt (the only crawler in src, no duplicate); the four heal folds in repair/graph.py, which all go through merge_entities and repeat no SQL; the cross-module private reaches in the diff (extraction._drop_reason, figures._data_url, store._check_domains, parsers.pdf._pymupdf_open), which are minor and not reported. Not covered in depth: capture/routes.py and capture/projects.py rewrites, host/roles.py and host/up.py swap logic, store/graph/traversal.py and paths.py internals, api/* handlers. The safety review owns correctness; the arXiv-id mismatch above is reported because its root is a reuse defect.

- **plan.** I read docs/PLAN.md (the order, the open items of AO, AI, AJ, AK, AL, AN, AP and AQ, "The graph", "Retrieval and ask"), the plan checklist, and the survey over 72710d3..HEAD (95 Python files). Then I checked the new abstractions against the next plan item that builds on each:

- **Stage AO.** Read parsers/quality.py, capture/pipeline (assess, choose_reader, follow_ups), the quality pass in maintain, select_for_reading's `finding`, POST /readings/bulk and the Admin page's checks tab. Findings 1 to 3 come from here.
- **World dates (AL, and "Later": `at` on traverse and connect).** `held_in_world` is built once in edges.py and used by traversal, paths and context. The path index caches world_at builds keyed with as_of. worlddates reads its cues from the lexicon's `world_time` section and its relations by ontology `kind`, not a list of names.
- **Entity folds (AP's 'twins' ids as aliases').** The new heal repairs (document-twins, spacing-twins, damaged-names) all fold through `merge_entities`, so merged_by and merged_run stay recorded.
- **AQ (originals referenced, not held).** Most new reads of an original go through `original_info` or `get_original`. Two passes in maintain (markup, quality) still call archive_path(hash) directly, as before; that is a small cost when AQ lands.
- **AJ.** The reader contract (host/readers.py) declares processes, companions, locks and log trimming, as the plan says, and the swaps record (`run/swaps.jsonl`) that AI's measurement waits on exists.

New importers (latex, latex2html, mdwiki) post through the door with a stamp, as the protocol requires. I ran only the survey script and read-only git and grep commands. Nothing touched the live database or the door.

- **independence.** Ran survey.py 72710d3 src (95 changed files, 77 commits) and read the independence checklist. Tuned numbers: paths.SOUND (6 to 7) is re-measured by scripts/eval_paths.py. worlddates.SUPPORTED=0.9 is a setting (steps.worlddates.supported) with scripts/bench_world_dates.py and docs/eval/world-dates-2026-10-06.md. The venue rules now live in the research pack's lexicon.yaml and scripts/eval_venues.py --diff covers them (the checklist's 'no eval yet' is out of date). readings.NOT_MATHS_P and NOT_MATHS_GENRES use genre labels from genres.yaml, not the owner's titles. The caps (MAX_SENTENCES, REFERENCES_*, PRECEDENTS, MARKED, batch sizes, figure page sizes) are budgets, not tuned on the library. Word lists: the core lexicon's status (EN/DE/FR keys, plus es/it 'estado/stato'), world_time (EN/DE/FR), notices (EN plus two German stems, no French: a French notice could become a cite phrase, which is harmless). Research venues: EN/DE/FR/ES/IT leading words and ordinals; spelled ordinals are English only, which is data and extendable. Craft ingredients: EN and DE, and other languages give no calls_for facts, which is the right failure. dates.month_names: EN/DE/FR. retrieval STOPWORDS (EN/DE) predate the range. figures._CAPTION (fig/figure/abb/abbildung, which predates the range) now also feeds quality's bare-figures check: Spanish/Italian 'Figura' and Dutch 'Figuur' are not seen, so the check does nothing, which is safe. pdf._MATH_FONT and the profile are font/geometry based and language-free. latexsrc._LONG_WORD is ASCII-only but returns True when it has nothing to judge by. text/status reads its words from the lexicon. Spacing-twins and damaged-names heals are language-neutral apart from the finding above. The drop-cap rule strips a Portuguese 'a' before an a-word, which is harmless. Private data: the only names in the added src are Julius O. Smith/CCRMA (a public source named in latex2html and mdwiki docstrings, plus 'GlobalJOSIndex' in NOT_PAGES, a site-specific but harmless filter) and 'TU Munich' as an example, no personal document. I did not run any eval against a library copy (none was named), and I did not review the UI JS or clients/cli beyond the mdwiki wiring.

- **generality.** I ran survey.py (95 changed files) and read the generality checklist. These were sound: world dates have one year-in-quote check (graph.worlddates uses extraction.checked_date, so the extraction's optional dates and the step share it); held_at and held_in_world are record time against world time, not two of one; latexsrc reuses importers.latex for flattening and pandoc and writes the same stamp; the status-file retries in host/process.py (read) and host/up.py (replace) are the two sides of one file, not duplicates; venue editions as 'a name with an edition' have no second case in the code or the plan (text/names.py already holds the general same-numbers check); meta.page.parent (one navigation parent) against part_of project edges is a deliberate split documented in pages.write_page. Checked by running code: references.parse and inbox.url_ids on versioned and old-style arXiv ids, and the pipeline's latex2html path through inbox.follow_ups.

- **performance.** Ran survey.py 72710d3 src and read the performance checklist. No copy of the library was named, so every measurement used stores in the scratchpad: (a) a store built by prax's own migrations (12,900 documents, 161k entities, 400k edges with 185k ended, edge_endings); (b) synthetic FTS5 stores of 500k and 1.47M chunks with the chunks/chunks_fts definition from migration 1; (c) a generated 800-page scanned PDF and a 4.2 MB text. Checked and found sound or small enough: cite_link's new _notice_pattern (0.34 ms per hit: ontology.lexicon() stats 17 files and re.compile is cached; about 3 ms a search); hidden_documents' new memo; traverse/traverse_map with world_at and within (about 1 ms); _mark_stated and hidden_by_premise (primary-key and premise-index lookups, rule edges only); entity_marks with 200 things (14.6 ms, plan uses idx_edges_src and idx_entities_canonical); hub_graph with within and personal=hide (744 ms, faster than unscoped; world_at adds about 9%; the unscoped 1.6 s on the test store predates this range); scope_documents (29 ms); quality_page over 12,900 metas of about 2 KB (19 ms counts, 41 ms list); the door-side assess on a landed text (pdf.profile 0.29 s on 800 pages, since it samples 12; quality.check 78 ms on 4.2 MB); the world_dates_needed selection (the established json_extract pattern over documents); worlddates.apply (at most 8 facts per document); and the index on edge_endings (its primary key (edge_id, run) serves changes' EXISTS). Scratch files are in C:\Users\lodsb\AppData\Local\Temp\claude\I--proj-prax\1433c6e5-cbdc-4514-92f9-acfbb0e5cb78\scratchpad\perf. No repository file was changed, and neither the live database nor the door was touched.
