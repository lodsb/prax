// Ask: the composer, the trail of a surf, the answer with its
// citations and sources, and keeping one on a page.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// ------------------------------------------------------------------- ask

// A question in, an answer with numbered citations out. The door gathers
// passages and graph facts; which model answers is the host's setting
// (/ask/config), overridable per question. "bundle only" shows the
// context without a model, which is what an MCP client gets.
let askConfig = null;

// A conversation laid out like a chat: the turns in a thread, the composer
// at its foot, and the sources of the selected turn in a column beside it
// (a [n] in an answer points at its card there; the card opens the
// document). The turns live in this tab's sessionStorage: a follow-up
// sends the earlier ones along (the door searches in their neighbourhood
// and the model sees them), "New ask" starts over, and a reload shows the
// conversation instead of asking again.
const ASK_KEY = "prax.ask";

function askSession() {
  try { return JSON.parse(sessionStorage.getItem(ASK_KEY) || "null") || { turns: [] }; }
  catch (_) { return { turns: [] }; }
}
function saveAskSession(turns) {
  const kept = turns.filter((t) => !t.pending && !t.error);
  try { sessionStorage.setItem(ASK_KEY, JSON.stringify({ turns: kept })); } catch (_) { /* full or blocked */ }
}
function clearAskSession() {
  try { sessionStorage.removeItem(ASK_KEY); } catch (_) { /* nothing to clear */ }
}

// The surfing budgets a question may set: how many steps the model takes
// before it answers (0: one shot from the first search) and how much it
// may read, in tokens, within what the chosen model's context holds.
function readingBounds(backend) {
  const reading = (askConfig && askConfig.reading) || {};
  const name = backend || (askConfig && askConfig.default) || "";
  return reading[name] || { default: 4000, max: 6000 };
}

function renderComposer(p) {
  const backend = p.backend || "";
  const dflt = askConfig ? askConfig.default : "none";
  const names = (askConfig && askConfig.models) || [];
  const steps = (askConfig && askConfig.steps) || { default: 8, max: 20 };
  const reading = readingBounds(backend);
  return `
  <form id="ask-form" class="composer" autocomplete="off">
    <textarea name="question" rows="1" placeholder="ask the library…" aria-label="question" autofocus>${esc(p.question || "")}</textarea>
    <button class="composer-send" title="ask (Enter; Shift+Enter for a new line)">Ask</button>
    <div class="composer-opts">
      <select name="backend" title="which model answers (prax.yaml)">
        <option value="" ${backend === "" ? "selected" : ""}>default (${esc(dflt)})</option>
        ${names.map((n) => `<option value="${esc(n)}" ${backend === n ? "selected" : ""}>${esc(n)}</option>`).join("")}
        <option value="none" ${backend === "none" ? "selected" : ""}>bundle only (no model)</option>
      </select>
      <select name="doctype" title="document type">
        <option value="" ${!p.doctype ? "selected" : ""}>any type</option>
        ${[["pdf", "PDFs"], ["web", "web pages"], ["image", "images"], ["text", "text files"], ["note", "notes"], ["page", "pages"]].map(([v, l]) => `<option value="${v}" ${p.doctype === v ? "selected" : ""}>${l}</option>`).join("")}
      </select>
      <label>passages <input name="limit" type="number" min="1" max="20" value="${esc(p.limit || settings().ask_limit)}" title="passages per search"></label>
      <label>steps <input name="steps" type="number" min="0" max="${steps.max}" value="${esc(p.steps != null ? p.steps : steps.default)}" title="how many steps the model surfs before answering (search again, read on, walk the graph, drop); 0 answers from the first search alone"></label>
      <label>reading <input name="tokens" type="number" min="1000" max="${reading.max}" step="500" value="${esc(p.tokens || reading.default)}" title="how much the steps may read, in tokens (at most ${reading.max} for this model)"></label>
      <span id="ask-count"></span>
      <button type="button" id="ask-new" class="composer-new" hidden title="forget this conversation">New ask</button>
    </div>
  </form>`;
}

