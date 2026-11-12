export const meta = {
  name: 'code-review',
  description: 'Quality review of prax code since a base commit: ten dimensions, every finding re-checked',
  whenToUse: 'The code-review skill in full mode: args {base, scope?, dimensions?, ultra?}',
  phases: [
    { title: 'Review', detail: 'reviewers per dimension, read-only' },
    { title: 'Verify', detail: 'skeptics re-check every finding' },
    { title: 'Critic', detail: 'ultra: what the review missed' },
  ],
}

// args: {base: "<commit before the range>", scope: "src" (a path),
//        dimensions: [...keys], ultra: true}
// ultra: two reviewers a dimension from different angles, a skeptic for
// each finding rather than one a dimension, and a completeness critic
const BASE = (args && args.base) || 'HEAD~20'
const SCOPE = (args && args.scope) || 'src'
const ULTRA = !!(args && args.ultra)
const SKILL = '.claude/skills/code-review'

const COMMON = `You are reviewing the prax repository (the working directory) for code quality, not safety (a safety and correctness review was done separately, the latest docs/eval/safety-review-*.md or review-*.md; do not report its findings again). The code under review is what changed in ${SCOPE} since commit ${BASE}: \`git log --oneline ${BASE}..HEAD -- ${SCOPE}\`, \`git diff ${BASE} HEAD -- ${SCOPE}\`. Start from \`.venv/Scripts/python.exe ${SKILL}/scripts/survey.py ${BASE} ${SCOPE}\` and read the checklist for your dimension in ${SKILL}/references/checklists.md before anything else; ${SKILL}/SKILL.md says what a finding must carry.

Hard rules:
- READ ONLY: no edits, no new files in the repository, no git command that changes anything, no installs. Throwaway scripts go in your own temp folder.
- Never open the live database or call the running door. For measurements use a test store (tmp_path-style, PRAX_DATA_DIR set to a temp folder).
- Report only findings with a cost someone will pay later, each with file and line, the evidence you checked (and what you ran), and why it matters for this project. No style the linters own, no wording, no taste. An empty list is a fine answer. Fewer, solid findings beat a long list.`

