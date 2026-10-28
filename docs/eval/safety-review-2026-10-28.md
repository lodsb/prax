# The safety and correctness review of 2026-10-28

A workflow of twelve agents read everything changed since `a5aac19`
(2026-10-19, the last review; 49 Python files, some 7,000 lines: photos
and their places, events from photos, the face plan AT, the token roles):
six reviewers by area (data safety, the wall, the new logic, external
input and the host, contracts and sizes, docs and tests), each finding
then re-checked by a skeptic told to refute it and to reproduce it on a
temporary store. 33 confirmed, one refuted; 28 distinct defects, the
others found again by a second area. Read-only: no agent opened the live
store or called the running door. Before the fixes, a read-only count on
the live store found no answer or refusal on an ended face and no
picture copy that was not a digest.

Each fix has a test that fails on the code the review read, in
`tests/test_review_2026_10_28.py` or beside the code it changed.

The refuted one: a contributor asking for a vision reading of an open
photo does not reach a paid model (`steps/parse.py`, `_requested`).

## 1. A second faces reading ends the named faces but leaves their face_names answers and depicts edges live, and nothing can undo them

*high*, data; `src/prax/store/documents/photos.py:261`

**Scenario.** A face is named 'Ada' (store.name_faces), so the picture depicts Ada. The picture is then read again: faces.VERSION goes up, or a lease runs out and a second worker posts. apply_faces sets gone_at on every live face of the picture and writes new rows. It never touches face_names or the depicts edge. The old answer stays live on a face that is gone, and the depicts edge stays live. The new face shows as unanswered. Undo is refused: unname_faces(run=...) and unname_faces([face]) both go through naming._faces, which accepts only live faces, so they raise 'no such face'. If the person names the new face 'Grace', the picture then depicts both Ada and Grace for good. The invariant ('the depicts edge no answer holds up is ended') breaks, and a wrong fact cannot be taken back.

**Evidence.** Read apply_faces (photos.py 243-281): one UPDATE of faces.gone_at, with no face_names or edge handling. Read naming._faces (naming.py 48-68): it requires gone_at IS NULL. Ran a throwaway pytest against a tmp store (scratchpad ds/test_ds.py::test_reread_orphans_answer). Steps: name the face Ada, call apply_faces again with version+1, try to undo, name the new face Grace. Output: 'live answers on gone face: 1 depicts: [('Ada', 1)]', 'new face answers: {}', 'undo by run fails: ValueError no such face: 1', 'after naming new face Grace: [('Ada', 1), ('Grace', 2)]'.

**The skeptic.** Read apply_faces (photos.py 243-281). It ends every live face with a single UPDATE of gone_at and never touches face_names or the depicts edges. naming._faces (naming.py 48-68) accepts only faces with gone_at IS NULL. faces_due hands a picture out again whenever meta.faces.v < VERSION, and apply_faces never checks the version it already holds, so a late post after a lease expires also gets through. Ran the throwaway test against a tmp store: 'live answers on gone face: 1 depicts: [(Ada,1)]', 'new face answers: {}', 'undo by run fails: ValueError no such face: 1', and after naming the new face Grace the picture depicts both [(Ada),(Grace)]. Fix: in apply_faces, end the live face_names of the gone faces and the depicts edges they hold up (or carry each answer over to the new face that best overlaps the old box). Also let unname_faces take a run whose faces are gone, and refuse a post whose version is not newer than meta.faces.v.

**Fix proposed.** When apply_faces ends faces, settle their answers in the same transaction. One way: carry each live answer (and live refusals) over to the new face whose box matches best (IoU past a line), pointing at the same edge_id. End the rest through naming._end_answers under a run of the reading, so a depicts edge with no live answer left is ended and recorded. Also let unname_faces(run=...) accept ended faces, so an action stays undoable after a re-read.

**Done.** Carried over: `apply_faces` moves each live answer and refusal of an old face to the new face whose box covers it (`SAME_FACE_IOU`, 0.4), under its own run, person, edge and time. An answer whose face was not found again stays on the ended face with its `depicts`, and `unname_faces(run=)` reaches it. A post older than the picture's reading is `stale` and changes nothing. Test: `test_a_faces_reading_again_carries_what_a_person_said`.

## 2. A contributor can make /doc/N/thumb and /preview serve any file on the host, the database included

*high*, wall; `src/prax/store/documents/photos.py:151`

**Scenario.** A contributor token sends POST /ingest {text, meta: {photo: {thumb: "C:/prax-data/prax.db"}}}. It could also send a relative path such as "../prax-data/prax.db". The door keeps the client's meta unchanged (api/capture.py:63, meta=req.meta). photo_copy then reads json_extract(meta,'$.photo.thumb') and passes it to archive_path(digest), which is config.archive_dir()/digest[:2]/digest. An absolute digest replaces the whole path, and '..' climbs out of the archive. Because the document is open, any reader or contributor token (thumb and preview are in READER_ROUTES since 447654d) can then fetch GET /doc/N/thumb and get the bytes. That covers the whole SQLite file (every personal document's metadata, the token hashes, edges, faces), prax.yaml with the owner's private names and paths, and any file the door process can read. Reading prax.db this way bypasses the wall completely.

**Evidence.** Throwaway test in the scratchpad (test_probe3.py) against a tmp PRAX_DATA_DIR. A contributor ingested text with meta.photo.thumb set to the tmp prax.db, first as an absolute path and then as '../<dir>/prax.db'. A reader token's GET /doc/1/thumb answered 200 both times, and the body began b'SQLite format 3\x00'. Code read: photos.py:140-152 (photo_copy is @_guards only, with no check on the digest), base.py:927-930 (archive_path joins the string unchecked), api/capture.py:25-63 (IngestText.meta passed straight through), api/documents.py:218-230 and 389-402 (the routes). By contrast, figure_blob (readings.py:178) fullmatches [0-9a-f]{16,64} before archive_path.

**The skeptic.** Reproduced in a throwaway test against a tmp PRAX_DATA_DIR (scratchpad skw/test_skw.py::test_thumb). A contributor token sent POST /ingest {text, meta:{photo:{thumb:<absolute path of the tmp prax.db>}}} and got 200. A reader token's GET /doc/1/thumb then answered 200 with a body starting b'SQLite format 3\x00'. The cause, from the code: api/capture.py:59-63 passes req.meta to ingest_text unchanged. photos.py:140-152 photo_copy reads $.photo.thumb and calls archive_path() with no hex check. base.py:927-930 joins archive_dir()/digest[:2]/digest, and on Windows an absolute digest replaces the whole path. My relative try '../prax.db' got 404, but only because I chose the wrong depth: '..' as digest[:2] climbs out of the archive the same way, so a deeper '../../data/prax.db'-style path works. The absolute case alone is enough. It leaks the whole database (token hashes, personal metadata, faces) and prax.yaml past the wall. Fix: in photo_copy, fullmatch [0-9a-f]{64} as figure_blob does before archive_path, and better also inside archive_path. The door should also refuse or strip client-supplied meta.photo (and the other door-written keys) on /ingest.

**Fix proposed.** In photo_copy, refuse any value that is not re.fullmatch('[0-9a-f]{64}'). Better, check inside archive_path itself, so every caller is covered (face_crop and the text_hash readers too). Also strip or refuse keys only the door writes (photo, faces, place, taken, reading, sensitivity…) in client-supplied meta on /ingest, /ingest/url(s) and /ingest/file, using the writer that store.DocumentMeta declares for each key. Add a wall test that posts such meta and expects a 404.

**Done.** `store.archive_path` refuses anything but a digest (16 to 64 hex digits), and `photo_copy` and `face_crop` answer None for one. `POST /ingest` refuses the meta keys only the door writes (`store.DOOR_WRITTEN_META`). Tests: `test_a_copy_is_an_archive_digest_and_nothing_else`, and the wall test now gives the personal document real copies (17).

## 3. A photo whose GPS rational is 0/0 makes the worker's whole pass fail, again on every lease cycle

*high*, logic; `src/prax/parsers/photos.py:86`

**Scenario.** A camera writes GPSLatitude/Longitude, or just GPSAltitude, with a zero denominator ("0/0", common when there is no fix). Pillow turns IFDRational(0,0) into NaN. _degrees and _gps keep it, because `lat == 0 and lon == 0` is False for NaN, so facts come out as {'gps': {'lat': nan, 'lon': nan}}. The worker's p.post goes through httpx 0.28.1, which encodes with allow_nan=False and raises ValueError. That exception leaves Photos.run and run_once (worker.py:86), so every step after photos in that pass (geocode, faces, facevectors, ...) is skipped. After GIVE_UP_AFTER identical failures the worker exits and `prax up` restarts it. When the lease ends, the same picture and its id-order neighbours come back in one batch and fail again, so those pictures are never read and the pass aborts on every lease cycle.

**Evidence.** Ran in the venv: photos._gps on IFDRational(0,0) parts gives {'lat': nan, 'lon': nan}. A real JPEG saved with those GPS tags, read by photos.read, gives facts {'gps': {'lat': nan, 'lon': nan}, ...}, and json.dumps(..., allow_nan=False) raises 'Out of range float values are not JSON compliant: nan'. httpx/_content.py:178 uses allow_nan=False. worker.run_once loops `steps_mod.get(step).run(p)` with no per-step guard; the pass-level except counts repeats toward GIVE_UP_AFTER. The altitude path (photos.py:104) has the same NaN problem.

