// A document: its header and actions, the process dialog, the
// chunks (text, tables, figures, formulas, references, ask blocks),
// the figure strip and a video's player.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// -------------------------------------------------------------- document


function domainsForm(cur) {
  const set = new Set(cur.domains || []);
  return `
  <form class="reading-form domains-form">
    <span class="muted">Read against</span>
    ${cur.modules.map((m) => `<label class="tick"><input type="checkbox" name="domain" value="${esc(m)}"${set.has(m) ? " checked" : ""}> ${esc(m)}</label>`).join("")}
    <button type="submit">Save</button>
    <button type="button" class="secondary domains-cancel">Cancel</button>
    <span class="muted">none ticked: every module. A change is read again by the worker's next extract pass; the old reading is retired, its edges kept as history.</span>
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
  if (meta.lang) bits.push(`<span class="muted" title="what the language pass read (meta.lang)">${esc(languageName(meta.lang))}</span>`);
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

// The figure strip: every picture of the document at a glance (a
// paper's figures, a talk's slides, a scan's pages), each a link to its
// chunk. Folded under the actions; "figures…" opens it, ?figures=1 too.
function figureStrip(chunks, docId) {
  const items = figureItems(chunks);
  if (!items.length) return "";
  const where = (f) => {
    // a frame's caption already opens with its moment
    if (f.time != null) return f.caption.startsWith(fmtTime(f.time)) ? "" : "at " + fmtTime(f.time);
    return f.page ? "p. " + f.page : "";
  };
  return `<section id="doc-figures" class="figure-strip" hidden>${items.map((f) => `
    <a href="#chunk-${f.chunk_id}" data-scroll="${f.chunk_id}" title="${esc(f.caption)}">
      <img src="/doc/${docId}/figure/${f.ref}" alt="${esc(f.caption)}" loading="lazy">
      <span class="cap">${esc(f.caption) || "<span class=muted>(no caption)</span>"}</span>
      <span class="muted where">${where(f)}</span>
    </a>`).join("")}</section>`;
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

// A reference chunk: the entry as written, and under it the library
// document it cites when the references pass matched one — "likely" for
// a title match (the score beside it), "?" when twins tied
function renderReference(c) {
  const d = c.data || {};
  const cited = d.cited;
  let line = "";
  if (cited && cited.doc_id) {
    const sure = cited.how === "ambiguous" ? `<span class="muted" title="one of several library documents under this title">?</span>`
      : cited.how === "sure" ? `<span class="muted" title="matched by title">likely${cited.score != null ? ` ${Number(cited.score).toFixed(2)}` : ""}</span>` : "";
    const also = (cited.also || []).map((o) => ` or <a href="#doc/${o.doc_id}">${esc(o.title || "")}</a>`).join("");
    line = `<p class="cited">&rarr; <a href="#doc/${cited.doc_id}">${esc(cited.title || "in the library")}</a>${also} ${sure}</p>`;
  }
  return md(c.text) + line;
}

// An ask block of a page: the question on the frame, the door's answer
// inside it, and what state it is in — not yet asked, asked on a day by
// a model, or edited by hand and left. The links run the questions pass
// for this block alone (PAGE_OF has the page's slug; the view fills it).
const PAGE_OF = {};
const ASKING_OF = {};  // doc id -> the block ids the pass is answering now
// The document view's click handlers are bound once, on the view, and
// read the current render's state from here: a listener added inside
// the view function would be added again on every re-render (the change
// poll re-renders the view every ten seconds while the store moves).
const DOC_VIEW = { pages: null, player: null };
// outline links, the figure strip, in-text citation markers: chunks
// render as the reader nears them, so the click is caught on the view
view.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-scroll]");
  if (!a || !DOC_VIEW.pages) return;
  e.preventDefault();
  DOC_VIEW.pages.ensure(Number(a.dataset.scroll));
  const el = document.getElementById("chunk-" + a.dataset.scroll);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
});
// a moment link seeks the page's player instead of leaving the page
view.addEventListener("click", (e) => {
  const a = e.target.closest("a.moment");
  if (!a || !DOC_VIEW.player || !DOC_VIEW.player.isConnected) return;
  e.preventDefault();
  playerSeek(DOC_VIEW.player, Number(a.dataset.t));
});
// An ask block's "ask now / ask again / answer anew": the questions pass
// for that block alone, as a job; the page gets a new revision. Bound
// once, like the two above: bound per render, one click started as many
// runs as there had been renders — twenty-six, once.
view.addEventListener("click", async (e) => {
  const a = e.target.closest("a.ask-block-run");
  if (!a) return;
  e.preventDefault();
  const release = a.dataset.release === "1";
  if (release && !confirm("Answer this block anew? What you wrote inside it is replaced by the door's answer (a <!-- prax:keep --> region would stay).")) return;
  const head = a.closest(".ask-block-head");
  const where = head ? head.querySelector(".muted") : null;
  if (where) where.innerHTML = `standing question · ${ASKING}`;  // at once; the change poll re-renders with the revision
  try {
    const r = await post("/questions/run", { slug: a.dataset.slug, force: true, release });
    setStatus(r.running ? `already asking (job ${r.job})` : `asking (job ${r.job})`);
  } catch (err) { setStatus(err.message); }
});
const ASKING = `<span class="asking">asking… <span class="muted">the page gets a revision when the model is done</span></span>`;
// The "process…" dialog: the routes a document can take from here
// (GET /doc/{id}/routes), grouped, each with the document's state
// beside it, the model the step resolves to and whether it costs money,
// and a button that asks for it. One dialog for every document view,
// made once; its clicks are caught on the dialog, like the view's.
const PROCESS = document.createElement("dialog");
PROCESS.id = "process-dialog";
document.body.appendChild(PROCESS);
const PROCESS_GROUPS = [["text", "The text"], ["figures", "The figures"], ["formulas", "The equations"], ["graph", "The graph"]];
let processDoc = null;  // the document the dialog is open for
let processTouched = false;  // something was asked for: re-render the page on close
function processState(s) {
  const bits = [];
  if (s.text_source) bits.push(`text: ${esc(s.text_source)}`);
  if (s.pages) bits.push(`${s.pages} pages`);
  bits.push(`${(s.text_len || 0).toLocaleString()} chars`);
  if (s.no_text) bits.push(`<span class="error">no text: every extractor found none</span>`);
  else if (s.thin) bits.push(`<span class="error">thin: under 100 bytes a page</span>`);
  if (s.figures) bits.push(`${s.figures} figure${s.figures === 1 ? "" : "s"}, ${s.figures_read} read`);
  if (s.formulas) bits.push(`${s.formulas} equation${s.formulas === 1 ? "" : "s"}, ${s.formulas_read} read`);
  if (s.extraction) bits.push(`graph by ${esc(s.extraction.extractor || "?")} on ${esc((s.extraction.at || "").slice(0, 10))}`);
  else bits.push("no graph yet");
  if (s.last_parse && s.last_parse.outcome && s.last_parse.outcome !== "ok") bits.push(`last parse: ${esc(s.last_parse.extractor || "")} ${esc(s.last_parse.outcome)}${s.last_parse.error ? ` (${esc(s.last_parse.error)})` : ""}`);
  return bits.join(" · ");
}
function processRoute(r) {
  const cls = ["route", r.available ? "" : "route-off", r.pending ? "route-pending" : ""].filter(Boolean).join(" ");
  const model = r.model ? `<span class="route-model ${r.paid ? "route-paid" : ""}" title="${r.paid ? "a paid model: the run costs money" : "a local model"}">${esc(r.model)}${r.paid ? " · paid" : ""}</span>` : "";
  const field = r.id === "ocr" ? `<input class="route-mode" name="mode" type="text" placeholder="language (host's default)" size="18" aria-label="OCR language">` : "";
  const button = r.pending
    ? `<button type="button" class="route-go" disabled>${esc(r.label)}</button><span class="route-requested">requested${r.action.kind === "reading" ? ` · <a href="#" class="route-cancel">cancel</a>` : ""}</span>`
    : `<button type="button" class="route-go" data-route="${esc(r.id)}"${r.available ? "" : " disabled"}>${esc(r.label)}</button>`;
  return `<div class="${cls}" data-route="${esc(r.id)}">
    <div class="route-act">${button}${field}</div>
    <div class="route-text"><span class="route-detail">${esc(r.detail)}</span>${r.note ? ` <span class="route-note muted">${esc(r.note)}</span>` : ""} ${model}${r.why ? `<span class="route-why">${esc(r.why)}</span>` : ""}</div>
  </div>`;
}
function processHtml(doc, view) {
  const routes = view.routes || [];
  const groups = PROCESS_GROUPS.filter(([g]) => routes.some((r) => r.group === g));
  const waiting = view.state.reading && view.state.reading.state === "requested" ? view.state.reading : null;
  return `
  <h2>Process</h2>
  <p class="muted process-title">${esc(doc.title || "(untitled)")}</p>
  <p class="muted process-state">${processState(view.state)}</p>
  ${waiting ? `<p class="muted process-waiting">a reading is waiting for a worker: ${esc(waiting.extractor)}${waiting.mode ? ` (${esc(waiting.mode)})` : ""}. A new request replaces it.</p>` : ""}
  ${groups.length ? groups.map(([g, title]) => `<section class="route-group"><h3>${title}</h3>${routes.filter((r) => r.group === g).map(processRoute).join("")}</section>`).join("") : `<p class="muted">Nothing to ask for on this document.</p>`}
  <p class="muted process-foot">A worker takes each request on its next pass (Jobs shows what waits). A reading replaces the text; a figure's or an image's readings add up. The graph is read again on the worker's next extract pass; the promote flag runs only with <code>--spend</code>.</p>
  <div class="dialog-actions"><button type="button" class="secondary process-close">Close</button></div>`;
}
async function openProcess(doc) {
  processDoc = doc;
  processTouched = false;
  let view;
  try { view = await api(`/doc/${doc.id}/routes`); } catch (err) { setStatus(err.message); return; }
  PROCESS.dataset.doc = String(doc.id);
  PROCESS.innerHTML = processHtml(doc, view);
  PROCESS.routes = view.routes;
  if (!PROCESS.open) PROCESS.showModal();
}
async function refreshProcess() {
  if (!processDoc) return;
  try {
    const view = await api(`/doc/${processDoc.id}/routes`);
    PROCESS.innerHTML = processHtml(processDoc, view);
    PROCESS.routes = view.routes;
  } catch (err) { setStatus(err.message); }
}
PROCESS.addEventListener("click", async (e) => {
  if (e.target.closest(".process-close")) { PROCESS.close(); return; }
  const cancel = e.target.closest(".route-cancel");
  if (cancel && processDoc) {
    e.preventDefault();
    await fetch(`/doc/${processDoc.id}/reading`, { method: "DELETE" });
    processTouched = true;
    refreshProcess();
    return;
  }
  const btn = e.target.closest("button.route-go");
  if (!btn || !processDoc) return;
  const route = (PROCESS.routes || []).find((r) => r.id === btn.dataset.route);
  if (!route) return;
  const act = route.action;
  try {
    if (act.kind === "promote") {
      const reason = prompt("Why does this document deserve the expensive pass? (optional)");
      if (reason === null) return;
      await post(`/doc/${processDoc.id}/promote`, { reason: reason || null });
      setStatus("promoted: the worker's promote pass runs it with --spend");
    } else if (act.kind === "rechunk") {
      const out = await post(`/doc/${processDoc.id}/rechunk`, {});
      setStatus(`chunked again: ${out.chunks} chunks`);
      processTouched = true;
    } else if (act.kind === "extract") {
      await post(`/doc/${processDoc.id}/extract`, {});
      setStatus("the graph is read again on the worker's next extract pass");
    } else {
      if (route.paid && !confirm(`This runs ${route.model}, which costs money. Request it?`)) return;
      let mode = act.mode;
      const field = btn.closest(".route").querySelector(".route-mode");
      if (field && field.value.trim()) mode = field.value.trim();
      await post(`/doc/${processDoc.id}/reading`, { extractor: act.extractor, mode });
      setStatus(`reading requested: ${act.extractor}${mode ? ` (${mode})` : ""}`);
    }
    processTouched = true;
    refreshProcess();
  } catch (err) { setStatus(err.message); }
});
PROCESS.addEventListener("close", () => {
  if (processTouched) render({ keepScroll: true });
  processDoc = null;
});
function renderAsk(c, docId) {
  const d = c.data || {};
  const slug = PAGE_OF[docId] || "";
  const ref = `${slug}#${d.id || ""}`;
  let state;
  if ((ASKING_OF[docId] || new Set()).has(d.id)) {
    state = ASKING;
  } else if (d.held) {
    state = `<span class="held" title="the interior no longer matches what the door wrote">edited by hand; the door left it</span> · <a href="#" class="ask-block-run" data-slug="${esc(ref)}" data-release="1" title="answer it anew; what you wrote inside goes">answer anew</a>`;
  } else if (!d.filled) {
    state = `not yet asked · <a href="#" class="ask-block-run" data-slug="${esc(ref)}" title="run the questions pass for this block now">ask now</a>`;
  } else {
    state = `asked ${esc(d.asked || "")}${d.run ? ` by ${esc(d.run)}` : ""} · <a href="#" class="ask-block-run" data-slug="${esc(ref)}" title="ask the question again now, whatever is new">ask again</a>`;
  }
  const inside = askInterior(c.text);
  return `<div class="ask-block ${d.held ? "is-held" : "plate"}">
    <div class="ask-block-head"><span class="ask-block-q">${esc(d.question || "")}</span><span class="muted">standing question · ${state}</span></div>
    <div class="ask-block-body">${inside ? md(inside) : `<p class="muted">The door answers it when the pass runs (a page saved with a new block starts one).</p>`}</div>
  </div>`;
}

