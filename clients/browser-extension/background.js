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

/* A short log the popup can show ("diagnostics"), so a failing step can be
   reported without opening the extension's console. */
const LOG_LINES = 60;
let logChain = Promise.resolve();
function log(level, ...parts) {
  const line = `${new Date().toISOString().slice(11, 19)} ${parts.map((p) => (p instanceof Error ? p.message : typeof p === "string" ? p : JSON.stringify(p))).join(" ")}`;
  (console[level] || console.log)("prax:", ...parts);
  logChain = logChain.then(async () => {
    const cur = (await progressArea().get("log")).log || [];
    await progressArea().set({ log: [...cur, line].slice(-LOG_LINES) });
  }).catch(() => { /* storage unavailable */ });
}

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
      const r = await fetch(url, { ...opts, cache: "force-cache", referrerPolicy: "strict-origin-when-cross-origin", signal: AbortSignal.timeout(30000) });
      if (r.status < 400) return r;
    } catch (_) { /* refused by the page's origin rules, or slow */ }
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
  try { await api.scripting.executeScript({ target: { tabId, allFrames: true }, files: [`${SF}single-file-hooks-frames.js`], world: "MAIN", injectImmediately: true }); log("info", "hooks injected"); } catch (err) { log("warn", "hooks not injected:", err); }
  try { await api.scripting.executeScript({ target: { tabId, allFrames: true }, files: [`${SF}single-file-frames.js`] }); log("info", "frame script injected"); } catch (err) { log("warn", "frame script not injected:", err); }
  await api.scripting.executeScript({ target: { tabId }, files: [`${SF}single-file.js`] });
  log("info", "core injected; taking the snapshot");
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
const TAB_TIMEOUT_MS = 5 * 60 * 1000;

function withTimeout(promise, ms, what) {
  let timer;
  const clock = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${what} took longer than ${ms / 1000} s`)), ms); });
  return Promise.race([promise, clock]).finally(() => clearTimeout(timer));
}

async function readTab(tabId) {
  let plain = null;
  try { plain = await readPlain(tabId); } catch (err) { log("warn", "cannot read tab", tabId, err); return null; }
  if (!plain) { log("warn", "the tab answered nothing", tabId); return null; }
  log("info", "read", plain.url, `${plain.html.length} chars`);
  if (lib.looksLikePdf(plain.url, plain.html)) return plain; // no snapshot of a viewer
  try {
    const snap = await withTimeout(snapshotTab(tabId), SNAPSHOT_TIMEOUT_MS, "the snapshot");
    log("info", "snapshot", `${snap.html.length} chars`);
    return snap;
  } catch (err) {
    log("warn", "snapshot failed, sending the plain DOM:", err);
    return plain ? { ...plain, snapshot: false, note: `plain DOM (snapshot failed: ${err.message})` } : null;
  }
}

/* The fetch bridge for the snapshot: the page asks, the background fetches
   with the browser's session and host permission, the bytes go back base64. */
const RESOURCE_TIMEOUT_MS = 30 * 1000;

async function bridgeFetch(url) {
  const res = await fetch(url, { credentials: "include", cache: "force-cache", referrerPolicy: "strict-origin-when-cross-origin", signal: AbortSignal.timeout(RESOURCE_TIMEOUT_MS) });
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
  const res = await fetch(url, { credentials: "include", redirect: "follow", signal: AbortSignal.timeout(120000) });
  const ct = res.headers.get("content-type") || "?";
  if (!res.ok) throw new Error(`the site answered ${res.status} to this browser's own request (${ct})`);
  const buf = await res.arrayBuffer();
  if (buf.byteLength > MAX_PDF_BYTES) throw new Error(`larger than ${MAX_PDF_BYTES / 1048576} MB`);
  if (!lib.isPdfResponse(ct, new Uint8Array(buf, 0, Math.min(5, buf.byteLength)))) throw new Error(`not a PDF: ${ct}, ${buf.byteLength} bytes`);
  log("info", "own fetch", url, `${buf.byteLength} bytes, ${ct}`);
  return new Blob([buf], { type: "application/pdf" });
}