**The skeptic.** I ran this in the venv. photos._gps on IFDRational(0,0) parts returned {'lat': nan, 'lon': nan, 'alt': nan}. With a valid lat/lon and only the altitude at 0/0 it returned {'lat': 48.0, 'lon': 2.0, 'alt': nan}, so altitude alone is enough to trigger it. httpx.Request(json={'a': nan}) raised 'Out of range float values are not JSON compliant: nan'. Photos.run calls p.post (steps/photos.py), which calls door.post_json, with no guard, and run_once (worker.py) has no per-step try. The exception therefore aborts the whole pass, and because the leased batch comes back after the lease ends, it repeats. Fix: drop non-finite values in _degrees and in the altitude path (math.isfinite).

**Fix proposed.** In _degrees and _gps, return None unless every value is math.isfinite, and check lat/lon ranges (|lat|<=90, |lon|<=180). Drop alt when it is not finite. As defence, apply_photo should refuse or strip non-finite gps values, and Photos._read should never post a non-finite float.

**Done.** `_degrees` keeps only finite degrees in range, an altitude only when finite, and `apply_photo` drops a position that is not one. One step's failure no longer ends the pass (`worker.run_once`, 15). Test: `test_a_position_of_no_fix_is_no_position`.

## 4. Event candidate ids are reused after each re-find, so a decision made from a page already on screen writes recorded_at for a different group's photos

*medium*, data; `src/prax/store/graph/events.py:228`

**Scenario.** replace_event_candidates deletes every open candidate and inserts the groups again. event_candidates.id is INTEGER PRIMARY KEY without AUTOINCREMENT, so the new rows reuse the deleted ids in time order. Several things re-find the candidates: the nightly events pass, split_event and join_event, and every load of the events tab (view-decide.js line 245 posts /graph/events/find). Any new or split group earlier in time moves every later group to a different id. A page loaded before that, in another tab, on a phone, or before the night, still shows candidate 2 as 'May 2019'. Clicking 'event' with that name calls decide_event(2), which now writes 'Holiday 2019' recorded_at facts, and located_in from its cues, for the 2018 photos.

**Evidence.** Read replace_event_candidates (events.py 218-264) and decide_event (422-509): the decision is keyed only by candidate_id, with no check of first_taken or the photo set. Read view-decide.js 245 and 278. Ran scratchpad ds/test_ev.py: two groups (2018, 2019) found, then a 2017 group added and found again, then decide_event called with the 2019 group's old id. Output: before {1: 2018, 2: 2019}, after {1: 2017, 2: 2018, 3: 2019}, 'decided id 2 wrote facts for docs in b? False in a? True'.

**The skeptic.** Read replace_event_candidates (events.py 218-264). It DELETEs the open candidates and re-INSERTs them, and the INTEGER PRIMARY KEY (migration 0051) reuses the freed ids in time order. decide_event checks only that _open_candidate(candidate_id) exists. view-decide.js:245 posts /graph/events/find on every load of the tab. Ran the test: before {1:2018, 2:2019}; after adding a 2017 group and finding again, {1:2017, 2:2018, 3:2019}; decide_event(2, name='Holiday 2019') wrote its facts for the 2018 photos ('in b? False in a? True'). Fix: give the table AUTOINCREMENT or keep the ids of unchanged groups, and have the decide request carry first_taken or a hash of the photo set, checked before anything is written.

**Fix proposed.** Make a candidate's identity stable and check it. Either keep the ids of groups that are found again (match on photo-set overlap, as replace_face_groups does) and use AUTOINCREMENT so an id is never reused, or have POST /graph/events/{id} send the candidate's first_taken/last_taken (or found_at) and refuse with 409 when they no longer match. Also stop re-finding on every page load.

**Done.** A group of the same photos keeps its candidate's id, and a new one takes an id no candidate ever had (`id_marks`, migration 59). A page shown before the groups were found again decides the photos it showed, or answers 404. Test: `test_an_event_candidate_keeps_its_id_and_none_is_given_twice`.

## 5. Placing or retiring a town's first picture ends the town's located_in fact, which every other picture of that town relies on

*medium*, data; `src/prax/store/graph/geo.py:136`

**Scenario.** apply_place writes 'town located_in region' and 'region located_in country' only once, sourced to the first picture placed there (_within skips them when find_edges finds them). Picture 2 of the same town writes only its own taken_in. Later the first picture is placed again with another answer (a new geocode version, a corrected position, 'nowhere'). apply_place then ends every geonames edge sourced to that picture, including the shared located_in. retire_set and retire_document do the same, since they end every edge whose source_doc is the picture. Picture 2 still says taken_in Aldmouth, but Aldmouth is no longer in any region or country, and nothing writes it back until another picture of that town is placed again. The same fact sourced to one picture also means the wall hides the town's located_in whenever that one picture is hidden, though open pictures reach the town.

**Evidence.** Read geo.py 120-185 (the `before` query ends all producer='geonames' edges of the doc, and _within writes once). Ran scratchpad ds/test_geo.py: two pictures placed in Aldmouth, then the first placed again as Brindle. Output: 'both placed: [(Aldmouth, Westshire, 1), (Westshire, Xland, 1)]', then 'd1 moved to Brindle: [(Brindle, Westshire, 1), (Westshire, Xland, 1)]', while taken_in edges for docs 1 and 2 are still live. Aldmouth's located_in is gone.

**The skeptic.** Read apply_place (geo.py 117-185). It ends every live geonames edge sourced to the picture, the shared located_in included, and _within writes that edge once, sourced to whichever picture got there first. Ran the test: with two pictures in Aldmouth, both located_in edges are live and sourced to doc 1. After doc 1 is placed again as Brindle, Aldmouth's located_in is gone, while the taken_in edges of docs 1 and 2 are still live. A full re-geocode pass at a new version would write it back when doc 2 is placed again. A single correction, a retire, or 'nowhere' leaves it missing, and the wall hides the shared fact whenever doc 1 is hidden. Fix: when placing again, end only the taken_in edge (plus any located_in no other live taken_in still needs), or source the containment edges per picture.

**Fix proposed.** Do not let one picture's re-reading end a shared gazetteer fact. Either end only the picture's own taken_in in apply_place and leave located_in edges to a pass that ends one once no live taken_in reaches its town, or write located_in per picture, as the taken_in is, so each picture holds up its own copy. Either way, after ending edges in apply_place or retire, re-check the towns and regions that are still reached and write their located_in again.

**Done.** Each picture states where its town is (`_within` by `document_edges_of`): ending one picture's facts leaves the others'. The gazetteer reading is version 2, so every picture is placed again. Test: `test_a_towns_facts_stand_while_one_picture_states_them`.

## 6. Undoing merge_people does not bring back the people the folded entity had absorbed earlier: they stay merged into the survivor

*medium*, data; `src/prax/store/graph/labels.py:551`

**Scenario.** merge_people(C, into=B) runs, then merge_people(B, into=A). merge_entities flattens the chain, so C's canonical_id is set to A as well. Undoing the second merge (POST /graph/unmerge with its run) runs unmerge_run. It clears canonical_id WHERE id = B OR canonical_id = B, but C now points at A, so C stays folded into A. Afterwards B stands alone and C (B's alias) is still A. Face reads (face_answers, picture_faces) follow COALESCE(canonical_id, id), so C's faces show as A, and depicts and attended walks fold C's pictures into A. The person page undo that 504cad1 offers is therefore not whole whenever the folded person had an alias.

**Evidence.** Read merge_entities (labels.py 36-86: 'UPDATE entities SET canonical_id = ? WHERE id = ? OR canonical_id = ?') and unmerge_run (528-588). Ran scratchpad ds/test_ds.py::test_merge_chain_undo. Output: 'canon: [(2,A,None),(4,B,2),(6,C,2)]', then after undoing B->A: '[(2,A,None),(4,B,None),(6,C,2)]', and 'face of C shows: ... name A'.

**The skeptic.** Read merge_entities (it flattens the chain with canonical_id = into WHERE id = ? OR canonical_id = ?) and unmerge_run, which only clears the entity it folded and its direct pointers. Ran the test: merge C into B, then B into A, then undo B->A. Result: [(2,A,None),(4,B,None),(6,C,2)]; C is still folded into A, and face_answers for C's face shows name A. Fix: record the earlier canonical_id of every entity a merge repoints (in the merge run), and have unmerge_run restore those values rather than NULLing only the directly merged entity.

**Fix proposed.** Record what each merge moved, so an undo can restore it. Either keep the old canonical_id of every alias the flattening repointed (for example a small merge_moves table keyed by run, or merged_run on each repointed row with its previous canonical), or have merge_people refuse an entity that has aliases. Then have unmerge_run point those aliases back at the entity that comes back.

**Done.** A merge with a run records what its flattening moved (`merge_moves`, migration 60), and `unmerge_run` points those back. Test: `test_undoing_a_merge_gives_back_what_the_folded_one_held`.

## 7. /entities shows a reader names that only hidden documents gave, through alias and label matches

*medium*, wall; `src/prax/store/graph/edges.py:62`

**Scenario.** A personal bank statement mentions 'Landlord Zebrafinch'. The administrator folds that entity into an open person 'Jane Doe' with POST /people/{id}/same (store.merge_people, new in 504cad1), or any resolution merge does the same. Labels written from a hidden document (keep_printed, label_in_language with source_doc) have the same effect. find_entities matches q against every label of every alias, and _said_openly only asks whether the canonical group has one visible edge. A reader's GET /entities?q=zebra (a reader route since 447654d) therefore returns Jane Doe with "as": "Landlord Zebrafinch". That reveals the full hidden name from a substring, and the fact that it is the same person. GET /traverse?entity=Landlord Zebrafinch then answers with Jane Doe's edges, which confirms it. person_page drops labels whose source_doc is hidden, but find_entities does not.

