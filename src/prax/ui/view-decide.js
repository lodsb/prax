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
  personal: { path: "/documents/suspected", title: "personal?", render: suspectRow,
    about: "Documents the rules in prax.yaml (private:) think are personal: a named token that may not see personal documents does not see these. Personal: kept from those tokens for good. Not personal: open again, and the rules never mark it again." },
  merges: { path: "/graph/merges", title: "merges to check", render: mergeRow,
    about: "Merges already made whose names differ by one word, or where a name was folded into a narrower one. Right: kept, and marked checked. Wrong: the merged one stands on its own again." },
};

function decideTabs(current) {
  const tab = (key, label) => key === current
    ? `<b>${esc(label)}</b>`
    : `<a href="#review${key ? `?list=${key}` : ""}">${esc(label)}</a>`;
  return `<nav class="decide-tabs">${[tab("", "facts that did not fit"),
    ...Object.entries(DECIDE_LISTS).map(([k, l]) => tab(k, l.title))].join(" · ")}</nav>`;
}

async function viewDecide(p) {
  const list = DECIDE_LISTS[p.list];
  const limit = Number(p.limit || 30);
  const offset = Number(p.offset || 0);
  view.innerHTML = `${decideTabs(p.list)}<p class="muted">${esc(list.about)}</p>
    <div id="decide-rule"></div><div id="decide-list">${listPlaceholder("decide-list")}</div>`;
  if (p.list === "pairs") {
    api("/graph/sameness").then((r) => { document.getElementById("decide-rule").innerHTML = sameRule(r); }).catch(() => {});
  }
  const box = document.getElementById("decide-list");
  let res;
  try { res = await api(list.path, { offset, limit }); } catch (err) { box.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  const page = (o) => `#review?${new URLSearchParams({ list: p.list, offset: o })}`;
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
