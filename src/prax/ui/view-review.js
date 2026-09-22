// The review queue: a misfit triple with its evidence, and the row
// form that settles it.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// ---------------------------------------------------------------- review
// Misfit triples parked by extraction (invariant 9). Each item can be
// dropped, marked as an ontology gap, or linked as an edge after fixing its
// types or relation with the current ontology's choices.

async function post(path, body) {
  setStatus("…");
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  setStatus("");
  if (res.status === 401) { askForToken(); throw new Error("access token required"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

// A POST answered line by line (/ask with stream): each JSON line goes
// to onEvent as it arrives; the promise settles when the door is done.
async function postLines(path, body, onEvent) {
  setStatus("…");
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (res.status === 401) { setStatus(""); askForToken(); throw new Error("access token required"); }
  if (!res.ok) {
    setStatus("");
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || res.statusText);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let rest = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    rest += decoder.decode(value, { stream: true });
    const lines = rest.split("\n");
    rest = lines.pop();
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line));
  }
  if (rest.trim()) onEvent(JSON.parse(rest));
  setStatus("");
}

function options(list, current) {
  return [`<option value="">–</option>`, ...list.map((x) => `<option ${x === current ? "selected" : ""}>${esc(x)}</option>`)].join("");
}

function reviewItem(it, onto) {
  const types = onto.entity_types, rels = Object.keys(onto.relations).sort();
  const unmapped = (it.reason || "").startsWith("unmapped:");
  return `
  <article class="review-item" id="review-${it.id}">
    <div class="review-triple"><b>${esc(it.src)}</b> <span class="muted">${esc(it.src_type || "?")}</span>
      <span class="rel">${esc(it.rel)}</span> <b>${esc(it.dst)}</b> <span class="muted">${esc(it.dst_type || "?")}</span></div>
    <div class="review-meta">#${it.id} · ${esc(it.reason)}${it.source_doc ? ` · <a href="#doc/${it.source_doc}${it.evidence ? `?find=${encodeURIComponent(String(it.evidence).slice(0, 120))}` : ""}">doc ${it.source_doc}</a>` : ""}${it.evidence ? ` · <i>“${esc(it.evidence)}”</i>` : ""}</div>
    <form class="review-form" data-id="${it.id}">
      <select name="src_type" title="source type">${options(types, it.src_type)}</select>
      <select name="rel" title="relation">${options(rels, unmapped ? "" : it.rel)}</select>
      <select name="dst_type" title="target type">${options(types, it.dst_type)}</select>
      <button name="action" value="linked">link</button>
      <button name="action" value="ontology" class="secondary" title="keep for a later ontology version">ontology gap</button>
      <button name="action" value="dropped" class="secondary">drop</button>
      <span class="error msg"></span>
    </form>
  </article>`;
}

async function viewReview(p) {
  const limit = Number(p.limit || 30);
  const offset = Number(p.offset || 0);
  const filter = { rel: p.rel || "", unmapped: p.unmapped || "" };
  view.innerHTML = `
  <form id="review-filter" class="search-form">
    <input name="rel" type="search" value="${esc(filter.rel)}" placeholder="relation (cites, uses, …)">
    <select name="unmapped">
      <option value="" ${filter.unmapped === "" ? "selected" : ""}>typed and unmapped</option>
      <option value="true" ${filter.unmapped === "true" ? "selected" : ""}>unmapped only (no types)</option>
      <option value="false" ${filter.unmapped === "false" ? "selected" : ""}>typed only (rule misfits)</option>
    </select>
    <button>Filter</button>
    <button type="button" id="bulk-drop" class="secondary" title="close every open item matching the filter as dropped">drop all matching</button>
    <button type="button" id="replay" class="secondary" title="link typed items the current ontology now accepts">replay against ontology</button>
    <span id="review-msg" class="muted"></span>
  </form>
  <div id="review-list">${listPlaceholder("review-list")}</div>`;
  const form = document.getElementById("review-filter");
  const query = () => ({ rel: filter.rel || undefined, unmapped: filter.unmapped || undefined });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    go("review", "", Object.fromEntries(new FormData(form)));
  });
  const msg = document.getElementById("review-msg");
  document.getElementById("bulk-drop").addEventListener("click", async () => {
    if (!filter.rel && !filter.unmapped) { msg.textContent = "set a filter first"; return; }
    const n = (await api("/review", { limit: 1, ...query() })).total;
    if (!window.confirm(`Drop all ${n.toLocaleString()} open items matching the filter?`)) return;
    try {
      const r = await post("/review/bulk", { resolution: "dropped", ...query() });
      msg.textContent = `dropped ${r.resolved.toLocaleString()}`;
      render();
    } catch (err) { msg.textContent = err.message; }
  });
  document.getElementById("replay").addEventListener("click", async () => {
    try {
      const r = await post("/review/replay", {});
      msg.textContent = `ontology v${r.ontology_version}: ${r.linked} linked, ${r.existing} already present, ${r.still_open} still open`;
      render();
    } catch (err) { msg.textContent = err.message; }
  });
  const list = document.getElementById("review-list");
  try {
    const [res, onto] = await Promise.all([api("/review", { limit, offset, ...query() }), api("/ontology")]);
    const page = (o) => `#review?${new URLSearchParams({ ...filter, offset: o })}`;
    const pager = `
      <div class="pager">
        <span class="muted">${res.total.toLocaleString()} open items · ${res.items.length ? offset + 1 : 0}–${Math.min(offset + limit, res.total)} · ontology v${esc(onto.version)}</span>
        ${offset > 0 ? `<a href="${page(Math.max(0, offset - limit))}">‹ previous</a>` : ""}
        ${offset + limit < res.total ? `<a href="${page(offset + limit)}">next ›</a>` : ""}
      </div>`;
    list.innerHTML = pager + res.items.map((it) => reviewItem(it, onto)).join("") + pager;
    list.querySelectorAll(".review-form").forEach((form) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const action = e.submitter && e.submitter.value;
        const data = Object.fromEntries(new FormData(form));
        const body = { resolution: action };
        if (action === "linked") Object.assign(body, { src_type: data.src_type, rel: data.rel, dst_type: data.dst_type });
        const msg = form.querySelector(".msg");
        try {
          const r = await post(`/review/${form.dataset.id}`, body);
          const item = document.getElementById(`review-${form.dataset.id}`);
          item.classList.add("done");
          form.innerHTML = `<span class="muted">${action}${r.edge_id ? ` · edge ${r.edge_id}` : ""}</span>`;
        } catch (err) {
          msg.textContent = err.message;
        }
      });
    });
  } catch (err) {
    list.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}