/* The last resort for a file the site will not hand to anything but a
   navigation (a Cloudflare challenge answers 403 to the extension's own
   fetch, cookies or not): the browser's downloader, which is a navigation,
   saves it under Downloads/prax-inbox with a sidecar naming the URL,
   domains and tags; the inbox watcher on the batch host consumes that
   folder like the drop folder. No door involved until then. */
const DROP = "prax-inbox";

function waitForDownload(id) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { api.downloads.onChanged.removeListener(onChange); reject(new Error("the download took longer than 5 minutes")); }, 5 * 60 * 1000);
    async function onChange(delta) {
      if (delta.id !== id || !delta.state) return;
      if (delta.state.current === "complete") {
        clearTimeout(timer);
        api.downloads.onChanged.removeListener(onChange);
        const [item] = await api.downloads.search({ id });
        resolve(item ? item.filename : null);
      } else if (delta.state.current === "interrupted") {
        clearTimeout(timer);
        api.downloads.onChanged.removeListener(onChange);
        const [item] = await api.downloads.search({ id });
        reject(new Error(`download interrupted${item && item.error ? `: ${item.error}` : ""}`));
      }
    }
    api.downloads.onChanged.addListener(onChange);
  });
}

async function writeSidecar(base, url, common) {
  const side = { title: common.title || null, source_url: url, domains: common.domains || undefined, tags: common.tags || undefined, session: common.session, by: "extension" };
  const text = JSON.stringify(side);
  // Firefox refuses a data: URL in a download and offers blob URLs (an
  // event page has a DOM); Chrome's service worker has no blob URLs and
  // takes data: ones
  const blobUrl = typeof URL.createObjectURL === "function" ? URL.createObjectURL(new Blob([text], { type: "application/json" })) : null;
  const sideUrl = blobUrl || "data:application/json;charset=utf-8," + encodeURIComponent(text);
  try {
    const sid = await api.downloads.download({ url: sideUrl, filename: `${DROP}/${base}.json`, saveAs: false, conflictAction: "overwrite" });
    await waitForDownload(sid);
  } finally {
    if (blobUrl) URL.revokeObjectURL(blobUrl);
  }
}

async function downloadRoute(url, common) {
  if (!api.downloads) throw new Error("this browser gives the extension no downloads");
  const name = lib.pdfFileName(url);
  let finalPath = null;
  try {
    const id = await api.downloads.download({ url, filename: `${DROP}/${name}`, saveAs: false, conflictAction: "uniquify" });
    finalPath = await waitForDownload(id);
  } catch (err) {
    // the site hands the file to a full page load only: the viewer tab has
    // the bytes, Ctrl+S saves them; the sidecar waits in the folder for a
    // file of that name and the watcher pairs the two
    log("warn", "download refused", url, err);
    await writeSidecar(name, url, common);
    return { mode: "manual", title: name, manual: true, note: `this site hands its PDF to a page load only. In that tab press Ctrl+S and save it as ${name} into Downloads/${DROP}/ (the sidecar with the URL and domains is already there); the inbox watcher takes it from there` };
  }
  const base = (finalPath || name).split(/[\\/]/).pop();
  await writeSidecar(base, url, common);
  log("info", "downloaded for the watcher", base);
  return { mode: "download", title: base, note: `saved to Downloads/${DROP}/${base}; the inbox watcher takes it from there`, downloaded: true };
}

/* A request from inside a page of the same site: the site's own script
   fetching, with the page's cookies and cache partition, which a challenge
   lets pass where the extension's own request (another origin) and the
   downloader are refused. An open HTML tab of that site serves; otherwise
   the site's front page is opened in a background tab and closed again. */
