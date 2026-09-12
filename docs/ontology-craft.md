# Craft, kitchen and workshop: the making modules (2026-09-12)

Three modules for the part of a library that is not research and not
gear-as-bought: things made by hand. `craft` holds what making shares,
`kitchen` and `workshop` are the two domains that were asked for. They
were written together, before any document had been read against them —
the opposite of how `studio` was grown — so the first twenty documents
read under each will decide what is missing, through the review queue.

    core1+craft1+kitchen1+research5+studio1+workshop1

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
