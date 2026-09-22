// Pages: the wiki's own documents, and what links to them.
// Part of the prax UI, loaded in the order index.html names
// (classic scripts sharing one scope; core first, boot last).

// ----------------------------------------------------------------- pages
// The wiki: notes on documents, project threads, topic pages. Each is a
// document, so a row opens the document view with its editor.

async function viewPages(p) {
  view.innerHTML = `
  <form id="new-page" class="search-form">
    <input name="title" type="text" placeholder="new page title…" required>
    <select name="kind">
      <option value="topic">topic</option>
      <option value="synthesis">synthesis</option>
      <option value="project">project</option>
    </select>
    <button>Create</button>
    <span id="page-create-msg" class="error"></span>
  </form>
  <div id="pages-list">${listPlaceholder("pages-list")}</div>`;
  document.getElementById("new-page").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    const slug = data.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80);
    try {
      const r = await put(`/page/${slug}`, {
        text: data.kind === "project"
          ? `# ${data.title}\n\n## Status\n\n## Open questions\n\n## Log\n\n`
          : `# ${data.title}\n\n`,
        title: data.title,
        kind: data.kind,
      });
      go("doc", String(r.doc_id), { edit: 1 });
    } catch (err) { document.getElementById("page-create-msg").textContent = err.message; }
  });
  const list = document.getElementById("pages-list");
  try {
    const pages = await api("/pages", { kind: p.kind });
    if (!pages.length) { list.innerHTML = `<p class="muted">No pages yet. Create a topic or project page above, or add a note from any document.</p>`; return; }
    const groups = { question: "Standing questions", project: "Projects", synthesis: "Syntheses", topic: "Topics", briefing: "Briefings", addendum: "Notes on documents" };
    // a standing question says whether the library has learned something
    // since; the ask blocks of other pages are standing questions too
    let standing = [];
    try { standing = await api("/questions"); } catch (_) { standing = []; }
    const news = Object.fromEntries(standing.map((q) => [q.slug, q]));
    const blocks = standing.filter((q) => q.block);
    const state = (q, slug) => {
      if (!q) return "";
      if (q.asking) return `<span class="asking">asking…</span>`;
      if (q.held) return `<span class="held">edited by hand; left</span> · <a href="#" class="ask-again" data-slug="${esc(slug)}" data-force="1" data-release="1">answer anew</a>`;
      if (q.due) return `<span title="${esc((q.new || []).map((n) => n.title).join(", "))}">${esc(q.why)}</span> · <a href="#" class="ask-again" data-slug="${esc(slug)}">${q.filled === false ? "ask now" : "ask again"}</a>`;
      return `settled · <a href="#" class="ask-again" data-slug="${esc(slug)}" data-force="1">ask again anyway</a>`;
    };
    const blockRows = blocks.map((q) => `<tr>
          <td><a href="#doc/${q.doc_id}">${esc(q.question)}</a> <span class="muted">in ${esc(q.title || "")}</span></td>
          <td class="muted">${state(q, q.slug)}</td>
          <td class="muted">r${q.revision}${q.model ? ` · ${esc(q.model)}` : ""}</td>
          <td class="muted">${esc((q.asked_at || "").slice(0, 16).replace("T", " "))}</td>
        </tr>`).join("");
    list.innerHTML = Object.entries(groups).map(([kind, label]) => {
      const rows = pages.filter((pg) => pg.kind === kind);
      if (!rows.length && !(kind === "question" && blocks.length)) return "";
      return `<h2 style="font-size:1rem;margin:1rem 0 .3rem">${label}</h2>
        <table class="doc-list page-list"><tbody>${rows.map((pg) => `<tr>
          <td><a href="#doc/${pg.doc_id}">${esc(pg.title || pg.slug)}</a></td>
          <td class="muted">${kind === "question" ? state(news[pg.slug], pg.slug) : esc(pg.slug)}</td>
          <td class="muted">r${pg.revision} · ${esc(pg.author || "")}</td>
          <td class="muted">${esc((pg.updated_at || "").slice(0, 16).replace("T", " "))}</td>
        </tr>`).join("")}${kind === "question" ? blockRows : ""}</tbody></table>`;
    }).join("");
    list.querySelectorAll("a.ask-again").forEach((a) => a.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        const r = await post("/questions/run", { slug: a.dataset.slug, force: !!a.dataset.force, release: !!a.dataset.release });
        a.replaceWith(Object.assign(document.createElement("span"), { className: "muted", textContent: `asking (job ${r.job})…` }));
      } catch (err) { setStatus(err.message); }
    }));
  } catch (err) {
    list.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}
