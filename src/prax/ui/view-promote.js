// Promote: what is flagged for the expensive model, and what is
// worth flagging.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// --------------------------------------------------------------- promote

// The queue for the expensive model: what is flagged (and whether that
// model has read it), and what the library keeps coming back to.
async function viewPromote(p) {
  loading();
  let d;
  try { d = await api("/promote", { limit: p.limit || 30 }); } catch (err) { view.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  const pending = d.promoted.filter((x) => !x.done).length;
  const row = (x) => `<tr>
      <td><a href="#doc/${x.doc_id}">${esc(x.title || "(untitled)")}</a></td>
      <td>${esc(x.promote.by)}</td>
      <td class="muted">${esc(x.promote.reason || "")}</td>
      <td class="muted">${esc((x.promote.at || "").slice(0, 10))}</td>
      <td>${x.done ? "done" : `<span class="muted">pending</span>`}</td>
      <td><a href="#" class="unpromote" data-id="${x.doc_id}" title="remove the flag">×</a></td>
    </tr>`;
  const cand = (c) => `<tr>
      <td><a href="#doc/${c.doc_id}">${esc(c.title || "(untitled)")}</a></td>
      <td class="num">${c.score}</td>
      <td class="muted">${[c.project ? `${c.project} project${c.project > 1 ? "s" : ""}` : "", c.synthesis ? `${c.synthesis} synthesis` : "", c.page ? `${c.page} note${c.page > 1 ? "s" : ""}` : "", c.cited ? `cited by ${c.cited}` : ""].filter(Boolean).join(", ")}</td>
      <td><a href="#" class="promote" data-id="${c.doc_id}">promote</a></td>
    </tr>`;
  view.innerHTML = `
    <p class="muted">The expensive pass runs with <b>${esc(d.step.model)}</b>${d.step.runtime ? ` (${esc(d.step.runtime)})` : ""}${d.step.error ? ` · <span class="error">${esc(d.step.error)}</span>` : ""}.
      Flagged documents are read by it when a worker runs the step with the asking: <code>prax work --steps promote --spend</code>; ${pending} pending.</p>
    <h2 style="font-size:1rem;margin:1rem 0 .3rem">Promoted (${d.promoted.length})</h2>
    ${d.promoted.length ? `<table class="doc-list"><thead><tr><th>document</th><th>by</th><th>reason</th><th>when</th><th>status</th><th></th></tr></thead><tbody>${d.promoted.map(row).join("")}</tbody></table>` : `<p class="muted">Nothing flagged yet. Promote from a document page, from the candidates below, or with the MCP tool.</p>`}
    <h2 style="font-size:1rem;margin:1.2rem 0 .3rem">Candidates</h2>
    <p class="muted">Scored by project membership (${PROMOTE_W.project}), synthesis sources (${PROMOTE_W.synthesis}), notes (${PROMOTE_W.page}) and citations from other library documents (${PROMOTE_W.cited} each).</p>
    ${d.candidates.length ? `<table class="doc-list"><thead><tr><th>document</th><th class="num">score</th><th>why</th><th></th></tr></thead><tbody>${d.candidates.map(cand).join("")}</tbody></table>` : `<p class="muted">No candidates: nothing cites, annotates or collects a document yet.</p>`}`;
  view.querySelectorAll("a.promote").forEach((a) => a.addEventListener("click", async (e) => {
    e.preventDefault();
    try { await post(`/doc/${a.dataset.id}/promote`, { reason: "candidate" }); render(); } catch (err) { setStatus(err.message); }
  }));
  view.querySelectorAll("a.unpromote").forEach((a) => a.addEventListener("click", async (e) => {
    e.preventDefault();
    await fetch(`/doc/${a.dataset.id}/promote`, { method: "DELETE" });
    render();
  }));
}
const PROMOTE_W = { project: 5, synthesis: 4, page: 3, cited: 1 };
