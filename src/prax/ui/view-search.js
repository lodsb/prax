// The search view: the query, the modes, a hit and its snippet.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

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
      const page = h.page ? `p. ${h.page}` : (h.time != null ? `at ${fmtTime(h.time)}` : "");
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
