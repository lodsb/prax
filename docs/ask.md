# The ask agent: what it can do

`ask` is a question answered from the library, with citations. On a host
that has a model it is a loop: the model works the library for a few
steps and then writes from what it kept (`prax.surf`, `prax.ask`). This
page is the reference for what that model may do, what it may not, what
each move costs and how to steer it. How to set it up is `docs/howto.md`
3i; why it is shaped this way is `docs/architecture.md` 4.

## Two shapes

**One shot** (`steps: 0`, or no model on this host). The door searches
the question, takes the best passage of each of the top documents and
what the graph records about them, and that bundle is the whole context:
either a model answers from it, or it comes back as it is for the
caller's own model (what Claude Code gets over MCP by default, and what
the Pi-class serving host does, which loads no model at all).

**Surfing** (`steps: 8` out of the box). Step 0 is the same search. From
there the model chooses each step, sees what it brought, and stops when
the passages it kept answer the question. The answer is then written
with the one-shot prompt over those passages, so a citation always
points at something that was actually read.

## The moves

Each step is two lines: a note (what the last result told it, or what it
is after) and one action. A local model is held to that shape, and to
passage numbers and document ids it has seen, by a grammar built fresh
for every step — so it cannot cite, read or walk to something that does
not exist. Claude follows the same lines without a grammar.

| Action | What it does | What comes back |
|---|---|---|
| `search: <words>` | the library's hybrid search again, in the model's own words | up to `passages` hits, one per document, 500 characters each, each with what the graph records about its document; documents already set aside and chunks already shown are skipped |
| `read: [n]` | read on where passage n stopped | the next ~1,500 characters of that document |
| `read: [n] <words>` | the part of that passage's document which holds those words | the chunk that holds most of them (a longer word counts for more), and what follows it |
| `read: doc <id>` | a document a result named, from its start | the same, from the top |
| `read: doc <id> <words>` | the part of that document about those words | the only way into a long paper a walk pointed at, whose start is a title page |
| `facts: [n]` | what the graph records about that passage's document | its relations and the entities they name, `cites` left out |
| `walk: <entity>` | one hop around an entity | up to 25 edges by relation, each with the document it came from, and those documents named so a later step can read one; an unknown name gets the names like it |
| `similar: [n]` | the documents nearest that passage's in vector space | six, named, so they can be read |
| `drop: [n] [m]` | set passages aside | they leave the working set and the answer; a later search will not bring their documents back |
| `answer` | stop looking | the answer is written from the passages kept |

Every one of these is a read the door already performs for a person
(`store.search`, `read_chunks`, `find_chunk`, `document_facts`,
`traverse`, `similar_documents`). A figure a vision model has read is
text like any other and comes back from a search or a reading with its
description; a figure nobody has read stays out of the way.

## What it cannot do

- **Write anything.** No edge, no page, no tag, no reading request. The
  loop is reads only; an answer worth keeping is saved by the person who
  asked, afterwards, with "keep on page…".
- **Spend money.** The model is the `ask` step's, and that is the host's
  choice. A local model costs time only; Claude costs what the run costs
  and is never chosen for you.
- **Leave the library.** No web, no fetching, no other store. What is
  not in prax cannot be found by asking prax.
- **Look at a picture.** It reads a figure's description when a vision
  model has made one (`docs/howto.md` 3b, the `unread-figures` ailment);
  it never sees pixels.
- **Invent a source.** A step can only name passages and documents it
  has seen. If the answer's prose still writes a `[9]` that matches no
  passage, it resolves to nothing: it is left out of the citation list
  and the UI does not link it.
- **Run forever.** Two budgets bound it, below.

## The budgets

| | Where it comes from | Out of the box | Ceiling |
|---|---|---|---|
| `steps` | the composer's "steps", `prax ask --steps`, the request's `steps`, `steps.ask.steps` in `prax.yaml` | 8 | 20 (0 = one shot) |
| `tokens` of reading | the composer's "reading", `--tokens`, `steps.ask.tokens` | 4,000 local, 16,000 Claude | the model's context less ~2,200 of prompt (`n_ctx` in `prax.yaml`); 60,000 for Claude, a cost ceiling rather than a context one |

