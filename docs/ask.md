# The ask agent: what it can do

`ask` answers a question from the library, with citations. On a host
that has a model, it is a loop: the model works the library for a few
steps, then writes from what it kept (`prax.surf`, `prax.ask`). This
page is the reference for what that model may do, what it may not,
what each move costs, and how to steer it. Setup is in
`docs/howto.md` section 3i. The reasons for the design are in
`docs/architecture.md` section 4.

## Two shapes

**One shot** (`steps: 0`, or no model on this host). The service (the
door, in the code's own words: the one process that reads and writes
the store) searches the question and takes the best passage from each of the top
documents, plus what the graph records about those documents. That
bundle is the whole context. Either a model answers from it, or the
bundle comes back unanswered for the caller's own model. Claude Code
gets the bundle over MCP by default. The Pi-class serving host, which
loads no model, always returns the bundle.

**Surfing** (`steps: 8` by default). Step 0 is the same search. From
there the model chooses each step, sees what it brought, and stops
when the passages it kept answer the question. The answer is then
written with the one-shot prompt over those passages. A citation
therefore always points at something the model read.

## The moves

Each step is two lines: a note and one action. The note says what the
last result told the model, or what it is after. A local model is held
to that shape by a grammar built for every step. The grammar also
limits it to passage numbers and document ids it has seen, so it cannot
cite, read or walk to something that does not exist. Claude follows the
same two lines without a grammar.

| Action | What it does | What comes back |
|---|---|---|
| `search: <words>` | the library's hybrid search again, in the model's own words | up to `passages` hits, one per document, 500 characters each, with what the graph records about each document. Documents already dropped and chunks already shown are skipped |
| `read: [n]` | read on where passage n stopped | the next ~1,500 characters of that document |
| `read: [n] <words>` | the part of that passage's document that holds those words | the chunk holding most of the words (a longer word counts more), and what follows it |
| `read: [n] (2)` | equation (2) of that passage's document, by the paper's own number | that formula chunk and what follows it. A number the paper does not have returns the numbers it does have |
| `read: doc <id>` | a document a result named, from its start | the same, from the top |
| `read: doc <id> <words>` | the part of that document about those words | the way into a long paper that a walk pointed at, when its start is a title page |
| `facts: [n]` | what the graph records about that passage's document | its relations and the entities they name; `cites` is left out |
| `walk: <entity>` | one hop around an entity | up to 25 edges by relation, each with its source document. The documents are named, so a later step can read one. An unknown name returns the names like it |
| `similar: [n]` | the documents nearest that passage's document in vector space | six, named, so they can be read |
| `drop: [n] [m]` | set passages aside | they leave the working set and the answer. A later search will not bring their documents back |
| `answer` | stop looking | the answer is written from the passages kept |

Every move is a read the service already performs for a person:
`store.search`, `read_chunks`, `find_chunk`, `document_facts`,
`traverse`, `similar_documents`. A figure a vision model has read is
text like any other. It comes back from a search or a reading with its
description. A figure nobody has read stays out of the results.

A formula passage is a display equation with its LaTeX and its
reading. It comes with one line that names the equations around it in
the paper, by number: two before, three after, with the first words of
each reading (`store.equations_near`). The model can then see that
"the kernel is the next equation" and read it, instead of reading on
blind. The sources column shows the same line, with each number a
link.

## What it cannot do

- **Write anything.** No edge, no page, no tag, no reading request. The
  loop only reads. The person who asked keeps an answer afterwards,
  with "keep on page…".
- **Spend money.** The model is the one configured for the `ask` step,
  which is the host's choice. A local model costs time only. Claude
  costs what the run costs and is never chosen for you.
- **Leave the library.** No web, no fetching, no other store. What is
  not in prax cannot be found by asking prax.
- **Look at a picture.** It reads a figure's description when a vision
  model has written one (`docs/howto.md` 3b, the `unread-figures`
  ailment). It never sees pixels.
- **Invent a source.** A step can only name passages and documents it
  has seen. If the answer still contains a `[9]` that matches no
  passage, the citation resolves to nothing: it is left out of the
  citation list and the UI does not link it.
- **Run forever.** Two budgets bound it, below.

## The budgets

