// The graph view: a neighbourhood drawn on a canvas, its legend and
// the edge a click explains.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// ----------------------------------------------------------------- graph
// The neighbourhood of an entity on a canvas. Nodes are (type, name);
// edges come from GET /traverse (hops=1) and a click on a node fetches its
// own neighbourhood and merges it in, forty neighbours at a time. The
// layout is a small spring model; no library (the previous SVG version
// rebuilt the whole picture on every change and froze on a hub with five
// hundred neighbours).

const TYPE_COLORS = {
  paper: "#2f5d8a", author: "#7a5c1e", concept: "#4b7a45", method: "#6a4b7a",
  claim: "#a0522d", tool: "#3b7a7a", venue: "#8a6d2f", dataset: "#5a5a8a",
  person: "#7a5c1e", organization: "#8a4b2f", document: "#2f5d8a", place: "#3f6b3f",
  event: "#7a3f6b", work: "#5a3f8a", page: "#2f7a8a", project: "#2f8a5a",
  material: "#6b8a2f", technique: "#8a2f5d", spec: "#2f4b8a",
};
const nodeKey = (name, type) => type + "|" + name;
// a type without a colour of its own takes its nearest ancestor's: the
// kitchen's ingredient is a material, its dish a work
function typeColor(t) {
  for (let i = 0; t && i < 8; i++, t = TYPE_PARENTS[t]) if (TYPE_COLORS[t]) return TYPE_COLORS[t];
  return "#888";
}
// a person or a type under it (an author) has a page of their own
function isPersonType(t) {
  for (let i = 0; t && i < 8; i++, t = TYPE_PARENTS[t]) if (t === "person") return true;
  return false;
}
const regionColor = (id) => `hsl(${regionHue(id)} 40% 44%)`;

// Where each thing was last drawn, by key, for the life of the page: a
// graph drawn again (a filter changed, a date moved, the way back) puts
// what it drew before where it was, so a view changes in place instead of
// being laid out anew.
const LAYOUT = new Map();
const LAYOUT_KEPT = 5000;

// The labels of the regions, for the legend of a graph coloured by them.
let REGION_LABELS = null;
async function regionLabels() {
  if (!REGION_LABELS) {
    REGION_LABELS = new Map();
    try {
      for (const c of (await api("/communities", { level: 0, limit: 500 })).communities) REGION_LABELS.set(c.id, regionName(c));
    } catch (_) { /* an older door: ids alone */ }
  }
  return REGION_LABELS;
}

class ForceGraph {
  // A canvas, not an SVG: five hundred nodes and their labels redraw in a
  // millisecond or two. The first data is laid out before it is shown (a
  // few hundred steps in one go, from where the layout memory or a hash of
  // the name puts each node), so a graph appears where it stays instead of
  // flying in from a corner; what an expansion adds settles a few steps
  // per frame while the view eases to take it in.
  constructor(canvas, onSelect) {
    if (ForceGraph.live) ForceGraph.live.stop();
    ForceGraph.live = this;  // the one on screen, for a theme change
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.onSelect = onSelect;
    this.onPath = null;  // called with connect's answer when a path is asked
    this.nodes = new Map();
    this.edges = new Map();
    this.links = [];  // co-occurrence links: {a, b, weight, n?, doc?, by?}
    this.strong = new Set();  // indexes of the links drawn at a glance
    this.selected = null;
    this.hover = null;
    this.find = "";  // the words typed into "find on the map"
    this.path = null;  // {edges: Set, nodes: Set} of the path last asked
    this.connectFrom = null;  // a node waiting for the other end of a path
    this.colourBy = "type";
    this.sides = null;  // two scopes compared: their names, nodes carry `side`
    this.alpha = 0;
    this.frame = null;
    this.goal = null;  // the view the drawing eases to
    this.follow = false;  // keep easing to fit while an expansion settles
    this.cap = 40;  // neighbours drawn per expansion; the rest wait in the panel
    this.held = false;  // the person panned or zoomed: no fitting behind their back
    this.resize();
    this.view = { x: this.w / 2, y: this.h / 2, k: 1 };  // screen = world * k + (x, y)
    this.bind();
    this.observer = new ResizeObserver(() => {
      const w = this.w, h = this.h;
      this.resize();
      if (this.held) { this.view.x += (this.w - w) / 2; this.view.y += (this.h - h) / 2; this.draw(); } else this.fit();
    });
    this.observer.observe(canvas);
  }

  stop() {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.observer.disconnect();
    this.remember();
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth || 900, h = this.canvas.clientHeight || 600;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = w; this.h = h;
  }

  remember() {
    for (const n of this.nodes.values()) { LAYOUT.delete(n.key); LAYOUT.set(n.key, [n.x, n.y]); }
    while (LAYOUT.size > LAYOUT_KEPT) LAYOUT.delete(LAYOUT.keys().next().value);
  }

  // A node, new or the one drawn already. A new one goes where the layout
  // memory has it, else near the node it was reached from, else on a ring
  // around the middle, at an angle its name gives.
  node(name, type, near, key) {
    key = key || nodeKey(name, type);
    let n = this.nodes.get(key);
    if (!n) {
      const kept = LAYOUT.get(key);
      const a = hashUnit(key) * Math.PI * 2;
      const r = near ? 70 + hashUnit(key + "r") * 50 : 60 + Math.sqrt(this.nodes.size + 1) * 40;
      n = { key, name, type, expanded: false, degree: 0, hidden: [], by: near ? near.key : null,
            x: kept ? kept[0] : (near ? near.x : 0) + Math.cos(a) * r,
            y: kept ? kept[1] : (near ? near.y : 0) + Math.sin(a) * r, vx: 0, vy: 0, known: !!kept };
      this.nodes.set(key, n);
    }
    return n;
  }

  // Merge edges in. Around a node being expanded, only its `cap` best
  // connected neighbours are drawn; the rest stay on the node as `hidden`
  // until asked for, because a hub with five hundred neighbours is a
  // hairball at any speed. `side` marks what one of two compared scopes
  // brought (1 or 2).
  merge(edgeList, aroundKey, nodeList, linkList, side) {
    const first = !this.placed;
    const near = aroundKey ? this.nodes.get(aroundKey) : null;
    const touched = new Set();
    for (const m of nodeList || []) {
      const n = this.node(m.name, m.type, null, m.key);
      n.degree = Math.max(n.degree, m.degree || 0);
      for (const f of ["docs", "region", "kind", "rid", "size", "members"]) if (m[f] !== undefined) n[f] = m[f];
      touched.add(n.key);
    }
    const linked = new Map(this.links.map((l, i) => [l.a < l.b ? l.a + "\n" + l.b : l.b + "\n" + l.a, i]));
    for (const l of linkList || []) {
      const a = l.akey || this.node(l.a, l.a_type, null).key, b = l.bkey || this.node(l.b, l.b_type, null).key;
      const pair = a < b ? a + "\n" + b : b + "\n" + a;
      if (linked.has(pair)) { const o = this.links[linked.get(pair)]; o.weight = Math.max(o.weight, l.weight); continue; }
      linked.set(pair, this.links.length);
      this.links.push({ a, b, weight: l.weight, n: l.n });
      touched.add(a); touched.add(b);
    }
    let fresh = edgeList.filter((e) => !this.edges.has(e.edge_id));
    for (const e of edgeList) if (this.edges.has(e.edge_id)) { touched.add(this.edges.get(e.edge_id).s); touched.add(this.edges.get(e.edge_id).t); }
    if (near && fresh.length > this.cap) {
      const other = (e) => nodeKey(e.src, e.src_type) === aroundKey ? nodeKey(e.dst, e.dst_type) : nodeKey(e.src, e.src_type);
      const degree = new Map();
      for (const e of fresh) degree.set(other(e), (degree.get(other(e)) || 0) + 1);
      const known = new Set(this.nodes.keys());
      // neighbours already on the map first, then the best connected
      fresh.sort((p, q) => (known.has(other(q)) - known.has(other(p))) || ((degree.get(other(q)) || 0) - (degree.get(other(p)) || 0)));
      const kept = new Set();
      const shown = [];
      for (const e of fresh) {
        const k = other(e);
        if (kept.size >= this.cap && !kept.has(k) && !known.has(k)) { near.hidden.push(e); continue; }
        kept.add(k); shown.push(e);
      }
      fresh = shown;
    }
    for (const e of fresh) {
      const s = this.node(e.src, e.src_type, near);
      const t = this.node(e.dst, e.dst_type, near);
      s.degree++; t.degree++;
      touched.add(s.key); touched.add(t.key);
      this.edges.set(e.edge_id, { ...e, s: s.key, t: t.key, by: aroundKey });
    }
    if (side) for (const k of touched) { const n = this.nodes.get(k); if (n) n.side = (n.side || 0) | side; }
    this.strong = strongLinks(this.links, 3);
    if (this.batching) return;  // two answers merged, laid out once (finish)
    if (first) this.place();
    else {
      this.follow = !this.held;
      this.kick(near ? 0.6 : 0.8);
    }
    this.mark();
  }