**Evidence.** Throwaway test (test_probe2.py). Before the merge, /entities?q=zebra for a reader gave []. After store.merge_people(land, jane) it gave [{'id': 2, 'name': 'Jane Doe', 'type': 'person', 'as': 'Landlord Zebrafinch', 'degree': 1}], and /traverse with the hidden name returned Jane Doe's edge. Code read: edges.py:62-92 (the label CTE has no viewer filter) and people.py:236-245 (the label filter find_entities lacks).

**The skeptic.** Reproduced (skw/test_skw.py::test_entities). Jane Doe was linked from an open paper, and 'Landlord Zebrafinch' only from a bank document marked personal. Before the merge, a reader's GET /entities?q=zebra gave []. After store.merge_people(zebrafinch, jane) it gave [{'id':2,'name':'Jane Doe','type':'person','as':'Landlord Zebrafinch','degree':1}]. GET /traverse?entity=Landlord Zebrafinch then answered 200 with Jane Doe's authored_by edge. In edges.py:62-92 the label CTE has no viewer filter, and _said_openly (edges.py:99-117) only asks whether the canonical group has one visible edge, so a label or alias that only hidden documents gave is matched and shown in 'as'. It does need an administrator's merge, or a label whose source is a hidden document, but both are normal operations. Fix: in find_entities, skip label matches whose label source_doc is hidden, or whose alias entity is said only by hidden documents, as person_page already does. traverse's name lookup needs the same filter.

**Fix proposed.** When hidden is non-empty, only match a label whose source_doc is NULL or visible, and whose from_entity (or alias entity) has a live edge from a visible document or from none. Drop 'as' when the matching label fails that test. Apply the same visibility test to the alias names traverse resolves (resolution by an alias that only hidden documents speak of should answer as unknown).

**Done.** A hit found only by another name is kept for a named token when one of the names that matched came from no hidden document and its entity has a fact the token sees (`edges._open_label`). Test: `test_a_name_only_a_hidden_document_gave_finds_nothing`.

## 8. The face-matching cache ignores merges and unmerges, so offers stay split under the folded entity

*medium*, logic; `src/prax/store/retrieval/faces.py:650`

**Scenario.** Two entities, 'Ada' and 'Lovelace', name faces of one person. Because the two look alike, the votes split and the margin rule offers nothing. The user then folds them with merge_people (or unmerge_run, or `prax resolve`). _match_state is built only from face_names/face_refusals max(id) and live counts plus the face vectors, and none of those change on a merge (merge_entities only sets entities.canonical_id). So _matched serves the stale result: votes still keyed by the folded id, `in_picture` and refusal exclusions under the old ids, and face_matches/picture_faces offer nothing. This lasts until some unrelated answer happens to change the state. The module comment says the cache is rebuilt 'once an answer, a refusal or a face's vector is new'. The task's contract, invalidation on rename and merge, is not kept for merges.

**Evidence.** Throwaway test against a tmp store, using the existing _grouped fixture: named a[:2] 'Ada' and a[2:4] 'Lovelace', called face_matches (no offers), then merge_people(lov, ada). face_matches still returned [] items. After clearing faces._MATCHED by hand it returned [(2, 'Ada', 10 open)]. grep shows _MATCHED is written only at faces.py:732 and nothing in labels.merge_entities or unmerge_run touches it.

**The skeptic.** I read faces.py:650-732. _match_state hashes only face_names and face_refusals (max(id), live count), the faces' count and max(vector_at), hidden, k and model. The reads inside resolve COALESCE(e.canonical_id, e.id), but the key does not include it. labels.merge_entities only UPDATEs entities.canonical_id and merged_by, plus an INSERT into entity_labels. Nothing in merge_people, merge_entities or unmerge_run touches face_names or _MATCHED (grep). After a merge or unmerge the stale votes keyed by the old ids are served. Fix: add something that moves on a merge to the state, for example max(merged_run) or a sum over canonical_id, or clear _MATCHED in merge_people and unmerge_run.

**Fix proposed.** Add the merge state to _match_state, for example `SELECT count(*), max(merged_run) FROM entities WHERE canonical_id IS NOT NULL`. Or keep a module-level generation counter that merge_entities, unmerge_run, rename_person and merge_people bump, and include it in the state.

**Done.** The cache key holds the canonical ids of the named and refused entities (`_match_state`). Test: `test_offers_follow_a_merge`.

## 9. photos_due stops after 4*limit unreachable referenced pictures, starving every later picture

*medium*, logic; `src/prax/store/documents/photos.py:63`

**Scenario.** A host maps two roots, nas and usb, and the usb share is unplugged. The query does not leave out pictures whose places are all on an absent root; it only adds `held = 1` when no root is present at all. Each such picture costs a check, and the loop breaks at `checks >= 4 * limit`. With 40 or more unreachable referenced pictures early in id order (an unmounted or unmapped root, or files moved or touched on a mounted share since the crawl), photos_due returns [] on every call. The photos step, and through it geocode, faces and facevectors, never reaches the reachable pictures that come later. The docstring says 'A root whose directory is absent is passed over whole', which is not what happens.

**Evidence.** Throwaway test: places.roots with nas and usb; registered 40 referenced JPEGs on usb, deleted the usb directory, then registered 1 on nas (doc 41). store.photos_due(con, VERSION, limit=10) returned [] while doc 41 was reachable.

**The skeptic.** I read store/documents/photos.py:38-73. roots leaves out absent directories, but the query adds `held = 1` only when no root is present at all. A referenced picture on the absent root still costs `checks += 1`, _place_file returns None, and the loop breaks at checks >= 4*limit. Forty or more unreachable referenced pictures early in id order make every call return []. That contradicts the docstring ('a root whose directory is absent is passed over whole'). The step's only caller (steps/photos.py hand_out) passes skip = leased only, so nothing else gets past them. Fix: leave out in SQL the pictures whose places are all on absent roots, or do not count a file whose root is absent as a check.

**Fix proposed.** Filter in SQL to documents with a live place on a present root, for example `held = 1 OR EXISTS (SELECT 1 FROM places p WHERE p.sha256 = d.hash AND p.gone IS NULL AND p.root IN (<present roots>))`. Count only file checks on present roots against the budget, and do not break: skip past them, or keep a cursor so the next call continues after the last id it looked at.

**Done.** The query leaves out a picture with no live place on a mounted root, and a picture whose file on a mounted root is not the one hashed rests an hour (`UNREACHED_REST`) instead of costing every call. Test: `test_pictures_out_of_reach_never_hide_the_ones_in_reach`.

## 10. Towns and regions are graph nodes by name alone, so homonyms merge and the closure puts places in the wrong country

*medium*, logic; `src/prax/store/graph/geo.py:102`

**Scenario.** Photos taken in Paris, France and in Paris, Texas both write taken_in 'Paris' (place), plus 'Paris located_in Île-de-France' and 'Paris located_in Texas'. located_in is transitive (core.yaml:159), so derive_rules then writes INFERRED 'Paris located_in France' and 'Paris located_in United States'. The same happens with a region that shares a country's name: Georgia the US state and Georgia the country make Tbilisi located_in United States. decide_event's located_in for an event (events.py:484) uses the same name-only place. The geonameid is kept in meta.place but never used as the node's identity.

**Evidence.** Throwaway test: apply_place on two photos with place Paris/Île-de-France/France and Paris/Texas/United States, then store.derive_rules. Live edges: Paris located_in France (rule:transitive), Texas (geonames), United States (rule:transitive), Île-de-France (geonames); both photos taken_in the one 'Paris'.

**The skeptic.** I read geo.py:89-185. _within and the taken_in edge use Edge(town, 'place', ...) by name alone. The geonameid goes into meta.place and the evidence string only, never into the node's identity. The same town name with two different regions gets two located_in edges from one node. core.yaml makes located_in transitive, so the rule pass spreads the error to both countries. Nothing in the logs or plan marks it as deliberate. Fix: give a place node an identity that tells homonyms apart, such as 'Paris, Texas', a qualified name, or a label keyed by geonameid.

**Fix proposed.** Key a gazetteer place by its GeoNames identity. Disambiguate the node name when a different geonameid already owns it (for example 'Paris, Texas', or the town with its region), or record the geonameid as a label or identifier and look it up before link(). Do the same for regions, keyed by CC.admin1, and countries, keyed by cc.

**Done.** A gazetteer place is a node by its name until the gazetteer put a node of that name elsewhere; then it is `Paris, Texas`, and a country named as a region elsewhere is `Georgia (country)` (`geo._place_node`). Test: `test_two_places_of_one_name_are_two_nodes`.

## 11. A new faces reading orphans every named face: answers vanish and cannot be undone while depicts stays live

*medium*, logic; `src/prax/store/documents/photos.py:261`

**Scenario.** faces.VERSION is bumped (a new detector or rule; the module says 'every picture is read again'), or a picture is otherwise re-read. apply_faces then sets gone_at on all of the picture's faces and inserts new rows. Nothing carries face_names, face_refusals or face_group membership over to the new faces. Every read joins on `f.gone_at IS NULL` (picture_faces, face_answers through _faces, _matched's named set, face_groups), so all the owner's answers (about 2,000 named faces per the log) disappear from the UI and from matching, and the old faces come back as open. The depicts edges those answers hold stay live. unname_faces(run=...) calls _faces() on the gone faces and raises 'no such face', so the facts can no longer be taken back through the UI either.