function fetchInPage(u) {
  return fetch(u, { credentials: "include", cache: "force-cache", referrerPolicy: "strict-origin-when-cross-origin", signal: AbortSignal.timeout(90000) })
    .then(async (r) => {
      if (!r.ok) return { error: `the site answered ${r.status} to the page's own request` };
      const bytes = new Uint8Array(await r.arrayBuffer());
      if (!(bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46)) return { error: `not a PDF: ${r.headers.get("content-type") || "?"}` };
      let s = "";
      for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      return { body: btoa(s), size: bytes.length };
    })
    .catch((e) => ({ error: String(e && e.message ? e.message : e) }));
}

function waitForTab(tabId, ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => { api.tabs.onUpdated.removeListener(onUpdated); resolve(false); }, ms);
    function onUpdated(id, info) {
      if (id === tabId && info.status === "complete") { clearTimeout(timer); api.tabs.onUpdated.removeListener(onUpdated); resolve(true); }
    }
    api.tabs.onUpdated.addListener(onUpdated);
  });
}

async function fetchViaSite(url) {
  const origin = new URL(url).origin;
  const open = (await api.tabs.query({ url: `${origin}/*` })).filter((t) => t.status === "complete" && !lib.looksLikePdf(t.url, ""));
  let tab = open[0] || null;
  let created = false;
  if (!tab) {
    tab = await api.tabs.create({ url: `${origin}/`, active: false });
    created = true;
    await waitForTab(tab.id, 20000);
    await new Promise((r) => setTimeout(r, 1500)); // a challenge page settles
  }
  try {
    const results = await api.scripting.executeScript({ target: { tabId: tab.id }, func: fetchInPage, args: [url] });
    const r = results && results[0] ? results[0].result : null;
    if (!r || r.error) throw new Error((r && r.error) || "no answer from the page");
    const bytes = Uint8Array.from(atob(r.body), (c) => c.charCodeAt(0));
    log("info", "fetched through the site's page", url, `${bytes.length} bytes`);
    return new Blob([bytes], { type: "application/pdf" });
  } finally {
    if (created) api.tabs.remove(tab.id).catch(() => {});
  }
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
  // an internal page (the Add-ons Manager, about:…, a file) cannot be read
  // by an extension: say so before trying
  if (!lib.capturable(tab.url)) return { tabId: tab.id, url: tab.url, title: tab.title || null, error: "this kind of page cannot be read" };
  const read = await readTab(tab.id);
  const url = (read && read.url) || tab.url;
  const title = (read && read.title) || tab.title || null;
  const p = lib.plan(url, read && read.html);
  if (p.mode === "skip") return { tabId: tab.id, url, title, error: p.reason };
  const common = { url, title, domains: opts.domains.length ? opts.domains : null, tags: opts.tags.length ? opts.tags : null, session: opts.session };
  if (p.mode === "html") {
    const note = read.snapshot ? "snapshot with images and styles" : (read.note || "plain DOM");
    const data = await door("/ingest/html", { ...common, html: read.html, mode: read.snapshot ? "snapshot" : "dom", note }, cfg);
    return { tabId: tab.id, url, title, mode: "html", note, ...data };
  }
  // A PDF by the look of the URL or the viewer, or a tab the browser would
  // not let us read at all (its PDF viewer is such a tab, and a publisher's
  // "view PDF" link rarely ends in .pdf): fetch it here, with the session
  // this browser has, and upload the bytes when they are a PDF. Only then
  // does the door fetch the URL itself, without any session.
  let why = p.reason || null;
  if (!read && !(await mayRead(url))) {
    // the browser would not let us into the tab, and would refuse our own
    // fetch of it just the same: say which permission is missing
    why = "no permission to read this site (grant access to all sites in the popup); the door fetched the URL instead";
  } else if (!read || lib.looksLikePdf(url, read.html)) {
    let blob = null;
    let refused = null;
    try { blob = await fetchPdf(url); } catch (err) { log("warn", "own fetch failed", url, err); refused = err.message; why = `own fetch failed: ${err.message}; the door fetched instead`; blob = null; }
    if (blob) {
      const data = await uploadFile(blob, lib.pdfFileName(url), common, cfg);
      return { tabId: tab.id, url, title, mode: "file", note: "PDF fetched with your session and uploaded", ...data };
    }
    if (refused && /answered 4\d\d/.test(refused)) {
      // the site refuses the extension's own request: the door will fare no
      // better; a request from inside one of the site's pages may, and the
      // browser's downloader is the last try
      try {
        blob = await fetchViaSite(url);
      } catch (err) { log("warn", "fetch through the site's page failed", url, err); blob = null; }
      if (blob) {
        const data = await uploadFile(blob, lib.pdfFileName(url), common, cfg);
        return { tabId: tab.id, url, title, mode: "file", note: "PDF fetched through the site's own page and uploaded", ...data };
      }
      const r = await downloadRoute(url, common);
      return { tabId: tab.id, url, title: title || r.title, ...r };
    }
  }
  const data = await door("/ingest/url", common, cfg);
  return { tabId: tab.id, url, title, mode: "url", note: why, ...data };
}