// An advertisement or a comment section: folded, with one line saying
// what it is. A run of them folds as one — a comment section is chunked
// per block, and a reader wants one line for the section, not twelve.
// The text is the artifact's own; nothing was removed, and a click opens
// it. Each block keeps its own anchor, so a link to a chunk still lands.
function renderAside(run, docId, highlight) {
  const kind = run[0].kind;
  const inside = run.some((c) => c.chunk_id === highlight);
  const text = run.map((c) => c.text).join("\n\n");
  const ids = run.map((c) => c.chunk_id).join(" ");
  const blocks = run
    .map((c) => `<div class="chunk-body" id="chunk-${c.chunk_id}" data-chunk="${c.chunk_id}">${md(c.text)}</div>`)
    .join("");
  return `
  <section class="chunk kind-${kind} aside-chunk${inside ? " highlight" : ""}" data-chunks="${ids}">
    <details${inside ? " open" : ""}>
      <summary>${badge(kind)} <span class="muted">${esc(asideLine(kind, run[0].data, text, run.length))}</span></summary>
      ${blocks}
    </details>
  </section>`;
}

// The chunks in order, with each run of set-aside ones folded together.
function renderChunks(list, highlight, docId, cites) {
  const out = [];
  for (let i = 0; i < list.length; i++) {
    const c = list[i];
    if (c.kind === "ad" || c.kind === "comment") {
      const run = [c];
      while (i + 1 < list.length && list[i + 1].kind === c.kind) run.push(list[++i]);
      out.push(renderAside(run, docId, highlight));
      continue;
    }
    out.push(renderChunk(c, highlight, docId, cites));
  }
  return out.join("");
}

