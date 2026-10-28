# The quality review of 2026-10-28

The project's skill `.claude/skills/code-review` in full mode, over
everything changed in `src` since 2026-10-19 (about a hundred commits:
photos, places, faces, events, people, the rights of named tokens),
after the safety review of the same day
(`docs/eval/safety-review-2026-10-28.md`): five reviewers, one a
dimension, each finding re-checked by a skeptic. 23 confirmed, 3 rejected.
The user asked on 2026-10-28 for every finding to be fixed. Each
says below what was done. The tests are in
`tests/test_quality_2026_10_28.py`, numbered as here, and beside the
code they change.

## 1. The faces store is spread over four parts, and the part in `retrieval` writes again what `graph` owns

medium cost, hygiene, fixed: the graph's side of faces is `store.graph.faces` (the answers carried to a face read again, the groups, the matching, a picture's faces), the vector side stays in `retrieval.faces`, and a face's live answer is one SQL fragment, `naming.LIVE_ANSWER`. `src/prax/store/retrieval/faces.py:454`.

**Evidence.** The `face_names` table is written in two packages: `documents/photos.py:303` (`_carry_answers` INSERTs into face_names and face_refusals) and `graph/naming.py`. It is read in four: base.py:617, retrieval/faces.py, graph/naming.py and documents/photos.py. The join for a face's live answer as its canonical person (`LEFT JOIN face_names n ... AND n.ended_at IS NULL LEFT JOIN entities x ... LEFT JOIN entities e ON e.id = COALESCE(x.canonical_id, x.id)`) is written three times: `naming.face_answers` (naming.py:362), `retrieval/faces._group_faces` (454) and `picture_faces` (862), plus a fourth variant in `_matched` (691). `retrieval` comes before `graph` in the store's ORDER, so retrieval/faces.py cannot import `face_answers`, `DEPICTS` or the events part. It therefore hard-codes `'recorded_at'` (523, 543) and the `type = 'event'` and canonical-entity reads (`_event_docs`, `_events_with_faces`) that `graph/events.py` and `graph/people.py` also hold. Found by grepping face_names, recorded_at and the COALESCE canonical join across src/prax/store.

**Why here.** Plan item AT is still growing (step 5b offers, small faces, the next naming features). Every change to what an answer is (a new verdict, a column on face_names, how a merged person shows) has to be made in three or four places in three packages. This review already found one of those drifting: the 2026-10-28 fix that carried answers to the new faces lives in documents/photos.

**Fix proposed** (a few hours (move, re-export, tests unchanged)). Keep the vector side in retrieval/faces: index path, apply/save/reconcile, `similar_faces`, `face_neighbours`. Move what reads answers, groups, matches and events into a graph part after `naming` (for example `graph/faces`): `faces_page`, `face_groups`, `face_group`, `face_matches`, `picture_faces`, `replace_face_groups`. There they share one live-answer SQL fragment and the `DEPICTS`/events constants. `_carry_answers` should call a naming helper rather than INSERT itself, or naming should own both tables' writes. The store `__init__` re-exports every name, so no caller changes.

**Checked by the skeptic.** Confirmed. The live-answer-as-canonical-person join (face_names n ... LEFT JOIN entities x ... e.id = COALESCE(x.canonical_id, x.id)) appears at graph/naming.py:361-365, retrieval/faces.py:451-456 and retrieval/faces.py:860-864, with a variant at retrieval/faces.py:690-692. retrieval/faces.py imports only from base and retrieval.vectors, because retrieval comes before graph in the store's ORDER. So it hard-codes 'recorded_at' (523, 543) and type = 'event' (513), which graph/events.py owns. face_names is written in documents/photos.py (_carry_answers, around line 303) and in graph/naming.py, and read in four modules. A change to what an answer is has to be made in three packages.

## 2. The door-only meta keys are a second list beside `DocumentMeta`, with no test tying the two together

medium cost, hygiene, fixed: `store.CLIENT_META` names what a client may send, and `DOOR_WRITTEN_META` is every other declared key, so a key a new step declares is the door's until named for a client. `/ingest` refuses the rest. `src/prax/store/base.py:230`.

**Evidence.** `DOOR_WRITTEN_META` (base.py:230-250) lists 18 keys by hand. `DocumentMeta` (base.py:150-221) already declares every key with its writer in a comment, and docs/meta.md does the same. Keys the door's passes write that the new list leaves out include `parse_history`, `pages`, `sections`, `world_dates`, `extraction_history`, `extraction_error` and `summary_run`. `grep -rn DOOR_WRITTEN_META tests` finds nothing, so no test fails when a step adds a door-written key (as AT's photo, taken, place and faces did) to DocumentMeta only.

**Why here.** The list was added because a client-sent `meta.photo.thumb` reached a file read (the 2026-10-28 review). Each new step adds a meta key, and the next one has to be remembered in two places.

**Fix proposed** (an hour). Make the writer part of the declaration. Either keep a `CLIENT_META` set of the keys a client may send and refuse the rest, which fails closed for a new key, or add a test that every DocumentMeta key is classified as client or door-written.

**Checked by the skeptic.** Confirmed. DOOR_WRITTEN_META (base.py:230-250) is a hand-written list beside DocumentMeta, whose comments already name each key's writer. No test references DOOR_WRITTEN_META or door_written; grepping tests finds nothing. Its only use is api/capture.py:54. Door-written keys such as parse_history, pages, sections, world_dates, extraction_history and extraction_error are missing from it. The next step that adds a door-written key that names a file or affects what the wall shows has to remember a second list, and nothing fails when it is forgotten.

## 3. The face recogniser is wired into the code, though the plan says a host can choose it with `faces.model`

medium cost, plan, fixed: `prax.ml.faces.RECOGNIZERS`, with SFace its one entry; `faces.model` in prax.yaml (`PRAX_FACES_MODEL`) chooses, `store.face_model` answers, the index file and the vector's length are the model's, and the hand-out tells the worker which model to read with. An unknown name is an error. `src/prax/store/retrieval/faces.py:50`.

