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

async function viewPerson(id) {
  loading();
  let got;
  try { got = await api(`/people/${encodeURIComponent(id)}`); } catch (err) { showError(view, err); return; }
  view.innerHTML = personHtml(got);
}
