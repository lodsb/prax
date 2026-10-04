export const meta = {
  name: 'code-review',
  description: 'Quality review of prax code since a base commit: five dimensions, every finding re-checked',
  whenToUse: 'The code-review skill in full mode: args {base, scope?, dimensions?}',
  phases: [
    { title: 'Review', detail: 'one reviewer per dimension, read-only' },
    { title: 'Verify', detail: 'one skeptic per dimension re-checks every finding' },
  ],
}

// args: {base: "<commit before the range>", scope: "src" (a path), dimensions: [...keys]}
const BASE = (args && args.base) || 'HEAD~20'
const SCOPE = (args && args.scope) || 'src'
const SKILL = '.claude/skills/code-review'

const COMMON = `You are reviewing the prax repository (the working directory) for code quality, not safety. The code under review is what changed in ${SCOPE} since commit ${BASE}: \`git log --oneline ${BASE}..HEAD -- ${SCOPE}\`, \`git diff ${BASE} HEAD -- ${SCOPE}\`. Start from \`python ${SKILL}/scripts/survey.py ${BASE} ${SCOPE}\` (run it with the repository's venv python) and read the checklist for your dimension in ${SKILL}/references/checklists.md before anything else; ${SKILL}/SKILL.md says what a finding must carry.

Hard rules:
- READ ONLY: no edits, no new files in the repository, no git command that changes anything, no installs.
- Never open the live database or call the running door. For measurements use a copy of the library if one is named in the task, read-only (sqlite3 URI mode=ro); otherwise a test store.
- Report only findings with a cost someone will pay later, each with file and line, the evidence you checked (and what you ran), and why it matters for this project. No style the linters own, no wording, no taste. An empty list is a fine answer.`

const DIMENSIONS = [
  { key: 'hygiene', title: 'Hygiene: reuse, modularity, packaging, structure', extra: 'Search for repeated shapes across the whole src tree, not only the diff: a pattern the change added may already exist elsewhere.' },
  { key: 'plan', title: 'Extensibility against the plan', extra: 'Read docs/PLAN.md (the order and the open stage items) first, and name the plan item each finding concerns.' },
  { key: 'independence', title: "Independence from the owner's library", extra: 'For each tuned number, say whether a script or test re-measures it. For each baked-in word list, say which languages or domains it covers. Never quote a personal document; refer to it by id.' },
  { key: 'generality', title: 'Conceptual generality', extra: 'A generalisation is only a finding when a second case is real (in the code, the library or the plan); say which.' },
  { key: 'performance', title: 'Performance on the Pi-class target', extra: 'Measure every claim: a timing, EXPLAIN QUERY PLAN, a memory figure. An unmeasured guess is not a finding.' },
]
const wanted = (args && args.dimensions) || DIMENSIONS.map(d => d.key)
const chosen = DIMENSIONS.filter(d => wanted.includes(d.key))
const skipped = DIMENSIONS.filter(d => !wanted.includes(d.key)).map(d => d.key)
if (skipped.length) log(`dimensions not run: ${skipped.join(', ')}`)

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'number' },
          cost: { type: 'string', enum: ['high', 'medium', 'low'], description: 'what leaving it costs' },
          evidence: { type: 'string' },
          why_here: { type: 'string', description: 'the plan item, library or target it affects' },
          fix: { type: 'string' },
          fix_size: { type: 'string', description: 'an hour, a stage, a migration' },
        },
        required: ['title', 'file', 'cost', 'evidence', 'why_here', 'fix'],
      },
    },
    covered: { type: 'string', description: 'what was looked at and found sound' },
  },
  required: ['findings', 'covered'],
}
const VERDICTS = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          index: { type: 'number' },
          real: { type: 'boolean' },
          cost: { type: 'string', enum: ['high', 'medium', 'low'] },
          reason: { type: 'string' },
        },
        required: ['index', 'real', 'reason'],
      },
    },
  },
  required: ['verdicts'],
}

const results = await pipeline(
  chosen,
  d => agent(`${COMMON}\n\nYour dimension: ${d.title} (checklist section "${d.key}"). ${d.extra}`, { label: `review:${d.key}`, phase: 'Review', schema: FINDINGS }),
  async (found, d) => {
    const items = (found && found.findings) || []
    if (!items.length) return { dimension: d.key, confirmed: [], rejected: [], covered: found ? found.covered : '' }
    const listed = items.map((f, i) => `[${i}] ${f.cost.toUpperCase()} ${f.title}\n  at ${f.file}:${f.line || '?'}\n  evidence: ${f.evidence}\n  why here: ${f.why_here}`).join('\n\n')
    const v = await agent(`${COMMON}\n\nYou are a skeptic for the dimension "${d.title}". Another reviewer reported the findings below. For EACH, open the code and try to refute it: is it as described, is the cost real for this project (the plan, another person's library, the board), is it already handled or deliberately chosen (CLAUDE.md, docs/rationale.md, a comment)? Re-run any measurement it rests on. Mark real=true only if you confirmed it yourself.\n\n${listed}`, { label: `verify:${d.key}`, phase: 'Verify', schema: VERDICTS })
    const byIndex = new Map(((v && v.verdicts) || []).map(x => [x.index, x]))
    const confirmed = [], rejected = []
    items.forEach((f, i) => {
      const x = byIndex.get(i)
      if (x && x.real) confirmed.push({ ...f, dimension: d.key, cost: x.cost || f.cost, verified: x.reason })
      else rejected.push({ title: f.title, file: f.file, dimension: d.key, why: x ? x.reason : 'no verdict' })
    })
    log(`${d.key}: ${confirmed.length} confirmed, ${rejected.length} refuted`)
    return { dimension: d.key, confirmed, rejected, covered: found.covered }
  },
)

const rank = { high: 0, medium: 1, low: 2 }
const done = results.filter(Boolean)
return {
  confirmed: done.flatMap(r => r.confirmed).sort((a, b) => rank[a.cost] - rank[b.cost]),
  rejected: done.flatMap(r => r.rejected),
  covered: Object.fromEntries(done.map(r => [r.dimension, r.covered])),
  not_run: skipped.concat(chosen.map(d => d.key).filter(k => !done.some(r => r.dimension === k))),
}