/* Whether this extension may read pages of that site: the host permission
   is optional, asked for once from the popup. */
async function mayRead(url) {
  let origin;
  try { const u = new URL(url); origin = `${u.protocol}//${u.host}/*`; } catch (_) { return false; }
  try { return await api.permissions.contains({ origins: [origin] }); } catch (_) { return true; }
}

async function setProgress(patch) {
  const cur = (await progressArea().get("progress")).progress || {};
  await progressArea().set({ progress: { ...cur, ...patch, at: Date.now() } });
}

/* The history: the last sends, newest first, kept in session storage so
   the popup shows them again after it closed (a tab switch closes it).
   A failed entry keeps what it needs for a retry. */
const HISTORY_LINES = 40;
let historyChain = Promise.resolve();
function remember(entry) {
  historyChain = historyChain.then(async () => {
    const cur = (await progressArea().get("history")).history || [];
    const rest = cur.filter((h) => h.id !== entry.id);
    await progressArea().set({ history: [entry, ...rest].slice(0, HISTORY_LINES) });
  }).catch(() => { /* storage unavailable */ });
  return historyChain;
}

async function capture(msg) {
  log("info", "capture", msg.tabIds);
  const cfg = await settings();
  if (!cfg.server) {
    await setProgress({ state: "error", error: "no server configured (options)", results: [] });
    return;
  }
  const opts = { domains: msg.domains || [], tags: msg.tags || [], session: msg.session || lib.sessionId(), close: !!msg.close };
  const tabs = [];
  for (const id of msg.tabIds) {
    try { tabs.push(await api.tabs.get(id)); } catch (_) { /* closed meanwhile */ }
  }
  if (!tabs.length) {
    await setProgress({ state: "error", error: "that tab is gone", results: [] });
    return;
  }
  await setProgress({ state: "running", session: opts.session, total: tabs.length, done: 0, results: [], error: null });
  const results = [];
  for (const tab of tabs) {
    // a retry keeps the failed entry's id, so it is updated in place
    const id = (msg.entryIds && msg.entryIds[tab.id]) || `${Date.now()}-${tab.id}`;
    await remember({ id, at: Date.now(), tabId: tab.id, url: tab.url, title: tab.title, state: "sending", domains: opts.domains, tags: opts.tags, close: opts.close });
    let r;
    // one tab never holds up the rest: five minutes, then on to the next
    try { r = await withTimeout(captureTab(tab, opts, cfg), TAB_TIMEOUT_MS, "this tab"); log("info", "sent", tab.url, r.mode, r.doc_id ? `doc ${r.doc_id}` : ""); } catch (err) { log("warn", "capture failed", tab.url, err); r = { tabId: tab.id, url: tab.url, title: tab.title, error: err.message }; }
    results.push(r);
    await remember({ id, at: Date.now(), ...r, state: r.error ? "failed" : r.manual ? "manual" : "done", domains: opts.domains, tags: opts.tags, close: opts.close });
    await setProgress({ done: results.length, results });
    if (opts.close && !r.error) {
      try { await api.tabs.remove(tab.id); } catch (_) { /* already gone */ }
    }
  }
  await setProgress({ state: "done", done: results.length, results });
}

