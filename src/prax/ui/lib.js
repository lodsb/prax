/* prax web UI: the pure helpers, shared by the page (loaded first)
   and by the node tests (tests/ui/lib.test.js). No DOM, no fetch, no
   globals read: everything here is a function of its arguments. */
"use strict";

// HTML escaping for everything interpolated into markup.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

// The hash router: "#doc/12?chunk=3" -> {name, arg, params}. A third path
// segment on a document route is a chunk id ("#doc/12/3").
function parseHash(hash) {
  const h = String(hash || "").replace(/^#/, "") || "search";
  const [pathPart, query] = h.split("?");
  const parts = pathPart.split("/");
  const params = Object.fromEntries(new URLSearchParams(query || ""));
  if (parts[0] === "doc" && parts.length === 3 && /^\d+$/.test(parts[2])) params.chunk = parts[2];
  return { name: parts[0], arg: parts[1] || "", params };
}

function headingPath(h) {
  return (h || []).map(esc).join(" › ");
}

// An edge carries its evidence as a quote, never a chunk id (chunks are a
// disposable index). A link with ?find=<quote> lands on the chunk that
// contains the quote, or the chunk sharing most of its words.
const norm = (t) => String(t || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

function locateChunk(chunks, quote) {
  const q = norm(quote);
  if (!q) return null;
  const probe = q.slice(0, 80);
  const hit = (chunks || []).find((c) => norm(c.text).includes(probe));
  if (hit) return hit.chunk_id;
  const words = new Set(q.split(" ").filter((w) => w.length > 3));
  let best = null, bestN = 0;
  for (const c of chunks || []) {
    const cw = new Set(norm(c.text).split(" "));
    let n = 0;
    for (const w of words) if (cw.has(w)) n++;
    if (n > bestN) { bestN = n; best = c.chunk_id; }
  }
  return bestN >= Math.max(2, words.size * 0.4) ? best : null;
}

// [n] citations in an answer become links to the passage's chunk.
function citeLinks(html, passages) {
  const byN = Object.fromEntries((passages || []).map((p) => [p.n, p]));
  return String(html).replace(/\[(\d+)\]/g, (m, n) => {
    const p = byN[n];
    if (!p) return m;
    return `<a class="cite" href="#doc/${p.doc_id}${p.chunk_id ? `?chunk=${p.chunk_id}` : ""}" title="${esc(p.title)}">[${n}]</a>`;
  });
}

// The maths in a run of text: $$…$$ and \[…\] display spans, $…$ and \(…\)
// inline ones, as a parser (marker) or a model writes them. A dollar sign is
// also a currency sign, so an inline span must not start with a digit or
// a space, must hold a letter or a backslash, must stay on its line and
// under 200 characters, and its closing $ must not be followed by a digit.
// Returns [{start, end, latex, display}] in text order.
const MATH = /\$\$([^$]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([^\n]+?)\\\)|\$([^$\n]{1,200}?)\$(?!\d)/g;
function mathSpans(text) {
  const out = [];
  const s = String(text || "");
  let m;
  MATH.lastIndex = 0;
  while ((m = MATH.exec(s)) !== null) {
    const display = m[1] !== undefined || m[2] !== undefined;
    const latex = (m[1] ?? m[2] ?? m[3] ?? m[4]).trim();
    if (!latex) continue;
    if (m[4] !== undefined) {
      const inner = m[4];
      if (/^[\s\d]/.test(inner) || /\s$/.test(inner) || !/[A-Za-z\\]/.test(inner)) continue;
    }
    out.push({ start: m.index, end: m.index + m[0].length, latex, display });
  }
  return out;
}

// The figures of a document for the strip: every figure chunk with a
// picture, its caption (the reference's own, else the first reading's
// first words), where it is (page, or the moment of a video frame).
function figureItems(chunks) {
  const out = [];
  for (const c of chunks || []) {
    if (c.kind !== "figure" || !c.data || !c.data.ref) continue;
    let caption = String(c.data.caption || "").trim();
    if (!caption) {
      const r = (c.data.readings || []).find((x) => x && x.text);
      if (r) caption = String(r.text).trim().split(/(?<=[.!?])\s/)[0].slice(0, 120);
    }
    out.push({ chunk_id: c.chunk_id, ref: c.data.ref, caption, page: c.page || null, time: c.time != null ? c.time : null });
  }
  return out;
}

// A document's reference chunks as a table of what each numbered entry
// cites: number -> {href, title, chunk} — the cited document's page when
// the references pass matched it, else the entry's own chunk. Entries
// without a number are not in-text markers and are left out.
function referenceLinks(chunks) {
  const out = {};
  for (const c of chunks || []) {
    if (c.kind !== "reference" || !c.data || c.data.number == null) continue;
    const cited = c.data.cited;
    out[String(c.data.number)] = cited && cited.doc_id
      ? { href: `#doc/${cited.doc_id}`, title: cited.title || "", chunk: c.chunk_id, how: cited.how || "" }
      : { href: `#chunk-${c.chunk_id}`, title: c.data.title || "", chunk: c.chunk_id, how: "" };
  }
  return out;
}

// The in-text citation markers of a numbered reference list — "[12]",
// "[3, 5]", "[3–5]" — linked through the table above; a marker the list
// has no entry for is left as it is (an equation, a footnote), and so is
// everything that is not a bracketed number.
function citeMarkers(html, links) {
  if (!links || !Object.keys(links).length) return html;
  return String(html || "").replace(/\[(\d{1,3}(?:\s*[,\u2013\u2014-]\s*\d{1,3})*)\]/g, (whole, inner) => {
    let any = false;
    const out = inner.split(/(\s*[,\u2013\u2014-]\s*)/).map((p) => {
      const l = /^\d{1,3}$/.test(p) ? links[p] : null;
      if (!l) return p;
      any = true;
      const scroll = l.href.startsWith("#chunk-") ? ` data-scroll="${l.chunk}"` : "";
      return `<a class="cite" href="${l.href}"${scroll} title="${esc(l.title)}">${p}</a>`;
    }).join("");
    return any ? `[${out}]` : whole;
  });
}

// An ask chunk's text — the block from its head marker to its tail — as
// what the frame shows: the interior alone, the markers gone (the keep
// markers too; what they held stays), and the source list's [n] markers
// left as they are. The question and the state come from the chunk's
// data, not from here.
function askInterior(text) {
  return String(text || "")
    .replace(/^[ \t]*<!--\s*\/?prax:ask\b[^>]*-->[ \t]*$/gm, "")
    .replace(/<!--\s*\/?prax:keep\s*-->/g, "")
    .trim();
}

// The markers a person writes to put a standing question in a page: an
// id the page does not use yet, the question quoted, the options given.
function askBlockMarkers(id, question, options) {
  const opts = Object.entries(options || {}).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${k}=${v}`).join(" ");
  const q = String(question || "").replace(/"/g, "'").replace(/\s+/g, " ").trim();
  return `<!-- prax:ask id=${id}${opts ? " " + opts : ""} "${q}" -->\n<!-- /prax:ask id=${id} -->\n`;
}

// The next free block id in a page's text: q1, q2, … past the ones there.
function nextAskId(text) {
  const used = new Set();
  for (const m of String(text || "").matchAll(/<!--\s*prax:ask\s+[^>]*\bid=(\S+)/g)) used.add(m[1].replace(/["']/g, ""));
  let n = 1;
  while (used.has(`q${n}`)) n += 1;
  return `q${n}`;
}

// What `prax up` runs on this host, and the card the roles share. A
// group is the roles that compete for one resource (run: group: card);
// one holds it, and a person can hand it to another — marker wants the
// card for a scan's pages, llama-server wants it for everything else.
// The door writes the supervisor's command file (POST /up/command); it
// does not supervise anything, so a host without `prax up` shows nothing.
function mb(n) {
  return n == null ? "?" : n >= 1024 ? `${(n / 1024).toFixed(1)} GB` : `${Math.round(n)} MB`;
}
function upPanel(u) {
  const s = u && u.up;
  if (!s) return "";
  const roles = s.roles || {};
  const groups = s.groups || {};
  const demand = (u.demand && u.demand.roles) || {};
  const card = (u.gpu || []).map((c) => `${esc(c.name)} ${mb(c.free_mb)} free of ${mb(c.total_mb)}`).join(" · ");
  const grouped = new Set();
  for (const g of Object.values(groups)) (g.members || []).forEach((m) => grouped.add(m));
  const lines = Object.entries(groups).map(([name, g]) => {
    const members = g.members || [];
    const holder = g.holder;
    const others = (g.was_up || []).join(", ") || "nothing";
    const back = g.back_when === "idle" ? "when nothing waits for it" : "when you say so";
    const waiting = members.filter((m) => m !== holder && (demand[m] || 0) > 0)
      .map((m) => `${demand[m]} for ${esc(m)}`).join(", ");
    const rates = Object.entries((u.demand || {}).readings || {})
      .filter(([, n]) => n > 0)
      .map(([name]) => `${esc(name)}: ${esc(queueRate(u.demand, name))}`)
      .join(" · ");
    return `<div class="up-group">
      <div><strong>${esc(name)}</strong> ${holder
        ? `— <span class="up-holder">${esc(holder)}</span> has it ${g.fits ? "beside" : "instead of"} ${esc(others)}, back ${back}`
        : "— shared"}</div>
      <div class="up-line muted">${members.map((m) => {
        const st = (roles[m] || {}).state || "?";
        const n = demand[m] || 0;
        const shown = st === "idle"
          ? `<span title="stopped after a quiet while (idle_minutes); work that asks for it loads it again, which takes a few minutes">idle, unloaded</span>`
          : esc(st);
        return `${esc(m)}: ${shown}${n ? ` · ${n} waiting` : ""}`;
      }).join(" · ")}${card ? ` · ${card}` : ""}</div>
      ${rates ? `<div class="up-line muted">${rates}</div>` : ""}
      <div class="up-acts">${members.map((m) => (roles[m] || {}).state === "up" || m === holder
        ? ""
        : `<button type="button" class="secondary up-swap" data-to="${esc(m)}">give it to ${esc(m)}${(demand[m] || 0) ? ` (${demand[m]} waiting)` : ""}</button>`).join("")}
        ${holder ? `<button type="button" class="secondary up-unswap" data-group="${esc(name)}">back to ${esc(others)}</button>` : ""}</div>
    </div>`;
  });
  const loose = Object.entries(roles).filter(([n]) => !grouped.has(n))
    .map(([n, r]) => `${esc(n)}: ${esc(r.state || "?")}`).join(" · ");
  // who holds the card, by process: the totals cannot say (2026-09-25:
  // the embedder crawled for a day beside a 20.8 GB llama-server)
  const holders = (u.gpu_holders || []).map((h) => `${esc(h.name)} ${mb(h.mb)}`).join(" · ");
  return `<section class="up-panel">
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">This host (prax up)</h2>
    ${lines.join("") || `<p class="muted">No role shares a resource with another (run: group:).</p>`}
    ${holders ? `<p class="muted up-line">On the card: ${holders}</p>` : ""}
    ${loose ? `<p class="muted up-line">${loose}</p>` : ""}
    <p id="up-msg" class="muted"></p>
  </section>`;
}
// The regions of the library (GET /communities): the partition of the
// topical entities the maintain pass rebuilds nightly, each named and
// described by the communities step. A region without a name yet is
// shown by its first members; a summary whose members moved says so.
function regionName(c) {
  return c.label || (c.members || []).slice(0, 3).join(", ") || `region ${c.id}`;
}
function regionList(list) {
  if (!list || !list.length) return "";
  const items = list.map((c) => `<li><a href="#graph?community=${c.id}">${esc(regionName(c))}</a>
    <span class="muted">${c.size} things${c.summary_state === "stale" ? " · the summary predates its members" : ""}</span>
    ${c.summary ? `<div class="muted">${esc(c.summary)}</div>` : ""}</li>`).join("");
  return `<section class="regions"><h2 style="font-size:1rem;margin:1rem 0 .3rem">Regions of the library</h2><ul class="entities">${items}</ul></section>`;
}
function regionPage(c) {
  if (!c) return `<p class="muted">No such region.</p>`;
  const member = (m) => `<a href="#graph?entity=${encodeURIComponent(m.name)}&type=${encodeURIComponent(m.type)}">${esc(m.name)}</a> <span class="muted">${esc(m.type)}</span>`;
  const up = c.parent != null ? `<p class="muted">Part of <a href="#graph?community=${c.parent}">region ${c.parent}</a>.</p>` : "";
  const summary = c.summary
    ? `<p>${esc(c.summary)}${c.summary_state === "stale" ? ` <span class="muted">(written before its members last moved)</span>` : ""}</p>`
    : `<p class="muted">Not described yet: the communities step names it.</p>`;
  const parts = (c.parts || []).length
    ? `<h3 style="font-size:.95rem">Its parts</h3><ul class="entities">${c.parts.map((p) => `<li><a href="#graph?community=${p.id}">${esc(regionName(p))}</a> <span class="muted">${p.size} things</span></li>`).join("")}</ul>`
    : "";
  const docs = (c.documents || []).length
    ? `<h3 style="font-size:.95rem">Documents naming most of it</h3><ul class="entities">${c.documents.map((d) => `<li><a href="#doc/${d.id}">${esc(d.title || `doc ${d.id}`)}</a> <span class="muted">${d.named} of its things</span></li>`).join("")}</ul>`
    : "";
  return `<div class="region">
    <h2 style="font-size:1.1rem;margin:.5rem 0">${esc(regionName(c))} <span class="muted">· ${c.size} things</span></h2>
    ${up}${summary}${parts}
    <h3 style="font-size:.95rem">What it is made of</h3>
    <ul class="entities">${(c.members || []).map((m) => `<li>${member(m)}</li>`).join("")}</ul>
    ${docs}
  </div>`;
}

// The rows of the review page's decision lists (view-decide.js). A side
// is an entity with its type, edges and a document naming it; each button
// carries what its decision needs, so the view only posts it.
function entitySide(e) {
  if (!e) return `<span class="muted">(gone)</span>`;
  const link = `#graph?entity=${encodeURIComponent(e.name)}&type=${encodeURIComponent(e.type)}`;
  return `<div class="decide-side"><a href="${link}">${esc(e.name)}</a>
    <span class="muted">${esc(e.type)} · ${e.edges} edges</span>
    ${e.document ? `<div class="muted">in “${esc(e.document)}”</div>` : ""}</div>`;
}
function pairRow(it) {
  const k = it.keep, o = it.other;
  // the local model's calibrated probability, when it asked and left the pair
  const p = it.p_same == null ? "" : ` · model: ${Math.round(it.p_same * 100)}% same`;
  return `<article class="decide-row"><div class="decide-pair">${entitySide(k)}${entitySide(o)}</div>
    <div class="decide-acts"><span class="muted">${esc(it.type)} · names ${it.score}${p}</span>
      <button type="button" data-act="same" data-keep="${k.id}" data-other="${o.id}">same, keep “${esc(k.name)}”</button>
      <button type="button" class="secondary" data-act="same" data-keep="${o.id}" data-other="${k.id}">same, keep “${esc(o.name)}”</button>
      <button type="button" class="secondary" data-act="different" data-keep="${k.id}" data-other="${o.id}">different</button>
      <span class="decide-out muted"></span></div></article>`;
}
// What "the same thing" means (GET /graph/sameness), folded above the
// pairs: the rule the models are asked with, so a person decides by it too.
function sameRule(r) {
  if (!r || !(r.same || []).length) return "";
  const cases = (cs) => cs.map((c) => `<li>${esc(c.case)}${(c.examples || []).length
    ? ` <span class="muted">(${c.examples.map(([a, b]) => `${esc(a)} · ${esc(b)}`).join("; ")})</span>` : ""}</li>`).join("");
  const mods = Object.entries(r.modules || {}).map(([name, m]) =>
    `<h4>${esc(name)}</h4><ul>${cases(m.same || []).replace(/<li>/g, "<li>same: ")}${cases(m.different || []).replace(/<li>/g, "<li>different: ")}</ul>`).join("");
  return `<details class="same-rule"><summary>What counts as the same thing</summary>
    <div class="decide-pair"><div><h4>The same</h4><ul>${cases(r.same)}</ul></div>
    <div><h4>Different</h4><ul>${cases(r.different || [])}</ul></div></div>
    ${mods ? `<p class="muted">And for the pairs of one domain:</p>${mods}` : ""}</details>`;
}
function splitRow(it) {
  const ids = it.parts.map((p) => p.id);
  const one = it.parts.map((p) => `<button type="button" class="secondary" data-act="one" data-keep="${p.id}" data-type="${esc(p.type)}" data-others="${ids.filter((i) => i !== p.id).join(",")}">one thing: a ${esc(p.type)}</button>`).join("");
  return `<article class="decide-row"><h3 style="font-size:1rem;margin:.2rem 0">${esc(it.name)}</h3>
    <div class="decide-pair">${it.parts.map((p) => entitySide({ ...p, name: it.name })).join("")}</div>
    <div class="decide-acts">${one}
      <button type="button" data-act="apart" data-ids="${ids.join(",")}">several things</button>
      <span class="decide-out muted"></span></div></article>`;
}
// A document the personal-document rules suspect: what it is, where it
// came from, and the cues (the owner's name is only ever "name").
function suspectRow(it) {
  const cues = (it.cues || []).map((c) => esc(c.replace(/^(strong|weak): /, ""))).join(" · ");
  return `<article class="decide-row"><div class="decide-pair"><div class="decide-side">
      <a href="#doc/${it.id}">${esc(it.title || `doc ${it.id}`)}</a>
      <span class="muted">${esc(it.mime || "")}${it.added_at ? ` · ${esc(String(it.added_at).slice(0, 10))}` : ""}</span>
      ${it.path ? `<div class="muted">${esc(it.path)}</div>` : ""}</div></div>
    <div class="decide-acts"><span class="muted">${cues || "no cue"}</span>
      <button type="button" data-act="personal" data-doc="${it.id}">personal</button>
      <button type="button" class="secondary" data-act="open" data-doc="${it.id}">not personal</button>
      <span class="decide-out muted"></span></div></article>`;
}
// The clean-up tab (view-cleanup in view-decide.js): the rules as links,
// a folder of one's own, what a rule would take, and what was taken.
function cleanupRules(rules, current, folder) {
  const link = (key, about) => key === current
    ? `<b>${esc(about)}</b>`
    : `<a href="#review?${new URLSearchParams({ list: "cleanup", rule: key })}">${esc(about)}</a>`;
  const named = Object.entries(rules || {}).filter(([k]) => k !== "folder").map(([k, a]) => `<li>${link(k, a)}</li>`).join("");
  return `<ul class="entities">${named}</ul>
    <form id="cleanup-folder" class="search-form" autocomplete="off">
      <label for="cleanup-folder-input">Everything from under a folder</label>
      <input id="cleanup-folder-input" name="folder" placeholder="coredata/backups/old_users" value="${esc(folder || "")}">
      <button>look</button></form>`;
}
function cleanupPreview(res) {
  if (!res) return "";
  const rows = (res.items || []).map((it) => `<li><a href="#doc/${it.id}">${esc(it.title || `doc ${it.id}`)}</a>
    <span class="muted">${esc(it.path || "")}${it.duplicate_of ? ` · a copy of doc ${it.duplicate_of}` : ""}</span></li>`).join("");
  const personal = res.personal ? ` · ${res.personal} marked or suspected personal` : "";
  return `<article class="decide-row"><h3 style="font-size:1rem;margin:.2rem 0">${esc(res.about)}${res.folder ? `: ${esc(res.folder)}` : ""}</h3>
    <p class="muted">${res.total.toLocaleString()} documents · ${res.edges.toLocaleString()} facts${personal}${res.total > (res.items || []).length ? ` · a sample of ${(res.items || []).length}` : ""}</p>
    <ul class="entities">${rows}</ul>
    <div class="decide-acts">${res.total ? `<button type="button" data-retire="1">retire ${res.total} documents</button>` : `<span class="muted">nothing to retire</span>`}
      <span class="decide-out muted"></span></div></article>`;
}
function cleanupRuns(runs) {
  if (!(runs || []).length) return "";
  return `<h3 style="font-size:.95rem">Clean-ups done</h3><ul class="entities">${runs.map((r) =>
    `<li>${esc(r.reason || r.run)} <span class="muted">· ${r.documents} documents · ${esc(String(r.at || "").slice(0, 16).replace("T", " "))}</span>
      <button type="button" class="secondary" data-restore="${esc(r.run)}">restore</button></li>`).join("")}</ul>`;
}
function mergeRow(it) {
  const a = it.alias, into = it.into;
  if (!a || !into) return "";
  return `<article class="decide-row"><div class="decide-pair">${entitySide(a)}<div class="decide-arrow muted">folded into</div>${entitySide(into)}</div>
    <div class="decide-acts"><span class="muted">${esc(it.why)}${it.by ? ` · by ${esc(it.by)}` : " · unsigned"}</span>
      <button type="button" data-act="same" data-keep="${into.id}" data-other="${a.id}">right</button>
      <button type="button" class="secondary" data-act="split" data-entity="${a.id}">wrong: split them</button>
      <span class="decide-out muted"></span></div></article>`;
}

// What the paid steps have cost, and what is left of the budget. A host
// whose models are all local has an empty ledger and no limits, and the
// panel says so in one line. Money is what was charged at the price of
// the moment (prax.ml.budget), not an estimate of the invoice.
function usd(n) {
  const v = Number(n || 0);
  return v && v < 0.01 ? `${(v * 100).toFixed(2)} ¢` : `$${v.toFixed(2)}`;
}
function spendPanel(s) {
  if (!s || !s.budget) return "";
  const b = s.budget, led = s.ledger || {}, today = (s.today || {}).usd || 0;
  const lim = b.limits || {}, spent = b.spent || {};
  const bar = (name, spentUsd, limit) => {
    if (!limit) return `<span class="muted">${name}: ${usd(spentUsd)} (no limit set)</span>`;
    const share = Math.min(100, Math.round((spentUsd / limit) * 100));
    return `<span class="spend-bar${share >= 100 ? " spend-over" : ""}" title="${usd(spentUsd)} of ${usd(limit)}">
      ${name}: ${usd(spentUsd)} of ${usd(limit)}<i style="--share:${share}%"></i></span>`;
  };
  const steps = (led.by_step || []).slice(0, 6)
    .map((x) => `${esc(x.step)} ${usd(x.usd)}`).join(" · ");
  const models = (led.by_model || []).slice(0, 4)
    .map((x) => `${esc(x.model)} ${usd(x.usd)} over ${x.calls} call${x.calls === 1 ? "" : "s"}`).join(" · ");
  return `<section class="spend-panel">
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Spending</h2>
    <div class="spend-line">${bar("today", spent.day || 0, lim.daily_usd)} · ${bar("this month", spent.month || 0, lim.monthly_usd)}</div>
    ${b.ok ? "" : `<p class="error spend-line">${esc(b.why)} — the paid steps are held until it turns.</p>`}
    ${led.calls
      ? `<p class="muted spend-line">${led.calls} paid call${led.calls === 1 ? "" : "s"} since ${esc((s.since || "").slice(0, 10))}: ${usd(led.usd)}${steps ? ` · ${steps}` : ""}</p>
         ${models ? `<p class="muted spend-line">${models}</p>` : ""}`
      : `<p class="muted spend-line">No paid call recorded${today ? "" : " — every step on this host is a local model"}.</p>`}
  </section>`;
}

// How a waiting queue is doing: what waits, how fast it moves, and how
// long that leaves. A long queue and a stopped one look the same in a
// count, which is how the figures backlog looked stuck for a day while
// it was moving at 55 an hour.
function queueRate(demand, name) {
  const waiting = ((demand || {}).readings || {})[name] || 0;
  if (!waiting) return "";
  const rate = ((demand || {}).rate || {})[name] || 0;
  const left = ((demand || {}).hours_left || {})[name];
  if (!rate) return `${waiting} waiting · nothing has read one in hours`;
  const when = left >= 48 ? `${Math.round(left / 24)} days` : `${Math.round(left)} h`;
  return `${waiting} waiting · ${rate}/h · about ${when} left`;
}

// The language a document is in, under a name rather than a code.
const LANGUAGES = { en: "English", de: "German", fr: "French", es: "Spanish", it: "Italian", nl: "Dutch", pt: "Portuguese", sv: "Swedish", da: "Danish", pl: "Polish", cs: "Czech", ru: "Russian", tr: "Turkish", ja: "Japanese", zh: "Chinese", ko: "Korean", ar: "Arabic", he: "Hebrew", el: "Greek", la: "Latin" };
function languageName(code) {
  return code ? (LANGUAGES[code] || code) : "";
}

// What an advertisement or a comment section says on its folded line:
// what it is, whose it is, and how much of it there is.
function asideLine(kind, data, text, blocks) {
  const n = (text || "").length;
  const size = n >= 1000 ? `${Math.round(n / 100) / 10}k characters` : `${n} characters`;
  const many = blocks > 1 ? ` · ${blocks} blocks` : "";
  if (kind === "comment") return `what readers wrote${many} · ${size}`;
  const d = data || {};
  const why = (d.why || []).join(", ");
  return `advertisement${d.brand ? ` · ${esc(d.brand)}` : ""}${why ? ` · ${esc(why)}` : ""}${many} · ${size}`;
}

// An amount as a cook writes it: halves and quarters as fractions, a
// whole number without its zero.
const FRACTIONS = { 0.5: "½", 0.25: "¼", 0.75: "¾", 0.333: "⅓", 0.667: "⅔", 0.125: "⅛" };
function amount(n) {
  if (n == null) return "";
  const whole = Math.floor(n), rest = Math.round((n - whole) * 1000) / 1000;
  const frac = FRACTIONS[rest];
  if (frac) return (whole ? whole : "") + frac;
  return String(Math.round(n * 100) / 100);
}

// A recipe's ingredient list as the box it is on the page: for how many,
// then every line with its amount, grouped as the recipe groups them.
function ingredientsBox(data) {
  const d = data || {}, groups = d.groups || [];
  if (!groups.length) return "";
  const item = (it) => {
    const head = it.amount != null ? `<b>${esc(amount(it.amount))}${it.unit ? ` ${esc(it.unit)}` : ""}</b> ` : "";
    const note = it.note ? ` <span class="muted">(${esc(it.note)})</span>` : "";
    return `<li>${head}${esc(it.item || it.text)}${note}</li>`;
  };
  const group = (g) => `${g.name ? `<h4>${esc(g.name)}</h4>` : ""}<ul class="ingredient-list">${(g.items || []).map(item).join("")}</ul>`;
  const serves = d.servings ? `<p class="ingredients-serves">${esc(d.servings.text)}</p>` : "";
  return `<div class="ingredients-box">${serves}${groups.map(group).join("")}</div>`;
}

// Why a queue is not moving: what the door says about the step nobody
// is asking for (`work.who_runs`). Empty when the work is in hand, so a
// view can render it unconditionally.
function waitingNote(w, pending) {
  if (!w || !w.why || !pending) return "";
  const how = w.how ? ` Run <code>${esc(w.how)} -n ${pending}</code>, or give the worker role <code>steps</code>${w.paid ? " and <code>spend: true</code>" : ""} in <code>prax.yaml</code>.` : "";
  const last = w.asked ? ` A worker last asked at ${esc(String(w.asked).slice(11, 16))}.` : "";
  return `<p class="waiting-note">Nothing here is doing that work: ${esc(w.why)}.${how}${last}</p>`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { esc, parseHash, headingPath, norm, locateChunk, citeLinks, mathSpans, figureItems, referenceLinks, citeMarkers, askInterior, askBlockMarkers, nextAskId, upPanel, mb, spendPanel, regionList, regionPage, regionName, pairRow, sameRule, suspectRow, cleanupRules, cleanupPreview, cleanupRuns, splitRow, mergeRow, entitySide, usd, waitingNote, asideLine, ingredientsBox, amount, languageName, queueRate };
}
