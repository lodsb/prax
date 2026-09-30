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
  return viewTokens(p);
}

async function viewTokens() {
  view.innerHTML = `${adminTabs("tokens")}
    <p class="muted">A named token reads the library through the search, the documents and the graph the MCP tools use, and nothing else: only the modules it is given, and no personal document unless it may. PRAX_TOKEN is the administrator's and is not listed.</p>
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
  const form = document.getElementById("token-form-add");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = form.querySelector(".msg");
    const name = form.name.value.trim();
    const domains = [...form.querySelectorAll("input[name=domain]:checked")].map((c) => c.value);
    try {
      const r = await post("/tokens", { name, domains: domains.length ? domains : null, personal: form.personal.checked });
      await viewTokens();
      document.getElementById("token-secret").innerHTML = tokenSecret(r.name, r.secret);
    } catch (err) { out.textContent = err.message; }
  });
}
