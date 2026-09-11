/* prax capture: the pure helpers, shared by the popup, the options page and
   the background, and tested with node (tests/ui/extension.test.js). No
   browser API in here. */

(function (root, factory) {
  const lib = factory();
  if (typeof module === "object" && module.exports) module.exports = lib;
  root.praxLib = lib;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const MAX_HTML_BYTES = 8 * 1024 * 1024;
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
    if (byteLength(html) > MAX_HTML_BYTES) return { mode: "url", reason: "page above 8 MB: the door fetches it" };
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

  /** A one-line result for the popup. */
  function describeResult(res) {
    if (res.error) return `failed: ${res.error}`;
    const bits = [res.created ? "new" : "already in the store"];
    bits.push(res.indexed ? "searchable" : "waiting for the parse queue");
    if (res.previous_capture) bits.push(`follows doc ${res.previous_capture}`);
    return bits.join(", ");
  }

  function splitList(text) {
    return String(text || "").split(",").map((s) => s.trim()).filter(Boolean);
  }

  return { MAX_HTML_BYTES, sessionId, capturable, looksLikePdf, plan, normalizeServer, originPattern, describeResult, splitList };
});
