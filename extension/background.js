/* prax capture: the background does the sending, so that "send all tabs"
   survives the popup closing. It answers two messages from the popup
   ({type: "capture", tabIds, domains, tags, close}) and ({type: "status"}),
   keeps its progress in storage.session (storage.local when the browser has
   no session area) where the popup watches it, and talks to the door with
   the settings from storage.local. Firefox runs this as an event page,
   Chrome as a service worker (background-sw.js imports the same files). */

const api = globalThis.browser || globalThis.chrome;
const lib = globalThis.praxLib;

const progressArea = () => api.storage.session || api.storage.local;

async function settings() {
  const s = await api.storage.local.get(["server", "token", "domains", "close"]);
  return { server: lib.normalizeServer(s.server), token: s.token || "", domains: s.domains || [], close: !!s.close };
}

async function door(path, body, cfg) {
  const headers = { "Content-Type": "application/json" };
  if (cfg.token) headers.Authorization = `Bearer ${cfg.token}`;
  const res = await fetch(`${cfg.server}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data;
}

async function readTab(tabId) {
  try {
    const results = await api.scripting.executeScript({
      target: { tabId },
      func: () => ({ url: location.href, title: document.title, html: document.documentElement.outerHTML }),
    });
    return results && results[0] ? results[0].result : null;
  } catch (err) {
    return null; // a page the browser will not let us read: the door fetches
  }
}

async function captureTab(tab, opts, cfg) {
  const read = await readTab(tab.id);
  const url = (read && read.url) || tab.url;
  const title = (read && read.title) || tab.title || null;
  const p = lib.plan(url, read && read.html);
  if (p.mode === "skip") return { tabId: tab.id, url, title, error: p.reason };
  const common = { url, title, domains: opts.domains.length ? opts.domains : null, tags: opts.tags.length ? opts.tags : null, session: opts.session };
  const data = p.mode === "html"
    ? await door("/ingest/html", { ...common, html: read.html }, cfg)
    : await door("/ingest/url", common, cfg);
  return { tabId: tab.id, url, title, mode: p.mode, note: p.reason || null, ...data };
}

async function setProgress(patch) {
  const cur = (await progressArea().get("progress")).progress || {};
  await progressArea().set({ progress: { ...cur, ...patch, at: Date.now() } });
}

async function capture(msg) {
  const cfg = await settings();
  if (!cfg.server) {
    await setProgress({ state: "error", error: "no server configured (options)", results: [] });
    return;
  }
  const opts = { domains: msg.domains || [], tags: msg.tags || [], session: lib.sessionId(), close: !!msg.close };
  const tabs = [];
  for (const id of msg.tabIds) {
    try { tabs.push(await api.tabs.get(id)); } catch (_) { /* closed meanwhile */ }
  }
  await setProgress({ state: "running", session: opts.session, total: tabs.length, done: 0, results: [], error: null });
  const results = [];
  for (const tab of tabs) {
    let r;
    try { r = await captureTab(tab, opts, cfg); } catch (err) { r = { tabId: tab.id, url: tab.url, title: tab.title, error: err.message }; }
    results.push(r);
    await setProgress({ done: results.length, results });
    if (opts.close && !r.error) {
      try { await api.tabs.remove(tab.id); } catch (_) { /* already gone */ }
    }
  }
  await setProgress({ state: "done", done: results.length, results });
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "capture") {
    capture(msg).catch((err) => setProgress({ state: "error", error: err.message }));
    sendResponse({ ok: true });
    return false;
  }
  return false;
});