// The trail of a surf: what the model did at each step, with what the step
// brought. Shown as it happens under the pending line, then folded under
// the answer; a [n] in it opens the source like one in the answer.
const STEP_WORDS = { search: "searched", read: "read", facts: "facts of", walk: "walked", similar: "like", drop: "set aside", answer: "enough read", error: "failed" };

function renderTrail(t) {
  const trail = t.trail || [];
  if (!trail.length) return "";
  const passages = t.passages || [];
  const items = trail.map((s) => {
    const verb = STEP_WORDS[s.action] || s.action;
    let head = esc(`${verb} ${s.arg || ""}`.trim());
    let result = s.action === "answer" ? "" : esc(s.result || "");
    if (s.action === "error") { head = esc(verb); result = esc(s.result || ""); }
    if (s.action === "read") head = citeLinks(head, passages);
    return `<li class="trail-step">
      <span class="trail-head">${head}${result ? `<span class="trail-result"> → ${citeLinks(result, passages)}</span>` : ""}</span>
      ${s.note ? `<span class="trail-note">${esc(s.note)}</span>` : ""}
    </li>`;
  }).join("");
  if (t.pending) return `<ol class="trail live" start="0">${items}</ol>`;
  const n = trail.filter((s) => s.n > 0).length;
  return `<details class="trail-fold"><summary>${n} step${n === 1 ? "" : "s"}${t.dropped && t.dropped.length ? `, ${t.dropped.length} set aside` : ""}</summary><ol class="trail" start="0">${items}</ol></details>`;
}

function renderTurn(t, i) {
  const passages = t.passages || [];
  const cited = new Set((t.citations || []).map((c) => c.n));
  let body;
  if (t.error) body = `<p class="error">${esc(t.error)}</p>`;
  else if (t.pending) body = `${renderTrail(t)}<p class="turn-pending muted">${esc(t.pending)}</p>`;
  else if (t.answer) body = `<div class="answer">${citeLinks(md(t.answer), passages)}</div>${renderTrail(t)}`;
  else body = `<p class="muted">${passages.length ? "No model answered; the sources beside are what a model would have been given." : "No passages found."}</p>${renderTrail(t)}`;
  const meta = (t.pending || t.error) ? "" : `
    <div class="turn-meta muted">
      ${t.model ? `<span>${esc(t.model)}${t.steps ? ` · ${t.steps} step${t.steps === 1 ? "" : "s"}` : ""} · ${t.seconds} s${t.cost_usd ? ` · $${t.cost_usd.toFixed(4)}` : ""} · ${(t.usage || {}).input_tokens || 0} in / ${(t.usage || {}).output_tokens || 0} out</span>` : ""}
      <button type="button" class="linkish turn-sources-link">${passages.length} source${passages.length === 1 ? "" : "s"}${cited.size ? `, ${cited.size} cited` : ""}</button>
      ${t.answer ? `<button type="button" class="linkish turn-keep-link">keep on page…</button>` : ""}
    </div>
    <div class="turn-keep" hidden></div>`;
  return `<article class="turn" data-turn="${i}">
    <div class="turn-q">${esc(t.question)}</div>
    <div class="turn-a">${body}${meta}</div>
  </article>`;
}

