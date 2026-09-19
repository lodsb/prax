/* prax capture options: server, token, default domains, close-after-send.
   Saving requests host permission for the server (Firefox grants host
   permissions on request, Chrome at install) and asks the door which
   domains it offers. */

const api = globalThis.browser || globalThis.chrome;
const lib = globalThis.praxLib;
const $ = (id) => document.getElementById(id);

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

function say(text, ok) {
  const m = $("msg");
  m.textContent = text;
  m.className = ok ? "ok" : "error";
}

function renderDomains(modules, chosen) {
  const box = $("domains");
  if (!modules.length) { box.innerHTML = `<span class="muted">the door offers no domains yet</span>`; return; }
  box.innerHTML = modules.map((m) => `<label class="chip"><input type="checkbox" name="domain" value="${esc(m)}" ${chosen.includes(m) ? "checked" : ""}> ${esc(m)}</label>`).join(" ");
}

function chosenDomains() { return [...document.querySelectorAll('input[name="domain"]:checked')].map((i) => i.value); }

async function probe(server, token) {
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const res = await fetch(`${server}/inbox?limit=1`, { headers });
  if (res.status === 401) throw new Error("the door refused the token (or wants one)");
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function ensurePermission(server) {
  // the server, and every site (the snapshot fetches a page's images and
  // fonts through the background; "send all tabs" reads background tabs)
  const origins = [lib.originPattern(server), "http://*/*", "https://*/*"];
  try {
    if (await api.permissions.contains({ origins })) return true;
    return await api.permissions.request({ origins });
  } catch (err) {
    return true; // a browser without optional host permissions granted them at install
  }
}

async function load() {
  const s = await api.storage.local.get(["server", "token", "domains", "close", "modules", "frame_interval", "site_rules"]);
  $("server").value = s.server || "";
  $("token").value = s.token || "";
  $("close").checked = !!s.close;
  if (s.frame_interval) $("frame_interval").value = s.frame_interval;
  $("site_rules").value = s.site_rules || "";
  renderDomains(s.modules || [], s.domains || []);
}

async function save(e) {
  e.preventDefault();
  const server = lib.normalizeServer($("server").value);
  if (!server) { say("the server must be http(s)://host[:port]", false); return; }
  const token = $("token").value.trim();
  if (!(await ensurePermission(server))) { say("permission to talk to the server was not granted", false); return; }
  const frameInterval = Math.max(5, Math.min(600, Number($("frame_interval").value) || 30));
  await api.storage.local.set({ server, token, close: $("close").checked, domains: chosenDomains(), frame_interval: frameInterval, site_rules: $("site_rules").value });
  try {
    const data = await probe(server, token);
    const modules = Array.isArray(data.modules) ? data.modules : [];
    await api.storage.local.set({ modules });
    renderDomains(modules, chosenDomains());
    say(`saved; the door answers (${modules.length} domain${modules.length === 1 ? "" : "s"})`, true);
  } catch (err) {
    say(`saved, but the door did not answer: ${err.message}`, false);
  }
}

async function test() {
  const server = lib.normalizeServer($("server").value);
  if (!server) { say("the server must be http(s)://host[:port]", false); return; }
  try {
    const data = await probe(server, $("token").value.trim());
    say(`the door answers; domains: ${(data.modules || []).join(", ") || "none"}`, true);
  } catch (err) {
    say(err.message, false);
  }
}

$("form").addEventListener("submit", save);
$("test").addEventListener("click", test);
$("domains").addEventListener("change", () => api.storage.local.set({ domains: chosenDomains() }));
load();