function renderChunk(c, highlight, docId, cites) {
  const cls = "chunk kind-" + (c.kind || "text") + (c.chunk_id === highlight ? " highlight" : "");
  let body;
  if (c.kind === "table" && c.data && c.data.header && c.data.header.length) {
    const caption = c.text.split("\n").filter((l) => !l.trim().startsWith("|")).join("\n").trim();
    body = (caption ? md(caption) : "") + renderTable(c.data);
  } else if (c.kind === "figure" && c.data && c.data.ref) {
    body = renderFigure(c, docId);
  } else if (c.kind === "formula" && c.data && c.data.latex) {
    body = renderFormula(c);
  } else if (c.kind === "reference") {
    body = renderReference(c);
  } else if (c.kind === "ask") {
    body = renderAsk(c, docId);
  } else if (c.kind === "code") {
    body = md(c.text);
  } else if (c.kind === "ingredients") {
    body = ingredientsBox(c.data) || md(c.text);
  } else {
    body = citeMarkers(md(c.text), cites);  // "[12]" reaches what entry 12 cites
  }
  return `
  <section class="${cls}" id="chunk-${c.chunk_id}" data-chunk="${c.chunk_id}">
    <div class="chunk-meta">
      ${badge(c.kind)}
      <span class="muted">${headingPath(c.heading)}</span>
      ${c.page ? `<span class="muted">p. ${c.page}</span>` : ""}
      ${c.time != null ? momentLink(docId, c.time, VIDEO_OF[docId]) : ""}
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

// The video a document is of, by id, for the moment links rendered
// inside it (renderChunk has no document in hand).
const VIDEO_OF = {};

// A video document's player: the first frame as a poster until the
// reader presses play (nothing is fetched from the provider before), then
// the provider's embed with its API on, so a moment link seeks it.
function playerHtml(doc, chunks, meta) {
  const v = meta.video || {};
  const first = chunks.find((c) => c.kind === "figure" && c.data && c.data.ref);
  const poster = first ? `<img src="/doc/${doc.id}/figure/${first.data.ref}" alt="">` : "";
  const where = [v.channel, v.duration ? fmtTime(v.duration) : null].filter(Boolean).map(esc).join(" · ");
  const embeddable = v.provider === "youtube" && v.id;
  return `<div class="doc-player" id="doc-player" data-provider="${esc(v.provider || "")}" data-video="${esc(v.id || "")}">
    ${embeddable
      ? `<button type="button" class="player-poster" id="player-play" title="load the player (from ${esc(v.provider || "the provider")})">${poster}<span class="player-badge">▶ play</span></button>`
      : `<a class="player-poster" href="${esc(v.url || "#")}" target="_blank" rel="noopener" title="the recording, where it is">${poster}<span class="player-badge">▶ watch there</span></a>`}
    <div class="player-meta muted">${where}${v.url ? ` · <a href="${esc(v.url)}" target="_blank" rel="noopener">watch there ↗</a>` : ""}</div>
  </div>`;
}
function playerLoad(box, startAt) {
  const provider = box.dataset.provider, vid = box.dataset.video;
  if (provider !== "youtube" || !vid) return null;
  let frame = box.querySelector("iframe");
  if (frame) return frame;
  const poster = box.querySelector(".player-poster");
  frame = document.createElement("iframe");
  frame.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(vid)}?enablejsapi=1&start=${Math.floor(startAt || 0)}&autoplay=1&origin=${encodeURIComponent(location.origin)}`;
  frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
  frame.referrerPolicy = "strict-origin-when-cross-origin";
  frame.title = "the video";
  if (poster) poster.replaceWith(frame); else box.prepend(frame);
  frame.addEventListener("load", () => frame.contentWindow.postMessage(JSON.stringify({ event: "listening", id: 1, channel: "widget" }), "https://www.youtube-nocookie.com"));
  return frame;
}
function playerSeek(box, t) {
  const frame = box.querySelector("iframe");
  if (!frame) {
    if (!playerLoad(box, t)) {
      // no embed for this provider: the moment opens where the recording is
      const a = box.querySelector("a.player-poster");
      if (a) window.open(`${a.href}${a.href.includes("?") ? "&" : "?"}t=${Math.floor(t)}s`, "_blank", "noopener");
    }
    return;
  }
  const post = (func, args) => frame.contentWindow.postMessage(JSON.stringify({ event: "command", func, args: args || [] }), "https://www.youtube-nocookie.com");
  post("seekTo", [Math.floor(t), true]);
  post("playVideo");
  box.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
  if (meta.video) VIDEO_OF[doc.id] = meta.video;
  let askingPage = false;
  if (meta.page && meta.page.slug) {
    PAGE_OF[doc.id] = meta.page.slug;
    // what the questions pass has in hand for this page right now
    try {
      const pg = await api(`/page/${meta.page.slug}`);
      ASKING_OF[doc.id] = new Set(pg.asking || []);
      askingPage = !!pg.asking_page;
    } catch (_) { ASKING_OF[doc.id] = new Set(); }
  }
  let highlight = p.chunk ? Number(p.chunk) : null;
  if (!highlight && p.find) highlight = locateChunk(chunks, p.find);
  const firstPage = highlight ? (chunks.find((c) => c.chunk_id === highlight) || {}).page : null;
  const pageMeta = meta.page || null;
  const nFigures = figureItems(chunks).length;
  view.innerHTML = `
  <header class="doc-head">
    <h1>${pageMeta ? `<span class="kind-pill">${esc(pageMeta.kind)}</span> ` : ""}${esc(doc.title || "(untitled)")}</h1>
    <div class="doc-meta">${metaLine(meta)}${pageMeta ? ` · revision ${pageMeta.revision} by ${esc(pageMeta.author || "?")}` : ""}${meta.question ? ` · a standing question, asked ${esc((meta.question.asked_at || "").slice(0, 10))} by ${esc(meta.question.model || "?")}${(meta.question.history || []).length ? `, moved ${meta.question.history.length} time${meta.question.history.length === 1 ? "" : "s"}` : ""}` : ""}</div>
    ${tags(meta)}
    <div class="doc-actions">
      <div class="doc-actions-zone doc-actions-left">
      <div class="doc-actions-group doc-actions-info muted">${esc(doc.mime || "")} · ${chunks.length} chunk${chunks.length === 1 ? "" : "s"} · ${(doc.text_len || 0).toLocaleString()} chars · doc ${doc.id}</div>
      <div class="doc-actions-group doc-actions-open">
        <a href="${originalHref(doc.id, firstPage)}" target="_blank" rel="noopener">open original ↗</a>
        <a href="/doc/${doc.id}/text" target="_blank" rel="noopener">raw text ↗</a>
        ${nFigures ? `<a href="#" id="figures" title="every picture of the document at a glance">${nFigures} figure${nFigures === 1 ? "" : "s"}…</a>` : ""}
      </div>
      </div>
      <div class="doc-actions-zone doc-actions-group doc-actions-edit">
        ${pageMeta ? `<a href="#" id="page-edit">edit page</a>` : `<a href="#" id="add-note">add a note</a>`}
        <a href="#" id="domains" title="which ontology modules this document is read against">domains…</a>
        ${pageMeta ? "" : `<a href="#" id="process" title="what has been done to this document and what can be asked for: OCR, the vision model over its pages or figures, marker, the graph again, the expensive model">process…</a>`}
        ${pageMeta || !meta.promote ? "" : `<a href="#" id="unpromote" title="take the promote flag off">un-promote</a>`}
        ${meta.question ? (askingPage ? ASKING : `<a href="#" id="ask-again" title="ask the question again now, whatever is new">ask again</a>`) : ""}
      </div>
      <div class="doc-actions-zone doc-actions-group doc-actions-remove">
        ${meta.retired ? `<a href="#" id="unretire" title="back into search and the graph">un-retire</a>` : `<a href="#" id="retire" title="out of search and the graph; row and file stay">retire…</a>`}
      </div>
    </div>
    ${readingLine(meta.reading)}
    <div id="domains-form" hidden></div>
    <div id="page-editor"></div>
  </header>
  ${rule()}
  ${figureStrip(chunks, doc.id)}
  ${meta.video ? playerHtml(doc, chunks, meta) : ""}
  <div class="doc-layout">
    <aside class="doc-outline">${outline(chunks)}</aside>
    <div class="doc-body">${(doc.mime || "").startsWith("image/") ? `<a href="${originalHref(doc.id)}" target="_blank" rel="noopener"><img class="doc-image" src="${originalHref(doc.id)}" alt="${esc(doc.title || "")}"></a>` : ""}${chunks.length ? "" : `<p class="muted">No text yet.${(doc.mime || "").startsWith("image/") ? " Describe it with <code>parse_pending.py --ids " + doc.id + " --extractor claude-vision</code>." : ""}</p>`}<div class="doc-more" hidden></div></div>
    <aside class="doc-context" id="doc-context"><p class="muted">Loading context…</p></aside>
  </div>`;
  const pages = pagedBody(view.querySelector(".doc-body"), chunks, highlight, doc, hasMaths(doc, chunks), referenceLinks(chunks));
  DOC_VIEW.pages = pages;  // for the click handlers bound once below
  const player = view.querySelector("#doc-player");
  DOC_VIEW.player = player;
  if (player) {
    const play = player.querySelector("#player-play");
    if (play) play.addEventListener("click", () => playerLoad(player, p.t ? Number(p.t) : 0));
  }
  const loadContext = async (domain) => {
    try {
      await modules();
      const ctx = await api(`/doc/${id}/context`, domain ? { domain } : {});
      ctx.domain = domain || "";
      const aside = document.getElementById("doc-context");
      if (!aside) return; // the view moved on while the context loaded
      aside.innerHTML = renderContext(ctx);
      bindProjectForm(doc.id);
      const sel = document.querySelector("#doc-context select[name=similar-domain]");
      if (sel) sel.addEventListener("change", () => loadContext(sel.value));
    } catch (err) {
      const aside = document.getElementById("doc-context");
      if (aside) aside.innerHTML = `<p class="error">${esc(err.message)}</p>`;
    }
  };
  loadContext(p.domain || "");
  if (pageMeta) {
    document.getElementById("page-edit").addEventListener("click", (e) => {
      e.preventDefault();  // a toggle: open the editor, or close it again
      if (document.getElementById("page-editor").dataset.open) closeEditor(); else openEditor(pageMeta.slug);
    });
    if (p.edit) openEditor(pageMeta.slug);
  }
  const askAgain = document.getElementById("ask-again");
  if (askAgain) askAgain.addEventListener("click", async (e) => {
    e.preventDefault();
    try {
      const r = await post("/questions/run", { slug: pageMeta.slug, force: true });
      askAgain.outerHTML = ASKING;  // the change poll re-renders the page when the revision lands
      setStatus(`asking again (job ${r.job})`);
    } catch (err) { setStatus(err.message); }
  });
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
  const proc = document.getElementById("process");
  if (proc) proc.addEventListener("click", (e) => { e.preventDefault(); openProcess(doc); });
  const cancelReading = document.getElementById("reading-cancel");
  if (cancelReading) cancelReading.addEventListener("click", async (e) => {
    e.preventDefault();
    await fetch(`/doc/${doc.id}/reading`, { method: "DELETE" });
    render({ keepScroll: true });
  });
  const figLink = document.getElementById("figures");
  const strip = document.getElementById("doc-figures");
  if (figLink && strip) {
    const label = figLink.textContent;
    const showStrip = (on) => {
      strip.hidden = !on;
      figLink.classList.toggle("open", on);
      figLink.textContent = on ? "fold the figures" : label;  // the same link both ways
    };
    figLink.addEventListener("click", (e) => { e.preventDefault(); showStrip(strip.hidden); });
    if (p.figures) showStrip(true);
  }
  // the domains as boxes to tick: none ticked is every module; a change
  // is read again against the new set by the worker's next extract pass
  document.getElementById("domains").addEventListener("click", async (e) => {
    e.preventDefault();
    const box = document.getElementById("domains-form");
    if (!box.hidden) { box.hidden = true; return; }
    let cur;
    try { cur = await api(`/doc/${doc.id}/domains`); } catch (err) { setStatus(err.message); return; }
    box.innerHTML = domainsForm(cur);
    box.hidden = false;
    box.querySelector("form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const domains = [...box.querySelectorAll("input[name=domain]:checked")].map((i) => i.value);
      try {
        const r = await put(`/doc/${doc.id}/domains`, { domains: domains.length ? domains : null });
        setStatus(r.reread ? "domains set; the next extract pass reads the document again" : "domains set");
        render({ keepScroll: true });
      } catch (err) { setStatus(err.message); }
    });
    box.querySelector(".domains-cancel").addEventListener("click", () => { box.hidden = true; });
  });
  if (!pageMeta) {
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
  if (highlight) {
    const el = document.getElementById("chunk-" + highlight);
    if (el) el.scrollIntoView({ block: "center" });
  }
}