function renderSources(t, i) {
  if (!t || t.pending || t.error) return `<p class="muted side-empty">The sources of an answer appear here: one passage per document, the cited ones marked, each with what the graph knows about its document.</p>`;
  const passages = t.passages || [];
  const facts = t.facts || {};
  const cited = new Set((t.citations || []).map((c) => c.n));
  const cards = passages.map((p) => {
    const f = facts[p.doc_id] || [];
    const cut = p.text.length > 300;
    return `
    <article class="source plate ${cited.has(p.n) ? "cited" : ""}" id="source-${p.n}">
      <div class="source-n">[${p.n}]</div>
      <div class="source-body">
        <a class="source-title" href="#doc/${p.doc_id}${p.chunk_id ? `?chunk=${p.chunk_id}` : ""}">${esc(p.title || "(untitled)")}</a>
        <div class="hit-meta">${badge(p.kind)} <span>${headingPath(p.heading)}</span> <span>${p.page ? `p. ${p.page}` : (p.time != null ? `at ${fmtTime(p.time)}` : "")}</span></div>
        ${figureThumb(p)}
        <p class="snippet source-short">${esc(plainFigures(p.text).slice(0, 300))}${cut ? `… <button type="button" class="linkish source-more">more</button>` : ""}</p>
        ${cut ? `<p class="snippet source-full" hidden>${esc(p.text)} <button type="button" class="linkish source-more">less</button></p>` : ""}
        ${nearbyLine(p)}
        ${f.length ? `<div class="chips">${f.map((x) => `<a class="chip" style="--c:${typeColor(x.type)}" href="#graph?entity=${encodeURIComponent(x.name)}" title="${esc(x.rel)}">${esc(x.rel)}: ${esc(x.name)}</a>`).join("")}</div>` : ""}
      </div>
    </article>`;
  }).join("");
  return `<div class="side-head"><span>Sources</span><span>turn ${i + 1} · ${passages.length}${cited.size ? `, ${cited.size} cited` : ""}</span></div>
    ${rule()}
    <div class="sources ${t.answer ? "with-answer" : ""}">${cards || `<p class="muted side-empty">No passages found for this turn.</p>`}</div>`;
}

// a formula passage's neighbours: the equations around it, by number, each
// a link into the document (what the model was shown as "equations nearby")
function nearbyLine(p) {
  if (!p.nearby || !p.nearby.length) return "";
  const items = p.nearby.map((e) => {
    const num = e.number ? `(${e.number})` : "(·)";
    const head = e.head.length > 44 ? `${e.head.slice(0, 43)}…` : e.head;
    if (e.here) return `<b title="this passage">${esc(num)}</b>`;
    return `<a href="#doc/${p.doc_id}?chunk=${e.chunk_id}" title="${esc(e.head)}">${esc(num)}</a> ${esc(head)}`;
  });
  return `<p class="nearby muted">equations nearby: ${items.join(" · ")}</p>`;
}

function renderKeepForm(t) {
  return `
    <form class="ask-save">
      <label>Keep on page <select name="slug"><option value="">loading…</option></select></label>
      <input name="heading" type="text" value="${esc(t.question)}" placeholder="heading" title="section heading">
      <button>Add to page</button>
      <label>or start a synthesis <input name="new_slug" type="text" placeholder="new page name" title="a new synthesis page seeded with this answer"></label>
      <button type="button" class="secondary ask-stand" title="a page of its own that the door asks again when the library learns something about it">or keep as a standing question</button>
      <span class="ask-save-msg muted"></span>
    </form>`;
}

async function bindKeepForm(form, t) {
  const slugSel = form.slug;
  try {
    const pages = await api("/pages");
    slugSel.innerHTML = pages.length
      ? pages.map((pg) => `<option value="${esc(pg.slug)}">${esc(pg.title || pg.slug)} (${esc(pg.kind)})</option>`).join("")
      : `<option value="">no pages yet: create one under Pages</option>`;
  } catch (err) {
    slugSel.innerHTML = `<option value="">${esc(err.message)}</option>`;
  }
  form.querySelector(".ask-stand").addEventListener("click", async () => {
    const msg = form.querySelector(".ask-save-msg");
    try {
      const res = await post("/questions", { result: t, options: t.asked_with || {} });
      msg.innerHTML = `standing question <a href="#doc/${res.doc_id}">${esc(res.slug)}</a>: asked again when the library learns something`;
    } catch (err) {
      msg.textContent = err.message;
    }
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    const msg = form.querySelector(".ask-save-msg");
    const slug = (data.new_slug || "").trim() || data.slug;
    if (!slug) { msg.textContent = "pick a page or name a new synthesis"; return; }
    try {
      const body = { slug, heading: data.heading, result: t };
      if (data.new_slug && data.new_slug.trim()) body.create = "synthesis";
      const res = await post("/ask/save", body);
      msg.innerHTML = `${res.created ? "created" : "saved as revision " + res.revision} <a href="#doc/${res.doc_id}">${esc(res.slug || slug)}</a>`;
    } catch (err) {
      msg.textContent = err.message;
    }
  });
}

