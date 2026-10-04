# The quality review of 2026-10-05

The project's skill `.claude/skills/code-review` in full mode, over
everything changed in `src` since `1c650c4^` (2026-10-02 to -04, after
the safety review's fixes): five reviewers, one a dimension (hygiene,
extensibility against the plan, independence from the owner's library,
conceptual generality, performance on the target), each finding
re-checked by a skeptic. 24 confirmed (three found twice: 4 and 23, 9
and 15, 19 and 21), 2 refuted. The fix plan is in `docs/PLAN.md`,
"The quality review's fixes"; each finding below names its step.

## 1. Two definitions of when a venue edition is 'the same thing': sameness.yaml says merge, the venue tier says keep apart and link part_of

high cost, generality, step 1. `ontology/sameness.yaml:33`.

**Evidence.** ontology/sameness.yaml:33-35 lists under `same:` 'a conference or journal and one of its editions or years, since the library keeps the series' (example [ISMIR 2010, ISMIR 2011]). The venue tier added in range (9804e70, src/prax/graph/resolution.py:88-92 and 316-338, src/prax/graph/venues.py) keeps each edition as its own entity linked `part_of` its series. The review fix 124e2b2 made rules.same_answers treat two sibling editions ('ISMIR 2008', 'ISMIR 2009') as two answers. resolution.py:52 keeps 'venue' in LIKELY_TYPES, and resolution.py:436-442 gives every judge of a likely pair the sameness.yaml rule, so the local or paid adjudicator is still told that sibling editions are one thing. names.same_numbers (resolution.py:264) guards only the near-name tier, not the embedding tier. docs/log.md:4516-4518 already records the effect: 'Earlier likely-tier rounds had folded some editions into "DAFx" itself; those stay until a person takes their round back.'

**Why here.** CLAUDE.md invariant 9 (sameness.yaml is what 'the same thing' means for every judge) and resolution's venue and edition tiers. Each nightly `prax resolve` likely round can undo the edition structure the venue tier builds, which brings back the functional-conflicts and edition links that tier exists to fix.

**Fix** (an hour plus a re-measure of the judge). Give sameness.yaml one meaning that matches the code. Move editions to `different:` ('a series and one of its editions, linked part_of'), keeping the example of two names for one edition under `same:`. Then measure the judge again (steps.adjudicate.platt), as CLAUDE.md requires after any change to the wording. Optionally keep pairs of venues that venues.read gives different editions out of the likely candidates.

**Checked by the skeptic.** Confirmed. ontology/sameness.yaml:33-35 lists 'a conference or journal and one of its editions or years' under same:, with the sibling example [ISMIR 2010, ISMIR 2011]. resolution.same_rule (resolution.py:436-442) hands that file to every judge. The venue tier (resolution.py:316-338, venues.py) keeps each edition apart and links it part_of its series, and rules.same_answers (rules.py:395-401) says outright that siblings 'ISMIR 2008' and 'ISMIR 2009' are two answers. 'venue' is still in LIKELY_TYPES (resolution.py:52). In _likely, only NEAR_TYPES get the same_numbers guard (resolution.py:264), and the venue tier adds only merge drops to `taken`, never editions. So an embedding pair of two editions, or of an edition and its series, still goes to an adjudicator that has been told to merge them. The log records the rule revision of 2026-09-28 (log.md:2196) and its effect: likely-tier rounds folded editions into 'DAFx' (log.md:4516). Nothing reconciles the two definitions.

## 2. cited_but_missing reads every reference chunk in the library because the planner picks idx_chunks_kind over the doc_id IN list

high cost, performance, step 1. `src/prax/store/documents/reads.py:1129`.

**Evidence.** I built a test store at library scale (12,900 documents, 1.42 M chunks of which 213,150 are references, migrations through 44, no sqlite_stat1 because prax never runs ANALYZE). EXPLAIN QUERY PLAN of `SELECT doc_id, data FROM chunks WHERE kind = 'reference' AND doc_id IN (40 ids)` gives `SEARCH chunks USING INDEX idx_chunks_kind (kind=?)`. That walks all 213 K reference rows: 237-266 ms on the desktop for 40 documents, and 241 ms for 500. Writing `+kind = 'reference'` gives `SEARCH chunks USING INDEX sqlite_autoindex_chunks_1 (doc_id=?)`: 0.72 ms for 40 documents and 23.5 ms for 500, the same rows. The per-document query at line 1041 (`doc_id = ? AND kind = ...`) already gets the doc_id index (0.0 ms). The old `math_scores` query in readings.py:406 has the same shape but is outside this range.

**Why here.** This is the cited_but_missing MCP tool and GET route (api/documents.py:307), an agent request. The cost grows with the size of the whole library's bibliographies, not with the documents asked about. Expect roughly 1 s per call on a Pi-class host.

**Fix** (an hour). Stop the kind index being chosen. Write `+kind = 'reference'` (or `INDEXED BY idx_chunks_doc`), or add an index on chunks(doc_id, kind). Any query that pairs kind with a doc_id list needs the same treatment.

**Checked by the skeptic.** Confirmed. reads.py:1129 queries `kind = 'reference' AND doc_id IN (...)`. The only kind index is idx_chunks_kind (migration 0002), the doc_id index is UNIQUE(doc_id, seq), and no store module runs ANALYZE. I rebuilt the shape in memory (12,900 docs, 110 chunks each, 16 of them references). EXPLAIN QUERY PLAN gives `SEARCH chunks USING INDEX idx_chunks_kind (kind=?)`: 24 ms for 40 ids even with tiny rows. With `+kind` it gives `SEARCH ... sqlite_autoindex_chunks_1 (doc_id=?)`: 0.5 ms, same 640 rows. So the cost grows with every bibliography in the library, not with the documents asked about. references_of at line 1041 uses doc_id = ? and is fine.

## 3. Two ways to sync a project, keyed differently, with the title and key rules written twice

medium cost, hygiene, step 2. `src/prax/capture/projects.py:47`.

**Evidence.** `prax import project` (clients/cli/prax_cli/importing.py:304) still runs importers/project.py `items()`: it keys `f"{cfg.name}/{rel}"` and builds the title from its own `_HEADING` plus `f"{heading or rel} ({cfg.name})"` (importers/project.py:46, 97-118). The new door path `/projects/sync` copies the same regex and title rule in capture/projects.py:42 and 53, whose docstring says 'as the importer wrote it', but keys `remote:repo_path` when there is a remote (doc_key, line 47). It adopts the importer's `name/rel` key as `legacy` and rewrites meta.project.key (lines 138, 155, 430-436). The `,`-split settings reader is written twice as well: importers/project.py:69 `words()` and capture/projects.py:60 `_words`. The importer looks documents up by meta.project.key (importers/feed.py:74-95), so after a sync it no longer finds what it wrote. I read the code and did not run it, so I have not confirmed whether that ends in duplicate documents or in the two paths rewriting each other's key.