// A document's body a page of chunks at a time: a paper is one page, a
// 500-page book (2,900 chunks, 650 equations for KaTeX) would otherwise
// take seconds to parse and typeset before anything showed. The rest
// renders as the reader nears the end, or all at once on request (the
// browser's own find wants the whole text on the page); an outline link
// or a highlighted chunk renders up to itself first.
const DOC_PAGE = 120;

function pagedBody(body, chunks, highlight, doc, maths, cites) {
  const more = body.querySelector(".doc-more");
  let rendered = 0;
  const renderTo = (n) => {
    n = Math.min(n, chunks.length);
    if (n <= rendered) return;
    const box = document.createElement("div");
    box.innerHTML = renderChunks(chunks.slice(rendered, n), highlight, doc.id, cites);
    if (maths) typesetMaths(box);
    while (box.firstChild) more.before(box.firstChild);
    rendered = n;
    const left = chunks.length - rendered;
    more.hidden = left <= 0;
    if (left > 0) more.innerHTML = `<span class="muted">${left.toLocaleString()} more chunks below</span> <button type="button" class="linkish doc-all">show all</button>`;
  };
  const ensure = (chunkId) => {
    const at = chunks.findIndex((c) => c.chunk_id === chunkId);
    if (at >= rendered) renderTo(at + Math.floor(DOC_PAGE / 2));
  };
  const first = highlight ? chunks.findIndex((c) => c.chunk_id === highlight) : -1;
  renderTo(Math.max(DOC_PAGE, first + Math.floor(DOC_PAGE / 2)));
  more.addEventListener("click", (e) => {
    if (e.target.classList.contains("doc-all")) renderTo(chunks.length);
  });
  if ("IntersectionObserver" in window) {
    const watch = new IntersectionObserver((entries) => {
      if (entries.some((en) => en.isIntersecting) && !more.hidden) renderTo(rendered + DOC_PAGE);
    }, { rootMargin: "800px 0px" });
    watch.observe(more);
  } else {
    renderTo(chunks.length);
  }
  return { ensure, renderTo };
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
// revision; the revision list with links to earlier texts. "edit page"
// opens it and reads "close the editor" while it is open; Cancel and
// that link close it, a save re-renders the page.
function closeEditor() {
  const box = document.getElementById("page-editor");
  if (!box) return;
  box.innerHTML = "";
  delete box.dataset.open;
  const link = document.getElementById("page-edit");
  if (link) link.textContent = "edit page";
}

async function openEditor(slug) {
  const box = document.getElementById("page-editor");
  if (!box || box.dataset.open) return;
  box.dataset.open = "1";
  const link = document.getElementById("page-edit");
  if (link) link.textContent = "close the editor";
  let page;
  try { page = await api(`/page/${slug}`); } catch (err) { box.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  box.innerHTML = `
    <div class="page-editor">
      <textarea id="page-text">${esc(page.text)}</textarea>
      <div class="row">
        <label class="field"><span class="muted">title</span><input id="page-title" type="text" value="${esc(page.title || "")}" placeholder="title" title="the page's title: change it to rename the page (its address stays)"></label>
        <label class="field"><span class="muted">what changed</span><input id="page-note" type="text" placeholder="a word for the revision list (optional)"></label>
        <button id="page-save">Save revision ${page.revision + 1}</button>
        <button id="page-cancel" class="secondary" type="button">Cancel</button>
        <button id="page-ask" class="secondary" type="button" title="a standing question inside this page: the door answers it between the markers and asks again when the library learns something">+ standing question</button>
        <span id="page-msg" class="muted"></span>
      </div>
      <div class="muted" style="margin-top:.4rem">Revisions: ${page.revisions.map((r) => `<a href="/page/${esc(slug)}/revision/${r.revision}" target="_blank" rel="noopener" title="${esc(r.note || "")}">r${r.revision} ${esc(r.author)} ${esc((r.created_at || "").slice(0, 10))}</a>`).join(" · ")}</div>
    </div>`;
  document.getElementById("page-cancel").addEventListener("click", closeEditor);
  // the markers of an ask block at the cursor; the door fills them on save
  document.getElementById("page-ask").addEventListener("click", () => {
    const ta = document.getElementById("page-text");
    const question = prompt("The question the door keeps answered here:");
    if (!question || !question.trim()) return;
    const markers = askBlockMarkers(nextAskId(ta.value), question.trim());
    const at = ta.selectionStart == null ? ta.value.length : ta.selectionStart;
    const before = ta.value.slice(0, at), after = ta.value.slice(at);
    // on a line of its own, a blank line either side
    const nl = String.fromCharCode(10);
    let lead = "";
    if (before && !before.endsWith(nl + nl)) lead = before.endsWith(nl) ? nl : nl + nl;
    const trail = after && !after.startsWith(nl) ? nl : "";
    ta.value = before + lead + markers + trail + after;
    ta.focus();
    document.getElementById("page-msg").textContent = "the block is answered when you save";
  });
  document.getElementById("page-save").addEventListener("click", async () => {
    const msg = document.getElementById("page-msg");
    try {
      const r = await put(`/page/${slug}`, {
        text: document.getElementById("page-text").value,
        title: document.getElementById("page-title").value || null,
        note: document.getElementById("page-note").value || null,
        author: "human",
      });
      render();
      if (r.job) setStatus(`saved; a new standing question is being answered (job ${r.job}) — the page gets a revision when the model is done`);
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
  // a citation matched by title (the references pass) is marked as such:
  // "likely" for a match over the threshold, "?" for a tie between twins
  const sureness = (c) => c.confidence === "INFERRED" ? `<span title="matched by title from the reference list">likely</span>` : c.confidence === "AMBIGUOUS" ? `<span title="one of several library documents under this title">?</span>` : "";
  const inLib = ctx.cited_by.length;
  parts.push(list(`Cited by${inLib ? ` (${inLib} in the library)` : ""}`, ctx.cited_by, (d) => docLink(d, sureness(d))));
  if (ctx.cites.length) {
    const libCites = ctx.cites.filter((c) => c.doc_id);
    const external = ctx.cites.length - libCites.length;
    parts.push(`<h3>Cites (${ctx.cites.length}${ctx.citations && ctx.citations.cited_by_count != null ? ` · cited by ${Number(ctx.citations.cited_by_count).toLocaleString()} overall` : ""})</h3><ul>${
      libCites.slice(0, 12).map((c) => `<li><a href="#doc/${c.doc_id}">${esc(c.title)}</a>${sureness(c) ? ` <span class="muted">${sureness(c)}</span>` : ""}</li>`).join("")
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
