# Packs

A pack is everything one field of knowledge brings to prax, in one
package. It holds:

- the ontology module that says what the field's things are;
- the readers that turn its files into text and chunks, and its chunk
  kinds;
- its worker steps;
- the tools `ask` and the surfer may call;
- its models and its dependencies.

Maths is the first (stage AD). Music and audio are the next
candidates. The user decided on 2026-09-30 that packs live in this
repository, under `src/prax/packs/`.

This document is the contract. Nothing in it is built yet.

## Why

Today a field's parts are spread over lists the core edits by hand:

| part | where it is named now |
|---|---|
| its things and relations | `ontology/<module>.yaml`, found by the loader |
| a reader of a file type | `prax.parsers.REGISTRY` |
| a reading asked for later | `store.READINGS`, `steps.READING_STEPS` |
| a kind of chunk | `prax.text.chunking.KINDS`, `store.ASIDE_KINDS` |
| a worker step | `steps.STEPS`, `steps.WATCHED_STEPS`, `steps._HOMES` |
| a tool of the surfer | `prax.answering.surf.DO` and its grammar line |
| dependencies | an extra in `pyproject.toml` |
| models | `models:` and `steps:` in `prax.yaml` |

Maths needs most of these rows: a module, a reading, the `formula`
kind's data, a step, a tool and SymPy. It would touch eight places, and
nothing would say they belong together. A pack is the place that
says so, and the lists become registries that read it.

Kitchen, studio, workshop and craft stay plain ontology modules: they
have no readers, steps or tools of their own.

## The layout

```
src/prax/packs/
  __init__.py          # PACKS: the manifests, and nothing else
  maths/
    __init__.py        # MANIFEST: names only, imports nothing
    maths.yaml         # the ontology module
    parse.py           # readers and the step's door and worker halves
    tools.py           # the surfer's tool and its sandbox
```

The package's `__init__.py` holds `MANIFEST` and imports nothing. The
thin client reads step names as cheaply as the door does (the rule of
`prax.steps`), so a pack's names must be readable without its
dependencies. The code a manifest names is imported when a step, reader
or tool is first used, as `steps._HOMES` does for the core steps.

## The manifest

```python
MANIFEST = Pack(
    name="maths",
    ontology=("maths.yaml",),
    extractors=("prax.packs.maths.parse:EXTRACTORS",),
    readings={"sympy": "formulas"},  # reading -> the step whose model it runs
    kinds=(),  # new chunk kinds; maths adds data to `formula`
    aside=(),  # kinds kept out of vectors and search
    steps={"sympy": "prax.packs.maths.parse"},
    watched=(),  # steps a worker runs unasked
    tools={"math": "prax.packs.maths.tools:do_math"},
    extra="maths",  # the pyproject extra it needs
    settings="maths",  # its section in prax.yaml
)
```

`Pack` is a frozen dataclass in `prax.packs`. Every field but `name` is
optional. The strings are import paths, resolved on first use.

## What a host turns on

`prax.yaml` names the packs this host runs:

```yaml
packs: [maths]
maths:
  timeout_s: 5
```

`packs:` is a new section in `config.SECTIONS`. A pack named there adds
its own section name (`settings`) to the known ones, so a setting is
read as any other (`config.setting("maths.timeout_s", ...)`). A matching
`PRAX_*` variable overrides it for one run.

A pack's runtime parts follow `packs:`: its readers, readings, steps
and tools exist on a host that names it and on no other. The door on the
board names none and serves the chunks the desktop's worker wrote.

**The ontology does not follow `packs:`.** Every host must compose the
same ontology. The version string is stamped on every edge
(invariant 9). A door and a worker that composed different ones would
stamp different versions for one document. So every pack's
ontology module in the repository is always loaded. Adding a pack's
module is a version bump like any module's. The documents with no domain
set are read against every module, so they are extracted again.
Documents assigned to other modules are not. That was the cost of
`computing` and `society` too.

A pack named in `packs:` whose extra is not installed is a configuration
error at start. The message names the pack, its extra and the command
that installs it.

## What a pack may not do

The invariants hold for a pack as for the core. Concretely:

- **No database.** A pack opens no SQLite file and brings no migration.
  Its step's door half calls `store` functions as a core step's does. A
  pack that needs to keep something keeps it in a chunk's `data` or in
  `documents.meta`. A new table is a migration of the core, argued in
  `docs/rationale.md`.
- **No new table for chunks** (rationale R13). A pack adds a kind and a
  locator shape, or data on an existing kind.
- **No code a model wrote.** A tool runs its own code in a subprocess
  with a timeout (`symbolic-maths.md`, "The pipeline", step 3). A model
  supplies arguments, never a program.
- **Nothing in the serving path past 1 GB** (invariant 7). A pack's
  models run in the worker. A pack that needs a model in the door, such
  as a text encoder for a second vector space, needs that decision
  first (below).
- **Its ontology module follows invariant 9:** unique names across
  modules, `naming:` on its types, a version bump for any growth.

`tests/test_invariants.py` gains two checks: no module under
`prax/packs/` opens a database (`sqlite3.connect`, `store.connect`), and
every manifest imports nothing.

## How the core finds them

Each hand-edited list becomes the core's entries plus those of the packs
this host runs. The list stays where it is, so a caller does not move:

| registry | reads |
|---|---|
| `ontology.load_dir` | `ontology/` and every pack's `ontology` files |
| `parsers.REGISTRY` | the core's extractors, then the packs' |
| `store.READINGS`, `steps.READING_STEPS` | the packs' `readings` |
| `chunking.KINDS`, `store.ASIDE_KINDS` | the packs' `kinds`, `aside` |
| `steps.STEPS`, `WATCHED_STEPS`, `_HOMES` | the packs' `steps`, `watched` |
| `surf.DO` and the grammar's action line | the packs' `tools` |
| `config.SECTIONS` | `packs`, and each pack's `settings` |

A pack's step name, reading name, kind or tool name may not repeat a
core one or another pack's. The registry refuses the duplicate at start.

## The packs in view

**maths** (stage AD, `docs/symbolic-maths.md`). An ontology module for
theorems, definitions and variables, if the extraction measures a need
for one. The `formula` chunk's `data` gains `sympy`, `symbols` and
`parsed_by`. A `sympy` step reads the formulas by the rules first, then
by the local model. A `math` tool offers the operations the user
chooses. The `maths` extra holds SymPy and ANTLR 4.11.

**music** (`docs/research-code-music-sound.md`). The `music.yaml` module
sketched there. Readers for MIDI and MusicXML through music21, when the
library holds such files. A `score` kind located by part and bar. Tools
for key, chords and transposition.

**audio.** A `sound` reader (duration, Essentia's key, tempo and
loudness, tags) and a `sound` kind located by `time` and `time_end`, as
a video's chunks are. Its CLAP vectors are a second vector space: another
model, another dimension, and a text encoder in the door. That changes
invariant 1 and the fusion of search for every field, so it is decided
on its own before an audio pack asks for it.

## The order of the work

1. **The registries** (about a day): `prax.packs` with `Pack` and
   `PACKS`, the lists above reading it, `packs:` in the config, and the
   two invariant checks. No pack yet. The tests prove the core behaves
   the same with none.
2. **The maths pack**, as stage AD plans it, once the user has chosen
   its operations.
3. **music**, after the library holds symbolic scores or the user sends
   them. **audio**, after the vector-space decision.
