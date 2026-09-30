# Code, music and sound in the graph (research, AF)

The third research question of stage AF in `docs/PLAN.md`: how prax
would represent code, music and sound. For each there are three
choices:

- an ontology module on the existing tables;
- new chunk kinds and locators on the existing `chunks` table
  (rationale R13: a new medium adds a kind and a locator shape, never a
  table);
- something the tables cannot hold.
 Nothing here is built. Measured read-only on a copy of the store
on 2026-09-30.

## What the library holds

**Code.**
- 13,869 `code` chunks in 639 documents: fenced listings from PDFs,
  located by character range and page. Their `data` is empty, so no
  chunk says its language.
- 481 documents of genre `source`, 470 of them RTF, most of them
  SuperCollider from the NAS (the SC Book's figures, SynthDefs, help
  files). 830 RTF documents hold SuperCollider code (`SynthDef`, `.ar(`).
  They are chunked as `text`, never as `code`.
- Seven real source files (five `.m`, two `.h`, from Zotero), and 15
  GitHub documents, which are READMEs.
- The graph since `computing`: 253 programs, 364 symbols, 83 code
  libraries, 50 programming languages, 42 source files. Some symbols are
  model noise (`hpol = polar5(...)`).

**Music.**
- 3,654 documents with the subject `music` and 2,860 with `audio`,
  nearly all of them writing about music.
- 35 documents of genre `score`: 33 Arabic maqam songbooks, probably
  scans, a poem and a libretto. 42 of genre `lyrics`.
- No MIDI, MusicXML, MEI or `**kern` file anywhere, Zotero's storage
  included.
- No music module. Music's things are typed as research's concepts and
  methods: 454 entity names hold "midi", 221 "chord", 43 "maqam".

**Sound.**
- One audio file (`square2.aif`, from a Zotero page).
- 41 videos, 28.8 hours, with 1,931 frames. Their audio is not
  archived. 4,062 chunks of 42 documents carry `time` and `time_end`
  in their locator, so a media locator exists already.

**Why so little.** The NAS sender (`clients/send/prax_send.py`) sends
documents and images only. Source files, audio and MIDI on the NAS were
never sent, so how much of each the NAS holds is not measured. A dry run
of the sender that counts by extension would say.

## Code

**What exists.**
- tree-sitter (Brunsfeld 2018): a parser a language, incremental. A
  community grammar for SuperCollider exists
  (madskjeldgaard/tree-sitter-supercollider).
- SCIP (Sourcegraph 2022), after LSIF (Microsoft 2019): an exact index
  of definitions and references, one indexer a language.
- Aider's repository map: tree-sitter's definitions and references,
  ranked by PageRank. The cheapest shape an agent can use.
- RepoGraph (arXiv 2410.14684, ICLR 2025): a line-level graph of
  definitions and references that improves agents on SWE-bench.
  CodexGraph (arXiv 2408.03910): a repository in a graph database the
  model queries.

**In prax.**
- **Documents.** One document per source file, content-addressed by its
  bytes: a file vendored into two repositories is stored once. A
  repository is the README document the GitHub import writes, and each
  file is `part_of` it.
- **Chunks.** The kind stays `code`. The locator gains `path`,
  `line_start` and `line_end`. `data` holds `{lang, symbol, defines,
  calls, imports}` from tree-sitter. The 13,869 existing code chunks get
  `lang` (a guess, with its confidence). The SuperCollider RTFs are
  chunked again as code. Both are a `code` reading on the readings
  queue.
- **Graph.** computing v3 adds `repository` (a `work`), `defines`
  (source file to symbol) and `calls` (symbol to symbol). Only calls
  across files or libraries become edges, with producer `tree-sitter`
  and confidence EXTRACTED. A repository of middling size has 10⁴ to 10⁵
  calls, which would pass the entity threshold of CLAUDE.md; the calls
  inside a file stay in chunk `data`. Symbols from the parser also
  replace the model's noisy ones.
