/* prax web UI: a client of the HTTP door. One page, hash routes, one render
   function per view. No framework; marked.js renders Markdown. */
"use strict";

const view = document.getElementById("view");
const statusEl = document.getElementById("status");

// ------------------------------------------------------------- utilities

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

async function api(path, params) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  }
  setStatus("…");
  const res = await fetch(url);
  setStatus("");
  if (res.status === 401) {
    askForToken();
    throw new Error("access token required");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* keep */ }
    throw new Error(msg);
  }
  return res.json();
}

// ------------------------------------------------------------------ auth
// The door wants a bearer token (PRAX_TOKEN). The UI exchanges it once for
// an HttpOnly session cookie via POST /session, so plain links (originals,
// raw text) work in new tabs too. Nothing is kept in localStorage.

function askForToken() {
  if (document.getElementById("token-form")) return;
  const box = document.createElement("div");
  box.className = "token-box";
  box.innerHTML = `
    <form id="token-form" class="search-form" autocomplete="off">
      <label for="token-input">Access token</label>
      <input id="token-input" name="token" type="password" placeholder="PRAX_TOKEN" autofocus>
      <button>Unlock</button>
      <span id="token-msg" class="error"></span>
    </form>`;
  view.prepend(box);
  document.getElementById("token-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const token = document.getElementById("token-input").value.trim();
    const res = await fetch("/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (res.ok) {
      box.remove();
      render();
    } else {
      document.getElementById("token-msg").textContent = "not accepted";
    }
  });
}

function setStatus(text) { statusEl.textContent = text; }

