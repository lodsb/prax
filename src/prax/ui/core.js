// The page itself: settings, the helpers every view uses, the
// token and the session, the status line.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

/* prax web UI: a client of the HTTP door. One page, hash routes, one render
   function per view. No framework; marked.js renders Markdown. */
"use strict";

// The element a view draws into. A live refresh points it at a hidden
// twin for the length of one draw (boot.js, refreshInPlace), so it is let.
let view = document.getElementById("view");
// A view shows "Loading…" while its first requests are out — but not on a
// live refresh (the poll below re-rendering the same route in place), where
// the page it already shows stays until the new one is ready.
let quiet = false;
function loading(text) {
  if (!quiet) view.innerHTML = `<p class="muted">${text || "Loading…"}</p>`;
}
// The list part of a view that draws its form first and fills the list
// after: on a live refresh the list it already shows, otherwise "Loading…".
function listPlaceholder(id) {
  const el = quiet && document.getElementById(id);
  return el ? el.innerHTML : `<p class="muted">Loading…</p>`;
}
const statusEl = document.getElementById("status");

// ---------------------------------------------------------------- settings
// What this browser prefers: the theme, a few defaults. localStorage, this
// browser only, never sent to the door (the session cookie is the only
// thing the door sees). The theme is also applied by an inline script in
// index.html before the first paint, so a reload does not flash.
const SETTINGS_KEY = "prax.settings";
const SETTINGS_DEFAULTS = { theme: "system", ask_limit: 8, search_limit: 20, faces: true };
const THEMES = ["bindery", "dessau", "riso", "cyanotype", "night", "funk"];  // docs/design/BRIEF.md
const OLD_THEMES = { light: "bindery", dark: "night", paper: "bindery" };

function settings() {
  let s;
  try { s = { ...SETTINGS_DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") }; }
  catch (_) { s = { ...SETTINGS_DEFAULTS }; }
  s.theme = OLD_THEMES[s.theme] || s.theme;
  return s;
}
function saveSettings(s) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch (_) { /* private mode */ }
  applyTheme(s.theme);
}
function applyTheme(theme) {
  // the four values switch the page and the inlined mark together
  if (!THEMES.includes(theme)) {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "night" : "bindery";
  }
  document.documentElement.dataset.praxTheme = theme;
  // the canvas reads its colours when it draws; a settled graph must be told
  const live = ForceGraph.live;
  if (live && document.contains(live.canvas)) live.draw();
}
function openSettings() {
  const dlg = document.getElementById("settings");
  const form = document.getElementById("settings-form");
  const s = settings();
  form.theme.value = s.theme;
  form.ask_limit.value = s.ask_limit;
  form.search_limit.value = s.search_limit;
  form.theme.onchange = () => applyTheme(form.theme.value);  // a live preview
  dlg.onclose = () => {
    const next = {
      theme: form.theme.value,
      ask_limit: Math.max(1, Math.min(20, Number(form.ask_limit.value) || 8)),
      search_limit: Math.max(5, Math.min(100, Number(form.search_limit.value) || 20)),
    };
    saveSettings(next);
  };
  dlg.showModal();
}
document.getElementById("settings-btn").addEventListener("click", openSettings);
// (the theme itself is applied by the inline script in index.html, before paint)

// ------------------------------------------------------------- utilities


async function api(path, params) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  }
  setStatus("…");
  const res = await fetch(url);
  setStatus("");
  if (res.status === 401) {
    askForToken();
    throw new Error("access token required");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* keep */ }
    throw new Error(msg);
  }
  return res.json();
}