A result is charged against the reading budget when it is shown, and cut
to what is left rather than overflowing it. A search costs about a
thousand tokens, a reading about four hundred, a walk or a facts call
almost nothing — so a model that searches three times has spent most of
a local budget before it has read much. `GET /ask/config` reports the
default and the ceiling for every model the host knows.

When the budget or the steps run out, the answer is written from what
was kept. Nothing is lost by a small budget except depth.

## What it costs

Measured on the 4090 with Qwen3.6-35B-A3B (2 slots of 16 K):

- a step: 2–4 seconds, because the prompt grows by appending and the
  server's prefix cache reads only the new tail;
- a whole surf: 15–60 seconds, most questions six to eight steps;
- the answer call: it reads everything kept once — 12 seconds for
  14,000 tokens warm, two to three times that cold.

A one-shot ask on the same host is about four seconds. Claude answers in
about the same time and costs about a cent a question.

What the steps buy, measured on questions whose answer is an equation
(`docs/eval/ask-equations-2026-09-17.md`, 22 questions over papers read
with marker): the paper is found and its formula chunk cited either
way; the one-shot answer used to stop at "the passages do not state
the equation" with the equation among them — a judge found it stated
in 11 of 22 one-shot answers and 16 of 22 surfed ones. One line in the
answer prompt (write the equation out as the passage has it, before
explaining it) made that 16 and 19: the surf still reads on to the
equations the first search did not bring, at 10 s a question against
16.

## The trail

Every step is an event: the note, the action, what it brought, how long
it took. `POST /ask` with `stream: true` sends one JSON object per line
as it happens (`step`, `answering`, `answer`, or `error`), which is what
the web UI paints under the question and what `prax ask --answer` prints
line by line. The finished result carries the same trail, the number of
steps, what was set aside and what reading was left; keeping an answer
on a page keeps the trail with it under "How it was found".

A client that goes away stops the loop at its next step: nothing is
written, and the model is not asked to answer for nobody.

A passage that is a figure carries the figure's reference (`figure`)
beside its words, which are what a vision model saw in it; the web
UI shows the picture in the sources column, and a search hit that is
a figure shows it in the hit list — the reading is what found it,
the picture is what it is about.

## When it goes wrong

- **The model repeats a move that brought nothing.** It is told which
  step already tried it instead of the store being asked again; a
  reading that finds nothing new falls back to whatever of that document
  is still unread, and a document read to its end says so and names its
  sections.
- **The server refuses the answer as too long.** Every passage is cut to
  the share the server states and it is asked once more, numbering
  intact (`usage.cut`).
- **The model or the server fails mid-loop.** The failure becomes a step
  in the trail and the answer is written from what was read.
- **Nothing matched at all.** The result comes back with no passages and
  no answer, which is the honest outcome rather than an invented one.

## Steering it

- **Ask for relations to make it walk**: "which methods extend X, and
  what does each build on" pushes it into the graph, where "what is X"
  will not.
- **`steps: 0`** for a quick answer from the first search; **more steps**
  for a question whose answer is deeper in a paper than its first page.
- **Raise the reading budget** before raising the steps on a broad
  question: three searches at eight hits each is a local budget.
- **`doctype`** narrows the search side (`pdf`, `web`, `image`, `note`,
  `page`); **`passages`** is how many hits a search brings back.
- **Follow-ups** carry the conversation: the earlier turns reach the
  model, and a question that leans on them ("and the second one?") is
  searched together with the previous question.

## Where to change it

| To change | Touch |
|---|---|
| what a move returns, or add one | a `do_<action>` in `prax.surf` over a store read, the action in `SYSTEM` and `grammar`, a word for it in the UI's `STEP_WORDS` and the CLI's `_STEP_WORDS` |
| how the model is told to work | `surf.SYSTEM` |
| what the answer is written from | `ask.SYSTEM`, `ask.Bundle.as_message` |
| the budgets and their ceilings | `surf.STEPS`, `surf.reading_bounds`, `steps.ask` in `prax.yaml` |
| which model answers | `steps.ask.model`, `PRAX_ASK` for one run |
