// Jobs: the passes running and lately run, the model servers, this
// host's roles and spending, and the health panel.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// ---------------------------------------------------------------- router

// ------------------------------------------------------------------ jobs
// What runs on the batch host (GET /jobs): running passes with a bar and a
// heartbeat, then what ran lately. The change poll below keeps it current.

function jobRow(j) {
  const pct = j.total ? Math.min(100, Math.round((100 * j.done) / j.total)) : (j.status === "running" ? 0 : 100);
  const when = (s) => (s || "").slice(5, 16).replace("T", " ");
  const state = j.status === "running" ? (j.stale ? `<span class="error" title="no heartbeat for ${j.age} s">stale?</span>` : `running · ${j.age}s ago`) : j.status;
  return `<tr>
      <td>${esc(j.name)}</td>
      <td><div class="jobbar ${j.stale ? "stale" : ""}" title="${j.done}${j.total ? ` / ${j.total}` : ""}"><div style="width:${pct}%"></div></div></td>
      <td class="num">${j.done}${j.total ? ` / ${j.total}` : ""}</td>
      <td class="muted" title="${esc(j.note || "")}">${esc((j.note || "").slice(0, 90))}</td>
      <td class="muted">${when(j.started_at)}</td>
      <td class="${j.status === "failed" ? "error" : ""}">${state}</td>
      <td class="muted">${esc(j.host || "")}${j.pid ? `:${j.pid}` : ""}</td>
    </tr>`;
}

function hostLine(h) {
  if (!h || h.ram_total_mb == null) return "";
  const gb = (mb) => (mb / 1024).toFixed(1);
  const tight = h.commit_free_mb != null && (h.commit_free_mb < 4096 || h.commit_free_mb < 0.1 * h.commit_limit_mb);
  return `<p class="muted">This door runs on <b>${esc(h.name || "")}</b>: ${gb(h.ram_free_mb)} of ${gb(h.ram_total_mb)} GB RAM free` +
    (h.commit_limit_mb != null ? `, commit headroom <span class="${tight ? "error" : ""}" title="RAM plus page file, minus what every process has charged; a GPU model server on Windows charges its VRAM here">${gb(h.commit_free_mb)} of ${gb(h.commit_limit_mb)} GB</span>${tight ? " (tight: close something or enlarge the page file)" : ""}` : "") + `.</p>`;
}

async function upCommand(body, msg) {
  msg.textContent = "asking…";
  try {
    const r = await post("/up/command", body);
    msg.textContent = `asked: ${r.asked} ${r.role || ""}${r.released ? ` · ${r.released} held requests released` : ""}`;
    setTimeout(() => render({ keepScroll: true }), 2500);
  } catch (err) { msg.textContent = err.message; }
}
view.addEventListener("click", (e) => {
  const swap = e.target.closest("button.up-swap");
  const back = e.target.closest("button.up-unswap");
  if (!swap && !back) return;
  const msg = document.getElementById("up-msg") || document.createElement("p");
  if (swap) upCommand({ cmd: "swap", to: swap.dataset.to, back_when: "idle" }, msg);
  else upCommand({ cmd: "unswap", group: back.dataset.group }, msg);
});

// The model servers prax.yaml names, with their load when started with
// --metrics: which model, slots, whether it sees images, tokens per second
// and the KV cache in use, so a slow pass can be told from an idle one.
function serverLines(servers) {
  if (!servers || !servers.length) return "";
  const rows = servers.map((s) => {
    const head = `<b>${esc(s.name)}</b> <span class="muted">${esc(s.url)}</span>`;
    if (!s.reachable) return `<li>${head} — <span class="error">not reachable</span> <span class="muted">(${esc(s.error || "")})</span></li>`;
    const m = s.metrics;
    const load = m
      ? ` · ${m.processing || 0} running, ${m.deferred || 0} waiting` +
        (m.prompt_tps ? ` · reading ${Math.round(m.prompt_tps)} tok/s` : "") +
        (m.predicted_tps ? ` · writing ${Math.round(m.predicted_tps)} tok/s` : "") +
        (m.prompt_tokens_total ? ` · ${(m.prompt_tokens_total / 1000).toFixed(0)}k tokens read since start${m.prompt_tokens_cached ? ` (${Math.round(100 * m.prompt_tokens_cached / m.prompt_tokens_total)}% from the prompt cache)` : ""}` : "") +
        (m.predicted_tokens_total ? `, ${(m.predicted_tokens_total / 1000).toFixed(0)}k written` : "")
      : ` · <span class="muted">no load figures (start it with --metrics)</span>`;
    return `<li>${head}: ${esc(s.file || s.alias || s.model)} · ${s.slots} slot${s.slots === 1 ? "" : "s"}${s.vision ? " · sees images" : ""}${load}</li>`;
  });
  return `<ul class="servers">${rows.join("")}</ul>`;
}

