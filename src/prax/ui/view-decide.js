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
    about: "What each document is and what it is about, in your words: the gold sample the genres step is measured against (docs/PLAN.md, stage Z). Tick every genre that fits and every subject it is about; the level in bold comes with them. Tick a level alone when nothing under it fits. A subject may be left out. \"to check\" holds a model's labels, the least sure first: save them as they are, or change the ticks first. \"decided a domain\" is the part of it a domain rule placed by those labels, where a wrong one costs the most. The documents come from each source in turn. About 150 is the aim." },
  versions: { title: "versions", view: (p) => viewVersions(p),
    about: "Two documents of one title (or one arXiv id) whose texts are much alike: a preprint and its journal version, or one text downloaded twice. Versions of one work: each is a version of the work, and a search shows the latest with the others folded under it. One text twice: the copy is retired into the one with more facts, as heal does. Different: never asked again. Headings only one side has are marked. Undo takes any answer back." },
  events: { title: "events", view: (p) => viewEvents(p),
    about: "Photos taken close together: no gap of a day between two of them, and one country. An event: name it (a folder's name or its place and months to start from), and each photo is recorded at it, with the event in the place most of them were taken. A name another event has joins that one. Not one: never asked again. Undo takes either back. Split…: cut a group before a burst of its photos, kept for every later pass; join takes a cut back." },
  faces: { title: "faces", view: (p) => viewFaces(p),
    about: "Every face the faces step found, as a small picture of it: the least sure first, to see what the detector takes for a face; the smallest, to see where recognition will struggle; or the surest. Alike shows the faces whose vectors are nearest to one, with how alike they are: a check of what the vectors say, not a decision. Nothing is named here yet." },
  matches: { title: "who is this?", view: (p) => viewFaceMatches(p),
    about: "Faces nobody has answered for yet that look like a person you named: each face against every face you named, so every name helps the next. The person whose answer settles the most pictures comes first, with a face you named of them beside. Click away a face that is not them and name the rest; the ones clicked away are not them, and are never offered as them again. Not them answers that for every face left. Nothing is named until you say so." },
  groups: { title: "face groups", view: (p) => viewFaceGroups(p),
    about: "Faces that are probably one person, grouped each night by how alike they look (the facegroups pass): the group with the most faces to name first. Click a face that is not them, then name the rest: a person the graph has (pick one as you type) or a new name. Each picture then depicts them, and a picture with a named face is kept from tokens that may not see personal documents. Naming two groups as one person joins them; a face clicked away stays to name. Not faces and nobody to name answer for the faces too. Every answer can be undone. Where a picture holds two of a group's faces, it is surely more than one person. Choose an event to name the faces of one occasion, those in no group too." },
  merges: { path: "/graph/merges", title: "merges to check", render: mergeRow,
    about: "Merges already made whose names differ by one word, or where a name was folded into a narrower one. Right: kept, and marked checked. Wrong: the merged one stands on its own again." },
};

// The tabs by what is reviewed in them: the graph's facts and things,
// what a document is, the photos. "" is the review queue (view-review.js).
const DECIDE_GROUPS = [
  { title: "the graph", what: "facts and things the extraction was not sure of", keys: ["", "pairs", "names", "merges"] },
  { title: "documents", what: "what each document is and is about, and which are versions of one work", keys: ["genre", "versions"] },
  { title: "photos", what: "pictures grouped into events, and the faces in them", keys: ["events", "faces", "groups", "matches"] },
];

