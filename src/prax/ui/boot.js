// What starts the page: the change poll, the views map, the router,
// the error reporting and the first render.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// --------------------------------------------------------------- changes
// A small poll: GET /changes returns a stamp that moves when the store
// changed (this door's writes, or another process's commits) and how many
// jobs run. When the stamp moved and a listing is open, it is drawn again
// off the page (refreshInPlace): when it comes out as it was drawn last,
// which is most of the time (the worker writes every few seconds, rarely
// about the page in front of you), nothing on the page is touched; when it
// changed, the new content is moved in with what was open kept open and
// what was scrolled kept scrolled. Not while something is being typed, not
// while the tab is hidden.

const LIVE_VIEWS = new Set(["inbox", "browse", "doc", "review", "promote", "jobs", "pages"]);
let lastStamp = null;
let uploading = false;  // an upload batch in flight: the inbox view must not be re-rendered under it
// The last upload's summary, shown until dismissed or the next one: in
// sessionStorage, so the view's re-renders and a reload of the tab keep it.
function inboxReport() {
  try { return sessionStorage.getItem("prax.capture.inbox-report") || ""; } catch (_) { return ""; }
}
function keepInboxReport(html) {
  try { html ? sessionStorage.setItem("prax.capture.inbox-report", html) : sessionStorage.removeItem("prax.capture.inbox-report"); } catch (_) { /* no storage */ }
}
function typing() {
  const el = document.activeElement;
  return uploading || !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT") || !!route().params.edit;
}
function setJobsBadge(n) {
  const b = document.getElementById("jobs-badge");
  if (!b) return;
  b.hidden = !n;
  b.textContent = n || "";
}
// While the door is busy with a maintenance pass, the header says which
// and how far ("maintenance: rechunk 3,400 of 9,962"), on every view: a
// page is slower then, and the reason should be on it.
function setMaintenance(m) {
  const b = document.getElementById("maintenance");
  if (!b) return;
  b.hidden = !m;
  if (!m) return;
  const far = m.total ? ` ${Number(m.done || 0).toLocaleString()} of ${Number(m.total).toLocaleString()}` : "";
  b.textContent = `maintenance: ${m.name}${far}`;
  b.title = m.note || "";
}
async function pollChanges() {
  if (document.visibilityState !== "visible") return;
  let d;
  try {
    const res = await fetch("/changes");
    if (!res.ok) return;
    d = await res.json();
  } catch (_) { return; }
  setJobsBadge(d.jobs);
  setMaintenance(d.maintenance);
  const moved = lastStamp !== null && d.stamp !== lastStamp;
  lastStamp = d.stamp;
  if (moved && LIVE_VIEWS.has(route().name) && !typing()) {
    const y = window.scrollY;
    if (await refreshInPlace()) window.scrollTo(0, y);
  }
}
setInterval(pollChanges, 10000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") pollChanges(); });
pollChanges();

const views = { search: viewSearch, ask: viewAsk, browse: viewBrowse, review: viewReview, pages: viewPages, promote: viewPromote, inbox: viewInbox, jobs: viewJobs, admin: viewAdmin };

// The HTML the view last drew: a refresh that draws the same is no change.
let drawn = "";

async function drawRoute(r) {
  if (r.name === "page" && r.arg) {
    // a page by its slug (#page/onset-notes): it is a document; open that
    const page = await api(`/page/${encodeURIComponent(decodeURIComponent(r.arg))}`);
    location.replace(`#doc/${page.doc_id}`);
    return;
  }
  if (r.name === "doc") return await viewDoc(r.arg, r.params);
  if (r.name === "graph") return await viewGraph(r.arg, r.params);
  return await (views[r.name] || viewSearch)(r.params);
}

async function render(opts) {
  const r = route();
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === r.name));
  view.classList.remove("stage", "wide");  // the ask view widens the page, the document and graph views take all of it; others get the default
  if (!(opts && opts.keepScroll)) window.scrollTo(0, 0);
  const key = `${r.name}/${r.arg || ""}`;
  quiet = !!(opts && opts.keepScroll) && view.dataset.route === key;  // the same page, refreshed in place
  view.dataset.route = key;
  try { return await drawRoute(r); } finally { quiet = false; drawn = view.innerHTML; }
}