**Evidence.** Read apply_faces (photos.py:243-281): it ends all live faces of the doc and inserts new ones, with no reference to face_names. grep for gone_at writers finds only this UPDATE. naming._faces requires gone_at IS NULL and raises ValueError otherwise, and unname_faces calls it for run undo (naming.py:337).

**The skeptic.** I read apply_faces (photos.py:243-281). It sets gone_at on all of the picture's live faces and inserts new rows, and carries no face_names, face_refusals or group membership over. naming._faces requires gone_at IS NULL and raises 'no such face'. unname_faces(run=...) calls _faces(ids + said_not) before ending answers (naming.py ~337), so a run on a gone face cannot be undone while its depicts edge stays live. Today this is latent, because faces_due re-reads only when faces.VERSION (now 1) is bumped. The first bump orphans every answer the owner has given. Fix: carry the answers over to the new faces by box overlap, or end their answers and depicts edges along with them, and let undo work on gone faces.

**Fix proposed.** When a picture is re-read, match each new face to an ended one by box overlap (IoU at or above some line) and move its live face_names and face_refusals to the new face id, or record a successor column on faces and have the reads follow it. Let unname_faces take back answers of gone faces, ending their edges, instead of refusing.

**Done.** As 1.

## 12. A picture past Pillow's bomb limit crashes the whole worker pass, again and again

*medium*, input; `src/prax/steps/photos.py:107`

**Scenario.** A crawl with --reference (or an upload) brings in a picture over 2 x Image.MAX_IMAGE_PIXELS (about 179 MP, for example a stitched panorama or a scanned poster). photos.read calls Image.open, which raises PIL.Image.DecompressionBombError. That class derives from Exception, not from OSError or ValueError. photos.read catches only (UnidentifiedImageError, OSError, ValueError), and Photos._read catches only ValueError, so the error goes up through Step.run and run_once into watch(). The pass is lost, and so is every step after photos in STEPS that pass: geocode, faces, facevectors and the packs' steps. The picture is never marked unreadable (photo_failed is never called). Once its lease runs out, photos_due hands it out first again (oldest first), and the pass fails the same way. After GIVE_UP_AFTER (20) passes the worker exits, `prax up` starts a new one, and the loop repeats. One file stops the photo, face and geocode work for the whole library. Pictures between 89 MP and 179 MP only warn, and are then decoded in full and copied up to four times (load, exif_transpose, convert, and copy() for each of the two JPEGs).

**Evidence.** Read parsers/photos.py (`_open`, `read`), steps/photos.py `_read`, worker.py run_once/watch (no per-step try, GIVE_UP_AFTER=20), steps/__init__.py order (photos before geocode, faces, facevectors). Ran a scratch script that sent a 20000x20000 PNG header through photos.read under Pillow 12.3.0 (MAX_IMAGE_PIXELS 89478485). Output: `bomb 20000x20000 UNCAUGHT (<class 'PIL.Image.DecompressionBombError'>, <class 'Exception'>, ...)`. Truncated or garbage PNG and JPEG input gave the caught ValueError, and 400 random EXIF corruptions were all read ok.

**The skeptic.** I reproduced it. I built a 20000x20000 1-bit PNG in memory and passed it to prax.parsers.photos.read under the repo venv. It raised PIL.Image.DecompressionBombError, whose MRO is (DecompressionBombError, Exception, ...). read() catches only (UnidentifiedImageError, OSError, ValueError), and Photos._read (src/prax/steps/photos.py:104-107) catches only ValueError around photos.read. Nothing in src, clients or scripts changes Image.MAX_IMAGE_PIXELS or catches DecompressionBombError. run_once (worker.py:86) has no try around each step, so the exception escapes to watch()'s per-pass handler. That handler counts identical failures and exits after GIVE_UP_AFTER=20. photos runs before geocode and faces in STEPS. photos_due orders by id and has no failure mark (photo_failed is never reached), so the same picture comes back first once its lease ends. Fix: catch Image.DecompressionBombError in photos.read and turn it into ValueError (unreadable). Better, call Image.open with a pixel limit or use draft()/reduce, and catch broadly per item in Photos._read, as the fetch branch already does.

**Fix proposed.** In photos.read, catch Image.DecompressionBombError (and MemoryError) next to OSError and turn them into the ValueError, so the picture is marked unreadable with a reason. In Photos._read, also catch any other Exception per picture as `unreadable` or `error`, as the faces step's fetch does. Optionally set Image.MAX_IMAGE_PIXELS to an explicit limit and decode large JPEGs with img.draft('RGB', (PREVIEW_PX, PREVIEW_PX)) or reduce() before transposing. Add a test that a bomb-sized header is marked failed and is not handed out again.

**Done.** `photos.read` turns any failure of a picture's bytes into its `ValueError` (a `DecompressionBombError` included), and the step marks anything `photos.read` raises as that picture's. Test: `test_a_picture_past_the_pixel_limit_is_that_pictures_trouble`.

## 13. A cut-short download becomes the final file: GeoNames, YuNet or SFace then fail every pass until someone deletes it by hand

*medium*, input; `src/prax/ml/fetch.py:102`

**Scenario.** The connection drops partway through `fetch.download` of cities1000.zip, a YuNet or SFace ONNX file, or a GGUF. http.client's read(amt) returns b'' on an early close instead of raising IncompleteRead, so the loop ends, `part.replace(dest)` runs, and the truncated file is installed. Content-Length is read but never compared with the bytes received. After that, gazetteer.available() is true because the file exists, and load() raises zipfile.BadZipFile on every Geocode.run. faces._session and faces._recognizer raise 'is not the YuNet/SFace file' on every pass. The sha256 pin catches the bad bytes but leaves them in place, so the error repeats. None of these is caught per step, so each pass fails from that step on, and the worker gives up after 20 passes and is restarted into the same failure. A truncated admin1CodesASCII.txt or countryInfo.txt fails silently instead: places get no region or country, with no error anywhere.

**Evidence.** Read fetch.download, gazetteer.load and available, and faces._session/_recognizer. Ran a scratch script with a local socket server that sends `Content-Length: 100000` and then 1000 bytes before closing. fetch.download returned normally and printed `dest exists: True size: 1000`.

**The skeptic.** I reproduced it with a local socket server that sends Content-Length: 100000 and then 1000 bytes before closing. fetch.download returned normally and the destination file was 1000 bytes. The loop at fetch.py:104-111 stops on an empty read and never compares the total with Content-Length; part.replace(dest) follows. After that, model_file and gazetteer.load skip the download because dest.exists() is true. gazetteer.load would fail on the zip on every Geocode pass, outside any per-item try. faces._session and _recognizer raise RuntimeError on the sha mismatch on every pass and leave the bad file in place. faces.detect is called outside the try in Faces._read (steps/faces.py:107), so the whole pass fails and the worker eventually gives up. Fix: if Content-Length was given and the bytes received are short, keep the .part file and raise instead of installing it. Also unlink a file that fails the sha pin (or a zip that will not open) so the next pass fetches it again.

**Fix proposed.** In download(), compare `done` with the expected total (Content-Length plus the resumed offset, or the total in Content-Range) and raise without renaming when they differ, keeping the .part file for the resume. In faces._session and _recognizer, unlink the file on a sha256 mismatch so the next call fetches it again. In gazetteer.load, catch BadZipFile, remove the zip, and let Geocode.run return None (or raise one clear error) instead of crashing the pass.

**Done.** `fetch.download` raises when fewer bytes came than announced, and keeps the part for the next run. A model file that fails its sha256 is deleted, and a GeoNames zip that is not one too. Test: `test_a_download_cut_short_is_not_the_file`.

## 14. One file name with ':' at the end of a part or with a backslash rejects its whole batch and leaves every later crawl unfinished

*medium*, input; `src/prax/store/documents/places.py:108`

**Scenario.** A NAS holds a folder such as `photos/Re:` or `Notes:`, or a file whose Linux name contains a backslash. All three are legal on ext4 and btrfs shares. prax_send --places sends it in a batch of BATCH entries. record_places raises ValueError on the first entry `_parts` refuses, and the door answers 400 for the whole batch, so the good entries are not recorded either. prax_send treats any HTTPError as fatal: it prints 'the door refused … (the token? the share's name?)' and stops. Every file after that batch is never recorded or referenced, and /places/done is never sent, so nothing is ever marked gone. Every later crawl stops at the same file for as long as it exists.

**Evidence.** Read _parts, record_places, api/places.py record_places and ingest_reference (ValueError becomes 400), and prax_send.places (HTTPError returns 2). Ran a scratch pytest against a tmp door: POST /places with a good entry plus `photos/Re:/x.jpg` gave `400 {"detail":"path: relative to its root with '/', not 'photos/Re:/x.jpg'"}` and recorded neither entry.

**The skeptic.** I confirmed it directly. _parts('photos/Re:/x.jpg') returns None, and so does a path holding a real backslash (chr(92)). store record_places raises ValueError on the first such entry (places.py:246-247) and so refuses the whole batch. api/places.py turns that into a 400. In prax_send.places, `except HTTPError` returns 2 before /places/done is posted, so every later file in the walk is never recorded. relative() only swaps os.sep for '/', so on a Linux client a name with a backslash or a trailing ':' in a part reaches the door unchanged. Every crawl stops at the same file. Fix: skip and report refused entries one at a time (the door returning per-entry errors rather than a 400 for the batch, or the client filtering with the same rule). Accept a backslash or a trailing ':' as an ordinary character where the serving host can still name the file, and refuse it only where it cannot.

