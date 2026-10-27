// The administrative side (stage W): who may read the library (named
// tokens), what is personal (the rules and the suspected documents) and
// what should not be in it (the clean-up). A restricted token never
// reaches these routes; the page is the administrator's.
// Part of the prax UI, loaded in the order index.html names.

const ADMIN_TABS = [
  ["tokens", "tokens"],
  ["personal", "personal?"],
  ["marked", "marked personal"],
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
  if (tab === "checks") return viewChecks(p);
  return viewTokens(p);
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
