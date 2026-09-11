/* prax web UI: the pure helpers, shared by the page (loaded before app.js)
   and by the node tests (tests/ui/lib.test.js). No DOM, no fetch, no
   globals read: everything here is a function of its arguments. */
"use strict";

// HTML escaping for everything interpolated into markup.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

// The hash router: "#doc/12?chunk=3" -> {name, arg, params}. A third path
// segment on a document route is a chunk id ("#doc/12/3").
function parseHash(hash) {
  const h = String(hash || "").replace(/^#/, "") || "search";
  const [pathPart, query] = h.split("?");
  const parts = pathPart.split("/");
  const params = Object.fromEntries(new URLSearchParams(query || ""));
  if (parts[0] === "doc" && parts.length === 3 && /^\d+$/.test(parts[2])) params.chunk = parts[2];
  return { name: parts[0], arg: parts[1] || "", params };
}

function headingPath(h) {
  return (h || []).map(esc).join(" › ");
}

// An edge carries its evidence as a quote, never a chunk id (chunks are a
// disposable index). A link with ?find=<quote> lands on the chunk that
// contains the quote, or the chunk sharing most of its words.
const norm = (t) => String(t || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

function locateChunk(chunks, quote) {
  const q = norm(quote);
  if (!q) return null;
  const probe = q.slice(0, 80);
  const hit = (chunks || []).find((c) => norm(c.text).includes(probe));
  if (hit) return hit.chunk_id;
  const words = new Set(q.split(" ").filter((w) => w.length > 3));
  let best = null, bestN = 0;
  for (const c of chunks || []) {
    const cw = new Set(norm(c.text).split(" "));
    let n = 0;
    for (const w of words) if (cw.has(w)) n++;
    if (n > bestN) { bestN = n; best = c.chunk_id; }
  }
  return bestN >= Math.max(2, words.size * 0.4) ? best : null;
}

// [n] citations in an answer become links to the passage's chunk.
function citeLinks(html, passages) {
  const byN = Object.fromEntries((passages || []).map((p) => [p.n, p]));
  return String(html).replace(/\[(\d+)\]/g, (m, n) => {
    const p = byN[n];
    if (!p) return m;
    return `<a class="cite" href="#doc/${p.doc_id}${p.chunk_id ? `?chunk=${p.chunk_id}` : ""}" title="${esc(p.title)}">[${n}]</a>`;
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { esc, parseHash, headingPath, norm, locateChunk, citeLinks };
}