  // The first data, laid out before it is seen; what the layout memory
  // placed barely moves.
  place() {
    this.placed = true;
    const nodes = [...this.nodes.values()];
    const known = nodes.filter((n) => n.known).length / Math.max(1, nodes.length);
    this.settle(known > 0.8 ? 0.15 : known > 0.4 ? 0.5 : 1);
    if (!this.held) this.view = this.fitView() || this.view;
    this.draw();
    this.remember();
  }

  finish() {
    this.batching = false;
    if (!this.placed) this.place(); else this.kick(0.8);
    this.mark();
  }

  // Run the layout to rest in one go, bounded in time.
  settle(alpha) {
    const t0 = performance.now();
    this.alpha = alpha;
    while (this.alpha > 0.02 && performance.now() - t0 < 200) { this.step(); this.alpha *= 0.985; }
    this.alpha = 0;
  }

  // Ask the door how many documents say something of each new thing and
  // which region it is in, so an expansion is sized and coloured like the
  // hubs it joins (POST /graph/marks).
  mark() {
    clearTimeout(this.marking);
    this.marking = setTimeout(async () => {
      const want = [...this.nodes.values()].filter((n) => n.docs === undefined && !n.kind && !n.asked);
      if (!want.length) return;
      for (const n of want) n.asked = true;
      try {
        const got = await send("POST", "/graph/marks", { things: want.slice(0, 200).map((n) => [n.name, n.type]), ...scopeIds() });
        for (const m of got.marks) {
          const n = this.nodes.get(nodeKey(m.name, m.type));
          if (n) { n.docs = m.docs; if (m.region !== undefined) n.region = m.region; }
        }
        this.draw();
        if (this.onMarks) this.onMarks();
      } catch (_) { /* an older door: sized by edges */ }
    }, 150);
  }

  showHidden(key) {
    const n = this.nodes.get(key);
    if (!n || !n.hidden.length) return;
    const rest = n.hidden; n.hidden = [];
    const cap = this.cap; this.cap = Infinity;
    this.merge(rest, key);
    this.cap = cap;
    this.select(key);
  }

  async expand(key) {
    const n = this.nodes.get(key);
    if (!n || n.kind) return;
    n.expanded = true;
    // the node is one thing: its type says which, where its name is several
    const edges = (await api("/traverse", { entity: n.name, hops: 1, limit: 0, type: n.type || "", ...scopeParams() })).edges;
    if (!n.expanded) return;  // folded while the fetch was in flight
    this.merge(edges, key);
    this.select(key);
  }

  // Undo an expansion: its edges go, and so does everything only they
  // justified, with whatever those nodes had opened in turn. Seeds (the
  // overview's hubs, the entity the view started on) always stay.
  fold(key) {
    const n = this.nodes.get(key);
    if (!n) return;
    this.hideDocs(key);
    this.unfold(key);
    this.prune(key);
    this.kick(0.4);
    this.select(key);
  }

  prune(keep) {
    const kept = new Set(this.links.flatMap((l) => [l.a, l.b]));
    for (const e of this.edges.values()) { kept.add(e.s); kept.add(e.t); }
    for (const m of [...this.nodes.values()]) {
      if (m.by !== null && m.key !== keep && !kept.has(m.key)) this.nodes.delete(m.key);
    }
    if (this.hover && !this.nodes.has(this.hover)) this.hover = null;
    this.strong = strongLinks(this.links, 3);
  }

  unfold(key) {
    const n = this.nodes.get(key);
    n.expanded = false;
    n.hidden = [];
    for (const [id, e] of [...this.edges]) {
      if (e.by !== key) continue;
      this.edges.delete(id);
      for (const k of [e.s, e.t]) { const m = this.nodes.get(k); if (m) m.degree = Math.max(0, m.degree - 1); }
    }
    for (const m of this.nodes.values()) {
      if (m.by === key && m.key !== key && m.expanded) this.unfold(m.key);
    }
  }

  // The documents behind a node's drawn facts, hung off it as squares,
  // the ones saying most first (GET /graph/documents names them).
  async showDocs(key) {
    const n = this.nodes.get(key);
    if (!n) return;
    const count = new Map();
    for (const e of this.edgesOf(key)) if (e.source_doc) count.set(e.source_doc, (count.get(e.source_doc) || 0) + 1);
    const ids = [...count.keys()].sort((a, b) => count.get(b) - count.get(a)).slice(0, 12);
    if (!ids.length) return;
    let titles = {};
    try { titles = (await api("/graph/documents", { ids: ids.join(",") })).titles; } catch (_) { /* ids alone */ }
    n.docsShown = true;
    for (const id of ids) {
      if (!(id in titles) && Object.keys(titles).length) continue;  // hidden from this viewer
      const d = this.node(titles[id] || `doc ${id}`, "document", n, `doc|${id}`);
      d.kind = "doc"; d.doc = id; d.docs = 0;
      this.links.push({ a: key, b: d.key, weight: count.get(id), doc: true, by: key });
    }
    this.strong = strongLinks(this.links, 3);
    this.follow = !this.held;
    this.kick(0.5);
    this.select(key);
  }

  hideDocs(key) {
    const n = this.nodes.get(key);
    if (!n || !n.docsShown) return;
    n.docsShown = false;
    this.links = this.links.filter((l) => !(l.doc && l.by === key));
    const still = new Set(this.links.filter((l) => l.doc).map((l) => l.b));
    for (const m of [...this.nodes.values()]) if (m.kind === "doc" && !still.has(m.key)) this.nodes.delete(m.key);
    this.strong = strongLinks(this.links, 3);
    this.kick(0.2);
    this.select(key);
  }

