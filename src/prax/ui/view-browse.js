// Browse: the library as a list, by type, source, domain or title.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// ---------------------------------------------------------------- browse

async function viewBrowse(p) {
  const limit = Number(p.limit || 50);
  const offset = Number(p.offset || 0);
  await modules();
  view.innerHTML = `
  <form id="browse-form" class="search-form">
    <input name="title" type="search" value="${esc(p.title || "")}" placeholder="title contains…">
    <input name="source" type="text" value="${esc(p.source || "")}" placeholder="source (zotero)">
    <input name="mime" type="text" value="${esc(p.mime || "")}" placeholder="mime (application/pdf)">
    ${domainSelect(p.domain || "")}
    <button>Filter</button>
  </form>
  <div id="browse-list"></div>`;
  const form = document.getElementById("browse-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    go("browse", "", Object.fromEntries(new FormData(form)));
  });
  const list = document.getElementById("browse-list");
  try {
    const res = await api("/documents", { limit, offset, title: p.title, source: p.source, mime: p.mime, domain: p.domain || undefined });
    const rows = res.items.map((d) => `
      <tr>
        <td><a href="#doc/${d.id}">${esc(d.title || "(untitled)")}</a></td>
        <td class="muted">${esc((d.meta.creators || []).slice(0, 2).map((c) => c.name).join(", "))}</td>
        <td class="muted">${esc(d.meta.date || "")}</td>
        <td class="muted">${esc(d.mime || "")}</td>
        <td class="num">${d.n_chunks}</td>
        <td class="muted">${esc((d.added_at || "").slice(0, 10))}</td>
      </tr>`).join("");
    const pager = `
      <div class="pager">
        <span class="muted">${res.total.toLocaleString()} documents · ${offset + 1}–${Math.min(offset + limit, res.total)}</span>
        ${offset > 0 ? `<a href="#browse?${new URLSearchParams({ ...p, offset: Math.max(0, offset - limit) })}">‹ newer</a>` : ""}
        ${offset + limit < res.total ? `<a href="#browse?${new URLSearchParams({ ...p, offset: offset + limit })}">older ›</a>` : ""}
      </div>`;
    list.innerHTML = pager + `
      <table class="doc-list">
        <thead><tr><th>Title</th><th>Creators</th><th>Date</th><th>Type</th><th>Chunks</th><th>Added</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>` + pager;
  } catch (err) {
    list.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}
