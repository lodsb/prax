// The administrative side (stage W): who may read the library (named
// tokens), what is personal (the rules and the suspected documents) and
// what should not be in it (the clean-up). A restricted token never
// reaches these routes; the page is the administrator's.
// Part of the prax UI, loaded in the order index.html names.

const ADMIN_TABS = [
  ["tokens", "tokens"],
  ["personal", "personal?"],
  ["marked", "marked personal"],
  ["media", "media"],
  ["cleanup", "clean up"],
  ["checks", "text checks"],
];

function adminTabs(current) {
  const tab = (key, label) => key === current
    ? `<b>${esc(label)}</b>`
    : `<a href="#admin?tab=${key}">${esc(label)}</a>`;
  return `<nav class="decide-tabs">${ADMIN_TABS.map(([k, l]) => tab(k, l)).join(" · ")}</nav>`;
}

async function viewAdmin(p) {
  const tab = p.tab || "tokens";
  if (tab === "personal") return viewDecide({ ...p, list: "personal" });
  if (tab === "marked") return viewDecide({ ...p, list: "marked" });
  if (tab === "cleanup") return viewCleanup(p);
  if (tab === "media") return viewMedia(p);
  if (tab === "checks") return viewChecks(p);
  return viewTokens(p);
}

// The referenced photos and recordings as a library of their own (plan
// item AQ, "A media view"): by folder, by month taken or by event, with a
// group released or kept personal in one answer, and undo.
const MEDIA_BY_LABEL = { folder: "folder", month: "month taken", event: "event" };
async function viewMedia(p) {
  const by = MEDIA_BY_LABEL[p.by] ? p.by : "folder";
  const under = p.under || "";
  const href = (u, b = by) => `#admin?${new URLSearchParams({ tab: "media", by: b, ...(u ? { under: u } : {}) })}`;
  view.innerHTML = `${adminTabs("media")}<p class="muted">The photos and recordings prax finds on a share and does not copy. Each is set aside, kept from every token that may not see personal documents, until you release it. Release or personal answers for a whole group, with the groups under it; a photo you decided one by one keeps your answer. Undo takes a group's answer back.</p>
    <p>${Object.entries(MEDIA_BY_LABEL).map(([k, l]) => k === by ? `<b>${esc(l)}</b>` : `<a href="${esc(href("", k))}">${esc(l)}</a>`).join(" · ")} <span id="media-count" class="muted"></span></p>
    <p id="media-crumbs"></p><div id="media-list">${listPlaceholder("media-list")}</div>`;
  const box = document.getElementById("media-list");
  let res;
  try { res = await api("/media", { by, under }); } catch (err) { showError(box, err); return; }
  const c = res.counts;
  document.getElementById("media-count").textContent = ` — ${(c.suspected + c.open + c.personal).toLocaleString()} in all: ${c.suspected.toLocaleString()} set aside, ${c.open.toLocaleString()} released, ${c.personal.toLocaleString()} personal`;
  document.getElementById("media-crumbs").innerHTML = mediaCrumbs(by, res.under, (u) => href(u));
  const leaf = (g) => by === "event" || (by === "month" && res.under) || g.key === res.under;
  box.innerHTML = res.groups.length
    ? res.groups.map((g) => mediaGroupRow(g, by, leaf(g) ? null : href(g.key))).join("")
    : `<p class="muted">Nothing here: no photo or recording is referenced yet (prax crawl --reference, docs/media-by-reference.md).</p>`;
  box.querySelectorAll("a.media-show").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    const row = a.closest(".media-row");
    const shelf = row.querySelector(".media-gallery");
    if (shelf.innerHTML) { shelf.innerHTML = ""; a.textContent = "show"; return; }
    a.textContent = "hide";
    mountGallery(shelf, { limit: 40, load: (offset, limit) => api("/media/pictures", { by, key: a.dataset.key, offset, limit }), empty: `<p class="muted">Nothing to show.</p>` });
  }));
  box.querySelectorAll("button[data-media]").forEach((b) => b.addEventListener("click", async () => {
    const row = b.closest(".media-row");
    const out = row.querySelector(".decide-out");
    const states = row.querySelector(".decide-acts .muted");
    const before = states.textContent;
    row.querySelectorAll("button[data-media]").forEach((x) => { x.disabled = true; });
    try {
      const r = await post("/media/decide", { by: b.dataset.by, key: b.dataset.key, state: b.dataset.media });
      const left = r.left ? ` (${r.left.toLocaleString()} decided before, left as they were)` : "";
      out.innerHTML = `${r.changed.toLocaleString()} ${r.state === "open" ? "released" : "kept personal"}${esc(left)} · <button type="button" class="secondary" data-undo-media="${esc(r.run)}">undo</button>`;
      row.classList.add("done");
      states.textContent = "answered";
      out.querySelector("button[data-undo-media]").addEventListener("click", async (ev) => {
        ev.target.disabled = true;
        try {
          const u = await post("/media/undo", { run: ev.target.dataset.undoMedia });
          out.textContent = `undone: ${u.restored.toLocaleString()} as they were`;
          states.textContent = before;
          row.classList.remove("done");
          row.querySelectorAll("button[data-media]").forEach((x) => { x.disabled = false; });
        } catch (err) { out.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
      });
    } catch (err) {
      out.innerHTML = `<span class="error">${esc(err.message)}</span>`;
      row.querySelectorAll("button[data-media]").forEach((x) => { x.disabled = false; });
    }
  }));
}

