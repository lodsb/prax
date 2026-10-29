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
  // who holds the card, and a server given back after a quiet while
  const held = lib.upPanel({
    up: { roles: { "llama-server": { state: "idle" }, marker: { state: "paused" } },
          groups: { card: { members: ["llama-server", "marker"] } } },
    demand: { roles: {} }, gpu: [],
    gpu_holders: [{ pid: 1, name: "python", mb: 2250 }, { pid: 2, name: "dwm", mb: 800 }],
  });
  assert.match(held, /On the card: python 2\.2 GB · dwm 800 MB/);
  assert.match(held, /llama-server: <span title="stopped after a quiet while[^"]*">idle, unloaded<\/span>/);
  // what waits, by role and action, each with "do it now"
  const waiting = lib.upPanel({
    up: { roles: { "llama-server": { state: "up" }, marker: { state: "paused", load_s: 41 } },
          groups: { card: { members: ["llama-server", "marker"] } } },
    demand: { roles: { marker: 2 }, ask_holds: true, groups: [
      { role: "marker", action: "marker", waiting: 2, rate: 1.5, hours_left: 1.3, now: false },
      { role: "llama-server", action: "vision-pages", waiting: 3, rate: null, hours_left: null, now: true },
    ] }, gpu: [],
    plan: { groups: [{ role: "marker", action: "marker", decision: "waits", why: "until the nightly window, or 360 s more", swap_s: 220, swap_guessed: true }] },
  });
  assert.match(waiting, /waits: until the nightly window, or 360 s more · a swap of about 4 min \(guessed\)<\/span>/);
  assert.match(waiting, /marker: 2 marker · 1\.5\/h · about 1 h left · needs the card \(marker is paused, loads in about 41 s\)[^]*?<button[^>]*class="linkish up-now" data-role="marker" data-action="marker">do it now/);
  assert.match(waiting, /llama-server: 3 vision-pages · <span class="up-holder">asked for now, after the answer in progress/);
  assert.doesNotMatch(waiting, /data-role="llama-server"/);
  // no supervisor on the host: no panel at all
  assert.equal(lib.upPanel(null), "");
  assert.equal(lib.upPanel({ up: null }), "");
  assert.equal(lib.mb(null), "?");
  assert.equal(lib.mb(512), "512 MB");
});

