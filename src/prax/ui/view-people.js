// People: the persons of the graph, and one person's page (plan item AT
// step 0, docs/biography.md §7): their facts in date order and by
// relation, the documents that say something about them, and what the
// pictures that depict them say once faces are named.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

async function viewPeople(p) {
  const q = p.q || "";
  view.innerHTML = `<form id="people-find" class="search-form">
      <input name="q" type="search" value="${esc(q)}" placeholder="a person's name…" aria-label="a person's name">
      <button>Find</button></form>
    <p class="muted">The people the library knows: the most photographed first, then the most connected. A person's page gathers what every source says of them.</p>
    <div id="people-list">${listPlaceholder("people-list")}</div>`;
  document.getElementById("people-find").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = new FormData(e.target).get("q");
    location.hash = `#people${v ? `?${new URLSearchParams({ q: v })}` : ""}`;
  });
  const box = document.getElementById("people-list");
  let rows;
  try { rows = await api("/people", { q, limit: 100 }); } catch (err) { showError(box, err); return; }
  // from the graph's panel: a name that is one person opens their page
  if (p.name) {
    const one = rows.filter((r) => r.name.toLowerCase() === p.name.toLowerCase());
    if (one.length === 1) { location.replace(`#person/${one[0].id}`); return; }
  }
  box.innerHTML = rows.length ? peopleTable(rows) : `<p class="muted">Nobody${q ? ` named like “${esc(q)}”` : ""}.</p>`;
}

// note: what the last change did, with its undo (a run of unmerge_run's)
async function viewPerson(id, note) {
  loading();
  let got;
  try { got = await api(`/people/${encodeURIComponent(id)}`); } catch (err) { showError(view, err); return; }
  view.innerHTML = personHtml(got);
  const head = view.querySelector("h1 + p");
  if (head) head.insertAdjacentHTML("afterend", personEditHtml(got));
  bindPersonEdit(got, note);
}

function bindPersonEdit(got, note) {
  const line = view.querySelector(".person-edit");
  if (!line) return;
  const out = line.querySelector(".msg");
  const rename = view.querySelector("form.person-rename");
  const same = view.querySelector("form.person-same");
  if (note) {
    out.innerHTML = `${esc(note.text)}${note.run ? ` · <a href="#" data-undo>undo</a>` : ""}`;
    const undo = out.querySelector("[data-undo]");
    if (undo) undo.addEventListener("click", async (e) => {
      e.preventDefault();
      try { await post("/graph/unmerge", { run: note.run }); viewPerson(got.id, { text: "taken back" }); }
      catch (err) { out.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
    });
  }
  line.querySelectorAll("[data-edit]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    const form = a.dataset.edit === "rename" ? rename : same;
    form.hidden = !form.hidden;
    if (!form.hidden) form.querySelector("input").focus();
  }));
  rename.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await post(`/people/${got.id}/rename`, { name: rename.name.value });
      viewPerson(r.entity_id, { text: r.run ? `renamed from ${r.was}` : "the same name", run: r.run });
    } catch (err) { out.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
  });
  const input = same.querySelector("input[name=who]");
  bindPersonField(input);
  same.addEventListener("submit", async (e) => {
    e.preventDefault();
    const who = personChoice(input.value);
    if (!who || who.entity_id == null) { out.textContent = "pick the other person from the list"; return; }
    try {
      const r = await post(`/people/${got.id}/same`, { other: who.entity_id });
      viewPerson(r.kept, { text: `${input.value.replace(/ #\d+$/, "")} joined`, run: r.run });
    } catch (err) { out.innerHTML = `<span class="error">${esc(err.message)}</span>`; }
  });
}
