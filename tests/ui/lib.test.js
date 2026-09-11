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