// A write to the door: POST, PUT or DELETE, with a JSON body when there is
// one. A refused token asks for one; a failure throws the door's detail,
// so a caller that shows its status never carries on as if it had worked.
async function send(method, path, body) {
  setStatus("…");
  const init = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  setStatus("");
  if (res.status === 401) { askForToken(); throw new Error("access token required"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

// A view's area when its request failed: the door's detail, in place of
// what would have been shown.
function showError(el, err) {
  el.innerHTML = `<p class="error">${esc(err && err.message ? err.message : String(err))}</p>`;
}

// ------------------------------------------------------------------ auth
// The door wants a bearer token: the administrator's (PRAX_TOKEN) or a
// named one (prax token add). The UI exchanges it once for
// an HttpOnly session cookie via POST /session, so plain links (originals,
// raw text) work in new tabs too. The token is never kept in the browser
// (localStorage holds the settings, sessionStorage the ask conversation).

function askForToken() {
  if (document.getElementById("token-form")) return;
  const box = document.createElement("div");
  box.className = "token-box plate";
  box.innerHTML = `
    <form id="token-form" class="search-form" autocomplete="off">
      <label for="token-input">Access token</label>
      <input id="token-input" name="token" type="password" placeholder="your access token" autofocus>
      <button>Unlock</button>
      <span id="token-msg" class="error"></span>
    </form>`;
  view.prepend(box);
  document.getElementById("token-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const token = document.getElementById("token-input").value.trim();
    const res = await fetch("/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (res.ok) {
      box.remove();
      await whoAmI();
      render();
    } else {
      document.getElementById("token-msg").textContent = "not accepted";
    }
  });
}

// Who the page is signed in as (GET /session). A named token sees the
// views its routes reach (auth.RESTRICTED_ROUTES), and the page shows
// only those: search, ask, browse, graph walks, pages, a document.
const GUEST_HIDDEN = ["people", "review", "promote", "inbox", "jobs", "admin"];
async function whoAmI() {
  let who = null;
  try {
    const res = await fetch("/session");
    if (res.ok) who = await res.json();
  } catch (_) { /* the door is away: the first view says so */ }
  const guest = !!who && who.admin === false;
  document.body.classList.toggle("guest", guest);
  // a reader's token changes nothing: the page leaves out its edits
  document.body.classList.toggle("reader", guest && who.role !== "contributor");
  document.querySelectorAll("nav a").forEach((a) => { a.hidden = guest && GUEST_HIDDEN.includes(a.dataset.view); });
  return who;
}
function isGuest() { return document.body.classList.contains("guest"); }

function setStatus(text) { statusEl.textContent = text; }


function route() { return parseHash(location.hash); }
function go(name, arg, params) {
  const q = new URLSearchParams(params || {}).toString();
  location.hash = name + (arg ? "/" + arg : "") + (q ? "?" + q : "");
}

// Markdown to HTML, then through DOMPurify (vendored): a document's text
// is somebody else's writing, and Markdown carries raw HTML through. The
// page's policy already refuses inline script; the sanitizer removes it,
// and with it forms, frames, styles and every attribute that is not
// plain content, so a captured page cannot restyle or clobber the UI.
// KaTeX's output is added after, by typesetMaths, and is the UI's own.
const MD_CLEAN = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: ["style", "form", "input", "button", "select", "textarea", "iframe", "object", "embed", "base", "meta", "link", "svg", "math"],
  FORBID_ATTR: ["style", "class", "id", "name", "target"],
  ALLOW_DATA_ATTR: false,
};
function md(text) {
  // U+FFFD is a glyph the PDF's font gave no name: a box, not a question
  const raw = marked.parse(text || "", { gfm: true, breaks: false });
  const html = typeof DOMPurify === "undefined" ? esc(raw) : DOMPurify.sanitize(raw, MD_CLEAN);
  return html.replace(/\uFFFD/g, '<span class="lost" title="a glyph the document\u2019s font did not name">\u25AB</span>');
}

// A rule that ends in a mark: a hairline, the lozenge, a hairline. Says a
// section ended; does nothing.
function rule() {
  return `<div class="rule" aria-hidden="true"><svg viewBox="-7 -7 14 14"><path d="M0 -5.4 C 0.8 -1.8 1.9 -0.7 5.3 0.2 C 1.8 0.9 0.7 2 -0.2 5.4 C -0.9 1.9 -2 0.8 -5.4 -0.2 C -1.9 -0.9 -0.8 -2 0 -5.4 Z" fill="var(--prax-colour)"/></svg></div>`;
}

function badge(kind) {
  return `<span class="badge badge-${esc(kind || "text")}">${esc(kind || "text")}</span>`;
}


function originalHref(docId, page) {
  return `/doc/${docId}/original` + (page ? `#page=${page}` : "");
}

// A moment in a recording: 754 → "12:34", 3754 → "1:02:34".
function fmtTime(t) {
  t = Math.max(0, Math.floor(Number(t) || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

// The link to a moment of a video document: seeks the player on its page
// when one is there, else opens the recording at that second.
function momentLink(docId, t, video) {
  const url = video && video.url ? `${video.url}${video.url.includes("?") ? "&" : "?"}t=${t}s` : null;
  return `<a class="moment" href="${url ? esc(url) : `#doc/${docId}`}" data-t="${t}" data-doc="${docId}" title="at ${fmtTime(t)}"${url ? ` target="_blank" rel="noopener"` : ""}>${fmtTime(t)}</a>`;
}

// A gallery over a set of pictures, a page at a time (lib.js galleryHtml):
// load(offset, limit) answers {total, offset, limit, items}. ``param``
// names the page in the address (#person/12?photos=3), kept without a
// new history entry, so a reload or a refresh draws the same page.
// ``follow(item, e)`` may take over the viewer's link (a figure's place in
// the text). Resolves once the first page is drawn: a live refresh
// compares the HTML with the gallery in it.
async function mountGallery(box, opts) {
  const limit = opts.limit || 20;
  const params = route().params;
  let offset = opts.param && /^\d+$/.test(params[opts.param] || "") ? (Number(params[opts.param]) - 1) * limit : 0;
  let page = null;
  const here = (o) => {
    const r = route();
    const q = { ...r.params };
    if (o) q[opts.param] = String(o / limit + 1); else delete q[opts.param];
    const s = new URLSearchParams(q).toString();
    return `#${r.name}${r.arg ? "/" + r.arg : ""}${s ? "?" + s : ""}`;
  };
  const draw = async (o) => {
    page = await opts.load(o, limit);
    if (!page.items.length && page.total && o > 0) page = await opts.load(Math.floor((page.total - 1) / limit) * limit, limit);  // past the end: the last page
    offset = page.offset;
    box.innerHTML = page.total ? galleryHtml(page, opts.param ? here : () => "#", opts.figures) : (opts.empty || "");
    if (opts.param) history.replaceState(null, "", here(offset));
  };
  const item = async (i) => {
    if (i < page.offset || i >= page.offset + page.items.length) await draw(Math.floor(i / limit) * limit);
    return page.items[i - page.offset];
  };
  let shade = null, at = 0;
  const close = () => { if (shade) { shade.remove(); shade = null; document.removeEventListener("keydown", keys); } };
  const show = async (i) => {
    const it = await item(i);
    if (!it) return;
    at = i;
    if (!shade) {
      shade = document.createElement("div");
      shade.className = "viewer";
      shade.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-viewer]");
        if (!b) { if (e.target === shade) close(); return; }
        const act = b.dataset.viewer;
        if (act === "close") close();
        else if (act === "prev" || act === "next") step(act === "prev" ? -1 : 1);
        else if (act === "open") {
          const cur = page.items[at - page.offset];
          close();
          if (opts.follow && cur) { e.preventDefault(); opts.follow(cur); }
        }
      });
      document.body.append(shade);
      document.addEventListener("keydown", keys);
    }
    shade.innerHTML = viewerHtml(it, i, page.total);
    const img = shade.querySelector(".viewer-img");
    // a photo the photos step gave no preview: its thumbnail, larger
    img.addEventListener("error", () => { if (!it.ref && !img.dataset.fell) { img.dataset.fell = "1"; img.src = pictureSrc(it, false); } }, { once: true });
    shade.querySelector("[data-viewer=close]").focus();
  };
  const step = (d) => { const n = viewerStep(at, d, page.total); if (n !== null) show(n); };
  function keys(e) {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowLeft") step(-1);
    else if (e.key === "ArrowRight") step(1);
    else return;
    e.preventDefault();
  }
  box.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-offset]");
    if (a) { e.preventDefault(); draw(Number(a.dataset.offset)).then(() => box.scrollIntoView({ block: "nearest" })); return; }
    const t = e.target.closest("a.gallery-tile");
    if (!t || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;  // a new tab keeps the link
    e.preventDefault();
    e.stopPropagation();  // not the document page's data-scroll handler
    show(Number(t.dataset.i));
  });
  await draw(offset);
}

// A picture set of the door's (GET /pictures) as a gallery's load.
function pictureLoad(key, id) {
  return (offset, limit) => api("/pictures", { [key]: id, offset, limit });
}

// A list already in hand as a gallery's load (a document's figures).
function listLoad(items) {
  return async (offset, limit) => ({ total: items.length, offset, limit, items: items.slice(offset, offset + limit) });
}