/* A retry of a failed entry: the same tab if it is still open, else the
   first tab showing that URL, else a fresh tab with it (kept open). */
async function retry(entry) {
  let tab = null;
  try { tab = await api.tabs.get(entry.tabId); if (tab.url !== entry.url) tab = null; } catch (_) { tab = null; }
  if (!tab) {
    const same = await api.tabs.query({ url: entry.url }).catch(() => []);
    tab = same[0] || null;
  }
  if (!tab) tab = await api.tabs.create({ url: entry.url, active: false });
  await capture({ tabIds: [tab.id], entryIds: { [tab.id]: entry.id }, domains: entry.domains || [], tags: entry.tags || [], close: false });
}

/* Every failed entry again, one after the other, each with its own
   domains and tags; entries are updated in place as they go. */
async function retryFailed() {
  const cur = (await progressArea().get("history")).history || [];
  const failed = cur.filter((h) => h.state === "failed");
  log("info", "retry failed", failed.length);
  for (const entry of failed) {
    try { await retry(entry); } catch (err) { log("warn", "retry failed", entry.url, err); }
  }
}

/* What SingleFile's content scripts expect from the extension's background
   (the SingleFile extension provides the same two services):
   - the lazy-image loader asks the background to run its timers, because a
     tab's own timers are throttled: "singlefile.lazyTimeout.setTimeout"
     {type, delay} is answered later with "singlefile.lazyTimeout.onTimeout"
     {type} sent to the same frame; "…clearTimeout" cancels;
   - a frame's answers to the top frame ("singlefile.frameTree.initResponse",
     "…ackInitRequest") go through the background to frame 0.
   Without the first, a snapshot waits forever. */
const lazyTimers = new Map();

function lazyKey(sender, type) {
  return `${sender.tab ? sender.tab.id : "?"}:${sender.frameId || 0}:${type}`;
}

function lazySetTimeout(msg, sender) {
  const key = lazyKey(sender, msg.type);
  clearTimeout(lazyTimers.get(key));
  lazyTimers.set(key, setTimeout(() => {
    lazyTimers.delete(key);
    if (!sender.tab) return;
    api.tabs.sendMessage(sender.tab.id, { method: "singlefile.lazyTimeout.onTimeout", type: msg.type }, { frameId: sender.frameId || 0 }).catch(() => { /* the tab is gone */ });
  }, msg.delay || 0));
}

function lazyClearTimeout(msg, sender) {
  const key = lazyKey(sender, msg.type);
  clearTimeout(lazyTimers.get(key));
  lazyTimers.delete(key);
}

/* The context menu: "Send this page to prax" on any page, the PDF viewer
   included (its own page cannot be read, so the file is fetched with the
   session, as from the popup), and "Send link to prax" on a link, which
   fetches the linked file the same way without opening it. Domains and
   tags are the options page's defaults. */
