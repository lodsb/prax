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
  let res;
  try {
    res = await fetch(`${cfg.server}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
  } catch (err) {
    // the browser's word for "nothing there" is a TypeError: say whose door
    throw new Error(`the door at ${cfg.server} did not answer (${err.message}); is prax up?`);
  }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) throw new Error(`the door at ${cfg.server} refused the token (options)`);
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

/* A video page: the transcript and a frame every so often, as one
   document (lib.videoHtml, the shape the door's video parser reads). The
   page's own player gives the recording's details and its caption
   tracks (a function run in the page's world: the player object is not
   visible from the extension's); the captions are fetched from inside the
   tab, with its session; the frames are drawn from the tab's <video>
   after seeking it, muted, the playback restored after; a frame that
   looks like the one before is dropped. Nothing is downloaded. */
const FRAME_DEFAULTS = { interval: 30, cap: 150, width: 1280, quality: 0.8, minChange: 6 };

function readPlayerMain() {
  // runs in the page's world (world: MAIN): YouTube's player, or the
  // initial player response the page came with
  const player = document.getElementById("movie_player");
  let r = null;
  try { r = player && typeof player.getPlayerResponse === "function" ? player.getPlayerResponse() : null; } catch (_) { r = null; }
  if (!r && typeof ytInitialPlayerResponse !== "undefined") r = ytInitialPlayerResponse;
  if (!r) return null;
  const d = r.videoDetails || {};
  const tracks = ((((r.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks) || []).map((t) => ({
    baseUrl: t.baseUrl, languageCode: t.languageCode || "", kind: t.kind || "",
    name: t.name ? (t.name.simpleText || (t.name.runs || []).map((x) => x.text).join("")) : "",
  }));
  const mf = (r.microformat || {}).playerMicroformatRenderer || {};
  const v = document.querySelector("video");
  return {
    videoId: d.videoId || null, title: d.title || document.title || "", author: d.author || "",
    lengthSeconds: Number(d.lengthSeconds) || 0, description: d.shortDescription || "",
    publishDate: (mf.publishDate || mf.uploadDate || "").slice(0, 10), tracks,
    duration: v && isFinite(v.duration) ? v.duration : 0, hasVideo: !!v,
    storyboard: ((r.storyboards || {}).playerStoryboardSpecRenderer || {}).spec || null,
  };
}

async function fetchTracksInnertube(videoId) {
  // runs in the tab. The caption URLs the watch page hands out answer
  // empty unless the page's own player asks (a session token it adds);
  // InnerTube's player as the iOS client hands out ones that answer, and
  // Android's do too, in the XML form. Asked at the page's origin, so a
  // test bed's page asks its own server.
  const clients = [
    { clientName: "IOS", clientVersion: "20.10.4", deviceModel: "iPhone16,2", hl: "en" },
    { clientName: "ANDROID", clientVersion: "20.10.38", androidSdkVersion: 30, hl: "en" },
  ];
  for (const client of clients) {
    try {
      const res = await fetch(`${location.origin}/youtubei/v1/player?prettyPrint=false`, {
        method: "POST", credentials: "omit", headers: { "content-type": "application/json" },
        body: JSON.stringify({ context: { client }, videoId, contentCheckOk: true, racyCheckOk: true }),
      });
      if (!res.ok) continue;
      const j = await res.json();
      if (j && j.playabilityStatus && j.playabilityStatus.status !== "OK") continue;
      const ts = ((((j || {}).captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks) || [];
      if (ts.length) {
        return { client: client.clientName, tracks: ts.map((t) => ({ baseUrl: t.baseUrl, languageCode: t.languageCode || "", kind: t.kind || "", name: t.name ? (t.name.simpleText || (t.name.runs || []).map((x) => x.text).join("")) : "" })) };
      }
    } catch (_) { /* the next client */ }
  }
  return { client: null, tracks: [] };
}

async function fetchCaptions(url) {
  // runs in the tab (the extension's world, the page's cookies): the track
  // as json3, or in the XML form some clients answer with, as events
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) return { error: `captions: ${r.status}` };
  const text = await r.text();
  if (!text.trim()) return { error: "captions: an empty answer (the URL wants the page's own player)" };
  try { return { events: JSON.parse(text).events || [] }; } catch (_) { /* not json3 */ }
  if (/^\s*<\?xml|^\s*<timedtext|^\s*<transcript/.test(text)) {
    const doc = new DOMParser().parseFromString(text, "text/xml");
    const events = [];
    for (const p of doc.querySelectorAll("p, text")) {
      const t = Number(p.getAttribute("t") || Math.round(Number(p.getAttribute("start") || 0) * 1000)) || 0;
      const d = Number(p.getAttribute("d") || Math.round(Number(p.getAttribute("dur") || 0) * 1000)) || 0;
      const words = p.textContent.replace(/\s+/g, " ").trim();
      if (words) events.push({ tStartMs: t, dDurationMs: d, segs: [{ utf8: words }] });
    }
    return { events };
  }
  return { error: "captions: neither json3 nor timedtext xml" };
}

async function grabFrames(times, opt, prev) {
  // runs in the page's world (world: MAIN): the page's player does the
  // seeking (it fetches the segment; a bare <video>.currentTime on a
  // paused YouTube player mostly times out and shows the old frame),
  // then the frame is drawn once it has been presented
  const v = document.querySelector("video");
  if (!v) return { frames: [], prev: null, error: "no video element" };
  const player = document.getElementById("movie_player");
  const api_ = player && typeof player.seekTo === "function" ? player : null;
  const state = { t: v.currentTime, paused: v.paused, muted: v.muted };
  v.muted = true;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  if (v.readyState === 0) {
    // nothing loaded yet: start it (muted) so the player has data, then hold
    try { api_ ? api_.playVideo() : await v.play(); } catch (_) { /* no autoplay: the seeks may still load */ }
    for (let i = 0; i < 40 && v.readyState < 2; i++) await wait(100);
  }
  if (api_) api_.pauseVideo(); else if (!v.paused) v.pause();
  const vw = v.videoWidth || 1280, vh = v.videoHeight || 720;
  const w = Math.min(opt.width, vw), h = Math.max(1, Math.round(w * vh / vw));
  const canvas = document.createElement("canvas"); canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d");
  const small = document.createElement("canvas"); small.width = 32; small.height = 18;
  const sctx = small.getContext("2d", { willReadFrequently: true });
  const presented = () => new Promise((resolve) => {
    // the next frame the video presents, or a beat if none comes
    let done = false;
    const fin = () => { if (!done) { done = true; resolve(); } };
    if (typeof v.requestVideoFrameCallback === "function") v.requestVideoFrameCallback(() => fin());
    setTimeout(fin, opt.frameTimeout || 800);
  });
  const seek = async (t) => {
    if (api_) api_.seekTo(t, true); else v.currentTime = t;
    const until = Date.now() + (opt.seekTimeout || 6000);
    while (Date.now() < until) {
      if (!v.seeking && v.readyState >= 2 && Math.abs(v.currentTime - t) < 1.5) return true;
      await wait(100);
    }
    return false;
  };
  const frames = [];
  let last = prev || null;
  let error = null;
  const missed = [];
  let gaveUp = false;
  let inARow = 0;
  for (const t of times) {
    if (gaveUp) { missed.push(t); continue; }
    const ok = await seek(t);
    if (!ok) {
      missed.push(t);
      inARow += 1;
      if (inARow >= 3) gaveUp = true;  // the player is not delivering: the storyboard's turn
      continue;  // never arrived: no stale picture
    }
    inARow = 0;
    await presented();
    try {
      ctx.drawImage(v, 0, 0, w, h);
      sctx.drawImage(v, 0, 0, 32, 18);
    } catch (e) { error = `cannot draw the video: ${e.message}`; break; }
    const px = sctx.getImageData(0, 0, 32, 18).data;
    const rgb = [];  // the colour, not a grey: two slides can share a luminance
    for (let i = 0; i < px.length; i += 4) rgb.push(px[i], px[i + 1], px[i + 2]);
    if (last && last.length === rgb.length) {
      let diff = 0;
      for (let i = 0; i < rgb.length; i++) diff += Math.abs(rgb[i] - last[i]);
      if (diff / rgb.length < opt.minChange) continue; // the same picture as before
    }
    last = rgb;
    let dataUrl;
    try { dataUrl = canvas.toDataURL("image/jpeg", opt.quality); } catch (e) { error = `cannot read the frame: ${e.message}`; break; }
    frames.push({ t, dataUrl });
  }
  if (opt.restore) {
    try {
      if (api_) { api_.seekTo(state.t, true); if (!state.paused) api_.playVideo(); } else { v.currentTime = state.t; if (!state.paused) v.play().catch(() => {}); }
      v.muted = state.muted;
    } catch (_) { /* the page's business */ }
  }
  return { frames, prev: last, error, missed, gaveUp };
}

async function storyboardFrames(plan, opt) {
  // runs in the tab: the seek bar's preview pictures for the moments the
  // player did not deliver — small (320x180 at best), but there for any
  // video, played or not. Sheets are fetched once each, from the page or
  // through the background; a picture like the one before is dropped.
  const bridge = globalThis.browser || globalThis.chrome;
  const sheets = new Map();
  const load = async (url) => {
    if (sheets.has(url)) return sheets.get(url);
    let blob = null;
    try { const r = await fetch(url); if (r.ok) blob = await r.blob(); } catch (_) { blob = null; }
    if (!blob) {
      try {
        const m = await bridge.runtime.sendMessage({ type: "fetch", url });
        if (m && m.body) blob = new Blob([Uint8Array.from(atob(m.body), (c) => c.charCodeAt(0))], { type: (m.headers && m.headers["content-type"]) || "image/jpeg" });
      } catch (_) { blob = null; }
    }
    let bmp = null;
    try { bmp = blob ? await createImageBitmap(blob) : null; } catch (_) { bmp = null; }
    sheets.set(url, bmp);
    return bmp;
  };
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  const small = document.createElement("canvas"); small.width = 32; small.height = 18;
  const sctx = small.getContext("2d", { willReadFrequently: true });
  const frames = [];
  let last = null;
  let missing = 0;
  for (const p of plan) {
    const bmp = await load(p.url);
    if (!bmp) { missing += 1; continue; }
    canvas.width = p.w; canvas.height = p.h;
    ctx.drawImage(bmp, p.x, p.y, p.w, p.h, 0, 0, p.w, p.h);
    sctx.drawImage(bmp, p.x, p.y, p.w, p.h, 0, 0, 32, 18);
    const px = sctx.getImageData(0, 0, 32, 18).data;
    const rgb = [];
    for (let i = 0; i < px.length; i += 4) rgb.push(px[i], px[i + 1], px[i + 2]);
    if (last) { let diff = 0; for (let i = 0; i < rgb.length; i++) diff += Math.abs(rgb[i] - last[i]); if (diff / rgb.length < opt.minChange) continue; }
    last = rgb;
    frames.push({ t: p.t, dataUrl: canvas.toDataURL("image/jpeg", 0.85), small: true });
  }
  return { frames, missing };
}

async function probeVideo(tabId) {
  // whether the page has a player (the fixture of the test bed has one
  // without being on youtube.com)
  try {
    const r = await api.scripting.executeScript({ target: { tabId }, func: () => !!document.getElementById("movie_player") });
    return !!(r && r[0] && r[0].result);
  } catch (_) { return false; }
}

async function captureVideo(tab, opts, cfg, common) {
  let info = null;
  try {
    const r = await api.scripting.executeScript({ target: { tabId: tab.id }, func: readPlayerMain, world: "MAIN" });
    info = r && r[0] ? r[0].result : null;
  } catch (err) { log("warn", "no player response", err); }
  log("info", "player", info ? `${info.videoId || "?"} ${info.duration}s video=${info.hasVideo} tracks=${(info.tracks || []).length}` : "none");
  if (!info || !info.hasVideo) return null; // not a video page after all: the usual capture
  const known = lib.videoOfUrl(tab.url) || {};
  const url = known.id ? `https://www.youtube.com/watch?v=${known.id}` : tab.url;
  const duration = Math.floor(info.duration || info.lengthSeconds || 0);
  const video = {
    provider: known.provider || "youtube", id: info.videoId || known.id || null, url,
    channel: info.author || "", duration, published: info.publishDate || "",
    captions: null, language: null, chapters: lib.chaptersFrom(info.description),
  };
  const notes = [];
  // the transcript: the caption tracks InnerTube hands out for the iOS
  // client answer; the page's own are gated, the last resort
  let paragraphs = [];
  let tracks = info.tracks || [];
  if (video.id) {
    try {
      const r = await api.scripting.executeScript({ target: { tabId: tab.id }, func: fetchTracksInnertube, args: [video.id] });
      const got = r && r[0] ? r[0].result : null;
      if (got && got.tracks && got.tracks.length) { tracks = got.tracks; log("info", "caption tracks via", got.client, got.tracks.length); }
    } catch (err) { log("warn", "innertube", err); }
  }
  const track = lib.chooseTrack(tracks, navigator.language);
  if (track) {
    const sep = track.baseUrl.includes("?") ? "&" : "?";
    const r = await api.scripting.executeScript({ target: { tabId: tab.id }, func: fetchCaptions, args: [`${track.baseUrl}${sep}fmt=json3`] });
    const got = r && r[0] ? r[0].result : null;
    if (got && got.events) {
      paragraphs = lib.groupCaptions(got.events);
      video.captions = track.kind === "asr" ? "asr" : "uploaded";
      video.language = track.languageCode || null;
      notes.push(`transcript (${track.languageCode || "?"}${track.kind === "asr" ? ", automatic" : ""}, ${paragraphs.length} paragraphs)`);
    } else {
      notes.push(`no transcript (${(got && got.error) || "the captions did not load"})`);
    }
  } else {
    notes.push("no captions");
  }
  // the frames
  const settings_ = await api.storage.local.get(["frame_interval", "frame_cap", "frame_width"]);
  const fopt = { ...FRAME_DEFAULTS, interval: Number(settings_.frame_interval) || FRAME_DEFAULTS.interval, cap: Number(settings_.frame_cap) || FRAME_DEFAULTS.cap, width: Number(settings_.frame_width) || FRAME_DEFAULTS.width };
  const times = lib.frameTimes(duration, fopt.interval, fopt.cap);
  const frames = [];
  let prev = null;
  let frameError = null;
  const missedTimes = [];
  const batch = 12;
  for (let i = 0; i < times.length; i += batch) {
    const part = times.slice(i, i + batch);
    const restore = i + batch >= times.length;
    let got = null;
    try {
      const r = await api.scripting.executeScript({ target: { tabId: tab.id }, func: grabFrames, args: [part, { ...fopt, restore }, prev], world: "MAIN" });
      got = r && r[0] ? r[0].result : null;
    } catch (err) { frameError = err.message; break; }
    if (!got) { frameError = "the tab answered nothing"; break; }
    frames.push(...(got.frames || []));
    if (got.missed && got.missed.length) missedTimes.push(...got.missed);
    prev = got.prev;
    await setProgress({ note: `${frames.length} frames of ${times.length} moments…` });
    if (got.error) { frameError = got.error; break; }
    if (got.gaveUp) { missedTimes.push(...times.slice(i + batch)); break; }
  }
  // the moments the player did not deliver, from the storyboard
  let smallFrames = 0;
  const wanted = missedTimes.length ? missedTimes : (frames.length ? [] : times);
  const board = lib.parseStoryboard(info.storyboard);
  if (wanted.length && board) {
    try {
      const r = await api.scripting.executeScript({ target: { tabId: tab.id }, func: storyboardFrames, args: [lib.storyboardPlan(board, wanted), { minChange: fopt.minChange }] });
      const got = r && r[0] ? r[0].result : null;
      if (got && got.frames && got.frames.length) {
        frames.push(...got.frames);
        frames.sort((a, b) => a.t - b.t);
        smallFrames = got.frames.length;
      }
    } catch (err) { log("warn", "storyboard", err); }
  }
  const stillMissed = missedTimes.length && !smallFrames ? ` (${missedTimes.length} moments never arrived)` : "";
  notes.push(frames.length ? `${frames.length} frames${smallFrames ? ` (${smallFrames} small, from the storyboard)` : ""}${stillMissed}` : `no frames${frameError ? ` (${frameError})` : stillMissed}`);
  if (!paragraphs.length && !frames.length) return { ...common, tabId: tab.id, mode: "video", error: `nothing to keep of the video: ${notes.join(", ")}` };
  const html = lib.videoHtml({ video, title: info.title || tab.title || "", description: info.description || "", paragraphs, frames });
  const note = notes.join(", ");
  const data = await door("/ingest/html", { ...common, url, title: info.title || common.title, html, mode: "video", note, video }, cfg);
  return { tabId: tab.id, url, title: info.title || common.title, mode: "video", note, ...data };
}

async function captureTab(tab, opts, cfg) {
  // an internal page (the Add-ons Manager, about:…, a file) cannot be read
  // by an extension: say so before trying
  if (!lib.capturable(tab.url)) return { tabId: tab.id, url: tab.url, title: tab.title || null, error: "this kind of page cannot be read" };
  if (lib.videoOfUrl(tab.url) || await probeVideo(tab.id)) {
    const common = { url: tab.url, title: tab.title || null, domains: opts.domains.length ? opts.domains : null, tags: opts.tags.length ? opts.tags : null, session: opts.session };
    const v = await captureVideo(tab, opts, cfg, common);
    if (v) return v;
  }
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

/* The toolbar icon's badge: the one piece of feedback a send from the
   keyboard or the context menu has, the popup being closed. "…" while it
   runs, "✓" for a few seconds when every tab went, "!" (kept) when one
   did not; the popup clears it when opened. */
const BADGE_MS = 4000;
let badgeTimer = null;
async function badge(text, color, keep) {
  const action = api.action || api.browserAction;
  if (!action || !action.setBadgeText) return;
  clearTimeout(badgeTimer);
  try {
    await action.setBadgeBackgroundColor({ color });
    if (action.setBadgeTextColor) await action.setBadgeTextColor({ color: "#fff" });
    await action.setBadgeText({ text });
  } catch (_) { /* no badge in this browser */ }
  if (text && !keep) badgeTimer = setTimeout(() => badge("", color, true), BADGE_MS);
}

async function capture(msg) {
  log("info", "capture", msg.tabIds);
  await badge("…", "#2f5d8a", true);
  const cfg = await settings();
  if (!cfg.server) {
    await setProgress({ state: "error", error: "no server configured (options)", results: [] });
    await badge("!", "#b3261e", true);
    return;
  }
  const opts = { domains: msg.domains || [], tags: msg.tags || [], session: msg.session || lib.sessionId(), close: !!msg.close };
  const tabs = [];
  for (const id of msg.tabIds) {
    try { tabs.push(await api.tabs.get(id)); } catch (_) { /* closed meanwhile */ }
  }
  if (!tabs.length) {
    await setProgress({ state: "error", error: "that tab is gone", results: [] });
    await badge("!", "#b3261e", true);
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
  const failed = results.some((r) => r.error);
  await badge(failed ? "!" : "✓", failed ? "#b3261e" : "#2e7d32", failed);
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

/* The tab in front, with the default domains: the keyboard command, and
   the same by message (what the test bed presses). */
async function sendActive() {
  const cfg = await settings();
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  await capture({ tabIds: [tab.id], domains: cfg.domains, tags: [], close: false });
}
if (api.commands && api.commands.onCommand) {
  api.commands.onCommand.addListener((name) => {
    if (name === "send-tab") sendActive().catch((err) => log("warn", "capture failed", err));
  });
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return false;
  if (msg.type === "badge-seen") { badge("", "#2f5d8a", true); sendResponse({ ok: true }); return false; }
  if (msg.type === "send-active") { sendActive().catch((err) => log("warn", "capture failed", err)); sendResponse({ ok: true }); return false; }
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
