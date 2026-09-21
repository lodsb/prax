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

// The maths in a run of text: $$…$$ and \[…\] display spans, $…$ and \(…\)
// inline ones, as a parser (marker) or a model writes them. A dollar sign is
// also a currency sign, so an inline span must not start with a digit or
// a space, must hold a letter or a backslash, must stay on its line and
// under 200 characters, and its closing $ must not be followed by a digit.
// Returns [{start, end, latex, display}] in text order.
const MATH = /\$\$([^$]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([^\n]+?)\\\)|\$([^$\n]{1,200}?)\$(?!\d)/g;
function mathSpans(text) {
  const out = [];
  const s = String(text || "");
  let m;
  MATH.lastIndex = 0;
  while ((m = MATH.exec(s)) !== null) {
    const display = m[1] !== undefined || m[2] !== undefined;
    const latex = (m[1] ?? m[2] ?? m[3] ?? m[4]).trim();
    if (!latex) continue;
    if (m[4] !== undefined) {
      const inner = m[4];
      if (/^[\s\d]/.test(inner) || /\s$/.test(inner) || !/[A-Za-z\\]/.test(inner)) continue;
    }
    out.push({ start: m.index, end: m.index + m[0].length, latex, display });
  }
  return out;
}

// The figures of a document for the strip: every figure chunk with a
// picture, its caption (the reference's own, else the first reading's
// first words), where it is (page, or the moment of a video frame).
function figureItems(chunks) {
  const out = [];
  for (const c of chunks || []) {
    if (c.kind !== "figure" || !c.data || !c.data.ref) continue;
    let caption = String(c.data.caption || "").trim();
    if (!caption) {
      const r = (c.data.readings || []).find((x) => x && x.text);
      if (r) caption = String(r.text).trim().split(/(?<=[.!?])\s/)[0].slice(0, 120);
    }
    out.push({ chunk_id: c.chunk_id, ref: c.data.ref, caption, page: c.page || null, time: c.time != null ? c.time : null });
  }
  return out;
}

// A document's reference chunks as a table of what each numbered entry
// cites: number -> {href, title, chunk} — the cited document's page when
// the references pass matched it, else the entry's own chunk. Entries
// without a number are not in-text markers and are left out.
function referenceLinks(chunks) {
  const out = {};
  for (const c of chunks || []) {
    if (c.kind !== "reference" || !c.data || c.data.number == null) continue;
    const cited = c.data.cited;
    out[String(c.data.number)] = cited && cited.doc_id
      ? { href: `#doc/${cited.doc_id}`, title: cited.title || "", chunk: c.chunk_id, how: cited.how || "" }
      : { href: `#chunk-${c.chunk_id}`, title: c.data.title || "", chunk: c.chunk_id, how: "" };
  }
  return out;
}

// The in-text citation markers of a numbered reference list — "[12]",
// "[3, 5]", "[3–5]" — linked through the table above; a marker the list
// has no entry for is left as it is (an equation, a footnote), and so is
// everything that is not a bracketed number.
function citeMarkers(html, links) {
  if (!links || !Object.keys(links).length) return html;
  return String(html || "").replace(/\[(\d{1,3}(?:\s*[,\u2013\u2014-]\s*\d{1,3})*)\]/g, (whole, inner) => {
    let any = false;
    const out = inner.split(/(\s*[,\u2013\u2014-]\s*)/).map((p) => {
      const l = /^\d{1,3}$/.test(p) ? links[p] : null;
      if (!l) return p;
      any = true;
      const scroll = l.href.startsWith("#chunk-") ? ` data-scroll="${l.chunk}"` : "";
      return `<a class="cite" href="${l.href}"${scroll} title="${esc(l.title)}">${p}</a>`;
    }).join("");
    return any ? `[${out}]` : whole;
  });
}

// An ask chunk's text — the block from its head marker to its tail — as
// what the frame shows: the interior alone, the markers gone (the keep
// markers too; what they held stays), and the source list's [n] markers
// left as they are. The question and the state come from the chunk's
// data, not from here.
function askInterior(text) {
  return String(text || "")
    .replace(/^[ \t]*<!--\s*\/?prax:ask\b[^>]*-->[ \t]*$/gm, "")
    .replace(/<!--\s*\/?prax:keep\s*-->/g, "")
    .trim();
}

// The markers a person writes to put a standing question in a page: an
// id the page does not use yet, the question quoted, the options given.
function askBlockMarkers(id, question, options) {
  const opts = Object.entries(options || {}).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${k}=${v}`).join(" ");
  const q = String(question || "").replace(/"/g, "'").replace(/\s+/g, " ").trim();
  return `<!-- prax:ask id=${id}${opts ? " " + opts : ""} "${q}" -->\n<!-- /prax:ask id=${id} -->\n`;
}

// The next free block id in a page's text: q1, q2, … past the ones there.
function nextAskId(text) {
  const used = new Set();
  for (const m of String(text || "").matchAll(/<!--\s*prax:ask\s+[^>]*\bid=(\S+)/g)) used.add(m[1].replace(/["']/g, ""));
  let n = 1;
  while (used.has(`q${n}`)) n += 1;
  return `q${n}`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { esc, parseHash, headingPath, norm, locateChunk, citeLinks, mathSpans, figureItems, referenceLinks, citeMarkers, askInterior, askBlockMarkers, nextAskId };
}