  // How the two are connected (GET /graph/connect): the best sound paths,
  // drawn in and marked; the answer goes to the panel.
  async connect(fromKey, toKey) {
    const a = this.nodes.get(fromKey), b = this.nodes.get(toKey);
    this.connectFrom = null;
    if (!a || !b || a.kind || b.kind) return;
    const got = await api("/graph/connect", { a: a.name, b: b.name, type_a: a.type, type_b: b.type, world_at: GRAPH_SCOPE.world_at || undefined });
    const edges = [];
    for (const p of got.paths || []) {
      for (const h of p.hops) {
        edges.push({ edge_id: h.edge_id, src: h.src, src_type: h.src_type, rel: h.rel, dst: h.dst, dst_type: h.dst_type,
          confidence: h.confidence, source_doc: h.source_doc, evidence: h.evidence });
      }
    }
    if (edges.length) {
      this.merge(edges, null);
      const on = new Set(edges.map((e) => e.edge_id));
      this.path = { edges: on, nodes: new Set(edges.flatMap((e) => [nodeKey(e.src, e.src_type), nodeKey(e.dst, e.dst_type)])) };
    } else this.path = null;
    this.draw();
    if (this.onPath) this.onPath(got, a, b);
  }

  clearPath() { this.path = null; this.connectFrom = null; this.draw(); }

  kick(alpha) {
    this.alpha = Math.max(this.alpha, alpha);
    this.run();
  }

  run() { if (!this.frame) this.frame = requestAnimationFrame(() => this.tick()); }

  tick() {
    this.frame = null;
    if (this.alpha > 0) {
      for (let i = 0; i < 3; i++) this.step();
      this.alpha *= 0.96;
      if (this.alpha < 0.015) { this.alpha = 0; this.follow = false; this.remember(); }
    }
    if (this.follow && !this.held) this.goal = this.fitView();
    if (this.goal) {
      const v = this.view, g = this.goal, t = 0.18;
      v.k += (g.k - v.k) * t; v.x += (g.x - v.x) * t; v.y += (g.y - v.y) * t;
      if (Math.abs(g.k - v.k) < 0.002 * g.k && Math.abs(g.x - v.x) < 0.5 && Math.abs(g.y - v.y) < 0.5) { this.view = { ...g }; this.goal = null; }
    }
    this.draw();
    if (this.alpha > 0 || this.goal) this.run();
  }