async function viewAsk(p) {
  if (!askConfig) {
    try { askConfig = await api("/ask/config"); } catch (_) { askConfig = { default: "none" }; }
  }
  view.classList.add("stage");
  view.innerHTML = `
    <div class="ask">
      <section class="ask-thread">
        <div id="ask-turns" class="ask-turns"></div>
        ${renderComposer(p)}
      </section>
      <aside id="ask-side" class="ask-side"></aside>
    </div>`;
  const turns = askSession().turns;
  const turnsEl = document.getElementById("ask-turns");
  const side = document.getElementById("ask-side");
  const form = document.getElementById("ask-form");
  const box = form.question;
  let selected = -1;

  const onScreen = () => document.contains(turnsEl);
  const toEnd = () => window.scrollTo(0, document.body.scrollHeight);
  function autosize() {
    box.style.height = "auto";
    box.style.height = Math.min(box.scrollHeight, 220) + "px";
  }
  function paint() {
    turnsEl.innerHTML = turns.length ? turns.map(renderTurn).join("") : `
      <div class="ask-empty muted">
        <p>Ask the library a question. The model surfs before it answers — searches again, reads on, walks the graph, sets aside what is beside the point — for as many steps as you allow, and the answer cites the passages it kept, shown beside it. A follow-up may refer to the earlier turns ("and the second one?").</p>
      </div>`;
    const done = turns.filter((t) => !t.pending && !t.error).length;
    document.getElementById("ask-new").hidden = !turns.length;
    document.getElementById("ask-count").textContent = done ? `${done} turn${done > 1 ? "s" : ""} in this tab; a follow-up may refer to them` : "";
    box.placeholder = done ? "ask a follow-up…" : "ask the library…";
    if (selected >= 0) turnsEl.querySelectorAll(".turn").forEach((el) => el.classList.toggle("selected", Number(el.dataset.turn) === selected));
    turnsEl.querySelectorAll(".answer").forEach(typesetMaths);
  }
  function select(i) {
    selected = i;
    turnsEl.querySelectorAll(".turn").forEach((el) => el.classList.toggle("selected", Number(el.dataset.turn) === i));
    side.innerHTML = renderSources(turns[i], i);
    side.querySelectorAll(".source-short, .source-full").forEach(typesetMaths);
  }
  function showSource(n) {
    const card = document.getElementById(`source-${n}`);
    if (!card) return;
    card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    card.classList.remove("flash");
    void card.offsetWidth;  // restart the animation
    card.classList.add("flash");
  }

  async function ask(question, opts) {
    const backend = opts.backend || askConfig.default;
    const earlier = turns.filter((t) => t.answer).map((t) => ({ question: t.question, answer: t.answer }));
    const steps = opts.steps == null || opts.steps === "" ? null : Math.max(0, Number(opts.steps) || 0);
    const surfing = backend !== "none" && steps !== 0;
    const turn = { question, pending: backend === "none" ? "gathering passages…" : surfing ? `${backend} is searching…` : `asking ${backend}… (a local model takes tens of seconds)` };
    turns.push(turn);
    paint();
    toEnd();
    const body = {
      question,
      backend: opts.backend || null,
      doctype: opts.doctype || null,
      limit: Number(opts.limit || settings().ask_limit),
      history: earlier,
    };
    if (steps !== null) body.steps = steps;
    if (opts.tokens) body.tokens = Number(opts.tokens);
    turn.asked_with = { steps: body.steps, tokens: body.tokens, doctype: body.doctype, limit: body.limit, backend: body.backend };
    let r;
    try {
      if (!surfing) {
        r = await post("/ask", body);
      } else {
        // the trail arrives step by step and is painted as it does
        turn.trail = [];
        const repaint = () => { if (onScreen()) { paint(); toEnd(); } };
        await postLines("/ask", { ...body, stream: true }, (e) => {
          if (e.event === "step") {
            turn.trail.push(e.step);
            const s = e.step;
            turn.pending = s.action === "answer" ? `${backend} has read enough…` : `${backend} is looking…`;
            repaint();
          } else if (e.event === "answering") {
            turn.pending = `${backend} is writing the answer from ${e.passages} passage${e.passages === 1 ? "" : "s"}…`;
            repaint();
          } else if (e.event === "answer") {
            r = e.result;
          } else if (e.event === "error") {
            throw new Error(e.detail || "the ask failed");
          }
        });
        if (!r) throw new Error("the door closed the stream without an answer");
      }
    } catch (err) {
      delete turn.pending;
      turn.error = err.message;
      if (onScreen()) { paint(); toEnd(); }
      return;
    }
    delete turn.pending;
    Object.assign(turn, r);
    saveAskSession(turns);
    if (!onScreen()) return;  // navigated away meanwhile; the turn is kept
    paint();
    select(turns.length - 1);
    turnsEl.lastElementChild.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const question = box.value.trim();
    if (!question) return;
    box.value = "";
    autosize();
    if (location.hash !== "#ask") history.replaceState(null, "", "#ask");  // a reload shows the conversation, not a re-ask
    ask(question, { backend: form.backend.value, doctype: form.doctype.value, limit: form.limit.value, steps: form.steps.value, tokens: form.tokens.value });
  });
  form.backend.addEventListener("change", () => {
    // the reading budget's ceiling is the chosen model's
    const bounds = readingBounds(form.backend.value === "none" ? "" : form.backend.value);
    form.tokens.max = bounds.max;
    form.tokens.title = `how much the steps may read, in tokens (at most ${bounds.max} for this model)`;
    if (Number(form.tokens.value) > bounds.max) form.tokens.value = bounds.default;
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  box.addEventListener("input", autosize);
  document.getElementById("ask-new").addEventListener("click", () => {
    clearAskSession();
    turns.length = 0;
    selected = -1;
    paint();
    side.innerHTML = renderSources(null);
    if (location.hash !== "#ask") history.replaceState(null, "", "#ask");
    box.focus();
  });
  turnsEl.addEventListener("click", (e) => {
    const art = e.target.closest(".turn");
    if (!art) return;
    const i = Number(art.dataset.turn);
    const cite = e.target.closest("a.cite");
    if (cite && !(e.ctrlKey || e.metaKey || e.shiftKey || e.button)) {
      // a plain click on [n] shows the source beside; a modified one opens the document
      e.preventDefault();
      if (selected !== i) select(i);
      showSource(Number(cite.textContent.replace(/\D/g, "")));
      return;
    }
    if (e.target.closest(".turn-sources-link")) {
      select(i);
      if (window.innerWidth <= 900) side.scrollIntoView({ block: "start", behavior: "smooth" });
      return;
    }
    if (e.target.closest(".turn-keep-link")) {
      const slot = art.querySelector(".turn-keep");
      if (!slot.hidden) { slot.hidden = true; return; }
      slot.innerHTML = renderKeepForm(turns[i]);
      slot.hidden = false;
      bindKeepForm(slot.querySelector("form"), turns[i]);
      return;
    }
    if (!e.target.closest("a, button, form")) select(i);
  });
  side.addEventListener("click", (e) => {
    if (!e.target.closest(".source-more")) return;
    const card = e.target.closest(".source");
    const full = card.querySelector(".source-full");
    full.hidden = !full.hidden;
    card.querySelector(".source-short").hidden = !full.hidden;
  });

  paint();
  autosize();
  side.innerHTML = renderSources(null);
  const kept = p.question ? turns.findIndex((t) => t.question === p.question) : -1;
  if (turns.length) {
    select(kept >= 0 ? kept : turns.length - 1);
    if (kept >= 0) turnsEl.children[kept].scrollIntoView({ block: "start" }); else toEnd();
  }
  if (p.question && kept < 0) {
    // a question in the link: ask it, then the link becomes the conversation
    box.value = "";
    history.replaceState(null, "", "#ask");
    await ask(p.question, p);
  }
}
