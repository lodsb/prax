// The lists a person decides (stage R), as tabs of the Review page: the
// likely pairs ("same thing?"), the names held by things of unrelated
// types, and the merges worth a second look. Each decision is a signed
// pair on the door (POST /graph/decide, /graph/unmerge-entity), which is
// also the gold sample a model's confidence is measured against.
// Part of the prax UI, loaded in the order index.html names.

const DECIDE_LISTS = {
  pairs: { path: "/graph/candidates", title: "same thing?", render: pairRow,
    about: "Pairs whose names are close by embedding that the local model was not sure about (its number is beside each). Same: the one with fewer edges folds into the other (a run of its own; undo takes it back). Different: never asked again." },
  names: { path: "/graph/split-names", title: "one name, several things", render: splitRow,
    about: "One name held by things of unrelated types. One thing: keep it as one of the types and fold the others in. Several things: kept apart." },
  personal: { path: "/documents/suspected", title: "personal?", render: suspectRow, admin: true,
    about: "Documents the rules in prax.yaml (private:) think are personal: a named token that may not see personal documents does not see these. Personal: kept from those tokens for good. Not personal: open again, and the rules never mark it again." },
  marked: { path: "/documents/suspected", params: { state: "personal" }, title: "marked personal", render: suspectRow, admin: true,
    about: "Every document marked personal, the last marked first: kept from every token that may not see personal documents. Not personal opens one again, and the rules never mark it again." },
  cleanup: { title: "clean up", view: (p) => viewCleanup(p), admin: true,
    about: "Documents picked by a rule, shown before anything happens, retired in one go and restored in one go. Retiring keeps the original and the text; search and the graph pass the document by." },
  genre: { title: "genre", view: (p) => viewGenres(p),
    about: "What each document is and what it is about, in your words: the gold sample the genres step is measured against (docs/PLAN.md, stage Z). Tick every genre that fits and every subject it is about; the level in bold comes with them. Tick a level alone when nothing under it fits. A subject may be left out. \"to check\" holds a model's labels, the least sure first: save them as they are, or change the ticks first. The documents come from each source in turn. About 150 is the aim." },
  merges: { path: "/graph/merges", title: "merges to check", render: mergeRow,
    about: "Merges already made whose names differ by one word, or where a name was folded into a narrower one. Right: kept, and marked checked. Wrong: the merged one stands on its own again." },
};

function decideTabs(current) {
  const tab = (key, label) => key === current
    ? `<b>${esc(label)}</b>`
    : `<a href="#review${key ? `?list=${key}` : ""}">${esc(label)}</a>`;
  return `<nav class="decide-tabs">${[tab("", "facts that did not fit"),
    ...Object.entries(DECIDE_LISTS).filter(([, l]) => !l.admin).map(([k, l]) => tab(k, l.title))].join(" · ")}</nav>`;
}

async function viewDecide(p) {
  const list = DECIDE_LISTS[p.list];
  if (list.view) return list.view(p);
  const limit = Number(p.limit || 30);
  const offset = Number(p.offset || 0);
  view.innerHTML = `${list.admin ? adminTabs(p.list) : decideTabs(p.list)}<p class="muted">${esc(list.about)}</p>
    <div id="decide-rule"></div><div id="decide-list">${listPlaceholder("decide-list")}</div>`;
  if (p.list === "pairs") {
    api("/graph/sameness").then((r) => { document.getElementById("decide-rule").innerHTML = sameRule(r); }).catch(() => {});
  }
  if (p.list === "personal") {
    api("/private/rules").then((r) => { document.getElementById("decide-rule").innerHTML = privateRules(r); }).catch(() => {});
  }
  const box = document.getElementById("decide-list");
  let res;
  try { res = await api(list.path, { ...(list.params || {}), offset, limit }); } catch (err) { showError(box, err); return; }
  const page = (o) => list.admin
    ? `#admin?${new URLSearchParams({ tab: p.list, offset: o })}`
    : `#review?${new URLSearchParams({ list: p.list, offset: o })}`;
  const pager = `<div class="pager"><span class="muted">${res.total.toLocaleString()} to decide · ${res.items.length ? offset + 1 : 0}–${Math.min(offset + limit, res.total)}</span>
    ${offset > 0 ? `<a href="${page(Math.max(0, offset - limit))}">‹ previous</a>` : ""}
    ${offset + limit < res.total ? `<a href="${page(offset + limit)}">next ›</a>` : ""}</div>`;
  box.innerHTML = res.items.length
    ? pager + res.items.map((it, i) => list.render(it, i)).join("") + pager
    : `<p class="muted">Nothing left to decide here.</p>`;
  box.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", () => decideAct(b)));
}