| | Where it comes from | Default | Ceiling |
|---|---|---|---|
| `steps` | the composer's "steps", `prax ask --steps`, the request's `steps`, `steps.ask.steps` in `prax.yaml` | 8 | 20 (0 = one shot) |
| `tokens` of reading | the composer's "reading", `--tokens`, `steps.ask.tokens` | 4,000 local, 16,000 Claude | the model's context less ~2,200 tokens of prompt (`n_ctx` in `prax.yaml`); 60,000 for Claude, which is a cost ceiling, not a context one |

A result is charged against the reading budget when it is shown. A
result that would overflow the budget is cut to what is left. A search
costs about a thousand tokens, a reading about four hundred, a walk or
a facts call almost nothing. A model that searches three times has
spent most of a local budget before it has read much. `GET /ask/config`
reports the default and the ceiling for every model the host knows.

When the budget or the steps run out, the answer is written from what
was kept. A small budget costs depth and nothing else.

## What it costs

Measured on the 4090 with Qwen3.6-35B-A3B (2 slots of 16 K):

- a step: 2–4 seconds. The prompt grows by appending, so the server's
  prefix cache reads only the new tail;
- a whole surf: 15–60 seconds. Most questions take six to eight steps;
- the answer call: 12 seconds for 14,000 tokens warm, two to three
  times that cold. It reads everything kept once.

A one-shot ask on the same host takes about four seconds. Claude
answers in about the same time and costs about a cent a question.

What the steps buy was measured on questions whose answer is an
equation (`docs/eval/ask-equations-2026-09-17.md`: 22 questions over
papers read with marker). Both shapes find the paper and cite its
formula chunk. The one-shot answer used to stop at "the passages do not
state the equation" with the equation among them. A judge found the
equation stated in 11 of 22 one-shot answers and 16 of 22 surfed ones.
One line added to the answer prompt (write the equation out as the
passage has it, before explaining it) raised that to 16 and 19. On 52
questions asked three times each (2026-09-19) the scores were 32
one-shot and 45 surfed, ±2. The surf reads on to the equations that
the first search only brought the neighbour of, at 10 s a question
against 15.

## The trail

Every step is an event: the note, the action, what it brought, how
long it took. `POST /ask` with `stream: true` sends one JSON object per
line as each happens (`step`, `answering`, `answer`, or `error`). The
web UI paints these under the question; `prax ask --answer` prints them
line by line. The finished result carries the same trail, the number of
steps, what was dropped and how much reading was left. Keeping an
answer on a page keeps the trail with it, under "How it was found".

A client that disconnects stops the loop at its next step. Nothing is
written, and the model is not asked to answer for nobody.

A passage that is a figure carries the figure's reference (`figure`)
beside its words, which are what a vision model saw in it. The web UI
shows the picture in the sources column. A search hit that is a figure
shows the picture in the hit list. The reading is what found it; the
picture is what it is about.

## Standing questions

An answer can be kept as a standing question: "keep as a standing
question" in the web UI, `prax ask --answer --stand …`, or
`POST /questions` with the result. That creates a page of kind
`question` holding the answer, its sources and the trail, as "keep on
page" would write them. The page also remembers what the answer was
built from (`meta.question`): the documents it cited and their text
hashes, the top twenty documents of the search, and the highest
document id at the time.

The check that keeps the question current needs no model. It runs on
the service's clock (`schedule: questions: "06:30"` in `prax.yaml`) or
on `prax questions`. It runs the search again and looks up the
entities of the sources. The question is due when any of these holds:

- a document that arrived since ranks in the top twenty now;
- a document that arrived since shares two of the answer's entities;
- a source was read again and its text changed.

A due question is asked again with the same options and the host's
`ask` model, unless the page names one. A re-ask sends the model three
things. The earlier answer goes in as the previous turn of the
conversation (`history`). The question goes in unchanged, so the search
sees exactly what was asked. A note (`note`, rendered as `Note:` after
`Question:`) names the new documents. It also says what kind of change
is wanted: replace where the new evidence changes the answer, add
where it adds, say so where it disagrees, keep what still holds. The
model may cite
only the passages of this turn, so a stale citation cannot come back by
name. The note names the kind of change because that is what the work
on stale answers found to help; anchoring on the old text survives
prompting alone.