function route() {
  const hash = location.hash.replace(/^#/, "") || "search";
  const [pathPart, query] = hash.split("?");
  const parts = pathPart.split("/");
  const params = Object.fromEntries(new URLSearchParams(query || ""));
  return { name: parts[0], arg: parts.slice(1).join("/"), params };
}

function go(name, arg, params) {
  const q = new URLSearchParams(params || {}).toString();
  location.hash = name + (arg ? "/" + arg : "") + (q ? "?" + q : "");
}

function md(text) {
  return marked.parse(text || "", { gfm: true, breaks: false });
}

function badge(kind) {
  return `<span class="badge badge-${esc(kind || "text")}">${esc(kind || "text")}</span>`;
}

function headingPath(h) {
  return (h || []).map(esc).join(" › ");
}

function originalHref(docId, page) {
  return `/doc/${docId}/original` + (page ? `#page=${page}` : "");
}

// ---------------------------------------------------------------- search

function renderSearchForm(p) {
  const mode = p.mode || "hybrid";
  const kind = p.kind || "";
  return `
  <form id="search-form" class="search-form">
    <input name="q" type="search" value="${esc(p.q || "")}" placeholder="query" autofocus>
    <select name="mode">
      ${["hybrid", "fts", "vec"].map((m) => `<option ${m === mode ? "selected" : ""}>${m}</option>`).join("")}
    </select>
    <select name="kind">
      <option value="" ${kind === "" ? "selected" : ""}>any kind</option>
      ${["text", "table", "figure", "code"].map((k) => `<option ${k === kind ? "selected" : ""}>${k}</option>`).join("")}
    </select>
    <input name="limit" type="number" min="1" max="100" value="${esc(p.limit || 20)}" title="limit">
    <button>Search</button>
  </form>`;
}

async function viewSearch(p) {
  view.innerHTML = renderSearchForm(p) + `<div id="results"></div>`;
  const form = document.getElementById("search-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    go("search", "", data);
  });
  if (!p.q) return;
  const results = document.getElementById("results");
  try {
    const hits = await api("/search", { q: p.q, mode: p.mode || "hybrid", kind: p.kind, limit: p.limit || 20 });
    if (!hits.length) { results.innerHTML = `<p class="muted">No hits.</p>`; return; }
    results.innerHTML = hits.map((h) => {
      const page = h.page ? `p. ${h.page}` : "";
      const sides = [];
      if (h.fts_rank !== undefined) {
        if (h.fts_rank) sides.push(`fts #${h.fts_rank}`);
        if (h.vec_rank) sides.push(`vec #${h.vec_rank}`);
      }
      return `
      <article class="hit">
        <a class="hit-title" href="#doc/${h.doc_id}?chunk=${h.chunk_id}">${esc(h.title || "(untitled)")}</a>
        <div class="hit-meta">
          ${badge(h.kind)}
          <span>${headingPath(h.heading)}</span>
          <span>${esc(page)}</span>
          <span class="muted">${sides.map(esc).join(" · ")}</span>
          <a href="${originalHref(h.doc_id, h.page)}" target="_blank" rel="noopener">original ↗</a>
        </div>
        <p class="snippet">${snippetHtml(h.snippet)}</p>
      </article>`;
    }).join("");
  } catch (err) {
    results.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

function snippetHtml(s) {
  // FTS marks matches with [ ]; keep the rest escaped.
  return esc(s).replace(/\[([^\]]+)\]/g, "<mark>$1</mark>");
}

// -------------------------------------------------------------- document

function metaLine(meta) {
  const bits = [];
  if (meta.creators && meta.creators.length) bits.push(meta.creators.map((c) => c.name).join(", "));
  if (meta.date) bits.push(meta.date);
  if (meta.doi) bits.push(`<a href="https://doi.org/${esc(meta.doi)}" target="_blank" rel="noopener">doi:${esc(meta.doi)}</a>`);
  if (meta.fields && meta.fields.publicationTitle) bits.push(esc(meta.fields.publicationTitle));
  if (meta.text_source) bits.push(`<span class="muted">text: ${esc(meta.text_source)}</span>`);
  if (meta.citations && meta.citations.resolved) {
    const c = meta.citations;
    bits.push(`<span title="${esc(c.source)} ${esc(c.id || "")}">cited by ${Number(c.cited_by_count || 0).toLocaleString()} · ${c.references} references</span>`);
  }
  return bits.join(" · ");
}

function tags(meta) {
  const t = [...(meta.tags || []), ...(meta.collections || []).map((c) => "📁 " + c)];
  return t.length ? `<div class="tags">${t.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</div>` : "";
}

function renderTable(data) {
  if (!data || !data.header) return "";
  const head = `<tr>${data.header.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
  const rows = (data.rows || []).map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table>${head}${rows}</table></div>`;
}

function renderChunk(c, highlight) {
  const cls = "chunk kind-" + (c.kind || "text") + (c.chunk_id === highlight ? " highlight" : "");
  let body;
  if (c.kind === "table" && c.data && c.data.header && c.data.header.length) {
    const caption = c.text.split("\n").filter((l) => !l.trim().startsWith("|")).join("\n").trim();
    body = (caption ? md(caption) : "") + renderTable(c.data);
  } else if (c.kind === "code") {
    body = md(c.text);
  } else {
    body = md(c.text);
  }
  return `
  <section class="${cls}" id="chunk-${c.chunk_id}" data-chunk="${c.chunk_id}">
    <div class="chunk-meta">
      ${badge(c.kind)}
      <span class="muted">${headingPath(c.heading)}</span>
      ${c.page ? `<span class="muted">p. ${c.page}</span>` : ""}
      <span class="muted">#${c.chunk_id}</span>
    </div>
    <div class="chunk-body">${body}</div>
  </section>`;
}

function outline(chunks) {
  const seen = new Set();
  const items = [];
  for (const c of chunks) {
    const h = (c.heading || []);
    if (!h.length) continue;
    const key = h.join(" ");
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(`<li style="--depth:${h.length - 1}"><a href="#chunk-${c.chunk_id}" data-scroll="${c.chunk_id}">${esc(h[h.length - 1])}</a></li>`);
  }
  return items.length ? `<ol class="outline">${items.join("")}</ol>` : "";
}

async function viewDoc(id, p) {
  view.innerHTML = `<p class="muted">Loading document ${esc(id)}…</p>`;
  let doc, chunks;
  try {
    [doc, chunks] = await Promise.all([api(`/get/${id}`, { max_chars: 0 }), api(`/doc/${id}/chunks`)]);
  } catch (err) {
    view.innerHTML = `<p class="error">${esc(err.message)}</p>`;
    return;
  }
  const meta = doc.meta || {};
  const highlight = p.chunk ? Number(p.chunk) : null;
  const firstPage = highlight ? (chunks.find((c) => c.chunk_id === highlight) || {}).page : null;
  view.innerHTML = `
  <header class="doc-head">
    <h1>${esc(doc.title || "(untitled)")}</h1>
    <div class="doc-meta">${metaLine(meta)}</div>
    ${tags(meta)}
    <div class="doc-actions">
      <a href="${originalHref(doc.id, firstPage)}" target="_blank" rel="noopener">open original ↗</a>
      <a href="/doc/${doc.id}/text" target="_blank" rel="noopener">raw text ↗</a>
      <span class="muted">${esc(doc.mime || "")} · ${chunks.length} chunks · ${(doc.text_len || 0).toLocaleString()} chars · doc ${doc.id}</span>
    </div>
  </header>
  <div class="doc-layout">
    <aside class="doc-outline">${outline(chunks)}</aside>
    <div class="doc-body">${(doc.mime || "").startsWith("image/") ? `<a href="${originalHref(doc.id)}" target="_blank" rel="noopener"><img class="doc-image" src="${originalHref(doc.id)}" alt="${esc(doc.title || "")}"></a>` : ""}${chunks.length ? chunks.map((c) => renderChunk(c, highlight)).join("") : `<p class="muted">No text yet.${(doc.mime || "").startsWith("image/") ? " Describe it with <code>parse_pending.py --ids " + doc.id + " --extractor claude-vision</code>." : ""}</p>`}</div>
    <aside class="doc-context" id="doc-context"><p class="muted">Loading context…</p></aside>
  </div>`;
  api(`/doc/${id}/context`).then((ctx) => {
    document.getElementById("doc-context").innerHTML = renderContext(ctx);
  }).catch((err) => {
    document.getElementById("doc-context").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  });
  document.querySelectorAll("[data-scroll]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    const el = document.getElementById("chunk-" + a.dataset.scroll);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  if (highlight) {
    const el = document.getElementById("chunk-" + highlight);
    if (el) el.scrollIntoView({ block: "center" });
  }
}

// The context column: where the document sits in the library.
function renderContext(ctx) {
  const docLink = (d, extra) => `<li><a href="#doc/${d.doc_id}">${esc(d.title || "(untitled)")}</a>${extra ? ` <span class="muted">${extra}</span>` : ""}</li>`;
  const list = (title, items, render) => items.length ? `<h3>${title}</h3><ul>${items.map(render).join("")}</ul>` : "";
  const parts = [];
  if (ctx.summary) parts.push(`<h3>Summary</h3><p class="summary">${esc(ctx.summary)}</p>`);
  if (ctx.entities.length) {
    parts.push(`<h3>Entities</h3><div class="chips">${ctx.entities.map((e) =>
      `<a class="chip" style="--c:${typeColor(e.type)}" href="#graph?entity=${encodeURIComponent(e.name)}" title="${esc(e.rel)} · ${esc(e.type)} · ${esc(e.confidence)}">${esc(e.name)}</a>`).join("")}</div>`);
  }
  parts.push(list("Similar documents", ctx.similar, (d) => docLink(d, `${d.score.toFixed(2)}`)));
  parts.push(list("Shares entities with", ctx.shared, (d) => docLink(d, `${d.count}: ${esc(d.entities.join(", "))}`)));
  const inLib = ctx.cited_by.length;
  parts.push(list(`Cited by${inLib ? ` (${inLib} in the library)` : ""}`, ctx.cited_by, (d) => docLink(d)));
  if (ctx.cites.length) {
    const libCites = ctx.cites.filter((c) => c.doc_id);
    const external = ctx.cites.length - libCites.length;
    parts.push(`<h3>Cites (${ctx.cites.length}${ctx.citations && ctx.citations.cited_by_count != null ? ` · cited by ${Number(ctx.citations.cited_by_count).toLocaleString()} overall` : ""})</h3><ul>${
      libCites.slice(0, 12).map((c) => `<li><a href="#doc/${c.doc_id}">${esc(c.title)}</a></li>`).join("")
    }${external ? `<li class="muted">${external} outside the library</li>` : ""}</ul>`);
  }
  parts.push(list("Same authors", ctx.same_authors, (d) => docLink(d, esc(d.authors.join(", ")))));
  const z = ctx.zotero;
  const zbits = [];
  if (z.parent) zbits.push(`<li>Part of ${z.parent.doc_id ? `<a href="#doc/${z.parent.doc_id}">${esc(z.parent.title)}</a>` : `<span class="muted">item ${esc(z.parent.key)} (not imported)</span>`}</li>`);
  for (const s of z.siblings) zbits.push(`<li><a href="#doc/${s.doc_id}">${esc(s.title || "(untitled)")}</a> <span class="muted">${esc(s.kind || "")}</span></li>`);
  if (z.collections.length) zbits.push(`<li class="muted">📁 ${z.collections.map(esc).join(" · ")}</li>`);
  if (z.tags.length) zbits.push(`<li class="muted">${z.tags.map(esc).join(" · ")}</li>`);
  if (zbits.length) parts.push(`<h3>Zotero</h3><ul>${zbits.join("")}</ul>`);
  const body = parts.filter(Boolean).join("");
  return body || `<p class="muted">Nothing connects this document yet: no extraction, no citations, no neighbours.</p>`;
}

// ---------------------------------------------------------------- browse

async function viewBrowse(p) {
  const limit = Number(p.limit || 50);
  const offset = Number(p.offset || 0);
  view.innerHTML = `
  <form id="browse-form" class="search-form">
    <input name="title" type="search" value="${esc(p.title || "")}" placeholder="title contains…">
    <input name="source" type="text" value="${esc(p.source || "")}" placeholder="source (zotero)">
    <input name="mime" type="text" value="${esc(p.mime || "")}" placeholder="mime (application/pdf)">
    <button>Filter</button>
  </form>
  <div id="browse-list"></div>`;
  const form = document.getElementById("browse-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    go("browse", "", Object.fromEntries(new FormData(form)));
  });
  const list = document.getElementById("browse-list");
  try {
    const res = await api("/documents", { limit, offset, title: p.title, source: p.source, mime: p.mime });
    const rows = res.items.map((d) => `
      <tr>
        <td><a href="#doc/${d.id}">${esc(d.title || "(untitled)")}</a></td>
        <td class="muted">${esc((d.meta.creators || []).slice(0, 2).map((c) => c.name).join(", "))}</td>
        <td class="muted">${esc(d.meta.date || "")}</td>
        <td class="muted">${esc(d.mime || "")}</td>
        <td class="num">${d.n_chunks}</td>
        <td class="muted">${esc((d.added_at || "").slice(0, 10))}</td>
      </tr>`).join("");
    const pager = `
      <div class="pager">
        <span class="muted">${res.total.toLocaleString()} documents · ${offset + 1}–${Math.min(offset + limit, res.total)}</span>
        ${offset > 0 ? `<a href="#browse?${new URLSearchParams({ ...p, offset: Math.max(0, offset - limit) })}">‹ newer</a>` : ""}
        ${offset + limit < res.total ? `<a href="#browse?${new URLSearchParams({ ...p, offset: offset + limit })}">older ›</a>` : ""}
      </div>`;
    list.innerHTML = pager + `
      <table class="doc-list">
        <thead><tr><th>Title</th><th>Creators</th><th>Date</th><th>Type</th><th>Chunks</th><th>Added</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>` + pager;
  } catch (err) {
    list.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

// ----------------------------------------------------------------- graph
// The neighbourhood of an entity as an SVG force layout. Nodes are
// (type, name); edges come from GET /traverse (hops=1) and a click on a
// node fetches its own neighbourhood and merges it in. The layout is a small
// spring model run to rest on every change; no library.

const TYPE_COLORS = {
  paper: "#2f5d8a", author: "#7a5c1e", concept: "#4b7a45", method: "#6a4b7a",
  claim: "#a0522d", tool: "#3b7a7a", venue: "#8a6d2f", dataset: "#5a5a8a",
};
const nodeKey = (name, type) => type + "|" + name;
const typeColor = (t) => TYPE_COLORS[t] || "#888";

class ForceGraph {
  constructor(svg, onSelect) {
    this.svg = svg;
    this.onSelect = onSelect;
    this.nodes = new Map();
    this.edges = new Map();
    this.links = [];  // co-occurrence links of the overview: {a, b, weight}
    this.selected = null;
    this.box = { x: -450, y: -300, w: 900, h: 600 };
    this.bindPanZoom();
    svg.addEventListener("click", (e) => {
      const g = e.target.closest(".node");
      if (!g) return;
      this.select(g.dataset.key);
      if (!this.nodes.get(g.dataset.key).expanded) this.expand(g.dataset.key);
    });
    svg.addEventListener("dblclick", (e) => {
      const g = e.target.closest(".node");
      if (g) go("graph", "", { entity: this.nodes.get(g.dataset.key).name });
    });
  }

  node(name, type, near) {
    const key = nodeKey(name, type);
    let n = this.nodes.get(key);
    if (!n) {
      const a = Math.random() * Math.PI * 2;
      const r = 60 + Math.random() * 60;
      n = { key, name, type, expanded: false, degree: 0,
            x: (near ? near.x : 0) + Math.cos(a) * r, y: (near ? near.y : 0) + Math.sin(a) * r, vx: 0, vy: 0 };
      this.nodes.set(key, n);
    }
    return n;
  }

  merge(edgeList, aroundKey, nodeList, linkList) {
    const near = aroundKey ? this.nodes.get(aroundKey) : null;
    for (const n of nodeList || []) this.node(n.name, n.type, null).degree = n.degree || 0;
    for (const l of linkList || []) {
      this.links.push({ a: this.node(l.a, l.a_type, null).key, b: this.node(l.b, l.b_type, null).key, weight: l.weight });
    }
    for (const e of edgeList) {
      if (this.edges.has(e.edge_id)) continue;
      const s = this.node(e.src, e.src_type, near);
      const t = this.node(e.dst, e.dst_type, near);
      s.degree++; t.degree++;
      this.edges.set(e.edge_id, { ...e, s: s.key, t: t.key });
    }
    this.layout();
    this.draw();
  }

  async expand(key) {
    const n = this.nodes.get(key);
    n.expanded = true;
    const edges = await api("/traverse", { entity: n.name, hops: 1 });
    this.merge(edges, key);
  }

  layout(iterations = 300) {
    const nodes = [...this.nodes.values()];
    const edges = [...this.edges.values()];
    let alpha = 1;
    for (let it = 0; it < iterations; it++) {
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          let dx = b.x - a.x, dy = b.y - a.y;
          let d2 = dx * dx + dy * dy || 1;
          if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
          const f = Math.min(6000 / d2, 8) * alpha;
          const d = Math.sqrt(d2);
          a.vx -= dx / d * f; a.vy -= dy / d * f;
          b.vx += dx / d * f; b.vy += dy / d * f;
        }
        a.vx -= a.x * 0.01 * alpha; a.vy -= a.y * 0.01 * alpha;
      }
      for (const e of edges) {
        const a = this.nodes.get(e.s), b = this.nodes.get(e.t);
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - 110) * 0.04 * alpha;
        a.vx += dx / d * f; a.vy += dy / d * f;
        b.vx -= dx / d * f; b.vy -= dy / d * f;
      }
      for (const l of this.links) {
        const a = this.nodes.get(l.a), b = this.nodes.get(l.b);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - 150) * Math.min(0.03, 0.004 * l.weight) * alpha;
        a.vx += dx / d * f; a.vy += dy / d * f;
        b.vx -= dx / d * f; b.vy -= dy / d * f;
      }
      for (const n of nodes) {
        n.x += n.vx; n.y += n.vy; n.vx *= 0.5; n.vy *= 0.5;
      }
      alpha *= 0.985;
    }
    this.fit();
  }

  fit() {
    const nodes = [...this.nodes.values()];
    if (!nodes.length) return;
    const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
    const pad = 80;
    const x = Math.min(...xs) - pad, y = Math.min(...ys) - pad;
    const w = Math.max(...xs) - x + pad, h = Math.max(...ys) - y + pad;
    const el = this.svg.getBoundingClientRect();
    const aspect = (el.width || 900) / (el.height || 600);
    if (w / h > aspect) this.box = { x, y: y - (w / aspect - h) / 2, w, h: w / aspect };
    else this.box = { x: x - (h * aspect - w) / 2, y, w: h * aspect, h };
    this.applyBox();
  }

  applyBox() {
    const b = this.box;
    this.svg.setAttribute("viewBox", `${b.x} ${b.y} ${b.w} ${b.h}`);
  }

  bindPanZoom() {
    let drag = null;
    this.svg.addEventListener("mousedown", (e) => {
      if (e.target.closest(".node")) return;
      drag = { x: e.clientX, y: e.clientY, bx: this.box.x, by: this.box.y };
      this.svg.classList.add("dragging");
    });
    window.addEventListener("mousemove", (e) => {
      if (!drag) return;
      const el = this.svg.getBoundingClientRect();
      const k = this.box.w / (el.width || 1);
      this.box.x = drag.bx - (e.clientX - drag.x) * k;
      this.box.y = drag.by - (e.clientY - drag.y) * k;
      this.applyBox();
    });
    window.addEventListener("mouseup", () => { drag = null; this.svg.classList.remove("dragging"); });
    this.svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const el = this.svg.getBoundingClientRect();
      const px = this.box.x + (e.clientX - el.left) / el.width * this.box.w;
      const py = this.box.y + (e.clientY - el.top) / el.height * this.box.h;
      const z = e.deltaY > 0 ? 1.15 : 1 / 1.15;
      this.box = { x: px - (px - this.box.x) * z, y: py - (py - this.box.y) * z, w: this.box.w * z, h: this.box.h * z };
      this.applyBox();
    }, { passive: false });
  }

  select(key) {
    this.selected = key;
    this.draw();
    this.onSelect(this.nodes.get(key), this.edgesOf(key));
  }

  edgesOf(key) {
    return [...this.edges.values()].filter((e) => e.s === key || e.t === key);
  }

  draw() {
    const lines = [], labels = [], nodes = [];
    for (const l of this.links) {
      const a = this.nodes.get(l.a), b = this.nodes.get(l.b);
      if (!a || !b) continue;
      lines.push(`<line class="co" style="stroke-width:${Math.min(6, 0.6 + l.weight * 0.5).toFixed(1)}" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"><title>${esc(a.name)} and ${esc(b.name)} share ${l.weight} documents</title></line>`);
    }
    for (const e of this.edges.values()) {
      const a = this.nodes.get(e.s), b = this.nodes.get(e.t);
      const cls = [e.confidence === "INFERRED" ? "inferred" : e.confidence === "AMBIGUOUS" ? "ambiguous" : "",
                   (e.s === this.selected || e.t === this.selected) ? "selected" : ""].join(" ");
      lines.push(`<line class="${cls}" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"><title>${esc(e.src)} ${esc(e.rel)} ${esc(e.dst)} (${esc(e.confidence)})${e.evidence ? "\n" + esc(e.evidence) : ""}</title></line>`);
      labels.push(`<text class="edge-label" x="${((a.x + b.x) / 2).toFixed(1)}" y="${((a.y + b.y) / 2 - 3).toFixed(1)}" text-anchor="middle">${esc(e.rel)}</text>`);
    }
    for (const n of this.nodes.values()) {
      const r = 6 + Math.min(14, Math.sqrt(n.degree) * 2.2);
      const cls = ["node", n.expanded ? "expanded" : "", n.key === this.selected ? "selected" : ""].join(" ");
      const label = n.name.length > 38 ? n.name.slice(0, 36) + "…" : n.name;
      nodes.push(`<g class="${cls}" data-key="${esc(n.key)}" transform="translate(${n.x.toFixed(1)},${n.y.toFixed(1)})"><circle r="${r.toFixed(1)}" fill="${typeColor(n.type)}"><title>${esc(n.name)} (${esc(n.type)}, ${n.degree} edges here)</title></circle><text x="${(r + 3).toFixed(1)}" y="4">${esc(label)}</text></g>`);
    }
    this.svg.innerHTML = `<g>${lines.join("")}${labels.join("")}${nodes.join("")}</g>`;
  }
}

