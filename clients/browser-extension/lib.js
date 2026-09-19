/* prax capture: the pure helpers, shared by the popup, the options page and
   the background, and tested with node (tests/ui/extension.test.js). No
   browser API in here. */

(function (root, factory) {
  const lib = factory();
  if (typeof module === "object" && module.exports) module.exports = lib;
  root.praxLib = lib;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const MAX_HTML_BYTES = 32 * 1024 * 1024;
  const UNCAPTURABLE = /^(about|chrome|chrome-extension|moz-extension|edge|file|view-source|data|blob|javascript):/i;

  /** A capture-session id: when, plus a short random suffix. */
  function sessionId(now, random) {
    const d = now || new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const stamp = `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`;
    const r = random !== undefined ? random : Math.random();
    return `${stamp}-${Math.floor(r * 0xffff).toString(16).padStart(4, "0")}`;
  }

  /** Whether the browser lets a page be read: no internal, file or extension pages. */
  function capturable(url) {
    return typeof url === "string" && /^https?:/i.test(url) && !UNCAPTURABLE.test(url);
  }

  /** A tab showing a PDF: the URL says so, or the DOM is a PDF viewer's. */
  function looksLikePdf(url, html) {
    if (typeof url === "string" && /\.pdf(?:$|[?#])/i.test(url)) return true;
    if (typeof html !== "string") return false;
    const head = html.slice(0, 20000);
    return /id="viewer" class="pdfViewer"/.test(head) || /<embed[^>]+type="application\/pdf"/i.test(head) || /<html[^>]*>\s*<head>\s*<title>[^<]*\.pdf<\/title>/i.test(head);
  }

  /** How to send a tab: as its rendered DOM, or as a URL the door fetches. */
  function plan(url, html) {
    if (!capturable(url)) return { mode: "skip", reason: "this kind of page cannot be read" };
    if (looksLikePdf(url, html)) return { mode: "url", reason: "a PDF: the door fetches it" };
    if (typeof html !== "string" || !html) return { mode: "url", reason: "the page did not answer: the door fetches it" };
    if (byteLength(html) > MAX_HTML_BYTES) return { mode: "url", reason: "page above 32 MB: the door fetches it" };
    return { mode: "html" };
  }

  function byteLength(s) {
    if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(s).length;
    return Buffer.byteLength(s, "utf8");
  }

  /** The server URL as an origin without a trailing slash, or null. */
  function normalizeServer(value) {
    const s = String(value || "").trim().replace(/\/+$/, "");
    if (!/^https?:\/\/[^\s/]+$/i.test(s)) return null;
    return s;
  }

  /** The match pattern a host permission request wants for a server. */
  function originPattern(server) {
    const s = normalizeServer(server);
    return s ? `${s}/*` : null;
  }

  /** Whether a fetched response is a PDF: the bytes say so, or the type does
      and the bytes do not say otherwise (a login page comes back as HTML). */
  function isPdfResponse(contentType, firstBytes) {
    const head = firstBytes ? Array.from(firstBytes.slice(0, 5)).map((b) => String.fromCharCode(b)).join("") : "";
    if (head.startsWith("%PDF")) return true;
    if (head) return false;
    return /^application\/pdf\b/i.test(String(contentType || ""));
  }

  /** A file name for a fetched PDF, from the URL's last path segment. */
  function pdfFileName(url) {
    let name = "";
    try { name = decodeURIComponent(new URL(url).pathname.split("/").filter(Boolean).pop() || ""); } catch (_) { /* not a URL */ }
    name = name.replace(/[\\/:*?"<>|]+/g, "_").trim();
    if (!name) name = "document.pdf";
    if (!/\.pdf$/i.test(name)) name += ".pdf";
    return name;
  }

  /** A one-line result for the popup. */
  function describeResult(res) {
    if (res.error) return `failed: ${res.error}`;
    if (res.mode === "video" && res.note) return `${res.created ? "new" : "already in the store"}: ${res.note}`;
    if (res.downloaded) return "downloaded for the watcher";
    if (res.manual) return "waiting for you";
    const bits = [res.created ? "new" : "already in the store"];
    bits.push(res.indexed ? "searchable" : "waiting for the parse queue");
    if (res.previous_capture) bits.push(`follows doc ${res.previous_capture}`);
    return bits.join(", ");
  }

  function splitList(text) {
    return String(text || "").split(",").map((s) => s.trim()).filter(Boolean);
  }

  // ------------------------------------------------------------- video
  // A watch page becomes one document: the transcript as timed paragraphs,
  // a frame every so often, in the HTML shape the door's video parser
  // reads exactly (src/prax/parsers/video.py). Everything here is pure;
  // the background does the fetching, seeking and drawing.

  /** The video a URL shows: {provider, id} for a YouTube watch page, else null. */
  function videoOfUrl(url) {
    let u;
    try { u = new URL(url); } catch (_) { return null; }
    const host = u.hostname.replace(/^www\.|^m\./, "");
    if (host === "youtu.be") {
      const id = u.pathname.split("/").filter(Boolean)[0];
      return id ? { provider: "youtube", id } : null;
    }
    if (host === "youtube.com" || host === "youtube-nocookie.com") {
      const id = u.searchParams.get("v") || (u.pathname.match(/^\/(?:embed|shorts|live)\/([\w-]{6,})/) || [])[1];
      return id ? { provider: "youtube", id } : null;
    }
    return null;
  }

  /** 754 → "12:34", 3754 → "1:02:34". */
  function fmtTime(t) {
    t = Math.max(0, Math.floor(Number(t) || 0));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    const mm = h ? String(m).padStart(2, "0") : String(m);
    return `${h ? h + ":" : ""}${mm}:${String(s).padStart(2, "0")}`;
  }

  /** The caption track to read: a person's in the reader's language, then a
      person's in any, then the automatic one; null without any. */
  function chooseTrack(tracks, language) {
    const list = (tracks || []).filter((t) => t && t.baseUrl);
    if (!list.length) return null;
    const lang = String(language || "").toLowerCase().split("-")[0];
    const human = list.filter((t) => t.kind !== "asr");
    const inLang = (arr) => arr.find((t) => String(t.languageCode || "").toLowerCase().split("-")[0] === lang);
    return inLang(human) || human[0] || inLang(list) || list[0];
  }

  /** YouTube's json3 caption events → paragraphs [{t, text}]: segments run
      together until a pause, a sentence end past some length, or a length
      cap; the automatic track's line-by-line repeats are folded. */
  function groupCaptions(events, options) {
    const opt = { pause: 1.5, target: 280, max: 420, ...(options || {}) };
    const segs = [];
    for (const e of events || []) {
      if (!e || !Array.isArray(e.segs)) continue;
      const text = e.segs.map((s) => (s && s.utf8) || "").join("").replace(/\s+/g, " ").trim();
      if (!text || text === "\n") continue;
      const t = (Number(e.tStartMs) || 0) / 1000;
      const end = t + (Number(e.dDurationMs) || 0) / 1000;
      segs.push({ t, end, text });
    }
    const out = [];
    let cur = null;
    for (const s of segs) {
      if (cur && cur.text.endsWith(s.text)) continue; // a rolling repeat
      const gap = cur ? s.t - cur.end : 0;
      const len = cur ? cur.text.length : 0;
      const sentenceEnd = cur ? /[.!?…]["')\]]?$/.test(cur.text) : false;
      if (!cur || gap > opt.pause || (sentenceEnd && len >= opt.target) || len + s.text.length > opt.max) {
        cur = { t: s.t, end: s.end, text: s.text };
        out.push(cur);
      } else {
        cur.text += " " + s.text;
        cur.end = Math.max(cur.end, s.end);
      }
    }
    return out.map((p) => ({ t: Math.floor(p.t), text: p.text }));
  }

  /** Chapters as a description lists them ("0:00 Intro" lines): [{t, title}],
      empty unless there are at least three and the first is 0:00. */
  function chaptersFrom(description) {
    const found = [];
    for (const line of String(description || "").split(/\r?\n/)) {
      const m = line.match(/^\s*[-•*]?\s*\(?((?:\d{1,2}:)?\d{1,2}:\d{2})\)?\s*[-–—:]?\s*(.+?)\s*$/);
      if (!m) continue;
      const parts = m[1].split(":").map(Number);
      const t = parts.reduce((acc, p) => acc * 60 + p, 0);
      found.push({ t, title: m[2] });
    }
    if (found.length < 3 || found[0].t !== 0) return [];
    for (let i = 1; i < found.length; i++) if (found[i].t <= found[i - 1].t) return [];
    return found;
  }

  /** When to take a frame: every `interval` seconds from 0, no more than
      `cap` of them (the interval stretches for a long recording). */
  function frameTimes(duration, interval, cap) {
    const d = Math.max(0, Math.floor(Number(duration) || 0));
    if (!d) return [0];
    let step = Math.max(1, Math.floor(Number(interval) || 30));
    const limit = Math.max(1, Math.floor(Number(cap) || 150));
    if (Math.floor(d / step) + 1 > limit) step = Math.ceil(d / (limit - 1));
    const out = [];
    for (let t = 0; t < d; t += step) out.push(t);
    return out;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /** The document: {video: meta, title, description, paragraphs: [{t, text}],
      frames: [{t, dataUrl}]} → HTML. A frame goes before the first paragraph
      at or after its moment, captioned with that paragraph's opening words. */
  function videoHtml(doc) {
    const v = doc.video || {};
    const link = (t) => v.url ? `${v.url}${v.url.includes("?") ? "&" : "?"}t=${t}s` : null;
    const paras = (doc.paragraphs || []).slice().sort((a, b) => a.t - b.t);
    const frames = (doc.frames || []).slice().sort((a, b) => a.t - b.t);
    const words = (text, n) => String(text || "").split(/\s+/).slice(0, n).join(" ");
    const body = [];
    let fi = 0;
    const emitFrame = (f, near) => {
      const cap = near ? `${fmtTime(f.t)} — ${words(near.text, 12)}` : `${fmtTime(f.t)} — frame`;
      body.push(`<figure data-t="${f.t}"><img src="${f.dataUrl}" alt=""><figcaption>${escapeHtml(cap)}</figcaption></figure>`);
    };
    for (const p of paras) {
      while (fi < frames.length && frames[fi].t <= p.t) emitFrame(frames[fi++], p);
      const href = link(p.t);
      body.push(`<p data-t="${p.t}">${href ? `<a href="${escapeHtml(href)}">${fmtTime(p.t)}</a>` : fmtTime(p.t)} ${escapeHtml(p.text)}</p>`);
    }
    while (fi < frames.length) emitFrame(frames[fi++], paras.length ? paras[paras.length - 1] : null);
    const desc = String(doc.description || "").split(/\n{2,}|\r?\n/).map((s) => s.trim()).filter(Boolean).map((s) => `<p>${escapeHtml(s)}</p>`).join("\n");
    const title = doc.title || v.title || "";
    return `<!doctype html>
<html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>
<meta name="prax-video" content="${escapeHtml(JSON.stringify(v))}">
<meta name="generator" content="prax capture"></head>
<body><article class="prax-video">
<h1>${escapeHtml(title)}</h1>
<p class="byline">${escapeHtml([v.channel, v.published, v.duration ? fmtTime(v.duration) : ""].filter(Boolean).join(" · "))}${v.url ? ` · <a href="${escapeHtml(v.url)}">${escapeHtml(v.url)}</a>` : ""}</p>
${desc ? `<section class="description">\n${desc}\n</section>` : ""}
<section class="transcript">
${body.join("\n")}
</section>
</article></body></html>
`;
  }

  return { MAX_HTML_BYTES, sessionId, capturable, looksLikePdf, plan, normalizeServer, originPattern, describeResult, splitList, isPdfResponse, pdfFileName, videoOfUrl, fmtTime, chooseTrack, groupCaptions, chaptersFrom, frameTimes, videoHtml };
});