// what a decision is undone by: its pairs and the runs they merged under
function undoButton(done) {
  return `<button type="button" class="secondary" data-undo="${esc(JSON.stringify(done))}">undo</button>`;
}

// One button's decision: the calls it stands for, then the row says what
// happened (and offers undo where a run can take it back).
async function decideAct(button) {
  const row = button.closest(".decide-row");
  const out = row.querySelector(".decide-out");
  const d = button.dataset;
  row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  try {
    let said = "";
    if (d.act === "same" || d.act === "different") {
      const r = await post("/graph/decide", { keep: Number(d.keep), other: Number(d.other), same: d.act === "same", across_types: d.across === "1" });
      said = `${d.act === "same" ? (r.run ? "one thing" : "one thing · checked") : "kept apart"} · ${undoButton([[d.keep, d.other, r.run || ""]])}`;
    } else if (d.act === "one") {
      const others = d.others.split(",").map(Number);
      const done = [];
      for (const o of others) done.push([d.keep, o, (await post("/graph/decide", { keep: Number(d.keep), other: o, same: true, across_types: true })).run || ""]);
      said = `one thing, as ${esc(d.type)} · ${undoButton(done)}`;
    } else if (d.act === "apart") {
      const ids = d.ids.split(",").map(Number);
      const done = [];
      for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
        await post("/graph/decide", { keep: ids[i], other: ids[j], same: false });
        done.push([ids[i], ids[j], ""]);
      }
      said = `several things, kept apart · ${undoButton(done)}`;
    } else if (d.act === "personal" || d.act === "open") {
      await post(`/doc/${Number(d.doc)}/sensitivity`, { state: d.act === "personal" ? "personal" : null }, "PUT");
      said = d.act === "personal" ? "personal: kept from the restricted tokens" : "not personal: open";
    } else if (d.act === "split") {
      await post("/graph/unmerge-entity", { entity: Number(d.entity) });
      said = "split: it stands on its own again";
    }
    row.classList.add("done");
    out.innerHTML = said;
    out.querySelectorAll("button[data-undo]").forEach((u) => u.addEventListener("click", async () => {
      u.disabled = true;
      for (const [keep, other, run] of JSON.parse(u.dataset.undo)) {
        await post("/graph/undecide", { keep: Number(keep), other: Number(other), run: run || null });
      }
      out.textContent = "undone: open again";
      row.classList.remove("done");
      row.querySelectorAll("button[data-act]").forEach((b) => { b.disabled = false; });
    }));
  } catch (err) {
    out.innerHTML = `<span class="error">${esc(err.message)}</span>`;
    row.querySelectorAll("button").forEach((b) => { b.disabled = false; });
  }
}

// Stage X: a rule picks a set, the page shows it, one click retires it
// (a second one confirms), and a past clean-up comes back with one click.
async function viewCleanup(p) {
  const list = DECIDE_LISTS.cleanup;
  view.innerHTML = `${adminTabs("cleanup")}<p class="muted">${esc(list.about)}</p>
    <div id="cleanup-rules">${listPlaceholder("cleanup-rules")}</div>
    <div id="cleanup-preview"></div><div id="cleanup-runs"></div>`;
  let info;
  try { info = await api("/cleanup"); } catch (err) {
    showError(document.getElementById("cleanup-rules"), err); return;
  }
  document.getElementById("cleanup-rules").innerHTML = cleanupRules(info.rules, p.rule, p.folder);
  document.getElementById("cleanup-runs").innerHTML = cleanupRuns(info.runs);
  const form = document.getElementById("cleanup-folder");
  if (form) form.addEventListener("submit", (e) => {
    e.preventDefault();
    location.hash = `#admin?${new URLSearchParams({ tab: "cleanup", rule: "folder", folder: form.folder.value.trim() })}`;
  });
  document.querySelectorAll("button[data-restore]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await post("/cleanup-restore", { run: b.dataset.restore });
      b.replaceWith(Object.assign(document.createElement("span"), { className: "muted", textContent: `restored ${r.restored}, ${r.edges_reopened} facts reopened` }));
    } catch (err) { b.disabled = false; b.insertAdjacentHTML("afterend", ` <span class="error">${esc(err.message)}</span>`); }
  }));
  if (!p.rule) return;
  const box = document.getElementById("cleanup-preview");
  box.innerHTML = listPlaceholder("cleanup-preview");
  let res;
  try { res = await api(`/cleanup/${encodeURIComponent(p.rule)}`, { folder: p.folder }); } catch (err) {
    showError(box, err); return;
  }
  box.innerHTML = cleanupPreview(res);
  const go = box.querySelector("button[data-retire]");
  if (go) go.addEventListener("click", async () => {
    if (!go.dataset.sure) { go.dataset.sure = "1"; go.textContent = `yes, retire ${res.total} documents`; return; }
    go.disabled = true;
    const out = box.querySelector(".decide-out");
    try {
      const r = await post(`/cleanup/${encodeURIComponent(res.rule)}`, { folder: res.folder });
      out.textContent = `retired ${r.retired} (${r.edges_ended} facts ended, ${r.edges_moved} moved to a first copy) as ${r.run}`;
      document.getElementById("cleanup-runs").innerHTML = cleanupRuns((await api("/cleanup")).runs);
    } catch (err) { go.disabled = false; out.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
  });
}