  step() {
    const nodes = [...this.nodes.values()];
    const alpha = this.alpha;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = hashUnit(a.key + b.key) - .5; dy = hashUnit(b.key + a.key) - .5; d2 = 1; }
        const f = Math.min(6000 / d2, 40) * alpha;
        const d = Math.sqrt(d2);
        a.vx -= dx / d * f; a.vy -= dy / d * f;
        b.vx += dx / d * f; b.vy += dy / d * f;
      }
    }
    for (const e of this.edges.values()) {
      const a = this.nodes.get(e.s), b = this.nodes.get(e.t);
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - 110) * 0.04 * alpha;
      a.vx += dx / d * f; a.vy += dy / d * f;
      b.vx -= dx / d * f; b.vy -= dy / d * f;
    }
    for (const l of this.links) {
      const a = this.nodes.get(l.a), b = this.nodes.get(l.b);
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = l.doc ? 70 : a.kind === "region" ? 90 + this.radius(a) + this.radius(b) : 150;
      const f = (d - rest) * Math.min(0.03, 0.004 * l.weight) * alpha;
      a.vx += dx / d * f; a.vy += dy / d * f;
      b.vx -= dx / d * f; b.vy -= dy / d * f;
    }
    for (const n of nodes) {
      if (n.pinned) { n.vx = 0; n.vy = 0; continue; }
      n.x += n.vx; n.y += n.vy; n.vx *= 0.5; n.vy *= 0.5;
    }
  }

  fitView() {
    const nodes = [...this.nodes.values()];
    if (!nodes.length) return null;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const pad = 70;
    const k = Math.min(3, Math.max(0.05, Math.min(this.w / (x1 - x0 + 2 * pad), this.h / (y1 - y0 + 2 * pad))));
    return { k, x: this.w / 2 - (x0 + x1) / 2 * k, y: this.h / 2 - (y0 + y1) / 2 * k };
  }

  // the fit button and a resize: ease to everything, and stop holding
  fit(eased) {
    const v = this.fitView();
    if (!v) return;
    this.held = false;
    if (eased) { this.goal = v; this.run(); } else { this.view = v; this.goal = null; this.draw(); }
  }

  // keep a node chosen by the keyboard or the finder in sight
  reveal(key) {
    const n = this.nodes.get(key);
    if (!n) return;
    const [x, y] = this.toScreen(n);
    if (x > 40 && x < this.w - 40 && y > 40 && y < this.h - 40) return;
    const v = this.goal || this.view;
    this.goal = { k: v.k, x: this.w / 2 - n.x * v.k, y: this.h / 2 - n.y * v.k };
    this.held = true;
    this.run();
  }

  toScreen(n) { return [n.x * this.view.k + this.view.x, n.y * this.view.k + this.view.y]; }
  // a node is as big as the number of documents that say something of it
  // (its edges, until the door has said), a region as its members
  radius(n) {
    if (n.kind === "region") return 8 + Math.min(34, Math.sqrt(n.size || 1) * 0.7);
    if (n.kind === "doc") return 5;
    const w = n.docs !== undefined ? n.docs : n.degree;
    return 4 + Math.min(16, Math.sqrt(w) * 1.9);
  }
  screenRadius(n) { return this.radius(n) * Math.min(1.4, Math.max(0.6, this.view.k)); }
  colour(n) {
    if (n.kind === "region") return regionColor(n.rid);
    if (this.colourBy === "region" && n.region !== undefined) return regionColor(n.region);
    if (this.colourBy === "region" && !n.kind) return "#9a9a9a";
    return typeColor(n.type);
  }
  matches(n) {
    if (!this.find) return false;
    return n.name.toLowerCase().includes(this.find);
  }

  draw() {
    const ctx = this.ctx, v = this.view;
    const css = getComputedStyle(document.documentElement);
    const col = (name, fallback) => (css.getPropertyValue(name) || fallback).trim();
    const muted = col("--muted", "#777"), line = col("--line", "#ddd"), fg = col("--fg", "#222"),
          accent = col("--accent", "#c33"), panel = col("--panel", "#fff");
    ctx.clearRect(0, 0, this.w, this.h);
    const sel = this.selected, hov = this.hover;
    const near = new Set();  // the selected node's neighbours, labelled first
    if (sel) {
      for (const e of this.edges.values()) { if (e.s === sel) near.add(e.t); if (e.t === sel) near.add(e.s); }
      for (const l of this.links) { if (l.a === sel) near.add(l.b); if (l.b === sel) near.add(l.a); }
    }
    // the finder and a path light some nodes; the rest step back
    const lit = (n) => (this.find ? this.matches(n) : true) && (this.path ? this.path.nodes.has(n.key) : true);
    const dimming = !!(this.find || this.path);
    // shared-document links, faintest: each node's three heaviest at a
    // glance, the rest when zoomed in or at the node in focus
    ctx.lineCap = "round";
    const all = this.links.length <= 60 || v.k >= 1.6;
    this.links.forEach((l, i) => {
      const a = this.nodes.get(l.a), b = this.nodes.get(l.b);
      if (!a || !b) return;
      const focus = l.a === sel || l.b === sel || l.a === hov || l.b === hov;
      if (!all && !focus && !this.strong.has(i)) return;
      const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
      ctx.strokeStyle = focus ? muted : line;
      ctx.globalAlpha = dimming && !(lit(a) && lit(b)) ? 0.25 : focus ? 0.7 : 0.9;
      ctx.lineWidth = l.doc ? 1 : Math.min(6, 0.6 + l.weight * 0.5);
      ctx.setLineDash(l.doc ? [2, 3] : []);
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    });
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    // edges; the selected node's and a path's on top, labelled
    const onTop = [];
    for (const e of this.edges.values()) {
      // a path, when one is shown, is the only thing drawn on top
      if (this.path ? this.path.edges.has(e.edge_id) : e.s === sel || e.t === sel) { onTop.push(e); continue; }
      const dim = dimming && !(lit(this.nodes.get(e.s)) && lit(this.nodes.get(e.t)));
      this.drawEdge(ctx, e, muted, 1, dim ? 0.15 : 0.55);
    }
    for (const e of onTop) this.drawEdge(ctx, e, accent, this.path && this.path.edges.has(e.edge_id) ? 3 : 2, 1);
    // nodes
    const nodes = [...this.nodes.values()];
    for (const n of nodes) {
      const [x, y] = this.toScreen(n);
      const r = this.screenRadius(n);
      ctx.globalAlpha = dimming && !lit(n) && n.key !== sel ? 0.25 : 1;
      ctx.beginPath();
      if (n.kind === "doc") ctx.rect(x - r, y - r, 2 * r, 2 * r); else ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = this.colour(n); ctx.fill();
      ctx.lineWidth = n.key === sel ? 3 : 1.5;
      ctx.strokeStyle = n.key === sel || n.key === this.connectFrom ? accent : (n.expanded ? fg : panel);
      ctx.stroke();
      if (this.sides && n.side && n.side !== 3) {
        // compared: a ring for what only the first scope has, a dashed
        // one for what only the second has
        ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2);
        ctx.lineWidth = 2.5; ctx.strokeStyle = n.side === 1 ? accent : fg;
        ctx.setLineDash(n.side === 1 ? [] : [3, 3]); ctx.stroke(); ctx.setLineDash([]);
      }
    }
    ctx.globalAlpha = 1;
    // labels: the ones that matter always, the rest by weight where they
    // fit without covering another
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    const weight = (n) => n.kind === "region" ? 1e6 + (n.size || 0) : (n.docs !== undefined ? n.docs : n.degree);
    const order = nodes.slice().sort((p, q) => {
      const rank = (n) => n.key === sel || n.key === hov ? 0 : this.matches(n) || (this.path && this.path.nodes.has(n.key)) ? 1 : near.has(n.key) ? 2 : 3;
      return rank(p) - rank(q) || weight(q) - weight(p);
    });
    const text = (n) => n.name.length > 38 ? n.name.slice(0, 36) + "…" : n.name;
    const cands = order.map((n) => {
      const [x, y] = this.toScreen(n);
      const r = this.screenRadius(n);
      return { key: n.key, x: x + r + 3, y: y - 7, w: ctx.measureText(text(n)).width, h: 14,
        must: n.key === sel || n.key === hov || this.matches(n) || !!(this.path && this.path.nodes.has(n.key)) };
    });
    const shown = placeLabels(cands, nodes.length > 140 ? 60 : Infinity);
    for (const c of cands) {
      if (!shown.has(c.key)) continue;
      const n = this.nodes.get(c.key);
      ctx.globalAlpha = dimming && !lit(n) && n.key !== sel ? 0.35 : 1;
      ctx.fillStyle = fg;
      ctx.fillText(text(n), c.x, c.y + 7);
    }
    ctx.globalAlpha = 1;
    for (const e of onTop) {
      const a = this.nodes.get(e.s), b = this.nodes.get(e.t);
      const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
      ctx.fillStyle = muted; ctx.font = "9px system-ui, sans-serif"; ctx.textAlign = "center";
      ctx.fillText(e.rel, (ax + bx) / 2, (ay + by) / 2 - 5);
      ctx.textAlign = "start"; ctx.font = "11px system-ui, sans-serif";
    }
  }

  drawEdge(ctx, e, colour, width, alpha) {
    const a = this.nodes.get(e.s), b = this.nodes.get(e.t);
    const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
    ctx.strokeStyle = colour; ctx.lineWidth = width; ctx.globalAlpha = alpha;
    ctx.setLineDash(e.confidence === "INFERRED" ? [4, 3] : e.confidence === "AMBIGUOUS" ? [1, 3] : []);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
  }

  hit(px, py) {
    let best = null, bestD = Infinity;
    for (const n of this.nodes.values()) {
      const [x, y] = this.toScreen(n);
      const r = this.screenRadius(n) + 4;
      const d = Math.hypot(px - x, py - y);
      if (d <= r && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  edgeHit(px, py) {
    let best = null, bestD = 5;
    for (const e of this.edges.values()) {
      const a = this.nodes.get(e.s), b = this.nodes.get(e.t);
      const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
      const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
      const d = Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
      if (d < bestD) { best = e; bestD = d; }
    }
    return best;
  }

  zoomAt(px, py, z) {
    const k = Math.min(6, Math.max(0.03, this.view.k * z));
    this.view = { k, x: px - (px - this.view.x) * (k / this.view.k), y: py - (py - this.view.y) * (k / this.view.k) };
    this.goal = null; this.follow = false;
    this.held = true;
    this.draw();
  }

  // Pointer events, so a finger works like the mouse: one drags a node or
  // pans, two pinch to zoom. The pointer is captured by the canvas, so no
  // listener outlives the view.
  bind() {
    const c = this.canvas;
    const at = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const down = new Map();  // pointerId -> [x, y]
    let drag = null;  // {node} or {pan: [x, y, viewX, viewY]} or {pinch: [dist, k, mx, my]}
    let moved = false;
    let lastTap = [0, null];
    c.addEventListener("pointerdown", (e) => {
      c.setPointerCapture(e.pointerId);
      const [px, py] = at(e);
      down.set(e.pointerId, [px, py]);
      if (down.size === 2) {
        const [[ax, ay], [bx, by]] = [...down.values()];
        if (drag && drag.node) drag.node.pinned = false;
        drag = { pinch: [Math.hypot(bx - ax, by - ay), this.view.k] };
        moved = true;
        return;
      }
      const n = this.hit(px, py);
      moved = false;
      if (n) { drag = { node: n, from: [px, py] }; }
      else drag = { pan: [px, py, this.view.x, this.view.y] };
      c.classList.add("dragging");
    });
    c.addEventListener("pointermove", (e) => {
      const [px, py] = at(e);
      if (down.has(e.pointerId)) down.set(e.pointerId, [px, py]);
      if (drag && drag.pinch && down.size === 2) {
        const [[ax, ay], [bx, by]] = [...down.values()];
        const d = Math.hypot(bx - ax, by - ay);
        const k = drag.pinch[1] * d / (drag.pinch[0] || 1);
        this.zoomAt((ax + bx) / 2, (ay + by) / 2, k / this.view.k);
      } else if (drag && drag.node) {
        if (!moved && Math.hypot(px - drag.from[0], py - drag.from[1]) < 4) return;
        moved = true;
        drag.node.pinned = true;
        drag.node.x = (px - this.view.x) / this.view.k; drag.node.y = (py - this.view.y) / this.view.k;
        this.kick(0.3);
      } else if (drag && drag.pan) {
        if (!moved && Math.hypot(px - drag.pan[0], py - drag.pan[1]) < 4) return;
        moved = true;
        this.held = true; this.goal = null; this.follow = false;
        this.view.x = drag.pan[2] + (px - drag.pan[0]); this.view.y = drag.pan[3] + (py - drag.pan[1]);
        this.draw();
      } else if (!drag) {
        const n = this.hit(px, py);
        const key = n ? n.key : null;
        if (key !== this.hover) { this.hover = key; this.draw(); }
        if (n) c.title = n.kind === "region" ? `${n.name} (${n.size} things)` : n.kind === "doc" ? `${n.name} (doc ${n.doc})`
          : `${n.name} (${n.type}${n.docs !== undefined ? `, in ${n.docs} documents` : ""}, ${n.degree} edges here${n.hidden.length ? `, ${n.hidden.length} neighbours not drawn` : ""})`;
        else {
          const ed = this.edgeHit(px, py);
          c.title = ed ? `${ed.src} ${ed.rel} ${ed.dst} (${ed.confidence})${ed.evidence ? "\n" + ed.evidence : ""}` : "";
        }
        c.style.cursor = n ? "pointer" : "grab";
      }
    });
    const up = (e) => {
      down.delete(e.pointerId);
      if (drag && drag.node) drag.node.pinned = false;
      if (down.size === 0) { drag = null; c.classList.remove("dragging"); }
    };
    c.addEventListener("pointerup", (e) => {
      const wasDrag = moved;
      const n = !wasDrag && drag && drag.node ? drag.node : null;
      up(e);
      if (wasDrag || !n) return;
      // a second tap on the same node soon after is a double click (a
      // finger has none of its own)
      const now = performance.now();
      if (e.pointerType !== "mouse" && lastTap[1] === n.key && now - lastTap[0] < 350) { lastTap = [0, null]; this.open(n); return; }
      lastTap = [now, n.key];
      this.press(n, e.shiftKey);
    });
    c.addEventListener("pointercancel", up);
    c.addEventListener("pointerleave", () => { if (this.hover) { this.hover = null; this.draw(); } });
    c.addEventListener("dblclick", (e) => {
      const n = this.hit(...at(e));
      if (n) this.open(n);
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [px, py] = at(e);
      this.zoomAt(px, py, e.deltaY > 0 ? 1 / 1.15 : 1.15);
    }, { passive: false });
    c.addEventListener("keydown", (e) => this.key(e));
  }

  // a click: a path's other end, else select (and the first time, expand;
  // on the selected node, fold)
  press(n, shift) {
    if ((shift && this.selected && this.selected !== n.key) || (this.connectFrom && this.connectFrom !== n.key)) {
      this.connect(this.connectFrom || this.selected, n.key);
      return;
    }
    if (n.kind) { this.select(n.key); return; }
    if (n.expanded && n.key === this.selected) { this.fold(n.key); return; }
    this.select(n.key);
    if (!n.expanded) this.expand(n.key);
  }

  // a double click: the thing's own page
  open(n) {
    if (n.kind === "doc") location.hash = `#doc/${n.doc}`;
    else if (n.kind === "region") go("graph", "", { community: n.rid });
    else go("graph", "", { entity: n.name, type: n.type, ...scopeParams() });
  }

  // The keyboard on a focused canvas: arrows walk to the neighbour that
  // way (any node, when none is selected), Enter expands or folds, Escape
  // lets go, + and - zoom, f fits.
  key(e) {
    const dirs = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (dirs[e.key]) {
      e.preventDefault();
      const from = this.nodes.get(this.selected);
      if (!from) {
        const first = [...this.nodes.values()].sort((p, q) => this.radius(q) - this.radius(p))[0];
        if (first) { this.select(first.key); this.reveal(first.key); }
        return;
      }
      let pool = new Set();
      for (const ed of this.edges.values()) { if (ed.s === from.key) pool.add(ed.t); if (ed.t === from.key) pool.add(ed.s); }
      for (const l of this.links) { if (l.a === from.key) pool.add(l.b); if (l.b === from.key) pool.add(l.a); }
      const toward = (keys) => neighbourToward(from, [...keys].map((k) => this.nodes.get(k)).filter(Boolean), ...dirs[e.key]);
      const next = toward(pool) || toward(this.nodes.keys());
      if (next) { this.select(next); this.reveal(next); }
    } else if (e.key === "Enter" && this.selected) {
      e.preventDefault();
      const n = this.nodes.get(this.selected);
      if (n.kind) this.open(n); else if (n.expanded) this.fold(n.key); else this.expand(n.key);
    } else if (e.key === "Escape") {
      this.clearPath();
      this.select(null);
    } else if (e.key === "+" || e.key === "=") this.zoomAt(this.w / 2, this.h / 2, 1.25);
    else if (e.key === "-") this.zoomAt(this.w / 2, this.h / 2, 0.8);
    else if (e.key === "f") this.fit(true);
  }

  select(key) {
    this.selected = key;
    this.draw();
    this.onSelect(key ? this.nodes.get(key) : null, key ? this.edgesOf(key) : [], this);
  }

  edgesOf(key) {
    return [...this.edges.values()].filter((e) => e.s === key || e.t === key);
  }
}

// The panel beside the canvas: what the selected thing is and says, and
// what can be done with it on the map.
function graphPanel(node, edges, graph) {
  if (!node) {
    return `<p class="muted">Click a node to see its edges; the first click also expands it, a second click on the selected node folds it again.
      Shift-click a second node for how the two are connected. With the map focused, the arrow keys walk from node to node, Enter expands, Escape lets go.</p>`;
  }
  if (node.kind === "doc") {
    return `<h2>${esc(node.name)}</h2><div class="muted">document ${node.doc} · <a href="#doc/${node.doc}">open it</a></div>`;
  }
  if (node.kind === "region") {
    const by = graph ? graph.links.filter((l) => l.a === node.key || l.b === node.key)
      .sort((p, q) => (q.n || 0) - (p.n || 0)).slice(0, 12) : [];
    const rows = by.map((l) => {
      const other = graph.nodes.get(l.a === node.key ? l.b : l.a);
      return other ? `<li><a href="#graph?community=${other.rid}">${esc(other.name)}</a> <span class="muted">· ${l.n} facts between them</span></li>` : "";
    }).join("");
    return `<h2>${esc(node.name)}</h2><div class="muted">region · ${node.size} things · <a href="#graph?community=${node.rid}">open it</a></div>
      ${(node.members || []).length ? `<p class="muted">Weighing most: ${node.members.map(esc).join(", ")}</p>` : ""}
      ${rows ? `<h3 style="font-size:.9rem">Reaching furthest into</h3><ul>${rows}</ul>` : ""}`;
  }
  const rows = edges.map((e) => {
    const out = e.s === node.key;
    const other = out ? e.dst : e.src;
    const otherType = out ? e.dst_type : e.src_type;
    return `<li>${out ? "" : `<b>${esc(other)}</b> <span class="muted">${esc(otherType)}</span> `}<span class="rel">${out ? "" : "→ "}${esc(e.rel)}${out ? " →" : ""}</span> ${out ? `<b>${esc(other)}</b> <span class="muted">${esc(otherType)}</span>` : ""}
      <span class="muted">· ${esc(e.confidence)}${e.producer ? ` · ${esc(e.producer)}` : ""}${e.source_doc ? ` · <a href="#doc/${e.source_doc}${e.evidence ? `?find=${encodeURIComponent(String(e.evidence).slice(0, 120))}` : ""}" title="${esc(e.evidence || "")}">doc ${e.source_doc}</a>` : ""}</span>
      ${e.evidence ? `<span class="ev">“${esc(e.evidence)}”</span>` : ""}</li>`;
  });
  const hidden = (node.hidden || []).length;
  const acts = [];
  if (hidden) acts.push(`${hidden} more neighbours <a href="#" id="graph-show-all">draw them</a>`);
  if (node.expanded) acts.push(`<a href="#" id="graph-fold">fold</a>`);
  if (edges.some((e) => e.source_doc)) acts.push(node.docsShown ? `<a href="#" id="graph-docs-off">hide its documents</a>` : `<a href="#" id="graph-docs">show its documents</a>`);
  acts.push(graph && graph.connectFrom === node.key ? `<span>now click the other end</span>` : `<a href="#" id="graph-path">how is it connected to…</a>`);
  if (isPersonType(node.type)) acts.unshift(`<a href="#people?${new URLSearchParams({ name: node.name })}">their page</a>`);
  const docs = node.docs !== undefined ? ` · in ${node.docs} documents` : "";
  return `<h2>${esc(node.name)}</h2><div class="muted">${esc(node.type)}${docs} · ${edges.length} edges drawn</div>
    <div class="graph-acts">${acts.join(" · ")}</div><ul>${rows.join("")}</ul>`;
}

// connect's answer in the panel: each path a line of hops with the quote
// behind each
function pathPanel(got, a, b) {
  const head = `<h2>${esc(a.name)} ↔ ${esc(b.name)}</h2>`;
  if ((got.unknown || []).length) return `${head}<p class="muted">Not in the graph: ${got.unknown.map(esc).join(", ")}.</p>`;
  if (!(got.paths || []).length) {
    return `${head}<p class="muted">No sound connection${got.weak_left_out ? ` (${got.weak_left_out} weak ones left out)` : ""}${GRAPH_SCOPE.world_at ? ` at ${esc(GRAPH_SCOPE.world_at)}` : ""}.</p>`;
  }
  const paths = got.paths.map((p, i) => `<li><b>${i + 1}.</b> ${p.hops.map((h) => `${esc(h.src)} <span class="rel">${esc(h.rel)} →</span> ${esc(h.dst)}
      <span class="muted">${h.source_doc ? `· <a href="#doc/${h.source_doc}">doc ${h.source_doc}</a>` : ""}${h.documents > 1 ? ` and ${h.documents - 1} more` : ""}${h.world_from ? ` · from ${esc(h.world_from)}` : ""}${h.world_to ? ` to ${esc(h.world_to)}` : ""}</span>
      ${h.evidence ? `<span class="ev">“${esc(h.evidence)}”</span>` : ""}`).join("<br>")}${p.weak ? ` <span class="muted">(weak)</span>` : ""}</li>`).join("");
  return `${head}<div class="graph-acts"><a href="#" id="graph-path-clear">clear the path</a></div><ul>${paths}</ul>`;
}

// The panel follows the selection; its links act on the graph.
function graphPanelUpdater(panel) {
  return (node, edges, graph) => {
    panel.innerHTML = graphPanel(node, edges, graph);
    const on = (id, act) => {
      const el = panel.querySelector(id);
      if (el) el.addEventListener("click", (e) => { e.preventDefault(); act(); });
    };
    if (!node) return;
    on("#graph-show-all", () => graph.showHidden(node.key));
    on("#graph-fold", () => graph.fold(node.key));
    on("#graph-docs", () => graph.showDocs(node.key));
    on("#graph-docs-off", () => graph.hideDocs(node.key));
    on("#graph-path", () => { graph.connectFrom = node.key; graph.select(node.key); });
  };
}

// What the graph is drawn from, when narrowed: a module's documents
// (domain), a page's tree with what it links (page: a project's notes and
// the papers they cite), the personal documents left out or alone
// (personal), and the facts that hold in the world at a date (world_at).
// The overview's hubs, an entity's walk and every expansion keep to it
// (GET /graph/overview, /traverse with the same parameters) until the
// choice is cleared. GRAPH_VS is a second scope drawn beside the first.
let GRAPH_SCOPE = { domain: "", page: "", personal: "", world_at: "" };
let GRAPH_VS = { domain: "", page: "", personal: "" };
const params = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v));
const scopeParams = () => params(GRAPH_SCOPE);
const vsParams = () => params({ ...GRAPH_VS, world_at: GRAPH_SCOPE.world_at });
const comparing = () => Object.values(GRAPH_VS).some(Boolean);
function scopeIds() {
  const out = {};
  if (GRAPH_SCOPE.page) out.page = Number(GRAPH_SCOPE.page);
  if (GRAPH_SCOPE.personal) out.personal = GRAPH_SCOPE.personal;
  return out;
}
const YEAR_FIRST = 1900, YEAR_LAST = new Date().getFullYear();