**Why here.** Stage AL (project sync) and CLAUDE.md invariant 3, 'one door'. Two writers of one document identity have to stay identical by hand. A change to the title rule or the key in one silently forks a project's documents in the other.

**Fix** (an hour or two). Make `prax import project` a client of `/projects/sync`: call `client.project_files` and post, as the MCP `sync_project` tool already does. Then delete `items()`, `_HEADING` and `words()` from the importer and keep the `.prax-project` reader only to fill the request.

**Checked by the skeptic.** Confirmed by reading. importers/project.py:46 and capture/projects.py:42 hold the same _HEADING regex. The title rule appears twice: importers/project.py:108-114 and capture/projects.py:_title, whose docstring says 'as the importer wrote it'. The comma-split reader appears twice: words() at importers/project.py:69 and _words at capture/projects.py:60. The keys differ: the importer keys f'{cfg.name}/{rel}', while doc_key uses '{remote}:{repo_path}' when there is a remote and adopts the old key as 'legacy'. `prax import project` (clients/cli/prax_cli/importing.py:304) still calls importers/project.items(). feed.existing (importers/feed.py:76) looks documents up by meta.project.key, so after a sync it cannot find the importer's old key. Even the sync CLI (importing.py:440) imports importers.project for its settings. The two writers of one document identity are kept identical only by hand. Whether this ends in duplicates or in content-hash dedupe was not run, as the reviewer also says.

## 4. The heal check for functional conflicts recomputes what rules.find_conflicts keeps, by a different rule

medium cost, hygiene, step 2. `src/prax/store/repair/graph.py:128`.

**Evidence.** `_functional_conflicts` (repair/graph.py:128-176) and `_functional_pairs` (store/graph/rules.py:420-460) both read the live edges of every functional relation, fold them with `part_of_ancestry` and `same_answers`, and decide which subjects disagree. They differ in three ways. The rules pass leaves out edges whose producer is `rule:%` and the heal check counts them. The heal check groups subjects by `(cs.name, cs.type)`, so two entities with one name and type merge into one subject, while rules groups by canonical id. Only rules caps at CONFLICT_PAIRS. Meanwhile `edge_conflicts` (migration 43) already holds the maintained set that `find_conflicts` keeps in step.

**Why here.** Stage AN (constraints, conflicts). `prax heal --check functional-conflicts` and the Review page's conflicts can report different counts for the same library. Every later change to what counts as a conflict, such as sibling editions or `part_of`, has to be made in both places; the 2026-10-04 review already touched this logic.

**Fix** (an hour). Have the heal check read `edge_conflicts WHERE ended_at IS NULL`, grouped by subject for display, or call `_functional_pairs`. Keep one definition of a conflict in store.graph.rules.

**Checked by the skeptic.** Confirmed. repair/graph.py:128-176 groups by (cs.name, cs.type) in Python, after grouping by cs.id in SQL, so two canonical entities with the same name and type fold into one subject. It counts rule:% edges, and it caps only by FUNCTIONAL_SHOWN. rules._functional_pairs (rules.py:420-460) keys by canonical id, excludes producer LIKE 'rule:%', and keeps the maintained set in edge_conflicts. repair comes after graph in the store's order, so it could read edge_conflicts or call the rules helper. The two counts can differ, and every change to the conflict rule has to be made twice.

## 5. Repairs that end an edge and write its correction do not record which edge corrects which

medium cost, plan, step 4. `src/prax/store/repair/graph.py:467`.

**Evidence.** _repair_part_of ends the reversed edge (_invalidate, which records only edge_endings(edge_id, run, ended_at)), then calls link() for the turned edge and throws away the id it returns. _repair_not_venues does the same at about lines 651-661. edge_endings already has a `restated_as` column, but only restore_run fills it (store/graph/edges.py:555), for the opposite direction. After a heal, 'edge 47184 was corrected into edge N' can only be guessed by matching run=HEAL_PRODUCER and reversed ends. There are now three edge-to-edge side tables, each with its own meaning: edge_premises (derived from), edge_conflicts (disagrees with) and edge_endings (ended by a run). The correction link the plan asks for would be a fourth, unless it goes into one of these.

**Why here.** PLAN AL 'As Utopia does it': '`supersedes` on an edge that corrects another, so "corrected" and "rejected" are a query'. AL step 9 G3: changes(domain=…) swamped by the night's part_of corrections, 'or a `corrected` side'. G2: an older fact ended or kept as evidence. Each night's heal passes produce these correction pairs and lose them.

**Fix** (an hour for the record in the two repairs; a migration only if a new column is chosen over restated_as). Record the correction where it happens: make _invalidate/record_ending take the replacing edge id (or add a `corrects` column next to restated_as), and have _repair_part_of and _repair_not_venues pass link()'s return value. Then changes() can count a 'corrected' side by joining edge_endings. Decide now whether the AL edge-level supersedes uses this table or a new one, so that a fifth side table does not follow.

**Checked by the skeptic.** Confirmed. _repair_part_of (repair/graph.py 467-479) and _repair_not_venues (651-663) call _invalidate and then link() but discard the id link() returns, so nothing records which edge corrects which. edge_endings.restated_as (migration 0044) cannot simply be reused: restore_run uses `restated_as IS NULL` (edges.py:517) to mean 'not yet restored', so filling it with the correction would make restore_run skip those rows. Recording the pair needs a column or table of its own. The pairs can be rebuilt afterwards only by guessing from the same run, the same source_doc and evidence, and src/dst swapped. That holds up for part_of and is weaker for not-venues. Each night's heal adds more of these unlinked pairs, while PLAN AL asks for `supersedes` on a correcting edge and G3 for a `corrected` side.

## 6. Venue tier merges at confidence 1.0 every night, with English/German rules tuned on this library and no script that re-measures them

medium cost, independence, step 3. `src/prax/graph/venues.py:29`.