const DIMENSIONS = [
  { key: 'hygiene', title: 'Hygiene: reuse, modularity, packaging, structure', extra: 'Search for repeated shapes across the whole src tree, not only the diff: a pattern the change added may already exist elsewhere.', angles: ['the store package (src/prax/store)', 'everything outside the store'] },
  { key: 'plan', title: 'Extensibility against the plan', extra: 'Read docs/PLAN.md (the order and the open stage items, stage AU at the top) first, and name the plan item each finding concerns.', angles: ['the next three plan items', 'the stages further out'] },
  { key: 'independence', title: "Independence from the owner's library", extra: 'For each tuned number, say whether a script or test re-measures it. For each baked-in word list, say which languages or domains it covers. Never quote a personal document; refer to it by id.', angles: ['tuned numbers and thresholds', 'word lists, languages, names and paths'] },
  { key: 'generality', title: 'Conceptual generality', extra: 'A generalisation is only a finding when a second case is real (in the code, the library or the plan); say which.', angles: ['special cases waiting for a concept', 'general machinery built for one case'] },
  { key: 'performance', title: 'Performance on the Pi-class target', extra: 'Measure every claim: a timing, EXPLAIN QUERY PLAN, a memory figure. An unmeasured guess is not a finding.', angles: ['per-request work and SQL plans', 'batch passes, start-up and memory peaks'] },
  { key: 'tests', title: 'Test quality', extra: `read the tests the range added or changed (\`git diff ${BASE} HEAD --stat -- tests\`). Look for a test that cannot fail (asserts what it just set, or a mock that answers the assertion); one that tests the implementation rather than the behaviour, so a sound refactor breaks it; behaviour the range added with no test at all (follow each new public function to a test); fixtures repeated across files that one conftest fixture would hold; slow tests (run \`pytest --durations=25 -q\` on the files the range touched, never the full suite) and why they are slow; order or timing dependence (sleeps, wall clocks, shared module state). The full suite takes eleven minutes on the dev box: a finding that cuts that with no lost coverage has a cost worth reporting.`, angles: ['tests that cannot fail or test the wrong thing', 'missing coverage and slow or fragile tests'] },
  { key: 'errors', title: 'Error handling and observability', extra: 'an exception swallowed (bare except, except Exception with no log or with a log nobody reads); an error turned into a silent default a caller cannot tell from a real answer; a failure a person on the Jobs page, the tray or the log cannot see or tell apart from success; a retry with no bound or no backoff; an error message that does not say which document, step or setting; the door\'s NotFound/ValueError contract (CLAUDE.md "Conventions") broken by a plain KeyError or a caught-and-reraised 500. Say how the owner would find out the thing failed.', angles: ['the door, the store and the steps', 'the worker, the host supervisor, the importers and the parsers'] },
  { key: 'contracts', title: 'Interface and contract design', extra: 'the door\'s routes and the MCP tools as an API an agent and a stranger\'s client build on: a parameter whose meaning differs between two routes (limit, offset, domain, as_of); a response shape that differs for the same thing (a document, a hit, an edge) across routes; a breaking change in the range with no note; a route the UI or MCP calls that the OpenAPI schema describes wrongly; a store function whose signature forces callers into one use (boolean traps, positional args that should be keyword). Invariant 6 (agent-shaped endpoints) is the measure for size.', angles: ['the door routes and the MCP tools', 'the store surface and the step protocol (prax.work)'] },
  { key: 'operability', title: 'Operability: configuration, start-up, upgrades, recovery', extra: 'a setting read in code that prax.example.yaml, docs/howto.md or the config reference does not document; a default that only works on the owner\'s machine; a migration or upgrade step that is slow on a library of 60,000 documents, or not resumable; what happens when the door or worker is killed mid-pass (the 2026-11-11 Windows Update reboot is a real case); a log or run file that grows without a bound; anything `prax up` or the tray cannot show or restart. A stranger installing prax on a Pi is the reader.', angles: ['configuration and documentation of settings', 'start-up, upgrade, interruption and growth'] },
  { key: 'docs', title: 'Documentation drift', extra: 'CLAUDE.md, docs/architecture.md, docs/meta.md, docs/howto.md and docstrings against the code the range changed: a name, function, setting, default, number or file that no longer exists or no longer behaves as written; an invariant whose kind (checked, enforced, measured, chosen) no longer matches what catches a breach; a docstring that describes the old behaviour. Each finding names the sentence and the code that contradicts it. Wording is not a finding; a false statement is.', angles: ['CLAUDE.md and docs/architecture.md', 'docs/meta.md, docs/howto.md and docstrings'] },
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
const VERDICT = {
  type: 'object',
  properties: {
    real: { type: 'boolean' },
    cost: { type: 'string', enum: ['high', 'medium', 'low'] },
    reason: { type: 'string' },
  },
  required: ['real', 'reason'],
}

const SKEPTIC = d => `${COMMON}\n\nYou are a skeptic for the dimension "${d.title}". Another reviewer reported the finding(s) below. Open the code and try to refute each: is it as described, is the cost real for this project (the plan, another person's library, the board), is it already handled or deliberately chosen (CLAUDE.md, docs/rationale.md, docs/PLAN.md, a comment)? Re-run any measurement it rests on. Mark real=true only if you confirmed it yourself; when unsure, real=false.`

const describe = (f, i) => `[${i}] ${f.cost.toUpperCase()} ${f.title}\n  at ${f.file}:${f.line || '?'}\n  evidence: ${f.evidence}\n  why here: ${f.why_here}\n  fix: ${f.fix}`

// ultra: one reviewer per angle; else one reviewer for the dimension
const review = async d => {
  const angles = ULTRA ? d.angles : [null]
  const got = await parallel(angles.map((a, n) => () => agent(
    `${COMMON}\n\nYour dimension: ${d.title} (checklist section "${d.key}"). ${d.extra}` + (a ? `\n\nYour angle within it: ${a}. Another reviewer takes the other angle; stay on yours.` : ''),
    { label: `review:${d.key}${a ? ':' + (n + 1) : ''}`, phase: 'Review', schema: FINDINGS },
  )))
  const ok = got.filter(Boolean)
  return {
    findings: ok.flatMap(r => r.findings || []),
    covered: ok.map(r => r.covered).join('\n\n'),
  }
}

const verify = async (found, d) => {
  const items = (found && found.findings) || []
  if (!items.length) return { dimension: d.key, confirmed: [], rejected: [], covered: found ? found.covered : '' }
  let verdicts
  if (ULTRA) {
    verdicts = await parallel(items.map((f, i) => () => agent(
      `${SKEPTIC(d)}\n\n${describe(f, 0)}`,
      { label: `verify:${d.key}:${i}`, phase: 'Verify', schema: VERDICT },
    )))
  } else {
    const v = await agent(`${SKEPTIC(d)}\n\n${items.map(describe).join('\n\n')}`, { label: `verify:${d.key}`, phase: 'Verify', schema: VERDICTS })
    const byIndex = new Map(((v && v.verdicts) || []).map(x => [x.index, x]))
    verdicts = items.map((_, i) => byIndex.get(i))
  }
  const confirmed = [], rejected = []
  items.forEach((f, i) => {
    const x = verdicts[i]
    if (x && x.real) confirmed.push({ ...f, dimension: d.key, cost: x.cost || f.cost, verified: x.reason })
    else rejected.push({ title: f.title, file: f.file, dimension: d.key, why: x ? x.reason : 'no verdict' })
  })
  log(`${d.key}: ${confirmed.length} confirmed, ${rejected.length} refuted`)
  return { dimension: d.key, confirmed, rejected, covered: found.covered }
}

const results = await pipeline(chosen, review, verify)

const rank = { high: 0, medium: 1, low: 2 }
const done = results.filter(Boolean)
const confirmed = done.flatMap(r => r.confirmed).sort((a, b) => rank[a.cost] - rank[b.cost])
let missed = null
if (ULTRA) {
  phase('Critic')
  const titles = confirmed.map(f => `- [${f.dimension}] ${f.title} (${f.file})`).join('\n') || '(none)'
  missed = await agent(
    `${COMMON}\n\nYou are the completeness critic of a quality review over ten dimensions (${chosen.map(d => d.key).join(', ')}). It confirmed these findings:\n${titles}\n\nWhat did it miss? Look at the largest changes of the range (\`git diff --stat ${BASE} HEAD -- ${SCOPE}\`) and at what no finding touches. Report only what you confirmed yourself, with the same evidence a reviewer must give; an empty list is a fine answer.`,
    { label: 'critic', phase: 'Critic', schema: FINDINGS },
  )
}
return {
  confirmed,
  missed: missed ? (missed.findings || []).map(f => ({ ...f, dimension: 'critic', verified: 'critic, not re-checked' })) : [],
  rejected: done.flatMap(r => r.rejected),
  covered: Object.fromEntries(done.map(r => [r.dimension, r.covered])),
  not_run: skipped.concat(chosen.map(d => d.key).filter(k => !done.some(r => r.dimension === k))),
}