// how the nodes are coloured, kept for the visit
let GRAPH_COLOUR = (() => { try { return sessionStorage.getItem("graph-colour") || "type"; } catch (_) { return "type"; } })();

let SCOPE_PAGES = null;  // the pages a filter may be: projects and collections
async function scopePages() {
  if (!SCOPE_PAGES) {
    try {
      const pages = await api("/pages");
      const parents = new Set(pages.map((pg) => pg.parent).filter((x) => x != null));
      SCOPE_PAGES = pages
        .filter((pg) => pg.kind === "project" || (pg.parent == null && parents.has(pg.doc_id)))
        .sort((a, b) => (a.kind === "project" ? 0 : 1) - (b.kind === "project" ? 0 : 1) || (a.title || a.slug).localeCompare(b.title || b.slug));
    } catch (_) { SCOPE_PAGES = []; }
  }
  return SCOPE_PAGES;
}
function pageSelect(name, value) {
  value = String(value || "");
  const opts = SCOPE_PAGES.map((pg) => `<option value="${pg.doc_id}"${String(pg.doc_id) === value ? " selected" : ""}>${pg.kind === "project" ? "project: " : ""}${esc(pg.title || pg.slug)}</option>`).join("");
  const other = value && !SCOPE_PAGES.some((pg) => String(pg.doc_id) === value) ? `<option value="${esc(value)}" selected>page ${esc(value)}</option>` : "";
  return `<select name="${name}" title="a project's or a collection's pages and what they link"><option value="">every page</option>${opts}${other}</select>`;
}
function personalSelect(name, value) {
  return `<select name="${name}" title="the documents marked or suspected personal">
      <option value=""${value ? "" : " selected"}>personal too</option>
      <option value="hide"${value === "hide" ? " selected" : ""}>without personal</option>
      <option value="only"${value === "only" ? " selected" : ""}>personal only</option>
    </select>`;
}
async function scopeBar(p, what) {
  await scopePages();
  const year = parseInt(GRAPH_SCOPE.world_at, 10);
  const at = Number.isFinite(year) ? Math.min(YEAR_LAST, Math.max(YEAR_FIRST, year)) : YEAR_FIRST - 1;
  const vs = comparing();
  return `<span class="graph-scope">
    ${domainSelect(GRAPH_SCOPE.domain, "domain", what)}
    ${pageSelect("page", GRAPH_SCOPE.page)}
    ${personalSelect("personal", GRAPH_SCOPE.personal)}
    <label class="graph-when" title="only the facts that hold in the world then, and the undated ones">
      <input type="range" name="world_at" min="${YEAR_FIRST - 1}" max="${YEAR_LAST}" value="${at}">
      <output>${at < YEAR_FIRST ? "any time" : at}</output>
    </label>
    <button type="button" class="secondary" id="graph-vs-toggle" aria-expanded="${vs}">${vs ? "compared" : "compare…"}</button>
    <span class="graph-vs"${vs ? "" : " hidden"}>with
      ${domainSelect(GRAPH_VS.domain, "vs_domain", "the second scope's module")}
      ${pageSelect("vs_page", GRAPH_VS.page)}
      ${personalSelect("vs_personal", GRAPH_VS.personal)}
    </span>
  </span>`;
}
function scopeChange(out, base) {
  const read = () => {
    const next = { ...base };
    out.querySelectorAll(".graph-scope select").forEach((x) => { if (x.value) next[x.name] = x.value; });
    const range = out.querySelector(".graph-scope input[name=world_at]");
    if (range && Number(range.value) >= YEAR_FIRST) next.world_at = range.value;
    return next;
  };
  out.querySelectorAll(".graph-scope select").forEach((s) => s.addEventListener("change", () => go("graph", "", read())));
  const range = out.querySelector(".graph-scope input[name=world_at]");
  if (range) {
    const label = range.nextElementSibling;
    range.addEventListener("input", () => { label.textContent = Number(range.value) < YEAR_FIRST ? "any time" : range.value; });
    range.addEventListener("change", () => go("graph", "", read()));
  }
  const toggle = out.querySelector("#graph-vs-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const box = out.querySelector(".graph-vs");
      if (comparing()) {  // compared: the button ends it
        const next = read();
        for (const k of ["vs_domain", "vs_page", "vs_personal"]) delete next[k];
        go("graph", "", next);
        return;
      }
      box.hidden = !box.hidden;
      toggle.setAttribute("aria-expanded", String(!box.hidden));
    });
  }
}
function scopeWordsOf(s) {
  const bits = [];
  if (s.domain) bits.push(`the <b>${esc(s.domain)}</b> documents`);
  if (s.page) {
    const pg = (SCOPE_PAGES || []).find((x) => String(x.doc_id) === String(s.page));
    bits.push(`<b>${esc(pg ? pg.title || pg.slug : `page ${s.page}`)}</b> and what it links`);
  }
  if (s.personal === "hide") bits.push("without personal documents");
  if (s.personal === "only") bits.push("personal documents only");
  return bits.join(", ");
}
function scopeWords() {
  const words = scopeWordsOf(GRAPH_SCOPE);
  const when = GRAPH_SCOPE.world_at ? `what holds in ${esc(GRAPH_SCOPE.world_at)}` : "";
  return [words, when].filter(Boolean).join(", ");
}
function compareWords() {
  if (!comparing()) return "";
  const a = scopeWordsOf(GRAPH_SCOPE) || "the whole library", b = scopeWordsOf(GRAPH_VS) || "the whole library";
  return `<span class="graph-compare"><span class="ring"></span> only in ${a} · <span class="ring dashed"></span> only in ${b} · plain: in both</span>`;
}