// Reading requests (a person asked for an extractor on a document): what
// waits for a worker, and what came back lately.
function readingLines(r) {
  if (!r || (!r.requested.length && !r.recent.length)) return "";
  const row = (x) => `<li><a href="#doc/${x.doc_id}">${esc(x.title || "doc " + x.doc_id)}</a> · ${esc(x.extractor)}${x.mode && x.mode !== "scans" ? ` (${esc(x.mode)})` : ""} · ${esc(x.by || "?")} ${esc((x.finished_at || x.at || "").replace("T", " ").slice(0, 16))}${x.state === "requested" ? "" : ` → <span class="${x.state === "error" ? "error" : ""}">${esc(x.outcome || x.state)}${x.error ? ": " + esc(x.error) : ""}</span>`}</li>`;
  const vision = r.vision && r.vision.model ? `the vision step is <b>${esc(r.vision.model)}</b>${r.vision.kind === "claude" ? " (paid: a worker will not run it; run parse_pending.py yourself)" : ""}` : "no vision model is set";
  return `
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Readings asked for (${r.waiting != null ? r.waiting : r.requested.length} waiting${r.waiting > r.requested.length ? `, ${r.requested.length} shown` : ""})</h2>
    <p class="muted">A reading is an extractor a person asked for on one document (its page's "read again…"); the worker takes these before the pending captures. ${vision.charAt(0).toUpperCase() + vision.slice(1)}.</p>
    ${r.requested.length ? `<ul class="servers">${r.requested.map(row).join("")}</ul>` : ""}
    ${r.recent.length ? `<p class="muted" style="margin:.4rem 0 .1rem">Came back:</p><ul class="servers">${r.recent.map(row).join("")}</ul>` : ""}`;
}

// The store's health: what the recurring ailments find right now
// (GET /heal), a "repair" on each ailment that can be, and one button
// that repairs all of them together — each a job. Nothing is deleted
// by a repair: edges are ended, items resolved, texts re-indexed from
// their own artifact, stamps moved.
//
// The check reads every chunk (ten seconds on a large store), so the
// page is drawn first and the panel filled in after; the result is kept
// for the live refreshes of the page and asked for again after a repair,
// on "check again", or when it is older than a few minutes.
let health = null, healthAt = 0;
const HEALTH_FRESH = 5 * 60 * 1000;

function healthLines(h) {
  if (!h) return `<p class="muted">checking the store's health…</p>`;
  const rows = h.ailments.map((a) => `<li class="${a.count ? "" : "muted"}">
      <b>${a.count.toLocaleString()}${a.capped ? "+" : ""}</b> ${esc(a.name)}${a.count ? ` <span class="muted">— ${esc(a.what)}</span>` : ""}${a.count && !a.repairable ? ` <span class="muted">(${esc(a.fix)})</span>` : ""}${a.count && a.repairable ? ` <button type="button" class="linkish heal-one" data-check="${esc(a.name)}" title="${esc(a.fix)}">repair</button>` : ""}${a.count && a.offers && a.offers.length ? a.offers.map((o, i) => ` <button type="button" class="linkish heal-offer" data-check="${esc(a.name)}" data-offer="${i}">${esc(o.label)}</button>`).join(" ·") : ""}</li>`);
  const repairable = h.ailments.filter((a) => a.count && a.repairable).length;
  return `
    <p class="muted" style="margin:0 0 .3rem">${h.found ? `${h.found} thing${h.found > 1 ? "s" : ""} to look at` : "nothing to repair"} · checked ${esc((h.checked_at || "").replace("T", " ").slice(0, 16))} · <button type="button" class="linkish" id="heal-check">check again</button></p>
    <ul class="servers">${rows.join("")}</ul>
    ${repairable > 1 ? `<p><button type="button" id="heal-now" class="secondary">Repair all ${repairable} together</button></p>` : ""}
    <p id="heal-msg" class="muted"></p>`;
}

function fillHealth() {
  const box = document.getElementById("health");
  if (!box) return;  // the page moved on
  box.innerHTML = healthLines(health);
  const heal = document.getElementById("heal-now");
  if (heal) heal.addEventListener("click", () => healNow(heal, null));
  const again = document.getElementById("heal-check");
  if (again) again.addEventListener("click", () => { health = null; fillHealth(); checkHealth(); });
  box.querySelectorAll(".heal-one").forEach((b) => b.addEventListener("click", () => healNow(b, [b.dataset.check])));
  box.querySelectorAll(".heal-offer").forEach((b) => b.addEventListener("click", () => offerNow(b)));
}