**Evidence.** PLAN AT step 2 says: "A host may name one in `prax.yaml` (`faces.model`)", with InsightFace at 512 dimensions as the example. grep for `faces.model` across src/prax and docs/howto.md finds it only in a comment (ml/faces.py:210). Nothing reads the setting. The model is fixed in four places: `FACE_MODEL = faces_ml.RECOGNIZER` (retrieval/faces.py:50) is the default of every face read; `face_index_path` gives every model's file the module's `faces_ml.DIM` (128, line 61); `_checked_vector` refuses any length but that DIM (line 98); and `FaceVectors.take_in` refuses any model but `store.FACE_MODEL` (steps/faces.py:151). The SFace repo, file, sha256, 112 px alignment template and DIM are module constants in ml/faces.py:211-226, so a second model has nowhere to go.

**Why here.** Plan item AT step 2 ("a host may name one"), and the 5b measurement, which a second model would need repeated. The schema is ready: `faces.vector_model` and the per-model index filename already allow re-embedding into a new file. The code is what blocks it.

**Fix proposed** (half a day). Add a small registry in prax.ml.faces mapping a name to repo, file, revision, sha256, dim, template and aligned size, with SFace as the only entry. Resolve the active model from `faces.model` through prax.config, or prax.models as the conventions ask. Make face_index_path and _checked_vector look the dimension up per model, and compare take_in against the configured model. A second model then means one registry entry and a re-embed. Until this is done, correct the PLAN sentence.

**Checked by the skeptic.** Confirmed. PLAN.md:1150-1151 lists 'A host may name one in prax.yaml (faces.model)' under step 2's 'Built:'. A grep of src/prax finds faces.model only in the ml/faces.py:210 comment. FACE_MODEL = faces_ml.RECOGNIZER (retrieval/faces.py:50) is the default for every read and for maintain.py:1348/1367. The worker posts faces.RECOGNIZER (steps/faces.py:189), the door refuses any other model (steps/faces.py:151), and DIM, the template and the SFace repo/sha are module constants. The plan describes this as built, but nothing reads the setting. A second model would mean refactoring ml/faces.py and the step, not just a config line, even though the schema (vector_model, a file per model) is ready.

## 4. An event's suggested name comes from a shared camera folder, so accepting the suggestion merges every group in that folder into one event

medium cost, independence, fixed: a folder names a group only when the group holds more than `events.folder_share` (half) of the folder's pictures, so a flat camera folder names none. Test in `tests/test_events.py`. `src/prax/store/graph/events.py:184`.

**Evidence.** photo_groups sets the suggestion to `folder or …`, where folder is the last path segment with a letter in it (_folder_name, line 108) that holds half of the group's photos. The check for a repeated suggestion (lines 209-212) skips folder suggestions on purpose (`if not g["folder"]`). ui/lib.js:412 puts the suggestion into the name field, and decide_event joins any event that already has that name (module docstring, 'A name another event already has joins that event'). The owner's photos come from icloudpd in date folders, which have no letter, so the rule never fired there. In a flat camera folder (Android `DCIM/Camera`, Dropbox `Camera Uploads`, `WhatsApp Images`, iOS `100APPLE`, Takeout `Photos from 2019`), every group gets the same suggestion, and taking each default gives one event holding years of photos. tests/test_events.py covers only a date folder and a per-occasion folder (lines 23 and 71). It has no flat camera folder.

**Why here.** Events from photos (PLAN AR, core 6) on any library not laid out the way icloudpd lays it out. The result is quietly wrong: the merged event is a real recorded_at fact for each photo, and the attended chain is built on it.

**Fix proposed** (an hour). Count a folder as a name only if it is specific to the group, meaning most of the folder's dated photos fall inside this group. Otherwise send folder suggestions through the same check for repeats as month names. Add a test with two groups in one flat `DCIM/Camera` folder.

**Checked by the skeptic.** Confirmed. _folder_name (events.py:108) accepts any last folder with a letter in it, so 'Camera', 'Camera Uploads' and '100APPLE' all pass. photo_groups sets suggestion = folder or ... (line 184), and the de-duplication at 209-212 skips folder suggestions on purpose: the comment says a folder two groups share 'may be' one event. decide_event looks up an existing event entity by that name (SELECT id FROM entities WHERE name=? AND type='event') and links recorded_at to it, and lib.js:412 pre-fills the name field. In a flat camera folder every group gets the same default, and accepting each default joins them all into one event. The docstring only weighs icloudpd's date folders. Nothing guards the case where one folder holds groups spread over months or years.

## 5. The face match line and grouping settings are meant to be re-measured per library, but the scripts that measured them were never added to the repository

medium cost, independence, fixed: `scripts/eval_faces.py` (the match, the offers, the sizes, the groups; read-only, counts only) and `scripts/eval_events.py`, both in invariant 4's list. On the owner's library the first reproduces the log's tables, the second the 165 groups of 11,184 photos. `src/prax/store/retrieval/faces.py:766`.

**Evidence.** faces.match.line 0.45, margin 0.05 and neighbours 5 (faces.py:765-767 and 870-872), and faces.groups min_size 3, min_samples 3 and floor 0.3 (maintain.py:1356), were all chosen from tables in docs/log.md (around lines 6855, 6975 and 8840). Those tables were produced by `match_eval.py`, `match_live.py`, `offer_eval.py` and `face_size_eval.py`, which docs/log.md says lived 'in the session's scratchpad'. `ls scripts` has no eval_faces or similar, and no test states an expected value. docs/PLAN.md:1239-1243 says the line is 'measured again for another library, never fixed in code', and step 5b (an INFERRED depicts past a line, PLAN.md:1209) depends on that measurement. The values are settings, which is right, but nobody else can measure what to set them to.

**Why here.** Plan item AT step 5 and 5b. Another person's faces (other ages, other cameras, other ancestry, where SFace's similarities fall differently) get the owner's line with no means of checking it.

**Fix proposed** (a stage). Add scripts/eval_faces.py (read-only, on a copy, counts only). It should rebuild the match table (named faces against the other named faces, nobody faces against all of them, by line and margin), the offer table (taken against refused by similarity band, from runs of kind `match` and face_refusals) and the groups table. Add it to invariant 4's list of read-only measurement scripts.

