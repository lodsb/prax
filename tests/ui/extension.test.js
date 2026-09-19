// The extension's pure helpers (clients/browser-extension/lib.js):
// node --test tests/ui/extension.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const lib = require(path.join(__dirname, "..", "..", "clients", "browser-extension", "lib.js"));

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

test("videoOfUrl: YouTube's watch pages in their forms, nothing else", () => {
  assert.deepEqual(lib.videoOfUrl("https://www.youtube.com/watch?v=abc123&t=5s"), { provider: "youtube", id: "abc123" });
  assert.deepEqual(lib.videoOfUrl("https://m.youtube.com/watch?v=abc123"), { provider: "youtube", id: "abc123" });
  assert.deepEqual(lib.videoOfUrl("https://youtu.be/xyz789?si=1"), { provider: "youtube", id: "xyz789" });
  assert.deepEqual(lib.videoOfUrl("https://www.youtube.com/shorts/short1234"), { provider: "youtube", id: "short1234" });
  assert.equal(lib.videoOfUrl("https://www.youtube.com/"), null);
  assert.equal(lib.videoOfUrl("https://example.org/watch?v=1"), null);
  assert.equal(lib.videoOfUrl("nope"), null);
});

test("fmtTime and frameTimes", () => {
  assert.equal(lib.fmtTime(0), "0:00");
  assert.equal(lib.fmtTime(754), "12:34");
  assert.equal(lib.fmtTime(3754), "1:02:34");
  assert.deepEqual(lib.frameTimes(125, 30, 150), [0, 30, 60, 90, 120]);
  assert.deepEqual(lib.frameTimes(0, 30, 150), [0]);
  const long = lib.frameTimes(36000, 30, 150); // ten hours: the interval stretches to the cap
  assert.ok(long.length <= 150 && long.length >= 140 && long[1] - long[0] > 30);
});

test("chooseTrack: a person's in the reader's language, then any person's, then the automatic", () => {
  const tracks = [
    { baseUrl: "a", languageCode: "de", kind: "asr" },
    { baseUrl: "b", languageCode: "en", kind: "" },
    { baseUrl: "c", languageCode: "de", kind: "" },
  ];
  assert.equal(lib.chooseTrack(tracks, "de-DE").baseUrl, "c");
  assert.equal(lib.chooseTrack(tracks, "fr").baseUrl, "b");
  assert.equal(lib.chooseTrack([tracks[0]], "en").baseUrl, "a");
  assert.equal(lib.chooseTrack([], "en"), null);
});

test("groupCaptions: segments run together until a pause, a sentence end past the target, or the cap", () => {
  const ev = (t, d, s) => ({ tStartMs: t, dDurationMs: d, segs: [{ utf8: s }] });
  const paras = lib.groupCaptions([
    ev(0, 1000, "welcome everyone"), ev(1000, 1000, "to the talk."), ev(1100, 100, "\n"),
    ev(4000, 1000, "after a pause"), ev(4000, 1000, "after a pause"), // a rolling repeat
  ]);
  assert.deepEqual(paras, [{ t: 0, text: "welcome everyone to the talk." }, { t: 4, text: "after a pause" }]);
  const long = lib.groupCaptions(Array.from({ length: 40 }, (_, i) => ev(i * 1000, 1000, `twelve letter words number ${i}.`)));
  assert.ok(long.length > 1 && long.every((p) => p.text.length <= 420));
});

test("chaptersFrom: a description's timestamp list, three or more from 0:00, in order", () => {
  assert.deepEqual(lib.chaptersFrom("Slides: x\n0:00 Intro\n1:05 - The envelope\n(12:34) Density\nthanks"), [
    { t: 0, title: "Intro" }, { t: 65, title: "The envelope" }, { t: 754, title: "Density" },
  ]);
  assert.deepEqual(lib.chaptersFrom("1:00 late\n2:00 b\n3:00 c"), []);
  assert.deepEqual(lib.chaptersFrom("0:00 a\n2:00 b"), []);
  assert.deepEqual(lib.chaptersFrom("0:00 a\n3:00 b\n2:00 c"), []);
});

test("videoHtml: the shape the door's video parser reads, frames before their paragraphs", () => {
  const html = lib.videoHtml({
    video: { provider: "youtube", id: "abc", url: "https://www.youtube.com/watch?v=abc", channel: "C", duration: 100 },
    title: "T <1>", description: "line one\n\nline two",
    paragraphs: [{ t: 0, text: "hello <world>" }, { t: 40, text: "later" }],
    frames: [{ t: 0, dataUrl: "data:image/jpeg;base64,AAA" }, { t: 30, dataUrl: "data:image/jpeg;base64,BBB" }, { t: 90, dataUrl: "data:image/jpeg;base64,CCC" }],
  });
  assert.ok(html.includes('<meta name="prax-video" content="{&quot;provider&quot;:&quot;youtube&quot;'));
  assert.ok(html.includes("<h1>T &lt;1&gt;</h1>"));
  assert.ok(html.includes("<p>line one</p>\n<p>line two</p>"));
  const order = [...html.matchAll(/<(figure|p) data-t="(\d+)"/g)].map((m) => `${m[1]}${m[2]}`);
  assert.deepEqual(order, ["figure0", "p0", "figure30", "p40", "figure90"]);
  assert.ok(html.includes('<figcaption>0:30 — later</figcaption>'));
  assert.ok(html.includes('<a href="https://www.youtube.com/watch?v=abc&amp;t=40s">0:40</a> later'));
  assert.equal(lib.describeResult({ mode: "video", created: true, note: "transcript, 3 frames" }), "new: transcript, 3 frames");
});

test("parseStoryboard and storyboardPlan: the best level, the sheet and the tile of a moment", () => {
  const spec = "https://i.ytimg.com/sb/abc/storyboard3_L$L/$N.jpg?sqp=SIG|48#27#100#10#10#0#default#rs$A|80#45#113#10#10#10000#M$M#rs$B|320#180#113#3#3#10000#M$M#rs$C";
  const lv = lib.parseStoryboard(spec);
  assert.equal(lv.w, 320); assert.equal(lv.h, 180); assert.equal(lv.cols, 3); assert.equal(lv.intervalMs, 10000); assert.equal(lv.level, 2);
  assert.equal(lv.sheetUrl(4), "https://i.ytimg.com/sb/abc/storyboard3_L2/M4.jpg?sqp=SIG&sigh=rs$C");
  const plan = lib.storyboardPlan(lv, [0, 30, 95, 100000]);
  assert.deepEqual(plan.map((p) => [p.t, p.url.endsWith("/M0.jpg?sqp=SIG&sigh=rs$C") ? 0 : p.url.endsWith("/M1.jpg?sqp=SIG&sigh=rs$C") ? 1 : "other", p.x, p.y]), [
    [0, 0, 0, 0], [30, 0, 0, 180], [95, 1, 0, 0], [100000, "other", 320, 180],  // the last moment clamps to picture 112: sheet 12, tile 4
  ]);
  assert.equal(lib.parseStoryboard(""), null);
  assert.equal(lib.parseStoryboard("https://x/$L/$N|0#0#0#0#0#0#a#b"), null);
});
