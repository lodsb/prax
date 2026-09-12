# Craft, kitchen and workshop: the making modules (2026-09-12)

Three modules for the part of a library that is not research and not
gear-as-bought: things made by hand. `craft` holds what making shares,
`kitchen` and `workshop` are the two domains that were asked for. They
were written together, before any document had been read against them —
the opposite of how `studio` was grown — and then grown the same day from
the first fourteen documents the library turned out to hold (two recipes,
twelve build documents; see "The first fourteen" below).

    core1+craft1+kitchen2+research5+studio3+workshop2

A document carries its domain set, so a recipe is read against
`core1+craft1+kitchen1` and a build against `core1+craft1+studio1+workshop1`.
Nothing in the library moved: the Zotero papers stay at `core1+research5`
and the gear at `core1+studio1`, and adding these modules re-selected only
the one document that has no domain set.

## What each holds

**`craft`** (requires core) is the vocabulary two makers of different
things share.

| type | what | examples |
|---|---|---|
| `technique` | a named way of doing something with hands, tools or heat | dovetail joint, reflow soldering, sous vide, French polishing, blind baking |
| `material` | what a thing is made of, as a kind and not as an amount | oak, 3 mm plywood, PLA, 60/40 solder, plain flour, brass sheet |

with three relations: a document `applies` a technique, a thing is
`made_of` a material, and a document or technique `needs` a tool. (The
plan put `needs` in kitchen; it moved down because a build needs a
bandsaw exactly as a recipe needs a stand mixer.)

**`kitchen`** (requires craft) is recipes. `recipe` is the self type — a
document in this domain *is* a recipe — with `dish` (a kind of work),
`ingredient` (a kind of material) and `cuisine` (a kind of concept). A
recipe `makes` a dish, `calls_for` an ingredient, is a `variant_of`
another recipe, and `belongs_to` a cuisine. Equipment is core's `tool`
through craft's `needs`.

**`workshop`** (requires craft and studio) is things built. `build` is
the self type — a project write-up, an instructable, a build log, a
repair report — with `design` (a plan that exists apart from the build:
a published schematic, a cutting list, a sewing pattern). A build is
`made_with` components, devices, tools and materials, `follows` a design
or a technique, and is `derived_from` an earlier build or design. It
reuses studio's `device` and `component` rather than inventing its own,
which is why it requires studio.

## The two lines that matter

**A technique is not a method.** Research's `method` is the scientific
kind — an algorithm, an experimental procedure, something a paper
proposes and evaluates. Craft's `technique` is the hands-on kind, learned
by doing and named by makers. Both descriptions say so, because both
modules can be loaded at once. Research aliases the word "technique" to
`method`; when craft is loaded the declared name wins and the alias is
dropped, which is the loader's rule for independent modules.

**A material is not a tool.** A material is consumed or becomes part of
the thing; a tool is picked up and put down again. Flour and solder are
materials, a stand mixer and a hot air station are tools.

## What was deliberately left out

- **Quantities, times, temperatures and the order of steps.** They stay
  in the text. The graph answers "what can I make with what I have,
  whose recipe is this a version of, what tradition is it from"; the text
  answers how much and how long.
- **A relation for modifying or repairing a device.** `derived_from` and
  `made_with` carry most of it; if the queue fills with mod and repair
  triples that do not fit, that is the evidence for workshop v2.
- **Nutrition, cost, difficulty, yield.** Nothing in the library asks
  for them yet.

## Using them

Give documents their domains, by rule or by hand:

    domains:
      - match: {path: kitchen/}      # the drop folder's subfolder, or a
        domains: [kitchen]           #   folder registered with --from
      - match: {path: workshop/}
        domains: [workshop]
      - match: {tag: recipe}         # a Zotero tag works too
        domains: [kitchen]

in `prax.yaml` (howto 3k), or drop files into `data/inbox/kitchen/` and
`data/inbox/workshop/` — the drop folder's subfolder is the domain — or
set them on a document's page in the UI, which never gets overwritten by
a rule. The browser extension's domain list grows on its own from
`GET /inbox`.

Then read twenty documents with the local model and look at what the
queue collected:

    prax work --scope all --steps extract -n 20
    # then the Review tab, or:
    curl -s 'http://127.0.0.1:8000/review?limit=50&unmapped=true' | less

The queue is the evidence for the next version of these modules, the way
the 19 gear documents were the evidence for studio v1.

## The first fourteen (2026-09-12)

The library is a research library; a sweep found two genuine recipes (a
vegetable frittata, a forum cookbook) and twelve genuine builds
(assembly instructions for a microphonic soundbox, a passive I/O module
and an XLR connector, a kit parts list, two hardware-hacking books, an
instrument-making workbook, a "how to make" paper, a TV script on
glueing, a CPU cooler's installation guide). They were given their
domains by hand and read with the local model.

| pass | edges | queued | what the queue said |
|---|---|---|---|
| v1 | 68 | 146 | two thirds of it was one thing: studio's `describes`, `covers`, `names` and `written_by` accepted only studio's four kinds of document, so a build could not describe the device it makes, cover a concept, name a part in passing or have an author |
| v2 (studio 2, workshop 2, kitchen 2) | 152 | 77 | those relations take any `document` now; `made_with` says it is the one for what a build contains; `variant_of` carries "a frittata is an omelette" |
| v3 (studio 3) | 148 | 82 | `names` reaches a person and an organization; `covers` says when to use `describes` and `applies` instead. The queue stopped moving: what is left is the model's free-form lines and the kind of judgement a person makes in the Review tab |

What the graph holds now: the frittata `makes` its dish, `calls_for`
eleven ingredients and is a `variant_of` an omelette; the soundbox is
`made_with` a contact microphone, wood, copper foil and a PCB, `needs` a
soldering iron, sandpaper, a wire cutter and an ESD wristband, and
`covers` woodworking.

Two things the pass surfaced that are not the modules' to fix:

- The local model copied its own prompt into the graph ("source name",
  "target name") — 1,357 edges' worth over the whole library. The line
  format now marks its placeholders as `<name>` and forbids emitting
  them, `extraction.apply` rejects such a triple outright, and the heal
  pass (howto 3m) mended what older passes had written.
- Every domain wants to say who wrote a document. Studio's `written_by`
  now takes any document, but a recipe read on its own (kitchen does not
  require studio) still has no relation for its author, and research has
  its own `authored_by`. "Who wrote this" belongs in core; that is a core
  v2, which re-selects the whole library, so it waits for the next full
  re-read.

To grow them further: drop recipes into `data/inbox/kitchen/` and build
logs into `data/inbox/workshop/`; the door takes them in, the worker
reads them, and the queue says what is missing.
