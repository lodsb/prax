# Reviewing the graph

prax builds its graph from what it reads, and some of what it reads goes
wrong. Two names for one thing stay apart. Two different things get
folded into one. The Review page is where you fix that by hand. Open it
from the menu at the top of the web interface.

The page has four tabs. The first holds the facts the reader could not
place. The other three ask you one question each: are these the same
thing?

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

The same thing:

- spelling variants: *spatialisation* and *spatialization*,
  *Gauss-Seidel* and *Gauß-Seidel*
- singular and plural: *LDR* and *LDRs*
- an abbreviation and its full name: *NMF* and *non-negative matrix
  factorization*
- the same name in another language: *Notenschrift* and *notation*

Different things:

- a general idea and a variant of it: *Kalman smoother* and *extended
  Kalman smoother*
- two editions or years of one event: the 23rd and the 26th ISMIR
- two names that share all but one word: *ongoing costs* and *exit
  costs*
- a thing and the tool that does it, when both matter: *beat tracking*
  (the task) and a *beat tracker* (a program)

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
