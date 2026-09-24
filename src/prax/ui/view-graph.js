// The graph view: a neighbourhood drawn on a canvas, its legend and
// the edge a click explains.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// ----------------------------------------------------------------- graph
// The neighbourhood of an entity on a canvas. Nodes are (type, name);
// edges come from GET /traverse (hops=1) and a click on a node fetches its
// own neighbourhood and merges it in, forty neighbours at a time. The
// layout is a small spring model animated a few steps per frame; no
// library (the previous SVG version rebuilt the whole picture on every
// change and froze on a hub with five hundred neighbours).

const TYPE_COLORS = {
  paper: "#2f5d8a", author: "#7a5c1e", concept: "#4b7a45", method: "#6a4b7a",
  claim: "#a0522d", tool: "#3b7a7a", venue: "#8a6d2f", dataset: "#5a5a8a",
  person: "#7a5c1e", organization: "#8a4b2f", document: "#2f5d8a", place: "#3f6b3f",
  event: "#7a3f6b", work: "#5a3f8a", page: "#2f7a8a", project: "#2f8a5a",
};
const nodeKey = (name, type) => type + "|" + name;
const typeColor = (t) => TYPE_COLORS[t] || "#888";

class ForceGraph {
  // A canvas, not an SVG: five hundred nodes and their labels redraw in a
  // millisecond or two, where an SVG of the same rebuilt through innerHTML
  // froze the page. The simulation is the same small spring model, but run
  // a few steps per animation frame with a decaying alpha instead of three
  // hundred at once, so the page answers while the layout settles.
  constructor(canvas, onSelect) {
    ForceGraph.live = this;  // the one on screen, for a theme change
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.onSelect = onSelect;
    this.nodes = new Map();
    this.edges = new Map();
    this.links = [];  // co-occurrence links of the overview: {a, b, weight}
    this.selected = null;
    this.hover = null;
    this.view = { x: 0, y: 0, k: 1 };  // screen = world * k + (x, y)
    this.alpha = 0;
    this.frame = null;
    this.fitPending = false;
    this.cap = 40;  // neighbours drawn per expansion; the rest wait in the panel
    this.held = false;  // the person panned or zoomed: a resize keeps their view
    this.resize();
    this.bind();
    // a resize before anyone touched the view fits again (the canvas may
    // have had no size yet when the layout first fitted)
    this.observer = new ResizeObserver(() => { this.resize(); if (this.held) this.draw(); else this.fit(); });
    this.observer.observe(canvas);
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth || 900, h = this.canvas.clientHeight || 600;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = w; this.h = h;
  }

  node(name, type, near) {
    const key = nodeKey(name, type);
    let n = this.nodes.get(key);
    if (!n) {
      const a = Math.random() * Math.PI * 2;
      const r = 60 + Math.random() * 60;
      n = { key, name, type, expanded: false, degree: 0, hidden: [], by: near ? near.key : null,
            x: (near ? near.x : 0) + Math.cos(a) * r, y: (near ? near.y : 0) + Math.sin(a) * r, vx: 0, vy: 0 };
      this.nodes.set(key, n);
    }
    return n;
  }

  // Merge edges in. Around a node being expanded, only its `cap` best
  // connected neighbours are drawn; the rest stay on the node as `hidden`
  // until asked for, because a hub with five hundred neighbours is a
  // hairball at any speed.
  merge(edgeList, aroundKey, nodeList, linkList) {
    const near = aroundKey ? this.nodes.get(aroundKey) : null;
    for (const n of nodeList || []) this.node(n.name, n.type, null).degree = n.degree || 0;
    for (const l of linkList || []) {
      this.links.push({ a: this.node(l.a, l.a_type, null).key, b: this.node(l.b, l.b_type, null).key, weight: l.weight });
    }
    let fresh = edgeList.filter((e) => !this.edges.has(e.edge_id));
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
      this.edges.set(e.edge_id, { ...e, s: s.key, t: t.key, by: aroundKey });
    }
    this.fitPending = true;
    this.kick(1);
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
    n.expanded = true;
    const edges = await api("/traverse", { entity: n.name, hops: 1 });
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
    this.unfold(key);
    const kept = new Set(this.links.flatMap((l) => [l.a, l.b]));
    for (const e of this.edges.values()) { kept.add(e.s); kept.add(e.t); }
    for (const m of [...this.nodes.values()]) {
      if (m.by !== null && m.key !== key && !kept.has(m.key)) this.nodes.delete(m.key);
    }
    if (this.hover && !this.nodes.has(this.hover)) this.hover = null;
    this.kick(0.5);
    this.select(key);
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