function graphPanel(node, edges) {
  if (!node) return `<p class="muted">Click a node to see its edges; the first click also expands it.</p>`;
  const rows = edges.map((e) => {
    const out = e.s === node.key;
    const other = out ? e.dst : e.src;
    const otherType = out ? e.dst_type : e.src_type;
    return `<li>${out ? "" : `<b>${esc(other)}</b> <span class="muted">${esc(otherType)}</span> `}<span class="rel">${out ? "" : "→ "}${esc(e.rel)}${out ? " →" : ""}</span> ${out ? `<b>${esc(other)}</b> <span class="muted">${esc(otherType)}</span>` : ""}
      <span class="muted">· ${esc(e.confidence)}${e.source_doc ? ` · <a href="#doc/${e.source_doc}">doc ${e.source_doc}</a>` : ""}</span>
      ${e.evidence ? `<span class="ev">“${esc(e.evidence)}”</span>` : ""}</li>`;
  });
  return `<h2>${esc(node.name)}</h2><div class="muted">${esc(node.type)} · ${edges.length} edges shown</div><ul>${rows.join("")}</ul>`;
}

async function viewGraph(arg, p) {
  const q = p.q || "";
  const entity = p.entity || arg || "";
  view.innerHTML = `
  <form id="graph-form" class="search-form">
    <input name="q" type="search" value="${esc(q)}" placeholder="find an entity…">
    <button>Find</button>
  </form>
  <div id="graph-out"></div>`;
  const form = document.getElementById("graph-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    go("graph", "", Object.fromEntries(new FormData(form)));
  });
  const out = document.getElementById("graph-out");
  try {
    if (!entity && !q) {
      const legend = Object.entries(TYPE_COLORS).map(([t, c]) => `<span style="--c:${c}">${t}</span>`).join("");
      out.innerHTML = `
        <div class="graph-tools">
          <span>Overview: the most connected concepts, methods, tools and datasets</span>
          <span class="muted">· faint lines: hubs that share documents · click a node to expand it, double-click to open its neighbourhood</span>
          <button type="button" id="graph-fit" class="secondary">fit</button>
        </div>
        <div class="legend">${legend}</div>
        <div class="graph-layout">
          <div class="graph-canvas"><svg xmlns="http://www.w3.org/2000/svg"></svg></div>
          <aside class="graph-panel" id="graph-panel">${graphPanel(null, [])}</aside>
        </div>`;
      const panel = document.getElementById("graph-panel");
      const graph = new ForceGraph(out.querySelector("svg"), (node, edges) => { panel.innerHTML = graphPanel(node, edges); });
      document.getElementById("graph-fit").addEventListener("click", () => graph.fit());
      const overview = await api("/graph/overview", { limit: 30 });
      if (!overview.nodes.length) { out.innerHTML = `<p class="muted">The graph is empty; run an extraction first.</p>`; return; }
      graph.merge(overview.edges, null, overview.nodes, overview.links);
      return;
    }
    if (!entity) {
      const ents = await api("/entities", { q, limit: 40 });
      if (!ents.length) { out.innerHTML = `<p class="muted">No entity matches.</p>`; return; }
      out.innerHTML = `<ul class="entities">${ents.map((e) => `<li><a href="#graph?entity=${encodeURIComponent(e.name)}">${esc(e.name)}</a> <span class="muted">${esc(e.type)} · ${e.degree} edges</span></li>`).join("")}</ul>`;
      return;
    }
    const legend = Object.entries(TYPE_COLORS).map(([t, c]) => `<span style="--c:${c}">${t}</span>`).join("");
    out.innerHTML = `
      <div class="graph-tools">
        <span>Neighbourhood of <b>${esc(entity)}</b></span>
        <span class="muted">· click a node to expand it, drag to pan, wheel to zoom · dashed edges are inferred or ambiguous</span>
        <button type="button" id="graph-fit" class="secondary">fit</button>
      </div>
      <div class="legend">${legend}</div>
      <div class="graph-layout">
        <div class="graph-canvas"><svg xmlns="http://www.w3.org/2000/svg"></svg></div>
        <aside class="graph-panel" id="graph-panel">${graphPanel(null, [])}</aside>
      </div>`;
    const panel = document.getElementById("graph-panel");
    const graph = new ForceGraph(out.querySelector("svg"), (node, edges) => { panel.innerHTML = graphPanel(node, edges); });
    document.getElementById("graph-fit").addEventListener("click", () => graph.fit());
    const edges = await api("/traverse", { entity, hops: 1 });
    if (!edges.length) { panel.innerHTML = `<p class="muted">No edges for this entity.</p>`; return; }
    graph.merge(edges, null);
    const start = [...graph.nodes.values()].find((n) => n.name === entity);
    if (start) { start.expanded = true; graph.select(start.key); }
  } catch (err) {
    out.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

// ---------------------------------------------------------------- review
// Misfit triples parked by extraction (invariant 9). Each item can be
// dropped, marked as an ontology gap, or linked as an edge after fixing its
// types or relation with the current ontology's choices.

async function post(path, body) {
  setStatus("…");
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  setStatus("");
  if (res.status === 401) { askForToken(); throw new Error("access token required"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function options(list, current) {
  return [`<option value="">–</option>`, ...list.map((x) => `<option ${x === current ? "selected" : ""}>${esc(x)}</option>`)].join("");
}

function reviewItem(it, onto) {
  const types = onto.entity_types, rels = Object.keys(onto.relations).sort();
  const unmapped = (it.reason || "").startsWith("unmapped:");
  return `
  <article class="review-item" id="review-${it.id}">
    <div class="review-triple"><b>${esc(it.src)}</b> <span class="muted">${esc(it.src_type || "?")}</span>
      <span class="rel">${esc(it.rel)}</span> <b>${esc(it.dst)}</b> <span class="muted">${esc(it.dst_type || "?")}</span></div>
    <div class="review-meta">#${it.id} · ${esc(it.reason)}${it.source_doc ? ` · <a href="#doc/${it.source_doc}">doc ${it.source_doc}</a>` : ""}${it.evidence ? ` · <i>“${esc(it.evidence)}”</i>` : ""}</div>
    <form class="review-form" data-id="${it.id}">
      <select name="src_type" title="source type">${options(types, it.src_type)}</select>
      <select name="rel" title="relation">${options(rels, unmapped ? "" : it.rel)}</select>
      <select name="dst_type" title="target type">${options(types, it.dst_type)}</select>
      <button name="action" value="linked">link</button>
      <button name="action" value="ontology" class="secondary" title="keep for a later ontology version">ontology gap</button>
      <button name="action" value="dropped" class="secondary">drop</button>
      <span class="error msg"></span>
    </form>
  </article>`;
}

async function viewReview(p) {
  const limit = Number(p.limit || 30);
  const offset = Number(p.offset || 0);
  const filter = { rel: p.rel || "", unmapped: p.unmapped || "" };
  view.innerHTML = `
  <form id="review-filter" class="search-form">
    <input name="rel" type="search" value="${esc(filter.rel)}" placeholder="relation (cites, uses, …)">
    <select name="unmapped">
      <option value="" ${filter.unmapped === "" ? "selected" : ""}>typed and unmapped</option>
      <option value="true" ${filter.unmapped === "true" ? "selected" : ""}>unmapped only (no types)</option>
      <option value="false" ${filter.unmapped === "false" ? "selected" : ""}>typed only (rule misfits)</option>
    </select>
    <button>Filter</button>
    <button type="button" id="bulk-drop" class="secondary" title="close every open item matching the filter as dropped">drop all matching</button>
    <button type="button" id="replay" class="secondary" title="link typed items the current ontology now accepts">replay against ontology</button>
    <span id="review-msg" class="muted"></span>
  </form>
  <div id="review-list"><p class="muted">Loading…</p></div>`;
  const form = document.getElementById("review-filter");
  const query = () => ({ rel: filter.rel || undefined, unmapped: filter.unmapped || undefined });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    go("review", "", Object.fromEntries(new FormData(form)));
  });
  const msg = document.getElementById("review-msg");
  document.getElementById("bulk-drop").addEventListener("click", async () => {
    if (!filter.rel && !filter.unmapped) { msg.textContent = "set a filter first"; return; }
    const n = (await api("/review", { limit: 1, ...query() })).total;
    if (!window.confirm(`Drop all ${n.toLocaleString()} open items matching the filter?`)) return;
    try {
      const r = await post("/review/bulk", { resolution: "dropped", ...query() });
      msg.textContent = `dropped ${r.resolved.toLocaleString()}`;
      render();
    } catch (err) { msg.textContent = err.message; }
  });
  document.getElementById("replay").addEventListener("click", async () => {
    try {
      const r = await post("/review/replay", {});
      msg.textContent = `ontology v${r.ontology_version}: ${r.linked} linked, ${r.existing} already present, ${r.still_open} still open`;
      render();
    } catch (err) { msg.textContent = err.message; }
  });
  const list = document.getElementById("review-list");
  try {
    const [res, onto] = await Promise.all([api("/review", { limit, offset, ...query() }), api("/ontology")]);
    const page = (o) => `#review?${new URLSearchParams({ ...filter, offset: o })}`;
    const pager = `
      <div class="pager">
        <span class="muted">${res.total.toLocaleString()} open items · ${res.items.length ? offset + 1 : 0}–${Math.min(offset + limit, res.total)} · ontology v${esc(onto.version)}</span>
        ${offset > 0 ? `<a href="${page(Math.max(0, offset - limit))}">‹ previous</a>` : ""}
        ${offset + limit < res.total ? `<a href="${page(offset + limit)}">next ›</a>` : ""}
      </div>`;
    list.innerHTML = pager + res.items.map((it) => reviewItem(it, onto)).join("") + pager;
    list.querySelectorAll(".review-form").forEach((form) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const action = e.submitter && e.submitter.value;
        const data = Object.fromEntries(new FormData(form));
        const body = { resolution: action };
        if (action === "linked") Object.assign(body, { src_type: data.src_type, rel: data.rel, dst_type: data.dst_type });
        const msg = form.querySelector(".msg");
        try {
          const r = await post(`/review/${form.dataset.id}`, body);
          const item = document.getElementById(`review-${form.dataset.id}`);
          item.classList.add("done");
          form.innerHTML = `<span class="muted">${action}${r.edge_id ? ` · edge ${r.edge_id}` : ""}</span>`;
        } catch (err) {
          msg.textContent = err.message;
        }
      });
    });
  } catch (err) {
    list.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

// ---------------------------------------------------------------- router

const views = { search: viewSearch, browse: viewBrowse, review: viewReview };

async function render() {
  const r = route();
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === r.name));
  window.scrollTo(0, 0);
  if (r.name === "doc") return viewDoc(r.arg, r.params);
  if (r.name === "graph") return viewGraph(r.arg, r.params);
  return (views[r.name] || viewSearch)(r.params);
}

document.getElementById("quick").addEventListener("submit", (e) => {
  e.preventDefault();
  go("search", "", { q: document.getElementById("quick-q").value });
});
window.addEventListener("hashchange", render);
render();
