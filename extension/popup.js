/* prax capture popup: two buttons, the domains the door offers, and the
   background's progress. The popup never sends itself; it asks the
   background and watches storage for the results. */

const api = globalThis.browser || globalThis.chrome;
const lib = globalThis.praxLib;
const $ = (id) => document.getElementById(id);
const progressArea = () => api.storage.session || api.storage.local;

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

let cfg = { server: null, token: "", domains: [], close: false };

async function loadSettings() {
  const s = await api.storage.local.get(["server", "token", "domains", "close", "modules"]);
  cfg = { server: lib.normalizeServer(s.server), token: s.token || "", domains: s.domains || [], close: !!s.close, modules: s.modules || [] };
  $("close").checked = cfg.close;
  renderDomains(cfg.modules, cfg.domains);
}

function renderDomains(modules, chosen) {
  const box = $("domains");
  if (!modules.length) { box.innerHTML = `<span class="muted">no domains known yet</span>`; return; }
  box.innerHTML = modules.map((m) => `<label class="chip"><input type="checkbox" name="domain" value="${esc(m)}" ${chosen.includes(m) ? "checked" : ""}> ${esc(m)}</label>`).join(" ");
}

function chosenDomains() { return [...document.querySelectorAll('input[name="domain"]:checked')].map((i) => i.value); }

async function checkServer() {
  const el = $("server");
  if (!cfg.server) { el.textContent = "no server set"; el.className = "error"; return; }
  el.textContent = cfg.server.replace(/^https?:\/\//, "");
  try {
    const headers = cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {};
    const res = await fetch(`${cfg.server}/inbox?limit=1`, { headers });
    if (res.status === 401) { el.className = "error"; el.title = "the door refused the token"; el.textContent += " · token refused"; return; }
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    el.className = "ok";
    el.title = "the door answers";
    if (Array.isArray(data.modules)) {
      await api.storage.local.set({ modules: data.modules });
      renderDomains(data.modules, chosenDomains().length ? chosenDomains() : cfg.domains);
    }
  } catch (err) {
    el.className = "error";
    el.textContent += " · unreachable";
    el.title = err.message;
  }
}

async function send(tabIds) {
  $("msg").textContent = "";
  if (!cfg.server) { $("msg").textContent = "set the server in the options first"; return; }
  if (!tabIds.length) { $("msg").textContent = "nothing to send"; return; }
  $("progress").hidden = false;
  $("results").innerHTML = "";
  $("fill").style.width = "0%";
  await progressArea().set({ progress: { state: "running", total: tabIds.length, done: 0, results: [] } });
  await api.runtime.sendMessage({ type: "capture", tabIds, domains: chosenDomains(), tags: lib.splitList($("tags").value), close: $("close").checked });
}

function renderProgress(p) {
  if (!p) return;
  $("progress").hidden = false;
  const pct = p.total ? Math.round((100 * (p.done || 0)) / p.total) : 0;
  $("fill").style.width = `${pct}%`;
  $("fill").className = p.state === "error" ? "error-fill" : "";
  const items = (p.results || []).map((r) => {
    const link = r.doc_id && cfg.server ? `<a href="${esc(cfg.server)}/ui/#doc/${r.doc_id}" target="_blank" rel="noopener">doc ${r.doc_id}</a> · ` : "";
    return `<li class="${r.error ? "error" : ""}"><span class="title">${esc(r.title || r.url)}</span><br><small>${link}${esc(lib.describeResult(r))}${r.note ? ` · ${esc(r.note)}` : ""}</small></li>`;
  });
  $("results").innerHTML = items.join("");
  if (p.state === "error" && p.error) $("msg").textContent = p.error;
  if (p.state === "done") $("msg").textContent = `${(p.results || []).filter((r) => !r.error).length} of ${p.total} sent`;
}

async function main() {
  await loadSettings();
  checkServer();
  const cur = (await progressArea().get("progress")).progress;
  if (cur && cur.state === "running") renderProgress(cur);
  api.storage.onChanged.addListener((changes, area) => {
    if (changes.progress && (area === "session" || area === "local")) renderProgress(changes.progress.newValue);
    if (changes.log && (area === "session" || area === "local")) renderLog(changes.log.newValue);
  });
  renderLog((await progressArea().get("log")).log);
  $("clear-log").addEventListener("click", async (e) => { e.preventDefault(); await api.runtime.sendMessage({ type: "clear-log" }); renderLog([]); });
  $("options").addEventListener("click", (e) => { e.preventDefault(); api.runtime.openOptionsPage(); });
  // Firefox grants host permissions on request only (Chrome at install),
  // and its permission prompt closes this popup: so the request is a
  // separate button, shown only while the permission is missing. Without
  // it the active tab is still read (activeTab), but the snapshot cannot
  // fetch a page's images cross-origin and a background tab cannot be read.
  await showSitesButton();
  $("send-tab").addEventListener("click", async () => {
    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    await send(tab ? [tab.id] : []);
  });
  $("send-window").addEventListener("click", async () => {
    const tabs = await api.tabs.query({ currentWindow: true });
    const ids = tabs.filter((t) => lib.capturable(t.url)).map((t) => t.id);
    if (ids.length < tabs.length) $("msg").textContent = `${tabs.length - ids.length} tab(s) cannot be read (internal or file pages)`;
    await send(ids);
  });
  $("close").addEventListener("change", () => api.storage.local.set({ close: $("close").checked }));
}

function renderLog(lines) {
  $("log").textContent = (lines || []).join("\n");
}

async function showSitesButton() {
  let granted = true;
  try { granted = await api.permissions.contains({ origins: ALL_SITES }); } catch (_) { granted = true; }
  const box = $("sites");
  box.hidden = granted;
  if (granted) return;
  $("grant-sites").addEventListener("click", async (e) => {
    e.preventDefault();
    try {
      const ok = await api.permissions.request({ origins: ALL_SITES });
      if (ok) box.hidden = true;
    } catch (err) { $("msg").textContent = err.message; }
  });
}
const ALL_SITES = ["http://*/*", "https://*/*"];

main().catch((err) => { $("msg").textContent = err.message; });