// An ailment's offer: a reading over everything it found (POST
// /readings/bulk); the worker drains the requests, Jobs shows them waiting.
async function offerNow(button) {
  const ailment = (health ? health.ailments : []).find((a) => a.name === button.dataset.check);
  const offer = ailment && ailment.offers[Number(button.dataset.offer)];
  if (!offer) return;
  const msg = document.getElementById("heal-msg");
  button.disabled = true;
  msg.textContent = "asking…";
  try {
    const { label, ...body } = offer;
    const r = await post("/readings/bulk", body);
    msg.textContent = `${label}: asked on ${r.requested} of ${r.selected}${r.skipped ? ` (${r.skipped} skipped)` : ""} — a worker takes them from here`;
  } catch (err) { msg.textContent = err.message; button.disabled = false; }
}

async function checkHealth() {
  if (health && Date.now() - healthAt < HEALTH_FRESH) return;
  let h;
  try { h = await api("/heal", { examples: 0 }); } catch (_) { return; /* the panel keeps saying it is checking */ }
  health = h;
  healthAt = Date.now();
  fillHealth();
}

// After a repair only the repaired ailments are looked at again (GET
// /heal?check=a,b) and put in place of their old rows: the others did not
// change, and the glyph scan among them costs ten seconds.
async function recheckHealth(names) {
  if (!health || !names.length) return;
  let h;
  try { h = await api("/heal", { check: names.join(","), examples: 0 }); } catch (_) { return; }
  const fresh = new Map(h.ailments.map((a) => [a.name, a]));
  health = {
    ...health,
    ailments: health.ailments.map((a) => fresh.get(a.name) || a),
    checked_at: h.checked_at,
  };
  health.found = health.ailments.filter((a) => a.count).length;
}

// One heal request (every repairable ailment, or the one named) and its
// outcome in the panel's message line; the page follows once the job is
// through, with the repaired ailments checked again.
async function healNow(button, checks) {
  const msg = document.getElementById("heal-msg");
  button.disabled = true;
  msg.textContent = "repairing… (a job; the page follows)";
  try {
    const r = await post("/heal", checks ? { checks } : {});
    msg.textContent = Object.entries(r).map(([k, v]) => `${k}: ${typeof v === "string" ? v : `${v.repaired} of ${v.found}${v["left alone"] ? ` (${v["left alone"]} left alone)` : ""}`}`).join(" · ") || "nothing to repair";
    await recheckHealth(checks || Object.keys(r));
    setTimeout(() => render({ keepScroll: true }), 1500);
  } catch (err) { msg.textContent = err.message; button.disabled = false; }
}

async function viewJobs(p) {
  loading();
  let d, servers = [], readings = null, host = null, money = null;
  try { d = await api("/jobs", { limit: p.limit || 30 }); } catch (err) { view.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  try { servers = (await api("/models/servers")).servers; } catch (_) { /* the list is a nicety */ }
  try { readings = await api("/readings", { limit: 20 }); } catch (_) { /* so is this one */ }
  try { host = await api("/up"); } catch (_) { /* no supervisor here, or an older door */ }
  try { money = await api("/spending", { days: 30 }); } catch (_) { /* an older door */ }
  const table = (rows) => `<table class="doc-list"><thead><tr><th>job</th><th>progress</th><th class="num">done</th><th>note</th><th>started</th><th>state</th><th>where</th></tr></thead><tbody>${rows.map(jobRow).join("")}</tbody></table>`;
  view.innerHTML = `
    <p class="muted">The passes announce themselves here: the worker's session, parsing, titles, extraction, embedding. A running job without a heartbeat for ten minutes is marked stale; one gone for half an hour is closed.</p>
    ${hostLine(d.host)}
    ${serverLines(servers)}
    ${readingLines(readings)}
    ${upPanel(host)}
    ${spendPanel(money)}
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Running (${d.running.length})</h2>
    ${d.running.length ? table(d.running) : `<p class="muted">Nothing running. On the machine with the models: <code>scripts/work.py --watch</code> keeps captures moving.</p>`}
    <h2 style="font-size:1rem;margin:1.2rem 0 .3rem">Recent</h2>
    ${d.recent.length ? table(d.recent) : `<p class="muted">No finished jobs yet.</p>`}
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Health</h2>
    <div id="health"></div>`;
  fillHealth();
  checkHealth();
}
