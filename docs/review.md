# Reviewing the graph

prax builds its graph from what it reads, and some of what it reads goes
wrong. Two names for one thing stay apart. Two different things get
folded into one. The Review page is where you fix that by hand. Open it
from the menu at the top of the web interface.

The page has five tabs. The first holds the facts the reader could not
place. Three ask you whether two names are the same thing. The last asks
whether a document is personal.

## Facts that did not fit

When prax reads a document, it writes down what the text says as small
facts: "this paper uses the wavelet transform". A fact that does not fit
prax's list of kinds of things lands here. You can give it the right
kinds and keep it, or drop it. Most of these clear themselves when the
list of kinds grows, so you rarely need this tab.

## Same thing?

These are pairs of names that look alike to prax: *Nintendo Wii Remote*
and *wii remote*, *twelve-tone equal temperament* and *12-tone equal
temperament*. For each side you see its kind, how many links it has in
the graph, and one document that mentions it.

You have three buttons.

- **Same, keep A** folds B into A. From then on both names lead to A,
  and everything the documents said about B now counts for A.
- **Same, keep B** does it the other way round. Keep the one with the
  clearer name, or the one with more links.
- **Different** leaves both alone. prax never asks about that pair again.

If you are not sure, skip the pair. Nothing happens to a pair you leave.

## One name, several things

Here one name belongs to things of different kinds. *SuperCollider*
appears as a tool in three papers and as a method in one. *Music
Perception* is a concept in two papers and a journal in a third.

- **One thing: a tool** (or whichever kind fits) keeps the name as that
  kind and folds the others into it. Choose this for *SuperCollider*: it
  is one piece of software, however a paper described it.
- **Several things** keeps them apart. Choose this for *Music
  Perception*: the journal and the field are two different things.

## Personal?

Documents that rules think are personal: a bank statement, an invoice
with your name on it, anything from a folder you named in `prax.yaml`
(`docs/howto.md`, "Who sees what"). Each row shows where the document came
from and the cues that made it suspect. Your name appears as "name".

- **Personal** keeps it from every token that may not see personal
  documents, for good.
- **Not personal** opens it again, and the rules never mark it again.

A suspected document is already hidden from those tokens, so nothing
leaks while it waits for you.

## Merges to check

These are merges prax already made that deserve a second look. Most came
from an automatic round that nobody checked by hand. The list holds two
kinds of risky merge.

- **One word apart.** The names differ in a single word: *preorder
  traversal* was folded into *postorder traversal*, and the *23rd ISMIR
  conference* into the *26th*. These are wrong more often, so they come
  first.
- **Narrower.** A general name was folded into a more specific one, or
  the other way round: *discrete Fourier transform* into *Fourier
  transform*. Many of these are fine (*Arduino microcontroller board* is
  an *Arduino board*), some are not.

**Right** keeps the merge and marks it as checked. **Wrong: split them**
gives the folded name its own place in the graph again.

## Rules of thumb

The rules are in `ontology/sameness.yaml`, and the "Same thing?" tab
shows them above the pairs under **What counts as the same thing**. The
models that decide pairs are asked with the same file, so your decisions
and theirs can be compared. In short:

- **the same:** spelling, singular and plural, an abbreviation and its
  full name, another language, a product and its versions, a conference
  and its editions, and synonyms within one field;
- **different:** a narrower idea and the general one, a phenomenon and
  the method for it, a method and its implementation, a part and the
  whole, a task and its tool, and names one word apart.

A domain adds its own cases: a dish and a variant of it are two recipes,
a device and its manual are two things.

To change a rule, edit the file. The local model's numbers move when the
wording does, so measure it again afterwards (`docs/howto.md`, "Entity
resolution").

When a merge would make a search for one name find documents about the
other, and you would be surprised, they are different.

## Undo

After each decision the row says what happened, with an **undo** button
beside it. Undo takes the merge back and forgets the decision, so the
pair is open again. Use it when you clicked the wrong button.

## What your decisions are for

Every decision you make is kept and marked as a person's, so prax
never asks you the same question twice. The weekly search for new look-alike
pairs skips the ones you answered.

Your decisions have a second use. prax is testing whether its local
model can tell how sure it is about a pair. If it can, the model settles
the clear cases by itself and only the doubtful ones cost money
(`docs/PLAN.md`, stage Q). To check that, prax compares the model with
answers a person gave. A few hundred decisions spread over the three
tabs are enough. Fifteen minutes now and then, one page at a time, gets
there.
