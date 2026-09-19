#!/usr/bin/env node
/* The browser extension, end to end, without a hand on the mouse.

     node scripts/extension_bed.mjs                       # Chrome, a throwaway door
     node scripts/extension_bed.mjs --browser firefox     # Firefox or Waterfox, through geckodriver
     node scripts/extension_bed.mjs --browser both
     node scripts/extension_bed.mjs --keep                # leave the door and the browser up
     node scripts/extension_bed.mjs --door http://127.0.0.1:8000 --token …   # a door of yours

   What it does: serves a fixture page (text, a stylesheet, an image, a
   frame) on a port of its own; starts a door on a temporary data
   directory (`prax serve`, its own store, nothing of yours); launches
   the browser headless with the extension loaded and drives it — Chrome
   over the DevTools protocol (the extension loaded with
   Extensions.loadUnpacked: branded Chrome dropped --load-extension),
   Firefox over geckodriver's WebDriver (the add-on installed
   temporarily under a UUID of our choosing, its pages opened from the
   browser's own chrome context since WebDriver may not navigate to
   them). In either: opens the fixture in a tab, gives the extension its
   settings the way the options page would (storage.local), checks the
   options page's "test connection", opens the popup as a page and
   checks it shows the door reachable and its domains, asks the
   background to capture the tab exactly as "send this tab" does and
   watches the progress it writes to storage, then asks the door what it
   received: one capture of the fixture URL, a snapshot with the image
   inlined, the stylesheet and the frame's text in it, the domain and
   the tag on it, and no second document when sent again. Every check
   prints a line; a failed one fails the run.

   Needs: node 22+, the repo's .venv; Chrome (or CHROME=…); for Firefox,
   geckodriver (GECKODRIVER=…, else ~/.local/share/prax/tools or
   %LOCALAPPDATA%/prax/tools) and Firefox or Waterfox (FIREFOX=…). */

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import zlib from "node:zlib";
import { mkdtempSync, readFileSync, readdirSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..");
const args = process.argv.slice(2);
const opt = (name, fallback) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : fallback; };
const KEEP = args.includes("--keep");
const BROWSER = opt("--browser", "chrome");
const CHROME = process.env.CHROME || ["C:/Program Files (x86)/Google/Chrome/Application/chrome.exe", "C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(existsSync);
const FIREFOX = process.env.FIREFOX || ["C:/Program Files/Waterfox/waterfox.exe", "C:/Program Files/Mozilla Firefox/firefox.exe", "/usr/bin/firefox", "/Applications/Firefox.app/Contents/MacOS/firefox"].find(existsSync);
const TOOLS = process.platform === "win32" ? join(process.env.LOCALAPPDATA || "", "prax", "tools") : join(process.env.HOME || "", ".local", "share", "prax", "tools");
const GECKODRIVER = process.env.GECKODRIVER || [join(TOOLS, "geckodriver.exe"), join(TOOLS, "geckodriver"), "/usr/bin/geckodriver", "/usr/local/bin/geckodriver"].find(existsSync);
const EXT_UUID = "3d9f0a2b-1c4e-4f6a-9b8d-7e5c2a1f0b33"; // the add-on's page address in Firefox, ours to choose
const EXT = join(repo, "clients", "browser-extension");
const PY = process.platform === "win32" ? join(repo, ".venv", "Scripts", "python.exe") : join(repo, ".venv", "bin", "python");

let failed = 0;
function check(ok, what, detail = "") {
  console.log(`${ok ? "  ok " : "  FAIL"} ${what}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failed += 1;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- fixture
const PDF = (() => {
  const fixtures = join(repo, "tests", "fixtures", "zotero", "storage", "FCEK3EI9");
  const f = existsSync(fixtures) ? readdirSync(fixtures).find((n) => n.toLowerCase().endsWith(".pdf")) : null;
  return f ? readFileSync(join(fixtures, f)) : null;
})();
// an 8x8 red PNG, built rather than pasted: Firefox refuses an image whose
// CRC or zlib check is off (Chrome shrugs), and SingleFile never fetches a
// broken image
const PNG = (() => {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type, "latin1"), data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(body));
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(8, 0); ihdr.writeUInt32BE(8, 4); ihdr[8] = 8; ihdr[9] = 2; // 8-bit RGB
  const raw = Buffer.alloc(8 * (1 + 8 * 3)); for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) raw[y * 25 + 1 + x * 3] = 0xff;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
})();
const TRACE = args.includes("--trace");
const fixture = createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  if (TRACE) console.log(`  [fixture] ${req.method} ${url.pathname} ${req.headers["sec-fetch-dest"] || ""} ${req.headers["sec-fetch-mode"] || ""} origin=${req.headers.origin || "-"} ua=${(req.headers["user-agent"] || "").slice(0, 30)}`);
  if (url.pathname === "/article") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(`<!doctype html><html><head><title>The Bed Article</title><link rel="stylesheet" href="/style.css"></head>
<body><h1>The Bed Article</h1><p class="lede">A page the extension must carry over whole: text, its stylesheet, an image and a frame.</p>
<img src="/dot.png" alt="a dot"><p>${"Granular synthesis scatters grains of sound. ".repeat(30)}</p>
<iframe src="/frame" title="the frame"></iframe></body></html>`);
  } else if (url.pathname === "/frame") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end("<!doctype html><html><body><p>FRAME-TEXT-7731 lives in the frame.</p></body></html>");
  } else if (url.pathname === "/style.css") {
    res.writeHead(200, { "content-type": "text/css" });
    res.end(".lede { font-style: italic; color: rgb(10, 20, 30); }");
  } else if (url.pathname === "/dot.png") {
    res.writeHead(200, { "content-type": "image/png" });
    res.end(PNG);
  } else if (url.pathname === "/paper.pdf") {
    res.writeHead(200, { "content-type": "application/pdf" });
    res.end(PDF);
  } else if (url.pathname === "/second") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(`<!doctype html><html><head><title>The Second Page</title></head><body><p>${"A second page for the whole-window send. ".repeat(20)}</p></body></html>`);
  } else {
    res.writeHead(404); res.end();
  }
});
await new Promise((r) => fixture.listen(0, "127.0.0.1", r));
const FIXTURE = `http://127.0.0.1:${fixture.address().port}`;
console.log(`fixture at ${FIXTURE}/article`);

// ------------------------------------------------------------------- door
let door = opt("--door", null);
let token = opt("--token", "");
let doorProc = null;
let dataDir = null;
async function get(path, init = {}) {
  const headers = { ...(init.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(`${door}${path}`, { ...init, headers });
  return r;
}
if (!door) {
  dataDir = mkdtempSync(join(tmpdir(), "prax-bed-"));
  const port = 8000 + Math.floor(Math.random() * 1000) + 100;
  door = `http://127.0.0.1:${port}`;
  const env = { ...process.env, PRAX_DATA_DIR: dataDir, PRAX_EMBED: "hash", PRAX_EXTRACT: "stub", PRAX_TITLES: "none", PRAX_INBOX_SCAN: "0", PYTHONIOENCODING: "utf-8" };
  delete env.PRAX_TOKEN;
  doorProc = spawn(PY, ["-m", "uvicorn", "prax.api:app", "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning"], { env, cwd: repo, stdio: ["ignore", "pipe", "pipe"] });
  doorProc.stderr.on("data", (d) => { if (KEEP) process.stderr.write(d); });
  let up = false;
  for (let i = 0; i < 60 && !up; i++) {
    await sleep(500);
    try { up = (await fetch(`${door}/health`)).ok; } catch (_) { /* not yet */ }
  }
  check(up, "a throwaway door is up", `${door} on ${dataDir}`);
  if (!up) process.exit(2);
} else {
  const r = await get("/health").catch(() => null);
  check(r && r.ok, "the door answers", door);
}

const runs = BROWSER === "both" ? ["chrome", "firefox"] : [BROWSER];
for (const which of runs) {
  console.log(`\n== ${which}`);
  const before = failed;
  try {
    if (which === "chrome") await runChrome();
    else await runFirefox();
  } catch (e) {
    check(false, `the ${which} run ended early`, e.message);
  }
  console.log(failed > before ? `${which}: ${failed - before} check(s) failed` : `${which}: all checks passed`);
}
if (doorProc && !KEEP) doorProc.kill();
fixture.close();
if (!KEEP && dataDir) setTimeout(() => rmSync(dataDir, { recursive: true, force: true }), 1500).unref();
console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);

// ---------------------------------------------------- the checks, either browser
// b: { evaluate(handle, expression), openPage(url) -> handle, fixtureTab: handle,
//      pageEval(expr) on the fixture, apiName: "chrome" | "browser" }
async function exercise(b) {
  const api = b.apiName;
  check((await b.pageEval("document.title")) === "The Bed Article", "the fixture page is open in a tab");
  const opts = await b.openPage(`${b.origin}/options.html`);
  await b.evaluate(opts, `${api}.storage.local.set(${JSON.stringify({ server: door, token, domains: ["research"], close: false })})`);
  const stored = await b.evaluate(opts, `${api}.storage.local.get(["server","token","domains"])`);
  check(stored.server === door && stored.domains?.[0] === "research", "the settings are stored", JSON.stringify(stored));
  await b.evaluate(opts, "location.reload()"); await sleep(1000);
  const shownServer = await b.evaluate(opts, `document.querySelector('#server') ? document.querySelector('#server').value : null`);
  check(shownServer === door, "the options page shows the server", String(shownServer));
  let tested = null;
  try {
    await b.evaluate(opts, `(async () => { const b = document.querySelector('#test'); if (b) b.click(); await new Promise(r => setTimeout(r, 2500)); })()`);
    tested = await b.evaluate(opts, `document.querySelector("#msg") ? document.querySelector("#msg").textContent : document.body.innerText.slice(0, 300)`);
  } catch (e) { tested = `error: ${e.message}`; }
  check(/answers|reachable|ok|domain/i.test(tested || ""), "the options page's test connection says the door answers", (tested || "").trim().slice(0, 120));

  const pop = await b.openPage(`${b.origin}/popup.html`);
  await sleep(2500);
  const popupState = await b.evaluate(pop, `({ server: document.querySelector('#server')?.textContent, cls: document.querySelector('#server')?.className, domains: [...document.querySelectorAll('input[name=domain]')].map(i => i.value), checked: [...document.querySelectorAll('input[name=domain]:checked')].map(i => i.value), buttons: [...document.querySelectorAll('button')].map(b => b.id || b.textContent.trim()), msg: document.querySelector('#msg')?.textContent })`);
  check(popupState.cls === "ok", "the popup finds the door reachable", `${popupState.server} [${popupState.cls}] ${popupState.msg || ""}`);
  check(popupState.domains.length > 0, "the popup lists the door's domains", popupState.domains.join(", "));
  check(popupState.checked.includes("research"), "the default domain is preselected", popupState.checked.join(", "));
  check(popupState.buttons.includes("send-tab") && popupState.buttons.includes("send-window"), "the two buttons are there", popupState.buttons.join(", "));

  const tabId = await b.evaluate(opts, `(async () => { const tabs = await ${api}.tabs.query({}); const t = tabs.find(t => t.url === ${JSON.stringify(`${FIXTURE}/article`)}); return t ? t.id : null; })()`);
  check(tabId != null, "the background can see the fixture tab", `tab ${tabId}`);
  const area = `(${api}.storage.session || ${api}.storage.local)`;
  const session = `bed-${Date.now()}`;
  const send = async (tags, sess) => {
    await b.evaluate(opts, `${area}.set({ progress: { state: "running", total: 1, done: 0, results: [] } })`);
    await b.evaluate(opts, `${api}.runtime.sendMessage({ type: "capture", tabIds: [${tabId}], domains: ["research"], tags: ${JSON.stringify(tags)}, close: false, session: ${JSON.stringify(sess)} })`);
    let progress = null;
    for (let i = 0; i < 80; i++) {
      await sleep(500);
      progress = (await b.evaluate(opts, `${area}.get("progress")`)).progress;
      if (progress && progress.state !== "running") break;
    }
    return progress;
  };
  let progress = await send(["bed"], session);
  check(progress && progress.state === "done", "the capture finished", JSON.stringify(progress).slice(0, 200));
  const result = progress?.results?.[0] || {};
  check(!result.error && result.doc_id, "the tab was sent as a snapshot", result.error || `doc ${result.doc_id} · ${result.note || result.mode || ""}`);

  const inbox = await (await get("/inbox?limit=5")).json();
  const item = (inbox.recent || []).find((r) => r.source_url === `${FIXTURE}/article` || r.title === "The Bed Article");
  check(!!item, "the door lists the capture in its inbox", item ? `${item.title} · domains ${JSON.stringify(item.domains)} · tags ${JSON.stringify(item.tags)}` : JSON.stringify(inbox).slice(0, 200));
  if (item) {
    check((item.domains || []).includes("research") && (item.tags || []).includes("bed"), "with the domain and the tag");
    const docId = item.doc_id || item.id;
    const original = await (await get(`/doc/${docId}/original`)).text();
    const img = (original.match(/<img[^>]*>/i) || ["(no img tag)"])[0];
    check(/data:image\/png;base64/.test(original), "the snapshot inlined the image", `${original.length} bytes of HTML; ${img.slice(0, 120)}`);
    if (!/data:image\/png;base64/.test(original)) {
      const log = (await b.evaluate(opts, `${area}.get("log")`)).log || [];
      console.log("  (extension log:", JSON.stringify(log.slice(-14)).slice(0, 3000), ")");
    }
    check(/font-style:\s*italic/.test(original), "and the stylesheet");
    check(original.includes("FRAME-TEXT-7731"), "and the frame's text");
    const text = await (await get(`/get/${docId}?max_chars=2000`)).json();
    check((text.text || "").includes("Granular synthesis scatters grains"), "the door indexed the article's text", `${text.text_len} chars`);
  }
  progress = await send([], session + "b");
  const again = await (await get("/inbox?limit=10")).json();
  const copies = (again.recent || []).filter((r) => r.title === "The Bed Article");
  check(copies.length === 1, "sending the same page again does not make a second document", `${copies.length} in the inbox; result ${JSON.stringify(progress?.results?.[0] || {}).slice(0, 120)}`);

  // a PDF open in a tab: fetched with the browser's own session and uploaded
  if (PDF) {
    const pdfTab = await b.evaluate(opts, `(async () => { const t = await ${api}.tabs.create({ url: ${JSON.stringify(`${FIXTURE}/paper.pdf`)}, active: false }); await new Promise(r => setTimeout(r, 2500)); return t.id; })()`);
    await b.evaluate(opts, `${area}.set({ progress: { state: "running", total: 1, done: 0, results: [] } })`);
    await b.evaluate(opts, `${api}.runtime.sendMessage({ type: "capture", tabIds: [${pdfTab}], domains: ["research"], tags: ["bed"], close: false, session: ${JSON.stringify(session + "pdf")} })`);
    for (let i = 0; i < 80; i++) { await sleep(500); progress = (await b.evaluate(opts, `${area}.get("progress")`)).progress; if (progress && progress.state !== "running") break; }
    const r = progress?.results?.[0] || {};
    check(progress?.state === "done" && !r.error && r.mode === "file", "a PDF tab is fetched and uploaded as a file", r.error || `${r.mode} · ${r.note} · doc ${r.doc_id}`);
    if (r.doc_id) {
      const d = await (await get(`/get/${r.doc_id}?max_chars=0`)).json();
      check(d.mime === "application/pdf" && (d.title || "").length > 0, "the door holds it as a PDF", `${d.mime} · ${d.title}`);
    }
  }

  // send all tabs in the window: the readable ones go, an internal page is named as unreadable
  await b.evaluate(opts, `(async () => { const t = await ${api}.tabs.create({ url: ${JSON.stringify(`${FIXTURE}/second`)}, active: false }); await new Promise(r => setTimeout(r, 1500)); return t.id; })()`);
  const all = await b.evaluate(opts, `(async () => (await ${api}.tabs.query({})).map(t => ({ id: t.id, url: t.url })))()`);
  const readable = all.filter((t) => /^https?:/.test(t.url) && !t.url.endsWith("/paper.pdf")).map((t) => t.id);
  check(readable.length >= 2, "the window holds the fixture pages", all.map((t) => t.url).join(" · ").slice(0, 160));
  await b.evaluate(opts, `${area}.set({ progress: { state: "running", total: ${readable.length}, done: 0, results: [] } })`);
  await b.evaluate(opts, `${api}.runtime.sendMessage({ type: "capture", tabIds: ${JSON.stringify(readable)}, domains: ["research"], tags: ["window"], close: false, session: ${JSON.stringify(session + "w")} })`);
  for (let i = 0; i < 120; i++) { await sleep(500); progress = (await b.evaluate(opts, `${area}.get("progress")`)).progress; if (progress && progress.state !== "running") break; }
  const results = progress?.results || [];
  check(progress?.state === "done" && results.length === readable.length && results.every((r) => !r.error), "every readable tab of the window is sent", results.map((r) => r.error || `${r.title || r.url} → doc ${r.doc_id}`).join(" · ").slice(0, 200));
  const after = await (await get("/inbox?limit=10")).json();
  check((after.recent || []).some((r) => r.title === "The Second Page"), "the second page is in the inbox");

  // without the host permission (Firefox grants it only when asked — a
  // release install starts without it): the popup offers the grant, a
  // background tab cannot be read and the result says so, the active
  // tab still goes (activeTab)
  const granted = await b.evaluate(opts, `${api}.permissions.contains({ origins: ["http://*/*", "https://*/*"] })`);
  const removable = granted && await b.evaluate(opts, `${api}.permissions.remove({ origins: ["http://*/*", "https://*/*"] }).then(() => true, () => false)`);
  if (removable) {
    await b.closePage(pop);  // a fresh popup, not the one opened with the permission
    const pop2 = await b.openPage(`${b.origin}/popup.html`);
    await sleep(2000);
    const offer = await b.evaluate(pop2, `(async () => ({ hidden: document.querySelector('#sites')?.hidden, text: document.querySelector('#sites')?.textContent?.trim(), msg: document.querySelector('#msg')?.textContent, contains: await ${api}.permissions.contains({ origins: ["http://*/*", "https://*/*"] }) }))()`);
    check(offer.hidden === false && /grant|allow|sites|permission/i.test(offer.text || ""), "without the host permission the popup offers the grant", JSON.stringify(offer).slice(0, 160));
    await b.evaluate(opts, `${area}.set({ progress: { state: "running", total: ${readable.length}, done: 0, results: [] } })`);
    await b.evaluate(opts, `${api}.runtime.sendMessage({ type: "capture", tabIds: ${JSON.stringify(readable)}, domains: ["research"], tags: ["nogrant"], close: false, session: ${JSON.stringify(session + "ng")} })`);
    for (let i = 0; i < 120; i++) { await sleep(500); progress = (await b.evaluate(opts, `${area}.get("progress")`)).progress; if (progress && progress.state !== "running") break; }
    const rs = progress?.results || [];
    const said = rs.map((r) => r.error || `${r.title || r.url} → ${r.mode || "?"} (${r.note || ""})`).join(" · ");
    check(progress?.state === "done" && rs.length === readable.length, "the send finishes without the permission", said.slice(0, 220));
    check(rs.every((r) => /permission/i.test(r.error || r.note || "")), "each result names the missing permission", said.slice(0, 220));
    check(rs.every((r) => !r.error && r.mode === "url" && r.doc_id), "and the page was sent by URL for the door to fetch", said.slice(0, 220));
  } else {
    console.log("  (host permission not removable here: the grant flow is not exercised)");
  }
}

// ---------------------------------------------------------------- chrome
async function runChrome() {
if (!CHROME) { check(false, "no Chrome found: set CHROME"); return; }
const profile = mkdtempSync(join(tmpdir(), "prax-bed-chrome-"));
const cdpPort = 9300 + Math.floor(Math.random() * 500);
const chrome = spawn(CHROME, [
  "--headless=new", `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${profile}`,
  // branded Chrome dropped --load-extension (137+): the extension is loaded
  // over the protocol instead, which this flag allows
  "--enable-unsafe-extension-debugging", "--remote-allow-origins=*",
  "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--window-size=1200,900",
  "about:blank",
], { stdio: "ignore" });

let wsUrl = null;
for (let i = 0; i < 40 && !wsUrl; i++) {
  await sleep(250);
  try { wsUrl = (await (await fetch(`http://127.0.0.1:${cdpPort}/json/version`)).json()).webSocketDebuggerUrl; } catch (_) { /* not yet */ }
}
check(!!wsUrl, "Chrome is up with the DevTools protocol", `${CHROME.split(/[\\/]/).pop()}`);
if (!wsUrl) { cleanup(); return; }

// a small CDP client: one browser socket, sessions per target
const ws = new WebSocket(wsUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let nextId = 1;
const pending = new Map();
const events = [];
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { const { res, rej } = pending.get(msg.id); pending.delete(msg.id); msg.error ? rej(new Error(msg.error.message)) : res(msg.result); }
  else events.push(msg);
};
function cdp(method, params = {}, sessionId) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params, sessionId }));
  return new Promise((res, rej) => pending.set(id, { res, rej }));
}
async function attach(targetId) {
  const { sessionId } = await cdp("Target.attachToTarget", { targetId, flatten: true });
  await cdp("Runtime.enable", {}, sessionId);
  return sessionId;
}
async function evaluate(sessionId, expression) {
  const r = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
async function openPage(url, ready = "true") {
  // a target of the extension's own pages: wait until the page is the one
  // asked for and loaded, and its chrome.* APIs are there
  const { targetId } = await cdp("Target.createTarget", { url });
  const s = await attach(targetId);
  for (let i = 0; i < 40; i++) {
    const ok = await evaluate(s, `location.href === ${JSON.stringify(url)} && document.readyState === "complete" && (${ready})`).catch(() => false);
    if (ok) return { targetId, s };
    await sleep(250);
  }
  throw new Error(`${url} did not come up`);
}

// the extension, loaded unpacked over the protocol; its service worker
// shows up as a target once it starts
let extId = null;
try { extId = (await cdp("Extensions.loadUnpacked", { path: EXT })).id; } catch (e) { check(false, "Extensions.loadUnpacked", e.message); }
let sw = null;
for (let i = 0; i < 40 && extId && !sw; i++) {
  const { targetInfos } = await cdp("Target.getTargets");
  sw = targetInfos.find((t) => t.type === "service_worker" && t.url.startsWith(`chrome-extension://${extId}/`));
  if (!sw) await sleep(250);
}
check(!!sw, "the extension loaded and its service worker runs", sw ? sw.url : String(extId));
if (!extId) { cleanup(); return; }

// the fixture in a tab
const { targetId: pageTarget } = await cdp("Target.createTarget", { url: `${FIXTURE}/article` });
const page = await attach(pageTarget);
await cdp("Page.enable", {}, page);
await sleep(1500);
const sessions = new Map();  // session -> target, for closing
await exercise({
  apiName: "chrome",
  origin: `chrome-extension://${extId}`,
  pageEval: (expr) => evaluate(page, expr),
  openPage: async (url) => { const { targetId, s } = await openPage(url, "typeof chrome !== 'undefined' && !!chrome.storage"); sessions.set(s, targetId); return s; },
  closePage: async (s) => { const t = sessions.get(s); if (t) await cdp("Target.closeTarget", { targetId: t }); },
  evaluate,
});
if (!KEEP) cleanup();

function cleanup() {
  try { ws.close(); } catch (_) { /* gone */ }
  try { chrome.kill(); } catch (_) { /* gone */ }
  setTimeout(() => rmSync(profile, { recursive: true, force: true }), 1500).unref();
}
}

// --------------------------------------------------------------- firefox
async function runFirefox() {
if (!FIREFOX) { check(false, "no Firefox or Waterfox found: set FIREFOX"); return; }
if (!GECKODRIVER) { check(false, "no geckodriver: set GECKODRIVER, or put it in the tools folder", TOOLS); return; }
const port = 4444 + Math.floor(Math.random() * 500);
const gd = spawn(GECKODRIVER, ["--port", String(port), "--allow-system-access"], { stdio: "ignore" });
await sleep(1500);
const base = `http://127.0.0.1:${port}`;
let sid = null;
async function wd(method, path, body) {
  const r = await fetch(base + path, { method, headers: { "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json();
  if (j.value && j.value.error) throw new Error(`${j.value.error}: ${j.value.message}`);
  return j.value;
}
const cleanup = async () => {
  try { if (sid) await wd("DELETE", `/session/${sid}`); } catch (_) { /* gone */ }
  try { gd.kill(); } catch (_) { /* gone */ }
};
try {
  const s = await wd("POST", "/session", { capabilities: { alwaysMatch: { "moz:firefoxOptions": {
    binary: FIREFOX, args: ["-headless"],
    prefs: { "extensions.webextensions.uuids": JSON.stringify({ "prax-capture@lodsb.org": EXT_UUID }), "xpinstall.signatures.required": false },
  } } } });
  sid = s.sessionId;
  check(!!sid, "Firefox is up through geckodriver", `${s.capabilities.browserName} ${s.capabilities.browserVersion}`);
  const addon = await wd("POST", `/session/${sid}/moz/addon/install`, { path: EXT, temporary: true });
  check(addon === "prax-capture@lodsb.org", "the add-on installed temporarily", String(addon));
  // WebDriver may not navigate to a moz-extension page: the browser opens it
  const openPage = async (url) => {
    await wd("POST", `/session/${sid}/moz/context`, { context: "chrome" });
    await wd("POST", `/session/${sid}/execute/sync`, { script: `
      const win = Services.wm.getMostRecentWindow("navigator:browser");
      win.gBrowser.selectedTab = win.gBrowser.addTab(arguments[0], { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });`, args: [url] });
    await wd("POST", `/session/${sid}/moz/context`, { context: "content" });
    for (let i = 0; i < 40; i++) {
      await sleep(250);
      for (const h of await wd("GET", `/session/${sid}/window/handles`)) {
        await wd("POST", `/session/${sid}/window`, { handle: h });
        const href = await wd("POST", `/session/${sid}/execute/sync`, { script: "return location.href", args: [] }).catch(() => "");
        if (href === url) {
          const ready = await wd("POST", `/session/${sid}/execute/sync`, { script: "return document.readyState === 'complete' && typeof browser === 'object' && !!browser.storage", args: [] }).catch(() => false);
          if (ready) return h;
        }
      }
    }
    throw new Error(`${url} did not come up`);
  };
  // an expression, evaluated in that page (awaited when it is a promise)
  const evaluate = async (handle, expression) => {
    await wd("POST", `/session/${sid}/window`, { handle });
    return wd("POST", `/session/${sid}/execute/async`, { script: `
      const done = arguments[arguments.length - 1];
      Promise.resolve().then(() => eval(arguments[0])).then((v) => done({ ok: true, v })).catch((e) => done({ ok: false, e: String(e && e.message || e) }));`, args: [expression] })
      .then((r) => { if (!r.ok) throw new Error(r.e); return r.v; });
  };
  await wd("POST", `/session/${sid}/url`, { url: `${FIXTURE}/article` });
  const fixtureHandle = await wd("GET", `/session/${sid}/window`);
  await sleep(1500);
  await exercise({
    apiName: "browser",
    origin: `moz-extension://${EXT_UUID}`,
    pageEval: (expr) => evaluate(fixtureHandle, expr),
    openPage,
    closePage: async (h) => { await wd("POST", `/session/${sid}/window`, { handle: h }); await wd("DELETE", `/session/${sid}/window`); },
    evaluate,
  });
} finally {
  if (!KEEP) await cleanup();
}
}

