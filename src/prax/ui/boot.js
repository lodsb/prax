// What starts the page: the change poll, the views map, the router,
// the error reporting and the first render.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// --------------------------------------------------------------- changes
// A small poll: GET /changes returns a stamp that moves when the store
// changed (this door's writes, or another process's commits) and how many
// jobs run. When the stamp moved and a listing is open, it is re-rendered in
// place (not while something is being typed, not while the tab is hidden).

const LIVE_VIEWS = new Set(["inbox", "browse", "doc", "review", "promote", "jobs", "pages"]);
let lastStamp = null;
let uploading = false;  // an upload batch in flight: the inbox view must not be re-rendered under it
// The last upload's summary, shown until dismissed or the next one: in
// sessionStorage, so the view's re-renders and a reload of the tab keep it.
function inboxReport() {
  try { return sessionStorage.getItem("prax.inbox-report") || ""; } catch (_) { return ""; }
}
function keepInboxReport(html) {
  try { html ? sessionStorage.setItem("prax.inbox-report", html) : sessionStorage.removeItem("prax.inbox-report"); } catch (_) { /* no storage */ }
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
async function pollChanges() {
  if (document.visibilityState !== "visible") return;
  let d;
  try {
    const res = await fetch("/changes");
    if (!res.ok) return;
    d = await res.json();
  } catch (_) { return; }
  setJobsBadge(d.jobs);
  const moved = lastStamp !== null && d.stamp !== lastStamp;
  lastStamp = d.stamp;
  if (moved && LIVE_VIEWS.has(route().name) && !typing()) {
    const y = window.scrollY;
    await render({ keepScroll: true });
    window.scrollTo(0, y);
  }
}
setInterval(pollChanges, 10000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") pollChanges(); });
pollChanges();

const views = { search: viewSearch, ask: viewAsk, browse: viewBrowse, review: viewReview, pages: viewPages, promote: viewPromote, inbox: viewInbox, jobs: viewJobs };

async function render(opts) {
  const r = route();
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === r.name));
  view.classList.remove("stage", "wide");  // the ask view widens the page, the document and graph views take all of it; others get the default
  if (!(opts && opts.keepScroll)) window.scrollTo(0, 0);
  const key = `${r.name}/${r.arg || ""}`;
  quiet = !!(opts && opts.keepScroll) && view.dataset.route === key;  // the same page, refreshed in place
  view.dataset.route = key;
  try {
    if (r.name === "doc") return await viewDoc(r.arg, r.params);
    if (r.name === "graph") return await viewGraph(r.arg, r.params);
    return await (views[r.name] || viewSearch)(r.params);
  } finally { quiet = false; }
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
