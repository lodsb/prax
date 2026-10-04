---
name: code-review
description: Review prax's code for quality rather than safety — hygiene (reuse, modularity, packaging, structure), extensibility against docs/PLAN.md, independence from the owner's library and generality for anyone's, conceptual generality, and performance on the Pi-class target. Use it when the user asks for a code review, a hygiene or quality pass, a review "of what was added", or asks whether code is general, reusable, extensible or fast enough; after a run of stages, before building more on them; and before a stage is called done when it added a module or a package. Two modes: quick (inline, over a diff or a package) and full (a workflow, one reviewer per dimension, every finding verified). Safety and correctness (the invariants, the wall, bugs) are a separate review; this skill points to it but does not replace it.
---

# code-review

A review of how the code is made, against what this project is: a
personal library today, written to serve anyone's library tomorrow, on a
small board, under a plan that keeps growing. The question for each
finding is "what will this cost later, and for whom", answered with a
file, a line and evidence.

This is not the safety review. Whether the code is correct, keeps the
invariants of CLAUDE.md and the wall, is reviewed separately (the
`review-three-days` workflow of 2026-10-04 is the shape). Run that one
first when both are due: quality findings on code that is about to
change for a bug are wasted.

## The five dimensions

Each has a checklist in `references/checklists.md`. Read the one for a
dimension before reviewing it; the questions are concrete and carry the
examples prax has already met.

1. **Hygiene**: reuse (the same thing written twice), modularity (size,
   one job a module, the parts' `ORDER`), packaging (a module in the
   package whose sentence fits it, CLAUDE.md "Conventions"), structure
   (what belongs in the lexicon, in `prax.yaml`, in a migration, in a
   pack).
2. **Extensibility against the plan**: read the open items of
   `docs/PLAN.md`; for each abstraction the change adds, does the next
   item the plan names build on it, or rewrite it?
3. **Independence from the owner's library**: thresholds tuned on it
   with no way to measure them again, its institutions, languages,
   domains and names in code, anything private reaching the repository.
   The test: does it work unchanged for a stranger's library (a French
   historian's, a lab's), and does it fail loudly where it cannot?
4. **Conceptual generality**: a special case where a general concept is
   waiting, and the opposite, a general machine built for one case.
5. **Performance on the target** (invariant 7): work per request that
   could be done once, scans and quadratic loops that grow with the
   library, SQL no index serves, memory peaks, start-up and first-call
   costs.

## Quick mode

For a diff, a commit range or one package, in this session.

1. Scope it: `git diff --stat <base> HEAD -- src` (or the package), and
   `python .claude/skills/code-review/scripts/survey.py <base>` for the
   facts a reviewer starts from: module sizes against the 2,000-line
   rule, long functions, new tuned constants, literals that look like
   the owner's data.
2. Go through the five dimensions in order, with their checklists. Read
   the code; do not judge from the survey alone.
3. For each finding, check it before reporting: open the place, follow
   the call, run a snippet when it is a performance claim. Drop what you
   could not confirm.
4. Report as below.

## Full mode

For a run of stages (days of commits), when the user asks for it or a
workflow is already agreed. Five reviewers in parallel, one per
dimension, each finding re-checked by a skeptic; about ten agents.

Run `references/workflow.js` with the Workflow tool:

    Workflow({scriptPath: ".claude/skills/code-review/references/workflow.js",
              args: {base: "<commit before the range>", scope: "src"}})

`args.dimensions` (a list of keys: hygiene, plan, independence,
generality, performance) runs a subset. The script is read-only for its
agents and returns `{confirmed, rejected}`. A workflow needs the user's
word (the Workflow tool's rule); asking for this review in full mode is
that word.

## Reporting

Ranked by what it costs to leave, most first. For each finding:

- **dimension**, **file:line**, one sentence of what is wrong;
- **evidence**: what in the code shows it, what was run;
- **why it matters here**: the plan item it blocks, the library it
  breaks, the board it slows, with a number where there is one;
- **fix**: the change, and its size (an hour, a stage, a migration).

Then a short list of what was looked at and found sound, so the next
review knows what was covered. Say what was not covered.

Do not report: style the linters own (ruff, mypy), wording, missing
docstrings, or a refactor whose only argument is taste. A finding needs
a cost someone will pay. Fewer, solid findings beat a long list.

## After the review

The findings go to the user before anything changes. Agreed fixes are
done as a stage like any other: tests first where behaviour changes,
the log entry under the night, the plan item ticked. A finding that is
real but not worth fixing now goes to `docs/PLAN.md` under its section,
with the review's date.