// the legend: the types, or the regions the drawn things are in
async function legendHtml(graph) {
  if (!graph || graph.colourBy !== "region") {
    return Object.entries(TYPE_COLORS).map(([t, c]) => `<span style="--c:${c}">${t}</span>`).join("");
  }
  const labels = await regionLabels();
  const count = new Map();
  for (const n of graph.nodes.values()) if (n.region !== undefined) count.set(n.region, (count.get(n.region) || 0) + 1);
  const shown = [...count.entries()].sort((a, b) => b[1] - a[1]).slice(0, 14);
  return shown.map(([id]) => `<span style="--c:${regionColor(id)}"><a href="#graph?community=${id}">${esc(labels.get(id) || `region ${id}`)}</a></span>`).join("")
    + (count.size > shown.length ? `<span class="muted" style="--c:transparent">${count.size - shown.length} more</span>` : "");
}

// The frame every graph is drawn in: tools, legend, canvas and panel.
// The region map has neither: its nodes are regions, coloured each its own.
function graphFrame(tools, below = "", things = true) {
  return `
    <div class="graph-tools">${tools}
      <input type="search" id="graph-find" placeholder="find on the map" aria-label="find on the map">
      ${things ? `<select id="graph-colour" title="what the colours say">
        <option value="type"${GRAPH_COLOUR === "type" ? " selected" : ""}>colour: type</option>
        <option value="region"${GRAPH_COLOUR === "region" ? " selected" : ""}>colour: region</option>
      </select>` : ""}
      <button type="button" id="graph-fit" class="secondary">fit</button>
    </div>
    ${things ? `<div class="legend" id="graph-legend"></div>` : ""}
    <div class="graph-layout">
      <div class="graph-canvas plate"><canvas tabindex="0" aria-label="the graph; arrow keys walk it"></canvas></div>
      <aside class="graph-panel" id="graph-panel">${graphPanel(null, [])}</aside>
    </div>${below}`;
}