**Fix proposed.** Have record_places and register_references skip a refused entry and return it in a `refused` list instead of failing the batch. Have prax_send count those as failed, which already leaves the crawl open, and keep going. Alternatively, have prax_send drop such names on the client before posting and count them as failed.

**Done.** `record_places` and `register_references` leave out an entry they cannot take and list it as `refused`; `prax_send` counts each as failed, so the crawl stays open and goes on. Tests: `test_a_name_the_door_cannot_take_leaves_the_rest_of_its_batch`, and the places tests now expect `refused`.

## 15. A geocode or face model fetch that fails stops every later step, and the worker restarts in a loop

*medium*, contracts; `src/prax/steps/photos.py:149`

**Scenario.** Take a fresh host with photos that has no GeoNames files and cannot reach geonames.org, with PRAX_OFFLINE unset (a LAN-only Pi, or the site down). gazetteer.available() says True because fetching is allowed. Geocode.run then calls _gazetteer(), whose load() calls fetch.download, and that raises. Nothing in the step catches it, and worker.run_once runs the steps in a plain loop, so the exception leaves the whole pass. faces, facevectors and the maths pack's equations never run. functools.cache does not keep exceptions, so every pass fails the same way. After GIVE_UP_AFTER (5) passes the worker exits, prax up restarts it, and the cycle repeats. The faces steps have the same hole: faces.available() only checks that onnxruntime and numpy import, and the YuNet/SFace fetch in _session()/_recognizer() runs inside the pass. A model file that exists but fails its sha256 (an interrupted or captive-portal download kept as the file) raises RuntimeError on every pass for ever, because model_file returns the existing file and never fetches it again. The gazetteer zip has the same problem, as a BadZipFile.

**Evidence.** I read steps/photos.py Geocode.run, graph/gazetteer.py available()/load(), ml/faces.py available()/_session()/_recognizer(), ml/fetch.py model_file(), worker.run_once and watch(). I ran a throwaway script (scratchpad t1/geo.py) on a tmp data dir with fetch.download raising ConnectionError and a fake Pass that hands geocode one position, then called worker.run_once(steps=(geocode, faces, facevectors, equations)). Output: "run_once raised: ConnectionError no network" and "steps fetched: ['geocode']". The later steps were never asked for work.

**The skeptic.** Confirmed. Geocode.run (src/prax/steps/photos.py:147-160) calls _gazetteer() -> gazetteer.load() -> fetch.download with no try. gazetteer.available() is true whenever PRAX_OFFLINE is unset. worker.run_once (src/prax/worker.py:86-92) runs the steps in a plain loop with no guard. In the faces step, faces.detect() runs outside the per-picture try (steps/faces.py:107), so a failure in _session()'s fetch or sha256 check also escapes, and fetch.model_file returns an existing bad file without fetching it again. I reproduced it in scratchpad sk/geo.py on a tmp data dir, with fetch.download raising and a fake Pass. Output: 'raised ConnectionError no network' and fetch asked only ['geocode'], so faces and facevectors never ran. One correction: the worker loop (watch) catches the exception per pass and gives up after GIVE_UP_AFTER = 20 passes of the same kind, not 5. The steps that come after keep starving, and the restart loop is as described. Fix: catch load or fetch failures in each step's run(), note them and return a line so later steps still run. Also delete a model file whose sha256 is wrong so the next run fetches it again.

**Fix proposed.** Make a missing or unfetchable resource a skipped step. Catch fetch or load errors in Geocode.run and Faces/FaceVectors.run and return a one-line note, with a back-off so the fetch is not retried on every pass. Have faces.available() and gazetteer.available() report False when the files are absent and cannot be fetched, or when they fail their checksum. Delete a file that fails its sha256 so the next fetch starts over. Also consider isolating each step in run_once (try/except per step, then go on), so one broken step cannot stall every step after it.

**Done.** A step loads its model or data before it fetches (`steps.base.ready`); one that cannot be had rests an hour and says so in the pass's line. `worker.run_once` runs every step and then fails the pass with the first failure, so a repeated failure is still counted. Test: `test_a_step_whose_resource_fails_rests_and_the_others_run`.

## 16. A picture past Pillow's pixel limit stalls the photos step and every step after it

*medium*, contracts; `src/prax/parsers/photos.py:159`

**Scenario.** Put on the share a picture over 2 x Image.MAX_IMAGE_PIXELS (about 179 MP), for example a stitched panorama or a scanned map. photos.read() catches only UnidentifiedImageError, OSError and ValueError. Pillow raises DecompressionBombError, which subclasses Exception and none of those. Photos._read catches only the fetch, so the exception leaves Photos.run and then run_once. The picture is never marked unreadable, so it is handed out again on every pass (photos_due, oldest first), and every pass fails at the same place. geocode, faces, facevectors and equations stop, and the worker exits after five passes into prax up's restart loop.

**Evidence.** I read parsers/photos.py read()/_open() and steps/photos.py Photos.run/_read. I ran a throwaway script (scratchpad t1/ph.py) with photos.read on a 2000x1500 JPEG and Image.MAX_IMAGE_PIXELS lowered to 1,000,000 to stand in for a 180 MP picture. Output: "bomb UNHANDLED DecompressionBombError Image size (3000000 pixels) exceeds limit...". A truncated JPEG, by contrast, was handled ("ValueError (handled): not a picture this host reads (OSError)").

**The skeptic.** Confirmed. parsers/photos.read catches only UnidentifiedImageError, OSError and ValueError (around line 157). In Pillow 12.3.0, DecompressionBombError's MRO is (DecompressionBombError, Exception), so it is none of those. I ran scratchpad sk/ph.py with MAX_IMAGE_PIXELS lowered to 1,000,000 and a 2000x1500 JPEG. Output: 'UNHANDLED DecompressionBombError'. Photos._read (steps/photos.py:98-108) catches only the fetch and a ValueError, so the exception leaves run() and run_once. The picture is never posted as unreadable, so after its lease it is handed out again, and every pass stalls at the same place. Fix: catch Image.DecompressionBombError, or any Exception, in read() or _read and report it as 'unreadable'.

**Fix proposed.** In photos.read(), also catch Image.DecompressionBombError, so the picture becomes 'unreadable' and is marked. A better option is to open it with draft()/reduce for the preview, so a very large picture still gets its thumbnail. As a backstop, Photos._read could turn any exception from photos.read into an 'unreadable' result for that one picture.

**Done.** As 12.

## 17. The wall's thumb/preview check uses a document that has no thumbnail or preview, so it passes with the guard removed

*medium*, tests; `tests/test_wall.py:322`

**Scenario.** test_the_web_page_with_a_named_token expects 404 from /doc/{bank}/thumb and /doc/{bank}/preview. But `bank` is a text document with no meta.photo, so store.photo_copy returns None and the route answers 404 whether or not @_guards runs. If a refactor drops the guard on store.photo_copy, a named token without --personal gets the screen-size JPEG of every suspected or personal photo (referenced photos are suspected from registration). The suite stays green.

**Evidence.** Read src/prax/store/documents/photos.py:138-152 (photo_copy is @_guards; it returns None when meta.photo has no copy) and src/prax/api/documents.py:389-392 (None becomes a 404). In a scratch test, I monkeypatched prax.store.photo_copy to its unguarded __wrapped__ and ran tests.test_wall.test_the_web_page_with_a_named_token: it passed. No test in test_photos.py or test_referenced_originals.py requests thumb or preview as a named token.

**The skeptic.** Confirmed. In tests/test_wall.py:319-320, `bank` is an ingest_text document with no meta.photo. That means store.photo_copy (src/prax/store/documents/photos.py:138) returns None and the route in src/prax/api/documents.py:389 answers 404 whether or not @_guards runs. In a scratch test I monkeypatched store.photo_copy (and the api module's store reference) to photo_copy.__wrapped__ and ran test_the_web_page_with_a_named_token: it passed. A grep of tests/ shows thumb and preview are fetched only as the administrator (test_photos.py:148-152) and in the route list at test_wall.py:153-154. Fix: in the wall test, give a suspected document a photo copy (photos' apply with copies={'thumb':..., 'preview':...}, as test_photos.py:225 does), assert 404 for the named token and 200 for the administrator.

**Fix proposed.** In _library, or in a test of its own, give the personal document photo copies (store.register a JPEG, then store.apply_photo with copies={'thumb':..., 'preview':...}, then set_sensitivity 'personal'). Assert a 404 for the named token and a 200 for the administrator, so the 404 can only come from the guard.

**Done.** The wall test gives the personal statement a thumbnail and a preview, asks for both as the named token (404) and as the administrator (200), and an open document's as the named token (200).

## 18. The chain rule's guard against mixed sensitivity is untested: removing it leaks an open `attended` fact from a personal picture

*medium*, tests; `tests/test_rules.py:344`

**Scenario.** rules.chain refuses two premises from different documents unless both are open (`elif rank1 == 0 and rank2 == 0 ... else: continue`). No test reaches that branch. The 'her in one picture, an event of another's' case links the two premises to different picture nodes, so no chain can form there at all. If the guard regresses so that any pair of premises gets source None, the rule writes `attended` with source_doc NULL from a `depicts` sourced to a suspected picture plus a `recorded_at` from an open one. That fact is unsourced and visible to every named token, and all tests pass.

**Evidence.** Read src/prax/store/graph/rules.py:108-139. Scratch test: I monkeypatched rules.chain with a copy that has no `rank1 == 0 and rank2 == 0` check. test_who_a_picture_of_an_event_depicts_attended_it and test_a_chain_across_two_open_documents both passed under it. A mixed case passed with the real code ({}) and failed under the mutation with `{('Ada', 'A trip'): None}`. Mixed case: one picture node, recorded_at sourced to an open document, depicts sourced to a suspected one.