  kick(alpha) {
    this.alpha = Math.max(this.alpha, alpha);
    if (!this.frame) this.frame = requestAnimationFrame(() => this.tick());
  }

  tick() {
    this.frame = null;
    for (let i = 0; i < 3; i++) this.step();
    this.alpha *= 0.97;
    if (this.fitPending && this.alpha < 0.4) { this.fitPending = false; this.fit(); }
    this.draw();
    if (this.alpha > 0.015) this.frame = requestAnimationFrame(() => this.tick());
    else this.alpha = 0;
  }

  step() {
    const nodes = [...this.nodes.values()];
    const alpha = this.alpha;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy || 1;
        if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
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
      const f = (d - 150) * Math.min(0.03, 0.004 * l.weight) * alpha;
      a.vx += dx / d * f; a.vy += dy / d * f;
      b.vx -= dx / d * f; b.vy -= dy / d * f;
    }
    for (const n of nodes) {
      if (n.pinned) { n.vx = 0; n.vy = 0; continue; }
      n.x += n.vx; n.y += n.vy; n.vx *= 0.5; n.vy *= 0.5;
    }
  }

  fit() {
    const nodes = [...this.nodes.values()];
    if (!nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const pad = 70;
    const k = Math.min(3, Math.max(0.05, Math.min(this.w / (x1 - x0 + 2 * pad), this.h / (y1 - y0 + 2 * pad))));
    this.view = { k, x: this.w / 2 - (x0 + x1) / 2 * k, y: this.h / 2 - (y0 + y1) / 2 * k };
    this.draw();
  }

  toScreen(n) { return [n.x * this.view.k + this.view.x, n.y * this.view.k + this.view.y]; }
  radius(n) { return 5 + Math.min(14, Math.sqrt(n.degree) * 2.2); }

  draw() {
    const ctx = this.ctx, v = this.view;
    const css = getComputedStyle(document.documentElement);
    const col = (name, fallback) => (css.getPropertyValue(name) || fallback).trim();
    const muted = col("--muted", "#777"), line = col("--line", "#ddd"), fg = col("--fg", "#222"),
          accent = col("--accent", "#c33"), panel = col("--panel", "#fff");
    ctx.clearRect(0, 0, this.w, this.h);
    // co-occurrence links, faintest
    ctx.lineCap = "round";
    for (const l of this.links) {
      const a = this.nodes.get(l.a), b = this.nodes.get(l.b);
      if (!a || !b) continue;
      const [ax, ay] = this.toScreen(a), [bx, by] = this.toScreen(b);
      ctx.strokeStyle = line; ctx.globalAlpha = 0.9;
      ctx.lineWidth = Math.min(6, 0.6 + l.weight * 0.5);
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    // edges; the selected node's edges on top, labelled
    const selectedEdges = [];
    for (const e of this.edges.values()) {
      if (e.s === this.selected || e.t === this.selected) { selectedEdges.push(e); continue; }
      this.drawEdge(ctx, e, muted, 1, 0.55);
    }
    for (const e of selectedEdges) this.drawEdge(ctx, e, accent, 2, 1);
    // nodes: the busiest get a label always, the rest when the map is small
    const nodes = [...this.nodes.values()];
    const labelled = new Set(nodes.length <= 140 ? nodes.map((n) => n.key)
      : nodes.slice().sort((p, q) => q.degree - p.degree).slice(0, 40).map((n) => n.key));
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (const n of nodes) {
      const [x, y] = this.toScreen(n);
      const r = this.radius(n) * Math.min(1.4, Math.max(0.6, v.k));
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = typeColor(n.type); ctx.fill();
      ctx.lineWidth = n.key === this.selected ? 3 : 1.5;
      ctx.strokeStyle = n.key === this.selected ? accent : (n.expanded ? fg : panel);
      ctx.stroke();
      if (labelled.has(n.key) || n.key === this.selected || n.key === this.hover) {
        const label = n.name.length > 38 ? n.name.slice(0, 36) + "…" : n.name;
        ctx.fillStyle = fg;
        ctx.fillText(label, x + r + 3, y);
      }
    }
    for (const e of selectedEdges) {
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
      const r = this.radius(n) * Math.min(1.4, Math.max(0.6, this.view.k)) + 4;
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

  bind() {
    const c = this.canvas;
    const at = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    let drag = null;  // {node} or {pan: [x, y, viewX, viewY]}
    let moved = false;
    c.addEventListener("mousedown", (e) => {
      const [px, py] = at(e);
      const n = this.hit(px, py);
      moved = false;
      if (n) { drag = { node: n }; n.pinned = true; }
      else drag = { pan: [px, py, this.view.x, this.view.y] };
      c.classList.add("dragging");
    });
    window.addEventListener("mousemove", (e) => {
      const [px, py] = at(e);
      if (drag && drag.node) {
        moved = true;
        drag.node.x = (px - this.view.x) / this.view.k; drag.node.y = (py - this.view.y) / this.view.k;
        this.kick(0.3);
      } else if (drag && drag.pan) {
        moved = true;
        this.held = true;
        this.view.x = drag.pan[2] + (px - drag.pan[0]); this.view.y = drag.pan[3] + (py - drag.pan[1]);
        this.draw();
      } else if (e.target === c) {
        const n = this.hit(px, py);
        const key = n ? n.key : null;
        if (key !== this.hover) { this.hover = key; this.draw(); }
        if (n) c.title = `${n.name} (${n.type}, ${n.degree} edges here${n.hidden.length ? `, ${n.hidden.length} neighbours not drawn` : ""})`;
        else { const ed = this.edgeHit(px, py); c.title = ed ? `${ed.src} ${ed.rel} ${ed.dst} (${ed.confidence})${ed.evidence ? "\n" + ed.evidence : ""}` : ""; }
        c.style.cursor = n ? "pointer" : "grab";
      }
    });
    window.addEventListener("mouseup", () => {
      if (drag && drag.node) drag.node.pinned = false;
      drag = null; c.classList.remove("dragging");
    });
    c.addEventListener("click", (e) => {
      if (moved) return;
      const n = this.hit(...at(e));
      if (!n) return;
      if (n.expanded && n.key === this.selected) { this.fold(n.key); return; }
      this.select(n.key);
      if (!n.expanded) this.expand(n.key);
    });
    c.addEventListener("dblclick", (e) => {
      const n = this.hit(...at(e));
      if (n) go("graph", "", { entity: n.name });
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [px, py] = at(e);
      const z = e.deltaY > 0 ? 1 / 1.15 : 1.15;
      const k = Math.min(6, Math.max(0.03, this.view.k * z));
      this.view = { k, x: px - (px - this.view.x) * (k / this.view.k), y: py - (py - this.view.y) * (k / this.view.k) };
      this.held = true;
      this.draw();
    }, { passive: false });
  }

  select(key) {
    this.selected = key;
    this.draw();
    this.onSelect(this.nodes.get(key), this.edgesOf(key));
  }

  edgesOf(key) {
    return [...this.edges.values()].filter((e) => e.s === key || e.t === key);
  }
}

function graphPanel(node, edges) {
  if (!node) return `<p class="muted">Click a node to see its edges; the first click also expands it, a second click on the selected node folds it again.</p>`;
  const rows = edges.map((e) => {
    const out = e.s === node.key;
    const other = out ? e.dst : e.src;
    const otherType = out ? e.dst_type : e.src_type;
    return `<li>${out ? "" : `<b>${esc(other)}</b> <span class="muted">${esc(otherType)}</span> `}<span class="rel">${out ? "" : "→ "}${esc(e.rel)}${out ? " →" : ""}</span> ${out ? `<b>${esc(other)}</b> <span class="muted">${esc(otherType)}</span>` : ""}
      <span class="muted">· ${esc(e.confidence)}${e.producer ? ` · ${esc(e.producer)}` : ""}${e.source_doc ? ` · <a href="#doc/${e.source_doc}${e.evidence ? `?find=${encodeURIComponent(String(e.evidence).slice(0, 120))}` : ""}" title="${esc(e.evidence || "")}">doc ${e.source_doc}</a>` : ""}</span>
      ${e.evidence ? `<span class="ev">“${esc(e.evidence)}”</span>` : ""}</li>`;
  });
  const hidden = (node.hidden || []).length;
  const more = hidden ? ` · ${hidden} more neighbours <a href="#" id="graph-show-all">draw them</a>` : "";
  const fold = node.expanded ? ` · <a href="#" id="graph-fold">fold</a>` : "";
  return `<h2>${esc(node.name)}</h2><div class="muted">${esc(node.type)} · ${edges.length} edges drawn${more}${fold}</div><ul>${rows.join("")}</ul>`;
}

// The panel follows the selection; its links act on the graph.
function graphPanelUpdater(panel, graphOf) {
  return (node, edges) => {
    panel.innerHTML = graphPanel(node, edges);
    const on = (id, act) => {
      const el = panel.querySelector(id);
      if (el) el.addEventListener("click", (e) => { e.preventDefault(); act(graphOf()); });
    };
    on("#graph-show-all", (g) => g.showHidden(node.key));
    on("#graph-fold", (g) => g.fold(node.key));
  };
}

async function viewGraph(arg, p) {
  const q = p.q || "";
  const entity = p.entity || arg || "";
  view.classList.add("wide");  // the canvas takes the page, like a document's text
  view.innerHTML = `
  <form id="graph-form" class="search-form">
    <input name="q" type="search" value="${esc(q)}" placeholder="find an entity…">
    <button>Find</button>
  </form>
  <div id="graph-out"></div>`;
  const form = document.getElementById("graph-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    go("graph", "", Object.fromEntries(new FormData(form)));
  });
  const out = document.getElementById("graph-out");
  try {
    if (!entity && !q) {
      const legend = Object.entries(TYPE_COLORS).map(([t, c]) => `<span style="--c:${c}">${t}</span>`).join("");
      out.innerHTML = `
        <div class="graph-tools">
          <span>Overview: the most connected concepts, methods, tools and datasets</span>
          <span class="muted">· faint lines: hubs that share documents · click a node to expand it, click it again to fold it, double-click to open its neighbourhood</span>
          <button type="button" id="graph-fit" class="secondary">fit</button>
        </div>
        <div class="legend">${legend}</div>
        <div class="graph-layout">
          <div class="graph-canvas plate"><canvas></canvas></div>
          <aside class="graph-panel" id="graph-panel">${graphPanel(null, [])}</aside>
        </div>`;
      const panel = document.getElementById("graph-panel");
      const graph = new ForceGraph(out.querySelector("canvas"), graphPanelUpdater(panel, () => graph));
      document.getElementById("graph-fit").addEventListener("click", () => graph.fit());
      const overview = await api("/graph/overview", { limit: 30 });
      if (!overview.nodes.length) { out.innerHTML = `<p class="muted">The graph is empty; run an extraction first.</p>`; return; }
      graph.merge(overview.edges, null, overview.nodes, overview.links);
      return;
    }
    if (!entity) {
      const ents = await api("/entities", { q, limit: 40 });
      if (!ents.length) { out.innerHTML = `<p class="muted">No entity matches.</p>`; return; }
      // `as` is there when the hit was found under a name the entity is
      // known by rather than its own: the word the document used
      out.innerHTML = `<ul class="entities">${ents.map((e) => `<li><a href="#graph?entity=${encodeURIComponent(e.name)}">${esc(e.name)}</a> <span class="muted">${esc(e.type)} · ${e.degree} edges${e.as ? ` · also called ${esc(e.as)}` : ""}</span></li>`).join("")}</ul>`;
      return;
    }
    const legend = Object.entries(TYPE_COLORS).map(([t, c]) => `<span style="--c:${c}">${t}</span>`).join("");
    out.innerHTML = `
      <div class="graph-tools">
        <span>Neighbourhood of <b>${esc(entity)}</b></span>
        <span class="muted">· click a node to expand it, click it again to fold it · drag to pan, wheel to zoom · dashed edges are inferred or ambiguous</span>
        <button type="button" id="graph-fit" class="secondary">fit</button>
      </div>
      <div class="legend">${legend}</div>
      <div class="graph-layout">
        <div class="graph-canvas plate"><canvas></canvas></div>
        <aside class="graph-panel" id="graph-panel">${graphPanel(null, [])}</aside>
      </div>`;
    const panel = document.getElementById("graph-panel");
    const graph = new ForceGraph(out.querySelector("canvas"), graphPanelUpdater(panel, () => graph));
    document.getElementById("graph-fit").addEventListener("click", () => graph.fit());
    const edges = await api("/traverse", { entity, hops: 1 });
    if (!edges.length) { panel.innerHTML = `<p class="muted">No edges for this entity.</p>`; return; }
    const first = edges.find((e) => e.src === entity || e.dst === entity);
    if (!first) { graph.merge(edges, null); return; }
    const start = graph.node(entity, first.src === entity ? first.src_type : first.dst_type, null);
    start.expanded = true;
    graph.merge(edges, start.key);
    graph.select(start.key);
  } catch (err) {
    out.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}
