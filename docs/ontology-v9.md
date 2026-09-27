# Ontology v9: a document where a paper was named

2026-09-27. Research 8 to 9; composed version
`core3+craft1+kitchen2+research9+studio4+workshop2`. Migration 0026.

## Why now

The research module was written when the library was papers, and the
relations a document takes named `paper` on the document's end. Since
then the library has grown articles, manuals, recipes, builds and pages,
and the review queue held what that refused: an article citing a paper
(17), a manual citing one (3), a document using a tool (21), an article
using one (6), a build using one (2), a document proposing a method
(13), and a few each for `extends`, `defines`, `contrasts` and
`funded_by`. None of these is a misreading by the model; the ontology
said "paper" where it meant "document". The same held for `advised_by`,
which took authors only, and refused a person advising a person (19).

## The changes

**Research 9.** Only widened; no type or relation is new or renamed.

- `cites`: from any document to any document (was page, paper or project
  to a paper). Still by title, never by reference number.
- `defines`, `extends`, `contrasts`, `supports`, `uses`, `funded_by`: a
  document wherever a paper was named, on either end.
- `proposes`: from a document or a person (was paper, author or page).
- `annotates` and `synthesizes`: towards any document, so a page may
  annotate a recipe or draw on a manual.
- `advised_by`: between persons (was authors). An author is a person, so
  every v8 reading still fits.

A subtype passes wherever its parent is allowed, so `document` takes in
paper, page, project, article, manual, datasheet, schematic, recipe,
build and design at once.

**Migration 0026** moves the extraction stamps from `research8` to
`research9`, as 0018 did for v8: every reading made under v8 is valid
under v9, so nothing becomes due. It matches the version string alone.
A meta written by SQL `json_set` has no space after its colons: 1,033
of the 2,292 stamps. 0018's pattern asked for the space and would have
missed them; none of 0018's own strings are left.

## Measured on a snapshot (2026-09-27)

- The migration: 2,292 documents restamped, none left at `research8`.
- The replay (`review.replay`, which the nightly `maintain` runs): 895
  typed items checked, 85 linked (`uses` 25, `proposes` 21, `cites` 15,
  `advised_by` 15, `extends` 4, `funded_by` 2, one each of `defines`,
  `contrasts` and `mentions`), 4 already in the graph, 806 still open.

## Not taken

- `mentions` from an organization, author or person (42, 24, 20): the
  document is what mentions; these are the model putting the author in
  the document's place. The typing rules are the place for them.
- `about` from a work, an organization or a tool (24, 17, 12): the same
  confusion of a document with its subject.
- The kitchen's and studio's misfits (`calls_for` and `has_spec` from a
  plain document, `names` towards a material): a module's own relations,
  for that module's next version, with the electronics module.