**The skeptic.** Confirmed. The 'lone'/'other' case in test_rules.py:359-362 links its two premises from different picture nodes, so no middle is shared and the `rank1 == 0 and rank2 == 0` branch at rules.py:129 is never reached. In a scratch test I replaced rules.chain with a copy that has no rank check (anything not from one document gets source None). test_who_a_picture_of_an_event_depicts_attended_it and test_a_chain_across_two_open_documents both still passed. A mixed case (one picture node, recorded_at sourced to an open document, depicts sourced to a suspected one) gave {} with the real code and {('Ada','A trip'): None} under the mutation: an unsourced fact that a named token could see. Fix: add that mixed case to test_rules.py.

**Fix proposed.** Add that mixed case to tests/test_rules.py: one picture node, depicts sourced to a suspected document, recorded_at sourced to an open one. Assert that derive_rules writes no `attended`.

**Done.** `test_no_chain_across_a_hidden_and_an_open_document` in `tests/test_rules.py`.

## 19. rename_person back to a name the person had before fails with an IntegrityError, and the rename run takes over the run of the label it marks

*low*, data; `src/prax/store/graph/naming.py:434`

**Scenario.** A person 'Anna' (baseline pref label, lang NULL) is renamed to 'Anne', then renamed back to 'Anna'. The second call's add_label('Anna', lang=en, kind=pref) first runs 'UPDATE entity_labels SET lang = en ... AND lang IS NULL' on the old 'Anna' row, which is still kind 'pref'. That gives two pref labels in 'en' and raises 'UNIQUE constraint failed: entity_labels.entity_id, entity_labels.lang'. The route answers with an error, so the user cannot rename someone back. Undoing the first rename does work. Separately, rename_person's 'UPDATE entity_labels SET was = 1, run = ?' overwrites the run of the existing label. A later unmerge_run of the label's original run no longer finds it. If the label is renamed twice, both labels end up was=1 under the second run, and undoing that run sets both to pref.

**Evidence.** Read rename_person (naming.py 397-439) and add_label (labels.py 278-376). Ran scratchpad ds/test_ds.py::test_rename_back_and_forth: rename_person(eid,'Anne') then rename_person(eid,'Anna') raised sqlite3.IntegrityError at labels.py:315 (UNIQUE constraint failed: entity_labels.entity_id, entity_labels.lang). The call's work was rolled back by _guarded, so no partial state remained.

**The skeptic.** Ran test_rename_back_and_forth on a tmp store. rename_person(eid,'Anne') followed by rename_person(eid,'Anna') raised sqlite3.IntegrityError at labels.py:315 (UNIQUE constraint failed: entity_labels.entity_id, entity_labels.lang). add_label's lang backfill UPDATE runs on the old NULL-lang 'Anna' row while it is still kind pref, next to the existing en pref. _guarded rolls the write back, so no state is corrupted; renaming someone back to an earlier name simply fails. I read but did not reproduce the second half: rename_person's 'SET was = 1, run = ?' overwrites the label's original run, so an undo of that original run no longer finds the label. Fix: demote the existing pref in that language (or set the old row's kind to alt) before the lang backfill, and keep the label's original run (for example in a separate column) instead of overwriting it.

**Fix proposed.** In add_label, demote the other pref labels of the language, including one whose lang is NULL and is about to take this language, before setting lang on the existing row. Or, in rename_person, demote the old name's label to alt explicitly before writing the new pref. Do not overwrite an existing label's run: record the rename's claim separately (for example a was_run column, or a new label row), so unmerge_run(rename-run) and the label's own provenance both survive.

**Done.** `add_label` demotes the language's preferred name before a name of no language takes that language. Left: a rename marks the old label with its own run, so an older rename's undo after a newer rename's leaves the newer old name; the person page offers the undo right after the action. Test: `test_a_person_renamed_back_and_to_a_folded_ones_name`.

## 20. The wall test's thumb/preview cases cannot fail

*low*, wall; `tests/test_wall.py:319`

**Scenario.** test_the_web_page_with_a_named_token asserts that GET /doc/{bank}/thumb and /preview answer 404 for a named token. The bank statement is a text document with no meta.photo, so photo_copy returns None and the route answers 404 for the administrator too. If the @_guards on photo_copy were removed, the test would still pass, and test_every_allowed_route_is_walked only checks that the routes appear in the static WALKED list.

**Evidence.** Read tests/test_wall.py:302-321 and _library (no picture is ingested), and photos.py:140-152 (None when $.photo.thumb is absent).

**The skeptic.** Confirmed by reading. In tests/test_wall.py:302-321 the bank document is ingest_text'ed with no meta.photo, so photo_copy returns None at photos.py:148-149. /doc/{bank}/thumb and /preview therefore answer 404 for every caller, the administrator included, and those two assertions cannot fail. One mitigation: removing @_guards from photo_copy would still be caught elsewhere, by tests/test_photos.py:219-235 test_the_copies_are_behind_the_wall, which checks the store guard with a real thumb. What stays uncovered is the route level: a route that served the copy without photo_copy's guard would pass the wall test. Fix: in _library, give the personal document a real photo copy (register an image, then apply_photo with copies), check that the administrator gets 200, then check that the named token gets 404.

**Fix proposed.** In _library, add a personal picture with a filed thumb and preview (or apply_photo on a registered JPEG). Assert that the administrator gets 200 on it, that the named token gets 404, and that an open picture's thumb answers 200 for the named token.

**Done.** As 17.

## 21. A town's located_in fact is sourced only to the first photo placed there, so the wall hides it while open photos show the town

*low*, logic; `src/prax/store/graph/geo.py:103`

**Scenario.** _within writes 'Town located_in Region' once, sourced to whichever picture was placed first, and skips it whenever any live edge with those names exists. Referenced photos are registered 'suspected' by default, so that first source is usually hidden from a named reader. A later photo of the same town that a person releases shows the reader 'photo taken_in Town' but not where the town is. The fact also goes for everyone when the first photo is placed again or ends its geonames edges, while later photos of the town are not placed again.

**Evidence.** Read geo.py:89-114 and 140-185: find_edges(con, edge) checks for any live edge with the same names, whatever its source, and returns 0 without writing. apply_place ends all of the doc's geonames edges before writing. text.register_references marks referenced photos suspected when private.referenced is on (the default).

**The skeptic.** I read geo.py:100-103. If find_edges(con, edge) finds any live edge with those names, whatever its source, _within returns without writing, so the town's located_in is sourced only to the first picture placed there. The wall hides an edge sourced to a hidden document. apply_place ends all of a doc's geonames edges when that doc is placed again, and later pictures of the town do not write the fact back. Fix: write it per source document, the way the rule pass picks the most open source, or write it again whenever the existing one's source is hidden or ended.

**Fix proposed.** Write the located_in edge per source picture, as document_edges_of does for one document, so each picture states it and the wall shows it when any visible picture says so. Or, when the standing edge's source is hidden, add one sourced to the current picture if that picture is more open.

**Done.** As 5: each picture states the town's place, so a visible picture shows it.

## 22. rename_person refuses a name only an alias of the same person carries, naming the person itself

*low*, logic; `src/prax/store/graph/naming.py:416`

**Scenario.** After merge_people(lovelace, ada), the user renames Ada to 'A. Lovelace', the folded alias's own name. The conflict query `WHERE name = ? AND type = ? AND id != eid` finds the alias row, and COALESCE(canonical_id, id) gives eid itself. rename_person then raises "another person (#<ada>) is called 'A. Lovelace': if they are one person, say so with 'same person as'", although they are already one person. A survivor can never take a folded entity's name.

**Evidence.** Read naming.py:416-425 against labels.merge_entities, which keeps the duplicate's row and name and only sets canonical_id. _refresh_name would also refuse to set the cached name while the alias row holds it.

**The skeptic.** I read naming.py:416-425. The conflict query `SELECT COALESCE(canonical_id, id) FROM entities WHERE name = ? AND type = ? AND id != eid` finds the folded alias row, which merge_entities keeps with its own name. COALESCE gives eid itself, and the code raises without comparing other[0] to eid. The error then tells the user to merge two entities that are already one. Fix: skip rows whose canonical is eid, or make the refusal say that the alias already holds the name. Taking the name over would also need the alias row's cached name to change, because _refresh_name refuses it.

**Fix proposed.** Exclude rows whose COALESCE(canonical_id, id) equals eid from the conflict check. To let the survivor show that name, the entities.name uniqueness also has to allow it, for example by moving the alias's cached name aside (it keeps its label) when the survivor takes it.

**Done.** The name check leaves out entities folded into the person, and a folded entity holding the name steps aside (`Name (#id)`, its label kept). Test: as 19.

## 23. A place path part like 'b:x.jpg' passes _parts and resolves outside its root on a Windows door

*low*, input; `src/prax/store/documents/places.py:108`

**Scenario.** _parts refuses '..', '.', empty parts and parts that end with ':', but it accepts a part with a drive letter and more text after the colon, such as `photos/b:evil.jpg` (a legal name on a Linux share). On the Windows door (the desktop serves the store), `Path(root).joinpath('photos', 'b:evil.jpg')` becomes `b:evil.jpg`, a path relative to drive B's current directory and outside the root. _place_file, and so original_file, /doc/{id}/original and the photos step, then stat and serve whatever lies there if its size and mtime match. Any other 'x:' part reads an NTFS alternate data stream of a file inside the root instead. The case is narrow, because places are written only by the administrator's crawl, but the escape is exactly what _parts was written to prevent.