// The canvas of a frame just put on the page, with its tools wired.
function mountGraph(out) {
  const panel = document.getElementById("graph-panel");
  const legend = document.getElementById("graph-legend");
  const graph = new ForceGraph(out.querySelector("canvas"), graphPanelUpdater(panel));
  graph.colourBy = GRAPH_COLOUR;
  const paint = async () => { if (legend) legend.innerHTML = await legendHtml(graph); };
  graph.onMarks = () => { if (graph.colourBy === "region") paint(); };
  graph.onPath = (got, a, b) => {
    panel.innerHTML = pathPanel(got, a, b);
    const clear = panel.querySelector("#graph-path-clear");
    if (clear) clear.addEventListener("click", (e) => { e.preventDefault(); graph.clearPath(); graph.select(graph.selected); });
  };
  document.getElementById("graph-fit").addEventListener("click", () => graph.fit(true));
  const colour = document.getElementById("graph-colour");
  if (colour) {
    colour.addEventListener("change", () => {
      GRAPH_COLOUR = graph.colourBy = colour.value;
      try { sessionStorage.setItem("graph-colour", GRAPH_COLOUR); } catch (_) { /* this visit only */ }
      graph.draw(); paint();
    });
  }
  const find = document.getElementById("graph-find");
  find.addEventListener("input", () => { graph.find = find.value.trim().toLowerCase(); graph.draw(); });
  find.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const hit = [...graph.nodes.values()].filter((n) => graph.matches(n)).sort((p, q) => graph.radius(q) - graph.radius(p))[0];
    if (hit) { graph.select(hit.key); graph.reveal(hit.key); graph.canvas.focus(); }
  });
  graph.canvas.addEventListener("keydown", (e) => { if (e.key === "/") { e.preventDefault(); find.focus(); } });
  paint();
  return graph;
}

// The region map's data as nodes and links the graph merges.
function regionMapData(got) {
  const nodes = got.nodes.map((r) => ({ key: `region|${r.id}`, name: regionName(r), type: "region", kind: "region", rid: r.id, size: r.size, members: r.members, degree: 0 }));
  const links = got.links.map((l) => ({ akey: `region|${l.a}`, bkey: `region|${l.b}`, weight: 1 + Math.log2(l.weight), n: l.weight }));
  return { nodes, links };
}

