# The electronics module, and studio 5

2026-09-27. Electronics 1 (new), studio 4 to 5; composed version
`core3+craft1+electronics1+kitchen2+research9+studio5+workshop2`.
Migration 0027.

## Why

niggles.txt asked for "an ontology for datasheets (electronics, chips
etc)". Studio has the paperwork of gear: a datasheet or a schematic
`describes` its device, and a device `has_part` components, `has_spec`
figures and `conforms_to` standards. It has no way to say what a circuit
is made of, or what a part is.

## The evidence

The library holds ten electronics documents: one datasheet (the
Leadshine iCLD57-21 stepper, whose PDF parsed to 1,408 characters), five
schematics (SC500, SC1000, the Pocket Sound Computer's `Schematic.pdf`,
Betula, a Quintec power supply) and four pages about PCB motors. What
their extractions sent to the review queue:

- **A schematic's parts** were refused 15 times: `schematic has_part
  component` 8 times (RP2040, AS5601, TLV62568DBVR, the Olimex
  A13-SOM, connectors by part number) and `schematic covers component` 7
  times. `describes` is for the device a document is about and `names` is
  for passing mentions, so a bill of materials had no relation.
- **What a part is:** `PIC18LF14K22 instance_of microcontroller`,
  `TLV62568DBVR has_spec voltage regulator`, `has_part microcontroller`.
  The kind of a part is something to search by ("which boards use an
  audio codec"), and nothing held it.
- **A part inside a part:** `component has_part component` (the SOM's
  processor), refused twice.
- **Pins:** the schematics print net labels (GND, VCC, GPIO0, SDA; 81 in
  `Schematic.pdf`), and none reached the queue as an entity.
- **Packages:** no text prints one.

## The changes

**Electronics 1**, which requires studio:

- `part_kind` (a concept, `naming: common`): microcontroller, voltage
  regulator, flash memory, audio codec. The vocabulary pass may translate
  it (`Spannungsregler` to `voltage regulator`).
- `package` (a concept, `naming: proper`): SOIC-8, QFN-56, TO-220.
  Declared ahead of its evidence, because every chip datasheet states
  one and the module is for the chip datasheets still to come. An
  ordering code's suffix is not a package.
- `shows_part`: a schematic or datasheet to a component or device. It is
  the bill of materials of the circuit drawn, by part number, never a
  reference designator. A build's parts stay workshop's `made_with`.
- `serves_as`: a component or device to its `part_kind`.
- `in_package`: a component to its `package`.

**Studio 5:** `has_part` from a component as well as a device.

**Not taken.** Pins are not entities: "VCC" names a different pin on
every chip, so as a name it would fold every chip's supply pin into one
thing. A pinout is a table, and a table chunk's `data` already holds it.
Electrical characteristics stay studio's `spec`, one figure per edge with
its unit and conditions in the name.

## What a document is read against

Studio holds electronics for browsing and a module filter
(`Ontology.within`), as craft holds kitchen. A document tagged `studio`
is still extracted against studio alone, so the four studio-tagged
electronics documents need `electronics` in their domains to be read
against it. Two schematics (Betula, the Quintec power supply) are tagged
`research`, from before 2026-09-21, when an upload fell into research.

**Migration 0027** moves the `studio4` stamps to `studio5`. A document
read against every module (no domain set; 61 on 2026-09-27) keeps its
stamp without `electronics1` and is due again: it was never read against
the new module, and any of them could be a datasheet.