test("regionList and regionPage: the regions, named or by their members", () => {
  const list = lib.regionList([
    { id: 3, size: 132, label: "Everyday cooking", summary: "Onions and garlic.", summary_state: "fresh", members: ["salt"] },
    { id: 4, size: 90, label: null, summary: null, summary_state: "none", members: ["wavelet", "spectrogram", "fft", "x"] },
    { id: 5, size: 20, label: "Old", summary: "Once.", summary_state: "stale", members: [] },
  ]);
  assert.match(list, /href="#graph\?community=3">Everyday cooking<\/a>/);
  assert.match(list, />wavelet, spectrogram, fft<\/a>/);  // unnamed: its first members
  assert.match(list, /the summary predates its members/);
  assert.equal(lib.regionList([]), "");
  const page = lib.regionPage({
    id: 7, level: 1, parent: 3, size: 12, label: "Soups", summary: "Broths.", summary_state: "fresh",
    members: [{ id: 1, name: "onion", type: "ingredient" }], parts: [],
    documents: [{ id: 42, title: "Dish 1", named: 4 }],
  });
  assert.match(page, /Part of <a href="#graph\?community=3">/);
  assert.match(page, /href="#graph\?entity=onion&type=ingredient">onion<\/a>/);
  assert.match(page, /href="#doc\/42">Dish 1<\/a> <span class="muted">4 of its things/);
  assert.match(lib.regionPage({ id: 8, size: 9, members: [], parts: [], documents: [], summary: null }), /Not described yet/);
  assert.match(lib.regionPage(null), /No such region/);
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

test("queueRate: how fast a queue moves, and how long that leaves", () => {
  const demand = { readings: { figures: 3477, marker: 0 }, rate: { figures: 55 }, hours_left: { figures: 63.2 } };
  assert.equal(lib.queueRate(demand, "figures"), "3477 waiting · 55/h · about 3 days left");
  assert.equal(lib.queueRate(demand, "marker"), "");
  assert.match(lib.queueRate({ readings: { figures: 12 } }, "figures"), /nothing has read one in hours/);
  const soon = { readings: { formulas: 40 }, rate: { formulas: 10 }, hours_left: { formulas: 4 } };
  assert.equal(lib.queueRate(soon, "formulas"), "40 waiting · 10/h · about 4 h left");
});

test("pairRow, splitRow, mergeRow: each button carries its decision", () => {
  const side = (id, name, type, edges) => ({ id, name, type, edges, document: "A paper" });
  const pair = lib.pairRow({ type: "method", score: 0.97, keep: side(1, "wavelet transform", "method", 9), other: side(2, "wavelet transformation", "method", 1) });
  assert.match(pair, /data-act="same" data-keep="1" data-other="2">same, keep “wavelet transform”/);
  assert.match(pair, /data-act="same" data-keep="2" data-other="1">same, keep “wavelet transformation”/);
  assert.match(pair, /data-act="different"/);
  assert.match(pair, /href="#graph\?entity=wavelet%20transform&type=method"/);
  assert.match(pair, /in “A paper”/);
  const split = lib.splitRow({ name: "SuperCollider", parts: [{ id: 5, type: "tool", edges: 65 }, { id: 6, type: "method", edges: 7 }] });
  assert.match(split, /data-act="one" data-keep="5" data-type="tool" data-others="6">one thing: a tool/);
  assert.match(split, /data-act="apart" data-ids="5,6">several things/);
  const merge = lib.mergeRow({ why: "one word apart", by: null, alias: side(8, "postorder traversal", "method", 2), into: side(7, "preorder traversal", "method", 4) });
  assert.match(merge, /one word apart · unsigned/);
  assert.match(merge, /data-act="split" data-entity="8">wrong: split them/);
  assert.match(merge, /data-act="same" data-keep="7" data-other="8">right/);
  assert.equal(lib.mergeRow({ why: "narrower", alias: null, into: null }), "");
});

test("foreignRow: theirs beside ours, three answers each naming the import and the gid", () => {
  const row = lib.foreignRow({ source: "laptop", gid: "e:ABC", name: "wave digital filter", type: "method", labels: [{ label: "Wellendigitalfilter", lang: "de" }], held: 2,
    candidate: { id: 4, name: "wave-digital filter", type: "method", edges: 9 } });
  for (const verdict of ["same", "new", "left_out"]) {
    assert.match(row, new RegExp(`data-act="foreign" data-source="laptop" data-gid="e:ABC" data-verdict="${verdict}"`));
  }
  assert.match(row, /from laptop · 2 facts wait/);
  assert.match(row, /also: Wellendigitalfilter/);
  assert.match(row, /href="#graph\?entity=wave-digital%20filter&type=method"/);
  assert.match(lib.foreignRow({ source: "x", gid: "e:1", name: "a", type: "concept", held: 1, candidate: null }), /1 fact waits[\s\S]*\(gone\)/);
});

test("pairRow shows the local model's number; sameRule folds the rule above the pairs", () => {
  const side = (id, name) => ({ id, name, type: "method", edges: 1 });
  const pair = lib.pairRow({ type: "method", score: 0.95, p_same: 0.734, keep: side(1, "windowing"), other: side(2, "windowing techniques") });
  assert.match(pair, /model: 73% same/);
  assert.doesNotMatch(lib.pairRow({ type: "method", score: 0.95, p_same: null, keep: side(1, "a"), other: side(2, "b") }), /model:/);
  const rule = lib.sameRule({
    same: [{ case: "a product and a version of it", examples: [["Ableton Live", "Ableton Live 7"]] }],
    different: [{ case: "a task and a tool that does it", examples: [] }],
    modules: { kitchen: { same: [], different: [{ case: "a dish and a variant of it", examples: [["Lasagne", "vegane Lasagne"]] }] } },
  });
  assert.match(rule, /<summary>What counts as the same thing<\/summary>/);
  assert.match(rule, /a product and a version of it <span class="muted">\(Ableton Live · Ableton Live 7\)/);
  assert.match(rule, /<h4>kitchen<\/h4><ul><li>different: a dish and a variant of it/);
  assert.equal(lib.sameRule({ same: [] }), "");
});

test("suspectRow: the cues, where it came from, and the two decisions", () => {
  const row = lib.suspectRow({ id: 7, title: "Rechnung Mai", mime: "application/pdf", added_at: "2026-09-28T00:40:00Z",
    path: "/mnt/nas/admin/Rechnung.pdf", cues: ["weak: rechnung", "name"] });
  assert.match(row, /href="#doc\/7">Rechnung Mai/);
  assert.match(row, /rechnung · name/);
  assert.match(row, /\/mnt\/nas\/admin\/Rechnung.pdf/);
  assert.match(row, /data-act="personal" data-doc="7">personal/);
  assert.match(row, /data-act="open" data-doc="7">not personal/);
});

test("cleanupRules, cleanupPreview, cleanupRuns: a set shown before it goes, and the way back", () => {
  const rules = lib.cleanupRules({ system: "from a system folder", names: "a licence by its name", folder: "under a folder" }, "names", "");
  assert.match(rules, /<a href="#admin\?tab=cleanup&rule=system">from a system folder<\/a>/);
  assert.match(rules, /<b>a licence by its name<\/b>/);
  assert.doesNotMatch(rules, /under a folder<\/a>/);
  assert.match(rules, /id="cleanup-folder"/);
  const preview = lib.cleanupPreview({ rule: "same-text", about: "the same text", folder: null, total: 55, edges: 308, personal: 8,
    items: [{ id: 9, title: "Paper copy", path: "/nas/b/paper.pdf", duplicate_of: 4 }] });
  assert.match(preview, /55 documents · 308 facts · 8 marked or suspected personal · a sample of 1/);
  assert.match(preview, /a copy of doc 4/);
  assert.match(preview, /data-retire="1">retire 55 documents/);
  assert.match(lib.cleanupPreview({ rule: "help", about: "help", total: 0, edges: 0, personal: 0, items: [] }), /nothing to retire/);
  const runs = lib.cleanupRuns([{ run: "cleanup-20260928T1200Z-system", at: "2026-09-28T12:00:00Z", reason: "clean-up: system", documents: 238 }]);
  assert.match(runs, /238 documents · 2026-09-28 12:00/);
  assert.match(runs, /data-restore="cleanup-20260928T1200Z-system">restore/);
  assert.equal(lib.cleanupRuns([]), "");
});

test("tokensTable, tokenSecret, privateRules: the admin page", () => {
  const table = lib.tokensTable({ tokens: [
    { name: "laptop", domains: ["research", "studio"], personal: false, role: "contributor", created_at: "2026-09-28T10:00:00Z", last_used: "2026-09-28T11:30:00Z" },
    { name: "assistant", domains: null, personal: true, role: "reader", created_at: "2026-09-28T10:00:00Z", last_used: null },
  ], modules: ["core", "research", "studio", "unassigned"], roles: ["reader", "contributor"] });
  assert.match(table, /<b>laptop<\/b><\/td>\s*<td><select data-role="laptop"><option>reader<\/option><option selected>contributor<\/option><\/select><\/td>\s*<td>research, studio<\/td>/);
  assert.match(table, /<select name="role"><option>reader<\/option>/);
  assert.match(table, /every module/);
  assert.match(table, /sees personal/);
  assert.match(table, /2026-09-28 11:30/);
  assert.match(table, />never</);
  assert.match(table, /data-revoke="assistant">revoke/);
  assert.match(table, /<input type="checkbox" name="domain" value="unassigned"> unassigned/);
  assert.match(lib.tokensTable({ tokens: [], modules: [] }), /No named tokens yet/);
  const secret = lib.tokenSecret("laptop", "prax_abc");
  assert.match(secret, /shown this once/);
  assert.match(secret, /<pre>prax_abc<\/pre>/);
  const rules = lib.privateRules({ strong: ["kontoauszug"], weak: [["rechnung", "invoice"]], weak_needed: 2, head: 5000,
    added: { strong: [], weak: [], paths: ["/archive/private-example"], names: 2 }, documents: { suspected: 404, personal: 3, released: 1 } });
  assert.match(rules, /404 suspected, 3 personal, 1 released by you</);
  const named = lib.privateRules({ strong: [], weak: [], weak_needed: 2, head: 5000, added: {},
    documents: { suspected: 1, personal: 0, released: 0, named_faces: 7 } });
  assert.match(named, /0 released by you, 7 hidden for a named face/);
  assert.match(rules, /rechnung \/ invoice/);
  assert.match(rules, /2 names \(a weak cue each\)/);
  assert.match(rules, /everything under \/archive\/private-example/);
});

test("regionLine: where the search hits live, above them", () => {
  const line = lib.regionLine({ kind: "region", region: { id: 3, label: "Everyday cooking", share: 0.72, summary: "Onions and garlic" }, part: { id: 9, label: "Soups" } });
  assert.match(line, /mostly in<\/span> <a href="#graph\?community=3">Everyday cooking<\/a> › <a href="#graph\?community=9">Soups<\/a>/);
  assert.match(line, /72% of what the first hits are about/);
  assert.match(line, /Onions and garlic/);
  assert.equal(lib.regionLine(null), "");
  assert.doesNotMatch(lib.regionLine({ region: { id: 1, label: "A", share: 0.5 }, part: null }), /›/);
});

test("propertiesHtml: where it came from, what it is, where it belongs, who may see it", () => {
  const doc = { id: 7, title: "Rechnung Mai", mime: "application/pdf", hash: "abcdef0123456789abcdef", added_at: "2026-09-28T00:40:00Z",
    parsed_at: "2026-09-28T01:00:00Z", text_len: 1234, original_path: null, source_url: null, sensitivity: "personal",
    meta: { source: "upload", capture: { by: "send" }, origin: { host: "nas", path: "/volume1/admin/Rechnung.pdf" }, lang: "de",
      text_source: "pymupdf4llm/1.28.2-r2", domains: ["research"], domains_by: "rule", tags: ["bills"],
      sensitivity: { state: "personal", by: "human", at: "2026-09-28T20:00:00Z" }, private: { cues: ["weak: rechnung", "name"] } } };
  const html = lib.propertiesHtml(doc);
  assert.match(html, /nas:\/volume1\/admin\/Rechnung.pdf/);
  assert.match(html, /pymupdf4llm\/1.28.2-r2, 1,234 characters/);
  assert.match(html, /research <span class="muted">\(rule\)<\/span>/);
  assert.match(html, /personal, by you on 2026-09-28 20:00/);
  assert.match(html, /what the rules found: rechnung · name/);
  assert.match(html, /data-props="open">not personal/);
  assert.doesNotMatch(html, /data-props="personal"/);
  const open = lib.propertiesHtml({ id: 8, title: "Paper", sensitivity: null, meta: {} });
  assert.match(open, /every module/);
  assert.match(open, /open\. Every token/);
  assert.match(open, /data-props="personal">personal/);
  assert.doesNotMatch(open, /data-props="open"/);
});

test("suspectRow for a document marked personal: when, and only the way back", () => {
  const row = lib.suspectRow({ id: 3, title: "Kontoauszug", state: "personal", decided: "2026-09-28T20:00:00Z", cues: ["strong: kontoauszug"] });
  assert.match(row, /marked 2026-09-28 · kontoauszug/);
  assert.doesNotMatch(row, /data-act="personal"/);
  assert.match(row, /data-act="open" data-doc="3">not personal/);
});

test("genreRow: the document as a labeller reads it, the vocabulary as boxes, its genres ticked", () => {
  const vocab = { levels: [
    { name: "instructional", description: "tells how", genres: [{ name: "datasheet", description: "a part's sheet" }, { name: "manual", description: "a manual" }] },
    { name: "opinion", description: "argues", genres: [{ name: "essay", description: "an essay" }] },
  ] };
  const it = { id: 5, title: "TL072 <dual op-amp>", source: "nas", lang: "en", mime: "application/pdf", where: "/nas/tl072.pdf",
    summary: "A datasheet.", opening: "TL07x Low-Noise JFET-Input", genres: ["datasheet"], genres_by: "human", genres_at: "2026-09-29T10:00:00Z" };
  const html = lib.genreRow(it, vocab);
  assert.match(html, /TL072 &lt;dual op-amp&gt;/);
  assert.match(html, /nas · en · application\/pdf · \/nas\/tl072.pdf/);
  assert.match(html, /value="datasheet" checked/);
  assert.doesNotMatch(html, /value="manual" checked/);
  assert.match(html, /genre-level"[^>]*title="tells how"/);
  assert.match(html, /the opening of the text/);
  assert.match(html, /labelled 2026-09-29/);
  assert.doesNotMatch(html, /what it is about/);  // no subjects in this vocabulary
  const both = lib.genreRow({ ...it, subjects: ["politics"] }, { ...vocab,
    subjects: { levels: [{ name: "society", description: "people", genres: [{ name: "politics", description: "policy" }, { name: "sociology", description: "society" }] }] } });
  assert.match(both, /what it is about/);
  assert.match(both, /name="subject" value="politics" checked/);
  assert.doesNotMatch(both, /name="subject" value="sociology" checked/);
  assert.match(both, /name="genre" value="datasheet" checked/);
  const bare = lib.genreRow({ id: 6, title: null, genres: [] }, vocab);
  assert.match(bare, /doc 6/);
  assert.match(bare, /No summary yet/);
  assert.doesNotMatch(bare, /checked/);
  assert.doesNotMatch(bare, /the opening of the text/);
});

test("genreRow for a model's labels: its probabilities and reason, the unsure marked", () => {
  const vocab = { levels: [{ name: "opinion", description: "argues", genres: [{ name: "essay", description: "e" }, { name: "blog", description: "b" }] }] };
  const html = lib.genreRow({ id: 9, title: "Collapse", genres: ["essay", "blog"], genres_by: "claude",
    p: { essay: 0.55, blog: 0.9 }, note: "an essay on a blog" }, vocab);
  assert.match(html, /Labelled by claude: an essay on a blog/);
  assert.match(html, /essay <span class="genre-p unsure">55%<\/span>/);
  assert.match(html, /blog <span class="genre-p">90%<\/span>/);
  const mine = lib.genreRow({ id: 9, title: "Collapse", genres: ["essay"], genres_by: "human", p: { essay: 1 } }, vocab);
  assert.doesNotMatch(mine, /genre-p|Labelled by/);
});

test("labelList: the labels under a level, a model's with its probability, and who gave them", () => {
  const levels = new Set(["informational", "technology"]);
  const mine = { genres_by: "human" };
  assert.equal(lib.labelList([{ genre: "informational", p: 1 }, { genre: "paper", p: 1 }], "genre", mine, levels), 'paper <span class="muted">(you)</span>');
  const model = { genres_by: "qwen@host" };
  assert.equal(lib.labelList([{ subject: "technology", p: 0.9 }, { subject: "audio", p: 0.72 }], "subject", model, levels),
    'audio <span class="muted">72%</span> <span class="muted">(qwen@host)</span>');
  assert.match(lib.labelList([{ genre: "informational", p: 1 }], "genre", mine, levels), /^informational/); // a level alone shows
  assert.equal(lib.labelList([], "genre", mine, levels), "");
  const html = lib.propertiesHtml({ id: 3, title: "P", meta: { genres_by: "human", genres: [{ genre: "paper", p: 1 }] } }, levels);
  assert.match(html, /<th>genres<\/th><td>paper/);
});


test("domainChips: a link per domain; none set says every module unless quiet", () => {
  const html = lib.domainChips(["studio", "a&b"], (d) => `#browse?domain=${d}`, false);
  assert.match(html, /href="#browse\?domain=studio">studio<\/a>/);
  assert.match(html, />a&amp;b<\/a>/);
  assert.match(lib.domainChips(null, () => "", false), /every module/);
  assert.equal(lib.domainChips([], () => "", true), "");
});

test("checksBox lists what does not hold, differs or was not checked", () => {
  assert.equal(lib.checksBox([]), "");
  assert.equal(lib.checksBox([{ kind: "link", verdict: "holds", link: "a = a" }]), "");
  const box = lib.checksBox([
    { kind: "link", verdict: "does not hold", link: "x <y> = 1" },
    { kind: "number", verdict: "differs", number: "13.28", result: "13.23" },
    { kind: "number", verdict: "not checked", number: "0.9366" },
    { kind: "number", verdict: "computed", number: "0.01323" },
  ]);
  assert.match(box, /does not hold: <code>x &lt;y&gt; = 1<\/code>/);
  assert.match(box, /13.28: the calculator gives 13.23/);
  assert.match(box, /0.9366: not checked/);
  assert.doesNotMatch(box, /0.01323/);
});

test("chunkTarget: a chunk id is trusted while it holds the link's words", () => {
  const chunks = [
    { chunk_id: 1, text: "the onset detector uses spectral flux" },
    { chunk_id: 2, text: "delta delta the new section after a re-sync" },
    { chunk_id: 3, text: "gamma gamma the passage the page cited" },
  ];
  assert.equal(lib.chunkTarget(chunks, "1", ""), 1);
  assert.equal(lib.chunkTarget(chunks, "9", ""), null);  // gone: no guess
  assert.equal(lib.chunkTarget(chunks, "1", "spectral flux"), 1);
  // id 2 was handed to other text: the words find the passage
  assert.equal(lib.chunkTarget(chunks, "2", "the passage the page cited"), 3);
  assert.equal(lib.chunkTarget(chunks, null, "spectral flux"), 1);
  assert.equal(lib.chunkTarget(chunks, "2", "words nowhere at all here"), null);
});

test("publishedLabel: the date as its source says, and what said so", () => {
  assert.equal(lib.publishedLabel({ published: { date: "2019-07", precision: "month", by: "arxiv" } }), '<span title="published, as its arXiv id says">2019-07</span>');
  assert.equal(lib.publishedLabel({ date: "2019" }), "2019");  // before the pass ran
  assert.equal(lib.publishedLabel({ date: "<b>2019</b>" }), "&lt;b&gt;2019&lt;/b&gt;");
  assert.equal(lib.publishedLabel({}), "");
  assert.equal(lib.publishedLabel(null), "");
});

test("dateLine: published, added and read, each said for what it is", () => {
  const meta = { published: { date: "2019-07", precision: "month", by: "record" } };
  const doc = { added_at: "2026-09-12T10:00:00Z", parsed_at: "2026-10-02T03:00:00Z" };
  const line = lib.dateLine(meta, doc);
  assert.match(line, /^published <span title="published, as its record \(Zotero\) says">2019-07<\/span>/);
  assert.match(line, /added 2026-09-12/);
  assert.match(line, /read 2026-10-02/);
  // read the day it came: said once
  assert.doesNotMatch(lib.dateLine({}, { added_at: "2026-09-12T10:00:00Z", parsed_at: "2026-09-12T10:05:00Z" }), /read/);
  assert.equal(lib.dateLine({}, null), "");
});

test("publishedLabel: a model's date says its words, and a bare one is asked", () => {
  const sure = lib.publishedLabel({ published: { date: "2009", by: "first-page", confidence: "high", words: "DAFx-09, Como" } });
  assert.match(sure, /its first page, read by a model says: “DAFx-09, Como”">2009<\/span>/);
  const bare = lib.publishedLabel({ published: { date: "2007-05-30", by: "first-page", confidence: "medium", words: "30. Mai 2007" } });
  assert.match(bare, /not said to be its publication\)">2007-05-30\?<\/span>/);
});

test("pairRow: a held pair says why it waits", () => {
  const side = (id, name) => ({ id, name, type: "concept", edges: 3 });
  const held = lib.pairRow({ type: "concept", score: 0.95, p_same: null, keep: side(1, "spectral flux"), other: side(2, "spectral fluxes"), held: "held: a page says something about it" });
  assert.match(held, /class="decide-row held"/);
  assert.match(held, /the merge waits for you \(a page says something about it\)/);
  const plain = lib.pairRow({ type: "concept", score: 0.95, p_same: null, keep: side(1, "a"), other: side(2, "b") });
  assert.doesNotMatch(plain, /waits for you/);
});

test("staleNote: the state, the date and what replaced it, as a link", () => {
  const html = lib.staleNote({ state: "superseded", since: "2026-10-02", by: "sync", replaced_by: [{ doc_id: 12, title: "Plan <v2>" }] });
  assert.match(html, /class="stale"/);
  assert.match(html, /superseded 2026-10-02/);
  assert.match(html, /href="#doc\/12">Plan &lt;v2&gt;<\/a>/);
  assert.equal(lib.staleNote(null), "");
  assert.match(lib.staleNote({ state: "invalid", by: "graph", replaced_by: [] }), /another document says so/);
  const line = lib.dateLine({ status: { state: "retired", since: "2026-09", words: "Retired 2026-09" } }, {});
  assert.match(line, /class="stale"[^>]*>retired 2026-09/);
});

test("staleSources: a page's sources no longer current, each with its replacement", () => {
  const html = lib.staleSources([{ doc_id: 3, title: "Tuning v1", state: "superseded", since: "2026-10-02", replaced_by: [{ doc_id: 4, title: "Tuning v2" }] }]);
  assert.match(html, /rests on 1 document no longer current/);
  assert.match(html, /href="#doc\/3">Tuning v1<\/a>/);
  assert.match(html, /href="#doc\/4">Tuning v2<\/a>/);
  assert.equal(lib.staleSources([]), "");
});

test("nameDiff: how one name becomes the other, letter by letter", () => {
  const body = (a, b) => lib.nameDiff(a, b).replace(/<p[^>]*>|<\/p>/g, "");
  assert.equal(body("ValhallaDSP", "Valhalla DSP"), "Valhalla<ins>␣</ins>DSP");
  assert.equal(body("colour", "color"), "colo<del>u</del>r");
  assert.equal(body("Interactive tabletops", "interactive tabletop"), "<del>I</del><ins>i</ins>nteractive tabletop<del>s</del>");
  // a short run kept between two changes reads as part of them
  assert.equal(body("Fourier transform", "Fourier-Transformation"), "Fourier<del>␣t</del><ins>-T</ins>ransform<ins>ation</ins>");
  // little spelling in common, or none to show: nothing
  assert.equal(lib.nameDiff("feedback delay network", "FDN"), "");
  assert.equal(lib.nameDiff("same", "same"), "");
  assert.match(lib.nameDiff("<b>x", "<b>xy"), /&lt;b&gt;x<ins>y<\/ins>/);
});

test("hashUnit is stable and in [0, 1)", () => {
  assert.equal(lib.hashUnit("concept|fdn"), lib.hashUnit("concept|fdn"));
  assert.notEqual(lib.hashUnit("concept|fdn"), lib.hashUnit("concept|fdtd"));
  for (const s of ["", "a", "tool|SuperCollider"]) { const u = lib.hashUnit(s); assert.ok(u >= 0 && u < 1); }
});

test("strongLinks keeps each node's heaviest links", () => {
  const links = [
    { a: "x", b: "a", weight: 9 }, { a: "x", b: "b", weight: 8 }, { a: "x", b: "c", weight: 7 },
    { a: "x", b: "d", weight: 1 }, { a: "d", b: "e", weight: 1 },
  ];
  const strong = lib.strongLinks(links, 3);
  assert.deepEqual([...strong].sort(), [0, 1, 2, 3, 4]);  // x-d is d's heaviest
  // x-c is neither x's heaviest (x-a) nor c's (c-y)
  const one = lib.strongLinks([...links, { a: "c", b: "y", weight: 20 }], 1);
  assert.ok(one.has(0) && one.has(5) && !one.has(2));
});

test("placeLabels skips a label that would cover one placed, never a must", () => {
  const box = (key, x, y, must) => ({ key, x, y, w: 50, h: 14, must });
  const got = lib.placeLabels([box("sel", 0, 0, true), box("big", 10, 5), box("far", 200, 0), box("hit", 5, 5, true)]);
  assert.deepEqual([...got].sort(), ["far", "hit", "sel"]);
  assert.deepEqual([...lib.placeLabels([box("a", 0, 0), box("b", 100, 0)], 1)], ["a"]);
});

test("neighbourToward picks the nearest node the arrow points at", () => {
  const from = { x: 0, y: 0 };
  const cands = [{ key: "right", x: 100, y: 10 }, { key: "far-right", x: 300, y: 0 }, { key: "up", x: 0, y: -50 }];
  assert.equal(lib.neighbourToward(from, cands, 1, 0), "right");
  assert.equal(lib.neighbourToward(from, cands, 0, -1), "up");
  assert.equal(lib.neighbourToward(from, cands, -1, 0), null);
});

test("regionHue spreads neighbouring ids apart", () => {
  assert.ok(Math.abs(lib.regionHue(1) - lib.regionHue(2)) > 60);
  assert.ok(lib.regionHue(348) >= 0 && lib.regionHue(348) < 360);
});

test("fixLabel names a text check's fix as the reading it asks for", () => {
  assert.equal(lib.fixLabel(null), "");
  assert.equal(lib.fixLabel({ extractor: "marker", ocr: true }), "marker (OCR)");
  assert.equal(lib.fixLabel({ extractor: "latex-source", mode: "2501.13484" }), "latex-source (2501.13484)");
  assert.equal(lib.fixLabel({ extractor: "figure-crops" }), "figure-crops");
  assert.equal(lib.fixLabel("marker-ocr"), "marker-ocr");  // kept before version 5
});

test("moduleOptions draws the modules as a tree, with a module alone under it", () => {
  const counts = {
    craft: { requires: ["core"], own: 28, within: 121, holds: ["kitchen", "workshop"] },
    kitchen: { requires: ["craft"], own: 81, within: 81, holds: [] },
    workshop: { requires: ["craft", "studio"], own: 12, within: 12, holds: [] },
    studio: { requires: ["core"], own: 300, within: 312, holds: ["workshop"] },
    research: { requires: ["core"], own: 9000, within: 9000, holds: [] },
  };
  const html = lib.moduleOptions(["craft", "kitchen", "research", "studio", "workshop"], counts, "only:craft");
  const rows = [...html.matchAll(/value="([^"]+)"[^>]*>([^<]+)</g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(rows, [
    ["craft", "craft + kitchen, workshop (121)"],
    ["only:craft", "  craft only (28)"],
    ["kitchen", "  kitchen (81)"],
    ["workshop", "  workshop (12)"],
    ["research", "research (9000)"],
    ["studio", "studio + workshop (312)"],
    ["only:studio", "  studio only (300)"],
  ]);
  assert.match(html, /value="only:craft"[^>]* selected/);
  // without counts the tree and its names stay
  assert.match(lib.moduleOptions(["craft", "kitchen"], { kitchen: { requires: ["craft"] } }, ""), /craft \+ kitchen<\/option>/);
});

test("burstLabel names a burst's day and hours, the second day past midnight", () => {
  assert.equal(lib.burstLabel({ first: "2018-06-10T09:00:00", last: "2018-06-10T18:30:00" }), "2018-06-10 09:00 – 18:30");
  assert.equal(lib.burstLabel({ first: "2018-06-10T22:00:00", last: "2018-06-11T01:30:00" }), "2018-06-10 22:00 – 06-11 01:30");
});

test("a person's page reads relations from their side and links people to their pages", () => {
  assert.equal(lib.personRelation("affiliated_with", "out", "Ada"), "Ada affiliated with");
  assert.equal(lib.personRelation("authored_by", "in", "Ada"), "authored by Ada");
  assert.match(lib.personOther({ other: "Bram", other_type: "person", other_id: 7, person: true }), /href="#person\/7"/);
  assert.match(lib.personOther({ other: "Harbour", other_type: "organization", other_id: 9, person: false }), /#graph\?entity=Harbour&amp;type=organization|#graph\?entity=Harbour&type=organization/);
  const html = lib.personHtml({ name: "Ada", type: "person", labels: [], facts: 0, documents_total: 0, photos: 0, timeline: [], relations: [], documents: [], sample: [], events: [], places: [], with: [] });
  assert.match(html, /No photo depicts them yet/);
});

test("a picture's faces: the count, and each a box in percent", () => {
  assert.equal(lib.facesWords(null), "");
  assert.equal(lib.facesWords({ n: 0 }), "no face");
  assert.equal(lib.facesWords({ n: 2 }), "2 faces");
  const box = lib.faceBox({ id: 4, box: [0.1, 0.25, 0.05, 0.075], score: 0.93 });
  assert.match(box, /left:10\.00%;top:25\.00%;width:5\.00%;height:7\.50%/);
  assert.match(box, /face 4 · 93%/);
});

test("the faces tab's tiles and counts", () => {
  const tile = lib.faceTile({ id: 3, doc_id: 9, crop: true, score: 0.912, px: 48 });
  assert.match(tile, /src="\/face\/3\/crop"/);
  assert.match(tile, /91% · 48 px/);
  assert.match(tile, /data-alike="3"/);
  const near = lib.faceTile({ id: 4, doc_id: 9, crop: true }, { similarity: 0.52, match: true });
  assert.match(near, /52% alike/);
  assert.doesNotMatch(near, /data-alike/);
  assert.match(lib.faceTile({ id: 5, doc_id: 1, crop: false, score: 0.8, px: 10 }), /no crop yet/);
  const counts = lib.faceCounts({ total: 1200, pictures: 400, vectored: 30, by_score: { "under 0.9": 5 }, by_size: { "under 40 px": 7 } });
  assert.match(counts, /1,200 faces in 400 pictures, 30 with a vector/);
  assert.match(counts, /under 0\.9 5/);
});

test("a face group's strip: what is open, a picture twice, who it was named", () => {
  const faces = [{ id: 1, doc_id: 7, crop: true, score: 0.9, px: 60, verdict: null }];
  const row = lib.faceGroupRow({ id: 12, size: 40, open: 30, pictures: 39, doubled: [7], named: [{ entity_id: 5, name: "Ada Lovelace", faces: 10 }], faces });
  assert.match(row, /group 12 · 30 faces to name in 39 pictures/);
  assert.match(row, /1 picture with two of its faces: more than one person/);
  assert.match(row, /list=groups&group=12/);
  assert.match(row, /all 30 to name/);
  assert.match(row, /named so far: <a href="#person\/5">Ada Lovelace<\/a> 10/);
  assert.match(row, /value="Ada Lovelace #5"/);
  assert.match(row, /data-faces="1"/);
  assert.match(row, /data-face="1"><button type="button" class="face-pick"/);
  const whole = lib.faceGroupRow({ id: 3, size: 2, open: 1, pictures: 1, doubled: [], named: [], faces: [...faces, { ...faces[0], id: 2, verdict: "nobody" }] }, true);
  assert.match(whole, /1 face to name in 1 picture/);
  assert.match(whole, /data-faces="1"/);
  assert.doesNotMatch(whole, /two of its faces|to name<\/a>|data-face="2"/);
  const loose = lib.faceGroupRow({ id: null, size: 2, open: 2, pictures: 1, doubled: [7], named: [], faces }, true);
  assert.match(loose, /not in a group/);
  assert.doesNotMatch(loose, /two of its faces/);
});

test("the name field's choices: name, type, photos and facts", () => {
  const html = lib.personChoices([{ id: 9, name: "Grace <Hopper>", type: "author", photos: 1, facts: 3 }]);
  assert.match(html, /data-value="Grace &lt;Hopper&gt; #9"/);
  assert.match(html, /author · 1 photo · 3 facts/);
  assert.equal(lib.personChoices([]), "");
});

test("a row of the matching: the person in the field, the named face beside", () => {
  const row = { entity_id: 7, name: "Ada <L>", open: 30, pictures: 25,
    like: { id: 1, doc_id: 2, crop: true, score: 0.9, px: 90 },
    faces: [{ id: 5, doc_id: 6, crop: true, score: 0.9, px: 60, similarity: 0.512 }] };
  const html = lib.faceMatchRow(row, false);
  assert.match(html, /data-person="7"/);
  assert.match(html, /value="Ada &lt;L&gt; #7"/);
  assert.match(html, /not Ada &lt;L&gt;/);
  assert.match(html, /51% alike/);
  assert.match(html, /face-like/);
  assert.match(html, /all 30/);
  assert.doesNotMatch(lib.faceMatchRow(row, true), /all 30/);
});

test("the name field: a person picked, or a new name", () => {
  assert.equal(lib.personValue({ id: 9, name: "Grace Hopper" }), "Grace Hopper #9");
  assert.deepEqual(lib.personChoice("Grace Hopper #9"), { entity_id: 9 });
  assert.deepEqual(lib.personChoice("  Grace  "), { name: "Grace" });
  assert.equal(lib.personChoice(" "), null);
});

test("a picture's faces listed under it: answered, offered, open", () => {
  const box = { box: [0.1, 0.1, 0.2, 0.2], score: 0.9, px: 60, crop: true };
  const named = lib.pictureFaceRow({ id: 1, ...box, answer: { verdict: "person", entity_id: 7, name: "Ada", run: "r" } }, 1);
  assert.match(named, /href="#person\/7">Ada</);
  assert.match(named, /data-unname="1"/);
  assert.doesNotMatch(named, /<form/);
  const offered = lib.pictureFaceRow({ id: 2, ...box, answer: null, offer: { entity_id: 7, name: "Ada", similarity: 0.61 } }, 2);
  assert.match(offered, /probably <a href="#person\/7">Ada<\/a>, 61% alike/);
  assert.match(offered, /data-offer="7"/);
  assert.match(offered, /value="Ada #7"/);
  const open = lib.pictureFaceRow({ id: 3, ...box, crop: false, answer: null, offer: null }, 3);
  assert.match(open, /no crop yet/);
  assert.match(open, /value=""/);
  assert.match(lib.pictureFaceRow({ id: 4, ...box, answer: { verdict: "not_face" } }, 4), /not a face/);
  const drawn = lib.faceBox({ id: 1, ...box, answer: { verdict: "person", name: "Ada" } }, 1);
  assert.match(drawn, /face-said/);
  assert.match(drawn, /title="Ada · face 1/);
  assert.match(drawn, /<span class="face-num">1<\/span>/);
});

test("the person page's rename and same-person forms", () => {
  const html = lib.personEditHtml({ id: 7, name: "Ada <L>" });
  assert.match(html, /data-edit="rename"/);
  assert.match(html, /data-edit="same"/);
  assert.match(html, /value="Ada &lt;L&gt;"/);
  assert.match(html, /form class="person-same search-form" hidden/);
  assert.match(html, /Ada &lt;L&gt; stays the name/);
});

test("a pair of versions: both sides, the headings one side has, the suggested answer first", () => {
  const side = (id, heads, extra) => ({ doc_id: id, title: "Ranking <things>", chars: 12000, headings: heads, added: "2026-01-02T00:00:00Z", ...extra });
  const html = lib.versionRow({ id: 7, overlap: 0.42, verdict: null,
    reasons: { by: "title", authors: ["lovelace"], suggest: "version" },
    a: side(3, ["Introduction", "Method"], { published: "2019" }),
    b: side(9, ["Introduction", "Method", "Appendix"]) });
  assert.match(html, /42% of their text alike · one title · authors lovelace/);
  assert.match(html, /Ranking &lt;things&gt;/);
  assert.match(html, /published 2019/);
  assert.match(html, /added 2026-01-02/);
  assert.match(html, /<mark>Appendix<\/mark>/);
  assert.doesNotMatch(html, /<mark>Method/);
  assert.match(html, /<button type="button" data-verdict="version">versions of one work/);
  const copy = lib.versionRow({ id: 8, overlap: 0.95, verdict: null, reasons: { suggest: "copy" }, a: side(1, []), b: side(2, []) });
  assert.match(copy, /<button type="button" data-verdict="copy">one text twice/);
  const done = lib.versionRow({ id: 9, overlap: 0.5, verdict: "different", reasons: {}, a: side(1, []), b: side(2, []) });
  assert.match(done, /<b>different<\/b>/);
  assert.match(done, /data-version-undo/);
});

test("which version of a work a document is, and the versions a hit folds", () => {
  const v = { work: { id: 1, name: "Ranking <things>" }, latest: 9,
    versions: [{ doc_id: 3, published: "2019" }, { doc_id: 9, published: "2021" }] };
  assert.match(lib.versionsLine(v, 3), /version 1 of 2 of <i>Ranking &lt;things&gt;<\/i>: <a href="#doc\/9">2021<\/a>/);
  assert.match(lib.versionsLine(v, 9), /the latest of 2/);
  assert.equal(lib.versionsLine(null, 3), "");
  assert.match(lib.foldedVersions([3]), /1 older version: <a href="#doc\/3">#3<\/a>/);
  assert.equal(lib.foldedVersions([]), "");
  assert.match(lib.staleNote({ state: "superseded", by: "versions", replaced_by: [{ doc_id: 9, title: "t" }] }), /a later version of its work/);
});

test("pageWindow names the ends and two either side, with gaps", () => {
  assert.deepEqual(lib.pageWindow(1, 1), [1]);
  assert.deepEqual(lib.pageWindow(1, 4), [1, 2, 3, 4]);
  assert.deepEqual(lib.pageWindow(6, 30), [1, null, 4, 5, 6, 7, 8, null, 30]);
  assert.deepEqual(lib.pageWindow(30, 30), [1, null, 28, 29, 30]);
  assert.deepEqual(lib.pageWindow(4, 30), [1, 2, 3, 4, 5, 6, null, 30]);
});

test("pagerHtml links pages by offset and says where it is", () => {
  const href = (o) => `#person/12?photos=${o / 20 + 1}&x=1`;
  assert.equal(lib.pagerHtml(20, 0, 20, href), "");  // one page needs none
  const html = lib.pagerHtml(233, 40, 20, href);
  assert.match(html, /<b aria-current="page">3<\/b>/);
  assert.match(html, /data-offset="20" class="step">‹/);
  assert.match(html, /data-offset="60" class="step">›/);
  assert.match(html, /data-offset="220">12</);
  assert.match(html, /41–60 of 233/);
  assert.match(html, /&amp;x=1/);  // the link is escaped
  assert.doesNotMatch(lib.pagerHtml(233, 220, 20, href), /class="step">›/);
});

test("galleryHtml draws photos and figures as tiles placed in their set", () => {
  const photos = lib.galleryHtml({ total: 45, offset: 20, limit: 20, items: [
    { doc_id: 7, taken: "2019-05-03T10:00:00", title: "IMG_1.JPG" }] }, (o) => `#p?o=${o}`);
  assert.match(photos, /href="#doc\/7" data-i="20"/);
  assert.match(photos, /src="\/doc\/7\/thumb"/);
  assert.match(photos, /<span class="muted where">2019-05-03<\/span>/);
  assert.match(photos, /class="pager pager-pages"/);
  const figs = lib.galleryHtml({ total: 1, offset: 0, limit: 20, items: [
    { doc_id: 3, ref: "ab12", caption: "Fig. 2 <b>", page: 4, chunk_id: 9, href: "#chunk-9" }] }, () => "#", true);
  assert.match(figs, /gallery-grid figures/);
  assert.match(figs, /src="\/doc\/3\/figure\/ab12"/);
  assert.match(figs, /Fig\. 2 &lt;b&gt;/);
  assert.match(figs, /p\. 4/);
  assert.doesNotMatch(figs, /pager/);
});

test("the viewer steps within the set and shows the large picture", () => {
  assert.equal(lib.viewerStep(0, -1, 5), null);
  assert.equal(lib.viewerStep(19, 1, 45), 20);  // across a page's end
  assert.equal(lib.viewerStep(44, 1, 45), null);
  const v = lib.viewerHtml({ doc_id: 7, taken: "2019-05-03T10:00:00", title: "IMG_1.JPG" }, 0, 45);
  assert.match(v, /src="\/doc\/7\/preview"/);
  assert.match(v, /data-viewer="prev" aria-label="previous" disabled/);
  assert.match(v, /1 of 45/);
  assert.match(v, /open the document/);
  const f = lib.viewerHtml({ doc_id: 3, ref: "ab12", caption: "Fig. 2", href: "#chunk-9" }, 44, 45);
  assert.match(f, /src="\/doc\/3\/figure\/ab12"/);
  assert.match(f, /href="#chunk-9" data-viewer="open">its place in the text/);
  assert.match(f, /data-viewer="next" aria-label="next" disabled/);
});

test("pictureSetOf reads the one set a #photos address names", () => {
  assert.deepEqual(lib.pictureSetOf({ place: "12", page: "2" }), { key: "place", id: 12 });
  assert.equal(lib.pictureSetOf({ place: "x" }), null);
  assert.equal(lib.pictureSetOf({}), null);
});
