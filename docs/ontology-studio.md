# The studio module: gear and its paperwork (2026-09-12)

The first batch of uploads through the Inbox view brought 19 documents
that are not papers. They are synthesizer and microphone manuals, a
service manual, datasheets, schematics, a cheat sheet, a magazine test and a
forum guide. Read under the research module they produced five
documents with no edges at all and a queue that says what the model
wanted to say. Counted by the shape:

| the model wanted | examples | studio answer |
|---|---|---|
| the device as an entity, its maker | "Waldorf Iridium", "TORAIZ SQUID", "Sony Corporation", "Leadshine", "Pioneer DJ" as `funded_by`, `published_in`, `affiliated_with` | `device` (a kind of tool) and `manufacturer` (a kind of organization); the core `developed_by` already links them |
| parts and functions | `has_part` touchscreen display, oscillator section; `describes` freeze buffer, CV control, bit reduction; Patch Finder as a tool | `component` (a kind of tool), `feature` (a kind of concept); `has_part`, `has_feature` |
| what the device speaks | MIDI, CV/gate, SCSI, GM2, WAV, AIFF, RS-485 typed as concept or dataset | `standard` (a kind of concept); `conforms_to` |
| rated values | `has_specification` holding torque, step angle | `spec`; `has_spec` |
| what the document is | `is a` datasheet, "user manual", "service manual", "cheat sheet", a review "on test" | `manual`, `datasheet`, `schematic`, `article` as kinds of document, and the module's `self_types`: the document being extracted is one of them |
| the document's subject | `about` waldorf iridium, `describes` electronic schematic | `describes` (document to device or component), `covers` (document to concept or standard) |
| compatibility lists | SRX series, SR-JV80, Akai S1000 as mentions or datasets | `compatible_with` (device to device or component), `names` as the weakest relation |
| where it appeared, who wrote it | `published_in` audiovias.com, `authored_by` Ruben Tilgner | `publication` (a kind of organization), `appeared_in`, `written_by` |

## What changed in the loader

- A module may declare `self_types`: what the document being extracted
  may be. Research says `paper`; studio says `manual`, `datasheet`,
  `schematic`, `article`. The prompt names the one type when only one is possible, and asks the
model to pick when several are. The local extractor maps "this manual" and
"the document" to the title for any of them.
- Aliases do not shadow declared names across independent modules.
  Studio aliases `mentions` to `names` and `about` to `covers` for a document that is studio only. When research is loaded as well, the
research relations keep their names and the aliases are left out. An
  alias that shadows a name in its own module or a required one is still
  an error.
- The composed version is now `core1+research5+studio1`. Documents with
  a domain set are stamped with their subset's version
  (`core1+research5` for the Zotero library, `core1+studio1` for gear),
  so the new module re-selects only documents without a set.
- A producer re-reading a document under another subset or module
  version supersedes its own earlier reading: `apply()` ends those edges
  (`store.retire_reading`, history kept) before writing the new ones.
  The manuals' first reading as papers is gone from the live graph that
  way; other producers' edges stay.
- One typing rule (`self-as-device`): the model likes to write "the manual has this feature" and "the datasheet
has this spec". When the document `describes` exactly one device, the rule moves the edge onto
  the device.

## What was done with the library

The uploads got their domains: the papers `research` through a rule in
prax.yaml (`source: upload`), the 19 gear documents `studio` by hand,
the two microphone technical reports both. The gear documents were then
re-extracted against the studio subset:

| | |
|---|---|
| edges from the studio reading | 191 (covers 86, names 56, has_feature 18, describes 15, has_spec 7, has_part 4, conforms_to 2) |
| edges the self-as-device rule moved onto devices | 58 |
| edges of the earlier research reading retired | 102 by the model, 57 rule-derived |
| still in the queue from these documents | 116, mostly untyped covers, names and has_spec |
| devices in the graph | 35 (Sony C-38B, Waldorf Iridium, XV-5080, Rane ONE, Mutable Instruments Clouds, the Leadshine motors, the SC500 and SC1000 boards, the microphones a review compares) |

The five documents that had no edges under research now have between 4
and 20 each. Two things the queue still shows. The model also writes
`covers` and `names` without types for parts and standards it is unsure
of, and a datasheet's specs come as many small `has_spec` lines the
rule types only when the device is known.

## Not in the module

- Prices, dealers and dates: metadata, not graph.
- A `person` who owns or plays a device: the family module's business.
- Software as such: `tool` in core covers it, and a plugin or a DAW is a
  `device` only when a manual treats a specific product.
