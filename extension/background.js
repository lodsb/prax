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

/* The snapshot: SingleFile (vendor/single-file, AGPL, the way Zotero's
   connector uses it) turns the page into one self-contained HTML string:
   images, fonts and stylesheets inlined, scripts removed, lazy images
   loaded first, frames included. The options follow SingleFile's own
   defaults for a saved page. */
const SNAPSHOT_OPTIONS = {
  removeHiddenElements: true,
  removeUnusedStyles: true,
  removeUnusedFonts: true,
  removeFrames: false,
  compressHTML: true,
  compressCSS: false,
  loadDeferredImages: true,
  loadDeferredImagesMaxIdleTime: 1500,
  loadDeferredImagesKeepZoomLevel: false,
  removeAlternativeFonts: true,
  removeAlternativeMedias: true,
  removeAlternativeImages: true,
  groupDuplicateImages: true,
  maxSizeDuplicateImages: 512 * 1024,
  saveRawPage: false,
  saveFavicon: true,
  insertMetaCSP: true,
  insertSingleFileComment: true,
  blockScripts: true,
  blockVideos: true,
  blockAudios: true,
  blockAlternativeImages: true,
  removeNoScriptTags: true,
  resolveLinks: true,
  networkTimeout: 0,
  saveOriginalURLs: false,
  blockMixedContent: false,
  imageReductionFactor: 1,
  acceptHeaders: {
    font: "application/font-woff2;q=1.0,application/font-woff;q=0.9,*/*;q=0.8",
    image: "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    stylesheet: "text/css,*/*;q=0.1",
    script: "*/*",
    document: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    video: "video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5",
    audio: "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,application/ogg;q=0.7,video/*;q=0.6,*/*;q=0.5",
  },
};
const SF = "vendor/single-file/";

/* Runs inside the tab (serialized by executeScript): resources are fetched
   from the page first, and through the background when the page's origin
   rules refuse (the background has host permission). */
function runSingleFile(options) {
  const bridge = globalThis.browser || globalThis.chrome;
  const viaBackground = async (url) => {
    const m = await bridge.runtime.sendMessage({ type: "fetch", url });
    if (!m || m.error) throw new Error((m && m.error) || "background fetch failed");
    const bytes = Uint8Array.from(atob(m.body), (c) => c.charCodeAt(0));
    return { status: m.status, headers: { get: (h) => m.headers[String(h).toLowerCase()] || null }, arrayBuffer: async () => bytes.buffer };
  };
  const hostFetch = async (url, opts) => {
    try {
      const r = await fetch(url, { ...opts, cache: "force-cache", referrerPolicy: "strict-origin-when-cross-origin" });
      if (r.status < 400) return r;
    } catch (_) { /* refused by the page's origin rules */ }
    return viaBackground(url);
  };
  return globalThis.singlefile
    .getPageData({ ...options }, { fetch: hostFetch, frameFetch: hostFetch })
    .then((pageData) => ({ url: location.href, title: pageData.title || document.title, html: pageData.content, snapshot: true }))
    .catch((err) => ({ url: location.href, title: document.title, error: String(err && err.message ? err.message : err) }));
}

async function snapshotTab(tabId) {
  // the hooks in the page's own world and the frame script in every frame
  // are best effort (a cross-origin frame, a browser without world: MAIN)
  try { await api.scripting.executeScript({ target: { tabId, allFrames: true }, files: [`${SF}single-file-hooks-frames.js`], world: "MAIN", injectImmediately: true }); } catch (_) { /* optional */ }
  try { await api.scripting.executeScript({ target: { tabId, allFrames: true }, files: [`${SF}single-file-frames.js`] }); } catch (_) { /* optional */ }
  await api.scripting.executeScript({ target: { tabId }, files: [`${SF}single-file.js`] });
  const results = await api.scripting.executeScript({ target: { tabId }, func: runSingleFile, args: [SNAPSHOT_OPTIONS] });
  const r = results && results[0] ? results[0].result : null;
  if (!r || r.error || !r.html) throw new Error((r && r.error) || "no snapshot");
  return r;
}

async function readPlain(tabId) {
  const results = await api.scripting.executeScript({
    target: { tabId },
    func: () => ({ url: location.href, title: document.title, html: document.documentElement.outerHTML }),
  });
  return results && results[0] ? results[0].result : null;
}

/* The page as the tab shows it: a SingleFile snapshot, else the bare DOM,
   else nothing (the door fetches the URL). */
const SNAPSHOT_TIMEOUT_MS = 60 * 1000;

function withTimeout(promise, ms, what) {
  let timer;
  const clock = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${what} took longer than ${ms / 1000} s`)), ms); });
  return Promise.race([promise, clock]).finally(() => clearTimeout(timer));
}

async function readTab(tabId) {
  let plain = null;
  try { plain = await readPlain(tabId); } catch (err) { console.warn("prax: cannot read tab", tabId, err); return null; }
  if (plain && lib.looksLikePdf(plain.url, plain.html)) return plain; // no snapshot of a viewer
  try {
    const snap = await withTimeout(snapshotTab(tabId), SNAPSHOT_TIMEOUT_MS, "the snapshot");
    console.info("prax: snapshot", plain.url, `${snap.html.length} chars`);
    return snap;
  } catch (err) {
    console.warn("prax: snapshot failed, sending the plain DOM", plain.url, err);
    return plain ? { ...plain, snapshot: false, note: `plain DOM (snapshot failed: ${err.message})` } : null;
  }
}

/* The fetch bridge for the snapshot: the page asks, the background fetches
   with the browser's session and host permission, the bytes go back base64. */
async function bridgeFetch(url) {
  const res = await fetch(url, { credentials: "include", cache: "force-cache", referrerPolicy: "strict-origin-when-cross-origin" });
  const buf = await res.arrayBuffer();
  let body = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000) body += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  const headers = {};
  res.headers.forEach((v, k) => { headers[k.toLowerCase()] = v; });
  return { status: res.status, headers, body: btoa(body) };
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
    return { tabId: tab.id, url, title, mode: "html", note: read.snapshot ? "snapshot with images and styles" : (read.note || null), ...data };
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
  console.info("prax: capture", msg.tabIds);
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
    try { r = await captureTab(tab, opts, cfg); } catch (err) { console.warn("prax: capture failed", tab.url, err); r = { tabId: tab.id, url: tab.url, title: tab.title, error: err.message }; }
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
  if (msg && msg.type === "fetch") {
    bridgeFetch(msg.url).then(sendResponse, (err) => sendResponse({ error: err.message }));
    return true; // answered asynchronously
  }
  return false;
});