**Evidence.** Ran PureWindowsPath('I:/root').joinpath('a','C:evil'), which gave `C:evil`. A scratch pytest showed `_parts('photos/b:evil.jpg')` returns ['photos', 'b:evil.jpg'], and POST /places with it returned `200 {"recorded":2}`.

**The skeptic.** I confirmed it. _parts('photos/b:evil.jpg') returns ['photos', 'b:evil.jpg'], and PureWindowsPath('I:/root').joinpath('photos','b:evil.jpg') gives 'b:evil.jpg', a path relative to drive B that lies outside the root. A part such as 'ab:x' stays inside the root but names an NTFS alternate data stream. _place_file (places.py:143-146) joins the parts and stats the result without checking that it stays under base. Only the administrator's crawl writes places, so this is low. Fix: refuse any ':' in a part (or, on Windows, a part whose PureWindowsPath has a drive), and check file.resolve().is_relative_to(base.resolve()) in _place_file.

**Fix proposed.** In _parts, refuse any part that contains ':' (and, on the door side, the Windows reserved names and names ending in '.' or a space). Or, in _place_file, check that `file.resolve()` stays under `base.resolve()` (is_relative_to) before stat.

**Done.** `_parts` refuses a `:` in any part. Test: as 14.

## 24. A crawl that silently skipped an unreadable folder still ends, and marks every file under that folder gone

*low*, input; `clients/send/prax_send.py:310`

**Scenario.** walk() calls os.walk(root) without onerror. A subfolder that cannot be listed (permission denied, an SMB or NFS hiccup, a disk error) is skipped without a word and is not counted as `failed`. places() then sees counts['failed'] == 0 and posts /places/done, and end_crawl sets `gone` on every place under that folder. Its referenced documents become unreachable: /doc/{id}/original is a 404, the photos step stops handing them out, and heal lists them. single_copies and the Live Photo clip test also read wrong until a later crawl sees the folder again. The data is recoverable, because a later upsert clears `gone`, but the run reported itself as complete.

**Evidence.** Read prax_send.walk (os.walk with no onerror; only per-file stat and hash errors raise counts['failed']), places() (done is posted when failed == 0), and store.end_crawl.

**The skeptic.** I confirmed it from the code and from a check. os.walk('Z:/nope/none') returns [] with no error, and walk() (prax_send.py:310) passes no onerror, so a folder that cannot be listed is skipped and does not raise counts['failed']. When failed is 0, places() posts /places/done (prax_send.py:787-790). end_crawl then sets gone on every place under the prefix with last_seen < since. I found a worse case of the same gap: run()/parse() never check that `top` exists or holds anything. A mistyped path, or a NAS mount point that is unmounted and empty, walks nothing and still ends the crawl, which marks every place of that root or prefix gone. It is recoverable, because the next successful crawl's upsert clears gone. Fix: pass onerror to os.walk and count it as failed, and refuse to post done when top is missing or the walk saw no files while the door holds live places under the prefix.

**Fix proposed.** Pass an onerror to os.walk in --places mode that counts the error into counts['failed'] (or stats), so a folder that could not be listed leaves the crawl open, as an unreadable file already does.

**Done.** `walk` counts a folder `os.walk` could not list (`unlisted`), and `places` counts those as failed, so the crawl is left open. Test: `test_a_crawl_counts_a_folder_it_could_not_list_and_a_file_that_moved`.

## 25. The size and mtime sent for a place are not the ones its hash was taken under

*low*, input; `clients/send/prax_send.py:769`

**Scenario.** State.hash_of stats the file, then reads and hashes it. places() then calls os.stat(path) a second time and sends that size and mtime with the digest. If the file was written during hashing or just after (a phone sync still copying a video into the share, an app rewriting a picture), the door records (new size, new mtime, digest of the old or partial bytes). _place_file trusts size plus mtime, so original_file then serves the new bytes as the original of a hash they do not have. The referenced document's photo and face readings are filed under the wrong hash, and a later crawl turns the same file into a second document.

**Evidence.** Read State.hash_of (stats before reading and journals that first stat) and places() (a separate os.stat after hash_of, whose values are what gets posted).

**The skeptic.** I confirmed it from the code. State.hash_of stats the file before it reads (prax_send.py:461) and journals that stat. places() then calls os.stat(path) a second time (line 767) and posts those size and mtime values with the digest. If the file changes during or after hashing, the door records the new size and mtime against a digest of the old or partial bytes. _place_file trusts size plus mtime (places.py:150), so original_file would serve the new bytes under the old hash. With a single stat taken before hashing, the mismatch would instead make _place_file refuse the file, which is safe. Fix: have hash_of return the stat it used, post that stat, and re-stat after hashing, treating a change as a failed read for this run.

**Fix proposed.** Have hash_of return the stat it used, stat again after hashing, and treat the file as failed for this run (crawl left open) when size or mtime changed. Then post the first stat's values.

**Done.** `State.hash_with_stat` stats before and after hashing and raises when the size or mtime moved; the crawl posts the stat the hash is true for. Test: as 24.

## 26. Face-match cache is not invalidated by merging or unmerging people, so offers stay stale

*low*, contracts; `src/prax/store/retrieval/faces.py:650`

**Scenario.** _match_state, the key of the _MATCHED cache, is built from face_names, face_refusals, the face vectors, the hidden set, k and the model. It has no part that changes when entities merge. _matched resolves each named face to COALESCE(canonical_id, id) when the cache is built. After POST /people/{id}/same (merge_people), POST /graph/unmerge, or a nightly resolve that merges two persons, face_matches and picture_faces keep serving the old grouping until some face answer changes. A person named under two entities stays split into two voters, and the offers that only the merged person would get are missing. A folded entity can still be offered under its alias row, and the 'already named in this picture' exclusion uses the old ids.

**Evidence.** I wrote a throwaway test (scratchpad t1/test_merge_cache.py) using tests.test_face_naming._grouped. It names a[:2] 'Ada One' and a[2:4] 'Ada Two', calls face_matches (nothing offered, because the two split the vote), runs store.merge_people(ada2, ada), and calls face_matches again. Output: "after merge []". After clearing faces._MATCHED by hand: "fresh [(2, 'Ada One', 10)]". The assertion that the post-merge answer equals the fresh one failed.

**The skeptic.** Confirmed. _match_state (store/retrieval/faces.py:650-665) keys only on face_names, face_refusals, the face vectors, the hidden set, k and model. merge_people (store/graph/naming.py:443) only calls merge_entities, which changes entities.canonical_id, and _matched reads that when it builds its answer. I wrote a throwaway test (scratchpad sk/test_mc.py) with the repository's conftest and tests.test_face_naming._grouped. It names a[:2] 'Ada One' and a[2:4] 'Ada Two', calls face_matches, runs merge_people, then calls face_matches again. Result: after = [], and after clearing _MATCHED, fresh = [(None, 'Ada One', 10)]. The assertion failed. Fix: add something to the state that changes on a merge or unmerge, such as max(merged_run) or the count and max of entities with canonical_id set, or clear _MATCHED in merge_entities and unmerge_run.

**Fix proposed.** Add an entity-merge stamp to _match_state, for example (SELECT count(*), max(merged_at) FROM entities WHERE canonical_id IS NOT NULL) together with the latest unmerge run. Or clear _MATCHED in merge_people, unmerge_run and merge_entities.

**Done.** As 8.

## 27. GET /get/{id} gives a reader token the paths of every copy of a referenced file

*low*, contracts; `src/prax/api/documents.py:134`

**Scenario.** A friend has a reader token (/get/\d+ is in READER_ROUTES). The owner releases a referenced photo (held 0, sensitivity cleared). get() then adds store.places_in_brief: every live place's root and path, up to 5, and the count of copies, including copies in other folders of the share. api/places.py says the places routes are 'the administrator's alone, as a path can be as personal as the file'. Through get, a named token sees folder names it was never given, such as a private album folder that holds another copy of the same bytes.

**Evidence.** I read api/documents.py get() (doc["places"] = store.places_in_brief(...) when not held, with no viewer check), store/documents/places.py places_in_brief (all live places of the hash, root and path), wall/auth.py READER_ROUTES (GET /get/\d+), and the docstring of api/places.py.

**The skeptic.** Confirmed by reading the code. GET /get/\d+ is in READER_ROUTES (wall/auth.py:76). In api/documents.py:56-59, get() adds store.places_in_brief for any document with held 0 and does not check the viewer. places_in_brief (store/documents/places.py:169) returns every live place of the hash, up to 5 roots and paths, plus the count of copies. brief_document keeps every key of the row except BRIEF_ROW_DROP, so 'places' also reaches the MCP or agent shape. The docstring of api/places.py says paths are the administrator's alone. A named token can therefore see the folder paths of other copies of a released referenced file. Fix: add places only when store.VIEWER is the administrator, or at most give the count and 'reachable' to a named token.

**Fix proposed.** Add places to get() only for the administrator (store.VIEWER.get() is None). For a named token, give at most copies/reachable and no paths.

**Done.** `/get` gives a named token the count of copies and whether one is reachable, never the paths.

## 28. Person page timeline has no cap

*low*, contracts; `src/prax/store/graph/people.py:181`