function decideTabs(current) {
  const title = (key) => key ? DECIDE_LISTS[key].title : "facts that did not fit";
  const tab = (key) => key === current
    ? `<b>${esc(title(key))}</b>`
    : `<a href="#review${key ? `?list=${key}` : ""}">${esc(title(key))}</a>`;
  const here = DECIDE_GROUPS.find((g) => g.keys.includes(current));
  const groups = DECIDE_GROUPS.map((g) =>
    `<span class="decide-group${g === here ? " here" : ""}"><span class="decide-group-name">${esc(g.title)}</span> ${g.keys.map(tab).join(" · ")}</span>`);
  return `<nav class="decide-tabs">${groups.join("")}</nav>${here ? `<p class="decide-what">Reviewing <b>${esc(here.title)}</b>: ${esc(here.what)}.</p>` : ""}`;
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
  const aside = res.referenced ? ` · ${res.referenced.toLocaleString()} photos and recordings on a share set aside, private until released` : "";
  const pager = `<div class="pager"><span class="muted">${res.total.toLocaleString()} to decide · ${res.items.length ? offset + 1 : 0}–${Math.min(offset + limit, res.total)}${aside}</span>
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
  const state = ["labelled", "check", "ruled"].includes(p.state) ? p.state : "open";
  const limit = 10;
  const offset = Number(p.offset || 0);
  const link = (s, label) => s === state ? `<b>${label}</b>` : `<a href="#review?${new URLSearchParams({ list: "genre", state: s })}">${label}</a>`;
  view.innerHTML = `${decideTabs("genre")}<p class="muted">${esc(list.about)}</p>
    <p>${link("ruled", "decided a domain")} · ${link("check", "to check")} · ${link("open", "to label")} · ${link("labelled", "labelled")} <span id="genre-count" class="muted"></span></p>
    <div id="decide-list">${listPlaceholder("decide-list")}</div>`;
  const box = document.getElementById("decide-list");
  let vocab, res;
  try {
    [vocab, res] = await Promise.all([api("/genres"), api("/documents/genre-sample", { state, offset, limit })]);
  } catch (err) { showError(box, err); return; }
  document.getElementById("genre-count").textContent = ` — ${res.to_check} to check, ${res.labelled} labelled, ${res.skipped} set aside${["open", "ruled"].includes(state) ? `, ${res.total.toLocaleString()} ${state === "ruled" ? "placed by a rule" : "to go"}` : ""}`;
  const page = (o) => `#review?${new URLSearchParams({ list: "genre", state, offset: o })}`;
  const pager = pagerHtml(res.total, offset, limit, page);
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

async function viewVersions(p) {
  const list = DECIDE_LISTS.versions;
  const verdict = ["version", "copy", "different"].includes(p.verdict) ? p.verdict : "";
  const limit = 20;
  const offset = Number(p.offset || 0);
  const link = (v, label) => v === verdict ? `<b>${label}</b>` : `<a href="#review?${new URLSearchParams({ list: "versions", ...(v ? { verdict: v } : {}) })}">${label}</a>`;
  view.innerHTML = `${decideTabs("versions")}<p class="muted">${esc(list.about)}</p>
    <p>${link("", "to answer")} · ${link("version", "versions")} · ${link("copy", "copies")} · ${link("different", "different")} <span id="version-count" class="muted"></span>
      · <a href="#" id="versions-find">look again</a></p>
    <div id="decide-list">${listPlaceholder("decide-list")}</div>`;
  const box = document.getElementById("decide-list");
  document.getElementById("versions-find").addEventListener("click", async (ev) => {
    ev.preventDefault();
    ev.target.textContent = "looking…";
    try { await post("/graph/versions/find", {}); } catch (err) { showError(box, err); return; }
    viewVersions(p);
  });
  let res;
  try { res = await api("/graph/versions", { ...(verdict ? { verdict } : {}), offset, limit }); } catch (err) { showError(box, err); return; }
  const c = res.counts;
  document.getElementById("version-count").textContent = ` — ${c.open} to answer, ${c.version} versions, ${c.copy} copies, ${c.different} different`;
  const page = (o) => `#review?${new URLSearchParams({ list: "versions", ...(verdict ? { verdict } : {}), offset: o })}`;
  const pager = pagerHtml(res.total, offset, limit, page);
  box.innerHTML = res.items.length
    ? res.items.map(versionRow).join("") + pager
    : `<p class="muted">${verdict ? "None yet." : "Nothing to answer: no two documents of one title share enough text (the versions pass of prax maintain finds them, or look again)."}</p>`;
  box.querySelectorAll(".version-row").forEach((row) => {
    const id = row.dataset.candidate;
    const out = row.querySelector(".decide-out");
    const send = async (path, body, done) => {
      row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      try {
        out.textContent = done(await post(path, body));
        row.classList.add("done");
      } catch (err) {
        out.innerHTML = `<span class="error">${esc(err.message)}</span>`;
        row.querySelectorAll("button").forEach((b) => { b.disabled = false; });
      }
    };
    row.querySelectorAll("[data-verdict]").forEach((b) => b.addEventListener("click", () =>
      send(`/graph/versions/${id}`, { verdict: b.dataset.verdict }, (r) =>
        r.verdict === "version" ? `versions of ${r.work.name}` : r.verdict === "copy" ? `#${r.retired} retired into #${r.kept}` : "different")));
    const undo = row.querySelector("[data-version-undo]");
    if (undo) undo.addEventListener("click", () => send(`/graph/versions/${id}/undo`, {}, (r) =>
      `open again${r.ended ? `, ${r.ended} facts ended` : ""}${r.unretired ? `, #${r.unretired} back` : ""}`));
  });
}

async function viewEvents(p) {
  const list = DECIDE_LISTS.events;
  const state = ["event", "not"].includes(p.state) ? p.state : "open";
  const limit = 20;
  const offset = Number(p.offset || 0);
  const link = (s, label) => s === state ? `<b>${label}</b>` : `<a href="#review?${new URLSearchParams({ list: "events", state: s })}">${label}</a>`;
  view.innerHTML = `${decideTabs("events")}<p class="muted">${esc(list.about)}</p>
    <p>${link("open", "to decide")} · ${link("event", "events")} · ${link("not", "not events")} <span id="event-count" class="muted"></span>
      · <a href="#" id="events-find">look again</a></p>
    <div id="decide-list">${listPlaceholder("decide-list")}</div>`;
  const box = document.getElementById("decide-list");
  document.getElementById("events-find").addEventListener("click", async (ev) => {
    ev.preventDefault();
    ev.target.textContent = "looking…";
    try { await post("/graph/events/find", {}); } catch (err) { showError(box, err); return; }
    viewEvents(p);
  });
  let res;
  try { res = await api("/graph/events", { state, offset, limit }); } catch (err) { showError(box, err); return; }
  const c = res.counts;
  document.getElementById("event-count").textContent = ` — ${c.open} to decide, ${c.event} events, ${c.not} not`;
  const page = (o) => `#review?${new URLSearchParams({ list: "events", state, offset: o })}`;
  const pager = pagerHtml(res.total, offset, limit, page);
  box.innerHTML = res.items.length
    ? res.items.map(eventRow).join("") + pager
    : `<p class="muted">${state === "open" ? "Nothing to decide: no group of photos waits (the events pass of prax maintain finds them, or look again)." : "None yet."}</p>`;
  box.querySelectorAll(".event-row").forEach((row) => {
    const id = row.dataset.candidate;
    const out = row.querySelector(".decide-out");
    const send = async (path, body, done) => {
      row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      try {
        const r = await post(path, body);
        out.textContent = done(r);
        row.classList.add("done");
      } catch (err) {
        out.innerHTML = `<span class="error">${esc(err.message)}</span>`;
        row.querySelectorAll("button").forEach((b) => { b.disabled = false; });
      }
    };
    const form = row.querySelector(".event-form");
    if (form) {
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const name = form.querySelector("input[name=name]").value.trim();
        if (!name) { out.textContent = "give it a name"; return; }
        send(`/graph/events/${id}`, { event: true, name }, (r) => `an event: ${r.name} (${r.facts} facts)`);
      });
      form.querySelector("[data-event-not]").addEventListener("click", () =>
        send(`/graph/events/${id}`, { event: false }, () => "not an event"));
      // a split or a join finds the groups again, so the page is drawn anew
      const regroup = async (path, body) => {
        try { await post(path, body); } catch (err) { out.innerHTML = `<span class="error">${esc(err.message)}</span>`; return; }
        viewEvents(p);
      };
      const join = form.querySelector("[data-event-join]");
      if (join) join.addEventListener("click", () => regroup(`/graph/events/${id}/join`, {}));
      const line = row.querySelector(".event-timeline");
      form.querySelector("[data-event-split]").addEventListener("click", async (e) => {
        if (line.innerHTML) { line.innerHTML = ""; e.target.textContent = "split…"; return; }
        e.target.textContent = "hide";
        line.innerHTML = `<p class="muted">loading…</p>`;
        let t;
        try { t = await api(`/graph/events/${id}/timeline`); } catch (err) { showError(line, err); return; }
        line.innerHTML = `<p class="muted">Split before a burst of photos; each side becomes a group of its own (fewer than eight photos are not asked about).</p>${timelineRows(t)}`;
        line.querySelectorAll("[data-cut]").forEach((b) =>
          b.addEventListener("click", () => regroup(`/graph/events/${id}/split`, { at: b.dataset.cut })));
      });
    }
    const undo = row.querySelector("[data-event-undo]");
    if (undo) undo.addEventListener("click", () => send(`/graph/events/${id}/undo`, {}, (r) => `open again${r.ended ? `, ${r.ended} facts ended` : ""}`));
  });
}

// The face groups (AT step 3): a strip of faces a group, or one group whole
async function viewFaceGroups(p) {
  const list = DECIDE_LISTS.groups;
  const limit = 30;
  const offset = Number(p.offset || 0);
  view.innerHTML = `${decideTabs("groups")}<p class="muted">${esc(list.about)}</p>
    <p id="group-counts" class="muted"></p><div id="face-alike"></div>
    <div id="decide-list">${listPlaceholder("decide-list")}</div>`;
  const box = document.getElementById("decide-list");
  const counts = document.getElementById("group-counts");
  const event = p.event ? Number(p.event) : undefined;
  if (p.group) {
    let g;
    try { g = await api(`/faces/groups/${Number(p.group)}`); } catch (err) { showError(box, err); return; }
    counts.innerHTML = `<a href="#review?list=groups">all groups</a>`;
    box.innerHTML = faceGroupRow(g, true);
  } else {
    let res;
    try { res = await api("/faces/groups", { offset, limit, event }); } catch (err) { showError(box, err); return; }
    const choose = res.events.length || res.event
      ? ` · <select id="face-event"><option value="">every event</option>${(res.event && !res.events.some((e) => e.id === res.event.id) ? [{ ...res.event, faces: 0 }] : []).concat(res.events).map((e) =>
        `<option value="${e.id}"${res.event && e.id === res.event.id ? " selected" : ""}>${esc(e.name)} (${e.faces})</option>`).join("")}</select>`
      : "";
    counts.innerHTML = `${res.total.toLocaleString()} groups with ${res.grouped.toLocaleString()} faces to name${res.done ? `, ${res.done.toLocaleString()} named whole` : ""}${res.built_at ? `, grouped ${esc(res.built_at)}` : ""}${choose}`;
    const pick = document.getElementById("face-event");
    if (pick) pick.addEventListener("change", () => { location.hash = `#review?${new URLSearchParams(pick.value ? { list: "groups", event: pick.value } : { list: "groups" })}`; });
    const page = (o) => `#review?${new URLSearchParams({ list: "groups", offset: o, ...(event ? { event } : {}) })}`;
    const pager = pagerHtml(res.total, offset, limit, page);
    const loose = res.loose && res.loose.faces.length ? faceGroupRow(res.loose, true) : "";
    box.innerHTML = res.items.length || loose
      ? res.items.map((g) => faceGroupRow(g)).join("") + loose + pager
      : `<p class="muted">No faces to name here: the facegroups pass of prax maintain groups the face vectors each night.</p>`;
  }
  bindAlike(box);
  bindNaming(box);
}

// AT step 5: the open faces that are probably a person already named
async function viewFaceMatches(p) {
  const list = DECIDE_LISTS.matches;
  const limit = 20;
  const offset = Number(p.offset || 0);
  view.innerHTML = `${decideTabs("matches")}<p class="muted">${esc(list.about)}</p>
    <p id="match-counts" class="muted"></p><div id="face-alike"></div>
    <div id="decide-list">${listPlaceholder("decide-list")}</div>`;
  const box = document.getElementById("decide-list");
  const counts = document.getElementById("match-counts");
  const person = p.person ? Number(p.person) : undefined;
  let res;
  try { res = await api("/faces/matches", { offset, limit, person }); } catch (err) { showError(box, err); return; }
  const line = `at least ${Math.round(res.line * 100)}% alike and ${Math.round(res.margin * 100)} points nearer than anyone else (faces.match in prax.yaml)`;
  counts.innerHTML = person
    ? `<a href="#review?list=matches">every person</a> · ${line}`
    : `${res.offered.toLocaleString()} of ${res.open.toLocaleString()} open faces look like ${res.total.toLocaleString()} people you named: ${line}`;
  const page = (o) => `#review?${new URLSearchParams({ list: "matches", offset: o })}`;
  const pager = person ? "" : pagerHtml(res.total, offset, limit, page);
  box.innerHTML = res.items.some((m) => m.faces.length)
    ? res.items.map((m) => faceMatchRow(m, Boolean(person))).join("") + pager
    : `<p class="muted">No face to offer: name some faces in the face groups tab first, and each one named is matched against the rest.</p>`;
  bindAlike(box);
  bindNaming(box);
}

// The name field's choices, the people the graph has, as one types: a
// click (or Enter on one) puts a person in the field; an answer that
// arrives after a later keystroke is dropped
let peopleTimer;
let peopleAsked = 0;
function choosePeople(input) {
  const list = input.parentElement.querySelector(".person-choices");
  clearTimeout(peopleTimer);
  const q = input.value.replace(/#\d+$/, "").trim();
  if (q.length < 2 || /#\d+$/.test(input.value)) { list.hidden = true; return; }
  const asked = ++peopleAsked;
  peopleTimer = setTimeout(async () => {
    let rows;
    try { rows = await api("/people", { q, limit: 12 }); } catch { return; }
    if (asked !== peopleAsked) return;
    list.innerHTML = rows.length ? personChoices(rows) : `<span class="muted">nobody known by that name: name them to make a new person</span>`;
    list.hidden = false;
  }, 200);
}
function bindPersonField(input) {
  const list = input.parentElement.querySelector(".person-choices");
  input.addEventListener("input", () => choosePeople(input));
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") list.hidden = true; });
  // mousedown, so the choice lands before the field's blur hides the list
  list.addEventListener("mousedown", (e) => {
    const b = e.target.closest(".person-choice");
    if (!b) return;
    e.preventDefault();
    input.value = b.dataset.value;
    list.hidden = true;
    input.focus();
  });
  input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 150));
}

// A group's form: name the faces not clicked away, or say what they are
function bindNaming(box) {
  box.querySelectorAll(".face-pick").forEach((b) => b.addEventListener("click", () => b.closest(".face-tile").classList.toggle("face-out")));
  box.querySelectorAll("form.face-name").forEach((form) => {
    const section = form.closest(".face-group");
    const out = form.querySelector(".msg");
    const input = form.querySelector("input[name=who]");
    bindPersonField(input);
    const chosen = () => [...section.querySelectorAll(".face-tile[data-face]:not(.face-out):not(.face-done)")].map((t) => Number(t.dataset.face));
    // a row of the matching's: the faces clicked away are not its person
    const offered = section.dataset.person ? Number(section.dataset.person) : null;
    const away = () => [...section.querySelectorAll(".face-tile[data-face].face-out:not(.face-done)")].map((t) => Number(t.dataset.face));
    const send = async (path, body, words) => {
      const faces = chosen();
      const done = body.refused ? faces.concat(body.refused) : faces;
      if (!faces.length) { out.textContent = "every face is clicked away"; return; }
      form.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      try {
        const r = await post(path, { faces, ...body });
        done.forEach((id) => section.querySelector(`.face-tile[data-face="${id}"]`).classList.add("face-done"));
        out.innerHTML = `${esc(words(r))} · <a href="#" data-undo="${esc(r.run)}">undo</a>`;
        out.querySelector("[data-undo]").addEventListener("click", async (e) => {
          e.preventDefault();
          try {
            await post("/faces/unname", { run: r.run });
            done.forEach((id) => section.querySelector(`.face-tile[data-face="${id}"]`).classList.remove("face-done"));
            out.textContent = "taken back";
          } catch (err) { out.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
        });
      } catch (err) { out.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
      form.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    };
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const who = personChoice(input.value);
      if (!who) { out.textContent = "who is this? a name, or a person picked from the list"; return; }
      const body = { ...who };
      if (offered != null) {
        body.matched = true;
        if (who.entity_id === offered && away().length) body.refused = away();
      }
      send("/faces/name", body, (r) => `${r.faces} face${r.faces === 1 ? "" : "s"} named ${r.name}${r.facts ? `, ${r.facts} picture${r.facts === 1 ? "" : "s"} depict them` : ""}${r.refused ? `, ${r.refused} not them` : ""}`);
    });
    form.querySelectorAll("button[type=button]").forEach((b) => b.addEventListener("click", () => {
      if (b.value === "refuse") {
        send("/faces/refuse", { entity_id: offered }, (r) => `${r.faces} face${r.faces === 1 ? "" : "s"}: not them`);
        return;
      }
      send("/faces/judge", { verdict: b.value }, (r) => `${r.faces} face${r.faces === 1 ? "" : "s"}: ${r.verdict === "nobody" ? "nobody to name" : "not faces"}`);
    }));
  });
}

// "alike" under a face tile: the faces most like it, above the list
function bindAlike(box) {
  const alike = document.getElementById("face-alike");
  box.querySelectorAll("[data-alike]").forEach((b) => b.addEventListener("click", async (e) => {
    e.preventDefault();
    const id = b.dataset.alike;
    alike.innerHTML = `<p class="muted">looking…</p>`;
    let near;
    try { near = await api(`/face/${id}/similar`, { k: 24 }); } catch (err) { showError(alike, err); return; }
    alike.innerHTML = `<section class="face-alike"><p>Most like face ${esc(id)} <a href="#" data-alike-close>close</a></p>
      <div class="face-grid">${faceTile({ id: Number(id), doc_id: Number(b.dataset.doc), crop: true, score: Number(b.dataset.score), px: Number(b.dataset.px) })}${near.map((f) => faceTile({ ...f, crop: true }, f)).join("")}</div>
      ${near.length ? "" : `<p class="muted">No vector for it yet: the facevectors step makes them.</p>`}</section>`;
    alike.querySelector("[data-alike-close]").addEventListener("click", (ev) => { ev.preventDefault(); alike.innerHTML = ""; });
    alike.scrollIntoView({ block: "nearest" });
  }));
}

// The faces the faces step found (AT steps 1 and 2): crops in an order,
// the counts by score and size, and for a face the faces most like it
async function viewFaces(p) {
  const list = DECIDE_LISTS.faces;
  const order = ["smallest", "surest"].includes(p.order) ? p.order : "least";
  const limit = 120;
  const offset = Number(p.offset || 0);
  const link = (o, label) => o === order ? `<b>${label}</b>` : `<a href="#review?${new URLSearchParams({ list: "faces", order: o })}">${label}</a>`;
  view.innerHTML = `${decideTabs("faces")}<p class="muted">${esc(list.about)}</p>
    <p>${link("least", "least sure first")} · ${link("smallest", "smallest first")} · ${link("surest", "surest first")}</p>
    <p id="face-counts" class="muted"></p><div id="face-alike"></div>
    <div id="decide-list">${listPlaceholder("decide-list")}</div>`;
  const box = document.getElementById("decide-list");
  let res;
  try { res = await api("/faces", { order, offset, limit }); } catch (err) { showError(box, err); return; }
  document.getElementById("face-counts").innerHTML = faceCounts(res);
  const page = (o) => `#review?${new URLSearchParams({ list: "faces", order, offset: o })}`;
  const pager = pagerHtml(res.total, offset, limit, page);
  box.innerHTML = res.items.length ? `<div class="face-grid">${res.items.map((f) => faceTile(f)).join("")}</div>${pager}` : `<p class="muted">No face found yet: the faces step reads the photos' previews.</p>`;
  bindAlike(box);
}