async function captureLink(linkUrl, tab) {
  const cfg = await settings();
  if (!cfg.server) { await setProgress({ state: "error", error: "no server configured (options)", results: [] }); return; }
  const id = `${Date.now()}-link`;
  const common = { url: linkUrl, title: null, domains: cfg.domains.length ? cfg.domains : null, tags: null, session: lib.sessionId() };
  await remember({ id, at: Date.now(), tabId: tab ? tab.id : null, url: linkUrl, title: linkUrl, state: "sending", domains: cfg.domains, tags: [] });
  let r;
  try {
    let blob = null;
    let why = null;
    let refused = null;
    try { blob = await fetchPdf(linkUrl); } catch (err) { refused = err.message; why = `own fetch failed: ${err.message}; the door fetched instead`; }
    if (blob) {
      const data = await uploadFile(blob, lib.pdfFileName(linkUrl), common, cfg);
      r = { url: linkUrl, title: lib.pdfFileName(linkUrl), mode: "file", note: "PDF fetched with your session and uploaded", ...data };
    } else if (refused && /answered 4\d\d/.test(refused)) {
      let viaSite = null;
      try { viaSite = await fetchViaSite(linkUrl); } catch (err) { log("warn", "fetch through the site's page failed", linkUrl, err); }
      if (viaSite) {
        const data = await uploadFile(viaSite, lib.pdfFileName(linkUrl), common, cfg);
        r = { url: linkUrl, title: lib.pdfFileName(linkUrl), mode: "file", note: "PDF fetched through the site's own page and uploaded", ...data };
      } else {
        r = { url: linkUrl, ...(await downloadRoute(linkUrl, common)) };
      }
    } else {
      const data = await door("/ingest/url", common, cfg);
      r = { url: linkUrl, title: linkUrl, mode: "url", note: why, ...data };
    }
  } catch (err) {
    r = { url: linkUrl, title: linkUrl, error: err.message };
  }
  await remember({ id, at: Date.now(), tabId: tab ? tab.id : null, ...r, state: r.error ? "failed" : r.manual ? "manual" : "done", domains: cfg.domains, tags: [] });
}

function installMenus() {
  const menus = api.contextMenus || api.menus;
  if (!menus) return;
  try {
    menus.removeAll(() => {
      menus.create({ id: "prax-page", title: "Send this page to prax", contexts: ["page", "frame", "selection", "image"] });
      menus.create({ id: "prax-link", title: "Send link to prax", contexts: ["link"] });
    });
  } catch (_) { /* no menus in this browser */ }
}
if (api.runtime.onInstalled) api.runtime.onInstalled.addListener(installMenus);
if (api.runtime.onStartup) api.runtime.onStartup.addListener(installMenus);
installMenus();
if (api.contextMenus || api.menus) {
  (api.contextMenus || api.menus).onClicked.addListener(async (info, tab) => {
    const cfg = await settings();
    if (info.menuItemId === "prax-link" && info.linkUrl) {
      captureLink(info.linkUrl, tab).catch((err) => log("warn", "link capture failed", err));
    } else if (info.menuItemId === "prax-page" && tab) {
      capture({ tabIds: [tab.id], domains: cfg.domains, tags: [], close: false }).catch((err) => log("warn", "capture failed", err));
    }
  });
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return false;
  if (msg.type === "capture") {
    capture(msg).catch((err) => setProgress({ state: "error", error: err.message }));
    sendResponse({ ok: true });
    return false;
  }
  if (msg.type === "fetch") {
    bridgeFetch(msg.url).then(sendResponse, (err) => { log("warn", "resource fetch failed", msg.url, err); sendResponse({ error: err.message }); });
    return true; // answered asynchronously
  }
  if (msg.type === "retry") {
    retry(msg.entry).catch((err) => setProgress({ state: "error", error: err.message }));
    sendResponse({ ok: true });
    return false;
  }
  if (msg.type === "retry-failed") {
    retryFailed().catch((err) => setProgress({ state: "error", error: err.message }));
    sendResponse({ ok: true });
    return false;
  }
  if (msg.type === "clear-history") {
    progressArea().set({ history: [] }).then(() => sendResponse({}), () => sendResponse({}));
    return true;
  }
  if (msg.type === "clear-log") {
    progressArea().set({ log: [] }).then(() => sendResponse({}), () => sendResponse({}));
    return true;
  }
  if (msg.method === "singlefile.lazyTimeout.setTimeout") {
    lazySetTimeout(msg, sender);
    sendResponse({});
    return false;
  }
  if (msg.method === "singlefile.lazyTimeout.clearTimeout") {
    lazyClearTimeout(msg, sender);
    sendResponse({});
    return false;
  }
  if (msg.method === "singlefile.frameTree.initResponse" || msg.method === "singlefile.frameTree.ackInitRequest") {
    if (sender.tab) api.tabs.sendMessage(sender.tab.id, msg, { frameId: 0 }).catch(() => { /* the top frame is gone */ });
    sendResponse({});
    return false;
  }
  return false;
});