- **Gains.** A search by kind and language ("SynthDef with Pulse in
  sclang"). `traverse` from a library reaches what uses it. The surfer
  reads a function by its symbol instead of a character range.
  bge-small embeds code poorly, but BM25 on identifiers does most of the
  work, so a code embedder is optional.

## Music

**What exists.**
- music21 (Cuthbert and Ariza, ISMIR 2010): parsing, key, Roman
  numerals, chords.
- Encodings: MusicXML 4.0 (2021), MEI 5 (2023), Humdrum `**kern`, ABC.
- Score recognition from images: Audiveris (MusicXML out) and oemer.
  Neither is known to read maqam quarter-tone accidentals; that is to
  be tested.
- Graphs: the Music Ontology (Raimond et al., ISMIR 2007), which
  separates a work from its performance and recording; MusicBrainz;
  the ChoCo chord corpus (Scientific Data 2023).
- CLaMP 3 (arXiv 2502.10362, 2025): scores, MIDI, audio and text in one
  vector space.

**In prax.**
- **A module now.** `music.yaml` on core and research, after the Music
  Ontology's split of work and performance:

  ```yaml
  module: music
  version: 1
  requires: [core, research]
  self_types: [score, lyrics]
  entity_types:
    musical_work: {parent: work, naming: proper}   # a piece, a song, a muwashshah
    recording:    {parent: work, naming: proper}
    instrument:   {parent: tool, naming: common}
    mode:         {parent: concept, naming: proper}  # maqam Rast, Dorian, raga Yaman
    form:         {parent: concept, naming: common}  # sonata form, dawr, twelve-bar blues
    score:        {parent: document}
    lyrics:       {parent: document}
  relation_types:
    composed_by:    {domain: [musical_work], range: [person]}
    lyrics_by:      {domain: [musical_work], range: [person]}
    in_mode:        {domain: [musical_work], range: [mode]}
    has_form:       {domain: [musical_work], range: [form]}
    scored_for:     {domain: [musical_work, score], range: [instrument]}
    notates:        {domain: [score], range: [musical_work]}
    performance_of: {domain: [recording], range: [musical_work]}
  ```

  The 3,654 music documents would type what research leaves as
  concepts. The 35 songbooks give works, maqamat and composers even
  from their title pages. It costs a module and a re-read of those
  documents, hours of the local model.
- **Score chunks later**, once there are scores prax can read. A kind
  `score` over the score written as text (ABC or `**kern`, a line a bar),
  so `chunk.text == artifact[start:end]` holds. The locator is `{part,
  measure_start, measure_end, page}`, or `time` for MIDI. `data` holds
  key or mode, meter, tempo, Roman numerals and cadences from music21.
  The step is a `score` parser, with Audiveris as a reading for scanned
  pages.

## Sound

**What exists.**
- CLAP (arXiv 2206.04769) and LAION-CLAP (ICASSP 2023, arXiv
  2211.06687): text and audio in one 512-dimensional space. A distilled
  CLAP audio model of about 7 M parameters runs on a Raspberry Pi 5
  with ONNX.
- PANNs (arXiv 1912.10211) for AudioSet tags; BEATs (arXiv 2212.09058);
  MERT (arXiv 2306.00107) for music audio.
- Essentia (Bogdanov et al. 2013) for key, tempo and loudness.
  Captions: WavCaps (arXiv 2303.17395), Qwen2-Audio (arXiv 2407.10759).

**In prax.** This is the one of the three the tables cannot fully
hold, because it needs a second vector space.
- **Documents.** One a file, the original archived by its hash
  (invariant 2 unchanged). A `sound` parser writes the text artifact.
  It holds the file's name and path words, duration and channels,
  Essentia's key, tempo and loudness, tags and a caption. A longer file
  gets a line a segment.
- **Chunks.** A kind `sound`, located by `time` and `time_end` as a
  video's chunks are. `data` holds `{tags, bpm, key, lufs}`.
- **The new part.** A second index, `vectors-clap.usearch`, keyed by
  chunk id, 512 dimensions, beside the text index (invariant 1's
  pattern). `chunk_embeddings` already records the model a vector comes
  from. The fusion gains one list: the query through CLAP's text model,
  matched against the audio vectors. That text model (RoBERTa, about 125
  M parameters, 250 MB at f16) sits in the serving path and must be
  measured against invariant 7's 1 GB.
- **Graph.** Little: a `sample` (a `work`), `recorded_with` a device,
  `from_library` a sample pack, and `sounds_like` only as INFERRED edges
  from vector neighbours. It fits studio's next version.
- **Gains.** "A dark evolving pad", "a kick like the 909", "like this
  sample" become searches. So would the videos' audio, if it were kept.

## What is worth doing first

1. **Code** (about two stages). The material is in the library: 13,869
   code chunks without a language, 830 SuperCollider files chunked as
   text, the computing module and the GitHub import. No new table and no
   new vector space. The user decides whether prax takes repositories
   file by file (the sender's extensions, the size of a clone) or only
   the code inside documents, and whether calls become edges at all.
2. **The music module** (a module and hours of re-reading). It types
   what 3,654 documents are about. Score chunks wait: the library holds
   no symbolic score, and the 33 maqam songbooks need a test of
   Audiveris on three of them first.
3. **Sound** (the most work: a parser, a second index, an encoder in the
   serving path). The library holds one sample. It is worth it only if
   the NAS holds sample libraries. The user decides three things. Does
   the sender's count by extension run first? Do gigabytes of audio
   belong in the archive, or are they indexed where they lie? May a
   second encoder run on the Q6A?