**Evidence.** `_LEADING`, `_ABBREVIATIONS`, `_WHEN` (English and German months plus SS/WS/Sommersemester), `_UNITS`/`_TENS` (English spelled ordinals), `_ORDINAL` (`st|nd|rd|th|d|.`), `VENUE_WORDS`, `_EVENT_WORDS` and `_PUBLISHERS` are all literals in code. resolution._venues (src/prax/graph/resolution.py:313-338) turns `venues.plan` into Candidates with confidence 1.0, and resolution.apply (line 593) merges them alongside the `sure` tier. That tier runs in the nightly `resolve` (schedule NAMES includes "resolve"; PLAN.md item 1 has it at 03:00). No eval exists: `ls scripts` lists eval_paths, eval_dates and others, but no eval_venues. tests/test_venues.py pins English examples. I ran `venues.read` on French, Spanish and Italian names: "12e Congrès Français d'Acoustique 2014" keeps `12e` in its series, and "Actas del 3er Congreso…" and "Atti del XX Colloquio…" get no edition. So their editions never group. That fails safe for now, but nothing reports it.

**Why here.** Independence: tuned numbers and words with no repeatable measurement, applied without review. The checklist itself says the venue rules "have no eval yet". Merges are undone only by `unmerge_run`, so a rule that is wrong for another library's venues spreads quietly.

**Fix** (a stage (eval script plus lexicon move)). Write scripts/eval_venues.py: a read-only copy, a sample of venue groups the tier proposes, scored by hand or against Zotero's publicationTitle, ids only. Move the word sets (leading words, abbreviations, when-words, venue/event words, ordinal suffixes) into a `venues:` section of ontology/lexicon.yaml beside `not_a_venue`, so another language can add them as data.

**Checked by the skeptic.** Confirmed. resolution._venues (graph/resolution.py ~313-338) turns venues.plan into confidence 1.0 merge and edition Candidates, and 'resolve' is in host/schedule.py NAMES, so the merges run nightly with no review. scripts/ has twelve eval_* files and none for venues. The checklist itself says the venue rules 'have no eval yet'. I re-ran venues.read: the French name keeps '12e' in its series, and the Spanish '3er' and Italian 'XX' names get no edition. Those cases fail safe. The cost is that tuned English and German rules merge with nothing anyone else can re-measure, and a merge is undone only by unmerge_run.

## 7. Status reader marks a project note stale when an ordinary English sentence near its top starts with 'Archived', 'Wrong', 'Replaced', 'Void'…

medium cost, independence, step 3. `src/prax/text/status.py:29`.

**Evidence.** STATES maps bare first words to states. read() (lines 125-144) accepts an unkeyed line in the first HEAD_LINES=30 lines when its first word is any STALE word. I ran `status.read`: '# Notes\n\nArchived copies of the datasets live on the NAS.' returned state='retired'. 'Wrong turns we took are listed below.' returned 'invalid'. 'Replaced the ADC in rev B.' returned 'superseded'. capture/projects.py:359 applies this to every synced project note and writes meta.status. That moves the note STALE_SHIFT places down in search and makes `ask` tell the model it is stale. The words are English only: 'Veraltet: siehe plan-v2.md' and front matter 'status: veraltet' both return None. They are code literals, not lexicon entries.

**Why here.** Independence and failure mode: quiet and wrong on any English project whose notes open with such a sentence, and silent for a German or French project's own status words. Stage AL 'what is current' depends on it.

**Fix** (an hour). Count an unkeyed line only when the word stands as a status marker: followed by ':' or a date, emphasised, or in a callout or blockquote. Ordinary prose words ('wrong', 'void', 'replaced', 'archived') should count only after 'Status:'. Move STATES into the lexicon so other languages can add their words. Add tests for the three false positives above.

**Checked by the skeptic.** Reproduced. status.read returns 'retired' for 'Archived copies of the datasets…', 'invalid' for 'Wrong turns we took…' and 'superseded' for 'Replaced the ADC in rev B.'. It returns None for 'Veraltet: …' and for front matter 'status: veraltet'. Any STALE first word in the first 30 lines counts without a 'Status:' key (lines 125-144), and capture/projects.py _statuses writes the result to meta.status, which drives the STALE_SHIFT demotion and the stale notice in ask. The module docstring says a passing mention should not count, but the code only checks that the line starts with the word. The words are English-only code literals.

## 8. Recipe markup turns French, Italian or Spanish ingredient lines into wrong EXTRACTED ingredient entities; the kitchen word lists are English/German code literals outside the kitchen pack

medium cost, independence, step 3. `src/prax/text/schemaorg.py:51`.

**Evidence.** `_PREP`, `_MEASURE` and `_LEADING`, and the split on und/and/oder/or/plus/sowie in ingredient_name, hold English and German cooking words in prax.text. I ran ingredient_name: '200 g de farine, tamisée' gave ['de farine'], "2 gousses d'ail hachées" gave ["gousses d'ail hachées"], '1 pizca de sal' gave ['pizca de sal'], '3 uova sbattute' gave ['uova sbattute']. English and German lines come out clean ('garlic', 'Zwetschgen'). store/maintain.py markup_facts (line 731) and _markup (line 802) link each name as `calls_for … ingredient` with producer jsonld, EXTRACTED, from the nightly maintain pass. No model or review sees them.

**Why here.** Independence (language and domain): a stranger's French or Italian recipe pages fill the graph with ingredient entities named by the measure and the preparation. CLAUDE.md puts a domain's words in its pack and the words that say what a name is in the lexicon.

**Fix** (an hour to half a stage). Move the measure, preparation and conjunction words into a lexicon section, or into the kitchen pack beside kitchen.yaml, keyed by language. When the page's language (meta.lang) has no word list, emit no `calls_for` edges rather than raw lines; the extraction can still read the recipe.

**Checked by the skeptic.** Reproduced. ingredient_name gives ['de farine'], ["gousses d'ail hachées"], ['pizca de sal'] and ['uova sbattute'], and English lines come out clean. store/maintain.py markup_facts links every r.ingredients as calls_for … ingredient under producer jsonld, and the nightly _markup pass writes them EXTRACTED with no model or review. French and Spanish recipe sites routinely publish JSON-LD recipeIngredient, so this fires on real pages. The kitchen words (_PREP etc.) are code in prax.text, not in the kitchen pack or the lexicon.

## 9. venues.py keeps its own lists of venue words, publishers and legal forms beside the lexicon that already holds them

medium cost, generality, step 3. `src/prax/graph/venues.py:61`.

