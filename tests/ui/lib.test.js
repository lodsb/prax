// node --test tests/ui  (run by tests/test_ui_js.py when node is present)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const lib = require("../../src/prax/ui/lib.js");

test("esc escapes markup and handles null", () => {
  assert.equal(lib.esc('<a href="x">&\'</a>'), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;");
  assert.equal(lib.esc(null), "");
  assert.equal(lib.esc(12), "12");
});

test("parseHash routes views, documents, chunks and queries", () => {
  assert.deepEqual(lib.parseHash(""), { name: "search", arg: "", params: {} });
  assert.deepEqual(lib.parseHash("#search?q=fdn&mode=fts"), { name: "search", arg: "", params: { q: "fdn", mode: "fts" } });
  assert.deepEqual(lib.parseHash("#doc/8072?chunk=2526521"), { name: "doc", arg: "8072", params: { chunk: "2526521" } });
  assert.deepEqual(lib.parseHash("#doc/8072/2526521"), { name: "doc", arg: "8072", params: { chunk: "2526521" } });
  assert.deepEqual(lib.parseHash("#doc/8072?find=%22Er%20erlaubt%22"), { name: "doc", arg: "8072", params: { find: '"Er erlaubt"' } });
  assert.deepEqual(lib.parseHash("#graph?entity=gorski-popiel%20methode"), { name: "graph", arg: "", params: { entity: "gorski-popiel methode" } });
  assert.equal(lib.parseHash("#doc/8072/notachunk").params.chunk, undefined);
});

test("headingPath escapes and joins", () => {
  assert.equal(lib.headingPath(["1 Intro", "1.1 <b>"]), "1 Intro › 1.1 &lt;b&gt;");
  assert.equal(lib.headingPath(null), "");
});

test("locateChunk finds the quote, falls back to word overlap, or gives up", () => {
  const chunks = [
    { chunk_id: 1, text: "Aktive Filter: ein Überblick über RC-Netzwerke." },
    { chunk_id: 2, text: "Er erlaubt ebenfalls die Simulation geerdeter und floatender Induktivitäten (-> Gorski-Popiel Methode)." },
    { chunk_id: 3, text: "Literatur und Anhang." },
  ];
  assert.equal(lib.locateChunk(chunks, '"Er erlaubt ebenfalls die Simulation geerdeter und floatender Induktivitäten"'), 2);
  // paraphrased evidence still lands on the chunk sharing most words
  assert.equal(lib.locateChunk(chunks, "Simulation floatender Induktivitäten mit der Gorski-Popiel Methode"), 2);
  assert.equal(lib.locateChunk(chunks, "completely unrelated words about reverb"), null);
  assert.equal(lib.locateChunk(chunks, ""), null);
  assert.equal(lib.locateChunk([], "anything at all"), null);
});

test("citeLinks links known passage numbers only", () => {
  const passages = [{ n: 1, doc_id: 7, chunk_id: 99, title: 'A "paper"' }, { n: 2, doc_id: 8, chunk_id: null, title: "B" }];
  const out = lib.citeLinks("<p>Claim [1] and [2], not [9].</p>", passages);
  assert.ok(out.includes('href="#doc/7?chunk=99"'));
  assert.ok(out.includes('href="#doc/8"'));
  assert.ok(out.includes('title="A &quot;paper&quot;"'));
  assert.ok(out.includes("not [9]."));
});


test("mathSpans finds maths and leaves prices alone", () => {
  const spans = (t) => lib.mathSpans(t).map((s) => [s.latex, s.display]);
  // a display equation as marker writes it, and the \[ \] form
  assert.deepEqual(spans("so $$i = I_s (e^{v/V_T} - 1)$$ holds"), [["i = I_s (e^{v/V_T} - 1)", true]]);
  assert.deepEqual(spans("and \\[x = \\frac{a}{b}\\] there"), [["x = \\frac{a}{b}", true]]);
  // inline: $…$ and \( \)
  assert.deepEqual(spans("$a$ and $b$, $\\frac{a}{b}$"), [["a", false], ["b", false], ["\\frac{a}{b}", false]]);
  assert.deepEqual(spans("with \\(a = v + iR\\) here"), [["a = v + iR", false]]);
  assert.deepEqual(spans("a $K \\rightarrow w$ step"), [["K \\rightarrow w", false]]);
  // a dollar sign is also a currency sign
  assert.deepEqual(spans("the current $i$ through it costs $5 and $10 today"), [["i", false]]);
  assert.deepEqual(spans("prices: $5 or $ 6, and $x$"), [["x", false]]);
  assert.deepEqual(spans("between $5 and $10"), []);
  assert.deepEqual(spans("a $-2$ dB step"), []);  // no letter, no backslash
  // stays on its line, stays short
  assert.deepEqual(spans("two lines $x\ny$ no"), []);
  assert.deepEqual(spans("$" + "x".repeat(201) + "$"), []);
  // positions are text offsets, in order
  const found = lib.mathSpans("see $a$ then $$b$$.");
  assert.deepEqual(found.map((s) => [s.start, s.end]), [[4, 7], [13, 18]]);
  assert.deepEqual(lib.mathSpans(""), []);
  assert.deepEqual(lib.mathSpans(null), []);
});

test("figureItems: the figure chunks with a picture, captioned by the reference or the first reading, placed by page or moment", () => {
  const chunks = [
    { chunk_id: 1, kind: "text", text: "prose" },
    { chunk_id: 2, kind: "figure", page: 3, data: { ref: "ab", caption: "Fig. 1: the filter" } },
    { chunk_id: 3, kind: "figure", time: 95, data: { ref: "cd", caption: "", readings: [{ model: "m", text: "A slide listing three steps. Then more." }] } },
    { chunk_id: 4, kind: "figure", data: { caption: "no ref" } },
  ];
  assert.deepEqual(lib.figureItems(chunks), [
    { chunk_id: 2, ref: "ab", caption: "Fig. 1: the filter", page: 3, time: null },
    { chunk_id: 3, ref: "cd", caption: "A slide listing three steps.", page: null, time: 95 },
  ]);
  assert.deepEqual(lib.figureItems([]), []);
});

test("referenceLinks and citeMarkers: numbered entries link the in-text markers to what they cite", () => {
  const chunks = [
    { chunk_id: 7, kind: "reference", data: { number: 1, title: "Islands of music", cited: { doc_id: 42, title: "Islands of Music", how: "sure", score: 0.97 } } },
    { chunk_id: 8, kind: "reference", data: { number: 2, title: "Unmatched work" } },
    { chunk_id: 9, kind: "reference", data: { title: "Author-year entry, no number" } },
    { chunk_id: 3, kind: "text", text: "prose" },
  ];
  const links = lib.referenceLinks(chunks);
  assert.deepEqual(Object.keys(links), ["1", "2"]);
  assert.equal(links["1"].href, "#doc/42");
  assert.equal(links["2"].href, "#chunk-8");
  const html = lib.citeMarkers("<p>As shown in [1] and [2, 5], see (3) and [3].</p>", links);
  assert.match(html, /\[<a class="cite" href="#doc\/42" title="Islands of Music">1<\/a>\]/);
  assert.match(html, /\[<a class="cite" href="#chunk-8" data-scroll="8" title="Unmatched work">2<\/a>, 5\]/);
  assert.ok(html.includes("(3) and [3]."));  // no entry 3: untouched
  assert.equal(lib.citeMarkers("<p>[1]</p>", {}), "<p>[1]</p>");
});

test("askInterior, askBlockMarkers and nextAskId: the block as shown, and as written", () => {
  const block = '<!-- prax:ask id=q1 steps=2 "how do FDNs stay lossless" -->\n\nAn answer [1].\n\n<!-- prax:keep -->\nMy remark.\n<!-- /prax:keep -->\n\nSources:\n\n- [1] [T](#doc/1)\n\n<!-- /prax:ask id=q1 sha=abc asked=2026-09-21 run=stub -->';
  assert.equal(lib.askInterior(block), "An answer [1].\n\n\nMy remark.\n\n\nSources:\n\n- [1] [T](#doc/1)");
  assert.equal(lib.askInterior('<!-- prax:ask id=q2 "q" -->\n<!-- /prax:ask id=q2 -->'), "");
  assert.equal(lib.askBlockMarkers("q3", ' why "this"  works ', { steps: 2, doctype: "" }), '<!-- prax:ask id=q3 steps=2 "why \'this\' works" -->\n<!-- /prax:ask id=q3 -->\n');
  assert.equal(lib.nextAskId(""), "q1");
  assert.equal(lib.nextAskId(block + '\n<!-- prax:ask id=q2 "x" -->'), "q3");
});

test("upPanel: the group's holder, what waits, and the buttons offered", () => {
  const swapped = lib.upPanel({
    up: { roles: { "llama-server": { state: "paused" }, marker: { state: "up" }, door: { state: "up" } },
          groups: { card: { holder: "marker", was_up: ["llama-server"], back_when: "idle", fits: false, members: ["llama-server", "marker"] } } },
    demand: { roles: { "llama-server": 4, marker: 1 } },
    gpu: [{ name: "RTX 4090", free_mb: 18000, total_mb: 24564 }],
  });
  assert.match(swapped, /marker<\/span> has it instead of llama-server/);
  assert.match(swapped, /back when nothing waits/);
  assert.match(swapped, /llama-server: paused · 4 waiting/);
  assert.match(swapped, /17\.6 GB free of 24\.0 GB/);
  assert.match(swapped, /class="secondary up-swap" data-to="llama-server">give it to llama-server \(4 waiting\)/);
  assert.match(swapped, /up-unswap" data-group="card">back to llama-server/);
  assert.doesNotMatch(swapped, /data-to="marker"/);  // it already holds it
  assert.match(swapped, /door: up/);  // a role in no group is listed, not offered
  // nothing on loan: the members and their states, no unswap
  const shared = lib.upPanel({
    up: { roles: { "llama-server": { state: "up" }, marker: { state: "paused" } },
          groups: {} },
    demand: { roles: {} }, gpu: [],
  });
  assert.match(shared, /No role shares a resource/);
  assert.doesNotMatch(shared, /up-unswap/);
  // no supervisor on the host: no panel at all
  assert.equal(lib.upPanel(null), "");
  assert.equal(lib.upPanel({ up: null }), "");
  assert.equal(lib.mb(null), "?");
  assert.equal(lib.mb(512), "512 MB");
});

test("spendPanel: the budget bars, the ledger, and a host that spends nothing", () => {
  const paid = lib.spendPanel({
    budget: { limits: { daily_usd: 5, monthly_usd: 50 }, spent: { day: 1.234, month: 12.5 }, ok: true, why: "" },
    since: "2026-08-23T00:00:00Z",
    ledger: { usd: 12.5, calls: 340, by_step: [{ step: "extract", usd: 9.2, calls: 300 }], by_model: [{ model: "claude-sonnet-5", usd: 12.5, calls: 340 }] },
    today: { usd: 1.234 },
  });
  assert.match(paid, /today: \$1\.23 of \$5\.00/);
  assert.match(paid, /this month: \$12\.50 of \$50\.00/);
  assert.match(paid, /340 paid calls since 2026-08-23/);
  assert.match(paid, /extract \$9\.20/);
  assert.match(paid, /--share:25%/);  // a quarter of today's budget
  const over = lib.spendPanel({
    budget: { limits: { daily_usd: 1 }, spent: { day: 1.4, month: 1.4 }, ok: false, why: "today's 1.00 USD is spent (1.40)" },
    ledger: { usd: 1.4, calls: 3, by_step: [], by_model: [] }, today: { usd: 1.4 },
  });
  assert.match(over, /spend-over/);
  assert.match(over, /held until it turns/);
  const free = lib.spendPanel({
    budget: { limits: {}, spent: { day: 0, month: 0 }, ok: true, why: "" },
    ledger: { usd: 0, calls: 0, by_step: [], by_model: [] }, today: { usd: 0 },
  });
  assert.match(free, /no limit set/);
  assert.match(free, /every step on this host is a local model/);
  assert.equal(lib.spendPanel(null), "");
  assert.equal(lib.usd(0.004), "0.40 ¢");
});

test("waitingNote: a queue nobody asks for, and one in hand", () => {
  const stuck = lib.waitingNote({
    step: "promote", model: "sonnet", paid: true, watched: false, asked: null,
    why: "no worker asks for promote work unless the run names it; sonnet costs money, so the run wants --spend",
    how: "prax work --steps promote --spend",
  }, 7);
  assert.match(stuck, /Nothing here is doing that work/);
  assert.match(stuck, /unless the run names it/);
  assert.match(stuck, /prax work --steps promote --spend -n 7/);
  assert.match(stuck, /spend: true/);
  const asked = lib.waitingNote({ why: "the budget is spent", how: "", asked: "2026-09-23T21:14:02Z" }, 3);
  assert.match(asked, /last asked at 21:14/);
  assert.equal(asked.includes("<code>"), false);  // no command to offer
  // nothing pending, or nothing in the way: no line at all
  assert.equal(lib.waitingNote({ why: "no worker has asked", how: "x" }, 0), "");
  assert.equal(lib.waitingNote({ why: "", how: "x" }, 7), "");
  assert.equal(lib.waitingNote(null, 7), "");
});

test("asideLine and ingredientsBox: what is folded, and the recipe box", () => {
  const ad = lib.asideLine("ad", { brand: "brilliant", why: ["“20% off”"] }, "x".repeat(1200));
  assert.match(ad, /advertisement · brilliant/);
  assert.match(ad, /1\.2k characters/);
  assert.match(lib.asideLine("comment", null, "abc"), /what readers wrote · 3 characters/);
  assert.match(lib.asideLine("comment", null, "x".repeat(4200), 6), /6 blocks · 4\.2k characters/);
  const box = lib.ingredientsBox({
    servings: { text: "Für 4 Personen", n: 4 },
    groups: [
      { name: null, items: [{ text: "60 ml Olivenöl", amount: 60, unit: "ml", item: "Olivenöl" },
                            { text: "½ TL Salz", amount: 0.5, unit: "TL", item: "Salz" }] },
      { name: "Für die Soße", items: [{ text: "120 g Sour Cream", amount: 120, unit: "g", item: "Sour Cream", note: "kalt" }] },
    ],
  });
  assert.match(box, /Für 4 Personen/);
  assert.match(box, /<b>60 ml<\/b> Olivenöl/);
  assert.match(box, /<b>½ TL<\/b> Salz/);
  assert.match(box, /<h4>Für die Soße<\/h4>/);
  assert.match(box, /\(kalt\)/);
  assert.equal(lib.ingredientsBox({ groups: [] }), "");
  assert.equal(lib.amount(2), "2");
  assert.equal(lib.amount(1.5), "1½");
});

test("languageName: a code under a name, and an unknown one as it stands", () => {
  assert.equal(lib.languageName("de"), "German");
  assert.equal(lib.languageName("xx"), "xx");
  assert.equal(lib.languageName(null), "");
});
