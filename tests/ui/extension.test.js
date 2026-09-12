// The extension's pure helpers (clients/extension/lib.js):
// node --test tests/ui/extension.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const lib = require(path.join(__dirname, "..", "..", "clients", "extension", "lib.js"));

test("sessionId is a UTC stamp plus four hex characters", () => {
  const id = lib.sessionId(new Date(Date.UTC(2026, 8, 12, 9, 5, 7)), 0.5);
  assert.equal(id, "20260912T090507-7fff");
  assert.match(lib.sessionId(), /^\d{8}T\d{6}-[0-9a-f]{4}$/);
});

test("capturable: only http(s) pages", () => {
  assert.ok(lib.capturable("https://example.org/a"));
  assert.ok(lib.capturable("http://127.0.0.1:8000/ui/"));
  for (const u of ["about:blank", "chrome://extensions", "moz-extension://x/popup.html", "file:///C:/a.pdf", "view-source:https://x", "", undefined]) {
    assert.equal(lib.capturable(u), false, u);
  }
});

test("looksLikePdf: by URL or by the viewer's DOM", () => {
  assert.ok(lib.looksLikePdf("https://x.org/paper.pdf", ""));
  assert.ok(lib.looksLikePdf("https://x.org/paper.pdf?dl=1", ""));
  assert.ok(lib.looksLikePdf("https://x.org/view", '<html><body><div id="viewer" class="pdfViewer"></div>'));
  assert.ok(lib.looksLikePdf("https://x.org/view", '<html><body><embed type="application/pdf" src="x">'));
  assert.equal(lib.looksLikePdf("https://x.org/pdf-tips", "<html><body><p>how to make a pdf</p>"), false);
});

test("plan: DOM when readable, URL for PDFs and huge or unread pages, skip for internal", () => {
  assert.deepEqual(lib.plan("https://x.org/a", "<html>hi</html>"), { mode: "html" });
  assert.equal(lib.plan("https://x.org/a.pdf", "<html>viewer</html>").mode, "url");
  assert.equal(lib.plan("https://x.org/a", null).mode, "url");
  assert.equal(lib.plan("https://x.org/a", "x".repeat(lib.MAX_HTML_BYTES + 1)).mode, "url");
  assert.equal(lib.plan("about:config", "<html>").mode, "skip");
});

test("normalizeServer and originPattern", () => {
  assert.equal(lib.normalizeServer(" http://127.0.0.1:8000/ "), "http://127.0.0.1:8000");
  assert.equal(lib.normalizeServer("https://pi.tail1234.ts.net"), "https://pi.tail1234.ts.net");
  assert.equal(lib.normalizeServer("pi:8000"), null);
  assert.equal(lib.normalizeServer("http://pi:8000/ui/"), null);
  assert.equal(lib.originPattern("http://pi:8000"), "http://pi:8000/*");
  assert.equal(lib.originPattern("nope"), null);
});

test("isPdfResponse: bytes first, then the content type", () => {
  const pdf = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d]);
  const html = new Uint8Array([0x3c, 0x68, 0x74, 0x6d, 0x6c]);
  assert.ok(lib.isPdfResponse("application/octet-stream", pdf));
  assert.equal(lib.isPdfResponse("application/pdf", html), false); // a login page
  assert.ok(lib.isPdfResponse("application/pdf; charset=binary", null));
  assert.equal(lib.isPdfResponse("text/html", null), false);
});

test("pdfFileName: the URL's last segment, made safe, ending in .pdf", () => {
  assert.equal(lib.pdfFileName("https://x.org/papers/smith%202020.pdf?dl=1"), "smith 2020.pdf");
  assert.equal(lib.pdfFileName("https://x.org/doi/pdf/10.1000/abc"), "abc.pdf");
  assert.equal(lib.pdfFileName("https://x.org/"), "document.pdf");
  assert.equal(lib.pdfFileName("nope"), "document.pdf");
});

test("describeResult and splitList", () => {
  assert.equal(lib.describeResult({ error: "401" }), "failed: 401");
  assert.equal(lib.describeResult({ created: true, indexed: true }), "new, searchable");
  assert.equal(lib.describeResult({ created: false, indexed: false, previous_capture: 7 }), "already in the store, waiting for the parse queue, follows doc 7");
  assert.deepEqual(lib.splitList(" a, b ,,c "), ["a", "b", "c"]);
  assert.deepEqual(lib.splitList(""), []);
});
