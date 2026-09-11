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

const MAX_PDF_BYTES = 64 * 1024 * 1024;

/* A PDF behind a paywall or a login: the door cannot fetch it, the browser
   can, with the session the tab already has. The bytes are uploaded as a
   file; when the fetch comes back as something else (a login page), the
   door fetches the URL as before. */
async function fetchPdf(url) {
  const res = await fetch(url, { credentials: "include", redirect: "follow" });
  if (!res.ok) return null;
  const buf = await res.arrayBuffer();
  if (buf.byteLength > MAX_PDF_BYTES) return null;
  if (!lib.isPdfResponse(res.headers.get("content-type"), new Uint8Array(buf, 0, Math.min(5, buf.byteLength)))) return null;
  return new Blob([buf], { type: "application/pdf" });
}

async function uploadFile(blob, name, common, cfg) {
  const fd = new FormData();
  fd.append("file", blob, name);
  if (common.title) fd.append("title", common.title);
  fd.append("source_url", common.url);
  if (common.domains) fd.append("domains", common.domains.join(","));
  if (common.tags) fd.append("tags", common.tags.join(","));
  if (common.session) fd.append("session", common.session);
  fd.append("by", "extension");
  const headers = cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {};
  const res = await fetch(`${cfg.server}/ingest/file`, { method: "POST", headers, body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data;
}

async function captureTab(tab, opts, cfg) {
  const read = await readTab(tab.id);
  const url = (read && read.url) || tab.url;
  const title = (read && read.title) || tab.title || null;
  const p = lib.plan(url, read && read.html);
  if (p.mode === "skip") return { tabId: tab.id, url, title, error: p.reason };
  const common = { url, title, domains: opts.domains.length ? opts.domains : null, tags: opts.tags.length ? opts.tags : null, session: opts.session };
  if (p.mode === "html") {
    const data = await door("/ingest/html", { ...common, html: read.html }, cfg);
    return { tabId: tab.id, url, title, mode: "html", note: null, ...data };
  }
  if (lib.looksLikePdf(url, read && read.html)) {
    let blob = null;
    try { blob = await fetchPdf(url); } catch (_) { blob = null; }
    if (blob) {
      const data = await uploadFile(blob, lib.pdfFileName(url), common, cfg);
      return { tabId: tab.id, url, title, mode: "file", note: "PDF fetched with your session and uploaded", ...data };
    }
  }
  const data = await door("/ingest/url", common, cfg);
  return { tabId: tab.id, url, title, mode: "url", note: p.reason || null, ...data };
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