The new answer is a new revision by the agent. Its note names what
changed, for example "ask: server-35b; new: *A newer FDN reverb*". The
sections a person appended under the answer stay where they are. The
page's revisions are therefore what the library learned about the
question. A question page is never evidence for a search or an ask,
not for its own re-ask and not for anyone else's. The same holds for
the briefing.

### Ask blocks: a standing question inside your own page

A question can also stand inside a page of your own, between your
notes, as an *ask block*. An ask block is two HTML comments on lines of
their own:

```markdown
<!-- prax:ask id=q1 "how do feedback delay networks stay lossless" -->
<!-- /prax:ask id=q1 -->
```

The editor's "+ standing question" button writes them at the cursor.
The head may carry `steps=`, `doctype=`, `limit=` and `domain=`.

Saving the page starts the questions pass for it. The pass fills the
space between the markers with the answer and its source list. There
is no trail; the block is meant to be compact. It closes the block with
the interior's hash, the day and the model in the tail:
`<!-- /prax:ask id=q1 sha=1f3a… asked=2026-09-21 run=server-35b -->`.
It remembers per block, in `meta.asks[id]`, what a question page
remembers in `meta.question`. HTML comments render as nothing, so the
page reads the same in any Markdown viewer. The web UI frames the block
on a plate, with the question on its rim and "asked <day> by <model> ·
ask again".

From then on the block is checked and re-asked like a question page.
The earlier interior is the conversation so far; the change is the
note beside the question. The re-ask replaces the interior in one
agent revision whose note names the block and the change, for example
"ask server-35b; q1: new: *A newer FDN reverb*". Nothing outside the
markers is touched: `store.fill_blocks` replaces interiors by id and
raises when a page's markers are gone.

Two rules protect what you write inside a block:

- An interior that no longer matches the tail's hash was edited by
  hand. The pass leaves it, records why in `meta.asks[id].held`, and
  the UI shows "edited by hand; the door left it". Only "answer anew"
  in the UI, or `--release` on the command line, replaces it.
- A `<!-- prax:keep -->` … `<!-- /prax:keep -->` region inside a block
  is yours. It does not count as a hand edit, and it is carried over
  verbatim after each new answer. It is the place for a remark that
  should survive the next answer.

A block's interior is a chunk of kind `ask`. It is set aside from
search and never embedded, like a reference entry, so an answer is
never its own evidence. The links in a page's prose (`[title](#doc/N)`,
the answer's sources among them) are the page's `annotates` edges. An
edge is made when a link appears and retired when the link goes.
`GET /questions` and `prax questions` list blocks beside the question
pages as `slug#id`. `prax questions --ask slug#id` asks one block.

The briefing is the clock's other page, one per day. "What arrived"
lists the documents that came since the last briefing, each with the
first line of its summary, and the questions whose answer moved, blocks
included, named with their page. No model is asked for it. A question
about the day is a question like any other. What you put under a
briefing (a remark, an ask block) stays when the day's page is run
again: the agent replaces its own part and leaves the rest. A briefing
whose agent part you edited is left alone.

## When it goes wrong

- **The model repeats a move that brought nothing.** It is told which
  step already tried it, and the store is not asked again. A reading
  that finds nothing new falls back to whatever part of that document
  is still unread. A document read to its end says so and names its
  sections.
- **The server refuses the answer as too long.** Every passage is cut
  to the share the server states, and the model is asked once more
  with the numbering intact (`usage.cut`).
- **The model or the server fails mid-loop.** The failure becomes a
  step in the trail, and the answer is written from what was read.
- **Nothing matched at all.** The result comes back with no passages
  and no answer. That is the honest outcome; nothing is invented.

## Steering it

- **Ask for relations to make it walk.** "Which methods extend X, and
  what does each build on" pushes it into the graph. "What is X" does
  not.
- **`steps: 0`** gives a quick answer from the first search. **More
  steps** help when the answer is deeper in a paper than its first
  page.
- **Raise the reading budget** before raising the steps on a broad
  question. Three searches at eight hits each use up a local budget.
- **`doctype`** narrows the search side (`pdf`, `web`, `image`, `note`,
  `page`). **`passages`** is how many hits a search brings back.
- **Follow-ups** carry the conversation. The earlier turns reach the
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