**Evidence.** venues.py:61 VENUE_WORDS, :123 _PUBLISHERS {ieee, acm, aes, springer, elsevier, wiley}, :55 _NOT_ACRONYM (GMBH, KG, AG, INC, LTD, LLC…), :29 _LEADING (proceedings…, German 'tagungsband'), and :48 _WHEN (English and German months and semesters) are Python sets. ontology/lexicon.yaml already holds the same kinds of words: `by_type.venue` (proceedings, symposium, journal of, conference on…, line 104), `not_a_venue.publisher` (springer, elsevier, wiley…, line 318), and `not_a_venue.company` (gmbh, inc, ltd, llc, ag, ohg, bv…). venues.not_a_venue reads the lexicon but also tests VENUE_WORDS first, and lexicon.yaml:312-315 points readers back at `prax.graph.venues.VENUE_WORDS`. So one concept, 'which words say what a name is', lives in two places that only partly overlap (the lexicon's company forms include 'co. kg', 's.a.', 'sarl'; _NOT_ACRONYM does not).

**Why here.** CLAUDE.md invariant 9: 'The words that say what a name is … are ontology/lexicon.yaml … not patterns in the module that reads them.' The case is real today: lexicon.yaml already has venue, publisher and company cues. A library in another language (a French historian's 'actes du colloque', 'revue') or a new legal form added to the lexicon changes not-venues and container-entities but not the series reading or acronym matching. The two then disagree on whether a name is a venue.

**Fix** (a few hours). Move VENUE_WORDS, _PUBLISHERS, the legal forms, the leading 'proceedings of' words and the season and month words into lexicon.yaml sections. venues.py then reads them through ontology.lexicon(), as not_a_venue already does for its own cues.

**Checked by the skeptic.** Confirmed. venues.py holds the venue words in Python: VENUE_WORDS (:61), _PUBLISHERS (:123), _NOT_ACRONYM (:55, legal forms), German/English _WHEN (:48) and the _LEADING regex (:29, which includes 'tagungsband'). lexicon.yaml already keeps by_type.venue, not_a_venue.publisher and not_a_venue.company, with forms that only partly overlap ('co. kg', 's.a.', 'sarl' are not in _NOT_ACRONYM). not_a_venue (venues.py:361) tests VENUE_WORDS before the lexicon, and lexicon.yaml:312-315 points back at the code set. CLAUDE.md invariant 9 says such cue words belong in the lexicon and not in the module that reads them. A new language or a new legal form added to the lexicon would reach not-venues but not series reading or acronym grouping.

## 10. The ASCII-fraction fix went into schema.org's ingredient_name only; the general ingredient parser still misreads '1/2 cup'

medium cost, generality, step 1. `src/prax/text/schemaorg.py:176`.

**Evidence.** 124e2b2 ('fractions', finding 20) added _FRACTION (schemaorg.py:176) and strips it in ingredient_name (:179) before calling ingredients.parse_item. ingredients._AMOUNT (src/prax/text/ingredients.py:135) still knows only decimals and Unicode vulgar fractions. Ran with the venv: ingredients.parse_item('1/2 cup milk') -> {'amount': 1.0, 'item': '/2 cup milk'} with no unit; parse_item('1 1/2 tbsp sugar') -> {'amount': 1.0, 'item': '1/2 tbsp sugar'}. schemaorg.ingredient_name gives ['milk'] and ['sugar'].

**Why here.** The second case is real and is the main one: every recipe's `ingredients` chunk gets its per-line amount and unit from ingredients.parse_item (CLAUDE.md Conventions: the data is kept so a later pass can answer 'the same for six people'). English recipes write '1/2 cup' as a matter of course, so their chunk data carries wrong amounts and items that start with '/2'. A search on the item and any scaling pass inherit the error.

**Fix** (an hour plus a rechunk of recipes). Move the ASCII fraction (and 'n m/d') into ingredients._AMOUNT and _number, so parse_item returns amount 0.5 or 1.5 with its unit. Drop the separate _FRACTION strip from schemaorg.ingredient_name. Then run `prax maintain --rechunk` for recipes.

**Checked by the skeptic.** Re-ran with the venv. ingredients.parse_item('1/2 cup milk') gives amount 1.0, item '/2 cup milk' and no unit. parse_item('1 1/2 tbsp sugar') gives amount 1.0, item '1/2 tbsp sugar'. '½ cup milk' parses correctly (0.5, cup). _AMOUNT (ingredients.py:135) knows no ASCII fraction. The 124e2b2 fix added _FRACTION only in schemaorg.ingredient_name (:176-185) and strips it there, so the general parser that fills every ingredients chunk's data (amount, unit, item) still carries the error. Scaling a recipe and searching for an item inherit it.

## 11. store.document_node is the new single answer to 'a document as a graph node', but pages and the references pass still hard-code 'paper'

medium cost, generality, step 3. `src/prax/store/pages.py:325`.

**Evidence.** b93e0a8 added store.document_node (src/prax/store/graph/edges.py:254-282): a document's title plus the document subtype its entity already has, else 'document'. Sync's links_to (capture/projects.py _keep_in_step), the markup pass (maintain.py:837) and traverse doc:N (traversal.py:331) use it. pages.write_page (pages.py:325-329), pages._link_edges (pages.py:398-402) and the part_of edge (pages.py:596) still type every non-page target as 'paper', and so does maintain.link_references (maintain.py:418: Edge(title, 'paper', 'cites', name, 'paper')). Entities are keyed by (name, type) (edges.py:140-158), so a page annotating a recipe, a datasheet or a video creates a second 'paper' entity with that title, apart from the node document_node and the extraction use.

**Why here.** Two mechanisms for one concept (checklist: two notions of the same entity). Pages linking captured web pages and recipes are common; CLAUDE.md treats every page link as an `annotates` edge. A walk from doc:N lands on whichever twin has more edges and reports the other only as a 'sense', so a page's annotations or a document's citations can drop out of the document's neighbourhood.

**Fix** (an hour or two, plus a heal or resolve round for the twins). Route the target typing in pages.write_page, pages._link_edges, the part_of edge and maintain.link_references through store.document_node. Existing 'paper' twins can then be folded by a sure-tier merge or a heal check.

**Checked by the skeptic.** Confirmed. store.document_node (edges.py:254-282) gives a document's node type as page, else the document subtype its entity already has, else 'document'. pages.write_page (pages.py:325-329), _link_edges (:398-402) and the part_of edge (:596) still type a non-page target as 'paper', and so does maintain.link_references (maintain.py:418). The entity lookup (edges.py:140-152) is keyed by name and type, and a label fallback only matches within the same type, so a page annotating a recipe or a datasheet makes a second 'paper' entity with that title. pages sits after graph in the store order and maintain already imports document_node, so nothing stops these callers from using it.

## 12. connect_entities scans the whole edges table on every call, under _HELD_LOCK, just to read the index stamp

medium cost, performance, step 1. `src/prax/store/graph/paths.py:31`.

**Evidence.** On the test store (300 K edges, 75 K ended), EXPLAIN QUERY PLAN of `SELECT max(id), max(valid_to) FROM edges` gives `SCAN edges`: 61.6 ms. Putting both aggregates in one SELECT turns off SQLite's min/max optimisation. The partial index idx_edges_ended (WHERE valid_to IS NOT NULL) is only used when the query states that condition. `SELECT (SELECT max(id) FROM edges), (SELECT max(valid_to) FROM edges WHERE valid_to IS NOT NULL)` plans as two index searches (`SEARCH edges USING COVERING INDEX idx_edges_ended`): 0.007 ms. Measured end to end, a cached path_index call took 30 ms and a whole connect_entities call 101 ms, so the stamp is a large share of it. The stamp is read inside `with _HELD_LOCK`, so concurrent connect calls queue behind each other's scans.

**Why here.** This is the connect MCP tool (stage AM), an agent request. The index is cached so that a call costs little, but every call still pays a scan that grows with the edges, which only ever grow (append-only, migration 35).

**Fix** (an hour). Read the stamp as two scalar subqueries, with `WHERE valid_to IS NOT NULL` on the max(valid_to) side.

**Checked by the skeptic.** Confirmed. paths.py:31 `SELECT max(id), max(valid_to) FROM edges` plans as `SCAN edges` (12.6 ms on 300 K bare rows in memory; real rows are wider). The two-subquery form with `WHERE valid_to IS NOT NULL` plans as a rowid search plus `COVERING INDEX idx_edges_ended`: 0.013 ms, same values. _stamp runs inside _HELD_LOCK on every call before the freshness check, so concurrent connect calls queue behind the scan. Edges are append-only, so it only gets worse.

## 13. connect with as_of builds the whole path index on every request: about 1 s and a 148 MB peak each, with no bound on concurrent builds

medium cost, performance, step 1. `src/prax/store/graph/paths.py:62`.

**Evidence.** On the test store (300 K edges, 161 K entities): `path_index(con, as_of='2026-06-01')` took 0.90 s with a tracemalloc peak of 148 MB, and the whole `connect_entities(..., as_of=...)` took 1.01 s, against 0.10 s without as_of. The as_of branch returns `paths.build(_rows(con, as_of=as_of))` outside `_HELD_LOCK` and keeps nothing. Every as_of call pays the full build, and two at once hold two peaks. The checklist's own figure for this build is 194 MB on the library.

**Why here.** Invariant 7 caps the serving path at 1 GB resident. connect(as_of=) is exposed through the MCP tool (mcp_server.py:316). Two or three agent calls with a date put a Pi-class door at 300-600 MB of transient allocation, on top of the memory-mapped vectors.

**Fix** (an hour). Cache the last as_of index or two, keyed by (as_of, stamp). Also serialise as_of builds behind `_HELD_LOCK` or a semaphore, so concurrent calls cannot stack their peaks.

**Checked by the skeptic.** As described: path_index's as_of branch (paths.py:62) calls paths.build on every call and keeps nothing. The module docstring says it keeps none, but nothing bounds the cost or how many builds run at once. I did not rebuild the 300 K-edge store. The checklist's own library figures for this build (1.2 s, 194 MB peak) support the reviewer's 0.9 s and 148 MB. as_of reaches the build from the MCP connect tool (mcp_server.py:208). Several dated calls at once each hold their own peak. That is transient memory, not resident, so it costs less than the reviewer suggests, but it is real on an 8 GB board shared with mmapped vectors.

## 14. cite_link's 100 ms budget is checked only between steps, and one step can take far longer: folding a book, or a library-wide FTS phrase query

medium cost, performance, step 1. `src/prax/store/retrieval/fusion.py:261`.

**Evidence.** For documents of 3,000 chunks or fewer (CITE_LOCAL), the whole document is read and folded in one go (line 228). Folding 3,000 chunks of about 1.4 KB (4.2 MB of text) took 83.5 ms on the desktop, which is most of CITE_BUDGET before the next late() check. For longer documents the phrase query `chunks_fts MATCH ? AND c.doc_id = ? LIMIT 2` plans as `SCAN chunks_fts VIRTUAL TABLE INDEX 0:M1` and filters by doc_id only after FTS has walked the matches across the whole library. On the 1.42 M-chunk test store, a phrase that every chunk holds took 1,117 ms. That is the worst case, because the test text is uniform; real cost depends on how common the phrase's terms are. Bounding the query by the document's chunk-id range (`AND chunks_fts.rowid BETWEEN ? AND ?`, from min/max of chunks.id for the document, then the doc_id filter) plans as `INDEX 0:M1><` and took 0.9 ms on the same query, with the same rows.

**Why here.** This runs on every agent-shaped search (cite=True) and in ask's passages. CITE_BUDGET is meant to cap the added latency at 100 ms. On a Pi-class CPU, one book among the hits can add several hundred ms past it.

**Fix** (an hour). Always run the phrase query bounded by the document's rowid range. That makes it cheap for every document, so the in-memory fold can go, or CITE_LOCAL can drop to a few hundred. Failing that, check late() while folding.

**Checked by the skeptic.** Confirmed for the fold. late() runs only between steps. The CITE_LOCAL fold at fusion.py:228 is one step. Folding 3,000 synthetic chunks of 1.43 KB with the real _FOLD regex took 102 ms on the desktop, the whole CITE_BUDGET before the first check, and several times that on a Pi. The phrase query does plan as `SCAN chunks_fts VIRTUAL TABLE INDEX 0:M1` with doc_id filtered afterwards. But on a 600 K-chunk Zipf-vocabulary FTS test it took 0.4-24 ms, not 1.1 s; the reviewer's uniform-text worst case overstates it. The proposed rowid BETWEEN bound got a worse plan in my test (30-1,100 ms), so the fix needs measuring and is not a given. The finding stands mainly on the fold overrunning the budget.

## 15. Venue rules keep the words that say what a name is as code sets, partly copied from the lexicon

low cost, hygiene, step 3. `src/prax/graph/venues.py:35`.

**Evidence.** venues.py holds these word lists as Python sets:
- `_ABBREVIATIONS` (35), `_STOP` (46), `_WHEN` (48: English and German months and semesters) and `_NOT_ACRONYM` (55: GMBH, AG, INC, LTD, LLC…).
- `VENUE_WORDS` (61), `_EVENT_WORDS` (67), `_PUBLISHERS` (123: ieee, acm, aes, springer, elsevier, wiley) and `_KIND_ONLY` (125).
- The `_LEADING` regex (29), with German 'tagungsband', 'zur', 'der'.

The lexicon already carries several of the same words: publishers (springer, elsevier, wiley) and legal forms (gmbh, ag, inc, ltd, llc) in `not_a_venue` (ontology/lexicon.yaml:316-330). Its comment at line 313 points at `prax.graph.venues.VENUE_WORDS` in code as the authority. Month names are a third copy beside `text/dates._MONTHS`, which also has French.

**Why here.** CLAUDE.md invariant 9: the words that say what a name is live in `ontology/lexicon.yaml`, not in the module that reads them. The checklist cites venue and publisher cues as having moved there. A library in another language, or with other publishers, has to edit core code. A cue added to the lexicon's publisher list does not reach `_PUBLISHERS`. The module is also research-only (`published_in`) yet sits in core `prax.graph` rather than the research pack.

**Fix** (a stage (small)). Move VENUE_WORDS, _EVENT_WORDS, _KIND_ONLY, _PUBLISHERS, _NOT_ACRONYM, _ABBREVIATIONS and the leading-phrase words into a `venues:` section of the lexicon (or the research pack's lexicon). Read them through `ontology.lexicon()` and `cue_pattern`, as `not_a_venue` already does. Take the month names from one place.

**Checked by the skeptic.** Confirmed. venues.py (added in this range, 9804e70) holds _ABBREVIATIONS, _STOP, _WHEN (English and German months and semesters), _NOT_ACRONYM (legal forms), VENUE_WORDS, _EVENT_WORDS, _PUBLISHERS and _KIND_ONLY as code sets. lexicon.yaml:316-330 not_a_venue already lists springer, elsevier, wiley and gmbh, inc, ltd, llc, ag, bv. Its comment points at venues.VENUE_WORDS in code as the authority. CLAUDE.md invariant 9 puts such cues in lexicon.yaml, and a cue added to the lexicon's publishers does not reach _PUBLISHERS. Cost is low to moderate: the lists do partly different jobs (a publisher as the prefix of a series name), but other languages and other publishers need code edits.

## 16. A second JSON-LD reader in text/dates, which misses what schemaorg.nodes handles

low cost, hygiene, step 2. `src/prax/text/dates.py:157`.

**Evidence.** `_jsonld_dates` (dates.py:157-178) reuses `schemaorg.JSONLD` but writes its own json.loads and tree walk. `schemaorg.nodes` (schemaorg.py:87-112) does that walk already. Unlike `nodes`, `_jsonld_dates` does not strip a `<!-- … -->`-wrapped block, so such a page's recipe is read and its date is not. It also ignores `schemaorg.own()` (the page's own object by OWN_TYPES) and takes the first `datePublished` anywhere in the tree, so 'the page's own object' has two definitions.

**Why here.** `meta.published` from schema.org (the CLAUDE.md conventions on dates) and the schema.org capture of pages and recipes. A fix to JSON-LD parsing reaches one reader and not the other.

**Fix** (an hour). Build `_jsonld_dates` on `schemaorg.nodes(head)`. Prefer `own(found)`'s datePublished, then the others in order.

**Checked by the skeptic.** Confirmed. dates._jsonld_dates (dates.py:157-178) reuses schemaorg.JSONLD but has its own json.loads and walk. It does not strip <!-- --> around a block, as schemaorg.nodes does at line 102, and it takes the first datePublished anywhere in the tree, not schemaorg.own()'s object. schemaorg.Said.date (schemaorg.py:234) already reads datePublished from the page's own object, so there are two readers with two definitions of 'the page's own object'.

## 17. Two patterns for a display formula's equation number, which disagree

low cost, hygiene, step 2. `src/prax/packs/maths/check.py:51`.

**Evidence.** Two patterns strip a display formula's equation number:
- `markup.EQ_NUMBER` (text/markup.py:154). It accepts `\quad|\qquad|\hfill|\tag` before the number, and `chunking.parse_formula` (text/chunking.py:344) uses it to split `data.number` off `data.latex`.
- The maths pack's own `TAG` (check.py:51), which `sides()` applies to the same `data.latex` through formulas._links. It accepts `, (3)`, `~(3)` and two spaces before `(3)`, which EQ_NUMBER does not, and does not accept `\hfill`.

So for `$$ a = b, (3) $$` the chunk keeps `data.number = None` with the number still in its latex, and only the checker knows it is a number.

**Why here.** CLAUDE.md: `prax.text.markup` is the one place that matches the format prax writes, a display formula and its number included. A module that reads it never writes the pattern again. Both the formula chunks (`data.number`, which prose refers to) and the `equations` step depend on it.

**Fix** (an hour plus a rechunk). Widen `markup.EQ_NUMBER` to the separators TAG learned on 2026-10-02 and have check.sides use it, keeping TAG only for model answers if they really differ. Then `prax maintain --rechunk` refills `data.number`.

**Checked by the skeptic.** Confirmed. markup.EQ_NUMBER (markup.py:154) requires \quad, \qquad, \hfill or \tag, and chunking.parse_formula uses it to set data.number. maths check.TAG (check.py:51) accepts ', (3)', '~(3)' and two spaces, and lacks \hfill. For '$$ a = b, (3) $$' the chunk keeps number=None with '(3)' left in its latex. Only the checker strips it. CLAUDE.md names prax.text.markup as the one matcher of the display-formula format, so this is a second pattern that disagrees with it.

## 18. The stale states and the status-to-relation map defined in three places

low cost, hygiene, step 2. `src/prax/store/retrieval/fusion.py:272`.

**Evidence.** The same stale states and their mapping to relations are written in three places:
- `fusion.STALE_STATES = ("retired", "superseded", "invalid", "deprecated")` and `STALE_RELS = {"supersedes": "superseded", "invalidates": "invalid"}` (fusion.py:272-273).
- `text/status.STALE = frozenset({…same four…})` (status.py:37).
- `capture/projects.STATUS_EDGE = {"invalid": "invalidates"}`, with supersedes as the default (projects.py:348), which is STALE_RELS inverted.

The store may import `prax.text` at module level.

**Why here.** Staleness in retrieval (STALE_SHIFT, `include_stale`) and the project sync's status lines. A new state, such as 'withdrawn' or 'retracted' as a state of its own, or a new relation, has to be added in three modules. If one is missed, a note the sync marks stale is not moved down in search, or the other way round.

**Fix** (under an hour). Keep STALE and the state-to-relation map in `text/status.py`, and import them in fusion.py and capture/projects.py.

**Checked by the skeptic.** Confirmed. The same four stale states are in fusion.STALE_STATES (fusion.py:272) and text/status.STALE (status.py:37). The relation map is in fusion.STALE_RELS and, inverted, in capture/projects.STATUS_EDGE (projects.py:348, with supersedes as the default). The store may import prax.text at module level, so fusion could take status.STALE. A new stale state or a new relation has to be added in three modules, or a note the sync marks stale is not moved down in search.

## 19. Path hop strength is a Python list of relation names from the packs, and it has already drifted from the ontology

low cost, plan, step 3. `src/prax/graph/paths.py:38`.

**Evidence.** STRONG and WEAK name relations from seven modules: research (cites, extends…), kitchen (calls_for, makes, variant_of), studio (describes, succeeds), workshop (derived_from), computing (written_in) and core. The list is in a core module that is not part of any pack. A grep of ontology/*.yaml and src/prax/packs/*/*.yaml finds no module that declares 'evaluates', which is in STRONG. No society relation is listed, so every one of them, and every relation of the planned family module, costs MEDIUM (2.0) without anyone having decided that. The ontology already carries per-relation annotations that bump no version and that ontology.lint checks (transitive, symmetric, functional, inverse_of, kind, same_as). hop strength is the same kind of fact about a relation.

**Why here.** CLAUDE.md invariant 9 / packs: 'A family module comes later', and society.yaml exists with no rules yet. Every new pack or relation has to edit paths.py, or its relations default to MEDIUM. G1 also rests on this cost model, and scripts/eval_paths.py measures SOUND against these weights.

**Fix** (an hour or two plus an eval run). Add a `strength: strong|weak` relation annotation, read by ontology.Relation (no version bump, the same as same_as), and have lint refuse an unknown value. Move the names into the modules that declare the relations, and build STRONG/WEAK from ontology.current(). Remove 'evaluates' or declare it. Run eval_paths.py again to confirm nothing moved.

**Checked by the skeptic.** Confirmed. STRONG and WEAK are hardcoded in graph/paths.py:38-47. A grep of ontology/*.yaml and src/prax/packs/*/*.yaml finds no module that declares 'evaluates', so that entry is dead and already out of step with the ontology. The relations of society.yaml (advocates, opposes, influenced_by, enacted_by, regulates) are in neither set, so they cost MEDIUM without anyone having decided that. The same will be true of the planned family module. The ontology already carries per-relation annotations that bump no version and that lint checks (kind, transitive and the rest), so hop strength belongs there and should not have to be edited in core code for every pack.

## 20. The card plan's swaps are recorded only as log prose, so AI's last step has no data to measure

low cost, plan, step 4. `src/prax/host/up.py:587`.

**Evidence.** _swap and _unswap report a swap only through _say(), which writes a free-text line ('{group}: {to} takes it instead of …') to logs/up.log. Load times got a structured record (process.LOADS = 'loads.json', the last five kept). Swaps and their reasons ('the plan: …') have no structured record. Reading waits are measurable from readings.at/finished_at, but swaps per day can only be counted by grepping prose, and that breaks as soon as the wording changes. The plan has been acting since 2026-10-02 (135f3da), so no baseline from before it is being kept either.

**Why here.** PLAN AI, its one open step: 'Measured: how long readings wait before and after, and how many swaps a day the plan makes. Fewer swaps for the same waits is the point.' The greedy-with-hysteresis plan is meant to be judged, and perhaps replaced by the cost graph, on that number.

**Fix** (an hour). Have _swap/_unswap append one JSON line per swap (at, group, from, to, why, plan decision) to run/, in the same way as loads.json, bounded or rotated. Add a small script or status field that counts swaps per day and pairs them with reading waits.

**Checked by the skeptic.** Confirmed. _swap (up.py:587-628) and _unswap report a swap only through _say(...) as free text ('{group}: {to} takes it {how} {others} (back when ...)'). Load times have a structured file (LOADS, loads.json, kept by _record_load), but swaps and their reasons have none, and the status file keeps only the current loan. PLAN AI's one open step (PLAN.md:146-148) is to measure 'how many swaps a day the plan makes'. That can now only be counted by grepping log prose, which breaks when the wording changes, and the swaps made since the plan began acting are not being kept in a form anyone can measure.

## 21. Path costs class relations as strong or weak by a hard-coded list of research and studio relation names

low cost, independence, step 3. `src/prax/graph/paths.py:38`.

**Evidence.** STRONG lists relation names from several packs (cites, proposes, calls_for, makes, advised_by, written_in…) and WEAK lists mentions and annotates. Every other relation costs MEDIUM_COST=2.0. A new pack's relations (society, the planned family module) are always medium, whatever they mean. The ontology already has per-relation attributes (`kind`: state/event/eternal, ontology.py:101) but no strength. scripts/eval_paths.py re-measures SOUND (and HUB) only on citation pairs, topic-page pairs, random paper pairs and recipe-paper pairs. So the strong list is measured only for research.

**Why here.** Domain words outside their pack: a library mostly about another domain gets connection answers weighted for research.

**Fix** (an hour). Add an optional `strength: strong|weak` to relations in the YAML modules (it bumps no version, like `kind`), and have paths read it from ontology.current(). Keep the current list as the default until the packs carry it.

**Checked by the skeptic.** Confirmed. STRONG in graph/paths.py mixes relations from core, research and craft (calls_for, makes, written_in). Every other relation costs MEDIUM_COST=2.0, so a strong relation added in a new pack doubles its hop cost against SOUND=6 unless someone edits paths.py. Nothing in the ontology or the pack docs points there. eval_paths measures only research pairs and recipe-paper pairs. The default is a sane middle, so the cost is modest: weaker connection answers in other domains.

## 22. Which schema.org types prax knows is listed twice: OWN_TYPES in code, same_as in genres.yaml, and the two disagree

low cost, generality, step 3. `src/prax/text/schemaorg.py:32`.

**Evidence.** schemaorg.own picks a page's own object only from OWN_TYPES (schemaorg.py:32-47, 113-128). ontology/genres.yaml:94-111 maps genres to schema.org types and says 'The `markup` pass reads it backwards: a page whose own schema.org type is one of these suggests its genre'. maintain.py:830-833 does that through label_for. Thesis, LearningResource, QAPage, Message, Invoice and SoftwareSourceCode are in genres.yaml but not in OWN_TYPES. Ran with the venv: schemaorg.of_page on a JSON-LD {'@type':'QAPage', author Ann Bee} -> None; the same with 'Article' -> a Said with the author. markup_facts (maintain.py:731) also hand-codes author->authored_by and publisher->published_by, although core.yaml declares those mappings as `same_as: schema:author` and `same_as: schema:publisher`.

**Why here.** The AN stage annotated the ontology and genres with same_as as the place where schema.org correspondences live. A genre or relation someone adds there is silently ignored by the markup pass that the file says reads it: no genre suggestion and no author or publisher facts for Q&A pages, theses or source-code pages.

**Fix** (an hour). Derive the accepted own types from ontology.genres().same_as plus the few extra types that carry facts but no genre (BlogPosting, VideoObject, Product…), or at least assert in a test that every genre's same_as type is in OWN_TYPES. Reading the author and publisher relations from the relations' same_as is optional at this size.

**Checked by the skeptic.** Re-ran with the venv. schemaorg.of_page on JSON-LD whose @type is QAPage, Thesis or SoftwareSourceCode returns own=None; with Article it returns a Said carrying the author. OWN_TYPES (schemaorg.py:32-47) lacks Thesis, LearningResource, QAPage, Message, Invoice and SoftwareSourceCode. genres.yaml:90-111 says the markup pass reads same_as backwards to suggest a genre, and maintain.py:830-833 does so through label_for, so those genres never get a suggestion and those pages give no author or publisher facts. The cost is low: those pages are rarer.

## 23. Functional conflicts are computed twice, by heal and by the conflicts pass, with different rules for which edges count

low cost, generality, step 2. `src/prax/store/repair/graph.py:128`.

**Evidence.** repair/graph.py:128-176 (_functional_conflicts, the heal ailment 'functional-conflicts', ailments.py:73) recomputes conflicts from live edges with its own SQL. rules.py:420-462 (_functional_pairs, the nightly conflicts pass that fills edge_conflicts, 83e19bc) does it again with a different filter: it excludes producer 'rule:%' edges and picks one edge per value. Both share same_answers and part_of_ancestry, but heal includes INFERRED rule edges and the table does not. Today only published_in is functional and nothing in the ontology derives it, so the two agree by accident.

**Why here.** Two mechanisms for one concept (stage AN: 'a constraint's breach is a finding for a person'). When a second functional relation or an inverse_of on a functional one arrives (the ontology lint already allows both), heal's count and the conflicts the walk and `why` show (traversal.py:597, api/graph.py:198) will differ. A person will then see conflicts in heal that the Review data has no row for.

**Fix** (an hour). Make the heal ailment read the open rows of edge_conflicts (grouped by subject), or have both call _functional_pairs, so one definition of 'two facts that cannot both hold' exists.

**Checked by the skeptic.** Confirmed. repair/graph.py:128-176 and rules.py:420-462 each recompute functional conflicts with their own SQL. rules excludes producer 'rule:%' edges and keeps one edge per value; heal counts every live edge, INFERRED rule edges included. Only research.yaml:118 declares a relation functional (published_in), and no rule derives it, so the two agree today. A rule that derived a functional relation would make heal show conflicts that edge_conflicts and the walk/why do not have. The cost is low and arrives later.

## 24. A named token's hidden set is rebuilt by a documents scan each time it is needed, and staleness adds a second build to every search

low cost, performance, step 1. `src/prax/store/retrieval/fusion.py:321`.

**Evidence.** hidden_documents (base.py:331) runs `SELECT id, json_extract(meta, '$.domains') FROM documents` over every row whenever a named token restricts domains. On the 12,900-document test store, with a domain-restricted Viewer, it took 18.3 ms per call. A search now calls it in _search_hits (line 465) and again in staleness (line 321, new in this range). connect_entities, references_of and cited_but_missing (all new) each call it once more. The administrator token pays nothing, because VIEWER is None.

**Why here.** Stage U: named tokens are what a second client or agent uses. The scan grows with the library on every request they make.

**Fix** (an hour). Compute the hidden set once per request and keep it in a context variable next to VIEWER. Or pass it from _search_hits into staleness.

**Checked by the skeptic.** Confirmed in code. For a domain-restricted Viewer, hidden_documents (base.py:345) scans every document with json_extract, and one search calls it twice: fusion.py:465 in _search_hits and fusion.py:321 in staleness. connect_entities (paths.py:120), references_of (reads.py:1038) and cited_but_missing (reads.py:1116) each call it once more. I did not re-time the 18 ms. It is plausible for 12,900 JSON metas. The administrator token pays nothing, so the cost is low but grows with the library.

## Refuted

- The references pass's match score lives only in the evidence string, so the path cost (G1) and 'confidence as a number' have nothing to read (plan): Refuted on its main claims. The score is not kept only in the evidence string: link_references also writes it as `score` (with `how`) into each entry of meta.references.links (maintain.py ~409-416), which a rechunk restores. The edges being immutable is not a dead end either. CLAUDE.md invariant 8 makes 'retire_run plus a new pass' the documented way to upgrade a producer's work, and the references pass is cheap to re-run. G1 itself is a one-line change to the confidence mapping in this pass (a title match near 1.00 becomes EXTRACTED), or a later numeric column filled from meta.references.links. Paths do read only the class, but nothing about the shape of this code makes G1 or 'confidence as a number' more costly. The note that evidence holds three kinds of text is about wording, not a cost.

- part_of direction check gates the rule pass's premises on a 20-sample hand measurement; no script re-measures it (independence): Its cues are data in lexicon.yaml, which the design allows (the checklist names the lexicon as extendable data), and on a library in another language no cue fires, so the check does nothing. A verdict only routes the triple to the review queue, where a person sees it, and keeps it out of rule premises. Only reversed/misfit are ever mended, and only by an explicit `prax heal --apply`. A missing re-measurement for a check that fails safe toward human review is not a cost someone will pay.
