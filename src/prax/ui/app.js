/* prax web UI: a client of the HTTP door. One page, hash routes, one render
   function per view. No framework; marked.js renders Markdown. */
"use strict";

const view = document.getElementById("view");
// A view shows "Loading…" while its first requests are out — but not on a
// live refresh (the poll below re-rendering the same route in place), where
// the page it already shows stays until the new one is ready.
let quiet = false;
function loading(text) {
  if (!quiet) view.innerHTML = `<p class="muted">${text || "Loading…"}</p>`;
}
// The list part of a view that draws its form first and fills the list
// after: on a live refresh the list it already shows, otherwise "Loading…".
function listPlaceholder(id) {
  const el = quiet && document.getElementById(id);
  return el ? el.innerHTML : `<p class="muted">Loading…</p>`;
}
const statusEl = document.getElementById("status");

// ---------------------------------------------------------------- settings
// What this browser prefers: the theme, a few defaults. localStorage, this
// browser only, never sent to the door (the session cookie is the only
// thing the door sees). The theme is also applied by an inline script in
// index.html before the first paint, so a reload does not flash.
const SETTINGS_KEY = "prax.settings";
const SETTINGS_DEFAULTS = { theme: "system", ask_limit: 8, search_limit: 20 };
const THEMES = ["bindery", "dessau", "riso", "cyanotype", "night", "funk"];  // docs/design/BRIEF.md
const OLD_THEMES = { light: "bindery", dark: "night", paper: "bindery" };