**Checked by the skeptic.** Confirmed. faces.py:765-767 reads faces.match.neighbours/line/margin with defaults 5/0.45/0.05. docs/log.md:6965, 6992, 8832 and 8856 name match_eval.py, match_live.py, offer_eval.py and face_size_eval.py as scratchpad scripts. None is in scripts/ (grep finds nothing), while the other measurements are checked in as scripts/eval_*.py. PLAN.md:1239-1243 says the line is 'measured again for another library, never fixed in code'. No one else can do that measurement.

## 6. Places from GPS and event-name suggestions are always in English, whatever graph.language says

medium cost, independence, fixed: a place's names in the library's languages are labels of its node (`Gazetteer.names`, GeoNames' alternate names a country, fetched when first asked; `geo._name_place`), and a node is found by what answers to its name, so one renamed into the library's language is still the place. An event's months are spelled in `graph.language` (`text.dates.spelled_month`). Pictures placed before this get their names when placed again; the gazetteer's version did not move, so nothing is re-placed for it. `src/prax/graph/gazetteer.py:155`.

**Evidence.** parse_countries reads countryInfo.txt, which holds English names. Towns and regions use GeoNames' `name` field and admin1CodesASCII, mostly English or ASCII forms ('Munich', 'Bavaria'). place declares no `naming:` in ontology/core.yaml (line 79), so it is proper, and the vocabulary pass will never give it a label in another language. geo.py writes those names as the nodes for taken_in and located_in. events.py:59 _MONTH_NAMES is English and builds the suggested names ('Aldmouth, Ardenia, June 2018'), which become event entities when accepted. Elsewhere a proper name is written as the document prints it, so a German document's place comes out as 'Deutschland' while the geocoder writes 'Germany', and nothing labels the two as one. A library with graph.language: de or fr gets English photo places and event names beside its own.

**Why here.** The convention that 'the library is written in one language, graph.language', and the multilingual label bridge that queries cross on (Apfel -> apple). Both are bypassed for every node that comes from GPS.

**Fix proposed** (a stage). Add the GeoNames alternate names (alternateNamesV2, filtered at load to graph.language and graph.label_languages) as labels on each town, region and country node. Take month names from the display language, or fall back to an ISO date where none is known.

**Checked by the skeptic.** Confirmed. parse_countries takes column 4 of countryInfo.txt, which holds the English name, and its docstring says so. Regions come from admin1CodesASCII and towns from the cities file's name field. Nothing in gazetteer.py or geo.py reads graph.language or the alternate names. place in core.yaml:79 declares no naming:, so it counts as proper and the vocabulary pass never labels it in another language. events.py _MONTH_NAMES is English. These are only suggestions a person can edit, but the place nodes are written as-is. A de/fr library ends up with 'Germany' from GPS beside 'Deutschland' from its documents, with no label tying the two together.

## 7. What is personal now has two definitions: the sensitivity column, and a named face that the wall reads as a second branch

medium cost, generality, fixed: `store.personal_sql(alias)` is the one test of personal (a sensitivity, or a named face) for the wall's reads, `document_hidden`, `scope_documents`, the counts and the rule pass. `src/prax/store/base.py:604`.

**Evidence.** _hidden_read (base.py:594-604) hides `sensitivity IS NOT NULL` and also every picture in `_NAMED_FACES` (base.py:616). document_hidden (base.py:646-649) repeats the same predicate in a second form, `_names_a_face` (base.py:622). Every other reader of personal-ness still reads only the column: rules._premise_edges (rules.py:69, `d.sensitivity IS NULL`), rules._chain_edges and SENSITIVITY_RANK (rules.py:50,85,101), domains._NOT_PERSONAL (domains.py:315), sensitivity_counts (domains.py:810, shown on the Admin page by api/wall.py:109), and pages.PERSONAL_STATES (pages.py:528). A picture that a person opened and that has a named face therefore counts as open to all of them and as hidden to the wall. I found this by grepping for `sensitivity` and `face_names` across src/prax/store.

**Why here.** Plan item AT's wall (PLAN.md near line 1230) says a picture with a named face is hidden even when a person opened it. Step 5b (INFERRED depicts tags) and 'a camera's owner' are planned next, and each will be one more reason a picture is personal. Each reason has to be added in two places in base.py and is missed by every reader that asks the column. The second case is already real: the rule pass and the Admin counts disagree with the wall today.

**Fix proposed** (an hour or two, plus tests). Give one store predicate for 'personal for a reason other than its domains', as a SQL fragment or view plus a per-document check built from the same text. Have hidden_documents, document_hidden, the rules' premise filter and the counts read that predicate, so a new reason is one line.

**Checked by the skeptic.** Confirmed: _hidden_read (base.py:594-604) and document_hidden (base.py:646-649) each carry a named-face branch via _NAMED_FACES / _names_a_face beside the sensitivity test, while domains._NOT_PERSONAL (315), sensitivity_counts (805) and pages.PERSONAL_STATES (528) read only the column. The wall itself stays consistent, because derived edges go through hidden_by_premise with hidden_documents, so the claim that the rule pass leaks is overstated. Still, a picture a person opened that has a named face counts as 'released' in the Admin counts while the wall hides it. Each further reason (step 5b) has to be added in two places in base.py.

## 8. Three writers mark a document suspected, and the face one keeps no cue

medium cost, generality, fixed: `documents.text.mark_suspected` writes the mark for every rule (`suspect`, `register_references`, a named face), with the cue the Admin page shows ("a face in it is named"), and never over a person's decision. `src/prax/store/graph/naming.py:159`.

**Evidence.** There are three writers of 'suspected by a rule'. (1) store.suspect (documents/text.py:578) writes `meta.private.cues` and `meta.sensitivity` with by=rules. (2) register_references (documents/text.py:128-137) writes the cue `referenced: <kind>` inline. (3) naming._suspect_picture (naming.py:159-176) runs a raw UPDATE with by='faces' and writes no `meta.private`. domains.set_sensitivity (domains.py:716), the store's writer of this column, is used by none of them. suspected_page (domains.py ~748) lists `meta.private.cues` as the reason. A picture suspected because a face in it was named therefore shows on the Admin page's 'personal?' list with no cue, and the person who decides cannot see why it is there.

**Why here.** This is the wall's stage V, where a rule suspects and a person decides. AT step 5b (an INFERRED depicts), and any later rule that suspects from the graph rather than the text, will write a fourth copy.

**Fix proposed** (an hour). Add one store function (for example `mark_suspected(con, doc_id, cues, by)`) that does both checks (not already decided by a person, still open), writes the cue into meta.private and the state into meta.sensitivity, and is called by suspect, register_references and _suspect_picture.

**Checked by the skeptic.** Confirmed: naming._suspect_picture (naming.py:159-176) does a raw UPDATE with by='faces' and writes no meta.private. store.suspect and register_references (text.py:128-137) write cues, and set_sensitivity is not used. suspected_page (domains.py) reads meta.private.cues, so a picture suspected because of a named face shows on the 'personal?' list with cues []. This only fires on an open picture no person has decided about (a held photo the rules did not suspect), which is exactly the case where nothing else explains it.

## 9. Run names are built by hand in a dozen places, and the same-second collision fix reached only `naming._run`

low cost, hygiene, fixed: `store.run_name(kind, *after, unique=, sep=)` in the twelve places; the person's actions (merge, naming, labels, events) take `unique=True`. `src/prax/api/curation.py:123`.

**Evidence.** `grep "now().replace(':', '').replace('-', '')"` finds the same run stamp built inline at api/curation.py:123, graph/graphio.py:215, documents/library.py:439, graph/events.py:489, graph/labels.py:465, maintain.py:1278 and 1360, and repair/graph.py:753, 876 and 991. naming.py:74 added `_run(kind)` with a random suffix 'since a person answers for two groups within one second'. That reasoning applies just as much to `/graph/merge`, which builds `"merge-" + second` with no suffix, while `store.merge_people` builds its merge runs through `_run("merge")`. Two merges made from the Review page within one second share a run, and `/graph/unmerge` takes both back.

**Why here.** Invariant 8: `restore_run` and `unmerge_run` undo a run whole, so a run name that is not unique turns one undo into two. Each new person-facing action (AT's naming and renaming, events) adds another hand-built stamp.

**Fix proposed** (an hour). Add one `store.run_name(kind, *, unique=False)` in base, with the stamp and an optional random suffix. Use it everywhere, with `unique=True` for every person-made action (curation merge, events decide, label decide, naming).

**Checked by the skeptic.** Confirmed, but only partly inside the reviewed change. The diff adds three more hand-built stamps (events.py:489, naming.py:77, maintain.py:1360), and the new collision-safe helper naming._run is private to naming instead of living in base. The /graph/merge stamp without a suffix (api/curation.py:123) is real, and two merges in one second would share a run that unmerge takes back whole. However, curation.py has not changed since a5aac19, so that collision was there before this change. events.py:489 adds the candidate id and the maintain passes run one at a time, so those two do not collide.

## 10. The sensitivity column and `meta.sensitivity` are written by hand in four places; `_suspect_picture` re-implements `_person_decided`

low cost, hygiene, fixed with 8: `_suspect_picture` calls `mark_suspected`, which asks `_person_decided`. `src/prax/store/graph/naming.py:159`.

**Evidence.** The shape 'UPDATE documents SET sensitivity = ?, meta = ? with meta.sensitivity = {state, by, at}' appears in four places: `domains.set_sensitivity` (domains.py:716), `text.suspect` (text.py:638-645), `text.register_references` (text.py:128-145, as an INSERT), and the new `naming._suspect_picture` (naming.py:159-176). The last one tests `(meta.get('sensitivity') or {}).get('by') == 'human'` itself; `documents/text._person_decided` (text.py:571) already exists, is re-exported, and asks exactly that.

**Why here.** CLAUDE.md's wall rule is 'a rule only ever sets suspected on an open document no person has decided about'. That rule is now restated in each writer, and each later rule source (faces now; places, events or recordings next under AT/AQ) would add a fifth copy. A copy that forgets the human check would quietly override a person's decision.

**Fix proposed** (an hour). Add one `_mark_suspected(con, doc_id, by, cues=None) -> bool` in documents (beside `set_sensitivity`) that holds the open-and-undecided check and the meta write. Have `text.suspect`, `register_references`'s post-insert and `naming._suspect_picture` call it.

**Checked by the skeptic.** Confirmed. naming._suspect_picture (naming.py:159-176) tests meta.sensitivity.by == 'human' inline, and documents/text._person_decided (text.py:571) already asks exactly that. graph comes after documents in the ORDER, so naming could import it. naming also writes the column and the meta shape by hand, like text.suspect and domains.set_sensitivity. Of the four copies only this one is new. The rule is held correctly today, so the cost is drift for the next rule source.

## 11. The four new worker steps each write the same timed batch loop by hand, and the due/count queries three times

low cost, hygiene, fixed: `steps.base.BatchStep` with one `BATCH_SECONDS` and `first_error`; Photos, Geocode, Faces and FaceVectors are batch steps. The store's due and count queries are left: the skeptic found their difference does not show in practice. `src/prax/steps/photos.py:75`.

**Evidence.** Photos.run (photos.py:75), Geocode.run (photos.py:155), Faces.run (faces.py:73) and FaceVectors.run (faces.py:182) each repeat the same steps: `while time.monotonic() - start < RUN_SECONDS`, fetch, note, post, tally `applied`/`errors`, and `if tally['errors'] and not tally['applied']: break`. Two separate `RUN_SECONDS = 90` constants exist. They have already drifted: only faces.py has `_first_error`, so a photos or geocode pass that fails says 'N not fetched' without the door's reason, and Photos.run does not use `ready`/`resting`. On the store side, `photos_due`/`photo_counts`, `faces_due`/`face_counts` and `places_due`/`place_counts` repeat `coalesce(json_extract(meta,'$.K.v'),0) < ?` with the retired filter. Their 'due' and 'counts' conditions already differ: `place_counts` counts non-pictures that `places_due` skips. `ModelStep.run` in steps/base.py is the template for model steps; the non-model steps have none.

**Why here.** AQ's recordings and AT's later steps add more steps of this no-model, per-picture kind. Each copies the loop again and inherits whichever copy it started from.

**Fix proposed** (an hour or two). Add a `BatchStep` (or a `timed_batches(p, name, read, tally_keys)` helper) in steps/base with one RUN_SECONDS, `_first_error` and the rest check. Optionally add one store helper for a versioned per-document reading's due condition, so due and counts cannot disagree.

**Checked by the skeptic.** Confirmed for the worker loops. Photos.run (photos.py:75), Geocode.run (photos.py:155), Faces.run (faces.py:73) and FaceVectors.run (faces.py:182) repeat the same timed fetch/post/tally/break loop. RUN_SECONDS = 90 is defined in both modules. _first_error exists only in faces.py. Geocode's summary line drops its error count entirely, and Photos.run skips resting/ready. The store-side due/count drift is weak: photo.gps is only written by the photos step, which handles pictures only, so in practice place_counts and places_due cover the same rows.

## 12. English month names are written out twice more, next to `text.dates` and two older copies

low cost, hygiene, fixed: `text.dates.SPELLED` and `spelled_month(n, lang)`, read by events, a photo's search words, worlddates and the LaTeX importer. `src/prax/store/graph/events.py:61`.

**Evidence.** The new code adds `_MONTH_NAMES` in graph/events.py:61, used for event suggestions, and `_MONTHS` in documents/meta.py:581, used for the photo search field. The same list already exists in graph/worlddates.py:397 and importers/latex.py:32. `text.dates` holds month names for every language it reads (`month_names()`). Every copy is English, while the library's language is `graph.language`, so a French library gets English event names and English month words in its search field.

**Why here.** Event names are shown on the Review page and become entity names once a person accepts them. The month words index the photo field for search. Both should follow `graph.language`, and with four copies a fix has to be made four times.

**Fix proposed** (an hour). Add one `text.dates.spelled_month(n, lang)`, backed by the per-language names `text.dates` already holds or by a small table, and use it in events, meta, worlddates and latex.

**Checked by the skeptic.** Partly confirmed. English month tuples appear at events.py:61, meta.py:581, worlddates.py:396 and latex.py:32. text.dates._MONTHS, however, is an unordered table for parsing in several languages, not a way to name a month in graph.language, so the 'reuse text.dates' fix is not available as described. The real cost is generality: event names, which become entity names, and the photo search words are English whatever graph.language says. That cost is real for another owner's library.

## 13. The defaults of the `faces.match.*` settings are written twice in one module

low cost, hygiene, fixed: `retrieval.faces.match_settings()` holds the three defaults once. `src/prax/store/retrieval/faces.py:870`.

**Evidence.** `face_matches` (765-767) and `picture_faces` (870-872) each read `config.number('faces.match.line', default=0.45)`, `faces.match.margin` (0.05) and `faces.match.neighbours` (5) with their own literals. The docstring of `picture_faces` promises 'at the same line and margin' as `face_matches`. Separately, `similar_faces` marks `match` by `faces_ml.MATCH = 0.363`, a different number for the same idea.

**Why here.** Changing one default (step 5b's re-measurement of the line is open) leaves the picture page and the Review tab's offers disagreeing.

**Fix proposed** (minutes). Add one `_match_settings()` returning (k, line, margin), used by both functions.

**Checked by the skeptic.** Confirmed. face_matches (retrieval/faces.py:765-767) and picture_faces (870-872) each repeat the three config reads with literal defaults in the same module, and picture_faces' docstring promises they match. The faces_ml.MATCH = 0.363 part is not a defect: ml/faces.py:209-217 documents it as OpenCV's line for similar_faces' 'match' flag, deliberately separate from the measured faces.match line.

## 14. The event grouping's thresholds are constants, while the face grouping beside it reads prax.yaml

low cost, hygiene, fixed: `events.event_settings()` reads `events.gap_hours`, `min_photos`, `place_share`, `burst_hours` and `folder_share` at call time; `faces` and `events` joined `config.SECTIONS` (`faces` was missing, so a host's `faces:` section was refused). `src/prax/store/graph/events.py:51`.

**Evidence.** `EVENT_GAP_HOURS = 24`, `EVENT_MIN_PHOTOS = 8`, `EVENT_PLACE_SHARE = 0.6` and `EVENT_BURST_HOURS = 3` are module constants. The facegroups pass beside it in maintain.py:1350-1358 reads `faces.groups.*` through `prax.config`. CLAUDE.md says what a host chooses is configuration.

**Why here.** How far apart two outings are depends on the person's photo habits, not on the code. Another owner of the library could only change these values by editing the code.

**Fix proposed** (under an hour). Read them as `events.*` settings with these defaults, the way `faces.groups` is read.

**Checked by the skeptic.** Confirmed. EVENT_GAP_HOURS, EVENT_MIN_PHOTOS, EVENT_PLACE_SHARE and EVENT_BURST_HOURS are module constants in graph/events.py:51-59. The facegroups pass beside them reads faces.groups.* through config (maintain.py:1350-1358), and CLAUDE.md says what a host chooses is configuration. The page's offered cuts make up for some of this, but the gap and the minimum count that decide which candidates exist at all cannot be changed without editing the code.

## 15. The photo reading replaces `meta.photo` whole under one version, so AR's next fields re-read every original from the share

low cost, plan, fixed: `documents.photos.PHOTO_KEYS` are the photos step's own; a re-read or a failure keeps the keys another reading wrote in `meta.photo`. `src/prax/store/documents/photos.py:133`.

**Evidence.** `apply_photo` builds a fresh dict and assigns it with `meta["photo"] = photo`, keeping no existing keys. `photos_due` makes a picture due again whenever `$.photo.v` < parsers.photos.VERSION, and it hands out only pictures whose original this door can reach (held, or on a mounted root). VERSION is one number for the whole reading: the comment at parsers/photos.py:24 says "a later one (another size, another field) is a new version, and every photo becomes due for it again". Today this module is the only writer of meta.photo (grep). This differs from the text side's convention, where each reading is its own queue row and asking for one never replaces another.

**Why here.** The AQ photos-step item lists what is left for AR: "the perceptual hash and a video's poster frame", and XMP once a library has it. A perceptual hash can be computed from the preview the door already holds. Under the current shape, though, adding it means bumping VERSION, which means fetching all ~13,000 originals over the NAS share again and re-filing thumbnails and previews. A separate step that writes into meta.photo would be wiped by the next photos re-read.

**Fix proposed** (an hour). Merge into meta.photo instead of replacing it, keeping keys this reading does not own. Give a new field its own stamp, or its own step reading the preview, such as `meta.photo.phash` with its own version, so only that step becomes due again.

**Checked by the skeptic.** Confirmed. apply_photo assigns meta['photo'] = photo whole (photos.py:133). parsers/photos.py:23-25 sets the convention that another field means a new VERSION, so every photo becomes due again, and photos_due re-reads the originals. PLAN.md:1000 names the perceptual hash as the next field for AR, and it can be computed from the preview the door already holds. Following the documented convention would re-fetch every original over the share. A separate writer into meta.photo would be wiped by the next re-read. The cost is low because AR could store the hash under its own key, but the comment steers the next author the other way.

## 16. A second face-match line is fixed in code, though the plan says the line comes from prax.yaml

low cost, plan, fixed: `ml.faces.MATCH` is gone; `similar_faces` marks a match at `faces.match.line`. `src/prax/store/retrieval/faces.py:242`.

**Evidence.** `similar_faces` marks each neighbour `"match": sim >= faces_ml.MATCH`, where MATCH is 0.363, OpenCV's demo value, a constant in ml/faces.py:217. `face_matches` and `picture_faces` read `faces.match.line` (default 0.45) from config (lines 766, 870). The comment in ml/faces.py:209-210 says the library's line is "`faces.match` in prax.yaml, never fixed here", but this flag is fixed there.

**Why here.** Plan AT: "The line is `faces.match` in `prax.yaml`, measured again for another library, never fixed in code". Step 5b adds a line of its own. That would make three thresholds for "same person", and one of them could not be configured or measured.

**Fix proposed** (an hour). Have similar_faces read `faces.match.line`, or drop the `match` flag if the Review page does not need it. Keep MATCH only as documentation of where the default came from.

**Checked by the skeptic.** Confirmed but small. similar_faces sets 'match': sim >= faces_ml.MATCH (0.363, retrieval/faces.py:242), while face_matches and picture_faces read faces.match.line (default 0.45, lines 766/870). This contradicts ml/faces.py:209-210 ('never fixed here'). Grepping the UI shows view-decide.js:467-469 does not read the field, and only tests/test_face_vectors.py:141 does. So today it is a misleading, unconfigurable field on /face/{id}/similar rather than a live third threshold. The cost lands when an agent, a future UI or step 5b reads it.

## 17. The event grouping numbers were tuned on the owner's 12,983 photos and are constants, with no setting and no eval

low cost, independence, fixed with 14 and 5: the settings, and `scripts/eval_events.py` prints the groups by gap and size. `src/prax/store/graph/events.py:50`.

**Evidence.** EVENT_GAP_HOURS 24, EVENT_MIN_PHOTOS 8, EVENT_PLACE_SHARE 0.6 and EVENT_BURST_HOURS 3 are module constants. The module docstring states that the measurement was taken on 2026-10-26 over the owner's dated photos (165 groups, gap 12 vs 36). grep finds no config.number for them, and scripts/ has no measurement of them. The tests use synthetic dates only. Someone who photographs every day gets one group per stretch of unbroken days, and that has to be split cut by cut. Someone who takes few photos gets almost nothing past 8.

**Why here.** PLAN AR events from photos, and the attended chain built on decided events.

**Fix proposed** (an hour). Read them as `events.gap_hours`, `events.min_photos` and `events.burst_hours` through prax.config with the current defaults. Have scripts/eval_faces.py, or a small eval_events.py, print the groups-by-gap table that the docstring quotes.

**Checked by the skeptic.** Confirmed. EVENT_GAP_HOURS, EVENT_MIN_PHOTOS, EVENT_PLACE_SHARE and EVENT_BURST_HOURS are module constants (events.py:50-60), with no config.number. The docstring says they were measured on the owner's 12,983 photos, and no measurement script exists. This goes against the 'what a host chooses is configuration' convention. The cost is reduced because a person can split groups with cuts, and a 24 h gap is a reasonable generic default, so LOW.

## 18. A Live Photo clip is recognised only in Apple and icloudpd naming, so another phone's motion clips become video documents

low cost, independence, fixed: `places.live_clips.suffixes` (.mov and .mp4 by default) and `.tags` (`_HEVC`). `src/prax/store/documents/places.py:39`.

**Evidence.** _LIVE_CLIP = ('.mov',) and _LIVE_CLIP_TAGS = ('_HEVC',). The comment says these were set after 25 of the owner's clips became documents. Google Takeout or Pixel exports that put a .MP4 beside the .jpg, and other downloaders' suffixes, do not match. Each such clip becomes a suspected video document (the `clips` cleanup rule had to remove the owner's 25 on the live store, per commit 0c84afc). The result is noise, not a wrong fact, and the cleanup rule catches only the same pattern.

**Why here.** Crawls with --reference (plan item AQ) of anyone's phone library.

**Fix proposed** (an hour). Make the clip suffixes and tags a `places.live_clips` setting with these defaults plus .mp4. Add a test for a .MP4 beside a .JPG.

**Checked by the skeptic.** Confirmed. _LIVE_CLIP=('.mov',) and _LIVE_CLIP_TAGS=('_HEVC',) (places.py:39-44), and the comment ties them to the owner's icloudpd case. Google Takeout exports an iPhone Live Photo's motion as a same-named .MP4 beside the .HEIC/.JPG, so those clips would become suspected video documents. One correction: Pixel and Samsung motion photos embed the clip in the JPEG, so the 'Pixel exports' part of the finding does not hold. The result is noise, not a wrong fact, so LOW.

## 19. Two policies for which premises a derived fact may stand on: a chain sources its fact to one document, the closures refuse every non-open premise

low cost, generality, fixed: one policy (`rules.by_document`): every kind derives from the open premises, then from each hidden document's own with the open ones, and its fact is sourced to that document. Test: a suspected picture's town in its country. `src/prax/store/graph/rules.py:69`.

**Evidence.** _premise_edges (rules.py:53-73), which feeds the transitive, symmetric and inverse kinds, drops any premise whose document has a sensitivity. The new chain kind (_chain_edges, chain(), rules.py:77-145) takes premises from documents of any sensitivity when both come from one document, and sources the derived fact to that document so the wall hides it. geo.apply_place (geo.py:228-238) writes `town located_in region` and `region located_in country` sourced to each picture, and referenced pictures are suspected from registration (e12aed6). located_in is `transitive: true` in core.yaml:160. The closure of the gazetteer's places therefore stands only on the open pictures, while `attended`, built from the same pictures, does not have that limit. I checked this by reading the code and core.yaml. I did not measure on the library, because none was named.

**Why here.** The rule pass (stage AN) and AT's pictures: the gazetteer facts are the second case of 'premises of one hidden document', and the policy written for chains fits them. Each new rule kind otherwise has to pick between two policies with no stated reason.

**Fix proposed** (a stage (a closure path through one document needs per-path sources and tests)). Lift the 'premises of one document are sourced to it, otherwise open premises only' choice out of chain() into the premise selection that every kind shares, so closure and converse use it too. Alternatively, state in the module docstring why closures keep the stricter policy.

**Checked by the skeptic.** Confirmed: _premise_edges (rules.py:53-73) drops every premise whose document has a sensitivity, while _chain_edges/chain() accept one document of any sensitivity and source the fact to it. geo.apply_place writes town located_in region and region located_in country sourced to the picture, and referenced pictures are suspected at registration, so the located_in closure stands only on open pictures. hidden_by_premise already makes a hidden premise safe, so the stricter closure policy has no stated reason. The cost today is small: missing public facts of the town-in-country kind, plus the choice every new rule kind has to make.

## 20. The person page works out 'events a person was at' itself, beside the rule that now derives attended

low cost, generality, fixed: the person page's events are the person's `attended` facts, which the rule pass derives. `src/prax/store/graph/people.py:95`.

**Evidence.** _photos_part (people.py:95-158) joins the person's depicts pictures to their recorded_at edges to list events. eca06ef added `attended: chain [~depicts, recorded_at]`, written nightly by the rule pass. _own_facts (people.py:64-92) returns every live edge at the person, including those attended edges. The page therefore shows the same occasions twice, worked out by two mechanisms with different rules: the chain requires one document or open documents and has a cap, while the page filters by the viewer's hidden set.

**Why here.** AT step 6 and the person page (step 0). When step 5b adds INFERRED depicts, the two lists will drift apart in a way a reader cannot explain.

**Fix proposed** (an hour). Read the page's events from the live attended edges, keeping the photo count and days from recorded_at, or leave attended out of the 'own facts' list. Either way, one mechanism answers 'where was this person'.

**Checked by the skeptic.** Confirmed: person_page removes only DEPICTS from facts (people.py:178), so the chain-derived attended edges land in timeline/relations while _photos_part (95-158) lists the same events from depicts+recorded_at. The page leaves depicts out precisely because the photos part covers it, and attended was not left out the same way. The two lists follow different rules: chain needs one document or open ones and has RULE_CAP, the page filters by the hidden set. Low cost: the duplication is visible but limited to the person page.

## 21. id_marks is general but has one user; face groups regroup each night too and can give a group id twice

low cost, generality, fixed: face groups take their ids from `id_marks` (`'face_groups'`), so a dissolved group's id is never given again. `src/prax/store/retrieval/faces.py:396`.

**Evidence.** Migration 0059 adds `id_marks` for 'the rows that are replaced whole and must never give an id twice', and only events.replace_event_candidates uses it (events.py:243,303). replace_face_groups deletes every group and takes `next_id = max(old)+1` from the previous run alone (faces.py:396). If the highest group dissolves one night, the next night can give its id to a different group. The UI addresses a group by id (`/faces/groups/{id}`, view-decide.js:319). The two passes also carry identity differently: face groups by Jaccard ≥ GROUP_CARRY (faces.py:321,408), event candidates only for an identical photo set (events.py:265).

**Why here.** These are the two nightly regroupings of AT (events and face groups). Naming goes by face id, so today a stale link shows a different group rather than writing a wrong answer. That keeps the cost low until an action is taken by group id.

**Fix proposed** (an hour). Have replace_face_groups take its fresh ids from id_marks ('face_groups'), the second user the table was written for.

**Checked by the skeptic.** Confirmed: replace_face_groups takes next_id = max(old)+1 (faces.py:396) from the previous run's members only. If the highest group dissolves in a run that creates no new group, the next run can give its id again. Migration 59's id_marks exists for this and only events uses it. The UI addresses /faces/groups/{id} (view-decide.js:319, api/documents.py:384). Naming goes by face id, so a stale link shows the wrong group but writes nothing wrong, which keeps the cost low.

## 22. people() reads every live edge of every person on each request, and the src half scans all edges

low cost, performance, fixed: `people()` counts in SQL with GROUP BY and is kept per viewer until the store changes (`store_state`). `src/prax/store/graph/people.py:244`.

**Evidence.** I built a test store with prax's migrations in the scratchpad (perf2/build.py): 27,000 documents, 161,000 entities of which 50,000 are persons, 218,500 live edges of which 60,000 are authored_by to persons, and 11,320 faces. Then I timed people() (perf2/time.py, best of 3, desktop CPU): 140 ms for the default list (q='') and 37 ms with q. EXPLAIN QUERY PLAN for the src half is 'SCAN e USING INDEX idx_edges_src' then a lookup of n and c: it walks all 218k live edges to find the few that start at a person. The dst half is 'SCAN n USING COVERING INDEX idx_entities_canonical' plus idx_edges_dst, and returns 61,500 rows, each counted in a Python loop. cProfile puts 93 ms of 333 ms profiled in the loop body. Doing the counting in SQL (GROUP BY c.id) gave 98 ms + 61 ms (perf2/people2.py), so the cost is the full read, not Python. The result is the same for every request until something is written.

**Why here.** Invariant 7 and the checklist's 'Growth: a loop over all edges in a request'. GET /people serves the People page every time it opens (view-people.js asks for limit 100). The cost grows with the number of authors in a research library, not with the people named from photos, which is what the page ranks first. On a Pi-class CPU it will be about 0.5 s at this size and more as the library grows.

**Fix proposed** (an hour). Memoise the q='' answer per (viewer, PRAGMA data_version, total_changes), as hidden_documents already does. Or drive the src half from entities of person types (scan n by type, then search e by src) instead of scanning edges. Rank only from the depicts edges plus a capped degree count instead of reading every authored_by edge.

**Checked by the skeptic.** Confirmed at people.py:244-289. Neither end is aggregated and there is no cache, so every GET /people reads every live edge of every person-typed entity, in both directions, and counts them in Python. I re-ran perf2/time.py with the repo venv on the reviewer's test store (27k docs, 50k persons, 218k live edges): people() takes 114.7 ms for the default list and 37.9 ms with q. That matches the reported 140/37 ms. Nothing in CLAUDE.md or the code comments chooses this. The cost grows with how many authors the library has, not with how many people are named from photos, which is what the page ranks first. That makes roughly 0.4-0.5 s per open of the People page on a Pi-class board. It is low because only one page pays it, but someone pays it on every visit. I did not re-check EXPLAIN myself: the read-only URI open failed on the Windows path. The timing stands without it.

## 23. Face matching recomputes everything for every picture page after any change, and the per-face Python loop dominates

low cost, performance, fixed: `nearest_people` vectorised (`column_of`, `np.add.at`, a masked argmax; the same answers on 30 random sets, 0.189 s to 0.101 s at the owner's counts); `picture_faces` matches only that picture's open faces; the named side's vectors are kept until a name changes (migration 61 indexes faces by picture). `src/prax/ml/matching.py:86`.

**Evidence.** Timed with perf2/match_bench.py on desktop: nearest_people takes 0.15 s at 5,400 open × 1,932 named faces of 32 people (the owner's counts in docs/log.md), 0.26 s with 300 people, and 2.82 s at 20,000 × 8,000. The matrix product alone takes 0.005, 0.010 and 0.113 s. cProfile at the owner's scale: 0.095 s of 0.228 s is the function's own per-row Python (votes via np.searchsorted per neighbour, 27,000 calls; np.delete per row, 5,400 calls). The caller retrieval/faces.py _matched (line 667) keeps one cached result, keyed by _match_state, which includes max(faces.vector_at) and the face_names and face_refusals counts and ids. So every naming, refusal or facevectors post invalidates it. picture_faces (line 838) then rebuilds it inside the request for the next picture page that has an open face. That rebuild includes _get_vectors over every open and named face under _INDEX_LOCK: 0.11 s at 10k faces and 0.49 s at 50k (perf2/faces_bench.py). Searches wait for that lock.

**Why here.** Plan item AT, steps 4 and 5. The naming loop is: name a face, open the next picture, rebuild the whole matching. On the Pi target that is about 3-4× the desktop figure for each picture page, and it grows with open × named faces. The module's docstring talks about bounded memory but says nothing of time.

**Fix proposed** (an hour). Vectorise the per-row part of nearest_people. Keep a column→person array so votes are np.take plus a bincount per row, and take the margin from a masked max instead of np.delete. That leaves the matmul and argpartition. In picture_faces, match only that picture's open faces against the named set (a few rows) instead of the global _matched, and keep the global cache for face_matches only.

**Checked by the skeptic.** Confirmed. In matching.py:86-120, each query row runs a Python loop: an np.searchsorted per neighbour, an np.delete, and an argmax over the person's rows. In faces.py, _matched keeps one cached result keyed by _match_state (the max id and count of face_names and face_refusals, max(vector_at), and the merge fingerprint), so any naming, refusal or facevectors post invalidates it. picture_faces (around line 875) then rebuilds it inside the next picture-page request that has an open face. That rebuild includes _get_vectors over all named and open faces, which holds _INDEX_LOCK for the whole loop (base.py:879-897), so searches wait. I re-ran match_bench.py: 0.16 s at 5,400x1,932 faces with 32 people (matmul 0.007 s), 0.18 s with 300 people, and 2.80 s at 20,000x8,000 (matmul 0.198 s). In cProfile, 0.134 s of 0.315 s is the function's own Python. In the naming loop, every picture page after an answer pays the full recompute: about 0.5 s on a Pi at the owner's counts, and quadratic growth. The module docstring bounds memory only; nothing chooses the time cost. It stays low because it is bounded at the owner's scale today, and _matched caches between writes.

## Rejected by the skeptics

- **`mimes.picture_sql` exists to say 'is a picture' once, but the new photos queries write it again** (`src/prax/store/documents/photos.py`). The cost the finding claims does not hold. photo_counts builds its NOT IN list from mimes.DOCUMENT_IMAGES (photos.py:102-104), and photos_due, places_due and photo_groups filter with mimes.is_picture. A type added to DOCUMENT_IMAGES therefore reaches every one of these queries correctly. The rule that a DjVu is not a picture still lives only in mimes. Only the SQL helper goes unused, which is cosmetic and costs nothing later.

- **`face_names` cannot tell a model's tag from a person's answer, so step 5b's tags would act as anchors for further matches** (`src/prax/migrations/0056_face_names.sql`). Refuted as stated. face_names does have `by` and `run` columns (0056:20-21), so an automatic tag can be told apart, e.g. by='matching' or a run kind, and the readers can filter on that. Step 5b is still unchecked in PLAN.md:1209 and its design is not fixed. It could also write INFERRED depicts edges with no face_names row at all. Either way, adding a similarity column later is a plain ADD COLUMN migration. The compounding risk belongs to how 5b gets designed; nothing that exists now blocks it.

- **A picture added later inside a decided event is dropped without notice, though the plan has it joining the event** (`src/prax/store/graph/events.py`). The input is not discarded. replace_event_candidates rebuilds the groups from the photos on every nightly pass (events.py:229-253). A photo inside a decided event's group, and in no candidate, can be derived at any time from event_candidate_photos plus the group, so nothing needs to be recorded now. PLAN.md:1018-1019 deliberately leaves this to AR, and building it means changing this one skip branch, which that stage would do anyway. Nothing here makes it more expensive later.
