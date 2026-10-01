# Packs

A pack is one domain of knowledge, in one package: what the domain's
things are, and what prax needs to read, keep and use them. Every domain
is a pack (the user, 2026-10-01). Some bring only knowledge: society,
kitchen, studio. Others bring code as well: maths, music.

Packs live in this repository, under `src/prax/packs/` (the user,
2026-09-30). This document is the contract. Nothing in it is built yet.

## Two halves

A pack has a knowledge half and a capability half, and two owners turn
them on:

| half | what it holds | turned on by |
|---|---|---|
| knowledge | the ontology module(s), its cases of "the same thing", its typing cues, the domain rules it suggests, its eval questions | the library |
| capability | readers, chunk kinds, worker steps, tools for `ask` and the surfer, models, the `pyproject` extra | the host, `packs:` in `prax.yaml` |

The knowledge is the library's because every host must compose the same
ontology. The version string is stamped on every edge (invariant 9). A
door and a worker that composed different ones would stamp different
versions for one document. The capability is the host's because the
board and the desktop run different things.

## Why

A domain's parts are spread today over files and lists the core edits
by hand:

| part | where it is now |
|---|---|
| its things and relations | `ontology/<module>.yaml` |
| what "the same thing" means for its types | the `modules:` section of `ontology/sameness.yaml` |
| the words that type its entities | `by_type` in `ontology/lexicon.yaml` |
| the rules that send documents to it | `domains:` in `prax.yaml` |
| a reader of a file type | `prax.parsers.REGISTRY` |
| a reading asked for later | `store.READINGS`, `steps.READING_STEPS` |
| a kind of chunk | `prax.text.chunking.KINDS`, `store.ASIDE_KINDS` |
| a worker step | `steps.STEPS`, `steps.WATCHED_STEPS`, `steps._HOMES` |
| a tool of the surfer | `prax.answering.surf.DO` and its grammar line |
| dependencies | an extra in `pyproject.toml` |
| models | `models:` and `steps:` in `prax.yaml` |

Maths needs most of these rows: a module, a reading, the `formula`
kind's data, a step, a tool and SymPy. It would touch eight places, and
nothing would say they belong together. A pack is the place that says
so, and the lists become registries that read it.

## What stays in the core

Only what every domain stands on:

- the machinery: the ontology loader, the rule engine
  (`store.assign_domains`), the sameness and lexicon mechanisms, the
  registries;
- `core.yaml`: person, organization, document, place, event, work,
  concept, tool, and the relations every document shares;
- `genres.yaml` and `subjects.yaml`: what any document is and what it is
  about. A rule reads them to send a document to a pack, so they belong
  to none;
- the general cues of `lexicon.yaml`: what an organization is, what is
  not a name;
- the rules this library applies, in `prax.yaml`. A pack suggests rules,
  and a person takes them after a dry run (`POST /domains/dry-run`).

## The layout

```
src/prax/packs/
  __init__.py          # PACKS: the manifests, and nothing else
  society/             # knowledge only
    __init__.py        # MANIFEST
    society.yaml       # the ontology module
    sameness.yaml      # its cases of "the same thing"
    lexicon.yaml       # its typing cues
    rules.yaml         # the domain rules it suggests
  maths/               # knowledge and capability
    __init__.py
    maths.yaml
    parse.py           # readers and the step's door and worker halves
    tools.py           # the surfer's tool and its sandbox
```

A pack's `__init__.py` holds `MANIFEST` and imports nothing. The thin
client reads step names as cheaply as the door does (the rule of
`prax.steps`), so a pack's names must be readable without its
dependencies. The code a manifest names is imported when a step, reader
or tool is first used, as `steps._HOMES` does for the core steps.

## The manifest

```python
MANIFEST = Pack(
    name="maths",
    # knowledge: always composed
    ontology=("maths.yaml",),
    sameness="sameness.yaml",
    lexicon="lexicon.yaml",
    rules="rules.yaml",
    # capability: on a host that names the pack
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
optional. The strings are import paths or files beside the manifest,
resolved on first use.

## What a host turns on

`prax.yaml` names the packs whose capability this host runs:

```yaml
packs: [maths]
maths:
  timeout_s: 5
```

`packs:` is a new section in `config.SECTIONS`. A pack named there adds
its own section name (`settings`) to the known ones, so a setting is
read as any other (`config.setting("maths.timeout_s", ...)`). A matching
`PRAX_*` variable overrides it for one run.

A pack's readers, readings, steps and tools exist on a host that names
it and on no other. The door on the board names none and serves the
chunks the desktop's worker wrote. A pack named in `packs:` whose extra
is not installed is a configuration error at start. The message names
the pack, its extra and the command that installs it.

## What the library turns on

Every pack's knowledge in the repository is composed, on every host.
Adding a pack's module is a version bump like any module's. The
documents with no domain set are read against every module, so they are
extracted again. Documents assigned to other modules are not. That was
the cost of `computing` and `society` too.

A library that leaves a domain out is not possible yet. It would need a
library setting kept in the database, not in `prax.yaml`, because both
hosts must agree. Leaving a pack out of a library that used it would be
a data migration, since its typed entities need a fallback type. It is
built when a library needs it.

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
- **Its rules are suggestions.** Nothing applies a pack's `rules.yaml`
  on its own.

`tests/test_invariants.py` gains two checks: no module under
`prax/packs/` opens a database (`sqlite3.connect`, `store.connect`), and
every manifest imports nothing.

## How the core finds them

Each hand-edited list becomes the core's entries plus the packs'. The
knowledge lists read every pack, the capability lists the packs this
host runs. The list stays where it is, so a caller does not move:

| registry | reads |
|---|---|
| `ontology.load_dir` | `ontology/core.yaml` and every pack's `ontology` |
| `ontology.sameness()` | the core's cases and every pack's `sameness` |
| `ontology.lexicon()` | the core's cues and every pack's `lexicon` |
| `parsers.REGISTRY` | the core's extractors, then the packs' |
| `store.READINGS`, `steps.READING_STEPS` | the packs' `readings` |
| `chunking.KINDS`, `store.ASIDE_KINDS` | the packs' `kinds`, `aside` |
| `steps.STEPS`, `WATCHED_STEPS`, `_HOMES` | the packs' `steps`, `watched` |
| `surf.DO` and the grammar's action line | the packs' `tools` |
| `config.SECTIONS` | `packs`, and each pack's `settings` |

A pack's step name, reading name, kind or tool name may not repeat a
core one or another pack's. The registry refuses the duplicate at start.

## The existing modules

`research`, `studio`, `electronics`, `computing`, `craft`, `kitchen`,
`workshop` and `society` move into packs of their own. Each takes its
module file, its section of `sameness.yaml` and its cues of
`lexicon.yaml`. The rules of `prax.yaml` that name it become its
`rules.yaml`. Modules built on each
other may share a pack: `craft` with `kitchen` and `workshop`, `studio`
with `electronics`.

The version string is built from module names and versions
(`core3+computing2+…`). A move changes neither, so no edge changes and
nothing is extracted again. The proof is the version string before and
after, and the full test suite.

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

1. **The registries and the existing modules** (about two days).
   `prax.packs` gets `Pack` and `PACKS`, and the lists above read it.
   `packs:` joins the config, with the two invariant checks. The eight
   modules move into packs with their sameness cases, cues and rules.
   The tests and the unchanged version string prove nothing moved.
2. **The maths pack**, as stage AD plans it, once the user has chosen
   its operations.
3. **music**, after the library holds symbolic scores or the user sends
   them. **audio**, after the vector-space decision.