// The genre tab: ten documents at a time, each with the vocabulary as
// checkboxes. Save writes the person's genres (PUT /doc/{id}/genres), can't
// tell takes the document out of the sample; the row says what was done
// and may be changed again. "labelled" lists the labels so far, the last
// first, for a correction.
async function viewGenres(p) {
  const list = DECIDE_LISTS.genre;
  const state = ["labelled", "check"].includes(p.state) ? p.state : "open";
  const limit = 10;
  const offset = Number(p.offset || 0);
  const link = (s, label) => s === state ? `<b>${label}</b>` : `<a href="#review?${new URLSearchParams({ list: "genre", state: s })}">${label}</a>`;
  view.innerHTML = `${decideTabs("genre")}<p class="muted">${esc(list.about)}</p>
    <p>${link("check", "to check")} · ${link("open", "to label")} · ${link("labelled", "labelled")} <span id="genre-count" class="muted"></span></p>
    <div id="decide-list">${listPlaceholder("decide-list")}</div>`;
  const box = document.getElementById("decide-list");
  let vocab, res;
  try {
    [vocab, res] = await Promise.all([api("/genres"), api("/documents/genre-sample", { state, offset, limit })]);
  } catch (err) { showError(box, err); return; }
  document.getElementById("genre-count").textContent = ` — ${res.to_check} to check, ${res.labelled} labelled, ${res.skipped} set aside${state === "open" ? `, ${res.total.toLocaleString()} to go` : ""}`;
  const page = (o) => `#review?${new URLSearchParams({ list: "genre", state, offset: o })}`;
  const pager = `<div class="pager">${offset > 0 ? `<a href="${page(Math.max(0, offset - limit))}">‹ previous</a>` : ""}
    ${offset + limit < res.total ? `<a href="${page(offset + limit)}">next ›</a>` : ""}</div>`;
  box.innerHTML = res.items.length
    ? res.items.map((it) => genreRow(it, vocab)).join("") + pager
    : `<p class="muted">Nothing ${{ open: "left to label", check: "left to check", labelled: "labelled yet" }[state]}.</p>`;
  box.querySelectorAll(".genre-row").forEach((row) => {
    const form = row.querySelector(".genre-form");
    const out = row.querySelector(".decide-out");
    const send = async (body) => {
      form.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      try {
        const r = await post(`/doc/${row.dataset.doc}/genres`, body, "PUT");
        out.textContent = r.skipped ? "set aside" : `saved: ${r.genres.join(", ")}${r.subjects.length ? ` · about ${r.subjects.join(", ")}` : ""}`;
        row.classList.add("done");
      } catch (err) { out.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
      form.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    };
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const genres = [...form.querySelectorAll("input[name=genre]:checked")].map((c) => c.value);
      const subjects = [...form.querySelectorAll("input[name=subject]:checked")].map((c) => c.value);
      if (!genres.length) { out.textContent = "tick at least one genre, or can't tell"; return; }
      send({ genres, subjects });
    });
    form.querySelector("[data-genre-skip]").addEventListener("click", () => send({ skip: true }));
  });
}