async function viewGraph(arg, p) {
  const q = p.q || "";
  const entity = p.entity || arg || "";
  GRAPH_SCOPE = { domain: p.domain || "", page: p.page || "", personal: p.personal || "", world_at: p.world_at || "" };
  GRAPH_VS = { domain: p.vs_domain || "", page: p.vs_page || "", personal: p.vs_personal || "" };
  const docId = p.doc || "";
  const communityId = p.community || "";
  await modules();
  // Everything is fetched before the page is touched: the graph on screen
  // stays until the next one can take its place, laid out already.
  let html = "", after = null;
  try {
    ({ html, after } = await graphRoute({ p, q, entity, docId, communityId }));
  } catch (err) {
    html = `<p class="error">${esc(err && err.message ? err.message : String(err))}</p>`;
  }
  view.classList.add("wide");  // the canvas takes the page, like a document's text
  view.innerHTML = `
  <form id="graph-form" class="search-form">
    <input name="q" type="search" value="${esc(q)}" placeholder="find an entity…">
    <button>Find</button>
  </form>
  <div id="graph-out">${html}</div>`;
  const form = document.getElementById("graph-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    go("graph", "", Object.fromEntries(new FormData(form)));
  });
  const out = document.getElementById("graph-out");
  if (after) {
    try { await after(out); } catch (err) { showError(out, err); }
  }
}

// What one graph route shows: its HTML, and what to do once it is on the
// page (`after`), with the data already fetched.
async function graphRoute({ p, q, entity, docId, communityId }) {
  if (communityId) {
    // one region of the library: its things drawn, what it covers, its parts
    let got = null;
    try { got = await api(`/communities/${encodeURIComponent(communityId)}`); } catch (_) { /* gone at a rebuild */ }
    if (!got) return { html: regionPage(got) };
    let drawn = null;
    try { drawn = await api(`/communities/${encodeURIComponent(communityId)}/graph`, { members: 40 }); } catch (_) { /* an older door: the text alone */ }
    const tools = `<span>The region <b>${esc(regionName(got))}</b>: the things that weigh most in it</span>
      <span class="muted">· faint lines: things that share documents · click a node to expand it, double-click to open its neighbourhood</span>`;
    return {
      html: graphFrame(tools, regionPage(got)),
      after: (out) => {
        const graph = mountGraph(out);
        if (drawn && drawn.nodes.length) graph.merge(drawn.edges, null, drawn.nodes, drawn.links);
      },
    };
  }
  if (docId) {
    // one document's graph: what it says, its own entity's facts first
    const got = await api(`/graph/document/${encodeURIComponent(docId)}`);
    const tools = `<span>What <a href="#doc/${esc(docId)}">doc ${esc(docId)}</a> says</span>
      <span class="muted">· ${got.edges.length} facts · click a node to expand it across the library</span>`;
    return {
      html: graphFrame(tools),
      after: (out) => {
        const graph = mountGraph(out);
        if (!got.edges.length) { document.getElementById("graph-panel").innerHTML = `<p class="muted">The graph records nothing from this document yet.</p>`; return; }
        graph.merge(got.edges, null);
      },
    };
  }
  if (!entity && !q && p.map === "regions") {
    // the regions themselves, a level above the hubs
    const got = await api("/communities/map", { limit: 40 });
    const tools = `<select id="graph-map" title="what the overview draws"><option value="">draw: the hubs</option><option value="regions" selected>draw: the regions</option></select>
      <span>The regions of the library; a line is the facts reaching between two</span>
      <span class="muted">· click a region for its neighbours, double-click to open it</span>`;
    return {
      html: graphFrame(tools, "", false),
      after: (out) => {
        mapChange(out);
        const graph = mountGraph(out);
        if (!got.nodes.length) { document.getElementById("graph-panel").innerHTML = `<p class="muted">No regions yet: the communities pass draws them.</p>`; return; }
        const { nodes, links } = regionMapData(got);
        graph.merge([], null, nodes, links);
      },
    };
  }
  if (!entity && !q) {
    const tools = `<select id="graph-map" title="what the overview draws"><option value="" selected>draw: the hubs</option><option value="regions">draw: the regions</option></select>
      ${await scopeBar(p, "draw one module's graph")}
      <span>${scopeWords() ? `The graph of ${scopeWords()}: its most connected things` : "Overview: the most connected concepts, methods, tools and datasets"}</span>
      ${compareWords()}
      <span class="muted">· faint lines: hubs that share documents · click a node to expand it, click it again to fold it, double-click to open its neighbourhood</span>`;
    const [overview, other] = await Promise.all([
      api("/graph/overview", { limit: 30, ...scopeParams() }),
      comparing() ? api("/graph/overview", { limit: 30, ...vsParams() }) : null,
    ]);
    let regions = null;
    if (!scopeWords() && !comparing()) {
      try { regions = (await api("/communities", { level: 0, limit: 40 })).communities; } catch (_) { /* an older door */ }
    }
    return {
      html: graphFrame(tools, regions ? regionList(regions) : ""),
      after: (out) => {
        mapChange(out);
        scopeChange(out, {});
        const graph = mountGraph(out);
        if (!overview.nodes.length && !(other && other.nodes.length)) {
          document.getElementById("graph-panel").innerHTML = `<p class="muted">Nothing in the graph${scopeWords() ? ` for ${scopeWords()}` : "; run an extraction first"}.</p>`;
          return;
        }
        drawCompared(graph, overview, other, (g) => [g.edges, null, g.nodes, g.links]);
      },
    };
  }
  if (!entity) {
    const ents = await api("/entities", { q, limit: 40 });
    if (!ents.length) return { html: `<p class="muted">No entity matches.</p>` };
    // `as` is there when the hit was found under a name the entity is
    // known by rather than its own: the word the document used
    return { html: `<ul class="entities">${ents.map((e) => `<li><a href="#graph?entity=${encodeURIComponent(e.name)}">${esc(e.name)}</a> <span class="muted">${esc(e.type)} · ${e.degree} edges${e.as ? ` · also called ${esc(e.as)}` : ""}</span></li>`).join("")}</ul>` };
  }
  const tools = `${await scopeBar(p, "only what one module's documents say")}
    <span>Neighbourhood of <b>${esc(entity)}</b>${scopeWords() ? ` in ${scopeWords()}` : ""}</span>
    ${compareWords()}
    <span class="muted">· click a node to expand it, click it again to fold it · drag to pan, wheel to zoom · dashed edges are inferred or ambiguous</span>`;
  const walk = (scope) => api("/traverse", { entity, hops: 1, limit: 0, type: p.type || "", ...scope });
  const [mine, other] = await Promise.all([walk(scopeParams()), comparing() ? walk(vsParams()) : null]);
  return {
    html: graphFrame(tools),
    after: (out) => {
      scopeChange(out, p.type ? { entity, type: p.type } : { entity });
      const graph = mountGraph(out);
      const edges = mine.edges.concat(other ? other.edges : []);
      if (!edges.length) { document.getElementById("graph-panel").innerHTML = `<p class="muted">No edges for this entity${scopeWords() ? ` in ${scopeWords()}` : ""}.</p>`; return; }
      const first = edges.find((e) => e.src === entity || e.dst === entity);
      if (!first) { drawCompared(graph, mine, other, (g) => [g.edges, null, null, null]); return; }
      const start = graph.node(entity, first.src === entity ? first.src_type : first.dst_type, null);
      start.expanded = true;
      drawCompared(graph, mine, other, (g) => [g.edges, start.key, null, null]);
      graph.select(start.key);
    },
  };
}

// One answer drawn, or two compared: each marks what it brought, and the
// layout runs once over both.
function drawCompared(graph, mine, other, args) {
  if (!other) { graph.merge(...args(mine)); return; }
  graph.sides = true;
  graph.batching = true;
  graph.merge(...args(mine), 1);
  graph.merge(...args(other), 2);
  graph.finish();
}

function mapChange(out) {
  const pick = out.querySelector("#graph-map");
  if (pick) pick.addEventListener("change", () => go("graph", "", pick.value ? { map: pick.value } : {}));
}