function settings() {
  let s;
  try { s = { ...SETTINGS_DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") }; }
  catch (_) { s = { ...SETTINGS_DEFAULTS }; }
  s.theme = OLD_THEMES[s.theme] || s.theme;
  return s;
}
function saveSettings(s) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch (_) { /* private mode */ }
  applyTheme(s.theme);
}
function applyTheme(theme) {
  // the four values switch the page and the inlined mark together
  if (!THEMES.includes(theme)) {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "night" : "bindery";
  }
  document.documentElement.dataset.praxTheme = theme;
  // the canvas reads its colours when it draws; a settled graph must be told
  const live = ForceGraph.live;
  if (live && document.contains(live.canvas)) live.draw();
}
function openSettings() {
  const dlg = document.getElementById("settings");
  const form = document.getElementById("settings-form");
  const s = settings();
  form.theme.value = s.theme;
  form.ask_limit.value = s.ask_limit;
  form.search_limit.value = s.search_limit;
  form.theme.onchange = () => applyTheme(form.theme.value);  // a live preview
  dlg.onclose = () => {
    const next = {
      theme: form.theme.value,
      ask_limit: Math.max(1, Math.min(20, Number(form.ask_limit.value) || 8)),
      search_limit: Math.max(5, Math.min(100, Number(form.search_limit.value) || 20)),
    };
    saveSettings(next);
  };
  dlg.showModal();
}
document.getElementById("settings-btn").addEventListener("click", openSettings);
// (the theme itself is applied by the inline script in index.html, before paint)

// ------------------------------------------------------------- utilities


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
// raw text) work in new tabs too. The token is never kept in the browser
// (localStorage holds the settings, sessionStorage the ask conversation).

function askForToken() {
  if (document.getElementById("token-form")) return;
  const box = document.createElement("div");
  box.className = "token-box plate";
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


function route() { return parseHash(location.hash); }
function go(name, arg, params) {
  const q = new URLSearchParams(params || {}).toString();
  location.hash = name + (arg ? "/" + arg : "") + (q ? "?" + q : "");
}

function md(text) {
  // U+FFFD is a glyph the PDF's font gave no name: a box, not a question
  const html = marked.parse(text || "", { gfm: true, breaks: false });
  return html.replace(/\uFFFD/g, '<span class="lost" title="a glyph the document\u2019s font did not name">\u25AB</span>');
}

// A rule that ends in a mark: a hairline, the lozenge, a hairline. Says a
// section ended; does nothing.
function rule() {
  return `<div class="rule" aria-hidden="true"><svg viewBox="-7 -7 14 14"><path d="M0 -5.4 C 0.8 -1.8 1.9 -0.7 5.3 0.2 C 1.8 0.9 0.7 2 -0.2 5.4 C -0.9 1.9 -2 0.8 -5.4 -0.2 C -1.9 -0.9 -0.8 -2 0 -5.4 Z" fill="var(--prax-colour)"/></svg></div>`;
}

function badge(kind) {
  return `<span class="badge badge-${esc(kind || "text")}">${esc(kind || "text")}</span>`;
}


function originalHref(docId, page) {
  return `/doc/${docId}/original` + (page ? `#page=${page}` : "");
}

// ---------------------------------------------------------------- search

// The ontology's modules, once per page load, for the domain selectors:
// "every module" is the empty value, which the door reads as no filter.
let MODULES = null;
async function modules() {
  if (MODULES) return MODULES;
  try {
    const onto = await api("/ontology");
    MODULES = Object.keys(onto.modules || {}).filter((m) => m !== "core").sort();
  } catch (err) {
    MODULES = [];
  }
  return MODULES;
}

function domainSelect(selected, name = "domain", title = "ontology module") {
  const opts = (MODULES || []).map((m) => `<option value="${esc(m)}" ${m === selected ? "selected" : ""}>${esc(m)}</option>`).join("");
  return `<select name="${name}" title="${title}"><option value="" ${!selected ? "selected" : ""}>every module</option>${opts}</select>`;
}

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
    <select name="doctype" title="document type">
      <option value="" ${!p.doctype ? "selected" : ""}>any type</option>
      ${[["pdf", "PDFs"], ["web", "web pages"], ["image", "images"], ["text", "text files"], ["note", "notes"]].map(([v, l]) => `<option value="${v}" ${p.doctype === v ? "selected" : ""}>${l}</option>`).join("")}
    </select>
    ${domainSelect(p.domain || "")}
    <input name="limit" type="number" min="1" max="100" value="${esc(p.limit || settings().search_limit)}" title="limit">
    <button>Search</button>
  </form>`;
}

async function viewSearch(p) {
  await modules();
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
    const hits = await api("/search", { q: p.q, mode: p.mode || "hybrid", kind: p.kind, doctype: p.doctype, domain: p.domain || undefined, limit: p.limit || settings().search_limit });
    if (!hits.length) { results.innerHTML = `<p class="muted">No hits.</p>`; return; }
    results.innerHTML = hits.map((h) => {
      const page = h.page ? `p. ${h.page}` : "";
      const sides = [];
      if (h.fts_rank !== undefined) {
        if (h.fts_rank) sides.push(`fts #${h.fts_rank}`);
        if (h.vec_rank) sides.push(`vec #${h.vec_rank}`);
        if (h.field_rank) sides.push(`doc #${h.field_rank}`);
        if (h.dvec_rank) sides.push(`doc-vec #${h.dvec_rank}`);
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
        ${figureThumb(h)}
        <p class="snippet">${snippetHtml(plainFigures(h.snippet))}</p>
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


// A reading request: which extractor a person may ask for on a document
// of this type, and what became of the last request (meta.reading).
const READINGS = {
  "application/pdf": [
    ["figures", "the vision model reads the figures the text references (the captioned ones, or every image), and writes what it shows under each"],
    ["vision-pages", "the vision model over the scanned pages (handwriting, scores, what OCR cannot read)"],
    ["pymupdf4llm", "read the PDF again (finds the figures)"],
    ["pymupdf4llm-ocr", "OCR (RapidOCR) on the pages without a text layer"],
    ["marker", "marker: the mathematics as LaTeX (formula chunks), tables as tables; needs its server (prax up --start marker)"],
    ["formulas", "the formulas model says in words what each display equation is, under it (needs LaTeX in the text: marker first)"],
    ["docling", "Docling's layout model (code, tables); slow"],
  ],
  "text/html": [
    ["figures", "the vision model reads every figure the text references, and writes what it shows under each"],
    ["formulas", "the formulas model says in words what each display equation is, under it"],
    ["trafilatura", "read the page again (finds the figures)"],
  ],
  "text/": [
    ["formulas", "the formulas model says in words what each display equation ($$…$$) is, under it"],
  ],
  "image/": [["vision", "the vision model describes the image; a second model's reading joins the first"]],
};
function readingChoices(mime) {
  for (const [prefix, choices] of Object.entries(READINGS)) {
    if ((mime || "").startsWith(prefix)) return choices;
  }
  return [];
}
function readingForm(doc) {
  const choices = readingChoices(doc.mime);
  if (!choices.length) return `<p class="muted">No extractor to ask for on ${esc(doc.mime || "this type")}.</p>`;
  return `
  <form class="reading-form">
    <label>Read with
      <select name="extractor">${choices.map(([v, l]) => `<option value="${v}">${esc(v)} — ${esc(l)}</option>`).join("")}</select>
    </label>
    <label class="reading-mode" data-for="vision-pages">pages
      <select name="mode"><option value="scans">the scanned ones (no text layer)</option><option value="all">every page (printed pages with notes in the margin)</option></select>
    </label>
    <label class="reading-mode" data-for="figures">figures
      <select name="mode"><option value="captioned">the ones a caption claims</option><option value="all">every image the text references, the uncaptioned ones too (decoration included)</option></select>
    </label>
    <label class="reading-mode" data-for="pymupdf4llm-ocr">language
      <input name="mode" type="text" placeholder="the host's setting (ch, en, latin, arabic, cyrillic…)" size="28">
    </label>
    <label class="reading-mode" data-for="formulas">which
      <select name="mode"><option value="new">the ones this model has not read</option><option value="again">every one, this model's earlier reading replaced</option></select>
    </label>
    <label class="reading-mode" data-for="marker">layout
      <select name="mode"><option value="fast">fast: the layout by rules, the maths by the model</option><option value="balanced">balanced: the vision model lays out too (slower)</option></select>
    </label>
    <button>Request</button>
    <span class="muted">A worker picks it up (Jobs shows what is waiting); the result replaces the text, except an image's readings, which add up.</span>
  </form>`;
}
function readingLine(r) {
  if (!r) return "";
  const when = (r.finished_at || r.at || "").replace("T", " ").slice(0, 16);
  const what = `${esc(r.extractor)}${r.mode && r.mode !== "scans" ? ` (${esc(r.mode)})` : ""}`;
  if (r.state === "requested") return `<p class="reading-line muted">reading requested: ${what} by ${esc(r.by || "?")}, ${when} — waiting for a worker <a href="#" id="reading-cancel">cancel</a></p>`;
  if (r.state === "error") return `<p class="reading-line"><span class="error">reading failed:</span> ${what} — ${esc(r.error || "")} <a href="#" id="reading-cancel" class="muted">dismiss</a></p>`;
  return `<p class="reading-line muted">read again ${when}: ${esc(r.stamp || what)} → ${esc(r.outcome || r.state)} <a href="#" id="reading-cancel">dismiss</a></p>`;
}

function metaLine(meta) {
  const bits = [];
  if (meta.creators && meta.creators.length) bits.push(meta.creators.map((c) => c.name).join(", "));
  if (meta.date) bits.push(meta.date);
  if (meta.doi) bits.push(`<a href="https://doi.org/${esc(meta.doi)}" target="_blank" rel="noopener">doi:${esc(meta.doi)}</a>`);
  if (meta.fields && meta.fields.publicationTitle) bits.push(esc(meta.fields.publicationTitle));
  if (meta.text_source) bits.push(`<span class="muted">text: ${esc(meta.text_source)}</span>`);
  if (meta.domains) bits.push(`<span class="muted" title="ontology modules this document is read against">domains: ${meta.domains.map(esc).join(", ")}</span>`);
  if (meta.promote) bits.push(`<span class="muted" title="${esc(meta.promote.reason || "")}">promoted by ${esc(meta.promote.by)}</span>`);
  if (meta.retired) bits.push(`<span class="error">retired ${esc((meta.retired.at || "").slice(0, 10))}: ${esc(meta.retired.reason || "")}${meta.retired.of ? ` of <a href="#doc/${meta.retired.of}">doc ${meta.retired.of}</a>` : ""}</span>`);
  if (meta.recaptured && meta.recaptured.length) bits.push(`<span class="muted" title="sent again with the same text">captured ${meta.recaptured.length + 1}×</span>`);
  if (meta.title_history && meta.title_history.length) {
    const former = meta.title_history[meta.title_history.length - 1].title;
    bits.push(`<span class="muted" title="${esc(meta.title_source || "")}">titled by ${esc(meta.title_source || "?")}, was “${esc(former)}”</span>`);
  }
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

// A figure chunk: the image out of the original (by the hash its text
// references), its caption, and what the vision model made of it.
function renderFigure(c, docId) {
  const d = c.data;
  const readings = (d.readings || []).map((r) => `<p class="figure-reading"><span class="muted" title="${esc(r.model)}">read by ${esc(r.model.split("@")[0])}:</span> ${esc(r.text)}</p>`).join("");
  const rest = c.text.split("\n").filter((l) => !l.startsWith("![") && !l.startsWith("*Figure, as read by")).join("\n").trim();
  return `<figure class="doc-figure plate">
    <a href="/doc/${docId}/figure/${d.ref}" target="_blank" rel="noopener"><img src="/doc/${docId}/figure/${d.ref}" alt="${esc(d.caption || "")}" loading="lazy"></a>
    ${d.caption ? `<figcaption>${esc(d.caption)}</figcaption>` : ""}
    ${rest ? md(rest) : ""}${readings}
  </figure>`;
}

// A hit or a passage that is a figure shows the figure: the reading is what
// found it, the picture is what it is about.
function figureThumb(h) {
  if (!h.figure) return "";
  const src = `/doc/${h.doc_id}/figure/${h.figure}`;
  return `<a class="hit-figure" href="${src}" target="_blank" rel="noopener"><img src="${src}" alt="" loading="lazy"></a>`;
}

// Snippets: an image reference is noise to a reader; its caption is not, and
// a reading's emphasis markers are for Markdown, not for a card of plain text.
function plainFigures(text) {
  return String(text || "").replace(/!\[([^\]\n]*)\]\(figure:[0-9a-f]+\)/g, "[figure: $1]")
    .replace(/\*(Figure|Formula), as read by ([^*:]+?):\*/g, "$1, read by $2:");
}

// A display equation: its LaTeX as the paper wrote it, the number the
// prose refers to it by, and what a model made of it underneath. No maths
// typesetting here — the LaTeX is the content, and a reading is what makes
// it findable in words.
function renderFormula(c) {
  const readings = (c.data.readings || []).map((r) =>
    `<p class="figure-reading"><span class="muted">read by ${esc(r.model)}:</span> ${esc(r.text)}</p>`).join("");
  const html = tex(c.data.latex, true);
  return `<div class="formula">
    ${c.data.number ? `<span class="formula-number">(${esc(c.data.number)})</span>` : ""}
    ${html ? `<div class="formula-tex">${html}</div>` : ""}
    <pre class="formula-latex"${html ? " hidden" : ""}>${esc(c.data.latex)}</pre>
    ${html ? `<button type="button" class="linkish formula-source" title="the LaTeX as the parser wrote it">LaTeX</button>` : ""}
  </div>${readings}`;
}

// Maths is typeset by KaTeX (vendored) from what a parser wrote as LaTeX. A
// formula chunk always, with the LaTeX a click away; the $…$ inside prose
// only where a document carries real maths (a formula chunk, or marker's
// text) and in an answer; never a search snippet, which is cut and marked.
// What KaTeX cannot parse stays as written, in the text's own colour.
function tex(latex, display) {
  if (typeof katex === "undefined") return null;
  try {
    return katex.renderToString(latex, { displayMode: display, throwOnError: true });
  } catch (err) {
    return null;
  }
}
function typesetMaths(root) {
  if (typeof katex === "undefined" || !root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => {
      const p = n.parentElement;
      if (!p || p.closest("pre, code, a, .katex, .formula-reading")) return NodeFilter.FILTER_REJECT;
      return /\$|\\[([]/.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    const frag = document.createDocumentFragment();
    let at = 0;
    for (const s of mathSpans(text)) {
      const html = tex(s.latex, s.display);
      if (html === null) continue;
      if (s.start > at) frag.appendChild(document.createTextNode(text.slice(at, s.start)));
      const span = document.createElement("span");
      span.className = s.display ? "tex tex-display" : "tex";
      span.innerHTML = html;
      frag.appendChild(span);
      at = s.end;
    }
    if (at === 0) continue;
    if (at < text.length) frag.appendChild(document.createTextNode(text.slice(at)));
    node.parentNode.replaceChild(frag, node);
  }
}
function hasMaths(doc, chunks) {
  return (chunks || []).some((c) => c.kind === "formula") || String((doc.meta || {}).text_source || "").startsWith("marker");
}

function renderChunk(c, highlight, docId) {
  const cls = "chunk kind-" + (c.kind || "text") + (c.chunk_id === highlight ? " highlight" : "");
  let body;
  if (c.kind === "table" && c.data && c.data.header && c.data.header.length) {
    const caption = c.text.split("\n").filter((l) => !l.trim().startsWith("|")).join("\n").trim();
    body = (caption ? md(caption) : "") + renderTable(c.data);
  } else if (c.kind === "figure" && c.data && c.data.ref) {
    body = renderFigure(c, docId);
  } else if (c.kind === "formula" && c.data && c.data.latex) {
    body = renderFormula(c);
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
  view.classList.add("wide");
  loading(`Loading document ${esc(id)}…`);
  let doc, chunks;
  try {
    [doc, chunks] = await Promise.all([api(`/get/${id}`, { max_chars: 0 }), api(`/doc/${id}/chunks`)]);
  } catch (err) {
    view.innerHTML = `<p class="error">${esc(err.message)}</p>`;
    return;
  }
  const meta = doc.meta || {};
  let highlight = p.chunk ? Number(p.chunk) : null;
  if (!highlight && p.find) highlight = locateChunk(chunks, p.find);
  const firstPage = highlight ? (chunks.find((c) => c.chunk_id === highlight) || {}).page : null;
  const pageMeta = meta.page || null;
  view.innerHTML = `
  <header class="doc-head">
    <h1>${pageMeta ? `<span class="kind-pill">${esc(pageMeta.kind)}</span> ` : ""}${esc(doc.title || "(untitled)")}</h1>
    <div class="doc-meta">${metaLine(meta)}${pageMeta ? ` · revision ${pageMeta.revision} by ${esc(pageMeta.author || "?")}` : ""}</div>
    ${tags(meta)}
    <div class="doc-actions">
      ${pageMeta ? `<a href="#" id="page-edit">edit page</a>` : `<a href="#" id="add-note">add a note</a>`}
      <a href="${originalHref(doc.id, firstPage)}" target="_blank" rel="noopener">open original ↗</a>
      ${pageMeta ? "" : (meta.promote ? `<a href="#" id="unpromote">un-promote</a>` : `<a href="#" id="promote" title="flag for the expensive model's pass">promote</a>`)}
      <a href="#" id="domains" title="which ontology modules this document is read against">domains…</a>
      ${pageMeta ? "" : `<a href="#" id="reading" title="run a named extractor on this document: the vision model over scanned pages, a second reading of an image, OCR, Docling">read again…</a>`}
      ${meta.retired ? `<a href="#" id="unretire" title="back into search and the graph">un-retire</a>` : `<a href="#" id="retire" title="out of search and the graph; row and file stay">retire…</a>`}
      <a href="/doc/${doc.id}/text" target="_blank" rel="noopener">raw text ↗</a>
      <span class="muted">${esc(doc.mime || "")} · ${chunks.length} chunks · ${(doc.text_len || 0).toLocaleString()} chars · doc ${doc.id}</span>
    </div>
    ${readingLine(meta.reading)}
    <div id="reading-form" hidden></div>
    <div id="page-editor"></div>
  </header>
  ${rule()}
  <div class="doc-layout">
    <aside class="doc-outline">${outline(chunks)}</aside>
    <div class="doc-body">${(doc.mime || "").startsWith("image/") ? `<a href="${originalHref(doc.id)}" target="_blank" rel="noopener"><img class="doc-image" src="${originalHref(doc.id)}" alt="${esc(doc.title || "")}"></a>` : ""}${chunks.length ? chunks.map((c) => renderChunk(c, highlight, doc.id)).join("") : `<p class="muted">No text yet.${(doc.mime || "").startsWith("image/") ? " Describe it with <code>parse_pending.py --ids " + doc.id + " --extractor claude-vision</code>." : ""}</p>`}</div>
    <aside class="doc-context" id="doc-context"><p class="muted">Loading context…</p></aside>
  </div>`;
  if (hasMaths(doc, chunks)) typesetMaths(view.querySelector(".doc-body"));
  const loadContext = async (domain) => {
    try {
      await modules();
      const ctx = await api(`/doc/${id}/context`, domain ? { domain } : {});
      ctx.domain = domain || "";
      document.getElementById("doc-context").innerHTML = renderContext(ctx);
      bindProjectForm(doc.id);
      const sel = document.querySelector("#doc-context select[name=similar-domain]");
      if (sel) sel.addEventListener("change", () => loadContext(sel.value));
    } catch (err) {
      document.getElementById("doc-context").innerHTML = `<p class="error">${esc(err.message)}</p>`;
    }
  };
  loadContext(p.domain || "");
  if (pageMeta) {
    document.getElementById("page-edit").addEventListener("click", (e) => { e.preventDefault(); openEditor(pageMeta.slug); });
    if (p.edit) openEditor(pageMeta.slug);
  }
  const retireLink = document.getElementById("retire");
  if (retireLink) retireLink.addEventListener("click", async (e) => {
    e.preventDefault();
    const reason = prompt("Retire this document (out of search and the graph; the file stays). Reason:", "retired by hand");
    if (reason === null) return;
    try { await post(`/doc/${doc.id}/retire`, { reason }); render(); } catch (err) { setStatus(err.message); }
  });
  const unretireLink = document.getElementById("unretire");
  if (unretireLink) unretireLink.addEventListener("click", async (e) => {
    e.preventDefault();
    await fetch(`/doc/${doc.id}/retire`, { method: "DELETE" });
    render();
  });
  const rd = document.getElementById("reading");
  if (rd) rd.addEventListener("click", (e) => {
    e.preventDefault();
    const box = document.getElementById("reading-form");
    if (!box.hidden) { box.hidden = true; return; }
    box.innerHTML = readingForm(doc);
    box.hidden = false;
    const form = box.querySelector("form");
    const pick = form.extractor;
    const modes = [...form.querySelectorAll(".reading-mode")];
    const showMode = () => { modes.forEach((m) => { m.hidden = m.dataset.for !== pick.value; }); };
    pick.addEventListener("change", showMode);
    showMode();
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const chosen = modes.find((m) => m.dataset.for === pick.value);
      const field = chosen && chosen.querySelector("select, input");
      const body = { extractor: pick.value, mode: field && field.value.trim() ? field.value.trim() : null };
      try { await post(`/doc/${doc.id}/reading`, body); render({ keepScroll: true }); } catch (err) { setStatus(err.message); }
    });
  });
  const cancelReading = document.getElementById("reading-cancel");
  if (cancelReading) cancelReading.addEventListener("click", async (e) => {
    e.preventDefault();
    await fetch(`/doc/${doc.id}/reading`, { method: "DELETE" });
    render({ keepScroll: true });
  });
  document.getElementById("domains").addEventListener("click", async (e) => {
    e.preventDefault();
    const cur = await api(`/doc/${doc.id}/domains`);
    const answer = prompt(`Domains for this document (comma-separated; empty = every module).\nModules: ${cur.modules.join(", ")}`, (cur.domains || []).join(", "));
    if (answer === null) return;
    const domains = answer.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      const res = await fetch(`/doc/${doc.id}/domains`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ domains: domains.length ? domains : null }) });
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      render();
    } catch (err) { setStatus(err.message); }
  });
  if (!pageMeta) {
    const pr = document.getElementById("promote");
    if (pr) pr.addEventListener("click", async (e) => {
      e.preventDefault();
      const reason = prompt("Why does this document deserve the expensive pass? (optional)") || null;
      try { await post(`/doc/${doc.id}/promote`, { reason }); render(); } catch (err) { setStatus(err.message); }
    });
    const un = document.getElementById("unpromote");
    if (un) un.addEventListener("click", async (e) => {
      e.preventDefault();
      await fetch(`/doc/${doc.id}/promote`, { method: "DELETE" });
      render();
    });
    document.getElementById("add-note").addEventListener("click", async (e) => {
      e.preventDefault();
      const slug = `note-${doc.id}-${Date.now().toString(36)}`;
      try {
        const r = await put(`/page/${slug}`, {
          text: `# Note on ${doc.title || "document " + doc.id}\n\n`,
          title: `Note on ${(doc.title || "").slice(0, 80)}`,
          kind: "addendum",
          annotates: [doc.id],
        });
        go("doc", String(r.doc_id), { edit: 1 });
      } catch (err) { setStatus(err.message); }
    });
  }
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

async function put(path, body) {
  setStatus("…");
  const res = await fetch(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  setStatus("");
  if (res.status === 401) { askForToken(); throw new Error("access token required"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

// The page editor: the current Markdown in a textarea, saved as a new
// revision; the revision list with links to earlier texts.
async function openEditor(slug) {
  const box = document.getElementById("page-editor");
  if (!box || box.dataset.open) return;
  box.dataset.open = "1";
  let page;
  try { page = await api(`/page/${slug}`); } catch (err) { box.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  box.innerHTML = `
    <div class="page-editor">
      <textarea id="page-text">${esc(page.text)}</textarea>
      <div class="row">
        <input id="page-title" type="text" value="${esc(page.title || "")}" placeholder="title">
        <input id="page-note" type="text" placeholder="what changed (optional)">
        <button id="page-save">Save revision ${page.revision + 1}</button>
        <button id="page-cancel" class="secondary" type="button">Cancel</button>
        <span id="page-msg" class="muted"></span>
      </div>
      <div class="muted" style="margin-top:.4rem">Revisions: ${page.revisions.map((r) => `<a href="/page/${esc(slug)}/revision/${r.revision}" target="_blank" rel="noopener" title="${esc(r.note || "")}">r${r.revision} ${esc(r.author)} ${esc((r.created_at || "").slice(0, 10))}</a>`).join(" · ")}</div>
    </div>`;
  document.getElementById("page-cancel").addEventListener("click", () => { box.innerHTML = ""; delete box.dataset.open; });
  document.getElementById("page-save").addEventListener("click", async () => {
    const msg = document.getElementById("page-msg");
    try {
      await put(`/page/${slug}`, {
        text: document.getElementById("page-text").value,
        title: document.getElementById("page-title").value || null,
        note: document.getElementById("page-note").value || null,
        author: "human",
      });
      render();
    } catch (err) { msg.textContent = err.message; }
  });
}

async function bindProjectForm(docId) {
  const form = document.getElementById("project-form");
  if (!form) return;
  let projects = [];
  try { projects = await api("/pages", { kind: "project" }); } catch (_) { return; }
  if (!projects.length) { form.innerHTML = `<span class="muted">no project pages yet</span>`; return; }
  form.innerHTML = `<select name="slug">${projects.map((pr) => `<option value="${esc(pr.slug)}">${esc(pr.title)}</option>`).join("")}</select> <button>add to project</button> <span class="muted msg"></span>`;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const slug = form.querySelector("select").value;
    try {
      const r = await post(`/project/${slug}/members`, { doc_id: docId });
      form.querySelector(".msg").textContent = r.existing ? "already a member" : "added";
      const ctx = await api(`/doc/${docId}/context`);
      document.getElementById("doc-context").innerHTML = renderContext(ctx);
      bindProjectForm(docId);
    } catch (err) { form.querySelector(".msg").textContent = err.message; }
  });
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
  parts.push(list("Notes on this document", ctx.notes || [], (d) => docLink(d, esc(d.kind))));
  if (ctx.page && ctx.page.kind === "project") {
    parts.push(list("In this project", ctx.members || [], (d) => d.doc_id ? docLink(d, esc(d.type)) : `<li>${esc(d.title)} <span class="muted">${esc(d.type)}</span></li>`));
  }
  parts.push(`<h3>Similar documents <span class="ctx-domain">${domainSelect(ctx.domain || "", "similar-domain", "similar documents within one module")}</span></h3>`
    + (ctx.similar.length ? `<ul>${ctx.similar.map((d) => docLink(d, `${d.score.toFixed(2)}`)).join("")}</ul>` : `<p class="muted">none in this module</p>`));
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
  if (!(ctx.page && ctx.page.kind === "project")) parts.push(`<h3>Projects</h3><form id="project-form" class="search-form"><span class="muted">…</span></form>`);
  const body = parts.filter(Boolean).join("");
  return body || `<p class="muted">Nothing connects this document yet: no extraction, no citations, no neighbours.</p>`;
}

// ---------------------------------------------------------------- browse

async function viewBrowse(p) {
  const limit = Number(p.limit || 50);
  const offset = Number(p.offset || 0);
  await modules();
  view.innerHTML = `
  <form id="browse-form" class="search-form">
    <input name="title" type="search" value="${esc(p.title || "")}" placeholder="title contains…">
    <input name="source" type="text" value="${esc(p.source || "")}" placeholder="source (zotero)">
    <input name="mime" type="text" value="${esc(p.mime || "")}" placeholder="mime (application/pdf)">
    ${domainSelect(p.domain || "")}
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
    const res = await api("/documents", { limit, offset, title: p.title, source: p.source, mime: p.mime, domain: p.domain || undefined });
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
// The neighbourhood of an entity on a canvas. Nodes are (type, name);
// edges come from GET /traverse (hops=1) and a click on a node fetches its
// own neighbourhood and merges it in, forty neighbours at a time. The
// layout is a small spring model animated a few steps per frame; no
// library (the previous SVG version rebuilt the whole picture on every
// change and froze on a hub with five hundred neighbours).

const TYPE_COLORS = {
  paper: "#2f5d8a", author: "#7a5c1e", concept: "#4b7a45", method: "#6a4b7a",
  claim: "#a0522d", tool: "#3b7a7a", venue: "#8a6d2f", dataset: "#5a5a8a",
  person: "#7a5c1e", organization: "#8a4b2f", document: "#2f5d8a", place: "#3f6b3f",
  event: "#7a3f6b", work: "#5a3f8a", page: "#2f7a8a", project: "#2f8a5a",
};
const nodeKey = (name, type) => type + "|" + name;
const typeColor = (t) => TYPE_COLORS[t] || "#888";

class ForceGraph {
  // A canvas, not an SVG: five hundred nodes and their labels redraw in a
  // millisecond or two, where an SVG of the same rebuilt through innerHTML
  // froze the page. The simulation is the same small spring model, but run
  // a few steps per animation frame with a decaying alpha instead of three
  // hundred at once, so the page answers while the layout settles.
  constructor(canvas, onSelect) {
    ForceGraph.live = this;  // the one on screen, for a theme change
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.onSelect = onSelect;
    this.nodes = new Map();
    this.edges = new Map();
    this.links = [];  // co-occurrence links of the overview: {a, b, weight}
    this.selected = null;
    this.hover = null;
    this.view = { x: 0, y: 0, k: 1 };  // screen = world * k + (x, y)
    this.alpha = 0;
    this.frame = null;
    this.fitPending = false;
    this.cap = 40;  // neighbours drawn per expansion; the rest wait in the panel
    this.held = false;  // the person panned or zoomed: a resize keeps their view
    this.resize();
    this.bind();
    // a resize before anyone touched the view fits again (the canvas may
    // have had no size yet when the layout first fitted)
    this.observer = new ResizeObserver(() => { this.resize(); if (this.held) this.draw(); else this.fit(); });
    this.observer.observe(canvas);
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth || 900, h = this.canvas.clientHeight || 600;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = w; this.h = h;
  }

  node(name, type, near) {
    const key = nodeKey(name, type);
    let n = this.nodes.get(key);
    if (!n) {
      const a = Math.random() * Math.PI * 2;
      const r = 60 + Math.random() * 60;
      n = { key, name, type, expanded: false, degree: 0, hidden: [], by: near ? near.key : null,
            x: (near ? near.x : 0) + Math.cos(a) * r, y: (near ? near.y : 0) + Math.sin(a) * r, vx: 0, vy: 0 };
      this.nodes.set(key, n);
    }
    return n;
  }

  // Merge edges in. Around a node being expanded, only its `cap` best
  // connected neighbours are drawn; the rest stay on the node as `hidden`
  // until asked for, because a hub with five hundred neighbours is a
  // hairball at any speed.
  merge(edgeList, aroundKey, nodeList, linkList) {
    const near = aroundKey ? this.nodes.get(aroundKey) : null;
    for (const n of nodeList || []) this.node(n.name, n.type, null).degree = n.degree || 0;
    for (const l of linkList || []) {
      this.links.push({ a: this.node(l.a, l.a_type, null).key, b: this.node(l.b, l.b_type, null).key, weight: l.weight });
    }
    let fresh = edgeList.filter((e) => !this.edges.has(e.edge_id));
    if (near && fresh.length > this.cap) {
      const other = (e) => nodeKey(e.src, e.src_type) === aroundKey ? nodeKey(e.dst, e.dst_type) : nodeKey(e.src, e.src_type);
      const degree = new Map();
      for (const e of fresh) degree.set(other(e), (degree.get(other(e)) || 0) + 1);
      const known = new Set(this.nodes.keys());
      // neighbours already on the map first, then the best connected
      fresh.sort((p, q) => (known.has(other(q)) - known.has(other(p))) || ((degree.get(other(q)) || 0) - (degree.get(other(p)) || 0)));
      const kept = new Set();
      const shown = [];
      for (const e of fresh) {
        const k = other(e);
        if (kept.size >= this.cap && !kept.has(k) && !known.has(k)) { near.hidden.push(e); continue; }
        kept.add(k); shown.push(e);
      }
      fresh = shown;
    }
    for (const e of fresh) {
      const s = this.node(e.src, e.src_type, near);
      const t = this.node(e.dst, e.dst_type, near);
      s.degree++; t.degree++;
      this.edges.set(e.edge_id, { ...e, s: s.key, t: t.key, by: aroundKey });
    }
    this.fitPending = true;
    this.kick(1);
  }

  showHidden(key) {
    const n = this.nodes.get(key);
    if (!n || !n.hidden.length) return;
    const rest = n.hidden; n.hidden = [];
    const cap = this.cap; this.cap = Infinity;
    this.merge(rest, key);
    this.cap = cap;
    this.select(key);
  }

  async expand(key) {
    const n = this.nodes.get(key);
    n.expanded = true;
    const edges = await api("/traverse", { entity: n.name, hops: 1 });
    if (!n.expanded) return;  // folded while the fetch was in flight
    this.merge(edges, key);
    this.select(key);
  }

  // Undo an expansion: its edges go, and so does everything only they
  // justified, with whatever those nodes had opened in turn. Seeds (the
  // overview's hubs, the entity the view started on) always stay.
  fold(key) {
    const n = this.nodes.get(key);
    if (!n) return;
    this.unfold(key);
    const kept = new Set(this.links.flatMap((l) => [l.a, l.b]));
    for (const e of this.edges.values()) { kept.add(e.s); kept.add(e.t); }
    for (const m of [...this.nodes.values()]) {
      if (m.by !== null && m.key !== key && !kept.has(m.key)) this.nodes.delete(m.key);
    }
    if (this.hover && !this.nodes.has(this.hover)) this.hover = null;
    this.kick(0.5);
    this.select(key);
  }

  unfold(key) {
    const n = this.nodes.get(key);
    n.expanded = false;
    n.hidden = [];
    for (const [id, e] of [...this.edges]) {
      if (e.by !== key) continue;
      this.edges.delete(id);
      for (const k of [e.s, e.t]) { const m = this.nodes.get(k); if (m) m.degree = Math.max(0, m.degree - 1); }
    }
    for (const m of this.nodes.values()) {
      if (m.by === key && m.key !== key && m.expanded) this.unfold(m.key);
    }
  }

  kick(alpha) {
    this.alpha = Math.max(this.alpha, alpha);
    if (!this.frame) this.frame = requestAnimationFrame(() => this.tick());
  }

  tick() {
    this.frame = null;
    for (let i = 0; i < 3; i++) this.step();
    this.alpha *= 0.97;
    if (this.fitPending && this.alpha < 0.4) { this.fitPending = false; this.fit(); }
    this.draw();
    if (this.alpha > 0.015) this.frame = requestAnimationFrame(() => this.tick());
    else this.alpha = 0;
  }

  step() {
    const nodes = [...this.nodes.values()];
    const alpha = this.alpha;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy || 1;
        if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
        const f = Math.min(6000 / d2, 40) * alpha;
        const d = Math.sqrt(d2);
        a.vx -= dx / d * f; a.vy -= dy / d * f;
        b.vx += dx / d * f; b.vy += dy / d * f;
      }
    }
    for (const e of this.edges.values()) {
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
      if (n.pinned) { n.vx = 0; n.vy = 0; continue; }
      n.x += n.vx; n.y += n.vy; n.vx *= 0.5; n.vy *= 0.5;
    }
  }

  fit() {
    const nodes = [...this.nodes.values()];
    if (!nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const pad = 70;
    const k = Math.min(3, Math.max(0.05, Math.min(this.w / (x1 - x0 + 2 * pad), this.h / (y1 - y0 + 2 * pad))));
    this.view = { k, x: this.w / 2 - (x0 + x1) / 2 * k, y: this.h / 2 - (y0 + y1) / 2 * k };
    this.draw();
  }

  toScreen(n) { return [n.x * this.view.k + this.view.x, n.y * this.view.k + this.view.y]; }
  radius(n) { return 5 + Math.min(14, Math.sqrt(n.degree) * 2.2); }

  draw() {
    const ctx = this.ctx, v = this.view;
    const css = getComputedStyle(document.documentElement);
    const col = (name, fallback) => (css.getPropertyValue(name) || fallback).trim();
    const muted = col("--muted", "#777"), line = col("--line", "#ddd"), fg = col("--fg", "#222"),
          accent = col("--accent", "#c33"), panel = col("--panel", "#fff");
    ctx.clearRect(0, 0, this.w, this.h);
    // co-occurrence links, faintest
    ctx.lineCap = "round";
    for (const l of this.links) {
      const a = this.nodes.get(l.a), b = this.nodes.get(l.b);
      if (!a || !b) continue;
      const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
      ctx.strokeStyle = line; ctx.globalAlpha = 0.9;
      ctx.lineWidth = Math.min(6, 0.6 + l.weight * 0.5);
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    // edges; the selected node's edges on top, labelled
    const selectedEdges = [];
    for (const e of this.edges.values()) {
      if (e.s === this.selected || e.t === this.selected) { selectedEdges.push(e); continue; }
      this.drawEdge(ctx, e, muted, 1, 0.55);
    }
    for (const e of selectedEdges) this.drawEdge(ctx, e, accent, 2, 1);
    // nodes: the busiest get a label always, the rest when the map is small
    const nodes = [...this.nodes.values()];
    const labelled = new Set(nodes.length <= 140 ? nodes.map((n) => n.key)
      : nodes.slice().sort((p, q) => q.degree - p.degree).slice(0, 40).map((n) => n.key));
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (const n of nodes) {
      const [x, y] = this.toScreen(n);
      const r = this.radius(n) * Math.min(1.4, Math.max(0.6, v.k));
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = typeColor(n.type); ctx.fill();
      ctx.lineWidth = n.key === this.selected ? 3 : 1.5;
      ctx.strokeStyle = n.key === this.selected ? accent : (n.expanded ? fg : panel);
      ctx.stroke();
      if (labelled.has(n.key) || n.key === this.selected || n.key === this.hover) {
        const label = n.name.length > 38 ? n.name.slice(0, 36) + "…" : n.name;
        ctx.fillStyle = fg;
        ctx.fillText(label, x + r + 3, y);
      }
    }
    for (const e of selectedEdges) {
      const a = this.nodes.get(e.s), b = this.nodes.get(e.t);
      const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
      ctx.fillStyle = muted; ctx.font = "9px system-ui, sans-serif"; ctx.textAlign = "center";
      ctx.fillText(e.rel, (ax + bx) / 2, (ay + by) / 2 - 5);
      ctx.textAlign = "start"; ctx.font = "11px system-ui, sans-serif";
    }
  }

  drawEdge(ctx, e, colour, width, alpha) {
    const a = this.nodes.get(e.s), b = this.nodes.get(e.t);
    const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
    ctx.strokeStyle = colour; ctx.lineWidth = width; ctx.globalAlpha = alpha;
    ctx.setLineDash(e.confidence === "INFERRED" ? [4, 3] : e.confidence === "AMBIGUOUS" ? [1, 3] : []);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
  }

  hit(px, py) {
    let best = null, bestD = Infinity;
    for (const n of this.nodes.values()) {
      const [x, y] = this.toScreen(n);
      const r = this.radius(n) * Math.min(1.4, Math.max(0.6, this.view.k)) + 4;
      const d = Math.hypot(px - x, py - y);
      if (d <= r && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  edgeHit(px, py) {
    let best = null, bestD = 5;
    for (const e of this.edges.values()) {
      const a = this.nodes.get(e.s), b = this.nodes.get(e.t);
      const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
      const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
      const d = Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
      if (d < bestD) { best = e; bestD = d; }
    }
    return best;
  }

  bind() {
    const c = this.canvas;
    const at = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    let drag = null;  // {node} or {pan: [x, y, viewX, viewY]}
    let moved = false;
    c.addEventListener("mousedown", (e) => {
      const [px, py] = at(e);
      const n = this.hit(px, py);
      moved = false;
      if (n) { drag = { node: n }; n.pinned = true; }
      else drag = { pan: [px, py, this.view.x, this.view.y] };
      c.classList.add("dragging");
    });
    window.addEventListener("mousemove", (e) => {
      const [px, py] = at(e);
      if (drag && drag.node) {
        moved = true;
        drag.node.x = (px - this.view.x) / this.view.k; drag.node.y = (py - this.view.y) / this.view.k;
        this.kick(0.3);
      } else if (drag && drag.pan) {
        moved = true;
        this.held = true;
        this.view.x = drag.pan[2] + (px - drag.pan[0]); this.view.y = drag.pan[3] + (py - drag.pan[1]);
        this.draw();
      } else if (e.target === c) {
        const n = this.hit(px, py);
        const key = n ? n.key : null;
        if (key !== this.hover) { this.hover = key; this.draw(); }
        if (n) c.title = `${n.name} (${n.type}, ${n.degree} edges here${n.hidden.length ? `, ${n.hidden.length} neighbours not drawn` : ""})`;
        else { const ed = this.edgeHit(px, py); c.title = ed ? `${ed.src} ${ed.rel} ${ed.dst} (${ed.confidence})${ed.evidence ? "\n" + ed.evidence : ""}` : ""; }
        c.style.cursor = n ? "pointer" : "grab";
      }
    });
    window.addEventListener("mouseup", () => {
      if (drag && drag.node) drag.node.pinned = false;
      drag = null; c.classList.remove("dragging");
    });
    c.addEventListener("click", (e) => {
      if (moved) return;
      const n = this.hit(...at(e));
      if (!n) return;
      if (n.expanded && n.key === this.selected) { this.fold(n.key); return; }
      this.select(n.key);
      if (!n.expanded) this.expand(n.key);
    });
    c.addEventListener("dblclick", (e) => {
      const n = this.hit(...at(e));
      if (n) go("graph", "", { entity: n.name });
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [px, py] = at(e);
      const z = e.deltaY > 0 ? 1 / 1.15 : 1.15;
      const k = Math.min(6, Math.max(0.03, this.view.k * z));
      this.view = { k, x: px - (px - this.view.x) * (k / this.view.k), y: py - (py - this.view.y) * (k / this.view.k) };
      this.held = true;
      this.draw();
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
}

function graphPanel(node, edges) {
  if (!node) return `<p class="muted">Click a node to see its edges; the first click also expands it, a second click on the selected node folds it again.</p>`;
  const rows = edges.map((e) => {
    const out = e.s === node.key;
    const other = out ? e.dst : e.src;
    const otherType = out ? e.dst_type : e.src_type;
    return `<li>${out ? "" : `<b>${esc(other)}</b> <span class="muted">${esc(otherType)}</span> `}<span class="rel">${out ? "" : "→ "}${esc(e.rel)}${out ? " →" : ""}</span> ${out ? `<b>${esc(other)}</b> <span class="muted">${esc(otherType)}</span>` : ""}
      <span class="muted">· ${esc(e.confidence)}${e.producer ? ` · ${esc(e.producer)}` : ""}${e.source_doc ? ` · <a href="#doc/${e.source_doc}${e.evidence ? `?find=${encodeURIComponent(String(e.evidence).slice(0, 120))}` : ""}" title="${esc(e.evidence || "")}">doc ${e.source_doc}</a>` : ""}</span>
      ${e.evidence ? `<span class="ev">“${esc(e.evidence)}”</span>` : ""}</li>`;
  });
  const hidden = (node.hidden || []).length;
  const more = hidden ? ` · ${hidden} more neighbours <a href="#" id="graph-show-all">draw them</a>` : "";
  const fold = node.expanded ? ` · <a href="#" id="graph-fold">fold</a>` : "";
  return `<h2>${esc(node.name)}</h2><div class="muted">${esc(node.type)} · ${edges.length} edges drawn${more}${fold}</div><ul>${rows.join("")}</ul>`;
}

// The panel follows the selection; its links act on the graph.
function graphPanelUpdater(panel, graphOf) {
  return (node, edges) => {
    panel.innerHTML = graphPanel(node, edges);
    const on = (id, act) => {
      const el = panel.querySelector(id);
      if (el) el.addEventListener("click", (e) => { e.preventDefault(); act(graphOf()); });
    };
    on("#graph-show-all", (g) => g.showHidden(node.key));
    on("#graph-fold", (g) => g.fold(node.key));
  };
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
          <span class="muted">· faint lines: hubs that share documents · click a node to expand it, click it again to fold it, double-click to open its neighbourhood</span>
          <button type="button" id="graph-fit" class="secondary">fit</button>
        </div>
        <div class="legend">${legend}</div>
        <div class="graph-layout">
          <div class="graph-canvas plate"><canvas></canvas></div>
          <aside class="graph-panel" id="graph-panel">${graphPanel(null, [])}</aside>
        </div>`;
      const panel = document.getElementById("graph-panel");
      const graph = new ForceGraph(out.querySelector("canvas"), graphPanelUpdater(panel, () => graph));
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
        <span class="muted">· click a node to expand it, click it again to fold it · drag to pan, wheel to zoom · dashed edges are inferred or ambiguous</span>
        <button type="button" id="graph-fit" class="secondary">fit</button>
      </div>
      <div class="legend">${legend}</div>
      <div class="graph-layout">
        <div class="graph-canvas plate"><canvas></canvas></div>
        <aside class="graph-panel" id="graph-panel">${graphPanel(null, [])}</aside>
      </div>`;
    const panel = document.getElementById("graph-panel");
    const graph = new ForceGraph(out.querySelector("canvas"), graphPanelUpdater(panel, () => graph));
    document.getElementById("graph-fit").addEventListener("click", () => graph.fit());
    const edges = await api("/traverse", { entity, hops: 1 });
    if (!edges.length) { panel.innerHTML = `<p class="muted">No edges for this entity.</p>`; return; }
    const first = edges.find((e) => e.src === entity || e.dst === entity);
    if (!first) { graph.merge(edges, null); return; }
    const start = graph.node(entity, first.src === entity ? first.src_type : first.dst_type, null);
    start.expanded = true;
    graph.merge(edges, start.key);
    graph.select(start.key);
  } catch (err) {
    out.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

// ----------------------------------------------------------------- pages
// The wiki: notes on documents, project threads, topic pages. Each is a
// document, so a row opens the document view with its editor.

async function viewPages(p) {
  view.innerHTML = `
  <form id="new-page" class="search-form">
    <input name="title" type="text" placeholder="new page title…" required>
    <select name="kind">
      <option value="topic">topic</option>
      <option value="synthesis">synthesis</option>
      <option value="project">project</option>
    </select>
    <button>Create</button>
    <span id="page-create-msg" class="error"></span>
  </form>
  <div id="pages-list">${listPlaceholder("pages-list")}</div>`;
  document.getElementById("new-page").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    const slug = data.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80);
    try {
      const r = await put(`/page/${slug}`, {
        text: data.kind === "project"
          ? `# ${data.title}\n\n## Status\n\n## Open questions\n\n## Log\n\n`
          : `# ${data.title}\n\n`,
        title: data.title,
        kind: data.kind,
      });
      go("doc", String(r.doc_id), { edit: 1 });
    } catch (err) { document.getElementById("page-create-msg").textContent = err.message; }
  });
  const list = document.getElementById("pages-list");
  try {
    const pages = await api("/pages", { kind: p.kind });
    if (!pages.length) { list.innerHTML = `<p class="muted">No pages yet. Create a topic or project page above, or add a note from any document.</p>`; return; }
    const groups = { project: "Projects", synthesis: "Syntheses", topic: "Topics", addendum: "Notes on documents" };
    list.innerHTML = Object.entries(groups).map(([kind, label]) => {
      const rows = pages.filter((pg) => pg.kind === kind);
      if (!rows.length) return "";
      return `<h2 style="font-size:1rem;margin:1rem 0 .3rem">${label}</h2>
        <table class="doc-list page-list"><tbody>${rows.map((pg) => `<tr>
          <td><a href="#doc/${pg.doc_id}">${esc(pg.title || pg.slug)}</a></td>
          <td class="muted">${esc(pg.slug)}</td>
          <td class="muted">r${pg.revision} · ${esc(pg.author || "")}</td>
          <td class="muted">${esc((pg.updated_at || "").slice(0, 16).replace("T", " "))}</td>
        </tr>`).join("")}</tbody></table>`;
    }).join("");
  } catch (err) {
    list.innerHTML = `<p class="error">${esc(err.message)}</p>`;
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

// A POST answered line by line (/ask with stream): each JSON line goes
// to onEvent as it arrives; the promise settles when the door is done.
async function postLines(path, body, onEvent) {
  setStatus("…");
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (res.status === 401) { setStatus(""); askForToken(); throw new Error("access token required"); }
  if (!res.ok) {
    setStatus("");
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || res.statusText);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let rest = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    rest += decoder.decode(value, { stream: true });
    const lines = rest.split("\n");
    rest = lines.pop();
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line));
  }
  if (rest.trim()) onEvent(JSON.parse(rest));
  setStatus("");
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
    <div class="review-meta">#${it.id} · ${esc(it.reason)}${it.source_doc ? ` · <a href="#doc/${it.source_doc}${it.evidence ? `?find=${encodeURIComponent(String(it.evidence).slice(0, 120))}` : ""}">doc ${it.source_doc}</a>` : ""}${it.evidence ? ` · <i>“${esc(it.evidence)}”</i>` : ""}</div>
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
  <div id="review-list">${listPlaceholder("review-list")}</div>`;
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

// ------------------------------------------------------------------- ask

// A question in, an answer with numbered citations out. The door gathers
// passages and graph facts; which model answers is the host's setting
// (/ask/config), overridable per question. "bundle only" shows the
// context without a model, which is what an MCP client gets.
let askConfig = null;

// A conversation laid out like a chat: the turns in a thread, the composer
// at its foot, and the sources of the selected turn in a column beside it
// (a [n] in an answer points at its card there; the card opens the
// document). The turns live in this tab's sessionStorage: a follow-up
// sends the earlier ones along (the door searches in their neighbourhood
// and the model sees them), "New ask" starts over, and a reload shows the
// conversation instead of asking again.
const ASK_KEY = "prax.ask";

function askSession() {
  try { return JSON.parse(sessionStorage.getItem(ASK_KEY) || "null") || { turns: [] }; }
  catch (_) { return { turns: [] }; }
}
function saveAskSession(turns) {
  const kept = turns.filter((t) => !t.pending && !t.error);
  try { sessionStorage.setItem(ASK_KEY, JSON.stringify({ turns: kept })); } catch (_) { /* full or blocked */ }
}
function clearAskSession() {
  try { sessionStorage.removeItem(ASK_KEY); } catch (_) { /* nothing to clear */ }
}

// The surfing budgets a question may set: how many steps the model takes
// before it answers (0: one shot from the first search) and how much it
// may read, in tokens, within what the chosen model's context holds.
function readingBounds(backend) {
  const reading = (askConfig && askConfig.reading) || {};
  const name = backend || (askConfig && askConfig.default) || "";
  return reading[name] || { default: 4000, max: 6000 };
}

function renderComposer(p) {
  const backend = p.backend || "";
  const dflt = askConfig ? askConfig.default : "none";
  const names = (askConfig && askConfig.models) || [];
  const steps = (askConfig && askConfig.steps) || { default: 8, max: 20 };
  const reading = readingBounds(backend);
  return `
  <form id="ask-form" class="composer" autocomplete="off">
    <textarea name="question" rows="1" placeholder="ask the library…" aria-label="question" autofocus>${esc(p.question || "")}</textarea>
    <button class="composer-send" title="ask (Enter; Shift+Enter for a new line)">Ask</button>
    <div class="composer-opts">
      <select name="backend" title="which model answers (prax.yaml)">
        <option value="" ${backend === "" ? "selected" : ""}>default (${esc(dflt)})</option>
        ${names.map((n) => `<option value="${esc(n)}" ${backend === n ? "selected" : ""}>${esc(n)}</option>`).join("")}
        <option value="none" ${backend === "none" ? "selected" : ""}>bundle only (no model)</option>
      </select>
      <select name="doctype" title="document type">
        <option value="" ${!p.doctype ? "selected" : ""}>any type</option>
        ${[["pdf", "PDFs"], ["web", "web pages"], ["image", "images"], ["text", "text files"], ["note", "notes"], ["page", "pages"]].map(([v, l]) => `<option value="${v}" ${p.doctype === v ? "selected" : ""}>${l}</option>`).join("")}
      </select>
      <label>passages <input name="limit" type="number" min="1" max="20" value="${esc(p.limit || settings().ask_limit)}" title="passages per search"></label>
      <label>steps <input name="steps" type="number" min="0" max="${steps.max}" value="${esc(p.steps != null ? p.steps : steps.default)}" title="how many steps the model surfs before answering (search again, read on, walk the graph, drop); 0 answers from the first search alone"></label>
      <label>reading <input name="tokens" type="number" min="1000" max="${reading.max}" step="500" value="${esc(p.tokens || reading.default)}" title="how much the steps may read, in tokens (at most ${reading.max} for this model)"></label>
      <span id="ask-count"></span>
      <button type="button" id="ask-new" class="composer-new" hidden title="forget this conversation">New ask</button>
    </div>
  </form>`;
}

// The trail of a surf: what the model did at each step, with what the step
// brought. Shown as it happens under the pending line, then folded under
// the answer; a [n] in it opens the source like one in the answer.
const STEP_WORDS = { search: "searched", read: "read", facts: "facts of", walk: "walked", similar: "like", drop: "set aside", answer: "enough read", error: "failed" };

function renderTrail(t) {
  const trail = t.trail || [];
  if (!trail.length) return "";
  const passages = t.passages || [];
  const items = trail.map((s) => {
    const verb = STEP_WORDS[s.action] || s.action;
    let head = esc(`${verb} ${s.arg || ""}`.trim());
    let result = s.action === "answer" ? "" : esc(s.result || "");
    if (s.action === "error") { head = esc(verb); result = esc(s.result || ""); }
    if (s.action === "read") head = citeLinks(head, passages);
    return `<li class="trail-step">
      <span class="trail-head">${head}${result ? `<span class="trail-result"> → ${citeLinks(result, passages)}</span>` : ""}</span>
      ${s.note ? `<span class="trail-note">${esc(s.note)}</span>` : ""}
    </li>`;
  }).join("");
  if (t.pending) return `<ol class="trail live" start="0">${items}</ol>`;
  const n = trail.filter((s) => s.n > 0).length;
  return `<details class="trail-fold"><summary>${n} step${n === 1 ? "" : "s"}${t.dropped && t.dropped.length ? `, ${t.dropped.length} set aside` : ""}</summary><ol class="trail" start="0">${items}</ol></details>`;
}

function renderTurn(t, i) {
  const passages = t.passages || [];
  const cited = new Set((t.citations || []).map((c) => c.n));
  let body;
  if (t.error) body = `<p class="error">${esc(t.error)}</p>`;
  else if (t.pending) body = `${renderTrail(t)}<p class="turn-pending muted">${esc(t.pending)}</p>`;
  else if (t.answer) body = `<div class="answer">${citeLinks(md(t.answer), passages)}</div>${renderTrail(t)}`;
  else body = `<p class="muted">${passages.length ? "No model answered; the sources beside are what a model would have been given." : "No passages found."}</p>${renderTrail(t)}`;
  const meta = (t.pending || t.error) ? "" : `
    <div class="turn-meta muted">
      ${t.model ? `<span>${esc(t.model)}${t.steps ? ` · ${t.steps} step${t.steps === 1 ? "" : "s"}` : ""} · ${t.seconds} s${t.cost_usd ? ` · $${t.cost_usd.toFixed(4)}` : ""} · ${(t.usage || {}).input_tokens || 0} in / ${(t.usage || {}).output_tokens || 0} out</span>` : ""}
      <button type="button" class="linkish turn-sources-link">${passages.length} source${passages.length === 1 ? "" : "s"}${cited.size ? `, ${cited.size} cited` : ""}</button>
      ${t.answer ? `<button type="button" class="linkish turn-keep-link">keep on page…</button>` : ""}
    </div>
    <div class="turn-keep" hidden></div>`;
  return `<article class="turn" data-turn="${i}">
    <div class="turn-q">${esc(t.question)}</div>
    <div class="turn-a">${body}${meta}</div>
  </article>`;
}

function renderSources(t, i) {
  if (!t || t.pending || t.error) return `<p class="muted side-empty">The sources of an answer appear here: one passage per document, the cited ones marked, each with what the graph knows about its document.</p>`;
  const passages = t.passages || [];
  const facts = t.facts || {};
  const cited = new Set((t.citations || []).map((c) => c.n));
  const cards = passages.map((p) => {
    const f = facts[p.doc_id] || [];
    const cut = p.text.length > 300;
    return `
    <article class="source plate ${cited.has(p.n) ? "cited" : ""}" id="source-${p.n}">
      <div class="source-n">[${p.n}]</div>
      <div class="source-body">
        <a class="source-title" href="#doc/${p.doc_id}${p.chunk_id ? `?chunk=${p.chunk_id}` : ""}">${esc(p.title || "(untitled)")}</a>
        <div class="hit-meta">${badge(p.kind)} <span>${headingPath(p.heading)}</span> <span>${p.page ? `p. ${p.page}` : ""}</span></div>
        ${figureThumb(p)}
        <p class="snippet source-short">${esc(plainFigures(p.text).slice(0, 300))}${cut ? `… <button type="button" class="linkish source-more">more</button>` : ""}</p>
        ${cut ? `<p class="snippet source-full" hidden>${esc(p.text)} <button type="button" class="linkish source-more">less</button></p>` : ""}
        ${f.length ? `<div class="chips">${f.map((x) => `<a class="chip" style="--c:${typeColor(x.type)}" href="#graph?entity=${encodeURIComponent(x.name)}" title="${esc(x.rel)}">${esc(x.rel)}: ${esc(x.name)}</a>`).join("")}</div>` : ""}
      </div>
    </article>`;
  }).join("");
  return `<div class="side-head"><span>Sources</span><span>turn ${i + 1} · ${passages.length}${cited.size ? `, ${cited.size} cited` : ""}</span></div>
    ${rule()}
    <div class="sources ${t.answer ? "with-answer" : ""}">${cards || `<p class="muted side-empty">No passages found for this turn.</p>`}</div>`;
}

function renderKeepForm(t) {
  return `
    <form class="ask-save">
      <label>Keep on page <select name="slug"><option value="">loading…</option></select></label>
      <input name="heading" type="text" value="${esc(t.question)}" placeholder="heading" title="section heading">
      <button>Add to page</button>
      <label>or start a synthesis <input name="new_slug" type="text" placeholder="new page name" title="a new synthesis page seeded with this answer"></label>
      <span class="ask-save-msg muted"></span>
    </form>`;
}

async function bindKeepForm(form, t) {
  const slugSel = form.slug;
  try {
    const pages = await api("/pages");
    slugSel.innerHTML = pages.length
      ? pages.map((pg) => `<option value="${esc(pg.slug)}">${esc(pg.title || pg.slug)} (${esc(pg.kind)})</option>`).join("")
      : `<option value="">no pages yet: create one under Pages</option>`;
  } catch (err) {
    slugSel.innerHTML = `<option value="">${esc(err.message)}</option>`;
  }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    const msg = form.querySelector(".ask-save-msg");
    const slug = (data.new_slug || "").trim() || data.slug;
    if (!slug) { msg.textContent = "pick a page or name a new synthesis"; return; }
    try {
      const body = { slug, heading: data.heading, result: t };
      if (data.new_slug && data.new_slug.trim()) body.create = "synthesis";
      const res = await post("/ask/save", body);
      msg.innerHTML = `${res.created ? "created" : "saved as revision " + res.revision} <a href="#doc/${res.doc_id}">${esc(res.slug || slug)}</a>`;
    } catch (err) {
      msg.textContent = err.message;
    }
  });
}

async function viewAsk(p) {
  if (!askConfig) {
    try { askConfig = await api("/ask/config"); } catch (_) { askConfig = { default: "none" }; }
  }
  view.classList.add("stage");
  view.innerHTML = `
    <div class="ask">
      <section class="ask-thread">
        <div id="ask-turns" class="ask-turns"></div>
        ${renderComposer(p)}
      </section>
      <aside id="ask-side" class="ask-side"></aside>
    </div>`;
  const turns = askSession().turns;
  const turnsEl = document.getElementById("ask-turns");
  const side = document.getElementById("ask-side");
  const form = document.getElementById("ask-form");
  const box = form.question;
  let selected = -1;

  const onScreen = () => document.contains(turnsEl);
  const toEnd = () => window.scrollTo(0, document.body.scrollHeight);
  function autosize() {
    box.style.height = "auto";
    box.style.height = Math.min(box.scrollHeight, 220) + "px";
  }
  function paint() {
    turnsEl.innerHTML = turns.length ? turns.map(renderTurn).join("") : `
      <div class="ask-empty muted">
        <p>Ask the library a question. The model surfs before it answers — searches again, reads on, walks the graph, sets aside what is beside the point — for as many steps as you allow, and the answer cites the passages it kept, shown beside it. A follow-up may refer to the earlier turns ("and the second one?").</p>
      </div>`;
    const done = turns.filter((t) => !t.pending && !t.error).length;
    document.getElementById("ask-new").hidden = !turns.length;
    document.getElementById("ask-count").textContent = done ? `${done} turn${done > 1 ? "s" : ""} in this tab; a follow-up may refer to them` : "";
    box.placeholder = done ? "ask a follow-up…" : "ask the library…";
    if (selected >= 0) turnsEl.querySelectorAll(".turn").forEach((el) => el.classList.toggle("selected", Number(el.dataset.turn) === selected));
    turnsEl.querySelectorAll(".answer").forEach(typesetMaths);
  }
  function select(i) {
    selected = i;
    turnsEl.querySelectorAll(".turn").forEach((el) => el.classList.toggle("selected", Number(el.dataset.turn) === i));
    side.innerHTML = renderSources(turns[i], i);
    side.querySelectorAll(".source-short, .source-full").forEach(typesetMaths);
  }
  function showSource(n) {
    const card = document.getElementById(`source-${n}`);
    if (!card) return;
    card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    card.classList.remove("flash");
    void card.offsetWidth;  // restart the animation
    card.classList.add("flash");
  }

  async function ask(question, opts) {
    const backend = opts.backend || askConfig.default;
    const earlier = turns.filter((t) => t.answer).map((t) => ({ question: t.question, answer: t.answer }));
    const steps = opts.steps == null || opts.steps === "" ? null : Math.max(0, Number(opts.steps) || 0);
    const surfing = backend !== "none" && steps !== 0;
    const turn = { question, pending: backend === "none" ? "gathering passages…" : surfing ? `${backend} is searching…` : `asking ${backend}… (a local model takes tens of seconds)` };
    turns.push(turn);
    paint();
    toEnd();
    const body = {
      question,
      backend: opts.backend || null,
      doctype: opts.doctype || null,
      limit: Number(opts.limit || settings().ask_limit),
      history: earlier,
    };
    if (steps !== null) body.steps = steps;
    if (opts.tokens) body.tokens = Number(opts.tokens);
    let r;
    try {
      if (!surfing) {
        r = await post("/ask", body);
      } else {
        // the trail arrives step by step and is painted as it does
        turn.trail = [];
        const repaint = () => { if (onScreen()) { paint(); toEnd(); } };
        await postLines("/ask", { ...body, stream: true }, (e) => {
          if (e.event === "step") {
            turn.trail.push(e.step);
            const s = e.step;
            turn.pending = s.action === "answer" ? `${backend} has read enough…` : `${backend} is looking…`;
            repaint();
          } else if (e.event === "answering") {
            turn.pending = `${backend} is writing the answer from ${e.passages} passage${e.passages === 1 ? "" : "s"}…`;
            repaint();
          } else if (e.event === "answer") {
            r = e.result;
          } else if (e.event === "error") {
            throw new Error(e.detail || "the ask failed");
          }
        });
        if (!r) throw new Error("the door closed the stream without an answer");
      }
    } catch (err) {
      delete turn.pending;
      turn.error = err.message;
      if (onScreen()) { paint(); toEnd(); }
      return;
    }
    delete turn.pending;
    Object.assign(turn, r);
    saveAskSession(turns);
    if (!onScreen()) return;  // navigated away meanwhile; the turn is kept
    paint();
    select(turns.length - 1);
    turnsEl.lastElementChild.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const question = box.value.trim();
    if (!question) return;
    box.value = "";
    autosize();
    if (location.hash !== "#ask") history.replaceState(null, "", "#ask");  // a reload shows the conversation, not a re-ask
    ask(question, { backend: form.backend.value, doctype: form.doctype.value, limit: form.limit.value, steps: form.steps.value, tokens: form.tokens.value });
  });
  form.backend.addEventListener("change", () => {
    // the reading budget's ceiling is the chosen model's
    const bounds = readingBounds(form.backend.value === "none" ? "" : form.backend.value);
    form.tokens.max = bounds.max;
    form.tokens.title = `how much the steps may read, in tokens (at most ${bounds.max} for this model)`;
    if (Number(form.tokens.value) > bounds.max) form.tokens.value = bounds.default;
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  box.addEventListener("input", autosize);
  document.getElementById("ask-new").addEventListener("click", () => {
    clearAskSession();
    turns.length = 0;
    selected = -1;
    paint();
    side.innerHTML = renderSources(null);
    if (location.hash !== "#ask") history.replaceState(null, "", "#ask");
    box.focus();
  });
  turnsEl.addEventListener("click", (e) => {
    const art = e.target.closest(".turn");
    if (!art) return;
    const i = Number(art.dataset.turn);
    const cite = e.target.closest("a.cite");
    if (cite && !(e.ctrlKey || e.metaKey || e.shiftKey || e.button)) {
      // a plain click on [n] shows the source beside; a modified one opens the document
      e.preventDefault();
      if (selected !== i) select(i);
      showSource(Number(cite.textContent.replace(/\D/g, "")));
      return;
    }
    if (e.target.closest(".turn-sources-link")) {
      select(i);
      if (window.innerWidth <= 900) side.scrollIntoView({ block: "start", behavior: "smooth" });
      return;
    }
    if (e.target.closest(".turn-keep-link")) {
      const slot = art.querySelector(".turn-keep");
      if (!slot.hidden) { slot.hidden = true; return; }
      slot.innerHTML = renderKeepForm(turns[i]);
      slot.hidden = false;
      bindKeepForm(slot.querySelector("form"), turns[i]);
      return;
    }
    if (!e.target.closest("a, button, form")) select(i);
  });
  side.addEventListener("click", (e) => {
    if (!e.target.closest(".source-more")) return;
    const card = e.target.closest(".source");
    const full = card.querySelector(".source-full");
    full.hidden = !full.hidden;
    card.querySelector(".source-short").hidden = !full.hidden;
  });

  paint();
  autosize();
  side.innerHTML = renderSources(null);
  const kept = p.question ? turns.findIndex((t) => t.question === p.question) : -1;
  if (turns.length) {
    select(kept >= 0 ? kept : turns.length - 1);
    if (kept >= 0) turnsEl.children[kept].scrollIntoView({ block: "start" }); else toEnd();
  }
  if (p.question && kept < 0) {
    // a question in the link: ask it, then the link becomes the conversation
    box.value = "";
    history.replaceState(null, "", "#ask");
    await ask(p.question, p);
  }
}

// --------------------------------------------------------------- promote

// The queue for the expensive model: what is flagged (and whether that
// model has read it), and what the library keeps coming back to.
async function viewPromote(p) {
  loading();
  let d;
  try { d = await api("/promote", { limit: p.limit || 30 }); } catch (err) { view.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  const pending = d.promoted.filter((x) => !x.done).length;
  const row = (x) => `<tr>
      <td><a href="#doc/${x.doc_id}">${esc(x.title || "(untitled)")}</a></td>
      <td>${esc(x.promote.by)}</td>
      <td class="muted">${esc(x.promote.reason || "")}</td>
      <td class="muted">${esc((x.promote.at || "").slice(0, 10))}</td>
      <td>${x.done ? "done" : `<span class="muted">pending</span>`}</td>
      <td><a href="#" class="unpromote" data-id="${x.doc_id}" title="remove the flag">×</a></td>
    </tr>`;
  const cand = (c) => `<tr>
      <td><a href="#doc/${c.doc_id}">${esc(c.title || "(untitled)")}</a></td>
      <td class="num">${c.score}</td>
      <td class="muted">${[c.project ? `${c.project} project${c.project > 1 ? "s" : ""}` : "", c.synthesis ? `${c.synthesis} synthesis` : "", c.page ? `${c.page} note${c.page > 1 ? "s" : ""}` : "", c.cited ? `cited by ${c.cited}` : ""].filter(Boolean).join(", ")}</td>
      <td><a href="#" class="promote" data-id="${c.doc_id}">promote</a></td>
    </tr>`;
  view.innerHTML = `
    <p class="muted">The expensive pass runs with <b>${esc(d.step.model)}</b>${d.step.runtime ? ` (${esc(d.step.runtime)})` : ""}${d.step.error ? ` · <span class="error">${esc(d.step.error)}</span>` : ""}.
      Flagged documents are read by it when a worker runs the step with the asking: <code>prax work --steps promote --spend</code>; ${pending} pending.</p>
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Promoted (${d.promoted.length})</h2>
    ${d.promoted.length ? `<table class="doc-list"><thead><tr><th>document</th><th>by</th><th>reason</th><th>when</th><th>status</th><th></th></tr></thead><tbody>${d.promoted.map(row).join("")}</tbody></table>` : `<p class="muted">Nothing flagged yet. Promote from a document page, from the candidates below, or with the MCP tool.</p>`}
    <h2 style="font-size:1rem;margin:1.2rem 0 .3rem">Candidates</h2>
    <p class="muted">Scored by project membership (${PROMOTE_W.project}), synthesis sources (${PROMOTE_W.synthesis}), notes (${PROMOTE_W.page}) and citations from other library documents (${PROMOTE_W.cited} each).</p>
    ${d.candidates.length ? `<table class="doc-list"><thead><tr><th>document</th><th class="num">score</th><th>why</th><th></th></tr></thead><tbody>${d.candidates.map(cand).join("")}</tbody></table>` : `<p class="muted">No candidates: nothing cites, annotates or collects a document yet.</p>`}`;
  view.querySelectorAll("a.promote").forEach((a) => a.addEventListener("click", async (e) => {
    e.preventDefault();
    try { await post(`/doc/${a.dataset.id}/promote`, { reason: "candidate" }); render(); } catch (err) { setStatus(err.message); }
  }));
  view.querySelectorAll("a.unpromote").forEach((a) => a.addEventListener("click", async (e) => {
    e.preventDefault();
    await fetch(`/doc/${a.dataset.id}/promote`, { method: "DELETE" });
    render();
  }));
}
const PROMOTE_W = { project: 5, synthesis: 4, page: 3, cited: 1 };

// ----------------------------------------------------------------- inbox
// Captures: upload files (drag and drop or pick), send a URL for the door
// to fetch, and the latest captures with their state. Each capture names
// its domains; the rules in prax.yaml apply when none is chosen.

async function viewInbox(p) {
  loading();
  let d;
  try { d = await api("/inbox", { limit: p.limit || 50 }); } catch (err) { view.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  const domainOpts = d.modules.map((m) => `<label class="chip"><input type="checkbox" name="domain" value="${esc(m)}"> ${esc(m)}</label>`).join(" ");
  const row = (x) => `<tr>
      <td><a href="#doc/${x.doc_id}">${esc(x.title || "(untitled)")}</a>${x.source_url ? ` <a class="muted" href="${esc(x.source_url)}" target="_blank" rel="noopener" title="${esc(x.source_url)}">↗</a>` : ""}</td>
      <td class="muted">${esc(x.source)}${x.capture.by && x.capture.by !== x.source ? ` (${esc(x.capture.by)})` : ""}${x.capture.mode ? ` · ${esc(x.capture.mode)}` : ""}${x.capture.note && x.capture.mode !== "snapshot" ? `<br><small title="${esc(x.capture.note)}">${esc(x.capture.note.slice(0, 60))}</small>` : ""}</td>
      <td class="muted">${esc((x.domains || []).join(", ") || "all")}${x.recaptured ? ` <span title="sent again with the same text">·${x.recaptured + 1}×</span>` : ""}</td>
      <td class="muted">${esc((x.capture.at || "").slice(0, 16).replace("T", " "))}</td>
      <td>${x.indexed ? (x.extracted ? "extracted" : "indexed") : x.tried ? `<span class="muted" title="every extractor tried it and found no text — a scan? OCR or the vision model can be asked for on its page (read again…)">no text found</span>` : `<span class="muted" title="registered; the worker (prax work --watch) extracts its text">pending</span>`}</td>
    </tr>`;
  view.innerHTML = `
    <form id="upload" class="search-form" autocomplete="off">
      <div id="drop" class="drop">Drop files here, or <label><input id="files" type="file" multiple hidden><u>choose files</u></label> or <label><input id="folder" type="file" webkitdirectory multiple hidden><u>a folder</u></label>. Text and HTML are searchable at once; PDFs and images wait for the parse queue.</div>
      <input id="up-title" type="text" placeholder="title (single file only)" style="flex:1 1 16rem">
      <span class="chips" id="up-domains">${domainOpts}</span>
      <input id="up-tags" type="text" placeholder="tags, comma-separated" style="flex:0 1 14rem">
      <button>Upload</button>
    </form>
    <form id="fetch" class="search-form" autocomplete="off">
      <input id="fetch-url" type="url" placeholder="https://… (the door fetches it: a page, a PDF)" style="flex:1 1 22rem" required>
      <input id="fetch-title" type="text" placeholder="title (optional)">
      <button>Fetch</button>
    </form>
    <p class="muted">Drop folder on the server: <code>${esc(d.inbox_dir)}</code> (a file in <code>inbox/&lt;domain&gt;/</code> lands in that domain; <code>scripts/inbox.py --watch --parse</code> consumes it).</p>
    <p id="inbox-msg">${inboxReport()}</p>
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Recent captures (${d.recent.length})</h2>
    ${d.recent.length ? `<table class="doc-list"><thead><tr><th>document</th><th>source</th><th>domains</th><th>when</th><th>state</th></tr></thead><tbody>${d.recent.map(row).join("")}</tbody></table>` : `<p class="muted">Nothing captured yet.</p>`}`;
  const clearKept = document.getElementById("inbox-msg-clear");
  if (clearKept) clearKept.addEventListener("click", () => { keepInboxReport(""); document.getElementById("inbox-msg").innerHTML = ""; });
  const chosenDomains = () => [...view.querySelectorAll("#up-domains input:checked")].map((i) => i.value);
  // The message box is looked up at report time: the view may have been
  // re-rendered meanwhile (the change poll), and a captured element would
  // then be a detached one that nobody sees.
  const msgBox = () => document.getElementById("inbox-msg");
  const line = (r) => r.error
    ? `<span class="error">${esc(r.name)}: ${esc(r.error)}</span>`
    : `${esc(r.name)} → <a href="#doc/${r.doc_id}">doc ${r.doc_id}</a>${r.duplicate_of ? " (the same page again)" : r.created ? "" : " (already in the store)"}${r.replaced ? `, replaces doc ${r.replaced}` : ""}${r.indexed ? ", searchable" : ", waiting for the parse queue"}`;
  const report = (results, done, total) => {
    const box = msgBox();
    if (!box) return;
    const n = results.length;
    const failed = results.filter((r) => r.error);
    const fresh = results.filter((r) => !r.error && r.created).length;
    const known = results.filter((r) => !r.error && !r.created).length;
    const summary = n > 1
      ? `<b>${done ? "Done:" : "Uploading:"}</b> ${n} of ${total} file${total === 1 ? "" : "s"} — ${fresh} new, ${known} already in the store${failed.length ? `, <span class="error">${failed.length} failed</span>` : ""}. ${done ? "The parse queue reads the new ones; the list below follows." : ""}`
      : "";
    // every failure in full; successes in full up to a screen, then folded
    const shown = n <= 40 ? results : [...failed, ...results.filter((r) => !r.error).slice(0, 12)];
    const rest = n - shown.length;
    const html = summary + (summary && shown.length ? "<br>" : "") + shown.map(line).join("<br>")
      + (rest > 0 ? `<br><span class="muted">… and ${rest} more (all in the list below once parsed)</span>` : "")
      + (done && n > 1 ? ` <button type="button" class="linkish" id="inbox-msg-clear">dismiss</button>` : "");
    keepInboxReport(html);  // shown again after a re-render or a reload of the page
    box.innerHTML = html;
    const clear = document.getElementById("inbox-msg-clear");
    if (clear) clear.addEventListener("click", () => { keepInboxReport(""); box.innerHTML = ""; });
  };
  async function upload(files) {
    if (!files.length) return;
    // what the form says, read once: the view may re-render during a long batch
    const title = files.length === 1 ? (document.getElementById("up-title").value || "").trim() : "";
    const doms = chosenDomains();
    const tags = (document.getElementById("up-tags").value || "").trim();
    uploading = true;
    const results = [];
    const total = files.length;
    try {
      for (const [i, f] of files.entries()) {
        setStatus(`uploading ${i + 1} of ${total}…`);
        const fd = new FormData();
        fd.append("file", f, f.name);
        if (title) fd.append("title", title);
        if (doms.length) fd.append("domains", doms.join(","));
        if (tags) fd.append("tags", tags);
        try {
          const res = await fetch("/ingest/file", { method: "POST", body: fd });
          if (res.status === 401) { askForToken(); throw new Error("access token required"); }
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || res.statusText);
          results.push({ name: f.name, ...data });
        } catch (err) { results.push({ name: f.name, error: err.message }); }
        if (i % 10 === 9) report(results, false, total);
      }
    } finally {
      uploading = false;
    }
    setStatus(`uploaded ${total} file${total === 1 ? "" : "s"}`);
    report(results, true, total);
    refreshList();
  }
  let pendingTimer = null;
  async function refreshList() {
    try {
      const fresh = await api("/inbox", { limit: p.limit || 50 });
      const tbody = view.querySelector("table.doc-list tbody");
      if (tbody) tbody.innerHTML = fresh.recent.map(row).join("");
      watchPending(fresh.recent);
    } catch (_) { /* the list stays as it was */ }
  }
  // while a capture is pending, the list follows the watcher's progress
  function watchPending(recent) {
    clearTimeout(pendingTimer);
    if (recent.some((x) => !x.indexed) && route().name === "inbox") {
      pendingTimer = setTimeout(refreshList, 10000);
    }
  }
  watchPending(d.recent);
  const drop = document.getElementById("drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); });
  document.getElementById("files").addEventListener("change", (e) => upload([...e.target.files]));
  document.getElementById("folder").addEventListener("change", (e) => upload([...e.target.files].filter((f) => !f.name.startsWith("."))));
  document.getElementById("upload").addEventListener("submit", (e) => { e.preventDefault(); upload([...document.getElementById("files").files]); });
  document.getElementById("fetch").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = document.getElementById("fetch-url").value.trim();
    const body = { url, title: document.getElementById("fetch-title").value.trim() || null, domains: chosenDomains().length ? chosenDomains() : null };
    try {
      const data = await post("/ingest/url", body);
      report([{ name: url, ...data }], true, 1);
      refreshList();
    } catch (err) { report([{ name: url, error: err.message }], true, 1); }
  });
}

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
  let d, servers = [], readings = null;
  try { d = await api("/jobs", { limit: p.limit || 30 }); } catch (err) { view.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  try { servers = (await api("/models/servers")).servers; } catch (_) { /* the list is a nicety */ }
  try { readings = await api("/readings", { limit: 20 }); } catch (_) { /* so is this one */ }
  const table = (rows) => `<table class="doc-list"><thead><tr><th>job</th><th>progress</th><th class="num">done</th><th>note</th><th>started</th><th>state</th><th>where</th></tr></thead><tbody>${rows.map(jobRow).join("")}</tbody></table>`;
  view.innerHTML = `
    <p class="muted">The passes announce themselves here: the worker's session, parsing, titles, extraction, embedding. A running job without a heartbeat for ten minutes is marked stale; one gone for half an hour is closed.</p>
    ${hostLine(d.host)}
    ${serverLines(servers)}
    ${readingLines(readings)}
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Running (${d.running.length})</h2>
    ${d.running.length ? table(d.running) : `<p class="muted">Nothing running. On the machine with the models: <code>scripts/work.py --watch</code> keeps captures moving.</p>`}
    <h2 style="font-size:1rem;margin:1.2rem 0 .3rem">Recent</h2>
    ${d.recent.length ? table(d.recent) : `<p class="muted">No finished jobs yet.</p>`}
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Health</h2>
    <div id="health"></div>`;
  fillHealth();
  checkHealth();
}

// --------------------------------------------------------------- changes
// A small poll: GET /changes returns a stamp that moves when the store
// changed (this door's writes, or another process's commits) and how many
// jobs run. When the stamp moved and a listing is open, it is re-rendered in
// place (not while something is being typed, not while the tab is hidden).

const LIVE_VIEWS = new Set(["inbox", "browse", "doc", "review", "promote", "jobs", "pages"]);
let lastStamp = null;
let uploading = false;  // an upload batch in flight: the inbox view must not be re-rendered under it
// The last upload's summary, shown until dismissed or the next one: in
// sessionStorage, so the view's re-renders and a reload of the tab keep it.
function inboxReport() {
  try { return sessionStorage.getItem("prax.inbox-report") || ""; } catch (_) { return ""; }
}
function keepInboxReport(html) {
  try { html ? sessionStorage.setItem("prax.inbox-report", html) : sessionStorage.removeItem("prax.inbox-report"); } catch (_) { /* no storage */ }
}
function typing() {
  const el = document.activeElement;
  return uploading || !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT") || !!route().params.edit;
}
function setJobsBadge(n) {
  const b = document.getElementById("jobs-badge");
  if (!b) return;
  b.hidden = !n;
  b.textContent = n || "";
}
async function pollChanges() {
  if (document.visibilityState !== "visible") return;
  let d;
  try {
    const res = await fetch("/changes");
    if (!res.ok) return;
    d = await res.json();
  } catch (_) { return; }
  setJobsBadge(d.jobs);
  const moved = lastStamp !== null && d.stamp !== lastStamp;
  lastStamp = d.stamp;
  if (moved && LIVE_VIEWS.has(route().name) && !typing()) {
    const y = window.scrollY;
    await render({ keepScroll: true });
    window.scrollTo(0, y);
  }
}
setInterval(pollChanges, 10000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") pollChanges(); });
pollChanges();

const views = { search: viewSearch, ask: viewAsk, browse: viewBrowse, review: viewReview, pages: viewPages, promote: viewPromote, inbox: viewInbox, jobs: viewJobs };

async function render(opts) {
  const r = route();
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === r.name));
  view.classList.remove("stage", "wide");  // the ask view widens the page, the document view takes all of it; others get the default
  if (!(opts && opts.keepScroll)) window.scrollTo(0, 0);
  const key = `${r.name}/${r.arg || ""}`;
  quiet = !!(opts && opts.keepScroll) && view.dataset.route === key;  // the same page, refreshed in place
  view.dataset.route = key;
  try {
    if (r.name === "doc") return await viewDoc(r.arg, r.params);
    if (r.name === "graph") return await viewGraph(r.arg, r.params);
    return await (views[r.name] || viewSearch)(r.params);
  } finally { quiet = false; }
}

document.getElementById("quick").addEventListener("submit", (e) => {
  e.preventDefault();
  go("search", "", { q: document.getElementById("quick-q").value });
});
// A client error is otherwise invisible: show it in the status area and
// tell the door, which logs it (POST /ui/error) so it can be read later.
function reportError(kind, err) {
  const message = (err && err.message) || String(err);
  setStatus("error: " + message);
  const body = { kind, message, stack: err && err.stack ? String(err.stack).slice(0, 4000) : null, hash: location.hash, agent: navigator.userAgent };
  fetch("/ui/error", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
}
window.addEventListener("error", (e) => reportError("error", e.error || e.message));
window.addEventListener("unhandledrejection", (e) => reportError("rejection", e.reason));

// a formula's LaTeX, shown and hidden beside its typeset form
document.addEventListener("click", (e) => {
  const b = e.target.closest(".formula-source");
  if (!b) return;
  const pre = b.parentElement.querySelector(".formula-latex");
  if (!pre) return;
  pre.hidden = !pre.hidden;
  b.textContent = pre.hidden ? "LaTeX" : "hide";
});

window.addEventListener("hashchange", render);
render();