// What the checks of a text found (stage AO, parsers.quality): how many
// documents each check flags, and one check's documents with what it saw.
// A fix is a reading: the list's at once with the button, a document's on
// its page. A fix that needs the card waits for it in the queue.
const CHECK_ABOUT = {
  "maths-as-text": "the formulas are loose symbols in the text: a maths font in the PDF, no LaTeX",
  "ocr-layer": "a scan read from the text an old OCR laid under its pictures",
  "bare-figures": "figure captions with no picture beside them",
  "short-text": "the text is much shorter than the PDF's own text layer",
  "leftovers": "what a reader left that is not text: a latex2html comment, a subscript as emphasis, an HTML entity",
};
// the readings that wait for the card
const CARD_READINGS = new Set(["marker"]);

async function viewChecks(p) {
  view.innerHTML = `${adminTabs("checks")}
    <p class="muted">What prax found wrong with a document's text when it landed, or in the nightly pass. Only the text a document holds now counts.</p>
    <div id="checks-list">${listPlaceholder("checks-list")}</div>`;
  const box = document.getElementById("checks-list");
  let res;
  try { res = await api("/documents/quality", p.check ? { check: p.check, offset: p.offset || 0, limit: 50 } : {}); } catch (err) { showError(box, err); return; }
  const chips = Object.entries(res.checks).map(([c, n]) => c === p.check
    ? `<b>${esc(c)} ${n}</b>`
    : `<a href="#admin?tab=checks&check=${encodeURIComponent(c)}" title="${esc(CHECK_ABOUT[c] || "")}">${esc(c)} ${n}</a>`);
  if (!chips.length) { box.innerHTML = `<p class="muted">Nothing found, or nothing checked yet (the quality pass of prax maintain).</p>`; return; }
  let html = `<p class="tags">${chips.join(" · ")}</p>`;
  if (res.items) {
    html += `<p class="muted">${esc(CHECK_ABOUT[res.check] || "")}</p>`;
    // each document is asked for the reading its own finding names, in the
    // mode the door gives it (an arXiv id, marker's OCR on this host's layout)
    const named = [...new Set(res.items.map((i) => (i.finding.fix || {}).extractor).filter(Boolean))];
    if (named.length) {
      const card = named.some((x) => CARD_READINGS.has(x));
      html += `<p><button data-fix="1">ask for each one's fix (${esc(named.join(", "))}) for all ${res.total}</button>${card ? ` <span class="muted">marker needs the card; waits for it</span>` : ""} <span class="decide-out"></span></p>`;
    }
    html += `<table class="checks"><tr><th>document</th><th>text</th><th>found</th><th>what it saw</th><th>fix</th></tr>${res.items.map((i) => `<tr>
      <td><a href="#doc/${i.id}">${esc(i.title || `doc ${i.id}`)}</a></td>
      <td class="muted">${esc(i.text_source || "")}</td>
      <td>${i.finding.count}</td>
      <td><code>${esc((i.finding.evidence || [])[0] || "")}</code></td>
      <td>${esc(fixLabel(i.finding.fix))}</td></tr>`).join("")}</table>`;
    const next = (Number(p.offset) || 0) + res.items.length;
    if (next < res.total) html += `<p><a href="#admin?tab=checks&check=${encodeURIComponent(res.check)}&offset=${next}">the next ${Math.min(50, res.total - next)}</a></p>`;
  }
  box.innerHTML = html;
  box.querySelectorAll("button[data-fix]").forEach((b) => b.addEventListener("click", async () => {
    if (!b.dataset.sure) { b.dataset.sure = "1"; b.textContent = `yes, ask for the fixes of ${res.total} documents`; return; }
    b.disabled = true;
    const out = b.parentElement.querySelector(".decide-out");
    try {
      const r = await post("/readings/bulk", { finding: res.check });
      out.textContent = `asked for ${r.requested}, ${r.skipped} skipped`;
    } catch (err) { b.disabled = false; out.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
  }));
}

async function viewTokens() {
  view.innerHTML = `${adminTabs("tokens")}
    <p class="muted">A named token reads the library through the search, the documents, the pages and the graph, here or through the MCP tools: only the modules it is given, and no personal document unless it may. A reader changes nothing and asks with no paid model; a contributor also adds to the library and changes it. Neither reaches the people, the faces, the review or this page. PRAX_TOKEN is the administrator's and is not listed.</p>
    <div id="token-secret"></div><div id="token-list">${listPlaceholder("token-list")}</div>`;
  const box = document.getElementById("token-list");
  let res;
  try { res = await api("/tokens"); } catch (err) { showError(box, err); return; }
  box.innerHTML = tokensTable(res);
  box.querySelectorAll("button[data-revoke]").forEach((b) => b.addEventListener("click", async () => {
    if (!b.dataset.sure) { b.dataset.sure = "1"; b.textContent = `yes, revoke ${b.dataset.revoke}`; return; }
    b.disabled = true;
    try { await post(`/tokens/${encodeURIComponent(b.dataset.revoke)}`, {}, "DELETE"); viewTokens(); }
    catch (err) { b.disabled = false; b.insertAdjacentHTML("afterend", ` <span class="error">${esc(err.message)}</span>`); }
  }));
  box.querySelectorAll("select[data-role]").forEach((s) => s.addEventListener("change", async () => {
    s.disabled = true;
    try { await post(`/tokens/${encodeURIComponent(s.dataset.role)}`, { role: s.value }, "PUT"); setStatus(`${s.dataset.role} is a ${s.value} now`); }
    catch (err) { s.insertAdjacentHTML("afterend", ` <span class="error">${esc(err.message)}</span>`); }
    s.disabled = false;
  }));
  const form = document.getElementById("token-form-add");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = form.querySelector(".msg");
    const name = form.name.value.trim();
    const domains = [...form.querySelectorAll("input[name=domain]:checked")].map((c) => c.value);
    try {
      const r = await post("/tokens", { name, domains: domains.length ? domains : null, personal: form.personal.checked, role: form.role.value });
      await viewTokens();
      document.getElementById("token-secret").innerHTML = tokenSecret(r.name, r.secret);
    } catch (err) { out.textContent = err.message; }
  });
}