**Scenario.** The relations list is capped at PERSON_FACTS_PER_RELATION, and documents, photos and events have caps of their own. The dated facts, though, go into 'timeline' whole, each a full row (edge, relation, other end, dates, confidence, producer, doc). Take a person with many world-dated facts: an author whose facts are dated by worlddates or jsonld, or someone with an attended fact for each of hundreds of events once the chain rule runs. GET /people/{id} then grows without bound. This is the only part of the person page with no cap.

**Evidence.** I read person_page: dated = sorted(...) over every dated fact, returned as 'timeline' with no slice. Every other list there is sliced (PERSON_DOCUMENTS, PERSON_PHOTOS, PERSON_TOP, PERSON_FACTS_PER_RELATION).

**The skeptic.** Confirmed. In person_page (store/graph/people.py, near the 'timeline': dated return), dated is every world-dated fact of the person, sorted and returned whole. _own_facts has no LIMIT either. Every other list on the page is sliced. The person page is an administrator-only UI route, not an agent endpoint, so the size cost is a page's payload and not Claude's context, hence low. Fix: cap the timeline, for example at PERSON_TIMELINE, and return timeline_total.

**Fix proposed.** Cap timeline, for example at PERSON_TIMELINE = 50 in world order, and add timeline_total, as documents_total does. Or page it with an offset.

**Done.** `PERSON_TIMELINE` (100) caps it, `timeline_total` counts it, and the person page says how many more.

## 29. `prax token role NAME` without --role quietly makes the token a reader

*low*, contracts; `clients/cli/prax_cli/main.py:925`

**Scenario.** --role defaults to 'reader' for every action. Someone runs `prax token role mcp` to see or check a token's role. The CLI sends PUT /tokens/mcp {role: reader} and prints 'mcp is a reader now'. An MCP client that writes pages, made as a contributor, then gets a 403 on every write. With no name, it PUTs /tokens/None.

**Evidence.** I read the build_parser token subparser (role choices default='reader', name nargs='?') and tokens.token(): the 'role' action calls door.put_json(f"/tokens/{a.name}", {"role": a.role}) with no check that --role was given.

**The skeptic.** Partly confirmed. In main.py:925-930, --role has default='reader' for every action. In tokens.py:45-47, 'role' PUTs {role: a.role} with no check that --role was given, so `prax token role mcp` demotes a contributor to reader and prints that it did. The no-name case is handled: tokens.py:38-40 fails with 'which token?' before the PUT, so the '/tokens/None' part of the claim is wrong. The harm fails closed (403s on writes) and is easy to undo, hence low. Fix: default=None, require --role for the 'role' action, and treat None as reader for 'add'.

**Fix proposed.** Default --role to None. For 'role', require both a name and --role (p.error otherwise). For 'add', apply 'reader' when --role is None.

**Done.** `--role` has no default; `prax token role NAME` without it fails, and `add` makes a reader.

## 30. PLAN.md says a `depicts` edge is hidden from a named token without --personal, but the code shows it once a person has opened the picture

*low*, tests; `docs/PLAN.md:1226`

**Scenario.** A person releases a picture: set_sensitivity(doc, None), which is by human. The release workflow for referenced photos in groups is planned, and the Admin page's referenced list already supports it. Later a face in that picture is named. naming._suspect_picture leaves it open because meta.sensitivity.by == 'human'. Every named token without --personal then sees, through /traverse, /graph/document/{id} and /graph/connect, which person the picture depicts, and the `attended` fact derived from it. PLAN's AT 'The wall' paragraph promises the opposite. CLAUDE.md promises only 'suspected unless a person decided', which matches the code. A reader of the plan would believe names on faces never reach a guest token.

**Evidence.** Read src/prax/store/graph/naming.py:156-173, and tests/test_face_naming.py:155-157, which asserts the picture stays open. grep found no filter on rel='depicts' in the store's base, traversal, views, context or the wall. Scratch test: _face(con, 81), set_sensitivity(doc, None), name_faces([face], name='Ada Example'), then traverse as Viewer(personal=False). It returned the depicts edge: {'rel': 'depicts', 'dst': 'Ada Example', 'source_doc': 1, 'evidence': "face 1 named 'Ada Example' by a person"}.

**The skeptic.** Partly confirmed. I read naming.py:156-173: _suspect_picture leaves a picture open when meta.sensitivity.by == 'human'. test_face_naming.py:155-157 asserts exactly that, so the behaviour is deliberate, and CLAUDE.md's wall text ('suspected unless a person decided about it') matches it. The divergence is in docs/PLAN.md:1232-1234, which says unconditionally that a depicts edge to a person is hidden from a named token without --personal. No code filters rel='depicts' for a viewer. So on a picture a person has opened, the name is visible to every named token, which is the opposite of what the plan says. The code follows the contract, so this is a wrong promise in the plan rather than a leak against CLAUDE.md. Fix: either reword PLAN's wall paragraph to 'unless a person opened the picture', or, if the plan is the intent, hide depicts and its chained attended from viewers without personal.

**Fix proposed.** Decide which behaviour is meant. If faces' names must stay behind the wall, make naming re-suspect a picture even after an earlier 'open' decision, or hide depicts and attended edges from viewers without personal in the store's edge filter, and test it. Otherwise correct PLAN.md's wall paragraph to say what the code does: a picture a person opened stays open, and its named faces show.

**Done.** Decided by the user (2026-10-28): a picture with a named face is hidden from a named token without `--personal`, even one a person opened (`store.hidden_documents`, `document_hidden`), and its facts with it. PLAN.md and CLAUDE.md say so. Test: `test_a_named_face_is_for_a_token_with_personal_only`.

## 31. The test for /domains/counts checks only for a 200, so a domain menu that counted personal documents would pass

*low*, tests; `tests/test_wall.py:342`

**Scenario.** /domains/counts joined the named token's routes in this range. Its wall check is `status_code == 200`. If store.domain_counts stops excluding hidden_documents, a guest's domain menu counts the personal and suspected documents in each module. No test fails, because test_browse and test_domains call it only as the administrator.

**Evidence.** Read src/prax/store/documents/domains.py:217-247 and tests/test_wall.py:342. Scratch test: I monkeypatched prax.store.documents.domains.hidden_documents to return an empty set and ran test_the_web_page_with_a_named_token: it passed.

**The skeptic.** Confirmed. test_wall.py:342 checks only status 200 for /domains/counts. test_browse.py:89 and test_domains.py:476 call it as the administrator only. In a scratch test I monkeypatched prax.store.documents.domains.hidden_documents to return set() and ran test_the_web_page_with_a_named_token: it passed. The real code does filter (domains.py:225-232), so nothing leaks today, but the suite cannot catch a regression. Fix: assert that the guest's counts exclude the suspected bank statement, compared with the administrator's.

**Fix proposed.** Put the personal statement in a module (or count `unassigned`). Assert that a named token's /domains/counts is one lower than the administrator's for it.

**Done.** The wall test compares the named token's `unassigned` count with the administrator's (two fewer).

## 32. media-by-reference.md still says thumb and preview are the administrator's only, but they are reader routes now

*low*, tests; `docs/media-by-reference.md:1`

**Scenario.** The photos-step section says `GET /doc/N/thumb` and `/doc/N/preview` serve the copies 'guarded like the document, for the administrator's token'. Since commit 447654d, auth.READER_ROUTES holds `/doc/\d+/(original|text|chunks|thumb|preview)`. Every named token, readers included, gets the preview of any open picture, which a host owner reading this doc would not expect.

**Evidence.** git diff a5aac19 HEAD -- docs/media-by-reference.md (the '**The photos step**' paragraph) compared with src/prax/wall/auth.py:98.

**The skeptic.** Confirmed. docs/media-by-reference.md:311-313 says thumb and preview are served 'for the administrator's token'. src/prax/wall/auth.py:99 lists `/doc/\d+/(original|text|chunks|thumb|preview)` among the named tokens' routes, and git log -S shows it came in with 447654d. The copies are still guarded, so a hidden picture gives a 404, but any reader token gets the preview of every open picture. A host owner reading the doc would expect otherwise. Fix: correct the sentence.

**Fix proposed.** Say that both are served to any token that sees the document (reader routes), and that the guard is what keeps suspected and personal pictures out.

**Done.** Corrected.

## 33. architecture.md's 'where to touch what' rows leave out the new store parts and the places router

*low*, tests; `docs/architecture.md:330`

**Scenario.** The store.documents row lists its parts 'in order' without `photos`. The store.graph row stops at `paths` (no geo, events, people, naming). The store.retrieval row has no `faces`. The prax.api row lists its routers without `places`. Nothing about faces, photos, events or naming appears in the table. Someone looking for where face naming lives is sent elsewhere, and the order the table states no longer matches the packages' ORDER that invariant 3 enforces.

**Evidence.** grep -n 'photos|naming|people|events|faces' docs/architecture.md returned nothing about them. CLAUDE.md invariant 3 and src/prax/store/*/__init__.py ORDER list the parts.

**The skeptic.** Confirmed as a documentation gap, but it is not a safety or correctness defect. In docs/architecture.md, the store.documents row (line 330, 'in order') has no photos, the store.retrieval row (331) has no faces, and the store.graph row stops before geo, events, people and naming. A grep finds no photos/faces/naming entries. The enforced ORDER lives in the packages' __init__ and tests/test_invariants.py, not in this table, so nothing breaks. Fix: update the rows.

**Fix proposed.** Add the parts to the three store rows in ORDER order, and `places` to the api row. Add rows for prax.ml.faces, clusters and matching, prax.steps.photos and faces, and prax.parsers.photos.

**Done.** The rows name the new parts, the `places` router, `prax.ml.faces`, `clusters`, `matching` and `prax.parsers.photos`.