// A fold is known by its summary with its numbers left out, and by which
// of the folds of that summary it is ("347 to decide" may be 346 now).
function foldKey(d) {
  const s = d.querySelector(":scope > summary");
  return (s ? s.textContent : "").replace(/\d[\d,.]*/g, "#").trim().slice(0, 80);
}
function keepState(root) {
  return {
    open: [...root.querySelectorAll("details")].map((d) => [foldKey(d), d.open]),
    scrolls: [...root.querySelectorAll("[id]")].filter((e) => e.scrollTop > 0).map((e) => [e.id, e.scrollTop]),
    focus: root.contains(document.activeElement) && document.activeElement.id ? document.activeElement.id : null,
  };
}
function restoreState(root, s) {
  const want = new Map();
  for (const [k, o] of s.open) { if (!want.has(k)) want.set(k, []); want.get(k).push(o); }
  const seen = new Map();
  for (const d of root.querySelectorAll("details")) {
    const k = foldKey(d);
    const i = seen.get(k) || 0;
    seen.set(k, i + 1);
    const flags = want.get(k);
    if (flags && i < flags.length) d.open = flags[i];
  }
  for (const [id, top] of s.scrolls) { const e = document.getElementById(id); if (e) e.scrollTop = top; }
  if (s.focus) { const e = document.getElementById(s.focus); if (e) e.focus(); }
}

// Draw the current route again into a hidden twin of the view, placed
// before it so that a view's getElementById finds the twin's elements.
// The same HTML as last drawn: the twin goes and the page stays as it is.
// Different: its children move into the view (handlers bound to them come
// along; those bound to the view itself stay) and the folds, the scrolled
// panels and the focus are put back. True when the page changed.
async function refreshInPlace() {
  const r = route();
  const shown = view;
  if (shown.dataset.route !== `${r.name}/${r.arg || ""}`) return false;
  const next = document.createElement("div");
  next.hidden = true;
  next.className = shown.className.replace(/\b(stage|wide)\b/g, "").trim();
  next.dataset.route = shown.dataset.route;
  shown.before(next);
  view = next;
  try {
    await drawRoute(r);
  } catch (_) {
    next.remove();
    return false;  // a refresh that fails leaves the page as it was
  } finally { view = shown; }
  const html = next.innerHTML;
  if (html === drawn || route().name !== r.name) { next.remove(); return false; }
  const state = keepState(shown);
  shown.className = next.className;
  shown.replaceChildren(...next.childNodes);
  next.remove();
  drawn = html;
  restoreState(shown, state);
  return true;
}

document.getElementById("quick").addEventListener("submit", (e) => {
  e.preventDefault();
  go("search", "", { q: document.getElementById("quick-q").value });
});
// A client error is otherwise invisible: show it in the status area and
// tell the door, which logs it (POST /ui/error) so it can be read later.
function reportError(kind, err) {
  const message = (err && err.message) || String(err);
  setStatus("error: " + message);
  const body = { kind, message, stack: err && err.stack ? String(err.stack).slice(0, 4000) : null, hash: location.hash, agent: navigator.userAgent };
  fetch("/ui/error", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
}
window.addEventListener("error", (e) => reportError("error", e.error || e.message));
window.addEventListener("unhandledrejection", (e) => reportError("rejection", e.reason));

// a formula's LaTeX, shown and hidden beside its typeset form
document.addEventListener("click", (e) => {
  const b = e.target.closest(".formula-source");
  if (!b) return;
  const pre = b.parentElement.querySelector(".formula-latex");
  if (!pre) return;
  pre.hidden = !pre.hidden;
  b.textContent = pre.hidden ? "LaTeX" : "hide";
});

window.addEventListener("hashchange", render);
render();
