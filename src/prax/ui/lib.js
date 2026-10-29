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

// The chunk a link means. A chunk id is not a passage's name: a re-index
// hands a changed passage's id to its successor (tests/test_store.py,
// 2026-10-03). So a link that also says the passage's words
// (#doc/N?chunk=M&find=…) is trusted only while chunk M still holds them;
// else the words find the passage, or nothing is highlighted.
function chunkTarget(chunks, chunkId, find) {
  const id = chunkId ? Number(chunkId) : null;
  const held = id != null ? (chunks || []).find((c) => c.chunk_id === id) : null;
  if (!find) return held ? id : null;
  if (held && locateChunk([held], find) === id) return id;
  return locateChunk(chunks, find);
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

// The pages a pager names around the current one (1-based): the first,
// the last and two either side, null where pages are left out.
function pageWindow(current, pages) {
  const want = new Set([1, pages]);
  for (let p = current - 2; p <= current + 2; p++) if (p >= 1 && p <= pages) want.add(p);
  const out = [];
  let last = 0;
  for (const p of [...want].sort((a, b) => a - b)) {
    if (p - last > 1) out.push(null);
    out.push(p);
    last = p;
  }
  return out;
}
// A pager under a paged list: ‹ 1 … 4 5 6 7 8 … 30 › and where it is.
// href(offset) is the link of the page starting there; each link carries
// data-offset too, for a list paged in place. One page needs no pager.
function pagerHtml(total, offset, limit, href) {
  if (total <= limit) return "";
  const pages = Math.ceil(total / limit);
  const current = Math.floor(offset / limit) + 1;
  const to = (p, text, cls) => `<a href="${esc(href((p - 1) * limit))}" data-offset="${(p - 1) * limit}"${cls ? ` class="${cls}"` : ""}>${text}</a>`;
  const nums = pageWindow(current, pages).map((p) => p === null ? `<span class="muted">…</span>`
    : p === current ? `<b aria-current="page">${p}</b>` : to(p, String(p)));
  const prev = current > 1 ? to(current - 1, "‹", "step") : "";
  const next = current < pages ? to(current + 1, "›", "step") : "";
  const span = `${(offset + 1).toLocaleString()}–${Math.min(offset + limit, total).toLocaleString()} of ${total.toLocaleString()}`;
  return `<div class="pager pager-pages" role="navigation" aria-label="pages">${prev}${nums.join("")}${next}<span class="muted">${span}</span></div>`;
}
// A picture of a set: a figure of a document (``ref``) or a photo. Its
// tile's image and the viewer's, larger.
function pictureSrc(it, large) {
  if (it.ref) return `/doc/${it.doc_id}/figure/${it.ref}`;
  return `/doc/${it.doc_id}/${large ? "preview" : "thumb"}`;
}
// The picture of an item, or, for one with no copy to show (a video, a
// photo not read yet: ``thumb: false``), what it is in words.
function pictureOrWord(it, large, cap) {
  if (it.thumb === false) {
    const kind = { video: "video", audio: "recording" }[it.kind] || "no picture yet";
    return `<span class="${large ? "viewer-img " : ""}picture-none">${esc(kind)}${it.title ? `<br>${esc(it.title)}` : ""}</span>`;
  }
  return large
    ? `<img class="viewer-img" src="${esc(pictureSrc(it, true))}" alt="${esc(cap)}">`
    : `<img loading="lazy" src="${esc(pictureSrc(it, false))}" alt="${esc(cap)}">`;
}
// What a tile says under its picture: a figure's caption and where it is,
// a photo's day.
function pictureLine(it) {
  if (it.ref) {
    const where = it.where != null ? it.where : it.page ? `p. ${it.page}` : "";
    return { cap: it.caption || "", where };
  }
  return { cap: "", where: it.taken ? String(it.taken).slice(0, 10) : "" };
}
// One page of a picture set as a grid of tiles and its pager. ``page`` is
// {total, offset, limit, items}; a tile's data-i is its place in the set.
function galleryHtml(page, href, figures) {
  const tiles = page.items.map((it, i) => {
    const { cap, where } = pictureLine(it);
    const title = it.caption || it.title || "";
    return `<a class="gallery-tile" href="${esc(it.href || `#doc/${it.doc_id}`)}" data-i="${page.offset + i}" title="${esc(title)}">
      ${pictureOrWord(it, false, cap)}${figures ? `<span class="cap">${esc(cap) || "<span class=muted>(no caption)</span>"}</span>` : ""}${where ? `<span class="muted where">${esc(where)}</span>` : ""}</a>`;
  }).join("");
  return `<div class="gallery-grid${figures ? " figures" : ""}">${tiles}</div>${pagerHtml(page.total, page.offset, page.limit, href)}`;
}
// The place in a set the viewer steps to, or null past either end.
function viewerStep(index, delta, total) {
  const next = index + delta;
  return next >= 0 && next < total ? next : null;
}
// The viewer over a gallery: the picture large, what it is, and the way
// to its document (or, for a figure, its place in the text).
function viewerHtml(it, index, total) {
  const { cap, where } = pictureLine(it);
  const words = [it.title && !it.ref ? esc(it.title) : esc(cap), where ? esc(where) : ""].filter(Boolean).join(" · ");
  const open = it.ref ? "its place in the text" : "open the document";
  return `<div class="viewer-box" role="dialog" aria-modal="true" aria-label="picture ${index + 1} of ${total}">
    <button type="button" class="viewer-close secondary" data-viewer="close" aria-label="close">×</button>
    <button type="button" class="viewer-step prev secondary" data-viewer="prev" aria-label="previous"${index > 0 ? "" : " disabled"}>‹</button>
    ${pictureOrWord(it, true, cap)}
    <button type="button" class="viewer-step next secondary" data-viewer="next" aria-label="next"${index + 1 < total ? "" : " disabled"}>›</button>
    <p class="viewer-line"><span class="muted">${(index + 1).toLocaleString()} of ${total.toLocaleString()}</span>${words ? ` · ${words}` : ""}
      · <a href="${esc(it.href || `#doc/${it.doc_id}`)}" data-viewer="open">${open}</a></p></div>`;
}
// The set a #photos view names, and its heading.
const PICTURE_SETS = { depicts: "Photos of", event: "Photos at", place: "Photos in", candidate: "Photos of the group" };
function pictureSetOf(params) {
  for (const key of Object.keys(PICTURE_SETS)) {
    if (params && params[key] && /^\d+$/.test(params[key])) return { key, id: Number(params[key]) };
  }
  return null;
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
// What waits for the group's roles, by role and action (GET /up's
// demand.groups), each with its rate and a "do it now" (POST /work/now):
// the supervisor gives that role the card at its next look, unless an
// ask holds it (stage AI).
const DECISIONS = { serving: "being served", next: "next", waits: "waits" };
function planNote(p) {
  if (!p) return "";
  const swap = p.swap_s ? ` · a swap of about ${p.swap_s >= 90 ? `${Math.round(p.swap_s / 60)} min` : `${p.swap_s} s`}${p.swap_guessed ? " (guessed)" : ""}` : "";
  return ` · <span class="muted" title="the card's plan (GET /work/plan)">${esc(DECISIONS[p.decision] || p.decision)}: ${esc(p.why)}${swap}</span>`;
}
function waitingList(demand, roles, members, holder, plan) {
  const d = demand || {};
  const planned = ((plan || {}).groups || []);
  const rows = (d.groups || []).filter((g) => members.includes(g.role)).map((g) => {
    const p = planned.find((x) => x.role === g.role && x.action === g.action);
    const when = g.hours_left == null ? "" : g.hours_left >= 48 ? `${Math.round(g.hours_left / 24)} days` : `${Math.round(g.hours_left)} h`;
    const pace = g.rate ? ` · ${g.rate}/h · about ${when} left` : "";
    const state = (roles[g.role] || {}).state || "?";
    const load = (roles[g.role] || {}).load_s;
    const loads = load == null ? "" : `, loads in about ${load >= 90 ? `${Math.round(load / 60)} min` : `${Math.round(load)} s`}`;
    const card = g.role === holder || state === "up" ? "" : ` · needs the card (${esc(g.role)} is ${esc(state)}${loads})`;
    const act = g.now
      ? ` · <span class="up-holder">asked for now${d.ask_holds ? ", after the answer in progress" : ""}</span>`
      : ` <button type="button" class="linkish up-now" data-role="${esc(g.role)}" data-action="${esc(g.action)}">do it now</button>`;
    return `<li>${esc(g.role)}: ${g.waiting} ${esc(g.action)}${pace}${card}${planNote(p)}${act}</li>`;
  });
  return rows.length ? `<ul class="servers up-waiting">${rows.join("")}</ul>` : "";
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
      ${waitingList(u.demand, roles, members, holder, u.plan)}
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
// How one name becomes the other, letter by letter: what only the first
// has is struck through, what only the second has is marked, a space is
// shown as ␣ so a joined word can be seen. Two names with little spelling
// in common (a translation, an acronym) get none, since a diff of them is
// noise; their sides above already show both.
function nameDiff(a, b) {
  a = String(a || ""); b = String(b || "");
  if (a === b || a.length * b.length > 40000) return "";
  // the longest common subsequence, as a table of suffix lengths
  const n = a.length, m = b.length;
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) {
    L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  }
  if (L[0][0] < 0.5 * Math.min(n, m)) return "";
  const ops = []; // [kind, text], kind "=", "-" or "+"
  const put = (kind, ch) => {
    const last = ops[ops.length - 1];
    if (last && last[0] === kind) last[1] += ch; else ops.push([kind, ch]);
  };
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { put("=", a[i]); i++; j++; }
    else if (j < m && (i === n || L[i][j + 1] >= L[i + 1][j])) { put("+", b[j]); j++; }
    else { put("-", a[i]); i++; }
  }
  // a run of one or two letters kept between two changes reads as part of
  // them ("colour" → "color" is one change, not three)
  const merged = [];
  for (let x = 0; x < ops.length; x++) {
    const [kind, text] = ops[x];
    const between = kind === "=" && text.length <= 2 && x > 0 && x < ops.length - 1
      && /\S/.test(text) && merged.length && merged[merged.length - 1][0] !== "=";
    if (between) { merged.push(["-", text], ["+", text]); continue; }
    merged.push([kind, text]);
  }
  // the deletions of a change before its insertions
  const out = [];
  let del = "", ins = "";
  const flush = () => {
    if (del) out.push(`<del>${esc(del.replace(/ /g, "␣"))}</del>`);
    if (ins) out.push(`<ins>${esc(ins.replace(/ /g, "␣"))}</ins>`);
    del = ""; ins = "";
  };
  for (const [kind, text] of merged) {
    if (kind === "-") del += text;
    else if (kind === "+") ins += text;
    else { flush(); out.push(esc(text)); }
  }
  flush();
  return `<p class="name-diff" title="from the first name to the second: struck through is only in the first, marked only in the second">${out.join("")}</p>`;
}

function pairRow(it) {
  const k = it.keep, o = it.other;
  // the local model's calibrated probability, when it asked and left the pair
  const p = it.p_same == null ? "" : ` · model: ${Math.round(it.p_same * 100)}% same`;
  // a model said one thing, and the merge waits for a person: why
  const held = it.held ? `<p class="decide-held">The model said these are one thing; the merge waits for you (${esc(it.held.replace(/^held: /, ""))}).</p>` : "";
  return `<article class="decide-row${it.held ? " held" : ""}">${held}${nameDiff(k.name, o.name)}<div class="decide-pair">${entitySide(k)}${entitySide(o)}</div>
    <div class="decide-acts"><span class="muted">${esc(it.type)} · names ${it.score}${p}</span>
      <button type="button" data-act="same" data-keep="${k.id}" data-other="${o.id}">same, keep “${esc(k.name)}”</button>
      <button type="button" class="secondary" data-act="same" data-keep="${o.id}" data-other="${k.id}">same, keep “${esc(o.name)}”</button>
      <button type="button" class="secondary" data-act="different" data-keep="${k.id}" data-other="${o.id}">different</button>
      <span class="decide-out muted"></span></div></article>`;
}
// The media view (Admin, "media"): one level of a grouping of the
// referenced photos and recordings. A group's line: its name (a way down
// when it has groups under it), what it holds, how much of it is set
// aside, and the two answers for all of it.
const MEDIA_KINDS = { image: ["photo", "photos"], video: ["video", "videos"], audio: ["recording", "recordings"] };
function mediaKinds(kinds) {
  return Object.entries(kinds || {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => {
    const [one, many] = MEDIA_KINDS[k] || [k, k];
    return `${n.toLocaleString()} ${n === 1 ? one : many}`;
  }).join(" · ");
}
function mediaGroupRow(g, by, down) {
  const states = [
    g.suspected ? `${g.suspected.toLocaleString()} set aside` : "",
    g.open ? `${g.open.toLocaleString()} released` : "",
    g.personal ? `${g.personal.toLocaleString()} personal` : "",
  ].filter(Boolean).join(" · ");
  const name = down ? `<a href="${esc(down)}">${esc(g.label)}</a>` : `<b>${esc(g.label)}</b>`;
  const act = (state, label, cls) => `<button type="button"${cls ? ` class="${cls}"` : ""} data-media="${state}" data-by="${esc(by)}" data-key="${esc(g.key)}"${g.suspected ? "" : " disabled"}>${label}</button>`;
  return `<article class="decide-row media-row" data-key="${esc(g.key)}">
    <div class="media-head">${name} <span class="muted">${esc(mediaKinds(g.kinds))}</span>
      <a href="#" class="media-show" data-key="${esc(g.key)}">show</a></div>
    <div class="decide-acts"><span class="muted">${esc(states)}</span>
      ${act("open", "release")} ${act("personal", "personal", "secondary")}
      <span class="decide-out muted"></span></div>
    <div class="media-gallery"></div></article>`;
}
// The way back up a folder or a year: each step above ``under`` a link.
function mediaCrumbs(by, under, href) {
  if (!under) return "";
  if (by === "month") return `<a href="${esc(href(""))}">all years</a> › <b>${esc(under)}</b>`;
  const [root, path] = [under.split(":")[0], under.slice(under.indexOf(":") + 1)];
  const parts = path ? path.split("/") : [];
  const steps = [`<a href="${esc(href(""))}">${esc(root)}</a>`];
  parts.forEach((p, i) => {
    const key = `${root}:${parts.slice(0, i + 1).join("/")}`;
    steps.push(i === parts.length - 1 ? `<b>${esc(p)}</b>` : `<a href="${esc(href(key))}">${esc(p)}</a>`);
  });
  return steps.join(" › ");
}
// One of another library's things whose name one here answers to
// (GET /graph/imports/likely): theirs beside ours, and three answers.
function foreignRow(it) {
  const c = it.candidate;
  const labels = (it.labels || []).map((l) => l.label).filter(Boolean);
  const theirs = `<div class="decide-side">${esc(it.name)}
    <span class="muted">${esc(it.type)} · from ${esc(it.source)} · ${it.held} ${it.held === 1 ? "fact waits" : "facts wait"}</span>
    ${labels.length ? `<div class="muted">also: ${esc(labels.join(", "))}</div>` : ""}</div>`;
  const act = (verdict, label, cls) => `<button type="button"${cls ? ` class="${cls}"` : ""} data-act="foreign" data-source="${esc(it.source)}" data-gid="${esc(it.gid)}" data-verdict="${verdict}">${label}</button>`;
  return `<article class="decide-row">${c ? nameDiff(it.name, c.name) : ""}<div class="decide-pair">${theirs}${entitySide(c)}</div>
    <div class="decide-acts">${act("same", "same thing")} ${act("new", "a thing of its own", "secondary")} ${act("left_out", "leave out", "secondary")}
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
// Two documents that are probably versions of one work (GET /graph/versions,
// plan item AS): each side's title, date, length and top-level headings, a
// heading only one side has marked, how much text they share, and why they
// were paired. The suggested answer is the first button.
const VERSION_ANSWERS = { version: "versions of one work", copy: "one text twice", different: "different" };
function versionSide(s, other) {
  const theirs = new Set(other.headings || []);
  const heads = (s.headings || []).map((h) => theirs.has(h) ? esc(h) : `<mark>${esc(h)}</mark>`);
  const when = s.published ? `published ${esc(s.published)}` : `added ${esc((s.added || "").slice(0, 10))}`;
  return `<div><h4><a href="#doc/${s.doc_id}">${esc(s.title || `#${s.doc_id}`)}</a></h4>
    <p class="muted">#${s.doc_id} · ${when} · ${Number(s.chars || 0).toLocaleString()} characters${s.retired ? " · retired" : ""}</p>
    ${heads.length ? `<p class="version-heads">${heads.join(" · ")}</p>` : '<p class="muted">no headings</p>'}</div>`;
}
function versionRow(it) {
  const r = it.reasons || {};
  const why = [`${Math.round(it.overlap * 100)}% of their text alike`,
    r.by === "arxiv" ? "one arXiv id" : "one title",
    (r.authors || []).length ? `authors ${r.authors.map(esc).join(", ")}` : ""].filter(Boolean);
  const order = [r.suggest || "version", ...Object.keys(VERSION_ANSWERS).filter((k) => k !== (r.suggest || "version"))];
  const acts = it.verdict
    ? `<span><b>${esc(VERSION_ANSWERS[it.verdict] || it.verdict)}</b></span>
        <button type="button" class="secondary" data-version-undo>undo</button>`
    : order.map((k, i) => `<button type="button"${i ? ' class="secondary"' : ""} data-verdict="${k}">${VERSION_ANSWERS[k]}</button>`).join(" ");
  return `<article class="decide-row version-row" data-candidate="${it.id}">
    <p class="muted">${why.join(" · ")}</p>
    <div class="decide-pair">${versionSide(it.a, it.b)}${versionSide(it.b, it.a)}</div>
    <div class="decide-acts">${acts}<span class="decide-out muted"></span></div></article>`;
}
// A group of photos taken close together (GET /graph/events): its days,
// how many, where and from which folder, a strip of its thumbnails, and
// the name it would be given, which a person may change before saying
// it is an event. A decided one shows its name and the undo.
function eventDays(first, last) {
  const a = (first || "").slice(0, 10), b = (last || "").slice(0, 10);
  return a === b ? a : `${a} – ${b}`;
}
function eventRow(it) {
  const cues = it.cues || {};
  const bits = [eventDays(it.first, it.last), `${it.photos.toLocaleString()} photos`,
    cues.where ? esc(cues.where) : (cues.placed ? "" : "no position in them"),
    cues.folder ? `folder “${esc(cues.folder)}”` : ""].filter(Boolean);
  const thumbs = (it.sample || []).map((d) =>
    `<a href="#doc/${d}"><img loading="lazy" src="/doc/${d}/thumb" alt=""></a>`).join("");
  const acts = it.state === "open"
    ? `<form class="event-form"><input name="name" value="${esc(it.suggestion)}" aria-label="the event's name" maxlength="120">
        <button type="submit">an event</button>
        <button type="button" class="secondary" data-event-not>not one</button>
        <button type="button" class="secondary" data-event-split>split…</button>
        ${cues.cut ? `<button type="button" class="secondary" data-event-join>join with the one before</button>` : ""}
        <span class="decide-out muted"></span></form><div class="event-timeline"></div>`
    : `<div class="decide-acts"><span>${it.state === "event" ? `an event: <b>${esc(it.name)}</b>` : "not an event"}</span>
        <button type="button" class="secondary" data-event-undo>undo</button>
        <span class="decide-out muted"></span></div>`;
  return `<article class="decide-row event-row" data-candidate="${it.id}">
    <p class="muted">${bits.join(" · ")} · <a href="#photos?candidate=${it.id}">all its photos</a></p><div class="event-thumbs">${thumbs}</div>${acts}</article>`;
}
// A burst of a group's photos (store.event_timeline): its day and hours,
// the second day only when it runs past midnight.
function burstLabel(b) {
  const a = b.first || "", z = b.last || "";
  const end = a.slice(0, 10) === z.slice(0, 10) ? z.slice(11, 16) : `${z.slice(5, 10)} ${z.slice(11, 16)}`;
  return `${a.slice(0, 10)} ${a.slice(11, 16)} – ${end}`;
}
// The bursts of a group, each with its thumbnails and, but for the
// first, a cut before it.
function timelineRows(t) {
  return (t.bursts || []).map((b, i) => `<div class="event-burst">
      <p class="muted">${i ? `<button type="button" class="secondary" data-cut="${esc(b.first)}">split here</button> ` : ""}${esc(burstLabel(b))} · ${b.photos.toLocaleString()} photo${b.photos === 1 ? "" : "s"}</p>
      <div class="event-thumbs">${(b.sample || []).map((d) => `<a href="#doc/${d}"><img loading="lazy" src="/doc/${d}/thumb" alt=""></a>`).join("")}</div></div>`).join("")
    || `<p class="muted">no dated photos in it</p>`;
}
// What the faces step found in a picture (meta.faces): how many, or that
// it looked and found none
function facesWords(f) {
  if (!f) return "";
  return f.n ? `${f.n} face${f.n === 1 ? "" : "s"}` : "no face";
}
// A face as a box over its picture: its place in percent of the picture,
// who it was named as and its score as the title, and with ``n`` its
// number in the list under the picture
function faceBox(f, n) {
  const [x, y, w, h] = f.box.map((v) => (v * 100).toFixed(2));
  const who = f.answer && f.answer.verdict === "person" ? `${esc(f.answer.name)} · ` : "";
  const num = n == null ? "" : `<span class="face-num">${n}</span>`;
  return `<span class="face-box${f.answer ? " face-said" : ""}" data-face="${f.id}" style="left:${x}%;top:${y}%;width:${w}%;height:${h}%" title="${who}face ${f.id} · ${Math.round(f.score * 100)}%">${num}</span>`;
}
// One face of a picture in the list under it (view-doc.js): its number on
// the box, its crop, and its answer with an undo, or a field to name it,
// holding the matching's offer when there is one. A name not picked from
// the list makes a new person
function pictureFaceRow(f, n) {
  const img = f.crop ? `<img loading="lazy" src="/face/${f.id}/crop" alt="">` : `<span class="face-none">no crop yet</span>`;
  const a = f.answer;
  let said;
  if (a) {
    const what = a.verdict === "person" ? `<a href="#person/${a.entity_id}">${esc(a.name)}</a>`
      : a.verdict === "nobody" ? "nobody to name" : "not a face";
    said = `${what} · <a href="#" data-unname="${f.id}">undo</a>`;
  } else {
    const offer = f.offer ? `<span class="muted">probably <a href="#person/${f.offer.entity_id}">${esc(f.offer.name)}</a>, ${Math.round(f.offer.similarity * 100)}% alike</span><br>` : "";
    said = `${offer}<form class="face-name"${f.offer ? ` data-offer="${f.offer.entity_id}"` : ""}>
      <span class="person-field"><input name="who" placeholder="who is this? a new name makes a person" autocomplete="off" value="${f.offer ? esc(personValue(f.offer)) : ""}"><span class="person-choices" hidden></span></span>
      <button name="act" value="name">name</button> <button type="button" value="not_face">not a face</button> <button type="button" value="nobody">nobody to name</button></form>`;
  }
  return `<li class="picture-face" data-face="${f.id}"><span class="face-num">${n}</span>${img}<div>${said} <span class="msg"></span></div></li>`;
}
// The faces tab's counts (store.faces_page): how many, in how many
// pictures, how many have a vector, by score and by size
function faceCounts(res) {
  const bands = (o) => Object.entries(o || {}).map(([k, n]) => `${esc(k)} ${Number(n).toLocaleString()}`).join(" · ");
  return `${res.total.toLocaleString()} face${res.total === 1 ? "" : "s"} in ${res.pictures.toLocaleString()} picture${res.pictures === 1 ? "" : "s"}, ${res.vectored.toLocaleString()} with a vector<br>score: ${bands(res.by_score)}<br>size: ${bands(res.by_size)}`;
}
// One face as a tile: its crop, its picture a click away, its score and
// size, or its likeness to the face asked about
function faceTile(f, near, pick) {
  const img = f.crop ? `<img loading="lazy" src="/face/${f.id}/crop" alt="">` : `<span class="face-none">no crop yet</span>`;
  if (pick) {
    // a face to answer for: the crop toggles it, its picture is a link
    const alike = f.similarity != null ? `${Math.round(f.similarity * 100)}% alike · ` : "";
    return `<figure class="face-tile" data-face="${f.id}"><button type="button" class="face-pick" title="click: not them">${img}</button><figcaption class="muted">${alike}<a href="#doc/${f.doc_id}">picture</a> · ${f.px} px
      · <a href="#" data-alike="${f.id}" data-doc="${f.doc_id}" data-score="${f.score}" data-px="${f.px}">alike</a></figcaption></figure>`;
  }
  // a likeness as a number only: OpenCV's line for one pair (MATCH) is
  // passed by 99% of a library's faces' nearest (2026-10-27), so a mark
  // at it would say nothing; step 5 measures a line of the library's own
  const line = near ? `${Math.round(near.similarity * 100)}% alike` : `${Math.round((f.score || 0) * 100)}% · ${f.px} px`;
  return `<figure class="face-tile"><a href="#doc/${f.doc_id}">${img}</a><figcaption class="muted">${line}
    ${near ? "" : ` · <a href="#" data-alike="${f.id}" data-doc="${f.doc_id}" data-score="${f.score}" data-px="${f.px}">alike</a>`}</figcaption></figure>`;
}
// One face group (AT step 3) as a strip of its firmest faces: its size,
// its pictures, and a warning where a picture holds two of its faces
// AT steps 3 and 4: a click on a face marks it "not them", the form
// answers for the rest, and what its faces were named so far is beside it
function faceGroupRow(g, all) {
  const plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;
  // faces in no group share pictures as a matter of course
  const doubled = g.id != null && g.doubled.length
    ? ` · <span class="error">${plural(g.doubled.length, "picture")} with two of its faces: more than one person</span>`
    : "";
  const open = g.open ?? g.size;
  const more = !all && g.id != null && open > g.faces.length
    ? ` · <a href="#review?${new URLSearchParams({ list: "groups", group: g.id })}">all ${open.toLocaleString()} to name</a>`
    : "";
  const named = (g.named || []).length
    ? `<br><span class="muted">named so far: ${g.named.map((w) => `<a href="#person/${w.entity_id}">${esc(w.name)}</a> ${w.faces}`).join(", ")}</span>`
    : "";
  const title = g.id == null ? "not in a group" : `group ${g.id}`;
  const suggest = (g.named || [])[0];
  const tiles = g.faces.filter((f) => !all || f.verdict == null);
  const form = tiles.length ? `<form class="face-name" data-faces="${tiles.map((f) => f.id).join(",")}">
      <span class="person-field"><input name="who" placeholder="who is this?" autocomplete="off" value="${suggest ? esc(personValue(suggest)) : ""}"><span class="person-choices" hidden></span></span>
      <button name="act" value="name">name them</button> <button name="act" value="not_face" type="button">not faces</button>
      <button name="act" value="nobody" type="button">nobody to name</button> <span class="msg"></span></form>` : "";
  return `<section class="face-group"><p>${title} · ${plural(open, "face")} to name in ${plural(g.pictures, "picture")}${doubled}${more}${named}</p>
    ${form}<div class="face-grid">${tiles.map((f) => faceTile(f, null, true)).join("")}</div></section>`;
}
// AT step 5: the open faces that are probably one person already named,
// beside a face of theirs. The field holds them; a face clicked away is
// "not them" once the rest are named, and never offered as them again
function faceMatchRow(m, all) {
  const plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;
  const more = !all && m.open > m.faces.length
    ? ` · <a href="#review?${new URLSearchParams({ list: "matches", person: m.entity_id })}">all ${m.open.toLocaleString()}</a>`
    : "";
  const like = m.like ? `<figure class="face-tile face-like"><a href="#doc/${m.like.doc_id}">${m.like.crop ? `<img loading="lazy" src="/face/${m.like.id}/crop" alt="">` : `<span class="face-none">no crop yet</span>`}</a><figcaption class="muted">named</figcaption></figure>` : "";
  const form = m.faces.length ? `<form class="face-name">
      <span class="person-field"><input name="who" placeholder="who is this?" autocomplete="off" value="${esc(personValue(m))}"><span class="person-choices" hidden></span></span>
      <button name="act" value="name">name them</button> <button name="act" value="refuse" type="button">not ${esc(m.name)}</button>
      <button name="act" value="not_face" type="button">not faces</button>
      <button name="act" value="nobody" type="button">nobody to name</button> <span class="msg"></span></form>` : "";
  return `<section class="face-group" data-person="${m.entity_id}"><p>probably <a href="#person/${m.entity_id}">${esc(m.name)}</a> · ${plural(m.open, "face")} in ${plural(m.pictures, "picture")}${more}</p>
    ${form}<div class="face-grid">${like}${m.faces.map((f) => faceTile(f, null, true)).join("")}</div></section>`;
}
// A person in the name field: their name and id, which personChoice reads
// back; anything else typed there is a new person's name
function personValue(p) {
  return `${p.name} #${p.entity_id ?? p.id}`;
}
// The people the name field offers, as buttons of the page's own: a
// browser's datalist is not drawn in Firefox on a field with autocomplete
// off, nor redrawn when its options arrive after the keystroke
function personChoices(rows) {
  return rows.map((r) => `<button type="button" class="person-choice" data-value="${esc(personValue(r))}">${esc(r.name)} <span class="muted">${esc(r.type)}${r.photos ? ` · ${r.photos} photo${r.photos === 1 ? "" : "s"}` : ""}${r.facts ? ` · ${r.facts} fact${r.facts === 1 ? "" : "s"}` : ""}</span></button>`).join("");
}
function personChoice(text) {
  const t = String(text || "").trim();
  const m = t.match(/#(\d+)$/);
  if (m) return { entity_id: Number(m[1]) };
  return t ? { name: t } : null;
}
// What the person page changes (view-people.js): their name, the old one
// kept as "also known as", and another entity that is them too, folded
// into them. Both shown on a click, each answered with an undo
function personEditHtml(got) {
  const name = esc(got.name);
  return `<p class="person-edit muted"><a href="#" data-edit="rename">rename</a> · <a href="#" data-edit="same">same person as…</a> <span class="msg"></span></p>
    <form class="person-rename search-form" hidden autocomplete="off"><input name="name" value="${name}" aria-label="the new name">
      <button>rename</button> <span class="muted">${name} stays a name they are also known by</span></form>
    <form class="person-same search-form" hidden><span class="person-field"><input name="who" placeholder="the other person, picked from the list" autocomplete="off"><span class="person-choices" hidden></span></span>
      <button>same person</button> <span class="muted">their faces, pictures and facts join ${name}'s, and ${name} stays the name</span></form>`;
}
// The people list (view-people.js): a row a person, linked to their page.
function peopleTable(rows) {
  return `<table class="people"><thead><tr><th>name</th><th>type</th><th>facts</th><th>photos</th></tr></thead><tbody>${rows.map((r) =>
    `<tr><td><a href="#person/${r.id}">${esc(r.name)}</a></td><td class="muted">${esc(r.type)}</td><td>${r.facts.toLocaleString()}</td><td>${r.photos ? r.photos.toLocaleString() : ""}</td></tr>`).join("")}</tbody></table>`;
}
// The other end of a person's fact: another person's page, else the graph.
function personOther(f) {
  const href = f.person ? `#person/${f.other_id}` : `#graph?${new URLSearchParams({ entity: f.other, type: f.other_type })}`;
  return `<a href="${href}">${esc(f.other)}</a> <span class="muted">${esc(f.other_type)}</span>`;
}
// A relation read from the person's side: "Ada affiliated with …" for
// what they are, "authored by Ada" for what points at them.
function personRelation(rel, dir, name) {
  const words = esc(String(rel).replace(/_/g, " "));
  return dir === "out" ? `${esc(name)} ${words}` : `${words} ${esc(name)}`;
}
// One person's page (store.person_page): who they are, the pictures and
// what they say, the dated facts, the rest by relation, the documents.
function personHtml(got) {
  const name = got.name;
  const doc = (f) => f.doc ? ` <a class="muted" href="#doc/${f.doc}">doc ${f.doc}</a>` : "";
  const known = (got.labels || []).map((l) => esc(l.label)).join(", ");
  const head = `<h1>${esc(name)}</h1><p class="muted">${esc(got.type)} · ${got.facts.toLocaleString()} facts · in ${got.documents_total.toLocaleString()} documents${got.photos ? ` · in ${got.photos.toLocaleString()} photos` : ""}
    · <a href="#graph?${new URLSearchParams({ entity: name, type: got.type })}">in the graph</a></p>${known ? `<p class="muted">Also known as ${known}</p>` : ""}`;
  const counted = (items, link) => items.map((x) => `<li>${link(x)} <span class="muted">· ${x.photos} photo${x.photos === 1 ? "" : "s"}</span></li>`).join("");
  const photos = got.photos
    ? `<section><h2>In photos</h2><div id="person-photos" class="gallery"></div>
      ${got.events.length ? `<h3>At</h3><ul>${counted(got.events, (e) => `<a href="#graph?${new URLSearchParams({ entity: e.name, type: "event" })}">${esc(e.name)}</a>${e.first ? ` <span class="muted">${esc(eventDays(e.first, e.last))}</span>` : ""} <a class="muted" href="#photos?event=${e.id}">its photos</a>`)}</ul>` : ""}
      ${got.places.length ? `<h3>Where</h3><ul>${counted(got.places, (x) => x.id ? `<a href="#photos?place=${x.id}">${esc(x.name)}</a>` : esc(x.name))}</ul>` : ""}
      ${got.with.length ? `<h3>With</h3><ul>${counted(got.with, (x) => `<a href="#person/${x.id}">${esc(x.name)}</a>`)}</ul>` : ""}</section>`
    : `<p class="muted">No photo depicts them yet: the faces step (plan item AT) names the people in pictures.</p>`;
  const dated = got.timeline.length
    ? `<section><h2>In time</h2><ul class="person-time">${got.timeline.map((f) => `<li><span class="when">${esc(f.world_from || "")}${f.world_to ? ` – ${esc(f.world_to)}` : ""}</span>
        ${personRelation(f.rel, f.dir, name)} ${personOther(f)}${doc(f)}</li>`).join("")}${(got.timeline_total || 0) > got.timeline.length ? `<li class="muted">and ${(got.timeline_total - got.timeline.length).toLocaleString()} more</li>` : ""}</ul></section>`
    : "";
  const rels = got.relations.map((r) => `<h3>${personRelation(r.rel, r.dir, name)} <span class="muted">${r.count.toLocaleString()}</span></h3>
      <ul>${r.facts.map((f) => `<li>${personOther(f)}${doc(f)}</li>`).join("")}${r.count > r.facts.length ? `<li class="muted">and ${(r.count - r.facts.length).toLocaleString()} more</li>` : ""}</ul>`).join("");
  const docs = got.documents.length
    ? `<section><h2>Documents</h2><ul>${got.documents.map((d) => `<li><a href="#doc/${d.id}">${esc(d.title || `doc ${d.id}`)}</a>${d.published ? ` <span class="muted">${esc(d.published)}</span>` : ""}
        <span class="muted">· ${d.facts} fact${d.facts === 1 ? "" : "s"}</span></li>`).join("")}${got.documents_total > got.documents.length ? `<li class="muted">and ${(got.documents_total - got.documents.length).toLocaleString()} more</li>` : ""}</ul></section>`
    : "";
  return `<article class="person">${head}${photos}${dated}${rels ? `<section><h2>Facts</h2>${rels}</section>` : ""}${docs}</article>`;
}
// A document the personal-document rules suspect: what it is, where it
// came from, and the cues (the owner's name is only ever "name").
function suspectRow(it) {
  const cues = (it.cues || []).map((c) => esc(c.replace(/^(strong|weak): /, ""))).join(" · ");
  const marked = it.state === "personal";
  return `<article class="decide-row"><div class="decide-pair"><div class="decide-side">
      <a href="#doc/${it.id}">${esc(it.title || `doc ${it.id}`)}</a>
      <span class="muted">${esc(it.mime || "")}${it.added_at ? ` · ${esc(String(it.added_at).slice(0, 10))}` : ""}</span>
      ${it.path ? `<div class="muted">${esc(it.path)}</div>` : ""}</div></div>
    <div class="decide-acts"><span class="muted">${marked ? `marked ${esc(String(it.decided || "").slice(0, 10))}${cues ? " · " : ""}` : ""}${cues || (marked ? "" : "no cue")}</span>
      ${marked ? "" : `<button type="button" data-act="personal" data-doc="${it.id}">personal</button>`}
      <button type="button" class="secondary" data-act="open" data-doc="${it.id}">not personal</button>
      <span class="decide-out muted"></span></div></article>`;
}
// The Review page's "genre" tab (stage Z): a document as a labeller reads
// it (title, where it came from, summary, the opening of its text) and the
// vocabularies as checkboxes, a level before the labels under it: what it
// is (ontology/genres.yaml) and what it is about (ontology/subjects.yaml).
// A document may take several of each; "can't tell" takes it out of the
// sample. The checked boxes are its labels so far, when it has some.
function genreRow(it, vocab) {
  const model = it.genres_by && it.genres_by !== "human";
  const sure = (name) => {
    const p = model ? (it.p || {})[name] : null;
    return p == null ? "" : ` <span class="genre-p${p < 0.7 ? " unsure" : ""}">${Math.round(p * 100)}%</span>`;
  };
  const facet = (f, field, have) => {
    const box = (name, about, level) => `<label class="genre-choice${level ? " genre-level" : ""}" title="${esc(about || "")}">
      <input type="checkbox" name="${field}" value="${esc(name)}"${have.has(name) ? " checked" : ""}> ${esc(name)}${have.has(name) ? sure(name) : ""}</label>`;
    return ((f || {}).levels || []).map((lv) => `<div class="genre-levels">${box(lv.name, lv.description, true)}
      <span class="genre-kids">${(lv.genres || []).map((g) => box(g.name, g.description, false)).join("")}</span></div>`).join("");
  };
  const levels = `<h4 class="genre-facet">what it is</h4>${facet(vocab, "genre", new Set(it.genres || []))}
    ${vocab.subjects ? `<h4 class="genre-facet">what it is about</h4>${facet(vocab.subjects, "subject", new Set(it.subjects || []))}` : ""}`;
  const opening = String(it.opening || "").trim();
  const where = it.where ? ` · ${esc(it.where)}` : "";
  return `<article class="decide-row genre-row" data-doc="${it.id}">
    <div class="decide-side"><a href="#doc/${it.id}">${esc(it.title || `doc ${it.id}`)}</a>
      <span class="muted genre-from">${esc(it.source || "")}${it.lang ? ` · ${esc(it.lang)}` : ""} · ${esc(it.mime || "")}${where}</span>
      ${it.summary ? `<p>${esc(it.summary)}</p>` : `<p class="muted">No summary yet.</p>`}
      ${model ? `<p class="genre-model">Labelled by ${esc(it.genres_by)}${it.note ? `: ${esc(it.note)}` : ""}. Save if it is right, or change the ticks first.</p>` : ""}
      ${opening ? `<details><summary>the opening of the text</summary><pre class="genre-opening">${esc(opening)}</pre></details>` : ""}</div>
    <form class="genre-form">${levels}
      <div class="decide-acts"><button type="submit">save</button>
        <button type="button" class="secondary" data-genre-skip="1">can't tell</button>
        <span class="decide-out muted">${it.genres_by === "human" ? `labelled ${esc(String(it.genres_at || "").slice(0, 10))}` : ""}</span></div></form></article>`;
}
// The clean-up tab (view-cleanup in view-decide.js): the rules as links,
// a folder of one's own, what a rule would take, and what was taken.
function cleanupRules(rules, current, folder) {
  const link = (key, about) => key === current
    ? `<b>${esc(about)}</b>`
    : `<a href="#admin?${new URLSearchParams({ tab: "cleanup", rule: key })}">${esc(about)}</a>`;
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
// The admin page (view-admin.js): the named tokens, the form that makes
// one, the secret shown once, and the personal-document rules in force.
function tokensTable(res) {
  const tokens = (res && res.tokens) || [];
  const roles = (res && res.roles) || ["reader", "contributor"];
  const roleSelect = (t) => `<select data-role="${esc(t.name)}">${roles.map((r) => `<option${r === t.role ? " selected" : ""}>${esc(r)}</option>`).join("")}</select>`;
  const rows = tokens.map((t) => `<tr><td><b>${esc(t.name)}</b></td>
    <td>${roleSelect(t)}</td>
    <td>${t.domains ? t.domains.map(esc).join(", ") : '<span class="muted">every module</span>'}</td>
    <td>${t.personal ? "sees personal" : '<span class="muted">no personal</span>'}</td>
    <td class="muted">${esc(String(t.created_at || "").slice(0, 10))}</td>
    <td class="muted">${t.last_used ? esc(String(t.last_used).slice(0, 16).replace("T", " ")) : "never"}</td>
    <td><button type="button" class="secondary" data-revoke="${esc(t.name)}">revoke</button></td></tr>`).join("");
  const table = tokens.length
    ? `<table class="entities"><thead><tr><th>name</th><th>role</th><th>modules</th><th>personal</th><th>made</th><th>last used</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
    : `<p class="muted">No named tokens yet.</p>`;
  const modules = ((res && res.modules) || []).map((m) =>
    `<label><input type="checkbox" name="domain" value="${esc(m)}"> ${esc(m)}</label>`).join(" ");
  return `${table}
    <form id="token-form-add" class="search-form" autocomplete="off">
      <label for="token-name">A new token</label>
      <input id="token-name" name="name" placeholder="laptop" pattern="[a-z0-9][a-z0-9_-]{0,39}" required>
      <div class="muted">${modules} <span>(none ticked: every module)</span></div>
      <label><input type="checkbox" name="personal"> may see personal documents</label>
      <label>role <select name="role">${roles.map((r) => `<option>${esc(r)}</option>`).join("")}</select></label>
      <div class="muted">a reader reads, and asks with no paid model; a contributor also adds documents, writes pages and links, renames and asks for readings</div>
      <button>make it</button> <span class="error msg"></span></form>`;
}
function tokenSecret(name, secret) {
  return `<article class="decide-row"><b>${esc(name)}</b>: its secret, shown this once and kept nowhere.
    Copy it into the client that uses it (PRAX_TOKEN there).
    <pre>${esc(secret)}</pre></article>`;
}
function privateRules(r) {
  if (!r) return "";
  const d = r.documents || {};
  const added = r.added || {};
  const extra = [
    added.names ? `${added.names} name${added.names === 1 ? "" : "s"} (a weak cue each)` : "",
    ...(added.paths || []).map((x) => `everything under ${esc(x)}`),
    ...(added.strong || []).map((x) => `strong: ${esc(x)}`),
    ...(added.weak || []).map((x) => `weak: ${esc(x)}`),
  ].filter(Boolean);
  return `<details class="same-rule"><summary>The rules: ${d.suspected || 0} suspected, ${d.personal || 0} personal, ${d.released || 0} released by you${d.named_faces ? `, ${d.named_faces} hidden for a named face` : ""}</summary>
    <p class="muted">A strong word marks a document alone; weak ones count only ${r.weak_needed} together (words that say one thing count once); a path marks everything under it. The rules read the title, the paths and the first ${r.head} characters, and never overrule your answer. Edit them in prax.yaml, under private:.</p>
    <div class="decide-pair"><div><h4>Strong</h4><p>${(r.strong || []).map(esc).join(", ")}</p></div>
    <div><h4>Weak</h4><p>${(r.weak || []).map((g) => g.map(esc).join(" / ")).join("; ")}</p></div></div>
    <h4>From prax.yaml</h4>${extra.length ? `<ul>${extra.map((x) => `<li>${x}</li>`).join("")}</ul>` : '<p class="muted">nothing added</p>'}</details>`;
}
// Above the search hits: the region of the library most of them live in
// (GET /search?regions=true), and its part when that is as clear.
function regionLine(where) {
  if (!where || !where.region) return "";
  const link = (c) => `<a href="#graph?community=${c.id}">${esc(c.label)}</a>`;
  const part = where.part ? ` › ${link(where.part)}` : "";
  const about = where.region.summary ? `<div class="muted">${esc(where.region.summary)}</div>` : "";
  return `<p class="region-line"><span class="muted">mostly in</span> ${link(where.region)}${part}
    <span class="muted">· ${Math.round(where.region.share * 100)}% of what the first hits are about</span>${about}</p>`;
}
// The document page's "properties…" dialog (view-doc.js): where a
// document came from, what it is, where it belongs and who may see it,
// with the administrative changes beside what they change.
// A document's genres or subjects for the properties dialog: the labels
// under a level (a level shows only when nothing under it was given), a
// model's with its probability, and who gave them. ``levels`` is the set
// of level and group names from GET /genres; without it every label shows.
function labelList(items, key, m, levels) {
  if (!items || !items.length) return "";
  const lv = levels || new Set();
  const leaves = items.filter((x) => !lv.has(x[key]));
  const shown = leaves.length ? leaves : items;
  const byModel = m.genres_by && m.genres_by !== "human";
  const one = (x) => `${esc(x[key])}${byModel && x.p != null ? ` <span class="muted">${Math.round(x.p * 100)}%</span>` : ""}`;
  const who = m.genres_by === "human" ? "you" : esc(m.genres_by || "?");
  return `${shown.map(one).join(", ")} <span class="muted">(${who})</span>`;
}
function propertiesHtml(doc, levels) {
  const m = doc.meta || {};
  const cap = m.capture || {};
  const origin = m.origin || {};
  const zot = m.zotero || {};
  const sens = m.sensitivity || {};
  const ext = m.extraction || {};
  const when = (s) => (s ? esc(String(s).slice(0, 16).replace("T", " ")) : "");
  const row = (k, v) => (v || v === 0 ? `<tr><th>${esc(k)}</th><td>${v}</td></tr>` : "");
  const list = (xs) => (xs && xs.length ? xs.map(esc).join(", ") : "");
  const state = doc.sensitivity || "open";
  const stateWord = { personal: "personal", suspected: "suspected personal", open: "open" }[state] || esc(state);
  const decided = sens.at ? `, by ${sens.by === "human" ? "you" : esc(sens.by || "?")} on ${when(sens.at)}` : "";
  const cues = ((m.private || {}).cues || []).map((c) => esc(c.replace(/^(strong|weak): /, ""))).join(" · ");
  const buttons = [
    state !== "personal" ? `<button type="button" data-props="personal">personal</button>` : "",
    state !== "open" ? `<button type="button" class="secondary" data-props="open">not personal</button>` : "",
  ].join(" ");
  const section = (title, rows) => (rows.trim() ? `<section class="route-group"><h3>${title}</h3><table class="props">${rows}</table></section>` : "");
  return `<h2>Properties</h2>
  <p class="muted process-title">${esc(doc.title || "(untitled)")} · doc ${doc.id}</p>
  ${section("Where it came from", [
    row("source", esc(m.source || "")),
    row("sent by", cap.by ? `${esc(cap.by)}${cap.at ? ` on ${when(cap.at)}` : ""}` : ""),
    row("from", origin.path ? `${esc(origin.host || "")}${origin.host ? ":" : ""}${esc(origin.path)}` : ""),
    row("file", esc(doc.original_path || "")),
    row("address", doc.source_url ? `<a href="${esc(doc.source_url)}" target="_blank" rel="noopener">${esc(doc.source_url)}</a>` : ""),
    row("Zotero", list(zot.keys)),
    row("added", when(doc.added_at)),
  ].join(""))}
  ${section("What it is", [
    row("genres", labelList(m.genres, "genre", m, levels)),
    row("about", labelList(m.subjects, "subject", m, levels)),
    row("type", esc(doc.mime || "")),
    row("pages", m.pages),
    row("language", esc(m.lang || "")),
    row("text", m.text_source ? `${esc(m.text_source)}, ${(doc.text_len || 0).toLocaleString()} characters, read ${when(doc.parsed_at)}` : ""),
    row("graph", ext.extractor ? `read by ${esc(ext.extractor)} against ${esc(ext.ontology_version || "?")}${ext.at ? ` on ${when(ext.at)}` : ""}` : ""),
    row("original", doc.hash ? `<code>${esc(doc.hash.slice(0, 16))}…</code>` : ""),
  ].join(""))}
  ${section("Where it belongs", [
    row("domains", m.domains ? `${list(m.domains)}${m.domains_by ? ` <span class="muted">(${esc(m.domains_by)})</span>` : ""}` : "every module"),
    row("tags", list(m.tags)),
    row("collections", list(m.collections)),
  ].join(""))}
  <section class="route-group"><h3>Who may see it</h3>
    <p>${stateWord}${decided}. ${state === "open" ? "Every token that may read its domains sees it." : "Only the administrator and tokens that may see personal documents see it."}</p>
    ${cues ? `<p class="muted">what the rules found: ${cues}</p>` : ""}
    <p class="props-acts">${buttons} <span class="props-out muted"></span></p>
  </section>
  ${m.retired ? `<p class="error">retired on ${when(m.retired.at)}: ${esc(m.retired.reason || "")}</p>` : ""}
  <div class="dialog-actions"><button type="button" class="secondary props-close">Close</button></div>`;
}
function mergeRow(it) {
  const a = it.alias, into = it.into;
  if (!a || !into) return "";
  return `<article class="decide-row">${nameDiff(a.name, into.name)}<div class="decide-pair">${entitySide(a)}<div class="decide-arrow muted">folded into</div>${entitySide(into)}</div>
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

// When a document was published, as its source says (meta.published:
// date, precision, by), with what said so on hover; a record's own date
// (meta.date) when the published pass has not run yet.
const PUBLISHED_BY = { human: "set by hand", record: "its record (Zotero)", paper: "the paper's page", citation: "the page's citation tags", jsonld: "the page's schema.org markup", arxiv: "its arXiv id", generic: "the page's article tags", "first-page": "its first page, read by a model" };
function publishedLabel(meta) {
  const p = (meta || {}).published;
  if (p && p.date) {
    const said = `published, as ${PUBLISHED_BY[p.by] || p.by || "?"} says` + (p.words ? `: “${p.words}”` : "");
    const unsure = p.confidence === "medium" ? " (a bare date on the page, not said to be its publication)" : "";
    return `<span title="${esc(said + unsure)}">${esc(p.date)}${unsure ? "?" : ""}</span>`;
  }
  return meta && meta.date ? esc(String(meta.date)) : "";
}

// A document that is no longer current (a search hit's `stale`: state,
// since, replaced_by): its state and date, and a link to what replaced it.
// Shown, never hidden: the search ranks it lower, the reader decides.
function staleNote(stale) {
  if (!stale || !stale.state) return "";
  const when = stale.since ? ` ${esc(stale.since)}` : "";
  const by = (stale.replaced_by || []).slice(0, 2)
    .map((r) => `<a href="#doc/${Number(r.doc_id)}">${esc(r.title || "doc " + r.doc_id)}</a>`).join("; ");
  const said = { graph: "another document says so", versions: "a later version of its work" }[stale.by] || "its own text says so";
  return `<span class="stale" title="no longer current: ${esc(said)}">${esc(stale.state)}${when}${by ? ` → ${by}` : ""}</span>`;
}

// A page whose sources are no longer current (GET /page's
// `stale_sources`): which, and what replaced each. Said under the page's
// header; the page itself is never changed for it.
function staleSources(sources) {
  if (!sources || !sources.length) return "";
  const items = sources.slice(0, 6).map((s) =>
    `<a href="#doc/${Number(s.doc_id)}">${esc(s.title || "doc " + s.doc_id)}</a> ${staleNote(s)}`);
  const more = sources.length > 6 ? ` and ${sources.length - 6} more` : "";
  const n = sources.length;
  return `<div class="stale-sources">This page rests on ${n} document${n === 1 ? "" : "s"} no longer current: ${items.join("; ")}${more}.</div>`;
}

// A document's times, for its page's header: when it was published (as
// its source says), when it came into the library, when its text was last
// read. Each says what it is, so "2019" is never mistaken for the day the
// file arrived.
// Which version of a work a document is (GET /get's `versions`, plan item
// AS): its place among them, the work, and the others, each a link.
function versionsLine(v, docId) {
  if (!v || !v.versions || v.versions.length < 2) return "";
  const at = v.versions.findIndex((x) => x.doc_id === docId);
  const others = v.versions.filter((x) => x.doc_id !== docId).map((x) =>
    `<a href="#doc/${Number(x.doc_id)}">${esc(x.published || "#" + x.doc_id)}</a>`);
  const which = at === v.versions.length - 1 ? "the latest" : `version ${at + 1}`;
  return `<span title="versions of one work, as a person decided">${which} of ${v.versions.length} of <i>${esc(v.work.name)}</i>: ${others.join(", ")}</span>`;
}
// A search hit's older versions folded under it (store.search, `versions`).
function foldedVersions(ids) {
  if (!ids || !ids.length) return "";
  const links = ids.map((d) => `<a href="#doc/${Number(d)}">#${Number(d)}</a>`).join(", ");
  return ` <span class="muted">· ${ids.length} older version${ids.length === 1 ? "" : "s"}: ${links}</span>`;
}
function dateLine(meta, doc) {
  const bits = [];
  const published = publishedLabel(meta);
  if (published) bits.push(`published ${published}`);
  const day = (s) => esc(String(s || "").slice(0, 10));
  if (doc && doc.added_at) bits.push(`<span class="muted" title="when it came into the library">added ${day(doc.added_at)}</span>`);
  if (doc && doc.parsed_at && day(doc.parsed_at) !== day(doc.added_at)) bits.push(`<span class="muted" title="when its text was last read">read ${day(doc.parsed_at)}</span>`);
  const st = (meta || {}).status;
  if (st && ["retired", "superseded", "invalid", "deprecated"].includes(st.state)) {
    bits.push(`<span class="stale" title="${esc(st.words || "")}">${esc(st.state)}${st.since ? " " + esc(st.since) : ""}</span>`);
  }
  return bits.join(" · ");
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
  if (kind === "figure-text") return `words printed in a figure${many} · ${size}`;
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

// A document's domains as small links, each to the view it came from
// narrowed to that domain (`href(domain)`); none set reads "every module"
// only where `quiet` is false, so a list of hits stays short.
function domainChips(domains, href, quiet) {
  if (!domains || !domains.length) return quiet ? "" : '<span class="muted domain-none" title="no domain set: read against every module">every module</span>';
  return `<span class="domain-chips" title="the ontology modules this document is read against">${domains.map((d) =>
    `<a class="domain-chip" href="${esc(href(d))}">${esc(d)}</a>`).join("")}</span>`;
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

// What the calculator found in an answer (the maths pack's answer check):
// the links that do not hold, the numbers that differ from a result or
// that nothing checked. The answer's text is left as the model wrote it;
// these sit under it. Empty when there is nothing to say.
function checksBox(checks) {
  const lines = (checks || []).map((c) => {
    if (c.kind === "link" && c.verdict === "does not hold") {
      return `<li>this does not hold: <code>${esc(c.link)}</code></li>`;
    }
    if (c.kind === "number" && c.verdict === "differs") {
      return `<li>${esc(c.number)}: the calculator gives ${esc(c.result)}</li>`;
    }
    if (c.kind === "number" && c.verdict === "not checked") {
      return `<li>${esc(c.number)}: not checked</li>`;
    }
    return "";
  }).filter(Boolean);
  if (!lines.length) return "";
  return `<div class="answer-checks"><span class="muted">The calculator:</span><ul>${lines.join("")}</ul></div>`;
}

// ----------------------------------------------------------- graph drawing
// The parts of the graph view that are arithmetic, kept here so they can
// be tested without a canvas.

// A number in [0, 1) from a string, the same on every visit: where a node
// no layout remembers is first put, so the same graph opens the same way.
function hashUnit(s) {
  let h = 2166136261;
  for (const ch of String(s)) { h ^= ch.codePointAt(0); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 100000) / 100000;
}

// A region's hue: spread by the golden angle so neighbouring ids differ.
function regionHue(id) {
  return Math.round((Number(id) * 137.508) % 360);
}

// The links worth drawing at a glance: each node's `per` heaviest. A link
// is strong when it is among them for either of its ends. Links are
// {a, b, weight}; the answer is the set of their indexes.
function strongLinks(links, per = 3) {
  const by = new Map();
  links.forEach((l, i) => {
    for (const k of [l.a, l.b]) { if (!by.has(k)) by.set(k, []); by.get(k).push(i); }
  });
  const strong = new Set();
  for (const ids of by.values()) {
    ids.sort((p, q) => links[q].weight - links[p].weight);
    for (const i of ids.slice(0, per)) strong.add(i);
  }
  return strong;
}

// Which labels to write. Candidates come in order of importance, each a
// box {key, x, y, w, h} with `must` for the ones always written (the
// selected node, the one under the pointer, a match). A label goes on when
// it overlaps none already placed; `limit` caps the ones that are not a
// must. The answer is the set of keys.
function placeLabels(cands, limit = Infinity) {
  const placed = [];
  const out = new Set();
  let free = 0;
  for (const c of cands) {
    if (!c.must) {
      if (free >= limit) continue;
      const clash = placed.some((p) => c.x < p.x + p.w && p.x < c.x + c.w && c.y < p.y + p.h && p.y < c.y + c.h);
      if (clash) continue;
      free++;
    }
    placed.push(c);
    out.add(c.key);
  }
  return out;
}

// The neighbour an arrow key moves to: of `cands` ({key, x, y}), the one
// closest in the direction (dx, dy) from `from`, within 60 degrees of it,
// distance weighted by how far off the direction it lies. None: null.
function neighbourToward(from, cands, dx, dy) {
  let best = null, bestScore = Infinity;
  for (const c of cands) {
    const vx = c.x - from.x, vy = c.y - from.y;
    const d = Math.hypot(vx, vy);
    if (!d) continue;
    const cos = (vx * dx + vy * dy) / d / Math.hypot(dx, dy);
    if (cos < 0.5) continue;
    const score = d * (2 - cos);
    if (score < bestScore) { best = c.key; bestScore = score; }
  }
  return best;
}

// A text check's fix (meta.quality, parsers.quality.Fix) in words: the
// reading, with the paper it fetches or marker reading every page itself.
// A finding kept before the fix was a reading (version 4) shows its word.
function fixLabel(fix) {
  if (!fix) return "";
  if (typeof fix === "string") return fix;
  if (fix.ocr) return `${fix.extractor} (OCR)`;
  return fix.mode ? `${fix.extractor} (${fix.mode})` : String(fix.extractor || "");
}

// The domain menu's options: the modules as a tree, each under the first
// module it is built on (workshop, on craft and studio, sits under craft).
// A module's filter holds the modules built on it (``Ontology.within``),
// so its line names them and counts them all, and "craft only" below it
// (``only:craft``) is the module alone. ``counts`` is /domains/counts'
// modules: own, within, requires, holds; without numbers the tree stays.
function moduleOptions(mods, counts, selected) {
  const of = (m) => counts[m] || {};
  const parentOf = (m) => (of(m).requires || []).find((r) => r !== "core" && mods.includes(r));
  const kids = (p) => mods.filter((m) => parentOf(m) === p);
  const n = (v) => (v == null ? "" : ` (${v})`);
  const out = [];
  const option = (value, depth, label, hint) => out.push(
    `<option value="${esc(value)}" title="${esc(hint)}"${value === selected ? " selected" : ""}>`
    + `${esc("  ".repeat(depth) + label)}</option>`);
  const walk = (m, depth) => {
    const below = kids(m);
    const holds = (of(m).holds || below).filter((x) => x !== m);
    if (!holds.length) {
      option(m, depth, m + n(of(m).within), m);
    } else {
      option(m, depth, `${m} + ${holds.join(", ")}${n(of(m).within)}`, `${m} and the modules built on it`);
      option(`only:${m}`, depth + 1, `${m} only${n(of(m).own)}`, `${m}'s own documents, without the modules built on it`);
    }
    for (const k of below) walk(k, depth + 1);
  };
  for (const m of mods.filter((x) => !parentOf(x))) walk(m, 0);
  return out.join("");
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { moduleOptions, fixLabel,esc, parseHash, headingPath, norm, locateChunk, citeLinks, mathSpans, figureItems, pageWindow, pagerHtml, pictureSrc, pictureLine, galleryHtml, viewerStep, viewerHtml, pictureOrWord, pictureSetOf, referenceLinks, citeMarkers, askInterior, askBlockMarkers, nextAskId, upPanel, waitingList, chunkTarget, publishedLabel, staleNote, versionsLine, foldedVersions, staleSources, dateLine, mb, spendPanel, regionList, regionPage, regionName, regionLine, propertiesHtml, labelList, genreRow, versionRow, eventRow, eventDays, burstLabel, timelineRows, facesWords, faceBox, pictureFaceRow, faceCounts, faceTile, faceGroupRow, faceMatchRow, personValue, personChoices, personChoice, personEditHtml, peopleTable, personOther, personRelation, personHtml, nameDiff, pairRow, foreignRow, mediaKinds, mediaGroupRow, mediaCrumbs, sameRule, suspectRow, cleanupRules, cleanupPreview, cleanupRuns, tokensTable, tokenSecret, privateRules, splitRow, mergeRow, entitySide, usd, waitingNote, domainChips, asideLine, ingredientsBox, amount, languageName, queueRate, checksBox, hashUnit, regionHue, strongLinks, placeLabels, neighbourToward };
}
