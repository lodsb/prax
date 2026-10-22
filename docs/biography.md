# Biography: a life in the library

Plan item AR. Written 2026-10-20 as a design study. Not built.

The user asked on 2026-10-20 to keep a family tree in prax, and with it
personal photos, friends, their own projects, artworks and releases, and
the events of a CV (school, university, associations). This note asks
whether prax's model can hold one person's life. It says what the model
already has, what it lacks, and in what order the gaps would be closed.
Nothing here names a real person; the examples are invented.

The pack is `biography`, the user's choice of 2026-10-20 over `life`
(too wide) and `family` (too narrow, the name the code reserved until
then). It holds what any person's life is made of, kinship included,
because a biography has parents and children too. What sets genealogy
apart is its method, not its relations, and the method lives in the
same pack (§1). Whether a document is personal is not the pack's to say
(§9).

A family tree is not one clean answer. Trees found online disagree,
dates are "about 1872", and two people of the same name are one person
or two. That is the case prax was built for: every fact carries its
source, and a disagreement is kept, not settled by the last writer.

## What prax already has

- **The types.** `core.yaml` has person, organization, place, event and
  work. A school is an organization, a concert an event, a release a
  work.
- **World dates** on every edge, with a precision of year, month or day
  (migration 36). "Studied at X from 2003 to 2008" is one edge with
  `world_from` 2003 and `world_to` 2008.
- **Evidence on every fact** (invariant 8): the source document, the
  quote, the producer and the run.
- **Disagreement kept.** Two facts of a functional relation that cannot
  both hold go to `edge_conflicts`, and `traverse` shows them as
  `disputed`.
- **The same thing, decided by a person.** Likely pairs go to the Review
  page, and each decision grows the judge's calibration set.
- **Several names for one entity.** `entity_labels` holds a birth name
  and a married name as alternatives of one person.
- **Paths.** `store.connect_entities` answers how two entities are
  connected, hop by hop, with each hop's evidence.
- **The wall.** A document marked `personal` is hidden from every named
  token that is not given `--personal`.
- **A fact written by hand.** The MCP `link` tool writes an edge with a
  source document and its evidence.

## What it lacks

### 1. A pack: `biography`

A pack of its own (`src/prax/packs/biography/`, `docs/packs.md`), with
one module on top of core. A first draft:

| kind | names |
|---|---|
| organization subtypes | `school`, `association`, `band` |
| work subtypes | `artwork`, `release`, `project` |
| event subtypes | `birth`, `death`, `marriage`, `exhibition`, `performance` |
| document subtypes (self types) | `certificate`, `letter`, `register_entry`, `family_tree`, `cv` |
| family relations | `child_of` (with `adopted_by`, `fostered_by` beside it), `spouse_of` (symmetric), `sibling_of` (symmetric) |
| other people | `friend_of` (symmetric), `knows` (symmetric, weak) |
| the CV | `studied_at`, `worked_at`, `member_of` |
| works | `created_by`, `released_by`, `shown_at` |
| being there | `attended` (person to event) |

`authored_by` stays core's relation for documents; `created_by` is for a
work that is not a document, such as a painting.

A domain rule sends a document to the pack: its genre (`certificate`,
`family_tree`, `cv`), its source (a GEDCOM import), or a person's
choice.

Genealogy is a method, and it is kept in the same pack. A historian's
biography of a composer and a family's tree use the same relations.
What genealogy adds is its sources (registers, certificates, census
entries, trees other people made), its work (people known from three
facts each, rebuilt across generations from records that disagree) and
its tools. In the pack those are the document types above, the sameness
cases (§4), the GEDCOM importer (§8) and kinship as a path (§10). They
split into a `genealogy` pack on top of `biography` only if they grow
large, and the split changes no fact's meaning.

A photo is not the pack's. Any library can hold pictures, so the
image reader (§7) is a capability of the core, and `depicts` (a picture
to a person, a place or an event) is a core relation, a bump of
`core.yaml`.

### 2. A birth date is a fact whose end is a date

prax has no literal values. An edge joins two entities, and "Literal
values … wait for a need" (PLAN, "Later / maybe"). A birth date is that
need. There are two ways:

- **An event entity.** "Birth of Anna Weber" is a `birth` entity. The
  person is `born_in` it, it is `located_in` a place, and the edge
  carries the date as its world date. This is GEDCOM's own shape, and it
  needs no change to the edge.
- **A literal end.** An edge whose `dst` is a date. That changes the
  edge's shape, every reader of edges, and the walk.

The recommendation is the event entity. It also gives a baptism, a
burial or a wedding a place to hang its photos and its witnesses.

### 3. Dates as uncertain as their sources

A genealogical date is often `ABT 1872`, `BEF 1900`, `BET 1870 AND
1875`, or in another calendar. Migration 36's precision says how fine a
date is (year, month, day), not how sure it is. The gap is a qualifier
beside each world date: `about`, `before`, `after`, `between` (with a
second date). That is a migration and a change to `store.link`'s
arguments; the date as written stays in the evidence.

### 4. The same person in several sources

One person appears in a GEDCOM file, a page captured from a genealogy
site, a certificate and a photo's tags. Resolution already finds likely
pairs. The pack adds its cases to its `sameness.yaml`:

- the same name, birth year and place: one person;
- the same name a generation apart (a son named after his father, a
  grandmother's name given to a granddaughter): two people. It is the
  most common mistake in family trees;
- a birth name and a married name with the same dates and parents: one
  person.

A pair that a person decides is a decision like any other on the Review
page.

### 5. How much a source counts

A church register and a stranger's online tree both say something, and
they are not worth the same. Today a fact's `confidence` is one of three
labels and says how it was read, not how good the source is. Two ways:

- **The source's genre decides.** `genres.yaml` gains `certificate`,
  `register_entry` and `family_tree`. A fact shows its source's genre,
  and an answer ranks a primary record first.
- **A number** (PLAN, "The graph": "Only then, `confidence` as a
  number").

The first needs no migration and comes first. A fact the owner states
(§6) counts as the owner's own account.

### 6. Facts a person states

Much of a CV has no document behind it. "I was in this band from 2005
to 2009" lives in memory. Invariant 8 needs a source for every fact, so
the source is a page the person writes: a "CV" page or a page "About my
grandmother". Pages are documents (invariant 1), so their facts are
extracted like any document's, or written by hand with `link`
(`source_doc` the page, the sentence as evidence). A later edit of the
page retires the facts whose sentence went, as a page's links do today.

### 7. Photos as documents

prax reads pictures only inside a document, as figures. A photo needs a
reader of its own (`prax.parsers.image`). It belongs to no pack: a
lab's micrographs and a museum's scans are pictures too.

- **The taken date** from EXIF, as `meta.taken`. It is when the picture
  was made, not when it was published, so it is not `meta.published`.
- **The place** from the GPS fields, as a `place` entity or the nearest
  one prax knows.
- **Tags, albums and the people in it** from XMP. digiKam, Lightroom and
  others write face regions with a person's name (the MWG regions
  schema). Those become `depicts` edges with no face recognition at all.
- **A caption** from the vision reading, which needs the card.

Face recognition is left out. It is expensive, it is the most sensitive
reading there is, and the tags a person already wrote cover most of it.

A photo library is large and usually lies on a NAS. It is the case for
which AQ references an original instead of copying it
(`docs/media-by-reference.md`). The user's plan of 2026-10-22: the
phone's library is copied onto the NAS, and prax keeps what it read and
a smaller copy:

- the original stays on the NAS, referenced by its hash and its place;
- the metadata above, in `meta`;
- a preview, about 1,600 pixels on the long side (some 300 KB as JPEG
  or WebP), and a thumbnail of about 320 (some 20 KB). Both are derived
  artifacts, filed by their own hash like a scanned page's crop
  (`figures.FILED`), never in SQLite (invariant 2);
- what the readings found: the caption as text, so a search finds it,
  and the perceptual hash.

20,000 photos then hold about 6 GB in the archive instead of the 60 to
100 GB of their originals. With the NAS off, the library still shows
every photo at screen size, and search and the graph work. The full
original is there whenever the door's host has the share mounted.

Two kinds of file need care. A phone's originals are HEIC, read with
`pillow-heif`, and a Live Photo is a HEIC with a short `.MOV` of the
same name beside it: one document, with the clip as a second place.
And the same picture sent through a messenger or resized is another
file with another hash. A perceptual hash (a 64-bit digest of what the
picture looks like) finds those near-duplicates. The crawl finds exact
copies by hash; the image reader finds near ones by the perceptual
hash, and a group of near ones is shown, never merged by itself.

Photos come after AQ stages 2 and 3.

### 8. Importers

Each reads its source and never writes to it (invariant 10).

- **GEDCOM**, 5.5.1 and 7.0. The file is a `family_tree` document and
  the source of every fact read from it. An individual becomes a person,
  a family's partners and children become relations, an event becomes
  an event entity with its date and place, a source record becomes a
  document or a reference. A citation's page and quality go into the
  fact's evidence. The vendors' `_` tags are kept in `meta` and read
  only where they are understood.
- **The photo library's metadata**, through the image reader (§7).
- **Captured web pages**, through the browser extension as today: an
  obituary, a newspaper article, a page of a genealogy site. A reader
  that turns a captured genealogy page into facts is a later step.
- **A list of works** (releases, artworks, exhibitions) as a page or a
  CSV file.

The owner's own works need no pack of their own yet (the user asked on
2026-10-23 where their music and releases belong). A release is a
`work` (core) `authored_by` its makers, `published_by` its label, with
its tracks `part_of` it. Its audio and video are referenced originals
like photos (`docs/media-by-reference.md`). Belonging is per document,
in `meta.domains`, and a document may be in several packs:

| file | domains |
|---|---|
| a clip of a child's birthday | biography |
| a release's master, its artwork | biography, studio |
| a recording of a concert the owner played | biography, studio |
| a session's project files, a mix | studio |

Biography holds the work as part of a life, and studio holds how it was
made. A module for releases (catalogue numbers, ISRCs, editions, a
discography) is added when a release needs a fact core cannot state.
It goes on top of studio, the way kitchen is on top of craft.

### 9. The wall, for everyone in it

A pack says what things mean; the wall says who may see a document
(stage U). The two stay apart. A historian's genealogy of a royal house
is biography and not personal; the owner's CV is personal and not
family. A pack that implied "personal" would get both wrong.

What is personal is found as today: the `private:` rules in `prax.yaml`
(the owner's names and private paths) and the defaults of
`prax.wall.private` set `suspected`, and a person decides on the Admin
page's "personal?" tab. A document about the owner's family names them,
so the owner's surnames in `private:` reach it in any pack. Friends and
relatives did not choose to be in the library, so what a personal
document says about them is hidden with it.

Living people need a rule of their own when AP (global ids and export)
is built: an export leaves out every fact about a person with no
recorded death, as GEDCOM exports do.

### 10. Kinship as a path

"How is this person related to me?" is a path over the family relations
only. `connect` already walks paths. It would take a set of relations to
walk, and the answer would name the kinship: three `child_of` hops up
and two down is a first cousin once removed. It is an answer computed
from the path, not a stored edge. `sibling_of` can be derived by the
rule pass from a shared parent (an INFERRED edge with its premises).

## What this study tests

Most of a life fits the model as it is. The gaps it finds are general,
and each would serve other libraries too:

- a date as the end of a fact (§2), answered with event entities;
- a date's uncertainty (§3), a qualifier beside the precision;
- the weight of a source (§5), its genre first;
- facts a person states, with a page as the source (§6);
- pictures as documents, with their metadata read before any model
  (§7);
- other people's privacy (§9);
- a path read as a named relation (§10).

## Stages

1. **The pack and GEDCOM.** The module, its genres, its sameness cases
   and its domain rule. A GEDCOM importer, tested on an invented tree
   checked into the tests. The date qualifier as a migration.
2. **The owner's own account.** Facts from pages, kinship as a path, a
   person's facts in date order on their entity page.
3. **Photos.** The image reader with EXIF and XMP, `depicts` in core,
   the pictures referenced on the NAS. This stage waits for AQ stage 3.
4. **Captions**, with the vision reading once the card runs.
5. **A timeline view** in the UI: one person, or the whole family, by
   world date.

## Questions for the user

The pack's name was the first question, answered the same day:
`biography`. Two remain:

1. **The photo library.** Which program keeps it, and does it write face
   tags to XMP? That decides how much of §7 needs no model.
2. **Where the trees are now.** An exported GEDCOM file or a site's
   pages decides which importer comes first.
